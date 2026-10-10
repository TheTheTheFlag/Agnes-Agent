"""app.planning.dag_planner — Plan-and-Execute 的 DAG 规划器（④局部重规划 + ①结构化计划）

把用户目标拆成结构化 DAG（nodes + edges + 可选软依赖），做环检测/规范化后
写入 dag_storage，供新 DAG 执行器调度。替代旧 planner（输出线性 JSON 列表）。

流程：
  1. 从 state.pending_plan 取 goal（回落：ToolMessage / user 消息）
  2. 让模型输出 {"nodes":[...], "edges":[...]}（每节点带 id/description/tool/params，
     边带 from/to/soft）
  3. normalize + DFS 环检测：成环则让模型重规划一次；再成环则退回"单节点计划"
  4. 清空旧 DAG plan（同一 thread），建新 plan 落库
  5. 推送 DAG 结构事件（供前端 vis-network 渲染）
"""

import json
from typing import Dict, List

from langchain_core.messages import HumanMessage, SystemMessage

from app.config import DB_PATH
from app.graph.state import State
from app.graph._agent_prompt import build_dag_planner_system_prompt
from app.memory import MemoryManager
from app.planning import dag_core as core
from app.planning.dag_storage import DAGStorage
from app.server import add_event, add_log_entry
from app.userctx import deliverables_rel


def _extract_goal(state: State) -> str:
    """提取规划目标。

    只认两个来源（权威优先）：
      1. state.pending_plan —— 用户显式 request_planning 工具写进来的唯一权威目标
      2. 最后一条 human 消息 —— 无 pending_plan 时的兜底（此时目标=用户当轮说了什么）

    注意：**不再**从历史 ToolMessage「已接收规划请求，目标：…」嗅探提取目标。
    历史残留在 tool 消息里的旧规划请求（可能是几天前、另一次任务的目标）一旦被取出，
    会覆盖当前任务、并让 planner 删掉正在执行的旧 plan 重建——2026-09-29 线上事故：
    用户插话「把主角设定改成失忆开局」被正确入队后，route 误入 planner，
    嗅探出旧的“500万字小说”目标，导致旧《忘川纪元》计划被删、新计划错建。
    目标只能来自当轮（pending_plan / 最新 human），绝不自历史气泡回捞。
    """
    goal = state.get("pending_plan")
    if goal:
        return str(goal).strip()
    for m in reversed(state.get("messages", [])):
        if hasattr(m, "type") and m.type == "human":
            return str(m.content).strip()
    return "执行当前请求"


def _ask_graph(llm, goal: str, thread_id: str = "default") -> Dict:
    import time as _t
    from app.server import add_event as _a_event
    from app.planning.react_loop import _llm_messages_to_text as _llm2txt
    _sys = build_dag_planner_system_prompt(deliverables_rel())
    _msgs = [SystemMessage(content=_sys), HumanMessage(content=f"目标：{goal}")]
    _t0 = _t.time()
    resp = llm.invoke(_msgs)
    try:
        _a_event("llm_call", {
            "node": "planner",
            "input": _llm2txt(_msgs),
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
    return json.loads(raw)


def create_dag_planner_node(llm):
    def planner_node(state: State):
        thread_id = state.get("thread_id", "default")
        from app.trace import record_node_start, record_node_end
        record_node_start(thread_id, "planner")
        dag = DAGStorage(DB_PATH)
        goal = _extract_goal(state)

        nodes, edges, warnings = [], [], []
        try:
            data = _ask_graph(llm, goal, thread_id)
            nodes, edges, warnings = core.normalize_nodes(data.get("nodes", []), data.get("edges", []))
        except Exception as e:
            add_log_entry("warning", f"[DAG-Planner] 结构化解析失败({e})，降级为单节点计划")

        # 环检测：成环让模型重规划一次，再成环则退化为单节点
        cycle = core.detect_cycle(nodes, edges) if (nodes and edges) else None
        if cycle:
            add_log_entry("warning", f"[DAG-Planner] 检测到环 {cycle}，请求重规划")
            try:
                data2 = _ask_graph(llm, goal + "（注意：不能有依赖循环）", thread_id)
                nodes, edges, warnings = core.normalize_nodes(data2.get("nodes", []), data2.get("edges", []))
                if core.detect_cycle(nodes, edges):
                    nodes, edges = [{"id": "1", "description": f"执行目标：{goal}"}], []
                    warnings.append("再次成环，退化为单节点")
            except Exception:
                nodes, edges = [{"id": "1", "description": f"执行目标：{goal}"}], []

        # 兜底：没有节点 → 单节点
        if not nodes:
            nodes = [{"id": "1", "description": f"执行目标：{goal}"}]
            edges = []

        # 清空该 thread 旧 plan，建新 plan
        old = dag.get_plan_by_thread(thread_id)
        if old:
            dag.delete_plan(old["id"])
        plan_id = dag.create_plan(thread_id, goal)
        for n in nodes:
            dag.add_node(plan_id, n["id"], n["description"], tool=n.get("tool"),
                         params=n.get("params"),
                         acceptance_criteria=n.get("acceptance_criteria"))
        for e in edges:
            dag.add_edge(plan_id, e["from"], e["to"], soft=bool(e.get("soft")))
        dag.set_plan_status(plan_id, "executing")
        dag.save_checkpoint(plan_id)

        # 契约遵守率：planner 是否真的把"产出文件路径"写进了 acceptance_criteria。
        # 这个比率直接决定后续 complete_node 契约校验的强度——没契约的节点只剩下
        # "必须真实落盘"这一层约束。记入日志 + 规划气泡，便于长期观察 prompt 遵守情况。
        with_acc = [n for n in nodes if str(n.get("acceptance_criteria") or "").strip()]
        no_path_ids = [n["id"] for n in nodes if not core.expected_artifacts(n)]
        contract_line = (f"[契约] 规划遵守率：{len(with_acc)}/{len(nodes)} 节点带验收标准，"
                         f"{len(nodes) - len(no_path_ids)} 个写明产出文件路径")
        if no_path_ids:
            contract_line += "；缺产出路径：" + ", ".join(no_path_ids)
        add_log_entry("info", contract_line)

        node_snap = [{"id": n["id"], "status": "pending", "description": n["description"][:40]} for n in nodes]
        add_event("planner", {
            "goal": goal[:80],
            "dag": {"nodes": node_snap, "edges": edges},
            "warnings": warnings,
        }, thread_id)
        add_event("node_thought", {
            "role": "planner",
            "title": f"📋 规划（{len(nodes)} 节点）",
            "text": (f"目标：{goal}\n节点：\n" + "\n".join(f"- {n['description'][:60]}" for n in nodes)
                     + f"\n\n{contract_line}"),
        }, thread_id)

        record_node_end(thread_id, "planner", f"{len(nodes)} 节点 DAG")
        add_log_entry("info", f"[DAG-Planner] {len(nodes)} 节点 / {len(edges)} 边")
        # 返回 task 标识供路由判断
        return {"task_plan_id": plan_id, "_dag_ready": True}
    return planner_node
