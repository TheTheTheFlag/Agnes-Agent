"""app.server.api.git — git 版本管理 API。"""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.server.git_ops import git_log, git_status, git_diff, auto_snapshot, is_auto_git_enabled, set_auto_git

router = APIRouter()


@router.get("/api/deliverables")
async def api_deliverables():
    """列出交付物目录文件。"""
    import os as _os
    from app.config import BASE_DIR
    ddir = _os.path.join(BASE_DIR, "deliverables")
    files = []
    if _os.path.isdir(ddir):
        for name in sorted(_os.listdir(ddir)):
            p = _os.path.join(ddir, name)
            if _os.path.isfile(p):
                try:
                    size = _os.path.getsize(p)
                except Exception:
                    size = 0
                files.append({"name": name, "size": size})
    return {"files": files, "dir": ddir}


@router.get("/api/deliverables/preview")
async def api_deliverables_preview(name: str = ""):
    """预览交付物文件内容（文本）。"""
    import os as _os
    from app.config import BASE_DIR
    safe = _os.path.basename(name)  # 防路径穿越
    p = _os.path.join(BASE_DIR, "deliverables", safe)
    if not _os.path.isfile(p):
        return JSONResponse({"error": "文件不存在"}, status_code=404)
    try:
        with open(p, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read(5000)
        return {"name": safe, "content": content}
    except Exception as e:
        return {"name": safe, "content": f"[读取失败] {e}"}


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
