import sys, os, json
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from langchain_core.messages import SystemMessage, HumanMessage
from app.graph.state import State
from app.memory import MemoryManager
from app.server import add_log_entry, add_event
from app.config import DB_PATH


def create_planner_node(llm):
    def planner_node(state: State):
        thread_id = state.get("thread_id", "default")
        mm = MemoryManager(db_path=DB_PATH, thread_id=thread_id)

        # 如果是重新规划，清空旧的进行中计划（DB 查，不用 state）
        old_plan = mm.get_task_plan_by_thread(thread_id)
        if old_plan:
            mm.delete_task_plan(thread_id, old_plan["id"])

        # goal 来源：state.pending_plan（chatbot 节点从 ToolMessage 提取后写入 ret，
        # route_after_chatbot 透传至 planner；不依赖 messages 末尾是因为最后一条可能是
        # ToolMessage 污染："已接收规划请求，目标：..."）
        goal = state.get("pending_plan")
        if not goal:
            # 兜底：反向从最近 ToolMessage 提取
            for m in reversed(state.get("messages", [])[-5:]):
                if hasattr(m, 'type') and m.type == 'tool' and '已接收规划请求' in (m.content or ''):
                    goal = m.content.split('目标：', 1)[1].strip()
                    break
        if not goal:
            # 最后兜底：取用户消息
            for m in reversed(state.get("messages", [])):
                if hasattr(m, 'type') and m.type == 'human':
                    goal = m.content
                    break
        if not goal:
            goal = "执行当前请求"

        system_prompt = """你是一个任务规划专家。将用户目标拆解为 2-4 个子任务。每个子任务包含 id, description, dependencies。输出 JSON 数组。"""
        response = llm.invoke([SystemMessage(content=system_prompt), HumanMessage(content=f"目标：{goal}")])
        raw = response.content.strip()
        try:
            if raw.startswith("```json"):
                raw = raw[7:]
            if raw.startswith("```"):
                raw = raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            subtasks_data = json.loads(raw)
            if not isinstance(subtasks_data, list):
                raise ValueError("不是数组")
            # 确保 id 为字符串，dependencies 中的元素也为字符串
            for item in subtasks_data:
                item["id"] = str(item["id"])
                if "dependencies" in item and item["dependencies"]:
                    # 转换 dependencies 中的每个元素为字符串
                    item["dependencies"] = [str(dep) for dep in item["dependencies"]]
                else:
                    item["dependencies"] = []
            # 限制子任务数量不超过 4
            if len(subtasks_data) > 4:
                print(f"[Planner] 子任务数 {len(subtasks_data)} > 4，强制合并")
                combined_desc = "; ".join([item["description"] for item in subtasks_data[1:]])
                subtasks_data = [
                    subtasks_data[0],
                    {"id": "2", "description": f"完成剩余工作：{combined_desc}", "dependencies": [str(subtasks_data[0]["id"])]}
                ]
        except Exception as e:
            print(f"[Planner] 解析失败: {e}，使用降级方案")
            subtasks_data = [{"id": "1", "description": f"执行目标：{goal}", "dependencies": []}]

        # 写入 DB（唯一真相源）
        task_plan_id = mm.create_task_plan(thread_id, goal)
        for item in subtasks_data:
            mm.add_subtask(task_plan_id, thread_id, item["id"], item["description"], item.get("dependencies", []))

        print(f"[Planner] 生成 {len(subtasks_data)} 个子任务")
        add_log_entry("info", f"规划: {len(subtasks_data)} 个子任务", {"goal": goal[:100]})
        # 事件携带完整子任务清单（供前端执行树渲染；/api/chat 的 node 事件也走同一数据结构）
        add_event("planner", {
            "goal": goal[:80],
            "subtask_count": len(subtasks_data),
            "subtasks": [{"id": s["id"], "desc": s["description"][:40], "status": "pending"} for s in subtasks_data],
        }, thread_id)
        # 规划思考气泡：给用户看到"我打算怎么拆解"
        descs = "\n".join([f"  {i+1}. {s['description']}" for i, s in enumerate(subtasks_data)])
        add_event("node_thought", {
            "role": "planner",
            "title": f"📋 规划：{goal[:60]}",
            "text": f"将目标拆解为 {len(subtasks)} 个子任务：\n{descs}",
            "subtask": "",
        }, thread_id)
        return state
    return planner_node