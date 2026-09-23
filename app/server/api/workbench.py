"""app.server.api.workbench — 短剧创作工作台状态 API。

GET  /api/workbench/drama           查询当前用户×当前线程的命令作用域状态
POST /api/workbench/drama           开启/刷新（enabled=true）或关闭（false）该线程作用域
"""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.userctx import current_user

router = APIRouter(prefix="/api/workbench", tags=["workbench"])


def _current_username(request: Request) -> str:
    return getattr(getattr(request, "state", None), "username", None) or current_user() or "Mirror"


@router.get("/drama")
async def workbench_drama_state(request: Request):
    from app.workbench import drama_command_state
    return drama_command_state(_current_username(request))


@router.post("/drama")
async def workbench_drama_set(request: Request, payload: dict):
    from app.workbench import set_drama_command_enabled
    enabled = bool((payload or {}).get("enabled", False))
    thread_id = str((payload or {}).get("thread_id") or "").strip() or None
    result = set_drama_command_enabled(thread_id=thread_id, enabled=enabled,
                                       username=_current_username(request))
    return {"ok": True, **result}