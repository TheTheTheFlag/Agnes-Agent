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
async def api_deliverables_preview(name: str = "", path: str = ""):
    """预览交付物文件内容（文本）。name 为根下的文件名；path 为相对根目录的子路径（如 短剧/渊核/script.json）。"""
    import os as _os
    from app.config import DELIVERABLES_DIR
    rel = path.strip("/\\") if path.strip("/\\") else _os.path.basename(name)
    root = str(DELIVERABLES_DIR)
    p = _os.path.normpath(_os.path.join(root, rel))
    if not p.startswith(root):  # 防路径穿越
        return JSONResponse({"error": "非法路径"}, status_code=400)
    if not _os.path.isfile(p):
        return JSONResponse({"error": "文件不存在"}, status_code=404)
    try:
        with open(p, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read(5000)
        return {"name": rel, "content": content}
    except Exception as e:
        return {"name": rel, "content": f"[读取失败] {e}"}


@router.get("/api/deliverables/download")
async def api_deliverables_download(name: str = "", path: str = ""):
    """下载交付物文件。name 为根下的文件名；path 为相对根目录的子路径（如 短剧/渊核/script.json）。"""
    import os as _os
    from fastapi.responses import FileResponse
    from app.config import DELIVERABLES_DIR
    rel = path.strip("/\\") if path.strip("/\\") else _os.path.basename(name)
    root = str(DELIVERABLES_DIR)
    p = _os.path.normpath(_os.path.join(root, rel))
    if not p.startswith(root):  # 防路径穿越
        return JSONResponse({"error": "非法路径"}, status_code=400)
    if not _os.path.isfile(p):
        return JSONResponse({"error": "文件不存在"}, status_code=404)
    return FileResponse(p, filename=_os.path.basename(p))


@router.get("/api/deliverables/view")
async def api_deliverables_view(name: str = "", path: str = ""):
    """内嵌渲染交付物（iframe 用，不触发下载）。html/md 返回原文，图片直接回源文件。"""
    import os as _os
    from fastapi.responses import FileResponse, HTMLResponse
    from app.config import DELIVERABLES_DIR
    rel = path.strip("/\\") if path.strip("/\\") else _os.path.basename(name)
    root = str(DELIVERABLES_DIR)
    p = _os.path.normpath(_os.path.join(root, rel))
    if not p.startswith(root):  # 防路径穿越
        return JSONResponse({"error": "非法路径"}, status_code=400)
    if not _os.path.isfile(p):
        return JSONResponse({"error": "文件不存在"}, status_code=404)
    low = rel.lower()
    if low.endswith((".html", ".htm", ".md", ".txt")):
        try:
            with open(p, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
        except Exception as e:
            return JSONResponse({"error": f"[读取失败] {e}"}, status_code=500)
        if low.endswith((".html", ".htm")):
            return HTMLResponse(content)
        return HTMLResponse("<pre style='white-space:pre-wrap;word-break:break-all;font-size:12px'>"
                             + _pre_escape(content) + "</pre>")
    if low.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".webm")):
        return FileResponse(p, media_type="application/octet-stream",
                            headers={"Cache-Control": "public, max-age=86400"})
    return FileResponse(p, media_type="application/octet-stream",
                        headers={"Cache-Control": "public, max-age=86400"})


def _pre_escape(t: str) -> str:
    import html as _html
    return _html.escape(t)


@router.get("/api/deliverables/tree")
async def api_deliverables_tree(path: str = ""):
    """递归列出交付物目录树。path 为相对根目录的子路径（如 短剧/渊核/），返回 {files:[{path,name,size}]}。"""
    import os as _os
    from app.config import DELIVERABLES_DIR
    root = str(DELIVERABLES_DIR)
    rel = path.strip("/\\")
    base = _os.path.normpath(_os.path.join(root, rel))
    if not base.startswith(root) or not _os.path.isdir(base):
        return JSONResponse({"error": "目录不存在"}, status_code=404)
    files = []
    for dirpath, dirnames, filenames in _os.walk(base):
        dirnames.sort()
        for fn in sorted(filenames):
            fp = _os.path.join(dirpath, fn)
            relp = _os.path.relpath(fp, root).replace("\\", "/")
            try:
                size = _os.path.getsize(fp)
            except Exception:
                size = 0
            files.append({"path": relp, "name": fn, "size": size})
    return {"files": files, "dir": rel}


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
