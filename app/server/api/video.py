"""app.server.api.video — 视频创作工作台后端（Agnes Video 2.5 Flash / V2.0）。

把「文生视频 / 图生视频 / 首尾帧 / 关键帧 / 图片参考」封装为简单 API：
  - 密钥复用 `app.server.api.image._agnes_keys`（每用户自己的 key；管理员轮换全库
    账号池，支持逗号分隔多 key）。视频接口按 key 限流（429），创建任务时逐个轮换。
  - 异步任务：POST 创建拿到 video_id → 前端轮询 /api/video/task/{id} → 后端再查上游，
    completed 后自动把 mp4 下载到 data/video_library/（ffmpeg 抽帧生成封面 + 读时长）。
  - 参考图：两个模型都直接内联 Data URI（实测 flash 的 first_frame/last_frame/images
    同样接受 Base64/Data URI，且素材体积不能过小，否则会被判为「非法载体」）。
  - 兜底：若上游仍要求「公开 http(s) URL」，则把图片复制到 data/video_ref/ 下的随机名，
    经公开挂载 /pub/video-ref 暴露给上游；公开地址 = 环境变量 AGNES_PUBLIC_BASE_URL
    （优先级最高）或本次请求的 base_url。
全部端点受登录中间件保护（/pub/video-ref 除外，仅供上游抓取参考图）。
"""
import base64
import json
import mimetypes
import os
import re
import shutil
import subprocess
import threading
import time
import uuid

import requests
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

from app.config import user_subdir
from app.server.api.image import _agnes_keys, _mask_key
from app.userctx import current_user, set_current_user

router = APIRouter(prefix="/api/video", tags=["video"])

_CREATE_URL = "https://api.agnes-ai.cn/v1/videos"
_QUERY_URL = "https://api.agnes-ai.cn/agnesapi"
_TIMEOUT = 120              # 创建任务超时
_DL_TIMEOUT = 900           # 下载视频（大文件）
_LIB = user_subdir("data_dir", "video_library")          # 按当前用户解析
_REF_DIR = user_subdir("data_dir", "video_ref")
_MANIFEST = user_subdir("data_dir", "video_library", "manifest.json")
_THUMB_MAX = 720
_MAX_GALLERY = 200
_REF_KEEP_SEC = 3 * 24 * 3600   # 参考图临时文件保留 3 天

# 模型 / 模式 / 参数（与官方文档一致）
VIDEO_MODELS = {
    "agnes-video-2.5-flash": {
        "label": "Agnes Video 2.5 Flash",
        "note": "OpenAI Videos 兼容 · 720P · 首尾帧 + 图片参考",
        "modes": {
            "text": {"label": "文生视频", "icon": "✦", "desc": "一句话描述 → 生成视频",
                     "needs": [], "hint": "[主体] + [动作] + [场景] + [镜头运镜] + [光线] + [风格]",
                     "ex": "雨后的未来城市街道，霓虹灯倒映在湿滑地面，一辆银色跑车缓慢驶过，电影级运镜，自然环境声"},
            "keyframe": {"label": "首尾帧", "icon": "⇥⇤", "desc": "首帧 / 尾帧控制过渡",
                         "needs": ["frames"], "hint": "描述首帧到尾帧如何变化，以及哪些元素要保持稳定",
                         "ex": "人物从首帧姿态自然转身走向窗边，镜头缓慢推进并平滑过渡到尾帧，保持面部与服装一致"},
            "reference": {"label": "图片参考", "icon": "⊕", "desc": "以 1–5 张图作为参考生成",
                          "needs": ["images"], "hint": "用 <Picture 1> / <Picture 2> 指代参考图，说明角色与风格",
                          "ex": "以 <Picture 1> 中的角色和美术风格为参考，角色在花田中自然奔跑，保持外观一致"},
        },
        "seconds": ["4", "5", "6", "8", "10", "12"],
        "aspect_ratios": ["16:9", "9:16", "1:1", "4:3", "3:4", "21:9"],
        "size": "720P",
    },
    "agnes-video-v2.0": {
        "label": "Agnes Video V2.0",
        "note": "文生 / 图生 / 关键帧 · 可调帧率与分辨率档",
        "modes": {
            "ti2vid": {"label": "文生视频", "icon": "✦", "desc": "纯文本生成视频",
                       "needs": [], "hint": "[主体] + [动作] + [场景] + [镜头运镜] + [光线] + [风格]",
                       "ex": "A cinematic shot of a cat walking on the beach at sunset, soft ocean waves, warm golden lighting, realistic motion"},
            "img2vid": {"label": "图生视频", "icon": "✎", "desc": "1 张图 → 动态视频",
                        "needs": ["image"], "hint": "描述「什么该动」，以及哪些主体要保持稳定",
                        "ex": "Animate the character with subtle breathing motion, hair moving gently in the wind, while keeping the face and outfit consistent"},
            "keyframes": {"label": "关键帧动画", "icon": "⇥⇤", "desc": "多张关键帧之间平滑过渡",
                          "needs": ["images2"], "hint": "清晰描述关键帧之间的过渡关系与一致性",
                          "ex": "Create a smooth transition from the first keyframe to the second keyframe, maintaining character identity and natural motion"},
        },
        "durations": [{"label": "约 3 秒", "num_frames": 81}, {"label": "约 5 秒", "num_frames": 121},
                      {"label": "约 10 秒", "num_frames": 241}, {"label": "约 18 秒", "num_frames": 441}],
        "frame_rates": [24, 30],
        "resolutions": ["480p", "720p", "1080p"],
        "ratios": ["16:9", "9:16", "1:1", "4:3", "3:4"],
    },
}

# 分辨率档 → 各画幅像素（v2.0，上游会再做标准化）
_RES_BASE = {  # (short_side, long_side)
    "480p": (480, 854),
    "720p": (720, 1280),
    "1080p": (1080, 1920),
}


def _dimensions(ratio: str, resolution: str) -> tuple:
    """按「短边 = 分辨率档」换算像素：16:9@720p → 1280x720，1:1@1080p → 1080x1080。"""
    short, _long = _RES_BASE.get(resolution, _RES_BASE["720p"])
    a, b = (ratio.split(":") + ["16", "9"])[:2]
    try:
        aw, ah = int(a), int(b)
    except ValueError:
        aw, ah = 16, 9
    if aw <= 0 or ah <= 0:
        aw, ah = 16, 9
    unit = short / min(aw, ah)
    return int(round(unit * aw)), int(round(unit * ah))


# ---------------- 作品库 manifest ----------------
# 改为每用户独立的内存列表 + 锁
_user_items: dict = {}   # {username: [...]}
_user_locks: dict = {}   # {username: threading.RLock()}


def _user_items_lock(username: str) -> threading.RLock:
    if username not in _user_locks:
        _user_locks[username] = threading.RLock()
    return _user_locks[username]


def _get_user_items(username: str) -> list:
    if username not in _user_items:
        _load_user_manifest(username)
    return _user_items[username]


def _load_user_manifest(username: str):
    """按用户加载作品列表（模块级 _LIB 是路径代理，会自动按当前用户解析）"""
    old_user = current_user()
    try:
        set_current_user(username)
        items = []
        try:
            with open(_MANIFEST, "r", encoding="utf-8") as f:
                raw = json.load(f)
            items = raw.get("items", []) if isinstance(raw, dict) else []
        except Exception as e:
            pass
        with _user_items_lock(username):
            _user_items[username] = items
    finally:
        set_current_user(old_user)


def _save_user_manifest(username: str):
    """保存当前用户的作品列表到磁盘"""
    old_user = current_user()
    try:
        set_current_user(username)
        os.makedirs(_LIB, exist_ok=True)
        with _user_items_lock(username):
            items = _user_items.get(username, [])
        with open(_MANIFEST, "w", encoding="utf-8") as f:
            json.dump({"items": items[:_MAX_GALLERY]}, f, ensure_ascii=False, indent=1)
    finally:
        set_current_user(old_user)


def _find(item_id: str, username: str):
    items = _get_user_items(username)
    return next((it for it in items if it.get("id") == item_id), None)


_id_re = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_ext_re = re.compile(r"^[A-Za-z0-9]{1,8}$")


# ---------------- ffmpeg ----------------
def _make_thumb(src: str, dst: str) -> bool:
    for ss in ("1", "0"):
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-ss", ss, "-i", src, "-frames:v", "1",
                 "-vf", f"scale={_THUMB_MAX}:-2", dst],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90, check=True)
            if os.path.isfile(dst) and os.path.getsize(dst) > 0:
                return True
        except Exception:
            continue
    return False


def _probe_duration(src: str):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", src],
            capture_output=True, text=True, timeout=30)
        val = (out.stdout or "").strip()
        return round(float(val), 2) if val else None
    except Exception:
        return None


# ---------------- 参考图处理 ----------------
def _resolve_local_file(relpath: str) -> str:
    """把前端传来的相对路径解析到项目内真实文件（uploads/ 或 image_library/）。"""
    rel = (relpath or "").replace("\\", "/").replace("/api/uploads/", "uploads/")
    name = os.path.basename(rel)
    _uploads = str(user_subdir("uploads_dir"))
    _img_lib = str(user_subdir("data_dir", "image_library"))
    if rel.startswith("uploads/"):
        cand = os.path.join(_uploads, name)
        if os.path.isfile(cand):
            return cand
    elif rel.startswith("image_library/"):
        cand = os.path.join(_img_lib, name)
        if os.path.isfile(cand):
            return cand
    for cand in (os.path.join(_uploads, name), os.path.join(_img_lib, name)):
        if os.path.isfile(cand):
            return cand
    raise ValueError(f"参考图文件不存在: {relpath}")


def _to_data_uri(relpath: str) -> str:
    fp = _resolve_local_file(relpath)
    raw = open(fp, "rb").read()
    if not raw:
        raise ValueError("参考图文件为空")
    if len(raw) > 25 * 1024 * 1024:
        raise ValueError("参考图超过 25MB 限制")
    mime = mimetypes.guess_type(fp)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def _public_base(request) -> str:
    env = (os.environ.get("AGNES_PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if env:
        return env
    try:
        return str(request.base_url).rstrip("/")
    except Exception:
        return ""


def _is_local_base(base: str) -> bool:
    return bool(re.search(r"//(localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])", base or ""))


def _to_public_url(relpath: str, base: str) -> str:
    """复制参考图到 data/video_ref/ 下的随机名，返回可被上游抓取的公开 URL。"""
    fp = _resolve_local_file(relpath)
    os.makedirs(_REF_DIR, exist_ok=True)
    ext = os.path.splitext(fp)[1].lower()
    if not _ext_re.match(ext.lstrip(".") or "png"):
        ext = mimetypes.guess_extension(mimetypes.guess_type(fp)[0] or "") or ".png"
    token = uuid.uuid4().hex[:20]
    dst = os.path.join(_REF_DIR, f"{token}{ext}")
    shutil.copyfile(fp, dst)
    return f"{base}/pub/video-ref/{token}{ext}"


def _prune_refs():
    try:
        now = time.time()
        for name in os.listdir(_REF_DIR):
            fp = os.path.join(_REF_DIR, name)
            if os.path.isfile(fp) and now - os.path.getmtime(fp) > _REF_KEEP_SEC:
                os.remove(fp)
    except Exception:
        pass


# ---------------- 上游调用 ----------------
def _post_create(key: str, payload: dict) -> requests.Response:
    return requests.post(_CREATE_URL, headers={
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }, json=payload, timeout=_TIMEOUT)


def _create_task(keys: list, payload: dict) -> tuple:
    """轮换 key 创建任务：429（限流）/ 5xx 换下一个 key；4xx 参数错误直接抛。"""
    last = ""
    for idx, key in enumerate(keys):
        try:
            r = _post_create(key, payload)
        except Exception as e:
            last = f"网络错误: {e}"
            continue
        if r.status_code == 429 or r.status_code >= 500:
            last = f"HTTP {r.status_code}: {r.text[:180]}"
            continue
        if r.status_code >= 400:
            raise RuntimeError(f"上游 HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        if not data.get("video_id"):
            last = f"上游未返回 video_id: {r.text[:180]}"
            continue
        return idx, data
    raise RuntimeError(f"所有 Key 均不可用（可能全部限流，请稍后重试）: {last}")


def _query_task(key: str, video_id: str, model: str) -> dict:
    r = requests.get(_QUERY_URL, params={"video_id": video_id, "model_name": model},
                     headers={"Authorization": f"Bearer {key}"}, timeout=60)
    if r.status_code >= 400:
        raise RuntimeError(f"上游查询 HTTP {r.status_code}: {r.text[:200]}")
    return r.json()


def _download(url: str, dst: str) -> bool:
    try:
        with requests.get(url, stream=True, timeout=_DL_TIMEOUT) as r:
            if r.status_code >= 400:
                return False
            tmp = dst + ".part"
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    if chunk:
                        f.write(chunk)
            os.replace(tmp, dst)
            return os.path.getsize(dst) > 0
    except Exception:
        return False


def _pick_key(keys: list, item: dict) -> str:
    if not keys:
        return ""
    idx = item.get("key_idx")
    if isinstance(idx, int) and 0 <= idx < len(keys):
        return keys[idx]
    return keys[0]


# ---------------- 参数构造 ----------------
def _build_payload(model: str, mode: str, payload: dict, request, use_public: bool = False) -> dict:
    prompt = str(payload.get("prompt") or "").strip()
    refs = [str(x) for x in (payload.get("refs") or []) if str(x).strip()]
    seed = payload.get("seed")

    def carrier(rel: str) -> str:
        """参考图载体：默认内联 Data URI；use_public 时改用公开 http URL（兜底）。"""
        if use_public:
            return _to_public_url(rel, _public_base(request))
        return _to_data_uri(rel)

    if model == "agnes-video-2.5-flash":
        out = {"model": model, "mode": mode, "prompt": prompt,
               "size": "720P", "n": 1,
               "seconds": str(payload.get("seconds") or "5"),
               "aspect_ratio": str(payload.get("aspect_ratio") or "16:9")}
        if seed not in (None, ""):
            out["seed"] = int(seed)
        if mode == "keyframe":
            urls = [carrier(r) for r in refs[:2]]
            if urls:
                out["first_frame"] = urls[0]
            if len(urls) > 1:
                out["last_frame"] = urls[1]
        elif mode == "reference":
            out["images"] = [carrier(r) for r in refs[:5]]
        return out

    # agnes-video-v2.0
    out = {"model": model, "prompt": prompt}
    num_frames = payload.get("num_frames")
    frame_rate = payload.get("frame_rate")
    width = payload.get("width")
    height = payload.get("height")
    if num_frames:
        out["num_frames"] = int(num_frames)
    if frame_rate:
        out["frame_rate"] = float(frame_rate)
    if len(refs) and mode in ("img2vid", "keyframes"):
        data_uris = [_to_data_uri(r) for r in refs]
        if mode == "img2vid":
            out["image"] = data_uris[0]
            out["mode"] = "ti2vid"
        else:
            out["extra_body"] = {"image": data_uris[:4], "mode": "keyframes"}
            out["mode"] = "keyframes"
    else:
        out["mode"] = "ti2vid"
        if not width or not height:
            ratio = str(payload.get("aspect_ratio") or "16:9")
            res = str(payload.get("resolution") or "720p")
            w, h = _dimensions(ratio, res)
            width, height = width or w, height or h
    if width and height:
        out["width"] = int(width)
        out["height"] = int(height)
    if seed not in (None, ""):
        out["seed"] = int(seed)
    neg = str(payload.get("negative_prompt") or "").strip()
    if neg:
        out["negative_prompt"] = neg
    return out


def _persist_new(model: str, mode: str, prompt: str, payload: dict, key_idx: int, created: dict, username: str) -> dict:
    item = {
        "id": uuid.uuid4().hex[:12],
        "model": model,
        "mode": mode,
        "prompt": prompt[:1000],
        "status": created.get("status") or "queued",
        "progress": int(created.get("progress") or 0),
        "video_id": created.get("video_id") or "",
        "task_id": created.get("task_id") or created.get("id") or "",
        "key_idx": key_idx,
        "seconds": str(created.get("seconds") or payload.get("seconds") or ""),
        "size": str(created.get("size") or payload.get("size") or ""),
        "ratio": str(payload.get("aspect_ratio") or ""),
        "num_frames": payload.get("num_frames"),
        "frame_rate": payload.get("frame_rate"),
        "seed": payload.get("seed"),
        "remote_url": "",
        "file": "",
        "thumb": "",
        "duration": None,
        "error": "",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "completed_at": "",
    }
    with _user_items_lock(username):
        items = _get_user_items(username)
        items.insert(0, item)
        items[:] = items[: _MAX_GALLERY]
    _save_user_manifest(username)
    return item




# ---------------- 端点 ----------------
@router.get("/status")
def status(request: Request):
    keys = _agnes_keys()
    has_ffmpeg = bool(shutil.which("ffmpeg"))
    return {
        "ok": True,
        "configured": bool(keys),
        "key_count": len(keys),
        "keys_hint": _mask_key(keys[0]) if keys else "",
        "ffmpeg": has_ffmpeg,
        "public_base": _public_base(request),
        "public_ok": not _is_local_base(_public_base(request)),
        "models": [dict(id=k, **v) for k, v in VIDEO_MODELS.items()],
        "note": "当前视频生成全部规格免费",
    }


@router.post("/generate")
def generate(payload: dict, request: Request):
    p = payload or {}
    model = str(p.get("model") or "agnes-video-2.5-flash")
    meta = VIDEO_MODELS.get(model)
    if not meta:
        return JSONResponse({"error": f"不支持的模型: {model}"}, status_code=400)
    mode = str(p.get("mode") or list(meta["modes"])[0])
    if mode not in meta["modes"]:
        return JSONResponse({"error": f"{model} 不支持模式 {mode}"}, status_code=400)
    prompt = str(p.get("prompt") or "").strip()
    if not prompt:
        return JSONResponse({"error": "提示词不能为空"}, status_code=400)
    refs = [str(x) for x in (p.get("refs") or []) if str(x).strip()]
    need = meta["modes"][mode].get("needs") or []
    if "image" in need and len(refs) < 1:
        return JSONResponse({"error": "图生视频需要 1 张参考图"}, status_code=400)
    if "images2" in need and len(refs) < 2:
        return JSONResponse({"error": "关键帧动画至少需要 2 张参考图"}, status_code=400)
    if "images" in need and len(refs) < 1:
        return JSONResponse({"error": "图片参考模式至少需要 1 张参考图"}, status_code=400)
    if len(refs) > 5:
        return JSONResponse({"error": "参考图最多 5 张"}, status_code=400)

    keys = _agnes_keys()
    if not keys:
        return JSONResponse({"error": "未检测到 agnes API Key：请在「模型」页接入 agnes 网关（base_url 含 agnes-ai）"},
                            status_code=503)

    try:
        upstream_payload = _build_payload(model, mode, p, request)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    _prune_refs()
    try:
        key_idx, created = _create_task(keys, upstream_payload)
    except RuntimeError as e:
        msg = str(e)
        # 上游拒收内联载体（多为图片过小或格式不符）时，退回公开 URL 兜底
        if ("公开 http(s) URL" in msg or "载体 URL" in msg) and refs:
            base = _public_base(request)
            if base and not _is_local_base(base):
                try:
                    upstream_payload = _build_payload(model, mode, p, request, use_public=True)
                    key_idx, created = _create_task(keys, upstream_payload)
                except (RuntimeError, ValueError) as e2:
                    return JSONResponse({"error": f"{msg}（公开 URL 兜底失败：{e2}）"}, status_code=502)
            else:
                return JSONResponse({"error": f"{msg}（参考图体积过小或格式不受支持，请换一张更清晰的图片）"},
                                    status_code=502)
        else:
            return JSONResponse({"error": msg}, status_code=502)

    username = getattr(getattr(request, "state", None), "username", None) or current_user()
    item = _persist_new(model, mode, prompt, upstream_payload, key_idx, created, username)
    return {"ok": True, "count": 1, "items": [item]}


@router.get("/task/{item_id}")
def task(item_id: str, request: Request = None):
    if not _id_re.match(item_id):
        return JSONResponse({"error": "非法 id"}, status_code=400)
    username = getattr(getattr(request, "state", None), "username", None) or current_user()
    item = _find(item_id, username)
    if not item:
        return JSONResponse({"error": "任务不存在"}, status_code=404)
    if item.get("status") in ("completed", "failed") and item.get("file"):
        return {"ok": True, "item": item}

    keys = _agnes_keys()
    if not keys:
        return JSONResponse({"error": "未检测到 agnes API Key"}, status_code=503)
    video_id = item.get("video_id")
    if not video_id:
        return JSONResponse({"error": "任务缺少 video_id"}, status_code=500)

    try:
        data = _query_task(_pick_key(keys, item), video_id, item["model"])
    except RuntimeError as e:
        return {"ok": True, "item": item, "warning": str(e)}

    st = data.get("status") or data.get("internal_status") or item.get("status")
    item["status"] = st
    if data.get("progress") is not None:
        item["progress"] = int(data.get("progress") or 0)
    if data.get("seconds"):
        item["seconds"] = str(data.get("seconds"))
    if data.get("size"):
        item["size"] = str(data.get("size"))

    if st == "failed":
        err = data.get("error")
        item["error"] = err.get("message") if isinstance(err, dict) else str(err or "生成失败")
        item["completed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    elif st == "completed":
        url = data.get("url") or (data.get("metadata") or {}).get("url") or ""
        if url and not item.get("file"):
            os.makedirs(_LIB, exist_ok=True)
            dst = os.path.join(_LIB, f"{item['id']}.mp4")
            if _download(url, dst):
                item["file"] = os.path.basename(dst)
                thumb = os.path.join(_LIB, f"{item['id']}_t.jpg")
                if _make_thumb(dst, thumb):
                    item["thumb"] = os.path.basename(thumb)
                item["duration"] = _probe_duration(dst)
                item["remote_url"] = url
            else:
                item["error"] = "视频下载失败（上游链接可能已过期）"
        elif not url:
            item["error"] = "上游未返回视频地址"
        item["completed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

    with _user_items_lock(username):
        _save_user_manifest(username)
    return {"ok": True, "item": item}


@router.get("/history")
def history(limit: int = 60, offset: int = 0, request: Request = None):
    username = getattr(getattr(request, "state", None), "username", None) or current_user()
    limit = max(1, min(limit, _MAX_GALLERY))
    offset = max(0, offset)
    with _user_items_lock(username):
        items = _get_user_items(username)
    return {"ok": True, "total": len(items), "items": items[offset: offset + limit]}


@router.get("/file/{item_id}")
def file(item_id: str, thumb: int = 0, request: Request = None):
    if not _id_re.match(item_id):
        return JSONResponse({"error": "非法 id"}, status_code=400)
    username = getattr(getattr(request, "state", None), "username", None) or current_user()
    item = _find(item_id, username)
    if not item:
        return JSONResponse({"error": "作品不存在"}, status_code=404)
    name = (item.get("thumb") if thumb else item.get("file")) or item.get("file")
    if not name:
        return JSONResponse({"error": "文件尚未生成"}, status_code=404)
    fp = os.path.join(_LIB, os.path.basename(name))
    if not os.path.isfile(fp):
        return JSONResponse({"error": "文件缺失"}, status_code=404)
    headers = {"Cache-Control": "public, max-age=86400"} if not thumb else {}
    media = "image/jpeg" if thumb else "video/mp4"
    return FileResponse(fp, media_type=media, headers=headers)


@router.post("/delete")
def delete(payload: dict, request: Request = None):
    username = getattr(getattr(request, "state", None), "username", None) or current_user()
    ids = set(str(x) for x in ((payload or {}).get("ids") or []) if str(x).strip())
    if not ids:
        return JSONResponse({"error": "ids 必填"}, status_code=400)
    removed = []
    with _user_items_lock(username):
        items = _get_user_items(username)
        keep = []
        for it in items:
            if it.get("id") in ids:
                removed.append(it)
            else:
                keep.append(it)
        items[:] = keep
    _save_user_manifest(username)
    for it in removed:
        for key in ("file", "thumb"):
            name = it.get(key)
            if name:
                try:
                    os.remove(os.path.join(_LIB, os.path.basename(name)))
                except OSError:
                    pass
    return {"ok": True, "deleted": len(removed)}
