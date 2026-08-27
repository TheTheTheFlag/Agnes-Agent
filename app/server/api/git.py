"""app.server.api.git — git 版本管理 API。"""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.server.git_ops import git_log, git_status, git_diff, auto_snapshot, is_auto_git_enabled, set_auto_git

router = APIRouter()


@router.get("/api/git/log")
async def api_git_log(limit: int = 20):
    return {"commits": git_log(limit), "auto_git": is_auto_git_enabled()}


@router.get("/api/git/status")
async def api_git_status():
    return git_status()


@router.get("/api/git/diff")
async def api_git_diff():
    return {"diff": git_diff()}


@router.post("/api/git/snapshot")
async def api_git_snapshot(payload: dict = None):
    reason = (payload or {}).get("reason", "手动快照")
    return auto_snapshot(reason)


@router.post("/api/git/auto")
async def api_git_auto(payload: dict):
    enabled = bool((payload or {}).get("enabled"))
    set_auto_git(enabled)
    return {"ok": True, "auto_git": enabled}
