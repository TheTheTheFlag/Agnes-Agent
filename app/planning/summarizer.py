import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from app.graph.state import State
from app.memory import MemoryManager
from app.server import add_log_entry, add_event
from app.config import DB_PATH


def create_summarizer_node(llm):
    def summarizer_node(state: State):
        plan = state.get("task_plan")
        if not plan:
            return state

        # 防御性：thread_id 在条件边传递时可能丢失
        thread_id = state.get("thread_id") or state.get("_thread_id") or "default"
        mm = MemoryManager(db_path=DB_PATH, thread_id=thread_id)
        # 标记计划 completed（真正完成）：优先用 state 里的 task_plan_id（与 validator/route 一致），
        # fallback 按 thread 查进行中计划。
        tid = state.get("task_plan_id")
        if not tid:
            meta = mm.get_task_plan_by_thread(thread_id)
            tid = meta["id"] if meta else None
        if tid:
            try:
                mm.complete_task_plan(tid)
            except Exception:
                pass

        results = [f"子任务 {sub.id}（{sub.description}）结果：{sub.result}" for sub in plan.subtasks]
        combined = "\n".join(results)

        if any(s.status == "failed" for s in plan.subtasks):
            summary = f"部分失败：\n{combined}"
        else:
            system_prompt = (
                "请根据以下子任务的执行结果，生成一个简洁、完整的最终回答，直接满足用户的原始需求。"
                "如果用户要求开发一个游戏或应用，请先确认文件已生成，然后简要说明如何使用。"
                "不要重复生成完整的代码（除非用户明确要求），而是告诉用户文件已存在，并提供使用说明。"
            )
            response = llm.invoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=f"目标：{plan.goal}\n结果：\n{combined}")
            ])
            summary = response.content

        state["messages"].append(AIMessage(content=summary))
        mm.save_summary(thread_id, summary)

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

        # 显式 return 整个 state 的更新，避免 LangGraph 漏掉 in-memory 改动
        return {
            **state,
            "task_plan": None,
            "task_plan_id": None,
            "messages": state["messages"],
        }
    return summarizer_node