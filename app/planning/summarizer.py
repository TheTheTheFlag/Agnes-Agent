import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from app.graph.state import State
from app.memory import MemoryManager
from app.server import add_log_entry, add_event
from app.config import DB_PATH


def create_summarizer_node(llm):
    def summarizer_node(state: State):
        thread_id = state.get("thread_id") or state.get("_thread_id") or "default"
        from app.trace import record_node_start
        record_node_start(thread_id, "summarizer")
        mm = MemoryManager(db_path=DB_PATH, thread_id=thread_id)

        # 优先查询新的 DAG 系统（dag_plans 表）
        try:
            from app.planning.dag_storage import DAGStorage
            dag = DAGStorage(DB_PATH)
            plan = dag.get_plan_by_thread(thread_id)
            if plan:
                tid = plan["id"]
                goal = plan.get("goal", "")
                # 标记计划 completed
                try:
                    dag.set_plan_status(tid, "completed")
                except Exception as e:
                    print(f"[Summarizer] 标记计划完成失败: {e}")
                # 获取所有节点
                nodes = dag.get_nodes(tid)
                results = []
                for n in nodes:
                    results.append(f"子任务 {n['id']}（{n['description']}）结果：{n.get('result') or ''}")
                combined = "\n".join(results)
            else:
                # 回退到旧系统
                plan_meta = mm.get_task_plan_by_thread(thread_id)
                if not plan_meta:
                    from app.trace import record_node_end as _rnd0
                    _rnd0(thread_id, "summarizer", "无进行中计划")
                    return {"thread_id": thread_id, "messages": state.get("messages", [])}
                tid = plan_meta["id"]
                goal = plan_meta.get("goal", "")
                db_subs = mm.get_subtasks(tid)
                results = [f"子任务 {s['id']}（{s['description']}）结果：{s.get('result') or ''}" for s in db_subs]
                combined = "\n".join(results)
                # 标记计划 completed
                try:
                    mm.complete_task_plan(tid)
                except Exception:
                    pass
        except Exception as e:
            print(f"[Summarizer] DAG 查询失败: {e}，回退到旧系统")
            plan_meta = mm.get_task_plan_by_thread(thread_id)
            if not plan_meta:
                from app.trace import record_node_end as _rnd0
                _rnd0(thread_id, "summarizer", "无进行中计划")
                return {"thread_id": thread_id, "messages": state.get("messages", [])}
            tid = plan_meta["id"]
            goal = plan_meta.get("goal", "")
            db_subs = mm.get_subtasks(tid)
            results = [f"子任务 {s['id']}（{s['description']}）结果：{s.get('result') or ''}" for s in db_subs]
            combined = "\n".join(results)
            try:
                mm.complete_task_plan(tid)
            except Exception:
                pass

        # 无论是否失败，都调用 LLM 生成友好回复
        system_prompt = (
            "请根据以下子任务的执行结果，生成一个简洁、完整的最终回答，直接满足用户的原始需求。"
            "如果部分子任务失败，请说明哪些完成了、哪些失败了，并告诉用户如何继续。"
            "如果用户要求开发一个游戏或应用，请先确认文件已生成，然后简要说明如何使用。"
            "不要重复生成完整的代码（除非用户明确要求），而是告诉用户文件已存在，并提供使用说明。"
        )
        # trace：记录 summarizer LLM 调用
        import time as _t
        from app.trace import record_llm
        _t0 = _t.time()
        _msgs = [SystemMessage(content=system_prompt),
                 HumanMessage(content=f"目标：{goal}\n结果：\n{combined}")]
        response = llm.invoke(_msgs)
        record_llm(thread_id, "summarizer", _msgs, response, duration_ms=(_t.time() - _t0) * 1000)
        # 推前端：summarizer 的模型输入/输出作为独立气泡
        try:
            from app.planning.react_loop import _llm_messages_to_text as _llm2txt
            add_event("llm_call", {
                "node": "summarizer",
                "input": _llm2txt(_msgs),
                "output": _llm2txt([response]),
                "duration_ms": int((_t.time() - _t0) * 1000),
            }, thread_id)
        except Exception:
            pass
        summary = response.content

        from app.trace import record_node_end as _rnd
        _rnd(thread_id, "summarizer", str(summary)[:200])

        state["messages"].append(AIMessage(content=summary))
        mm.save_summary(thread_id, summary)
        # 落 messages 表：让切会话时前端能通过 /api/messages 拉回这条最终汇总
        # （chatbot 节点已落 user/assistant，但 summarizer 是另一个节点，需自己写）
        try:
            mm.add_message(thread_id, "assistant", summary)
        except Exception:
            pass

        print("[Summarizer] 完成")
        add_log_entry("success", "汇总完成")
        add_event("summarizer", {"done": True}, thread_id)
        # 总结气泡：让用户看到"我最终怎么回答的"，与 chatbot 助手气泡区分
        add_event("node_thought", {
            "role": "summarizer",
            "title": "📝 最终总结",
            "text": summary[:2000],
            "subtask": "",
        }, thread_id)

        # messages 已追加 + DB 已 complete_task_plan；END 触发由 planner_node 重新进入时
        # 检测到 DB 中无进行中计划，自然走到 END。
        return {
            "thread_id": thread_id,
            "messages": state["messages"],
        }
    return summarizer_node