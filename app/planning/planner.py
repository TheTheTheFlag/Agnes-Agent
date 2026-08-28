import sys, os, json
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from langchain_core.messages import SystemMessage, HumanMessage
from app.graph.state import State, Subtask, TaskPlan
from app.memory import MemoryManager
from app.server import add_log_entry, add_event
from app.config import DB_PATH


def create_planner_node(llm):
    def planner_node(state: State):
        thread_id = state.get("thread_id", "default")
        mm = MemoryManager(db_path=DB_PATH, thread_id=thread_id)

        # 如果是重新规划，清空旧子任务
        if state.get("task_plan_id"):
            mm.delete_task_plan(thread_id, state["task_plan_id"])
            state["task_plan"] = None
            state["task_plan_id"] = None

        last_msg = state["messages"][-1]
        goal = last_msg.content

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

        # 创建任务计划
        task_plan_id = mm.create_task_plan(thread_id, goal)
        for item in subtasks_data:
            mm.add_subtask(task_plan_id, thread_id, item["id"], item["description"], item.get("dependencies", []))

        # 构造 Subtask 对象（所有字段都已确保类型正确）
        subtasks = [
            Subtask(
                id=item["id"],
                description=item["description"],
                dependencies=item.get("dependencies", [])
            )
            for item in subtasks_data
        ]
        task_plan = TaskPlan(goal=goal, subtasks=subtasks, status="planning")
        state["task_plan"] = task_plan
        state["task_plan_id"] = task_plan_id

        print(f"[Planner] 生成 {len(subtasks)} 个子任务")
        add_log_entry("info", f"规划: {len(subtasks)} 个子任务", {"goal": goal[:100]})
        # 事件携带完整子任务清单（供前端执行树渲染；/api/chat 的 node 事件也走同一数据结构）
        add_event("planner", {
            "goal": goal[:80],
            "subtask_count": len(subtasks),
            "subtasks": [{"id": s.id, "desc": s.description[:40], "status": "pending"} for s in subtasks],
        }, thread_id)
        return state
    return planner_node