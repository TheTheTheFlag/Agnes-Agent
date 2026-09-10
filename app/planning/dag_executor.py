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


def _run_node(thread_id: str, task_plan_id: int, node: Dict, goal: str,
              parent_artifacts: List[str], missing_soft: List[str],
              llm_builder, tools_list, mm, dag: DAGStorage, on_event):
    """执行单个节点（线程池 worker）。返回 (node_id, status, result, artifacts)。"""
    node_id = node["id"]
    description = node["description"]
    on_event("executor", {"subtask": node_id, "status": "running",
                          "goal": goal[:80], "nodes": _snapshot_for_event(dag, task_plan_id)})
    dag.set_node_status(task_plan_id, node_id, core.STATUS_RUNNING)
    dag.save_checkpoint(task_plan_id)

    artifacts_context = ""
    if parent_artifacts:
        artifacts_context = "\n【已有产出】\n" + "\n".join(parent_artifacts)
    if missing_soft:
        artifacts_context += "\n\n【提示】以下软依赖节点未能成功，其结果缺失（本次不阻塞，但结论可能不完整）：\n" + \
                             "\n".join(f"- {m}" for m in missing_soft)

    system_prompt = build_dag_executor_system_prompt(
        node_id=node_id,
        description=description,
        goal=goal,
        tool_names=", ".join(t.name for t in tools_list),
        artifacts_context=artifacts_context,
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
                          "nodes": _snapshot_for_event(dag, task_plan_id)})
    return node_id, status, final_result, artifacts


def _snapshot_for_event(dag: DAGStorage, plan_id: int) -> List[Dict]:
    """把当前 DAG 节点状态转成前端事件快照（vis-network 用）。"""
    return [{"id": n["id"], "status": n["status"], "description": n["description"][:40]}
            for n in dag.get_nodes(plan_id)]


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
        if failed_ids and plan["replan_count"] < 3:
            replan_requested = True

        dag.recover_stale_running(plan["id"])
        nodes = dag.get_nodes(plan["id"])
        edges = dag.get_edges(plan["id"])
        node_map = {n["id"]: n for n in nodes}
        node_map, edges = _reinstall_state(nodes, edges)

        if replan_requested:
            _do_local_replan(plan, failed_ids, dag, node_map, edges, llm_builder, thread_id)
            nodes = dag.get_nodes(plan["id"])
            node_map = {n["id"]: n for n in nodes}

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
                     node_map, edges, llm_builder, thread_id: str):
    """④ 局部重规划：锁定已完成节点，只让 LLM 重出受影响子图（failed 及其强依赖后继），
    替换回 DAG 继续。重规划后 replan_count++。"""
    # 受影响子图 = failed 节点 + 其强依赖后继（传递闭包）
    affected = set(failed_ids)
    changed = True
    while changed:
        changed = False
        for e in edges:
            if e["from"] in affected and not e.get("soft") and e["to"] not in affected:
                affected.add(e["to"])
                changed = True

    # 扩展 C：失败节点的所有"前驱"（祖先）若其产物在失败节点上有依赖，父也并入 affected。
    # 理由：如果 LLM 不知道某个"前驱"产物的具体值，LLM 重生时可能复用旧值但实际旧值已陈旧。
    # 保守做法：把这些"产物可能已过时"的祖先也丢给 LLM，让它自己决定是否重做。
    ancestors = set()
    changed = True
    while changed:
        changed = False
        for e in edges:
            if e["to"] in affected and not e.get("soft") and e["from"] not in affected and e["from"] not in ancestors:
                ancestors.add(e["from"])
                changed = True
    # 排除已成功且被"保留"的节点——它们产物可用；但失败/skipped 的祖先要纳入
    new_ancestors = set()
    for aid in ancestors:
        st = node_map.get(aid, {}).get("status")
        if st in (None, core.STATUS_PENDING, core.STATUS_RUNNING, core.STATUS_FAILED):
            new_ancestors.add(aid)
    # 不动 success 祖先（产物已锁定，能继续用）；只把 pending/running/failed 祖先纳入
    if new_ancestors:
        affected.update(new_ancestors)
        # 再传播一遍（这些祖先可能又有强依赖后继）
        changed = True
        while changed:
            changed = False
            for e in edges:
                if e["from"] in affected and not e.get("soft") and e["to"] not in affected:
                    affected.add(e["to"])
                    changed = True

    # 保留已完成部分（success/skipped 节点 + 非受影响节点）
    kept = [n for n in node_map.values() if n["id"] not in affected and n["status"] in
            (core.STATUS_SUCCESS, core.STATUS_SKIPPED)]

    goal = plan["goal"]
    failed_desc = "\n".join(f"- {node_map.get(f, {}).get('description', f)}"
                            for f in failed_ids)
    done_context = "\n".join(f"- {n['description']}（已成功，产出 {n.get('artifacts', []) or '无'}）"
                             for n in kept[:20])

    prompt_msgs = [
        SystemMessage(content=(
            "你是 DAG 局部重规划器。请把下方'需要重规划的子图'重新拆成新节点（id 用 a1,a2... 避免冲突），"
            "覆盖所有失败节点和它们的后继。\n"
            "【硬性输出要求】\n"
            "1. 新节点数 ≥ 失败节点数（每个失败节点必须有一个对应新节点来替代）\n"
            "2. 保持拓扑依赖闭包完整：被标 'skipped'（局部重规划替换）的旧节点，如果它有'后继'指向尚未完成的任务，"
            "你必须在 edges 里给新节点搭出同样的依赖关系\n"
            "3. 显式标记：哪些新节点'集成/消费'了已成功节点的产物（写在 description 里）\n"
            "4. 软依赖节点可把 soft 置 true\n"
            "输出 JSON 对象: {\"nodes\":[{\"id\":\"a1\",\"description\":\"...\"}],"
            "\"edges\":[{\"from\":\"x\",\"to\":\"y\",\"soft\":false}]}。"
        )),
        HumanMessage(content=(
            f"原目标：{goal}\n"
            f"需要重规划的子图（这些节点失败）：\n{failed_desc}\n\n"
            f"已被局部重规划替换（skipped）的旧节点：{', '.join(sorted(affected)) or '（无）'}\n"
            f"已完成（锁定不能改）：\n{done_context or '（无）'}\n\n"
            f"请只重规划受影响部分，输出新的 nodes+edges。"
            f"务必保证：每个失败节点都有新节点替代，且新子图与已成功节点的对接关系在 edges 里写明。"
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
        return

    # 把受影响节点标记 skipped（它们被替换），再写入新节点
    for nid in affected:
        if node_map.get(nid, {}).get("status") != core.STATUS_SUCCESS:
            dag.set_node_status(plan["id"], nid, core.STATUS_SKIPPED, result="局部重规划替换")
    for n in new_nodes:
        nid = n["id"]
        dag.add_node(plan["id"], nid, n["description"], tool=None, params={}, status=core.STATUS_PENDING)
    for e in new_edges:
        dag.add_edge(plan["id"], e["from"], e["to"], soft=bool(e.get("soft")))

    dag.bump_replan(plan["id"])
    dag.save_checkpoint(plan["id"])
    add_log_entry("info", f"局部重规划：{len(affected)} 失败节点 → {len(new_nodes)} 新节点")
