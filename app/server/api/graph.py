from typing import Dict, Optional
from fastapi import APIRouter, Query
from pydantic import BaseModel

router = APIRouter(prefix="/api/graph", tags=["graph"])


class EntityEditReq(BaseModel):
    thread_id: str
    name: str
    entity_type: str = ""
    description: str = ""
    new_name: str = ""
    allow_rename: bool = True
    allow_merge: bool = False


class EntityDeleteReq(BaseModel):
    thread_id: str
    name: str


class RelationEditReq(BaseModel):
    thread_id: str
    src: str
    tgt: str
    description: str = ""
    keywords: str = ""
    weight: Optional[float] = None


class RelationDeleteReq(BaseModel):
    thread_id: str
    src: str
    tgt: str


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


@router.get("/entities")
async def graph_entities(thread_id: str = Query(...), q: str = Query(""), kind: str = Query(""), page: int = Query(1), page_size: int = Query(50)) -> Dict:
    from app.memory.ligraphrag_adapter import graph_entities as fn
    try:
        return fn(thread_id, q=q, kind=kind, page=page, page_size=page_size)
    except Exception as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/relations")
async def graph_relations(thread_id: str = Query(...), q: str = Query(""), src: str = Query(""), tgt: str = Query(""), page: int = Query(1), page_size: int = Query(50)) -> Dict:
    from app.memory.ligraphrag_adapter import graph_relations as fn
    try:
        return fn(thread_id, q=q, src=src, tgt=tgt, page=page, page_size=page_size)
    except Exception as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/entity_edit")
async def entity_edit(req: EntityEditReq) -> Dict:
    from app.memory.ligraphrag_adapter import graph_entity_edit as fn
    try:
        return fn(req.thread_id, req.name, entity_type=req.entity_type, description=req.description, new_name=req.new_name, allow_rename=req.allow_rename, allow_merge=req.allow_merge)
    except Exception as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/entity_delete")
async def entity_delete(req: EntityDeleteReq) -> Dict:
    from app.memory.ligraphrag_adapter import graph_entity_delete as fn
    try:
        return fn(req.thread_id, req.name)
    except Exception as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/relation_edit")
async def relation_edit(req: RelationEditReq) -> Dict:
    from app.memory.ligraphrag_adapter import graph_relation_edit as fn
    try:
        return fn(req.thread_id, req.src, req.tgt, description=req.description, keywords=req.keywords, weight=req.weight)
    except Exception as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/relation_delete")
async def relation_delete(req: RelationDeleteReq) -> Dict:
    from app.memory.ligraphrag_adapter import graph_relation_delete as fn
    try:
        return fn(req.thread_id, req.src, req.tgt)
    except Exception as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/merge_variants")
async def merge_variants(thread_id: str = Query(...)) -> Dict:
    from app.memory.ligraphrag_adapter import graph_merge_variants as fn
    try:
        return fn(thread_id)
    except Exception as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=str(e))
