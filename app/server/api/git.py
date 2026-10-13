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
            if name == _THUMB_DIRNAME:      # 缩略图缓存不进清单
                continue
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


_THUMB_DIRNAME = ".thumbs"
_THUMB_MAX = 640          # 缩略图最长边上限
_THUMB_DEFAULT_W = 420    # 未指定 w 时的默认宽度
_IMG_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
_VID_EXT = (".mp4", ".webm", ".mov", ".mkv")


def _thumb_cache_path(root: str, abs_path: str, rel: str, w: int) -> str:
    """缩略图磁盘缓存键：源路径 + 源 mtime + 宽度（源图更新即自动失效）。"""
    import hashlib
    import os as _os
    try:
        mt = _os.path.getmtime(abs_path)
    except OSError:
        mt = 0
    key = hashlib.sha1(("%s|%.3f|%d" % (rel, mt, w)).encode("utf-8")).hexdigest()[:24]
    d = _os.path.join(root, _THUMB_DIRNAME)
    _os.makedirs(d, exist_ok=True)
    return _os.path.join(d, key + ".jpg")


def _make_deliv_thumb(abs_path: str, dst: str, w: int) -> bool:
    """生成 JPEG 缩略图。图片走 Pillow，视频走 ffmpeg 抽帧。失败返回 False（调用方回退原图）。"""
    import os as _os
    import subprocess
    low = abs_path.lower()
    try:
        if low.endswith(_IMG_EXT):
            from PIL import Image
            with Image.open(abs_path) as im:
                im = im.convert("RGB")
                im.thumbnail((w, w))
                im.save(dst, "JPEG", quality=82, optimize=True)
            return _os.path.getsize(dst) > 0
        if low.endswith(_VID_EXT):
            ff = "/usr/bin/ffmpeg"
            if not _os.path.exists(ff):
                return False
            # 抽 0.5s 帧；-vf scale 只缩不放。timeout 20s 防坏文件挂住请求
            r = subprocess.run(
                [ff, "-nostdin", "-loglevel", "error", "-y",
                 "-ss", "0.5", "-i", abs_path, "-frames:v", "1",
                 "-vf", "scale=%d:-2" % w, "-q:v", "4", dst],
                capture_output=True, timeout=20)
            return r.returncode == 0 and _os.path.exists(dst) and _os.path.getsize(dst) > 0
    except Exception:
        return False
    return False


@router.get("/api/deliverables/view")
async def api_deliverables_view(name: str = "", path: str = "", thumb: int = 0, w: int = 0):
    """内嵌渲染交付物（iframe 用，不触发下载）。html/md 返回原文，图片直接回源文件。

    thumb=1 时返回 ≤w 像素的 JPEG 缩略图（磁盘缓存，源文件 mtime 变更自动失效），
    供审核台等需要一次列出大量原图/视频的列表页使用，避免直出 2624×1472 PNG / 整段 mp4。
    缩略图生成失败时静默回退原文件，保证页面不至于空图。
    """
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

    # ---- 缩略图分支（仅图片/视频；文本类走原逻辑）----
    if thumb and low.endswith(_IMG_EXT + _VID_EXT):
        tw = w if w and 64 <= w <= 2048 else _THUMB_DEFAULT_W
        try:
            tp = _thumb_cache_path(root, p, rel, tw)
            if not _os.path.exists(tp):
                if not _make_deliv_thumb(p, tp, min(tw, _THUMB_MAX)):
                    raise OSError("thumb failed")
            return FileResponse(tp, media_type="image/jpeg",
                                headers={"Cache-Control": "public, max-age=604800"})
        except Exception:
            pass  # 回退原图

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
    if low.endswith(_IMG_EXT + _VID_EXT):
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
        # 跳过缩略图缓存目录（内部产物，不进交付物清单）
        dirnames[:] = sorted(d for d in dirnames if d != _THUMB_DIRNAME)
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
