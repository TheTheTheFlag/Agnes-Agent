# planning/executor.py

import sys, os, json, re
from typing import List
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from langchain_core.messages import HumanMessage, ToolMessage, SystemMessage
from langgraph.types import interrupt
from app.graph.state import State, Subtask
from app.graph.utils import _tool_params_summary
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

        # 找可执行的子任务（pending 或 running——running 表示上次审批中断，需要恢复执行）
        executable = None
        for sub in subtasks:
            if sub["status"] in ("pending", "running"):
                deps = sub.get("dependencies", [])
                if not isinstance(deps, list):
                    deps = []
                deps_done = all(
                    any(dep_id == s["id"] and s["status"] == "done" for s in subtasks)
                    for dep_id in deps
                )
                if deps_done:
                    executable = sub
                    if sub["status"] == "running":
                        print(f"[Executor] 恢复中断的子任务: {sub['id']}")
                    break

        # 如果找不到可执行任务，尝试强制执行第一个 pending/running（打破死锁）
        if not executable:
            for sub in subtasks:
                if sub["status"] in ("pending", "running"):
                    executable = sub
                    print(f"[Executor] 无可用任务，强制执行: {sub['id']}")
                    break

        if not executable:
            print("[Executor] 没有待执行任务，返回")
            return state

        sub_id = executable["id"]
        description = executable["description"]
        # 残留卡死检测：上一轮已标记 running 但实际从未完成（GraphInterrupt 后未恢复），
        # 强制转为 failed 让本轮新执行接管，避免无限等 running 子任务。
        exec_status = executable.get("status", "pending")
        if exec_status == "running":
            # 距上次更新超过 5 分钟视为卡死
            try:
                from datetime import datetime as _dt
                last_upd = executable.get("updated_at")
                if last_upd and (_dt.now() - _dt.fromisoformat(last_upd.replace(" ", "T"))).total_seconds() > 300:
                    print(f"[Executor] {sub_id} 残留 running 超过 5 分钟，标记为 failed 让本轮接管")
                    mm.update_subtask_status(task_plan_id, sub_id, "failed", result="残留 running 超时，自动清理")
            except Exception:
                pass
        print(f"  ▶️ 执行 {sub_id}: {description[:50]}...")
        mm.update_subtask_status(task_plan_id, sub_id, "running")

        # 全量子任务状态快照（供前端执行树实时渲染；每次进入 executor 都会更新）
        _tp = state.get("task_plan")
        _goal = getattr(_tp, "goal", "") if _tp else ""
        add_event("executor", {
            "goal": _goal[:80],
            "subtasks": [{"id": s["id"], "desc": s["description"][:40], "status": s["status"]} for s in mm.get_subtasks(task_plan_id)],
        }, thread_id)

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

执行纪律（必须遵守）：
1. 探索（ls/read/grep/搜索）最多 3 次工具调用，之后必须直接撰写内容。
2. 外部搜索（tavily）不可用时，基于你的既有知识直接撰写，不要反复重试搜索。
3. 必须调用 write_file 产出文件到 deliverables 目录，文件大小 > 0 才算完成。
4. 不要在没有产出文件的情况下结束；如果已完成撰写但文件没保存，立即补一次 write_file。
5. 如果 deliverables 中已存在本任务的产出文件（之前规划/执行生成过），直接在其基础上完善/覆盖，不要重新从零探索。
6. 输出：FINAL_ANSWER: [结果] 并附上文件名。
"""

        initial_messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=f"执行子任务：{description}")
        ]

        # 需审批的工具（与 chatbot 一致）：命令执行 + 文件写/改/删
        _APPROVAL_TOOLS = ("execute_command", "write_file", "edit_file", "delete_file")

        def _on_tool_before(name, params):
            if name in ("execute_command",):
                cmd = params.get('command', '')
                for d in ['rm -rf /', 'dd ', 'mkfs', 'format', 'shutdown']:
                    if d in cmd:
                        print(f"⚠️ [安全] 阻止: {cmd}")
                        return False
            return True

        def _on_tool_after(name, params, result):
            # 记录操作历史（所有工具调用审计）
            try:
                if name in ("execute_command", "system_command"):
                    mm.add_command_history(thread_id, params.get('command', ''), success=True,
                                           stdout_preview=str(result)[:2000])
                elif name in ("write_file", "edit_file", "delete_file"):
                    op = {"write_file": "写入文件", "edit_file": "编辑文件", "delete_file": "删除文件"}.get(name, name)
                    mm.add_command_history(thread_id, f"{op}: {params.get('path', '')}", success=True,
                                           stdout_preview=str(result)[:2000])
                else:
                    # 其余工具（ls/read/glob/grep/tavily/memory 等）补记审计
                    summary = _tool_params_summary(name, params)
                    mm.add_command_history(thread_id, f"{name}: {summary}", success=True,
                                           stdout_preview=str(result)[:500])
            except Exception:
                pass
            add_log_entry("info", f"工具: {name}", {"params": params, "result_preview": str(result)[:200]})
            add_event("tool_call", {"name": name, "params": params}, thread_id)

        def _interrupt_handler(name, params):
            if name in _APPROVAL_TOOLS:
                # 审批模式优先级：前端按钮设置的 _CONFIG > graph state
                try:
                    from app.server import config as _srv_cfg
                    cfg_mode = (_srv_cfg._CONFIG or {}).get("configurable", {}).get("approval_mode")
                except Exception:
                    cfg_mode = None
                mode = cfg_mode or state.get("approval_mode", "per_ask")
                if mode == "per_ask":
                    desc = params.get('command') or params.get('path') or ''
                    resp = interrupt({"question": f"执行操作？\n{name}: {desc}", "command": desc, "mode": mode})
                    allow = resp.get("allow", False)
                    new_mode = resp.get("mode")
                    if new_mode and new_mode in ("per_ask", "session_allow", "always_allow"):
                        state["approval_mode"] = new_mode
                        print(f"[审批模式] {new_mode}")
                    if not allow:
                        try:
                            mm.add_command_history(thread_id, f"{name}: {desc}", success=False,
                                                   stdout_preview="[用户拒绝]")
                        except Exception:
                            pass
                    return allow, None
            return True, None

        loop = ReActLoop(llm_with_tools, max_iterations=5)
        final_result = None
        artifacts = []
        status = "failed"
        # 子任务执行：瞬时 LLM/工具失败自动重试（最多 2 次），避免一次失败就触发重规划
        for attempt in range(3):
            try:
                result = loop.run(
                    messages=initial_messages, tools=tools_list, state=state,
                    on_tool_before=_on_tool_before, on_tool_after=_on_tool_after,
                    interrupt_handler=_interrupt_handler,
                )
                final_result = result["final_answer"]
                artifacts = extract_artifacts(final_result)
                status = "done"
                break
            except Exception as e:
                # 审批信号（GraphInterrupt）必须冒泡到 graph 层触发人工审批，不能当失败
                from langgraph.errors import GraphInterrupt
                if isinstance(e, GraphInterrupt):
                    raise
                print(f"  ⚠️ 子任务执行异常（第 {attempt + 1}/3 次）: {type(e).__name__}: {str(e)[:120]}")
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
        add_event("executor", {
            "subtask": sub_id, "status": status,
            "goal": (getattr(state.get("task_plan"), "goal", "") or "")[:80],
            # 附带最新全量快照：DB 已写入本次状态，前端刷新/重放时直接覆盖
            "subtasks": [{"id": s["id"], "desc": s["description"][:40], "status": s["status"]} for s in mm.get_subtasks(task_plan_id)],
        }, thread_id)

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
        # 重规划计数：本轮所有子任务执行完且有失败 → 递增（真正写回 graph state，
        # 注意路由函数里修改 state 无效，必须在节点 return 时带上）
        any_failed = any(s["status"] == "failed" for s in updated_subtasks)
        if any_failed and all_done:
            state["_total_replans"] = state.get("_total_replans", 0) + 1
            print(f"[Executor] 有失败子任务，重规划计数 -> {state['_total_replans']}")
        if all_done:
            print("[Executor] 所有子任务已完成，标记 plan 为 done")
            # 同步更新 DB 主任务状态（子任务全 done 后主任务不应停留在 planning/executing）
            try:
                mm.update_task_plan_status(task_plan_id, "completed")
            except Exception:
                pass
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