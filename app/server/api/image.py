"""app.server.api.image — 图片创作工作台后端（Agnes Image 2.5/2.1/2.0 Flash）。

把「文生图 / 图生图 / 多图合成」封装为简单 API：
  - 密钥复用项目主 agnes 网关接入（.model_config 中 base_url 含 agnes-ai 的 provider，
    支持逗号分隔多 key 自动轮换），退化到 skills/agnes-media/keys.json。
  - 产物统一落盘 data/image_library/（原图 + 缩略图），manifest.json 记录参数，
    前端"作品库"画廊直接读取，无需二次联网。
  - 参考图复用 /api/upload 落盘的 uploads/ 路径，后端转 Data URI 传给上游。
全部端点受登录中间件保护。
"""
import base64
import json
import mimetypes
import os
import re
import threading
import time
import uuid

import requests
from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

from app.config import BASE_DIR, MODEL_CONFIG_PATH, user_subdir

router = APIRouter(prefix="/api/image", tags=["image"])

# 支持的模型 / 档位 / 宽高比（与官方文档一致）
IMAGE_MODELS = [
    "agnes-image-2.5-flash",
    "agnes-image-2.1-flash",
    "agnes-image-2.0-flash",
]
IMAGE_MODEL_NOTES = {
    "agnes-image-2.5-flash": "最新一代 · 高信息密度 / 构图保留最佳",
    "agnes-image-2.1-flash": "升级版 · 复杂构图与细节优化",
    "agnes-image-2.0-flash": "高性能 · 快速出图",
}
IMAGE_SIZES = ("1K", "2K", "3K", "4K")
IMAGE_RATIOS = ("1:1", "3:4", "4:3", "16:9", "9:16", "2:3", "3:2", "21:9")
IMAGE_MODES = ("txt2img", "img2img", "multi")

_UPSTREAM = "https://api.agnes-ai.cn/v1/images/generations"
_TIMEOUT = 360          # 官方建议 60-360s
_LIB = user_subdir("data_dir", "image_library")          # 按当前用户解析
_MANIFEST = user_subdir("data_dir", "image_library", "manifest.json")
_THUMB_MAX = 520
_MAX_GALLERY = 300      # 作品库保留上限（旧作品仅从列表移除，文件仍在磁盘）


def _agnes_keys() -> list:
    """解析 agnes 图片密钥列表（.model_config 优先，skills/agnes-media/keys.json 兜底）。"""
    keys: list = []
    try:
        with open(MODEL_CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        for cm in cfg.get("custom", []) or []:
            bu = str(cm.get("base_url") or "")
            if "agnes-ai" in bu or bu.startswith("https://api.agnes-ai"):
                for k in str(cm.get("api_key") or "").split(","):
                    k = k.strip()
                    if k:
                        keys.append(k)
    except Exception:
        pass
    if not keys:
        try:
            kf = os.path.join(BASE_DIR, "app", "skills", "agnes-media", "keys.json")
            with open(kf, "r", encoding="utf-8") as f:
                d = json.load(f)
            for k in (d.get("agnes"), d.get("api_key")):
                if str(k or "").strip():
                    keys.append(str(k).strip())
        except Exception:
            pass
    seen, out = set(), []
    for k in keys:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def _mask_key(k: str, tail: int = 4) -> str:
    return (k[:10] + "…" + k[-tail:]) if len(k) > 14 else "sk-…"


# ---------------- 作品库 manifest ----------------
# 改为每用户独立的内存列表 + 锁
_user_items: dict = {}   # {username: [...]}
_user_locks: dict = {}   # {username: threading.Lock()}

def _user_items_lock(username: str) -> threading.Lock:
    if username not in _user_locks:
        _user_locks[username] = threading.Lock()
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
        except Exception:
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


def _make_thumb(src: str, dst: str) -> bool:
    try:
        from PIL import Image
        with Image.open(src) as im:
            im = im.convert("RGB")
            im.thumbnail((_THUMB_MAX, _THUMB_MAX))
            im.save(dst, "JPEG", quality=82)
        return True
    except Exception:
        return False


_EXT_RE = re.compile(r"^[A-Za-z0-9]{1,32}$")


def _path_to_data_uri(relpath: str) -> str:
    """参考图路径 → Data URI（用于 extra_body.image）。
    支持两种写法：uploads/<name>（/api/upload 上传文件）与 image_library/<name>（作品库复用）。"""
    rel = (relpath or "").replace("\\", "/").replace("/api/uploads/", "uploads/")
    name = os.path.basename(rel)
    if not _EXT_RE.match(os.path.splitext(name)[0] or "x") and not _EXT_RE.match(name.split(".")[-1] or "x"):
        name = name.replace(".", "_")
    fp = None
    if rel.startswith("uploads/"):
        cand = os.path.join(str(user_subdir("uploads_dir")), name)
        if os.path.isfile(cand):
            fp = cand
    elif rel.startswith("image_library/"):
        cand = os.path.join(str(_LIB), name)
        if os.path.isfile(cand):
            fp = cand
    if not fp:
        cand = os.path.join(str(user_subdir("uploads_dir")), name)
        if os.path.isfile(cand):
            fp = cand
    if not fp:
        raise ValueError(f"参考图文件不存在: {relpath}")
    try:
        raw = open(fp, "rb").read()
    except OSError as e:
        raise ValueError(f"参考图读取失败: {e}")
    if not raw:
        raise ValueError("参考图文件为空")
    if len(raw) > 25 * 1024 * 1024:
        raise ValueError("参考图超过 25MB 限制")
    mime = mimetypes.guess_type(name)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def _call_generate(key: str, payload: dict) -> dict:
    resp = requests.post(_UPSTREAM, headers={
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }, json=payload, timeout=_TIMEOUT)
    if resp.status_code >= 400:
        snippet = resp.text[:300]
        raise RuntimeError(f"上游 HTTP {resp.status_code}: {snippet}")
    data = resp.json().get("data") or []
    if not data:
        raise RuntimeError(f"上游返回空结果: {resp.text[:200]}")
    first = data[0] or {}
    b64 = first.get("b64_json") or ""
    url = first.get("url") or ""
    if not b64 and not url:
        raise RuntimeError("上游未返回图像数据")
    return {"b64": b64, "url": url}


def _persist_item(mode: str, model: str, prompt: str, size: str, ratio: str,
                  n_refs: int, b64: str, url: str, username: str) -> dict:
    # 切换到目标用户路径
    old_user = current_user()
    try:
        set_current_user(username)
        os.makedirs(_LIB, exist_ok=True)
        item_id = uuid.uuid4().hex[:12]
        raw_fp = None
        thumb_fp = None
        if b64:
            try:
                raw_bytes = base64.b64decode(b64)
            except Exception:
                raw_bytes = b""
            if raw_bytes:
                raw_fp = os.path.join(_LIB, f"{item_id}.png")
                with open(raw_fp, "wb") as f:
                    f.write(raw_bytes)
                thumb_fp = os.path.join(_LIB, f"{item_id}_t.jpg")
                _make_thumb(raw_fp, thumb_fp)
        if not raw_fp and url:
            try:
                r = requests.get(url, timeout=_TIMEOUT)
                if r.status_code == 200 and r.content:
                    ext = os.path.splitext(url.split("?")[0])[1][:5] or ".png"
                    raw_fp = os.path.join(_LIB, f"{item_id}{ext}")
                    with open(raw_fp, "wb") as f:
                        f.write(r.content)
                    thumb_fp = os.path.join(_LIB, f"{item_id}_t.jpg")
                    _make_thumb(raw_fp, thumb_fp)
            except Exception:
                raw_fp = None
        if not raw_fp:
            raise RuntimeError("产物保存失败（上游既无 Base64 也无法下载）")

        item = {
            "id": item_id,
            "mode": mode,
            "model": model,
            "prompt": prompt[:1000],
            "size": size,
            "ratio": ratio,
            "n_refs": n_refs,
            "file": os.path.basename(raw_fp),
            "thumb": os.path.basename(thumb_fp) if thumb_fp else "",
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "user": username,  # 记录归属用户
        }
        with _user_items_lock(username):
            items = _user_items.setdefault(username, [])
            items.insert(0, item)
            items[:] = items[:_MAX_GALLERY]
        _save_user_manifest(username)
        return item
    finally:
        set_current_user(old_user)



@router.get("/status")
async def status():
    keys = _agnes_keys()
    return {
        "ok": True,
        "configured": bool(keys),
        "key_count": len(keys),
        "keys_hint": _mask_key(keys[0]) if keys else "",
        "models": [{"id": m, "note": IMAGE_MODEL_NOTES.get(m, "")} for m in IMAGE_MODELS],
        "sizes": list(IMAGE_SIZES),
        "ratios": list(IMAGE_RATIOS),
        "note": "当前所有输出档位与参考图均免费",
    }


@router.post("/generate")
async def generate(payload: dict, request: Request):
    p = payload or {}
    mode = str(p.get("mode") or "txt2img")
    model = str(p.get("model") or "agnes-image-2.5-flash")
    prompt = str(p.get("prompt") or "").strip()
    size = str(p.get("size") or "2K")
    ratio = str(p.get("ratio") or "1:1")
    refs = [str(x) for x in (p.get("refs") or []) if str(x).strip()]
    count = int(p.get("count") or 1)
    # 获取当前用户
    username = getattr(getattr(request, "state", None), "username", None) or current_user()

    if mode not in IMAGE_MODES:
        return JSONResponse({"error": f"mode 必须是 {'/'.join(IMAGE_MODES)}"}, status_code=400)
    if model not in IMAGE_MODELS:
        return JSONResponse({"error": f"不支持的模型: {model}"}, status_code=400)
    if not prompt:
        return JSONResponse({"error": "提示词不能为空"}, status_code=400)
    if size not in IMAGE_SIZES:
        return JSONResponse({"error": f"size 必须是 {'/'.join(IMAGE_SIZES)}"}, status_code=400)
    if ratio not in IMAGE_RATIOS:
        return JSONResponse({"error": f"ratio 必须是 {'/'.join(IMAGE_RATIOS)}"}, status_code=400)
    if not 1 <= count <= 4:
        return JSONResponse({"error": "count 范围 1-4"}, status_code=400)
    if mode == "img2img" and len(refs) < 1:
        return JSONResponse({"error": "图生图至少需要 1 张参考图"}, status_code=400)
    if mode == "multi" and len(refs) < 2:
        return JSONResponse({"error": "多图合成至少需要 2 张参考图"}, status_code=400)
    if len(refs) > 5:
        return JSONResponse({"error": "参考图最多 5 张"}, status_code=400)

    keys = _agnes_keys()
    if not keys:
        return JSONResponse({
            "error": "未检测到 agnes 图片 API Key：请在「模型」页接入 agnes 网关（base_url 含 agnes-ai）",
        }, status_code=503)

    data_uris = []
    try:
        data_uris = [_path_to_data_uri(r) for r in refs]
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    base_payload = {"model": model, "prompt": prompt, "size": size}
    if ratio:
        base_payload["ratio"] = ratio
    if data_uris:
        base_payload["extra_body"] = {"image": data_uris, "response_format": "b64_json"}
    else:
        base_payload["return_base64"] = True

    items = []
    for i in range(count):
        payload_dup = dict(base_payload)
        if count > 1:
            payload_dup["prompt"] = prompt.replace("#i", "").replace("{i}", "") or prompt
        last_err = ""
        ok = False
        for key in keys:
            try:
                got = _call_generate(key, payload_dup)
                item = _persist_item(mode, model, prompt, size, ratio, len(data_uris),
                                     got["b64"], got["url"], username)
                items.append(item)
                ok = True
                break
            except RuntimeError as e:
                msg = str(e)
                if "401" in msg or "403" in msg or "unauthorized" in msg.lower() or "invalid" in msg.lower():
                    last_err = msg
                    continue  # 换下一个 key
                last_err = msg
                break
        if not ok:
            return JSONResponse({"error": f"第 {i + 1} 张生成失败: {last_err}"}, status_code=502)

    return {"ok": True, "count": len(items), "items": items}


@router.get("/history")
async def history(limit: int = 60, offset: int = 0, request: Request = None):
    """返回当前用户的作品列表"""
    username = getattr(getattr(request, "state", None), "username", None) or current_user()
    limit = max(1, min(limit, _MAX_GALLERY))
    offset = max(0, offset)
    with _user_items_lock(username):
        items = _get_user_items(username)
    return {"ok": True, "total": len(items), "items": items[offset: offset + limit]}


@router.get("/file/{item_id}")
async def file(item_id: str, thumb: int = 0, request: Request = None):
    """获取作品文件，仅允许访问当前用户自己的作品"""
    if not _EXT_RE.match(item_id):
        return JSONResponse({"error": "非法 id"}, status_code=400)
    username = getattr(getattr(request, "state", None), "username", None) or current_user()
    found = None
    with _user_items_lock(username):
        for it in _get_user_items(username):
            if it.get("id") == item_id:
                found = it
                break
    if not found:
        return JSONResponse({"error": "作品不存在"}, status_code=404)
    name = (found.get("thumb") if thumb else found.get("file")) or found.get("file")
    fp = os.path.join(_LIB, os.path.basename(name))
    if not os.path.isfile(fp):
        return JSONResponse({"error": "文件缺失"}, status_code=404)
    headers = {"Cache-Control": "public, max-age=86400"} if not thumb else {}
    return FileResponse(fp, headers=headers)


@router.post("/delete")
async def delete(payload: dict, request: Request):
    """删除作品，仅允许删除自己创建的作品"""
    username = getattr(getattr(request, "state", None), "username", None) or current_user()
    ids = set(str(x) for x in ((payload or {}).get("ids") or []) if str(x).strip())
    if not ids:
        return JSONResponse({"error": "ids 必填"}, status_code=400)
    with _user_items_lock(username):
        items = _get_user_items(username)
        before = len(items)
        items[:] = [it for it in items if it.get("id") not in ids]
        deleted = before - len(items)
    _save_user_manifest(username)
    return {"ok": True, "deleted": deleted}