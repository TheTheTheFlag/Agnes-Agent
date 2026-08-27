# planning/executor.py

import sys, os, json, re
from typing import List
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from langchain_core.messages import HumanMessage, ToolMessage, SystemMessage
from app.graph.state import State, Subtask
from app.planning.react_loop import ReActLoop
from app.server import add_log_entry, add_event
from app.config import DB_PATH


def create_executor_node(llm, tools_list):
    llm_with_tools = llm.bind_tools(tools_list)

    def executor_node(state: State):
        print("[Executor] 进入")
        thread_id = state.get("thread_id", "default")
        task_plan_id = state.get("task_plan_id")
        from app.memory import MemoryManager
        mm = MemoryManager(db_path=DB_PATH, thread_id=thread_id)

        if not task_plan_id:
            if state.get("task_plan"):
                plan = state["task_plan"]
                existing = mm.get_task_plan_by_thread(thread_id)
                if existing:
                    task_plan_id = existing["id"]
                    state["task_plan_id"] = task_plan_id
                else:
                    new_id = mm.create_task_plan(thread_id, plan.goal)
                    for sub in plan.subtasks:
                        mm.add_subtask(new_id, thread_id, sub.id, sub.description, sub.dependencies)
                    task_plan_id = new_id
                    state["task_plan_id"] = task_plan_id
            else:
                return state

        subtasks = mm.get_subtasks(task_plan_id)
        if not subtasks:
            return state

        # 打印当前状态（调试）
        status_summary = [(s["id"], s["status"]) for s in subtasks]
        print(f"[Executor] 子任务状态: {status_summary}")

        # 检查是否全部完成
        if all(s["status"] in ("done", "failed") for s in subtasks):
            print("[Executor] 所有子任务已完成，标记 plan 为 done")
            if state.get("task_plan"):
                state["task_plan"].status = "done"
            return state

        # 找可执行的 pending 任务（依赖已满足）
        executable = None
        for sub in subtasks:
            if sub["status"] == "pending":
                deps = sub.get("dependencies", [])
                if not isinstance(deps, list):
                    deps = []
                deps_done = all(
                    any(dep_id == s["id"] and s["status"] == "done" for s in subtasks)
                    for dep_id in deps
                )
                if deps_done:
                    executable = sub
                    break

        # 如果找不到可执行任务，尝试强制执行第一个 pending（打破死锁）
        if not executable:
            for sub in subtasks:
                if sub["status"] == "pending":
                    executable = sub
                    print(f"[Executor] 无可用任务，强制执行: {sub['id']}")
                    break

        if not executable:
            print("[Executor] 没有 pending 任务，返回")
            return state

        sub_id = executable["id"]
        description = executable["description"]
        print(f"  ▶️ 执行 {sub_id}: {description[:50]}...")
        mm.update_subtask_status(task_plan_id, sub_id, "running")

        # 收集依赖产出
        deps_artifacts = []
        for dep_id in executable.get("dependencies", []):
            dep = next((s for s in subtasks if s["id"] == dep_id), None)
            if dep and dep.get("artifacts"):
                deps_artifacts.extend(dep["artifacts"])
        deps_artifacts = list(set(deps_artifacts))

        artifacts_context = f"\n【已有产出】\n" + "\n".join(deps_artifacts) if deps_artifacts else ""

        goal = state['task_plan'].goal if state.get('task_plan') else ""

        system_prompt = f"""执行子任务：{description}
目标：{goal}
{artifacts_context}
完成标准：文件创建且大小>0。
输出：FINAL_ANSWER: [结果] 并附上文件名。
"""

        initial_messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=f"执行子任务：{description}")
        ]

        loop = ReActLoop(llm_with_tools, max_iterations=8)
        try:
            result = loop.run(messages=initial_messages, tools=tools_list, state=state)
            final_result = result["final_answer"]
            artifacts = extract_artifacts(final_result)
            status = "done"
        except Exception as e:
            final_result = f"执行失败: {e}"
            artifacts = []
            status = "failed"

        mm.update_subtask_status(task_plan_id, sub_id, status, result=final_result, artifacts=artifacts)

        if status == "done":
            print(f"  ✅ 完成，产出: {artifacts}")
            add_log_entry("success", f"子任务 {sub_id} 完成", {"artifacts": artifacts})
        else:
            print(f"  ❌ 失败")
            add_log_entry("error", f"子任务 {sub_id} 失败", {"error": final_result[:100]})
        add_event("executor", {"subtask": sub_id, "status": status})

        # 关键：从 DB 重新加载最新 subtask 状态，重建 state["task_plan"] 中的 subtasks 列表，
        # 否则 route_after_executor 看到的还是创建时的 pending 状态，会导致死循环。
        from app.graph.state import Subtask
        latest_subtasks = mm.get_subtasks(task_plan_id)
        if state.get("task_plan") and latest_subtasks:
            new_subtasks = []
            for row in latest_subtasks:
                new_subtasks.append(Subtask(
                    id=row["id"],
                    description=row["description"],
                    dependencies=row.get("dependencies") or [],
                    status=row["status"],
                    result=row.get("result"),
                ))
            updated_plan = state["task_plan"].model_copy(update={"subtasks": new_subtasks})
            state["task_plan"] = updated_plan

        # 重新加载子任务，检查是否全部完成
        updated_subtasks = mm.get_subtasks(task_plan_id)
        all_done = all(s["status"] in ("done", "failed") for s in updated_subtasks)
        if all_done:
            print("[Executor] 所有子任务已完成，标记 plan 为 done")
            if state.get("task_plan"):
                # 必须显式 return 才能让 LangGraph 把状态写回 checkpoint，
                # 否则 in-memory 改动会被下一次 state 读取忽略，触发环。
                updated_plan = state["task_plan"].model_copy(update={"status": "done"})
                return {**state, "task_plan": updated_plan}
        return state
    return executor_node

def extract_artifacts(text: str) -> List[str]:
    """从文本中提取文件名，增强版"""
    if not text:
        return []

    artifacts = []

    # 1. 直接匹配常见扩展名
    patterns = [
        r'([\w\-\./]+\.html)',
        r'([\w\-\./]+\.js)',
        r'([\w\-\./]+\.css)',
        r'([\w\-\./]+\.py)',
        r'([\w\-\./]+\.txt)',
        r'([\w\-\./]+\.json)',
        r'([\w\-\./]+\.md)',
    ]
    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for m in matches:
            if not re.search(r'\.(pyc|log|tmp|swp|bak|o|so|dll)$', m, re.IGNORECASE):
                artifacts.append(m)

    # 2. 查找 "文件名：" 或 "file:" 后面的内容
    name_match = re.search(r'(?:文件名|file)[：:]\s*[`"\']?([\w\-\./]+\.\w+)[`"\']?', text, re.IGNORECASE)
    if name_match:
        artifacts.append(name_match.group(1))

    # 3. 查找 "已生成"、"创建" 等关键词后的文件名
    gen_match = re.search(r'(?:生成|创建|保存)[了]?\s*[`"\']?([\w\-\./]+\.\w+)[`"\']?', text, re.IGNORECASE)
    if gen_match:
        artifacts.append(gen_match.group(1))

    return list(set(artifacts))