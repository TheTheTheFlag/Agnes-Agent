"""app.planning.dag_executor — Plan-and-Execute 的 DAG 执行器

对标文章四层的落地（3 状态机/Checkpoint、4 局部重规划），复用现有 ReActLoop
做单节点执行，用线程池+信号量做同层并行。

执行流程（每进入 executor 节点一轮）：
  1. 从 DB 读 plan + nodes + edges；recover 卡死 running
  2. 用 dag_core 计算：失败隔离（强依赖失败 → 后继 skipped）、软依赖、当前 ready 批
  3. 并发执行 ready 批（线程池 + 信号量限并发），逐节点 ReActLoop + complete/fail 终止工具
  4. 节点结果写回 DB + save_checkpoint；软依赖失败只标注缺数据
  5. 推送 DAG 事件（结构 + 节点状态 + replan）供前端 vis-network 渲染
  6. 结束后由 router 决定：还有 ready/未完成 → 继续；全部终态 → 结束/验证

局部重规划（④）：不在本模块内嵌——由 router 检测"存在 failed 节点"后触发
一个窄 replan 动作：仅把 failed 节点及其强依赖后继摘出，交给 LLM 重出这一段，
替换回原 DAG 继续。上限 3 次见 dag_plans.replan_count。
"""

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import BoundedSemaphore
from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.types import interrupt

from app.config import DB_PATH
from app.graph.state import State
from app.graph.utils import _tool_params_summary
from app.memory import MemoryManager
from app.planning import dag_core as core
from app.planning.dag_storage import DAGStorage
from app.planning.react_loop import ReActLoop
from app.server import add_event, add_log_entry
from app.graph._agent_prompt import build_dag_executor_system_prompt

# 工具调用硬约束（沿用旧 executor）：探索 ≤2、写入 ≤8、路径必须 deliverables/
_EXPLORE_TOOLS = ("ls", "glob_files", "read_file", "execute_command")
_WRITE_TOOLS = ("write_file", "edit_file", "delete_file")
_APPROVAL_TOOLS = ("execute_command", "write_file", "edit_file", "delete_file")


def _extract_artifacts(text: str) -> List[str]:
    """从文本提取文件名（复用旧 executor 的启发式）。"""
    import re
    if not text:
        return []
    out = []
    for pat in (r'([\w\-\./]+\.(?:html|js|css|py|txt|json|md))',):
        for m in re.findall(pat, text, re.I):
            if not re.search(r'\.(pyc|log|tmp|swp|bak)$', m):
                out.append(m)
    return list(dict.fromkeys(out))


def _has_real_artifact(files) -> bool:
    """声明的产物里是否至少有一个真实存在于磁盘上。

    用于"未落盘不许声明完成"的硬约束：模型常见的失败模式是只输出一句
    "直接产出两个文件。"就调 complete_node（files 传空或传不存在的路径），
    事后只能由 _run_node 把 success 改判 failed，白跑一整轮才触发重规划。
    """
    for f in files or []:
        p = str(f).replace("\\", "/").strip()
        if p and os.path.exists(p):
            return True
    return False


def _normalize_declared_files(files) -> List[str]:
    """规范化 complete_node 声明的文件路径（去空、统一斜杠、去重）。"""
    out = []
    for f in files or []:
        p = str(f).replace("\\", "/").strip()
        if p:
            out.append(p)
    return list(dict.fromkeys(out))


# 契约解析函数（expected_artifacts / missing_expected）已下沉到 dag_core：
# 它们是纯函数，planner（统计契约遵守率）与 executor（complete_node 硬校验）都要用，
# 放 core 可避免 dag_planner → dag_executor 的跨模块耦合。此处保留本地别名，调用点无需改动。
_expected_artifacts = core.expected_artifacts
_missing_expected = core.missing_expected


def _run_node(thread_id: str, task_plan_id: int, node: Dict, goal: str,
              parent_artifacts: List[str], missing_soft: List[str],
              llm_builder, tools_list, mm, dag: DAGStorage, on_event):
    """执行单个节点（线程池 worker）。返回 (node_id, status, result, artifacts)。"""
    node_id = node["id"]
    description = node["description"]
    on_event("executor", {"subtask": node_id, "status": "running",
                          "goal": goal[:80],
                          "nodes": _snapshot_for_event(dag, task_plan_id),
                          "edges": _snapshot_edges_for_event(dag, task_plan_id)})
    dag.set_node_status(task_plan_id, node_id, core.STATUS_RUNNING)
    dag.save_checkpoint(task_plan_id)

    artifacts_context = ""
    if parent_artifacts:
        artifacts_context = "\n【已有产出】\n" + "\n".join(parent_artifacts)
    if missing_soft:
        artifacts_context += "\n\n【提示】以下软依赖节点未能成功，其结果缺失（本次不阻塞，但结论可能不完整）：\n" + \
                             "\n".join(f"- {m}" for m in missing_soft)

    # 契约前置：解析本节点"应产出哪些文件"，用于 system prompt 提示与 complete_node 校验
    expected_artifacts = _expected_artifacts(node)

    system_prompt = build_dag_executor_system_prompt(
        node_id=node_id,
        description=description,
        goal=goal,
        tool_names=", ".join(t.name for t in tools_list),
        artifacts_context=artifacts_context,
        acceptance_criteria=(node.get("acceptance_criteria") or ""),
    )

    _outcome = {"status": None, "reason": "", "files": []}
    _written: List[str] = []
    _usage = {"explore": 0, "write": 0}

    # 不要用 @tool 装饰器：langchain 1.6 对嵌套闭包函数上的 @tool 装饰有兼容问题
    # （会抛 "Function must have a docstring" 或 "first argument must be string or callable"）。
    # 改用 StructuredTool.from_function 显式包装，绕开装饰器问题。
    from langchain_core.tools import StructuredTool

    def complete_node_plain(description: str, files: List[str]) -> str:
        """声明子任务完成。files 列出本节点产出的实际文件路径（写入 deliverables/）。"""
        existed, missing = [], []
        for f in files or []:
            p = str(f).replace("\\", "/")
            if p and os.path.exists(p):
                existed.append(p)
            else:
                missing.append(p)
        _outcome["status"] = "success"
        _outcome["description"] = description
        _outcome["files"] = list(dict.fromkeys(existed + _written))
        return f"已声明完成，文件校验通过: {existed or '无'}; 缺失: {missing or '无'}"

    def fail_node_plain(reason: str) -> str:
        """声明子任务失败。"""
        _outcome["status"] = "failed"
        _outcome["reason"] = reason or ""
        return f"已声明失败: {reason}"

    complete_node_tool = StructuredTool.from_function(
        func=complete_node_plain,
        name="complete_node",
        description="声明子任务完成并列出产出文件路径（写入 deliverables/）。框架会校验文件存在后把节点置 success。",
    )
    fail_node_tool = StructuredTool.from_function(
        func=fail_node_plain,
        name="fail_node",
        description="声明子任务失败并给出原因（API 错误 / 工具受限 / 资料缺失等），框架会跳过强依赖该节点的下游并触发局部重规划。",
    )
    # 替换闭包内 @tool 装饰过的同名引用为新工具（向后兼容旧代码中 complete_node / fail_node 的引用）
    complete_node = complete_node_tool
    fail_node = fail_node_tool

    def _on_tool_before(name, params):
        """工具调用前置校验。返回 (allow, reason)：
          - allow=True, reason=""     → 放行
          - allow=False, reason="..." → 拒因，react_loop 会把它写进 ToolMessage 让 LLM 看到并改正
        """
        if name == "execute_command":
            cmd = params.get("command", "")
            for d in ("rm -rf /", "dd ", "mkfs", "format", "shutdown"):
                if d in cmd:
                    return False, f"[安全策略] execute_command 命令 '{cmd[:80]}' 包含危险关键字 '{d}'，已被拒绝。"
        if name == "complete_node":
            # 硬约束：没有真实产物就不允许声明完成。
            # 线上事故：模型只输出一句"直接产出两个文件。"就调 complete_node（files 传空或
            # 传不存在的路径），success 只能由 _run_node 事后改判 failed，白跑一整轮。
            # 这里前置拦下，拒因会作为 ToolMessage 回给模型逼它先 write_file；
            # react_loop 自带"连续被拒 3 次强制终止"兜底，不会因此空转。
            declared = _normalize_declared_files(params.get("files"))
            if not _has_real_artifact(declared):
                return False, (
                    "[未落盘] complete_node 被拒绝：本节点还没有任何真实产物。"
                    f"你声明的 files={declared or '（空）'} 在磁盘上都不存在。"
                    "请先用 write_file 把完整内容写入 deliverables/（路径必须以 'deliverables/' 开头，"
                    "例如 'deliverables/tetris/index.html'），确认文件真实存在后再调用 complete_node；"
                    "若确实无法产出，请改调 fail_node 说明卡在哪里。"
                )
            # 硬约束 2（契约前置）：验收标准里要求的产出文件必须被覆盖。
            # 只在解析出明确产物路径时生效（旧计划没有该信息则不拦，避免误伤）。
            if expected_artifacts:
                miss = _missing_expected(declared, expected_artifacts)
                if miss:
                    return False, (
                        f"[契约未满足] complete_node 被拒绝：本节点验收标准要求产出 {expected_artifacts}，"
                        f"但你声明的 files 里缺少：{miss}。"
                        f"请用 write_file 补齐这些文件（路径与验收标准一致）后再声明完成；"
                        f"若确实无法产出，请改调 fail_node 说明原因。"
                    )
        if name in _EXPLORE_TOOLS:
            if _usage["explore"] >= 99:
                return False, (
                    f"[安全策略] 探索类工具（{name}）调用次数已达上限（99 次）。"
                    f"请直接调用写文件或 execute_command 产出结果，不要再调用 read_file/glob/ls 等探索工具。"
                )
            _usage["explore"] += 1
        if name in _WRITE_TOOLS:
            if _usage["write"] >= 99:
                return False, (
                    f"[安全策略] 写文件类工具（{name}）调用次数已达上限（99 次）。"
                    f"请合并到更少的文件里或直接调用 complete_node 声明完成，不要再调写工具。"
                )
            _usage["write"] += 1
            path = str(params.get("path", "") or "").replace("\\", "/")
            # 增强校验：path 为空时给出明确提示
            if not path:
                return False, (
                    f"[参数错误] 工具 {name} 缺少必需的 'path' 参数。"
                    f"请在工具调用中提供完整路径，例如：{{\"path\": \"deliverables/xxx.html\", \"content\": \"...\"}}"
                )
            if not path.startswith("deliverables/"):
                return False, (
                    f"[路径错误] 写文件路径必须以 'deliverables/' 开头（产出统一归档），"
                    f"当前路径 '{path}' 不符合。"
                    f"请把文件路径改成 'deliverables/xxx' 形式（例如 'deliverables/snake/index.html'、"
                    f"'deliverables/snake/style.css'、'deliverables/snake/app.js'）。"
                )
        return True, ""

    def _on_tool_after(name, params, result):
        try:
            mm.add_command_history(thread_id, f"{name}: {_tool_params_summary(name, params)}", success=True,
                                   stdout_preview=str(result)[:500])
        except Exception:
            pass
        if name in ("write_file", "edit_file") and str(params.get("path", "") or ""):
            _written.append(str(params["path"]).replace("\\", "/"))
        add_event("tool_call", {"name": name, "params": params, "node": node_id, "result": str(result)}, thread_id)
        on_event(None, None)  # no-op keep signature

    def _interrupt_handler(name, params):
        if name in _APPROVAL_TOOLS:
            try:
                from app.server import config as _srv_cfg
                mode = (_srv_cfg._CONFIG or {}).get("configurable", {}).get("approval_mode") or "session_allow"
            except Exception:
                mode = "session_allow"
            if mode == "per_ask":
                desc = params.get("command") or params.get("path") or ""
                resp = interrupt({"question": f"执行操作？\n{name}: {desc}", "command": desc, "mode": mode})
                allow = resp.get("allow", False)
                new_mode = resp.get("mode")
                if new_mode in ("per_ask", "session_allow", "always_allow"):
                    try:
                        from app.server import config as _srv_cfg
                        if _srv_cfg._CONFIG is None:
                            _srv_cfg._CONFIG = {"configurable": {}}
                        _srv_cfg._CONFIG.setdefault("configurable", {})["approval_mode"] = new_mode
                    except Exception:
                        pass
                return allow, None
        return True, None

    node_tools = list(tools_list) + [complete_node, fail_node]
    # max_iterations=12：executor 单子任务需要"探索+写入+校验+complete_node"多轮，
    # 历史 5 太紧，模型会卡在最后一两次没机会收尾；reactive_loop 内还有
    # _NO_PROGRESS_LIMIT 强制收尾兜底（连续 3 轮无 tool_calls 直接 break），
    # 不会因 12 而显著增加 LLM 调用——只在模型真正有动作时跑满。
    loop = ReActLoop(llm_builder[0].bind_tools(node_tools), max_iterations=12, node="executor",
                     require_final_marker=True, terminate_tools={"complete_node", "fail_node"})

    final_result = None
    status = core.STATUS_FAILED
    artifacts: List[str] = []
    # 去掉外层 for attempt in range(3)：内层 tenacity 已自带 3 次重试（15/30/60s 退避），
    # 外层再 3 次会撞 LangGraph 节点超时并导致 set_node_status 跑不到、节点卡 running。
    # 这里只调一次 loop.run，任何异常（LLM 失败/工具异常）都走 except 走 failed。
    try:
        r = loop.run(messages=[
            SystemMessage(content=system_prompt),
            HumanMessage(content=f"执行子任务：{description}"),
        ], tools=node_tools, state={}, on_tool_before=_on_tool_before,
            on_tool_after=_on_tool_after, interrupt_handler=_interrupt_handler)
        final_result = r["final_answer"]
        if _outcome["status"] == "failed":
            status = core.STATUS_FAILED
            final_result = f"节点失败（fail_node）: {_outcome['reason'] or final_result}"
        elif _outcome["status"] == "success" or _written:
            status = core.STATUS_SUCCESS
            artifacts = list(dict.fromkeys(_written + _outcome["files"] + _extract_artifacts(final_result or "")))
        else:
            status = core.STATUS_FAILED
            final_result = (final_result or "") + "\n[自动判定] 未调用任何写入工具且未声明完成/失败。"
    except Exception as e:
        from langgraph.errors import GraphInterrupt
        if isinstance(e, GraphInterrupt):
            raise  # 审批中断必须冒泡
        final_result = f"执行异常: {type(e).__name__}: {str(e)[:300]}"
        status = core.STATUS_FAILED
        artifacts = []

    if status == core.STATUS_SUCCESS and not artifacts:
        status = core.STATUS_FAILED  # done 但无产出 → 失败，触发反馈
    # finally 兜底：无论如何都把 status 写回 DB，避免节点卡在 running。
    # 注意：GraphInterrupt 必须冒泡（外层 langgraph 状态机要 resume），
    # 所以 finally 写在 except-GraphInterrupt 之后。
    dag.set_node_status(task_plan_id, node_id, status, result=final_result, artifacts=artifacts)
    dag.save_checkpoint(task_plan_id)
    on_event("executor", {"subtask": node_id, "status": status, "goal": goal[:80],
                          "nodes": _snapshot_for_event(dag, task_plan_id),
                          "edges": _snapshot_edges_for_event(dag, task_plan_id)})
    return node_id, status, final_result, artifacts


def _snapshot_for_event(dag: DAGStorage, plan_id: int) -> List[Dict]:
    """把当前 DAG 节点状态转成前端事件快照（vis-network 用）。"""
    return [{"id": n["id"], "status": n["status"], "description": n["description"][:40]}
            for n in dag.get_nodes(plan_id)]


def _snapshot_edges_for_event(dag: DAGStorage, plan_id: int) -> List[Dict]:
    """把当前 DAG 边转成前端事件快照（供前端 updateTodoFromNodes 做拓扑分层用）。"""
    return [{"from": e["from"], "to": e["to"], "soft": bool(e.get("soft", False))}
            for e in dag.get_edges(plan_id)]


def create_executor(llm_builder, tools_list):
    """返回 executor 节点函数。llm_builder 为一个元素的 list，包住可 bind_tools 的 llm 实例，
    便于在线程池中共享（每线程各自 bind_tools）。"""
    def executor_node(state: State):
        thread_id = state.get("thread_id", "default")
        from app.trace import record_node_start, record_node_end
        record_node_start(thread_id, "executor")
        dag = DAGStorage(DB_PATH)
        mm = MemoryManager(db_path=DB_PATH, thread_id=thread_id)

        plan = dag.get_plan_by_thread(thread_id)
        if not plan:
            record_node_end(thread_id, "executor", "无进行中计划")
            return {"thread_id": thread_id}

        # 局部重规划：存在 failed 节点 → 摘出受影响子图重规划（④）
        replan_requested = False
        failed_ids = [n["id"] for n in dag.get_nodes(plan["id"]) if n["status"] == core.STATUS_FAILED]
        if failed_ids and plan["replan_count"] < core.MAX_REPLAN:
            replan_requested = True

        dag.recover_stale_running(plan["id"])
        nodes = dag.get_nodes(plan["id"])
        edges = dag.get_edges(plan["id"])
        node_map = {n["id"]: n for n in nodes}
        node_map, edges = _reinstall_state(nodes, edges)

        if replan_requested:
            ok = _do_local_replan(plan, failed_ids, dag, node_map, edges, llm_builder, thread_id)
            if not ok:
                # 重规划没能产出替代节点（LLM 报错/返回空）→ 无法继续推进，收敛结束。
                # 用 _empty_streak=3 让 route_after_executor 直接走 summarizer，避免无限空转。
                add_log_entry("error", "局部重规划未产出新节点，结束本计划")
                dag.set_plan_status(plan["id"], "failed")
                record_node_end(thread_id, "executor", "局部重规划失败，结束")
                return {"thread_id": thread_id, "_empty_streak": 3}
            nodes = dag.get_nodes(plan["id"])
            # 重规划会新增节点与边：必须把 edges 一并重读，否则下面 compute_ready_batch
            # 仍按旧边算依赖，新节点会全部被当成"无前置"并发执行（依赖语义失效）。
            edges = dag.get_edges(plan["id"])
            node_map = {n["id"]: n for n in nodes}
            edges = [dict(e) for e in edges]

        # 计算失败隔离（强依赖失败 → 后继 skipped）与软依赖缺数据标注
        skips = core.compute_failure_skips(node_map, edges)
        for sid in skips:
            if node_map[sid]["status"] != core.STATUS_SUCCESS:
                dag.set_node_status(plan["id"], sid, core.STATUS_SKIPPED, result="前置强依赖失败，跳过")
        # 重新读取一次状态（skipped 可能让更多节点变 ready）
        nodes = dag.get_nodes(plan["id"])
        node_map = {n["id"]: n for n in nodes}

        # 计算 ready 批（本批并行）
        ready = core.compute_ready_batch(node_map, edges)
        if not ready:
            # 防 trace 里几百次"无可执行节点"死循环：连续空批 3 次说明任务真的卡住了
            # （要么所有节点终态、要么全卡在 running 超过 5 分钟），直接跳出进 summarizer
            empty_streak = int(state.get("_empty_streak", 0)) + 1
            record_node_end(thread_id, "executor",
                            f"无可执行节点 (连续 {empty_streak}/3 次)")
            if empty_streak >= 3:
                add_log_entry("info", f"[DAG] 连续 3 次无可执行节点，结束执行进 summarizer")
                return {"thread_id": thread_id, "_empty_streak": empty_streak}
            return {"thread_id": thread_id, "_empty_streak": empty_streak}
        # 本批有 ready，把 empty_streak 清零
        state_for_return = {"thread_id": thread_id, "_empty_streak": 0}

        goal = plan["goal"]
        # 并发执行 ready 批
        max_workers = min(len(ready), 4)
        sem = BoundedSemaphore(max_workers)
        results: Dict[str, Dict] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {}
            for nid in ready:
                futures[pool.submit(_run_node, thread_id, plan["id"],
                                    dict(node_map[nid]), goal,
                                    _parent_artifacts(node_map, edges, nid),
                                    _missing_soft(node_map, edges, nid),
                                    llm_builder, tools_list, mm, dag,
                                    _emit_dag_event(dag, plan["id"]))] = nid
            for fut in as_completed(futures):
                nid = futures[fut]
                try:
                    results[nid] = fut.result()
                except Exception as e:
                    results[nid] = (nid, core.STATUS_FAILED, f"执行异常: {e}", [])

        # 本级完成后再次传播 skipped（可能有新失败导致新 skip）
        nodes = dag.get_nodes(plan["id"])
        node_map2 = {n["id"]: n for n in nodes}
        skips2 = core.compute_failure_skips(node_map2, edges)
        for sid in skips2:
            if sid not in results:
                dag.set_node_status(plan["id"], sid, core.STATUS_SKIPPED, result="前置失败，跳过")

        dag.save_checkpoint(plan["id"])
        dag.set_plan_status(plan["id"], "executing")
        record_node_end(thread_id, "executor", f"本批 {len(ready)} 节点")
        return state_for_return
    return executor_node


def _emit_dag_event(dag: DAGStorage, plan_id: int):
    """返回 on_event 回调：把节点状态推给前端 SSE。"""
    def _cb(step, payload):
        # 发送节点状态更新事件到前端
        if payload and "nodes" in payload:
            add_event("executor", payload, None)  # thread_id 由 add_event 内部处理
    return _cb


def _reinstall_state(nodes: List[Dict], edges: List[Dict]):
    """把 DB 读到的 nodes/edges 规整为 dag_core 可用的 dict 形式。"""
    node_map = {n["id"]: dict(n) for n in nodes}
    return node_map, [dict(e) for e in edges]


def _parent_artifacts(node_map, edges, nid) -> List[str]:
    """获取当前节点的所有上下文 artifacts：
    - 直接父节点（强依赖）的 artifacts
    - 同批已完成的并行节点的 artifacts（通过检查所有成功节点并过滤掉当前节点自身）
    """
    out = []

    # 1. 直接父节点的 artifacts（强依赖）
    parents = [e["from"] for e in edges if e["to"] == nid]
    for p in parents:
        pn = node_map.get(p, {})
        if pn.get("status") == core.STATUS_SUCCESS:
            out.extend(pn.get("artifacts") or [])

    # 2. 同批并行节点中已完成的 artifacts（避免并行节点间信息孤岛）
    #    逻辑：所有成功节点的 artifacts，排除当前节点自身（因为还没执行）
    for other_nid, other_node in node_map.items():
        if other_nid != nid and other_node.get("status") == core.STATUS_SUCCESS:
            other_artifacts = other_node.get("artifacts") or []
            for art in other_artifacts:
                if art not in out:
                    out.append(art)

    return list(dict.fromkeys(out))


def _missing_soft(node_map, edges, nid) -> List[str]:
    """软依赖父失败/跳过的节点，标注缺数据。"""
    out = []
    for e in edges:
        if e["to"] == nid and e.get("soft"):
            ps = node_map.get(e["from"], {}).get("status")
            if ps in (core.STATUS_FAILED, core.STATUS_SKIPPED):
                out.append(node_map.get(e["from"], {}).get("description", e["from"]))
    return out


def _do_local_replan(plan: Dict, failed_ids: List[str], dag: DAGStorage,
                     node_map, edges, llm_builder, thread_id: str) -> bool:
    """④ 局部重规划：锁定已完成节点，只让 LLM 重出"失败节点"的替代子图。

    收敛后的语义（避免一个失败波及全图）：
      - affected        = 失败节点 + 已失败(failed)的强依赖后继（传递闭包）→ 需要重新拆解
      - rewire_children = 尚未执行(pending/ready)的强依赖后继 → 本身没问题，不重拆，
                          只把它们的入边从被替换节点重挂到替代节点，依赖关系无缝转移
    返回是否成功产出替代节点（False = LLM 失败/返回空，调用方应结束本计划）。
    """
    # ---- ① 需要重新拆解的节点：失败节点 + 已失败的强依赖后继（传递闭包）----
    affected = set(failed_ids)
    changed = True
    while changed:
        changed = False
        for e in edges:
            if e["from"] in affected and not e.get("soft") and e["to"] not in affected:
                if node_map.get(e["to"], {}).get("status") == core.STATUS_FAILED:
                    affected.add(e["to"])
                    changed = True

    # ---- ② 未执行的后继：不重拆，稍后把入边重挂到替代节点 ----
    rewire_children = set()
    for e in edges:
        if e["from"] in affected and not e.get("soft") and e["to"] not in affected:
            st = node_map.get(e["to"], {}).get("status")
            if st in (None, core.STATUS_PENDING, core.STATUS_READY):
                rewire_children.add(e["to"])

    # 保留已完成部分（success/skipped 节点 + 非受影响节点）
    kept = [n for n in node_map.values() if n["id"] not in affected and n["status"] in
            (core.STATUS_SUCCESS, core.STATUS_SKIPPED)]

    goal = plan["goal"]
    # P2-1：把失败节点的"失败原因"交给 LLM 换做法；
    # 同时把**原验收标准（契约）**一并给出——否则重规划器看不到接口，
    # 只能从 description 重新猜，产物路径必然漂移，下游契约随之落空。
    failed_desc = "\n".join(
        "- %s\n  ↳ 必须继续满足的验收标准：%s\n  ↳ 失败原因：%s" % (
            node_map.get(f, {}).get("description", f),
            (str(node_map.get(f, {}).get("acceptance_criteria") or "").strip() or "（无）")[:300],
            (str(node_map.get(f, {}).get("result") or "").strip() or "（无）")[:300],
        )
        for f in failed_ids
    )
    done_context = "\n".join(f"- {n['description']}（已成功，产出 {n.get('artifacts', []) or '无'}）"
                             for n in kept[:20])
    # 未执行的下游：它们的契约依赖替代节点的产出接口，必须把"它们在等什么"一并交给重规划器，
    # 否则重规划器可能改掉产物路径，导致下游契约永久无法满足。
    pending_lines = []
    for nid in sorted(rewire_children):
        n = node_map.get(nid, {})
        acc = str(n.get("acceptance_criteria") or "").strip()
        line = "- %s：%s" % (nid, (n.get("description") or "")[:60])
        if acc:
            line += "\n   ↳ 它期待的接口/产物：%s" % acc[:200]
        pending_lines.append(line)
    pending_context = "\n".join(pending_lines) or "（无）"

    prompt_msgs = [
        SystemMessage(content=(
            "你是 DAG 局部重规划器。只重规划'失败节点'，新节点 id 用 a1,a2... 避免冲突。\n"
            "【硬性输出要求】\n"
            "1. 每个失败节点都必须被替代：在对应新节点上用 \"replaces\" 标注它替代哪个旧节点 id，"
            "并用 \"acceptance_criteria\" 写明怎样算完成 + 要产出的具体文件路径"
            "（如 deliverables/tetris/index.html）\n"
            "2. **接口冻结**：原失败节点验收标准里列出的产出文件路径，是下游节点依赖的接口——"
            "替代节点必须产出**完全相同的路径**（不改名、不少产、不换目录）。"
            "你只能改变'怎么做'，不能改变'产出什么'\n"
            "3. 必须针对'失败原因'换做法（例如上一版只输出文字、没有真正落盘，"
            "这一版就必须真的调用写文件工具把文件写出来），不要原样复读原来的拆法\n"
            "4. 只需给出新节点之间的依赖边；对'未执行的后继'的依赖由系统自动重挂，"
            "但**下方列出的'它们期待的接口/产物'必须被满足**\n"
            "5. 显式标记：哪些新节点'集成/消费'了已成功节点的产物（写在 description 里）\n"
            "6. 软依赖节点可把 soft 置 true\n"
            "输出 JSON 对象: {\"nodes\":[{\"id\":\"a1\",\"replaces\":\"n1\","
            "\"acceptance_criteria\":\"...\",\"description\":\"...\"}],"
            "\"edges\":[{\"from\":\"x\",\"to\":\"y\",\"soft\":false}]}。"
        )),
        HumanMessage(content=(
            f"原目标：{goal}\n"
            f"失败节点（需要被替代，含原验收标准与失败原因）：\n{failed_desc}\n\n"
            f"尚未执行、依赖会被系统自动重挂的节点（不要替代它们，但必须满足它们期待的接口）：\n"
            f"{pending_context}\n\n"
            f"已完成（锁定不能改）：\n{done_context or '（无）'}\n\n"
            f"请输出替代失败节点的新 nodes，以及它们之间的 edges。"
        )),
    ]
    llm = llm_builder[0]
    try:
        import time as _t
        from app.planning.react_loop import _llm_messages_to_text as _llm2txt
        _t0 = _t.time()
        resp = llm.invoke(prompt_msgs)
        try:
            add_event("llm_call", {
                "node": "executor(replan)",
                "input": _llm2txt(prompt_msgs),
                "output": _llm2txt([resp]),
                "duration_ms": int((_t.time() - _t0) * 1000),
            }, thread_id)
        except Exception:
            pass
        raw = resp.content.strip()
        if raw.startswith("```json"):
            raw = raw[7:].strip()
        if raw.startswith("```"):
            raw = raw[3:].strip()
        if raw.endswith("```"):
            raw = raw[:-3].strip()
        data = json.loads(raw)
        new_nodes = data.get("nodes", [])
        new_edges = data.get("edges", [])
        if not new_nodes:
            raise ValueError("重规划未返回节点")
    except Exception as e:
        add_log_entry("error", f"局部重规划失败: {e}")
        return False

    # ---- 解析 replaces 映射：旧节点 id → [替代节点 id]（一个旧节点可被拆成多个新节点）----
    repl_map: Dict[str, List[str]] = {}
    for n in new_nodes:
        rep = str(n.get("replaces") or "").strip()
        if rep and rep in affected:
            repl_map.setdefault(rep, []).append(n["id"])

    # ---- 契约继承兜底：模型没给 acceptance_criteria 时，从它替代的原节点继承 ----
    # 不信任模型自觉：缺契约会让 _expected_artifacts 返回空 → 契约校验整段跳过，
    # 等于退回"事后改判 failed"的老路。
    inherited = 0
    for n in new_nodes:
        if not str(n.get("acceptance_criteria") or "").strip():
            rep = str(n.get("replaces") or "").strip()
            parent_acc = ""
            if rep:
                parent_acc = str(node_map.get(rep, {}).get("acceptance_criteria") or "").strip()
            if parent_acc:
                n["acceptance_criteria"] = parent_acc
                inherited += 1

    # LLM 没按约定标注 replaces → 无法安全重挂依赖，退回"把未执行后继一并替换"保证依赖完整
    if rewire_children and not repl_map:
        add_log_entry("warn", f"重规划未标注 replaces，未执行的 {len(rewire_children)} 个后继一并纳入替换")
        affected |= rewire_children
        rewire_children = set()

    # 把受影响节点标记 skipped（它们被替换），再写入新节点
    for nid in affected:
        if node_map.get(nid, {}).get("status") != core.STATUS_SUCCESS:
            dag.set_node_status(plan["id"], nid, core.STATUS_SKIPPED, result="局部重规划替换")
    for n in new_nodes:
        nid = n["id"]
        dag.add_node(plan["id"], nid, n["description"], tool=None, params={},
                     status=core.STATUS_PENDING,
                     acceptance_criteria=n.get("acceptance_criteria"),
                     replaces=n.get("replaces"))
    for e in new_edges:
        dag.add_edge(plan["id"], e["from"], e["to"], soft=bool(e.get("soft")))

    # ---- 未执行后继的入边重挂：old_from -> child 改接 new_from -> child ----
    rewired = 0
    for child in rewire_children:
        for e in list(edges):
            if e["to"] != child or e["from"] not in affected:
                continue
            news = repl_map.get(e["from"]) or []
            if not news:
                continue
            dag.remove_edge(plan["id"], e["from"], child)
            for nf in news:
                dag.add_edge(plan["id"], nf, child, soft=bool(e.get("soft")))
            rewired += 1

    dag.bump_replan(plan["id"])
    dag.save_checkpoint(plan["id"])

    # ---- 接口冻结校验：替代节点是否仍产出"被替代节点承诺过的产物路径"？----
    # 注意语义：acceptance_criteria 描述的是"本节点要产出什么"，不是"它期待别人产出什么"，
    # 所以校验对象是「原节点的产出承诺 vs 替代节点的产出承诺」，而不是下游自己的契约。
    # 上面的契约继承兜底通常已保证一致；但模型若显式给出了不同契约，就可能悄悄改掉接口
    # （下游依赖的产物路径），这里检测并告警。
    got_paths = set()
    for n in new_nodes:
        got_paths.update(_expected_artifacts(n))
    drift = []
    for n in new_nodes:
        rep = str(n.get("replaces") or "").strip()
        if not rep:
            continue
        want = _expected_artifacts(node_map.get(rep, {}))
        lost = _missing_expected(sorted(got_paths), want)
        if lost:
            drift.append(f"{n['id']}(替代 {rep}) 未产出 {lost}")
    if drift:
        add_log_entry("warn",
                      "局部重规划后接口可能漂移（原承诺的产物未被任何替代节点产出）：" + "；".join(drift))

    add_log_entry("info", f"局部重规划：{len(affected)} 节点被替换 → {len(new_nodes)} 新节点"
                          + (f"，{inherited} 个新节点继承原契约" if inherited else "")
                          + (f"，{rewired} 条后继依赖已重挂" if rewired else ""))
    return True
