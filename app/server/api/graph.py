from typing import Dict
from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/graph", tags=["graph"])


@router.get("/nodes")
async def graph_nodes(thread_id: str = Query(..., description="会话 ID")) -> Dict:
    from app.memory.ligraphrag_adapter import graph_snapshot, _graph_ns
    ns = _graph_ns(thread_id)
    snap = graph_snapshot(thread_id, ns=ns)
    return {
        "thread_id": thread_id,
        "namespace": ns,
        "namespace_label": "全局图谱（跨会话）" if ns == "__global__" else ns,
        "node_count": len(snap.get("nodes", [])),
        "nodes": snap.get("nodes", []),
    }


@router.get("/edges")
async def graph_edges(thread_id: str = Query(..., description="会话 ID")) -> Dict:
    from app.memory.ligraphrag_adapter import graph_snapshot, _graph_ns
    ns = _graph_ns(thread_id)
    snap = graph_snapshot(thread_id, ns=ns)
    return {
        "thread_id": thread_id,
        "namespace": ns,
        "namespace_label": "全局图谱（跨会话）" if ns == "__global__" else ns,
        "edge_count": len(snap.get("edges", [])),
        "edges": snap.get("edges", []),
    }