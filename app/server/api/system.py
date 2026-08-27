"""app.server.api.system — 系统级 API（state/prompt/logs/events/SSE）。"""
import json
import asyncio
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse, StreamingResponse

from app.server import store as _store  # 模块引用：_state_snapshot 等会被重新赋值，必须实时读取
from app.server import config as _srv_cfg

router = APIRouter()

@router.get("/api/state")
@router.get("/api/state")
async def get_state(thread_id: str = ""):
    # 按 thread_id 返回对应会话的 state 快照；不传则用当前 _CONFIG 的 thread_id
    if not thread_id and _srv_cfg._CONFIG:
        thread_id = (_srv_cfg._CONFIG.get("configurable") or {}).get("thread_id", "")
    if thread_id and thread_id in _store._state_snapshots:
        return JSONResponse(_store._state_snapshots[thread_id])
    # 兜底：无该 thread 快照 → 返回空（新会话还没对话时的正确表现）
    if thread_id:
        return JSONResponse({"_thread_id": thread_id, "messages": [], "note": "该会话尚无状态快照（可能还没对话）"})
    return JSONResponse(_store._state_snapshot)


@router.get("/api/prompt")
async def get_prompt(thread_id: str = ""):
    # 优先返回指定会话的提示词；未指定则全局
    if thread_id and thread_id in _store._prompt_snapshots:
        return JSONResponse({"prompt": _store._prompt_snapshots[thread_id], "thread_id": thread_id})
    return JSONResponse({"prompt": _store._prompt_snapshot})


@router.get("/api/logs")
async def get_logs(limit: int = Query(100, ge=1, le=500)):
    # 优先 DB（含历史会话）；内存兜底
    logs = _store.get_persisted_logs(limit)
    if not logs and _store._log_entries:
        logs = _store._log_entries[-limit:]
    return JSONResponse({"logs": logs, "total": len(logs)})


@router.get("/api/events")
async def get_events(limit: int = Query(50, ge=1, le=200), thread_id: str = ""):
    # 按会话过滤：指定 thread_id 只返回该会话事件，避免跨会话混显
    events = _store.get_persisted_events(limit, thread_id=thread_id or None)
    if not events and _store._events:
        events = [e for e in _store._events[-limit:] if not thread_id or e.get("thread_id") == thread_id]
    return JSONResponse({"events": events, "total": len(events)})


@router.post("/api/clear-logs")
async def clear_logs():
    _store._log_entries = []
    _store._events = []
    _store.clear_persisted()
    return {"status": "ok"}


# ==================== 审查 API（输入/输出/记忆/历史/diff）====================


@router.get("/api/sse")
async def sse_stream():
    async def event_generator():
        queue = asyncio.Queue()
        _store._event_listeners.append(queue)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield f": heartbeat\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            if queue in _store._event_listeners:
                _store._event_listeners.remove(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
        }
    )


# ==================== HTML 面板 ====================

# raw string：内联 JS 里的 \n、\s 等保持原样，避免 Python 转义破坏 JS 语法


# ==================== 启动 ====================

