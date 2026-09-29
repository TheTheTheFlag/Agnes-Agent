"""app.server.api.image — 图片创作工作台后端（Agnes Image 2.5/2.1/2.0 Flash）。

把「文生图 / 图生图 / 多图合成」封装为简单 API：
  - 密钥复用项目主 agnes 网关接入（每用户自己的 key；管理员自动轮换账号池，
    支持逗号分隔多 key），退化到 skills/agnes-media/keys.json。
  - 产物统一落盘 data/image_library/（原图 + 缩略图），manifest.json 记录参数，
    前端"作品库"画廊直接读取，无需二次联网。
  - 参考图复用 /api/upload 落盘的 uploads/ 路径，后端转 Data URI 传给上游。
全部端点受登录中间件保护。
"""
import base64
import hashlib
import json
import mimetypes
import os
import re
import threading
import time
import uuid

import requests
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

from app.config import BASE_DIR, user_subdir
from app.userctx import current_user, set_current_user

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
_MAX_GALLERY = None    # 不限制作品数量，显示全部历史作品

# 异步任务重试策略：失败自动重试直到成功（上限：总时长 6h、单次最长退避 300s）
_RETRY_TOTAL_MAX = 6 * 3600      # 任务重试总时长上限 6 小时
_RETRY_START_SLEEP = 5           # 首轮失败后的等待（秒）
_RETRY_429_SLEEP = 300           # 上游限流（429）冷却等待
_RETRY_CAP_SLEEP = 120           # 普通失败退避上限

# 提交去重：同一入参（含参考图内容哈希）在窗口内的在途任务只保留一个（防重复扣费）
_DEDUP_WINDOW = 300              # 秒；窗口内同内容且在途（queued/running）视为重复提交


def _dedup_key(payload: dict, mode: str, count: int) -> str:
    """提交指纹：入参 JSON 规范化 + 模式/张数；参考图 Data URI 使去重按图片内容而非文件名。"""
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True) + f"|mode={mode}|count={count}"
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def _find_dedup(username: str, key: str) -> list:
    """窗口内同指纹且在途的任务列表（命中则复用返回，不再新建）。"""
    if not key:
        return []
    now = time.time()
    out = []
    with _user_items_lock(username):
        for it in _get_user_items(username):
            if it.get("dedup_key") != key:
                continue
            if it.get("status") not in ("queued", "running"):
                continue
            try:
                created = time.mktime(time.strptime(it.get("created_at") or "", "%Y-%m-%d %H:%M:%S"))
            except ValueError:
                created = now
            if now - created <= _DEDUP_WINDOW:
                out.append(it)
    return out


def _task_cancelled(username: str, item_id: str) -> bool:
    """任务是否已取消/被删除（worker 每轮检查，命中即停止继续重试）。"""
    with _user_items_lock(username):
        for it in _get_user_items(username):
            if it.get("id") == item_id:
                return it.get("status") == "cancelled"
    return True   # 条目已被删除：也没有继续重试的必要


def _agnes_keys() -> list:
    """解析当前用户可用的 agnes 图片密钥。

    每用户自己的 users.agnes_key（逗号分隔多 key）；管理员为数据库全量 key 池。
    均无时退化为 skills/agnes-media/keys.json。
    返回去重后的 key 列表。
    """
    keys: list = []
    try:
        from app.userctx import resolve_user_keys
        keys = resolve_user_keys().get("agnes", []) or []
    except Exception:
        keys = []
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
# 改为每用户独立的内存列表 + 可重入锁（避免嵌套调用死锁）
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
    # 直接使用 set_current_user 切换上下文，不需要调用 current_user()
    import app.userctx
    _old_token = app.userctx._cur_user.get(None)
    try:
        app.userctx.set_current_user(username)
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
        app.userctx.set_current_user(_old_token)

def _save_user_manifest(username: str):
    """保存当前用户的作品列表到磁盘"""
    import app.userctx
    _old_token = app.userctx._cur_user.get(None)
    try:
        app.userctx.set_current_user(username)
        os.makedirs(_LIB, exist_ok=True)
        with _user_items_lock(username):
            items = _user_items.get(username, [])
        with open(_MANIFEST, "w", encoding="utf-8") as f:
            json.dump({"items": items}, f, ensure_ascii=False, indent=1)
    finally:
        app.userctx.set_current_user(_old_token)


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


# ---------------- agnes-media 技能产物同步 ----------------
# agnes-media 技能（图片/视频创作台）生成的文件默认落在
#   app/skills_builtin/agnes-media/output/YYYYMMDD/（内置版）
#   app/skills/agnes-media/output/YYYYMMDD/（用户版）
# 本函数把其中的图片复制进当前用户作品库并登记 manifest，使技能产物
# 自动出现在创作台「作品库」里。幂等：按 (basename, size) 去重。
_SKILL_OUTPUT_DIRS = (
    os.path.join(BASE_DIR, "app", "skills_builtin", "agnes-media", "output"),
    os.path.join(BASE_DIR, "app", "skills", "agnes-media", "output"),
)
_IMG_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")


def _load_deleted_sources(username: str) -> set:
    """载入该用户已删除源文件的墓碑指纹，防止 _sync_skill_outputs 重新导入。"""
    fp = os.path.join(str(user_subdir("data_dir")), "deleted_sources_image.json")
    try:
        with open(fp, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return {tuple(x) for x in raw if isinstance(x, list) and len(x) == 3}
    except Exception:
        return set()


def _save_deleted_sources(username: str, dead: set) -> None:
    fp = os.path.join(str(user_subdir("data_dir")), "deleted_sources_image.json")
    try:
        with open(fp, "w", encoding="utf-8") as f:
            json.dump([list(x) for x in dead if len(x) == 3], f, ensure_ascii=False)
    except Exception:
        pass


def _sync_skill_outputs(username: str) -> int:
    """把 agnes-media 技能产出的图片同步进当前用户作品库，返回新增数量。"""
    import shutil
    added = 0
    old_user = current_user()
    try:
        set_current_user(username)
        items = _get_user_items(username)

        def _src_key(it):
            return (it.get("src_name") or os.path.basename(it.get("file") or ""),
                    it.get("size_bytes") or 0,
                    it.get("src_mtime") or 0)

        known = {_src_key(it) for it in items}
        dead = _load_deleted_sources(username)
        for root in _SKILL_OUTPUT_DIRS:
            if not os.path.isdir(root):
                continue
            for dirpath, _, filenames in os.walk(root):
                for fn in sorted(filenames):
                    if not fn.lower().endswith(_IMG_EXTS):
                        continue
                    src = os.path.join(dirpath, fn)
                    try:
                        sz = os.path.getsize(src)
                        src_mtime = int(os.path.getmtime(src))
                    except Exception:
                        continue
                    if (fn, sz, src_mtime) in known:
                        continue
                    if (fn, sz, src_mtime) in dead:
                        continue
                    item_id = uuid.uuid4().hex[:12]
                    ext = os.path.splitext(fn)[1].lower() or ".png"
                    dst = os.path.join(str(_LIB), f"{item_id}{ext}")
                    try:
                        shutil.copy2(src, dst)
                    except Exception:
                        continue
                    thumb_fp = os.path.join(str(_LIB), f"{item_id}_t.jpg")
                    if not _make_thumb(dst, thumb_fp):
                        thumb_fp = None
                    item = {
                        "id": item_id,
                        "mode": "txt2img",
                        "model": "agnes-media",
                        "prompt": "",
                        "size": "",
                        "ratio": "",
                        "n_refs": 0,
                        "file": os.path.basename(dst),
                        "thumb": os.path.basename(thumb_fp) if thumb_fp else "",
                        "size_bytes": sz,
                        "src_name": fn,
                        "src_mtime": src_mtime,
                        "created_at": time.strftime("%Y-%m-%d %H:%M:%S",
                                                    time.localtime(os.path.getmtime(src))),
                        "user": username,
                        "source": "agnes-media",
                    }
                    with _user_items_lock(username):
                        _get_user_items(username).insert(0, item)
                    _save_user_manifest(username)
                    added += 1
    except Exception:
        pass
    finally:
        try:
            set_current_user(old_user)
        except Exception:
            pass
    return added


_EXT_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


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
    try:
        resp = requests.post(_UPSTREAM, headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }, json=payload, timeout=_TIMEOUT)
    except requests.RequestException as e:
        raise RuntimeError(f"上游连接失败: {e.__class__.__name__}: {e}")
    if resp.status_code >= 400:
        snippet = resp.text[:300]
        raise RuntimeError(f"上游 HTTP {resp.status_code}: {snippet}")
    try:
        data = (resp.json() or {}).get("data") or []
    except (ValueError, AttributeError) as e:
        raise RuntimeError(f"上游响应解析失败: {e}: {resp.text[:200]}")
    if not data:
        raise RuntimeError(f"上游返回空结果: {resp.text[:200]}")
    first = data[0] or {}
    b64 = first.get("b64_json") or ""
    url = first.get("url") or ""
    if not b64 and not url:
        raise RuntimeError("上游未返回图像数据")
    return {"b64": b64, "url": url}


def _create_pending(mode: str, model: str, prompt: str, size: str, ratio: str,
                    n_refs: int, username: str, dedup_key: str = "", source: str = "") -> dict:
    """创建「生成中」占位条目并落盘 manifest，立即返回给前端展示。"""
    item = {
        "id": uuid.uuid4().hex[:12],
        "mode": mode,
        "model": model,
        "prompt": prompt[:1000],
        "size": size,
        "ratio": ratio,
        "n_refs": n_refs,
        "source": source,
        "file": "",
        "thumb": "",
        "status": "queued",
        "progress": 0,
        "attempt": 0,
        "last_heartbeat": time.strftime("%Y-%m-%d %H:%M:%S"),
        "dedup_key": dedup_key,
        "error": "",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "completed_at": "",
        "user": username,
    }
    with _user_items_lock(username):
        items = _user_items.setdefault(username, [])
        items.insert(0, item)
        items[:] = items
    _save_user_manifest(username)
    return item


def _update_pending(username: str, item_id: str, **fields):
    """按 id 更新占位条目（status/progress/error 等），仅当任务未被删除时生效。

    已取消任务是粘性状态：除「真成功(completed)」外任何回写都不覆盖 cancelled，
    避免取消后旧线程把状态改回 running 造成「取消无效」。
    """
    with _user_items_lock(username):
        for it in _get_user_items(username):
            if it.get("id") == item_id:
                if it.get("status") == "cancelled" and fields.get("status") != "completed":
                    break
                it.update(fields)
                break
    _save_user_manifest(username)


def _finish_pending(username: str, item_id: str, got: dict) -> None:
    """生成成功后落盘原图 + 缩略图并标记 completed（复用 _persist_item 的文件逻辑）。"""
    old_user = current_user()
    try:
        set_current_user(username)
        os.makedirs(_LIB, exist_ok=True)
        raw_fp = None
        thumb_fp = None
        b64 = got.get("b64") or ""
        url = got.get("url") or ""
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
        _update_pending(username, item_id,
                        file=os.path.basename(raw_fp),
                        thumb=os.path.basename(thumb_fp) if thumb_fp else "",
                        status="completed", progress=100,
                        completed_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                        error="")
    finally:
        set_current_user(old_user)


def _worker_run(payload: dict, keys: list, username: str, item_id: str) -> None:
    """单任务后台执行：轮换 key + 失败自动重试直到成功/超时；每轮检查取消标记。"""
    t0 = time.time()
    sleep = _RETRY_START_SLEEP
    attempt = 0
    while True:
        if _task_cancelled(username, item_id):
            return                             # 已取消：停止继续重试（在途上游请求无法中断）
        attempt += 1
        _update_pending(username, item_id, status="running", progress=0, attempt=attempt,
                        last_heartbeat=time.strftime("%Y-%m-%d %H:%M:%S"))
        last_err = ""
        for key in keys:
            if _task_cancelled(username, item_id):
                return
            try:
                got = _call_generate(key, payload)
                _finish_pending(username, item_id, got)
                return  # 成功
            except RuntimeError as e:
                msg = str(e)
                last_err = msg
                if "429" in msg or "rate limit" in msg.lower() or "unavailable" in msg.lower():
                    sleep = _RETRY_429_SLEEP          # 限流：冷却 300s 后继续
                    continue                          # 换下个 key 再试
                if "401" in msg or "403" in msg or "unauthorized" in msg.lower() or "invalid" in msg.lower():
                    sleep = _RETRY_START_SLEEP         # 密钥失效：换下个 key
                    continue
                break                                  # 其它错误（5xx/网络等）：本 key 失败换下个 key
        _update_pending(username, item_id, status="running", error=last_err[:200],
                        last_heartbeat=time.strftime("%Y-%m-%d %H:%M:%S"))
        if time.time() - t0 > _RETRY_TOTAL_MAX:
            _update_pending(username, item_id, status="failed", progress=0,
                            error=f"重试超过 {_RETRY_TOTAL_MAX // 3600}h 仍未成功，最后错误: {last_err[:200]}",
                            completed_at=time.strftime("%Y-%m-%d %H:%M:%S"))
            return
        time.sleep(sleep)
        sleep = min(int(sleep * 1.6) + 3, _RETRY_CAP_SLEEP)   # 指数退避，封顶


def _start_worker(username: str, jobs: list) -> None:
    """jobs: [{item, payload}] —— 为每一张图启动独立后台线程。"""
    for j in jobs:
        threading.Thread(
            target=_worker_run,
            args=(j["payload"], _agnes_keys(), username, j["item"]["id"]),
            daemon=True,
        ).start()



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
def generate(payload: dict, request: Request):
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
    # 来源标识：内部通道（media.py / DAG 等技能产物）未显式传 source 时默认打短剧流程标，
    # 使创作台作品库只展示页面生成内容（画廊按 exclude_source 过滤该标）。
    src = str(p.get("source") or "")
    if not src and request.headers.get("X-Agnes-Internal-Token"):
        src = "short-drama-director"

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

    dedup_key = _dedup_key(base_payload, mode, count)
    if not p.get("allow_duplicate"):
        dups = _find_dedup(username, dedup_key)
        if dups:
            return {"ok": True, "count": len(dups), "items": dups, "async": True, "deduped": True}

    # 异步化：立即创建占位任务并启动后台线程（失败自动重试直到成功）
    jobs = []
    items = []
    for i in range(count):
        payload_dup = dict(base_payload)
        if count > 1:
            payload_dup["prompt"] = prompt.replace("#i", "").replace("{i}", "") or prompt
        item = _create_pending(mode, model, payload_dup["prompt"], size, ratio,
                               len(data_uris), username, dedup_key=dedup_key,
                               source=src)
        jobs.append({"item": item, "payload": payload_dup})
        items.append(item)
    _start_worker(username, jobs)

    return {"ok": True, "count": len(items), "items": items, "async": True}


@router.get("/task/{item_id}")
async def task(item_id: str, request: Request = None):
    """轮询图片生成任务状态（queued / running / completed / failed）。"""
    if not _EXT_RE.match(item_id):
        return JSONResponse({"error": "非法 id"}, status_code=400)
    username = getattr(getattr(request, "state", None), "username", None) or current_user()
    with _user_items_lock(username):
        item = next((it for it in _get_user_items(username) if it.get("id") == item_id), None)
    if not item:
        return JSONResponse({"error": "任务不存在"}, status_code=404)
    # 仅作品库存在、无任务记录的历史作品（旧同步入库）→ 视为已完成，避免前端/外部轮询空转
    if not item.get("status"):
        item = dict(item, status="completed")
    return {"ok": True, "item": item}


@router.get("/history")
async def history(limit: int = 60, offset: int = 0, exclude_source: str = "", request: Request = None):
    """返回当前用户的作品列表。exclude_source=逗号分隔的 source 列表（如短片流程的 short-drama-director），
    被排除的条目仍在作品库内（短剧审核页依赖其 /task /file 端点），只是不在创作台作品库展示。"""
    username = getattr(getattr(request, "state", None), "username", None) or current_user()
    # 打开作品库前先把 agnes-media 技能产物同步进来（幂等；输出目录通常为空，开销可忽略）
    try:
        _sync_skill_outputs(username)
    except Exception:
        pass
    limit = max(1, limit) if _MAX_GALLERY is None else max(1, min(limit, _MAX_GALLERY))
    offset = max(0, offset)
    excluded = {x for x in (exclude_source or "").split(",") if x}
    with _user_items_lock(username):
        items = _get_user_items(username)
    if excluded:
        items = [it for it in items if it.get("source") not in excluded]
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
    """删除作品：移除 manifest 记录 + 物理删除作品库副本 + 登记源文件墓碑（防止 skills 同步复活）"""
    username = getattr(getattr(request, "state", None), "username", None) or current_user()
    ids = set(str(x) for x in ((payload or {}).get("ids") or []) if str(x).strip())
    if not ids:
        return JSONResponse({"error": "ids 必填"}, status_code=400)
    with _user_items_lock(username):
        items = _get_user_items(username)
        before = len(items)
        removed = [it for it in items if it.get("id") in ids]
        items[:] = [it for it in items if it.get("id") not in ids]
        deleted = before - len(items)
    if removed:
        dead = _load_deleted_sources(username)
        changed = False
        for it in removed:
            src_name = it.get("src_name") or os.path.basename(it.get("file") or "")
            if src_name:
                dead.add((src_name, int(it.get("size_bytes") or 0), int(it.get("src_mtime") or 0)))
                changed = True
            # 物理删除作品库副本（源文件保留，但被墓碑挡住不再同步）
            for f in (it.get("file"), it.get("thumb")):
                if not f:
                    continue
                try:
                    fp = os.path.join(str(_LIB), os.path.basename(f))
                    if os.path.isfile(fp):
                        os.remove(fp)
                except Exception:
                    pass
        if changed:
            _save_deleted_sources(username, dead)
    _save_user_manifest(username)
    return {"ok": True, "deleted": deleted}


@router.post("/cancel")
async def cancel(payload: dict, request: Request = None):
    """取消任务：仅阻止后端继续自动重试（在途上游请求无法中断，可能仍产生一次扣费）。"""
    username = getattr(getattr(request, "state", None), "username", None) or current_user()
    ids = set(str(x) for x in ((payload or {}).get("ids") or []) if str(x).strip())
    if not ids:
        return JSONResponse({"error": "ids 必填"}, status_code=400)
    done = []
    with _user_items_lock(username):
        for it in _get_user_items(username):
            if it.get("id") not in ids:
                continue
            if it.get("status") in ("queued", "running"):
                it["status"] = "cancelled"
                it["error"] = "已取消（不再自动重试）"
                it["completed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                done.append(it.get("id"))
    _save_user_manifest(username)
    return {"ok": True, "cancelled": len(done), "ids": done}
