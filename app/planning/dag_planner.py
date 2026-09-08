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
from app.memory import MemoryManager
from app.planning import dag_core as core
from app.planning.dag_storage import DAGStorage
from app.server import add_event, add_log_entry

_SYS = """你是一个 DAG 任务规划器。把用户目标拆成"有依赖关系"的执行图，输出 JSON：
{
  "nodes": [{"id": "1", "description": "步骤说明", "tool": "优选工具名(可省略)", "params": {} }],
  "edges": [{"from": "1", "to": "2", "soft": false}]
}
规则：
- 节点数 2-6 个；能并行的步骤分开成节点（同层会在执行期并行）。
- edges 表达依赖：from 完成后 to 才能执行。无依赖的可不连（= 同层并行）。
- 软依赖：某依赖"缺失不阻塞、只是结果不完整"时，该边 soft 置 true。
- 严禁成环（不能出现 1→2→3→1）或引用不存在的节点 id。
只输出 JSON，不要多余文字。"""


def _extract_goal(state: State) -> str:
    goal = state.get("pending_plan")
    if goal:
        return str(goal).strip()
    for m in reversed(state.get("messages", [])[-5:]):
        if hasattr(m, "type") and m.type == "tool" and "已接收规划请求" in (m.content or ""):
            try:
                return m.content.split("目标：", 1)[1].strip()
            except Exception:
                pass
    for m in reversed(state.get("messages", [])):
        if hasattr(m, "type") and m.type == "human":
            return str(m.content).strip()
    return "执行当前请求"


def _ask_graph(llm, goal: str) -> Dict:
    resp = llm.invoke([
        SystemMessage(content=_SYS),
        HumanMessage(content=f"目标：{goal}"),
    ])
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
            data = _ask_graph(llm, goal)
            nodes, edges, warnings = core.normalize_nodes(data.get("nodes", []), data.get("edges", []))
        except Exception as e:
            add_log_entry("warning", f"[DAG-Planner] 结构化解析失败({e})，降级为单节点计划")

        # 环检测：成环让模型重规划一次，再成环则退化为单节点
        cycle = core.detect_cycle(nodes, edges) if (nodes and edges) else None
        if cycle:
            add_log_entry("warning", f"[DAG-Planner] 检测到环 {cycle}，请求重规划")
            try:
                data2 = _ask_graph(llm, goal + "（注意：不能有依赖循环）")
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
            dag.add_node(plan_id, n["id"], n["description"], tool=n.get("tool"), params=n.get("params"))
        for e in edges:
            dag.add_edge(plan_id, e["from"], e["to"], soft=bool(e.get("soft")))
        dag.set_plan_status(plan_id, "executing")
        dag.save_checkpoint(plan_id)

        node_snap = [{"id": n["id"], "status": "pending", "description": n["description"][:40]} for n in nodes]
        add_event("planner", {
            "goal": goal[:80],
            "dag": {"nodes": node_snap, "edges": edges},
            "warnings": warnings,
        }, thread_id)
        add_event("node_thought", {
            "role": "planner",
            "title": f"📋 规划（{len(nodes)} 节点）",
            "text": f"目标：{goal}\n节点：\n" + "\n".join(f"- {n['description'][:60]}" for n in nodes),
        }, thread_id)

        record_node_end(thread_id, "planner", f"{len(nodes)} 节点 DAG")
        add_log_entry("info", f"[DAG-Planner] {len(nodes)} 节点 / {len(edges)} 边")
        # 返回 task 标识供路由判断
        return {"task_plan_id": plan_id, "_dag_ready": True}
    return planner_node
