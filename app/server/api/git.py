"""app.server.api.git — git 版本管理 API。

多用户门禁：/api/git/*（仓库级日志/状态/提交开关）仅管理员可访问；
/api/deliverables* 走每用户目录（DELIVERABLES_DIR 路径代理），普通用户只能看到自己的工作区。
"""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.server.git_ops import git_log, git_status, git_diff, auto_snapshot, is_auto_git_enabled, set_auto_git

router = APIRouter()


def _require_admin(request: Request):
    """非管理员访问仓库级 git 端点 → 403。"""
    try:
        from app.server.config import _is_request_admin
        if not _is_request_admin(request):
            return JSONResponse({"error": "仅管理员可访问此接口"}, status_code=403)
    except Exception:
        pass
    return None


@router.get("/api/deliverables")
async def api_deliverables():
    """列出交付物目录文件。"""
    import os as _os
    from app.config import DELIVERABLES_DIR
    ddir = str(DELIVERABLES_DIR)
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
    from app.config import DELIVERABLES_DIR
    safe = _os.path.basename(name)  # 防路径穿越
    p = _os.path.join(str(DELIVERABLES_DIR), safe)
    if not _os.path.isfile(p):
        return JSONResponse({"error": "文件不存在"}, status_code=404)
    try:
        with open(p, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read(5000)
        return {"name": safe, "content": content}
    except Exception as e:
        return {"name": safe, "content": f"[读取失败] {e}"}


@router.get("/api/deliverables/download")
async def api_deliverables_download(name: str = ""):
    """下载交付物文件。"""
    import os as _os
    from fastapi.responses import FileResponse
    from app.config import DELIVERABLES_DIR
    safe = _os.path.basename(name)  # 防路径穿越
    p = _os.path.join(str(DELIVERABLES_DIR), safe)
    if not _os.path.isfile(p):
        return JSONResponse({"error": "文件不存在"}, status_code=404)
    return FileResponse(p, filename=safe)


@router.get("/api/git/log")
async def api_git_log(limit: int = 20, request: Request = None):
    denied = _require_admin(request)
    if denied:
        return denied
    return {"commits": git_log(limit), "auto_git": is_auto_git_enabled()}


@router.get("/api/git/status")
async def api_git_status(request: Request = None):
    denied = _require_admin(request)
    if denied:
        return denied
    return git_status()


@router.get("/api/git/diff")
async def api_git_diff(request: Request = None):
    denied = _require_admin(request)
    if denied:
        return denied
    return {"diff": git_diff()}


@router.post("/api/git/snapshot")
async def api_git_snapshot(payload: dict = None, request: Request = None):
    denied = _require_admin(request)
    if denied:
        return denied
    reason = (payload or {}).get("reason", "手动快照")
    return auto_snapshot(reason)


@router.post("/api/git/auto")
async def api_git_auto(payload: dict, request: Request = None):
    denied = _require_admin(request)
    if denied:
        return denied
    enabled = bool((payload or {}).get("enabled"))
    set_auto_git(enabled)
    return {"ok": True, "auto_git": enabled}
