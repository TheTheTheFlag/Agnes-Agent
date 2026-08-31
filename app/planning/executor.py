# planning/executor.py

import sys, os, json, re
from typing import List
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from langchain_core.messages import HumanMessage, ToolMessage, SystemMessage
from langchain_core.tools import tool
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
        from app.trace import record_node_start, record_node_end, record_tool
        record_node_start(thread_id, "executor")
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

        # ===== 阶段 3.5: 反馈修复模式（validator 失败原因回喂）=====
        # 有 _validation_message 时：不选新子任务，直接进入"修复轮"——LLM 读取失败文件并修正，
        # 修复完成后回到 validator 再验证。修复目标从失败信息中提取（或修复最后产出文件）。
        nxt = None
        fix_msg = state.get("_validation_message") or ""
        if fix_msg:
            sub_id = "fix"
            description = f"修复验证失败的问题（读取失败文件并修正后重新写入 deliverables）"
            print(f"  🔧 修复模式: {fix_msg[:100]}")
            # 进入修复分支
        else:
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
        if sub_id != "fix":
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

        # 收集依赖产出（直接从 DB 查，避免依赖已删除的 subtasks/executable 局部变量）
        deps_artifacts = []
        _dep_ids = nxt.get("dependencies", []) if nxt else []
        for dep_id in _dep_ids:
            dep_sub = next((s for s in mm.get_subtasks(task_plan_id) if s["id"] == dep_id), None)
            if dep_sub and dep_sub.get("artifacts"):
                deps_artifacts.extend(dep_sub["artifacts"])
        deps_artifacts = list(set(deps_artifacts))

        artifacts_context = f"\n【已有产出】\n" + "\n".join(deps_artifacts) if deps_artifacts else ""

        # 目标从 DB 读（state 不缓存）
        goal = (mm.get_task_plan_by_thread(thread_id) or {}).get("goal", "")

        system_prompt = f"""你是执行子任务 {sub_id} 的执行器。目标：{goal}
{artifacts_context}
{f'''【上次验证失败，需要修复】
验证器反馈的问题：
{fix_msg}

修复要求：读取失败/相关的文件，修正问题后重新写入 deliverables。只修验证器指出的问题，不要重写整个项目。
''' if fix_msg else ''}
执行纪律（必须严格遵守）：
1. 【探索限制】探索类工具（ls/glob/read_file）最多调用 2 次，之后必须直接撰写并写入文件。
2. 【唯一产出】调用 write_file 写入 deliverables 目录（或已存在的交付文件），文件内容完整、大小>0。
3. 【禁止空转】不要重复写入同一文件、不要为"凑工具调用"而调用工具。一次写完整。
4. 【收尾】写完文件后调用 complete_subtask 工具声明完成（files 参数列出实际写入的文件路径），不要继续探索；只输出文字不算完成。
5. 【失败即返回】若无法产出文件（如依赖缺失、权限错误），调用 fail_subtask 工具声明失败（reason 参数写原因），不要重试多次。
"""

        initial_messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=f"执行子任务：{description}")
        ]

        # 需审批的工具（与 chatbot 一致）：命令执行 + 文件写/改/删
        _APPROVAL_TOOLS = ("execute_command", "write_file", "edit_file", "delete_file")

        # 本子任务实际写入的文件路径（_on_tool_after 收集；比 extract_artifacts 从文本猜更可靠）
        _written_files: List[str] = []

        # 结构化完成/失败声明（工业方案：替代 FINAL_ANSWER 文本约定）。
        # 模型调用 complete_subtask/fail_subtask 工具来声明结果，系统校验后据此判定；
        # 若模型不调用，则由"是否有真实写入文件"（系统观测）兜底判定。
        _outcome: dict = {"status": None, "reason": "", "files": []}

        @tool
        def complete_subtask(description: str, files: List[str]) -> str:
            """声明当前子任务完成。description: 完成说明（一句话即可）；files: 产出文件路径列表（相对项目根，如 deliverables/snake_game/index.html）。系统会校验这些文件是否真实存在。"""
            existed, missing = [], []
            for f in files or []:
                p = str(f).replace("\\", "/")
                if p and os.path.exists(p):
                    existed.append(p)
                else:
                    missing.append(p)
            _outcome["status"] = "done"
            _outcome["description"] = description
            _outcome["files"] = list(dict.fromkeys(existed + _written_files))
            return f"已声明完成。文件校验通过: {existed or '无'}; 缺失: {missing or '无'}; 系统已记录写入: {_written_files or '无'}"

        @tool
        def fail_subtask(reason: str) -> str:
            """声明当前子任务失败。reason: 失败原因（将作为子任务失败结果记录）。"""
            _outcome["status"] = "failed"
            _outcome["reason"] = reason or ""
            return f"已声明失败: {reason}"

        # 工具调用硬约束（不依赖 LLM 自觉，ReActLoop 层强制）：
        #   - 探索类（ls/glob/read_file/execute_command）累计 ≤2 次，超限拒绝
        #   - 写入类（write_file/edit_file/delete_file）累计 ≤8 次，超限拒绝
        #   - write_file 路径必须 deliverables 开头（禁止写根目录/其他位置）
        _tool_usage = {"explore": 0, "write": 0}
        _EXPLORE_TOOLS = ("ls", "glob_files", "read_file", "execute_command")
        _WRITE_TOOLS = ("write_file", "edit_file", "delete_file")

        def _on_tool_before(name, params):
            if name in ("execute_command",):
                cmd = params.get('command', '')
                for d in ['rm -rf /', 'dd ', 'mkfs', 'format', 'shutdown']:
                    if d in cmd:
                        print(f"⚠️ [安全] 阻止: {cmd}")
                        return False
            # 探索上限
            if name in _EXPLORE_TOOLS:
                if _tool_usage["explore"] >= 2:
                    print(f"⛔ 探索次数已达上限（2），拒绝 {name}，请直接撰写并写入文件")
                    return False
                _tool_usage["explore"] += 1
            # 写入上限
            if name in _WRITE_TOOLS:
                if _tool_usage["write"] >= 8:
                    print(f"⛔ 写入次数已达上限（8），拒绝 {name}，请结束")
                    return False
                _tool_usage["write"] += 1
            # write_file 强制 deliverables 前缀
            if name in ("write_file", "edit_file", "delete_file"):
                path = str(params.get('path', '') or '')
                norm = path.replace('\\', '/')
                if not norm.startswith('deliverables/'):
                    print(f"⛔ 路径必须写入 deliverables/：{path}")
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
                    # 记录真实写入路径（成功才记），作为子任务产出文件的可靠来源
                    path = str(params.get('path', '') or '')
                    if name in ("write_file", "edit_file") and path and not str(result).startswith("工具执行错误"):
                        _written_files.append(path)
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
            # trace：工具调用（含参数/结果摘要）
            record_tool(thread_id, "executor", name, params, result)

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

        executor_tools = list(tools_list) + [complete_subtask, fail_subtask]
        # 关键：模型看到的工具 schema 来自 llm.bind_tools(...)，而不是 loop.run(tools=...)——
        # 后者只用于工具执行匹配。必须用 executor_tools 重新 bind，否则模型看不到
        # complete_subtask/fail_subtask（会误以为它们不存在）。
        loop = ReActLoop(llm.bind_tools(executor_tools), max_iterations=5, node="executor",
                         require_final_marker=True, terminate_tools={"complete_subtask", "fail_subtask"})
        final_result = None
        artifacts = []
        status = "failed"
        # 子任务执行：瞬时 LLM/工具失败自动重试（最多 2 次），避免一次失败就触发重规划
        for attempt in range(3):
            try:
                result = loop.run(
                    messages=initial_messages, tools=executor_tools, state=state,
                    on_tool_before=_on_tool_before, on_tool_after=_on_tool_after,
                    interrupt_handler=_interrupt_handler,
                )
                final_result = result["final_answer"]
                # 工业方案：完成/失败由系统观测 + 结构化终止工具决定，不依赖 FINAL_ANSWER 文本。
                #   - fail_subtask 被调用 → failed（工具写出的 reason）
                #   - complete_subtask 被调用，或系统观测到真实写入文件（_written_files）→ done
                #   - 两者都没有（轮次耗尽/纯文本）→ failed
                if _outcome["status"] == "failed":
                    status = "failed"
                    final_result = f"子任务失败（fail_subtask）: {_outcome['reason'] or final_result}"
                elif _outcome["status"] == "done" or _written_files:
                    status = "done"
                    # 产出以真实写入路径为准（系统观测），complete_subtask 校验通过的 files 与
                    # 文本提取的文件名仅作补充记录
                    artifacts = list(dict.fromkeys(_written_files + _outcome["files"] + extract_artifacts(final_result)))
                else:
                    status = "failed"
                    final_result = (final_result or "") + "\n[自动判定] 未调用任何写入工具、也未声明完成/失败（结构化终止工具 complete_subtask/fail_subtask 均未调用）。"
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

        # 完成判定兜底：done 但无产出文件 → 降级为 failed（避免 validator 拿到空产出）
        if status == "done" and not artifacts:
            status = "failed"
            final_result = (final_result or "") + "\n[自动判定] 标记完成但无产出文件，降级失败以触发反馈修复。"
            print(f"  ⚠️ {sub_id} done 但无产出文件 → 降级 failed")
        if status == "failed" and not final_result:
            final_result = "执行失败（无产出）"
        # 修复模式不写真实子任务（sub_id="fix"）；正常执行才更新 DB
        if sub_id != "fix":
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
        # 重规划计数：只要 DB 里仍存在失败子任务就计数（route_after_executor 依据 failed>0
        # 决定是否重规划，计数必须与之对应，否则"子任务中途失败但未全部完成"时 replans 永不
        # 递增，重规划上限 2 次失效 → planner→executor→planner 无限循环）。
        progress = mm.get_plan_progress(task_plan_id)
        replans = state.get("_total_replans", 0)
        if progress['any_failed']:
            replans = replans + 1
            print(f"[Executor] 存在失败子任务，_total_replans++ -> {replans}")
        # 修复模式：执行完成后清除修复信号，让 validator 重新验证
        ret_extra = {}
        if sub_id == "fix":
            ret_extra = {"_validation_message": "", "_fix_attempts": 0}
        record_node_end(thread_id, "executor", f"{'修复' if sub_id == 'fix' else '子任务' + str(sub_id)} {status}")
        return {"task_plan_id": task_plan_id, "_total_replans": replans, **ret_extra}
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