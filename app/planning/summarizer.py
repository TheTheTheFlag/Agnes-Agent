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
        mm = MemoryManager(db_path=DB_PATH, thread_id=thread_id)
        # DB 唯一来源：按 thread 查当前进行中计划
        plan_meta = mm.get_task_plan_by_thread(thread_id)
        if not plan_meta:
            return {"thread_id": thread_id, "messages": state.get("messages", [])}
        tid = plan_meta["id"]
        # 标记计划 completed（真正完成）
        try:
            mm.complete_task_plan(tid)
        except Exception:
            pass

        # ===== 数据流：所有子任务/结果从 DB 查（state 不缓存）=====
        goal = plan_meta.get("goal", "")
        db_subs = mm.get_subtasks(tid)
        results = [f"子任务 {s['id']}（{s['description']}）结果：{s.get('result') or ''}" for s in db_subs]
        combined = "\n".join(results)

        if any(s['status'] == "failed" for s in db_subs):
            summary = f"部分失败：\n{combined}"
        else:
            system_prompt = (
                "请根据以下子任务的执行结果，生成一个简洁、完整的最终回答，直接满足用户的原始需求。"
                "如果用户要求开发一个游戏或应用，请先确认文件已生成，然后简要说明如何使用。"
                "不要重复生成完整的代码（除非用户明确要求），而是告诉用户文件已存在，并提供使用说明。"
            )
            # trace：记录 summarizer LLM 调用
            import time as _t
            from app.trace import record_llm, record_node_start, record_node_end
            record_node_start(thread_id, "summarizer")
            _t0 = _t.time()
            _msgs = [SystemMessage(content=system_prompt),
                     HumanMessage(content=f"目标：{goal}\n结果：\n{combined}")]
            response = llm.invoke(_msgs)
            record_llm(thread_id, "summarizer", _msgs, response, duration_ms=(_t.time() - _t0) * 1000)
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