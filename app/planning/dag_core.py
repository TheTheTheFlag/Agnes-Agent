"""app.planning.dag_core — Plan-and-Execute 的 DAG 计算核心（纯函数，可独立测试）

对应文章四层：
  ① 结构化计划：模型输出 nodes + edges → 变成可调度 DAG
  ② 校验与排序：DFS 三色环检测 + Kahn 拓扑分层（并行层）
  ③ 状态机与失败传播：pending/ready/running/success/failed/skipped，软依赖
  ④ 局部重规划：锁定已完成，只重规划受影响子图（由 executor 层触发，本模块提供子图提取）

设计（对齐业界先进方案）：
  - 环检测：DFS + 三色标记（白/灰/黑），撞灰即成环，交给重规划而非自动断边。
  - 拓扑层：Kahn 一次性出队可并行的一整批 = 同一依赖层。
  - 软依赖：soft 边失败不阻塞后继，但后继上下文会标注"缺数据"。
  - 失败隔离：强依赖失败 → 后继标记 skipped（递归传播）；无关分支不受影响。
"""

from typing import Dict, List, Optional, Set, Tuple

STATUS_PENDING = "pending"
STATUS_READY = "ready"
STATUS_RUNNING = "running"
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"


# ---------------- 图结构 ----------------
class DAG:
    """内存态 DAG：nodes: {id: {id, description, ...}}；edges: [(from,to,soft)]。"""

    def __init__(self, nodes: List[Dict], edges: List[Dict]):
        self.nodes: Dict[str, Dict] = {n["id"]: dict(n) for n in nodes}
        # 邻接表
        self.adj: Dict[str, List[Tuple[str, bool]]] = {nid: [] for nid in self.nodes}
        self.rev: Dict[str, List[Tuple[str, bool]]] = {nid: [] for nid in self.nodes}
        for e in edges:
            f, t = e["from"], e["to"]
            soft = bool(e.get("soft"))
            if f in self.adj and t in self.nodes:
                self.adj[f].append((t, soft))
                self.rev[t].append((f, soft))

    @property
    def node_ids(self) -> List[str]:
        return list(self.nodes.keys())

    def order(self) -> List[str]:
        return self.node_ids


# ---------------- ① 结构规范化 ----------------
def normalize_nodes(nodes_raw: List[Dict], edges_raw: List[Dict]) -> Tuple[List[Dict], List[Dict], List[str]]:
    """规范化 node 列表与 edge 列表：
      - id 强制字符串
      - 去掉引用未知节点的边（告警）
      - 去重边
    返回 (nodes, edges, warnings)。"""
    warnings: List[str] = []
    nodes: Dict[str, Dict] = {}
    for n in nodes_raw:
        nid = str(n.get("id", "")).strip()
        if not nid:
            continue
        nodes[nid] = {
            "id": nid,
            "description": str(n.get("description") or nid),
            "acceptance_criteria": n.get("acceptance_criteria") or "",
            "tool": n.get("tool") or None,
            "params": n.get("params") or {},
        }

    seen: Set[Tuple[str, str, bool]] = set()
    edges: List[Dict] = []
    for e in edges_raw:
        f, t = str(e.get("from", "")), str(e.get("to", ""))
        soft = bool(e.get("soft"))
        if f not in nodes or t not in nodes:
            warnings.append(f"边 {f}->{t} 引用未知节点，已忽略")
            continue
        key = (f, t, soft)
        if key in seen:
            continue
        seen.add(key)
        edges.append({"from": f, "to": t, "soft": soft})
    return list(nodes.values()), edges, warnings


# ---------------- ② 环检测（DFS 三色） ----------------
def detect_cycle(nodes: List[Dict], edges: List[Dict]) -> Optional[List[str]]:
    """DFS 三色标记环检测。返回一个环（节点 id 列表）；无环返回 None。"""
    adj: Dict[str, List[str]] = {n["id"]: [] for n in nodes}
    for e in edges:
        adj.setdefault(e["from"], []).append(e["to"])

    WHITE, GRAY, BLACK = 0, 1, 2
    color = {nid: WHITE for nid in adj}
    stack: List[str] = []

    def dfs(u: str) -> Optional[List[str]]:
        color[u] = GRAY
        stack.append(u)
        for v in adj.get(u, []):
            if color[v] == GRAY:
                # 找到环：从栈中 v 的位置到栈顶
                return stack[stack.index(v):] + [v]
            if color[v] == WHITE:
                cyc = dfs(v)
                if cyc:
                    return cyc
        stack.pop()
        color[u] = BLACK
        return None

    for nid in list(adj.keys()):
        if color[nid] == WHITE:
            cyc = dfs(nid)
            if cyc:
                return cyc
    return None


# ---------------- ② 拓扑分层（Kahn，取一整批 = 一个并行层） ----------------
def topo_layers(nodes: List[Dict], edges: List[Dict]) -> List[List[str]]:
    """Kahn 算法分并行层。返回按依赖顺序的层列表（每层是一批可并行节点）。
    若成环，返回空列表（由调用方决定重规划）。"""
    in_deg: Dict[str, int] = {n["id"]: 0 for n in nodes}
    adj: Dict[str, List[str]] = {n["id"]: [] for n in nodes}
    for e in edges:
        if e["from"] in in_deg and e["to"] in in_deg:
            in_deg[e["to"]] += 1
            adj[e["from"]].append(e["to"])

    from collections import deque
    q = deque([nid for nid, d in in_deg.items() if d == 0])
    layers: List[List[str]] = []
    processed = 0

    while q:
        cur = list(q)
        q.clear()
        layers.append(cur)
        processed += len(cur)
        for u in cur:
            for v in adj[u]:
                in_deg[v] -= 1
                if in_deg[v] == 0:
                    q.append(v)

    # 若有节点未处理 → 有环
    if processed != len(in_deg):
        return []
    return layers


# ---------------- ③ 状态推进：从 DB 状态算出可执行批次与后继失败 ----------------
def compute_ready_batch(nodes: Dict[str, Dict], edges: List[Dict]) -> List[str]:
    """给定当前节点状态（nodes[id]['status']），返回本批 ready 节点：
      所有强依赖父都 success（软依赖父可以失败/pending，不阻塞）。
      仅考虑 pending 状态的节点。"""
    adj: Dict[str, List[Tuple[str, bool]]] = {nid: [] for nid in nodes}
    for e in edges:
        adj.setdefault(e["to"], []).append((e["from"], bool(e.get("soft"))))

    batch: List[str] = []
    for nid, n in nodes.items():
        if n.get("status") != STATUS_PENDING:
            continue
        deps = adj.get(nid, [])
        ok = True
        for parent, soft in deps:
            p_status = nodes.get(parent, {}).get("status")
            if soft:
                # 软依赖：父 success/skipped/failed 都可不阻塞（视为缺数据）
                continue
            if p_status != STATUS_SUCCESS:
                ok = False
                break
        if ok:
            batch.append(nid)
    return batch


def rev_adj_for(nodes: Dict[str, Dict], edges: List[Dict]) -> Dict[str, List[Tuple[str, bool]]]:
    """构造反向邻接：child -> [(parent, soft)]。"""
    child_to_parents: Dict[str, List[Tuple[str, bool]]] = {nid: [] for nid in nodes}
    for e in edges:
        child_to_parents.setdefault(e["to"], []).append((e["from"], bool(e.get("soft"))))
    return child_to_parents


def compute_failure_skips(nodes: Dict[str, Dict], edges: List[Dict]) -> List[str]:
    """强依赖失败 → 递归把后继标 skipped。返回应标 skipped 的节点 id 列表（不碰软依赖后继）。
    迭代传播直到稳定：仅当某节点所有强依赖父都已失败时，才标 skipped。"""
    child_to_parents = rev_adj_for(nodes, edges)
    to_skip: Set[str] = set()
    changed = True
    while changed:
        changed = False
        for nid, n in nodes.items():
            if nid in to_skip or n.get("status") in (STATUS_SUCCESS, STATUS_RUNNING):
                continue
            parents = child_to_parents.get(nid, [])
            if not parents:
                continue
            # 仅强依赖父
            strong = [p for p, soft in parents if not soft]
            if not strong:
                continue
            if all(nodes.get(p, {}).get("status") == STATUS_FAILED for p in strong):
                to_skip.add(nid)
                changed = True
    return list(to_skip)
