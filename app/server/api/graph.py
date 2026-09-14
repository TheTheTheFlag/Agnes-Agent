import json
from typing import Dict, List
from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/graph", tags=["graph"])


@router.get("/nodes")
async def graph_nodes(thread_id: str = Query(..., description="会话 ID")) -> Dict:
    try:
        from app.memory.ligraphrag_adapter import get_lightrag
        rag = get_lightrag(thread_id)
        nodes = []
        try:
            nx_graph = rag.graph_storage.graph
            for nid in nx_graph.nodes():
                data = nx_graph.nodes[nid] or {}
                nodes.append({
                    "id": nid,
                    "name": str(nid),
                    "kind": data.get("kind", "Entity"),
                    "attrs": json.dumps(data, ensure_ascii=False, default=str),
                })
        except Exception:
            pass
        seen = set()
        unique = []
        for n in nodes:
            if n["id"] not in seen:
                seen.add(n["id"])
                unique.append(n)
        return {"thread_id": thread_id, "node_count": len(unique), "nodes": unique[:500]}
    except Exception as e:
        return {"thread_id": thread_id, "node_count": 0, "nodes": [], "error": str(e)}


@router.get("/edges")
async def graph_edges(thread_id: str = Query(..., description="会话 ID")) -> Dict:
    try:
        from app.memory.ligraphrag_adapter import get_lightrag
        rag = get_lightrag(thread_id)
        edges = []
        try:
            nx_graph = rag.graph_storage.graph
            for u, v, data in nx_graph.edges(data=True):
                edges.append({
                    "id": f"{u}|{v}",
                    "from_id": str(u),
                    "to_id": str(v),
                    "label": (data or {}).get("description", "") or "",
                    "attrs": json.dumps(data, ensure_ascii=False, default=str) if data else "{}",
                })
        except Exception:
            pass
        seen = set()
        unique = []
        for e in edges:
            if e["id"] not in seen:
                seen.add(e["id"])
                unique.append(e)
        return {"thread_id": thread_id, "edge_count": len(unique), "edges": unique[:1000]}
    except Exception as e:
        return {"thread_id": thread_id, "edge_count": 0, "edges": [], "error": str(e)}