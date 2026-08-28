# planning/executor.py

import sys, os, json, re
from typing import List
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from langchain_core.messages import HumanMessage, ToolMessage, SystemMessage
from langgraph.types import interrupt
from app.graph.state import State
from app.graph.utils import _tool_params_summary
from app.planning.react_loop import ReActLoop
from app.server import add_log_entry, add_event
from app.config import DB_PATH


def create_executor_node(llm, tools_list):
    """Executor 节点：每轮只做一件事 —— 选下一个 pending 子任务 → 执行 → 写回结果。
    数据流：state 只传 thread_id/task_plan_id，所有子任务/进度决策都从 DB 读（唯一真相源）。
    """
    llm_with_tools = llm.bind_tools(tools_list)

    def executor_node(state: State):
        print("[Executor] 进入")
        thread_id = state.get("thread_id", "default")
        from app.memory import MemoryManager
        mm = MemoryManager(db_path=DB_PATH, thread_id=thread_id)

        # ===== 阶段 1: 查 task_plan_id（DB 唯一来源）=====
        plan = mm.get_task_plan_by_thread(thread_id)
        if not plan:
            print(f"[Executor] 无任务计划 thread={thread_id}")
            return {}
        task_plan_id = plan["id"]

        # ===== 阶段 2: 清理卡死 running（DB 操作，幂等）=====
        stale = mm.recover_stale_running(task_plan_id, timeout_seconds=300)
        if stale:
            print(f"[Executor] 恢复 {stale} 个卡死 running 子任务为 failed")

        # ===== 阶段 3: 检查是否全部完成（DB 查）=====
        progress = mm.get_plan_progress(task_plan_id)
        print(f"[Executor] 进度: {progress}")
        if progress['all_done']:
            print("[Executor] 所有子任务已完成，进验证/汇总")
            return {"thread_id": thread_id}

        # ===== 阶段 4: 选下一个子任务（DB 决策，避开 running）=====
        nxt = mm.get_next_executable_subtask(task_plan_id)
        if nxt is None:
            # DB 找不到 pending 但还有 running（应被 recover 清理了）；兜底返回
            print("[Executor] 找不到可执行子任务，退出")
            return {"thread_id": thread_id}

        sub_id = nxt['id']
        description = nxt['description']
        print(f"  ▶️ 执行 {sub_id}: {description[:50]}...")

        # ===== 阶段 5: 标 running + 推送事件（DB 写一次）=====
        mm.update_subtask_status(task_plan_id, sub_id, "running")
        # executor 事件（前端执行树用）：子任务序号 + 全量子任务状态（DB 当前快照）
        add_event("executor", {
            "goal": (mm.get_task_plan_by_thread(thread_id) or {}).get("goal", "")[:80],
            "subtask": sub_id,
            "subtasks": [{"id": s["id"], "desc": s["description"][:40], "status": s["status"]}
                          for s in mm.get_subtasks(task_plan_id)],
        }, thread_id)
        # 节点思考气泡
        add_event("node_thought", {
            "role": "executor",
            "title": f"⚙️ 子任务{sub_id}：{description[:60]}",
            "text": f"目标：{(mm.get_task_plan_by_thread(thread_id) or {}).get('goal', '')[:80]}\n子任务：{description}",
            "subtask": sub_id,
        }, thread_id)

        # 收集依赖产出
        deps_artifacts = []
        for dep_id in executable.get("dependencies", []):
            dep = next((s for s in subtasks if s["id"] == dep_id), None)
            if dep and dep.get("artifacts"):
                deps_artifacts.extend(dep["artifacts"])
        deps_artifacts = list(set(deps_artifacts))

        artifacts_context = f"\n【已有产出】\n" + "\n".join(deps_artifacts) if deps_artifacts else ""

        # 目标从 DB 读（state 不缓存）
        goal = (mm.get_task_plan_by_thread(thread_id) or {}).get("goal", "")

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
            # subtask 字段：标记工具归属的当前子任务，前端执行树据此把工具挂到对应子任务下
            add_event("tool_call", {"name": name, "params": params, "subtask": sub_id}, thread_id)

        def _interrupt_handler(name, params):
            if name in _APPROVAL_TOOLS:
                # 审批模式：只读 _srv_cfg._CONFIG，默认 session_allow（不写 state）
                try:
                    from app.server import config as _srv_cfg
                    mode = (_srv_cfg._CONFIG or {}).get("configurable", {}).get("approval_mode") or "session_allow"
                except Exception:
                    mode = "session_allow"
                if mode == "per_ask":
                    desc = params.get('command') or params.get('path') or ''
                    resp = interrupt({"question": f"执行操作？\n{name}: {desc}", "command": desc, "mode": mode})
                    allow = resp.get("allow", False)
                    new_mode = resp.get("mode")
                    if new_mode and new_mode in ("per_ask", "session_allow", "always_allow"):
                        try:
                            from app.server import config as _srv_cfg
                            if _srv_cfg._CONFIG is None:
                                _srv_cfg._CONFIG = {"configurable": {}}
                            _srv_cfg._CONFIG.setdefault("configurable", {})["approval_mode"] = new_mode
                        except Exception:
                            pass
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
            add_event("node_thought", {
                "role": "executor-result",
                "title": f"✅ 子任务{sub_id} 完成",
                "text": f"产出：{', '.join(artifacts) if artifacts else '（无文件）'}\n结果摘要：{(final_result or '')[:200]}",
                "subtask": sub_id,
            }, thread_id)
        else:
            print(f"  ❌ 失败")
            add_log_entry("error", f"子任务 {sub_id} 失败", {"error": final_result[:100]})
            add_event("node_thought", {
                "role": "executor-result",
                "title": f"❌ 子任务{sub_id} 失败",
                "text": (final_result or '')[:300],
                "subtask": sub_id,
            }, thread_id)
        # 全量快照事件：DB 刚更新，前端用此覆盖执行树
        add_event("executor", {
            "subtask": sub_id, "status": status,
            "goal": (mm.get_task_plan_by_thread(thread_id) or {}).get("goal", "")[:80],
            "subtasks": [{"id": s["id"], "desc": s["description"][:40], "status": s["status"]}
                          for s in mm.get_subtasks(task_plan_id)],
        }, thread_id)

        # ===== 阶段 6: 写回结果（DB 唯一来源）=====
        # 重规划计数：全部完成 + 有失败 → 计数（路由函数从 state 读，所以必须 return）
        progress = mm.get_plan_progress(task_plan_id)
        replans = state.get("_total_replans", 0)
        if progress['all_done'] and progress['any_failed']:
            replans = replans + 1
            print(f"[Executor] 全部完成但有失败，_total_replans++ -> {replans}")
        # 不再重建 state.task_plan（state 不缓存子任务列表）；路由/validator 都从 DB 读
        return {"task_plan_id": task_plan_id, "_total_replans": replans}
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