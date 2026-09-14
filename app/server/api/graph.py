from typing import Dict
from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/graph", tags=["graph"])


@router.get("/nodes")
async def graph_nodes(thread_id: str = Query(..., description="会话 ID")) -> Dict:
    from app.memory.ligraphrag_adapter import graph_snapshot
    snap = graph_snapshot(thread_id)
    return {
        "thread_id": thread_id,
        "node_count": len(snap.get("nodes", [])),
        "nodes": snap.get("nodes", []),
    }


@router.get("/edges")
async def graph_edges(thread_id: str = Query(..., description="会话 ID")) -> Dict:
    from app.memory.ligraphrag_adapter import graph_snapshot
    snap = graph_snapshot(thread_id)
    return {
        "thread_id": thread_id,
        "edge_count": len(snap.get("edges", [])),
        "edges": snap.get("edges", []),
    }