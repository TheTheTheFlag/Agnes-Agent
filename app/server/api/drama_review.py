"""app.server.api.drama_review — 短剧审核台 API。

在 8081 创作工作台「短剧审核」tab 上提供：
  · 资产审核：列出 assets/manifest.json 的角色/场景/道具（提示词 + 出图），
    可改提示词重新生成（media.py image --ref 锁形象），产物回写 manifest。
  · 分镜审核：逐集解析投喂稿，列出每段分镜提示词与已产出视频；
    可「直接重渲」或「修改提示词后重渲」单段（gen_kzqs_videos.py 单段跑），
    确认后合并成完整视频（ffmpeg concat，整集 + 连播）。

渲染链路约定（2026-09-29 反转，配音 = 视频自带音轨）：
  段视频默认保留模型自带音轨（不 -an）；配音随视频生成自带，角色音色与中文发音锁写进视频词
  （short-drama-agnes-sop `references/drama-pipeline-SOP.md` §9 / `references/render-contracts.md` §8）。
  _render_env 设 `KEEP_AUDIO=1` 为冗余保险（新驱动默认即保留，兼容旧驱动）。
  段缝采用「尾帧接力」：段 N 渲染时首帧=ffmpeg 抽段 N-1 视频真实末帧（resize 2K 覆盖 kf1.png），
  段 N-1 实际渲染末帧 100% 成为段 N 首帧，段缝像素级一致。
  E01-01 为全片唯一独立首帧（kf1.png 出图），每段 kf2.png 仍是 AI 出图（叙事落点，可改词重出）。
  级联重渲：POST /segments/cascade 自某段起串行重渲到片尾，跨集自动接力。
"""
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse

from app.userctx import current_user

router = APIRouter(prefix="/api/drama-review", tags=["drama-review"])

MEDIA = "/home/mirror/Agnes-Agent/app/skills_builtin/agnes-media/media.py"
GENERATOR = "/home/mirror/Agnes-Agent/tools_deliver/gen_kzqs_videos.py"
TOKEN_PATH = "/home/mirror/Agnes-Agent/data/internal_token"
AGNES_ROOT = "/home/mirror/Agnes-Agent"

_TASKS = {}
_TASKS_LOCK = threading.RLock()
_PROCS = {}          # tid -> Popen（用于用户取消时终止子进程）
_PROCS_LOCK = threading.RLock()


def _username(request) -> str:
    return getattr(getattr(request, "state", None), "username", None) or current_user() or "Mirror"


def _deliv_root() -> str:
    from app.config import DELIVERABLES_DIR
    return str(DELIVERABLES_DIR)


def _proj_dir(name: str) -> str:
    root = _deliv_root()
    base = os.path.normpath(os.path.join(root, "短剧", name))
    if not base.startswith(root):
        raise ValueError("非法路径")
    return base


def _token_env():
    env = dict(os.environ)
    try:
        tok = open(TOKEN_PATH, encoding="utf-8").read().strip()
        if tok:
            env["AGENT_INTERNAL_TOKEN"] = tok
    except Exception:
        pass
    # 审核页重渲也属于短剧流程产物：打标后创作台作品库隐藏、仅短剧审核可见
    env["AGENT_MEDIA_SOURCE"] = "short-drama-director"
    return env


def _render_env():
    """渲染子进程环境：token + KEEP_AUDIO=1（新口径默认保留音轨，此值冗余保险，兼容新旧驱动）。
    另加 PYTHONUNBUFFERED=1，确保子进程 print 行实时流入任务日志（否则管道缓冲导致进度滞后）。"""
    env = _token_env()
    env["KEEP_AUDIO"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """终止以 proc 为组长的整条进程组：SIGTERM → 最多等 3s → SIGKILL。"""
    try:
        pgid = os.getpgid(proc.pid)
    except (ProcessLookupError, OSError):
        pgid = None
    for sig, wait_after in ((signal.SIGTERM, 3), (signal.SIGKILL, 2)):
        if pgid is not None:
            try:
                os.killpg(pgid, sig)
            except (ProcessLookupError, OSError):
                break
        else:
            try:
                proc.send_signal(sig)
            except (ProcessLookupError, OSError):
                break
        try:
            proc.wait(timeout=wait_after)
            break
        except subprocess.TimeoutExpired:
            continue


def _run_proc(cmd, timeout: int, env, tid: str = None):
    """subprocess.run 的进程组版：超时先杀整条进程组再抛 TimeoutExpired。

    subprocess.run 超时只杀直接子进程，渲染脚本（gen_kzqs_videos.py）派生的
    media.py 孙进程会变孤儿继续跑，重试即产生重复 take。
    tid 给定时登记 Popen，/tasks/{tid}/cancel 可中途终止。
    """
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, env=env, start_new_session=True)
    if tid:
        with _PROCS_LOCK:
            _PROCS[tid] = proc
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_process_tree(proc)
        try:
            proc.communicate(timeout=5)
        except Exception:
            pass
        raise
    finally:
        if tid:
            with _PROCS_LOCK:
                _PROCS.pop(tid, None)
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def _run_proc_stream(cmd, timeout: int, env, on_line=None, tid: str = None):
    """_run_proc 的流式版：逐行回调 stdout/stderr（实时进度），超时仍按进程组杀掉。

    on_line 收到的是去掉行尾换行符的原始行；回调异常被吞掉，不影响子进程。
    tid 给定时登记 Popen，/tasks/{tid}/cancel 可中途终止。
    """
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, env=env, start_new_session=True, bufsize=1)
    if tid:
        with _PROCS_LOCK:
            _PROCS[tid] = proc
    out_buf, err_buf = [], []

    def _reader(stream, buf):
        try:
            for line in iter(stream.readline, ""):
                if not line:
                    break
                buf.append(line)
                if on_line:
                    try:
                        on_line(line.rstrip("\r\n"))
                    except Exception:
                        pass
        except Exception:
            pass
        finally:
            try:
                stream.close()
            except Exception:
                pass

    ths = [threading.Thread(target=_reader, args=(proc.stdout, out_buf), daemon=True),
           threading.Thread(target=_reader, args=(proc.stderr, err_buf), daemon=True)]
    for th in ths:
        th.start()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_process_tree(proc)
        for th in ths:
            th.join(timeout=5)
        raise subprocess.TimeoutExpired(cmd, timeout, output="".join(out_buf),
                                        stderr="".join(err_buf))
    finally:
        if tid:
            with _PROCS_LOCK:
                _PROCS.pop(tid, None)
    for th in ths:
        th.join(timeout=10)
    return subprocess.CompletedProcess(cmd, proc.returncode, "".join(out_buf), "".join(err_buf))


def _kill_task_proc(tid: str) -> bool:
    """终止任务当前关联的子进程（进程组），无论其是否仍在运行。"""
    with _PROCS_LOCK:
        proc = _PROCS.pop(tid, None)
    if proc is None:
        return False
    try:
        _kill_process_tree(proc)
    except Exception:
        pass
    return True


# ==================== 任务登记（前端轮询） ====================
def _new_task(kind: str, meta: dict, jobs: int = 0) -> str:
    """登记任务。jobs>0 时前端可按 done/jobs 显示进度条。"""
    tid = uuid.uuid4().hex[:12]
    with _TASKS_LOCK:
        _TASKS[tid] = {"id": tid, "kind": kind, "meta": meta, "status": "queued",
                       "jobs": int(jobs or 0), "done": 0,
                       "log": [], "created": time.time(), "updated": time.time()}
    return tid


def _touch(tid: str, status=None, line=None, inc: int = 0):
    with _TASKS_LOCK:
        t = _TASKS.get(tid)
        if not t:
            return
        if line:
            t["log"].append(line)
            if len(t["log"]) > 500:
                t["log"] = t["log"][-500:]
        if inc:
            t["done"] = min(int(t.get("done", 0)) + int(inc),
                            t.get("jobs") or int(t.get("done", 0)) + int(inc))
        if status:
            cur = t.get("status")
            if cur == "cancelled" and status in ("done", "failed"):
                status = None          # 已被用户取消，终态优先，不降级
        if status:
            t["status"] = status
        t["updated"] = time.time()


def _prune_tasks(max_keep: int = 80):
    with _TASKS_LOCK:
        if len(_TASKS) <= max_keep * 2:
            return
        for k in sorted(_TASKS, key=lambda x: _TASKS[x]["updated"], reverse=True)[max_keep:]:
            _TASKS.pop(k, None)


def _now() -> str:
    return time.strftime("%H:%M:%S")


# ==================== 项目 / 资产 ====================
@router.get("/projects")
async def list_projects(request: Request):
    root = os.path.join(_deliv_root(), "短剧")
    out = []
    if os.path.isdir(root):
        for n in sorted(os.listdir(root)):
            p = os.path.join(root, n)
            if not os.path.isdir(p) or n.startswith("."):
                continue
            has_assets = os.path.isfile(os.path.join(p, "assets", "manifest.json"))
            eps = []
            feed = os.path.join(p, "投喂稿")
            if os.path.isdir(feed):
                eps = sorted(d for d in os.listdir(feed)
                             if os.path.isfile(os.path.join(feed, d, f"投喂稿-{d}.md")))
            out.append({"name": n, "has_assets": has_assets, "episodes": eps})
    return {"projects": out}


# ==================== 剧本审核 ====================
#
# 剧本来源优先级：
#   1) 项目根 `剧本/` 目录下的全部 .md（如「一一的两岁」）
#   2) 项目根 `小说-*.md` / `*剧本*.md` / `screenplay-*.md`
#   3) 无则视为「未提供剧本」，前端给出提示（该剧中剧本通常以投喂稿承载）
def _script_docs(pdir: str):
    root = _deliv_root()
    docs = []
    def _push(p):
        if os.path.isfile(p) and p.startswith(root):
            rel = os.path.relpath(p, root).replace("\\", "/")
            try:
                size = os.path.getsize(p)
            except OSError:
                size = 0
            docs.append({"name": os.path.basename(p), "path": rel, "size": size})
    feed = os.path.join(pdir, "剧本")
    if os.path.isdir(feed):
        for f in sorted(os.listdir(feed)):
            if f.lower().endswith(".md"):
                _push(os.path.join(feed, f))
        if docs:
            return docs
    for f in sorted(os.listdir(pdir)):
        low = f.lower()
        if not low.endswith(".md"):
            continue
        if ("剧本" in f) or f.startswith("小说-") or f.startswith("screenplay") or f.startswith("script"):
            _push(os.path.join(pdir, f))
    return docs


@router.get("/script")
async def drama_script(name: str = "", request: Request = None):
    """列出项目的剧本文档（供剧本审核 tab 展示）。"""
    pdir = _proj_dir(name)
    return {"name": name, "docs": _script_docs(pdir)}


@router.get("/script/read")
async def drama_script_read(name: str = "", path: str = "", request: Request = None):
    """读取某个剧本文档的纯文本（按交付物根定位，防路径穿越）。"""
    root = _deliv_root()
    rel = (path or "").strip("/\\")
    if not rel:
        return JSONResponse({"error": "path 必填"}, status_code=400)
    p = os.path.normpath(os.path.join(root, rel))
    if not p.startswith(root):
        return JSONResponse({"error": "非法路径"}, status_code=400)
    if not os.path.isfile(p):
        return JSONResponse({"error": "文件不存在"}, status_code=404)
    try:
        content = open(p, encoding="utf-8", errors="replace").read(600000)
    except Exception as e:
        return JSONResponse({"error": f"读取失败: {e}"}, status_code=500)
    return {"name": rel, "content": content}


# ==================== 资产 manifest 结构容错 ====================
# 历史 manifest 为 {assets:{characters,scenes,props}} 嵌套（条目含 views[].file），
# 新契约为顶层扁平列表（§2c·补）。审核台必须两种都能读、都能回写，
# 且回写不得改变原结构形状（legacy 继续 legacy）。
_TYPE_ALIAS = {"characters": "character", "character": "character",
               "scenes": "scene", "scene": "scene",
               "interior": "scene", "exterior": "scene", "location": "scene",
               "props": "prop", "prop": "prop", "item": "prop",
               "monsters": "monster", "monster": "monster"}

_VALID_TYPES = ("character", "scene", "prop", "monster")


def _normalize_asset_type(t, cat=None):
    """type 越界时回落分类键（单数化），最终缺省 prop。"""
    t = _TYPE_ALIAS.get(str(t or "").strip(), str(t or "").strip())
    if t in _VALID_TYPES:
        return t
    return _TYPE_ALIAS.get(str(cat or "").strip(), "prop")


def _asset_items(data):
    """→ (pairs: [(cat|None, item)], shape)，shape ∈ flat|wrapped|categorized|nested|unknown。

    item 为原结构内对象的引用，就地改即写回原形状；
    nested（顶层为分类字典、值为字典/字符串的 dict-of-dicts，如「异能纪元」）由特征图
    生成流程产生，只保证可读展示，回写前由 assets_regenerate 统一扁平成新契约。
    """
    if isinstance(data, list):
        return [(None, x) for x in data if isinstance(x, dict)], "flat"
    if isinstance(data, dict):
        v = data.get("items")
        if isinstance(v, list):
            return [(None, x) for x in v if isinstance(x, dict)], "wrapped"
        v = data.get("assets")
        if isinstance(v, list):
            return [(None, x) for x in v if isinstance(x, dict)], "flat"
        if isinstance(v, dict):
            out = []
            for cat, lst in v.items():
                if isinstance(lst, list):
                    out += [(cat, x) for x in lst if isinstance(x, dict)]
            return out, "categorized"
        # dict-of-dicts：顶层分类键（characters/scenes/props/monsters…）的值为字典。
        #   characters: {名字:{views:[…], files:[paths]}} → 每视角一个条目；
        #   scenes/props/monsters: {id: relpath} → 每 id 一个条目。
        out = []
        for key, dv in data.items():
            ctype = _TYPE_ALIAS.get(str(key).strip())
            if ctype not in _VALID_TYPES or not isinstance(dv, dict):
                continue
            if key in ("characters", "character"):
                for name, val in dv.items():
                    if not isinstance(val, dict):
                        continue
                    for f in val.get("files") or []:
                        if not isinstance(f, str) or not f.strip():
                            continue
                        rel = f.strip().replace("\\", "/").lstrip("/")
                        out.append((key, {"id": os.path.splitext(os.path.basename(rel))[0],
                                          "name": name, "type": ctype, "ratio": "16:9",
                                          "status": "ready", "views": [{"file": rel}]}))
            else:
                for aid, f in dv.items():
                    rel = f if isinstance(f, str) else (f.get("path") or f.get("file"))
                    if not isinstance(rel, str) or not rel.strip():
                        continue
                    rel = rel.strip().replace("\\", "/").lstrip("/")
                    out.append((key, {"id": aid, "name": aid, "type": ctype, "ratio": "16:9",
                                      "status": "ready", "views": [{"file": rel}]}))
        return out, "nested" if out else "unknown"
    return [], "unknown"


def _flatten_nested(data):
    """把 dict-of-dicts 特征图 manifest 扁平成新契约（顶层数组）——首张资产被重新生成时原地自愈。"""
    out = []
    for key, dv in (data.items() if isinstance(data, dict) else []):
        ctype = _TYPE_ALIAS.get(str(key).strip())
        if ctype not in _VALID_TYPES or not isinstance(dv, dict):
            continue
        if key in ("characters", "character"):
            for name, val in dv.items():
                if not isinstance(val, dict):
                    continue
                for f in val.get("files") or []:
                    rel = f if isinstance(f, str) else ""
                    if not rel.strip():
                        continue
                    rel = rel.strip().replace("\\", "/").lstrip("/")
                    out.append({"id": os.path.splitext(os.path.basename(rel))[0], "type": ctype,
                                "name": name, "prompt": "", "ratio": "16:9", "status": "ready",
                                "path": "assets/" + rel})
        else:
            for aid, f in dv.items():
                rel = f if isinstance(f, str) else (f.get("path") or f.get("file"))
                if not isinstance(rel, str) or not rel.strip():
                    continue
                rel = rel.strip().replace("\\", "/").lstrip("/")
                out.append({"id": aid, "type": ctype, "name": aid, "prompt": "", "ratio": "16:9",
                            "status": "ready", "path": "assets/" + rel})
    return out


def _asset_raw_ref(data, aid):
    """在原始结构里就地定位 id/code == aid 的条目（回写它 = 不改结构形状）。"""
    for _cat, it in _asset_items(data)[0]:
        if (it.get("id") or it.get("code")) == aid:
            return it
    return None


def _asset_abs_path(it, pdir):
    """把条目里各种历史形式的图片引用解析成实际存在的绝对路径（找不到 → ""）。

    候选来源：path 字段 + views[].file/image/path + 顶层 file；
    候选基准：原值（绝对直用）→ 项目根 → DELIVERABLES 根 → Agnes 根。
    """
    vals = []
    for k in ("path", "file"):
        v = it.get(k)
        if isinstance(v, str) and v.strip():
            vals.append(v.strip())
    for v in it.get("views") or []:
        if isinstance(v, dict):
            for k in ("file", "image", "path"):
                x = v.get(k)
                if isinstance(x, str) and x.strip():
                    vals.append(x.strip())
    cands = []
    for v in vals:
        v = v.replace("\\", "/")
        if os.path.isabs(v):
            cands.append(v)
        else:
            cands.append(os.path.join(pdir, v))
            # dict-of-dicts 特征图 manifest 的路径相对 assets/（如 "characters/…/xxx.png"）
            cands.append(os.path.join(pdir, "assets", v))
            cands.append(os.path.join(_deliv_root(), v))
            cands.append(os.path.join(AGNES_ROOT, v))
    for c in dict.fromkeys(cands):
        c = os.path.normpath(c)
        if os.path.isfile(c):
            return c
    return ""


def _dump_manifest(mp, obj):
    """原子写回：先写 .tmp 再 os.replace，避免审核台读到半截 JSON。"""
    tmp = mp + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, mp)


@router.get("/assets")
async def list_assets(name: str = "", request: Request = None):
    pdir = _proj_dir(name)
    mp = os.path.join(pdir, "assets", "manifest.json")
    if not os.path.isfile(mp):
        return {"assets": [], "name": name}
    try:
        data = json.load(open(mp, encoding="utf-8"))
    except Exception as e:
        return JSONResponse({"error": f"manifest 解析失败: {e}"}, status_code=500)
    pairs, _shape = _asset_items(data)
    root = _deliv_root()
    out = []
    for cat, it in pairs:
        abs_path = _asset_abs_path(it, pdir)
        rel = os.path.relpath(abs_path, root).replace("\\", "/") if abs_path.startswith(root) else ""
        st = str(it.get("status") or it.get("asset_status") or "")
        if st == "completed":
            st = "ready"
        # ready 但图解析不到 → 降为 draft（不为已消失的图显示「就绪」，与 qa_manifest 口径一致）
        if st in ("", "ready"):
            st = "ready" if abs_path else "draft"
        out.append({
            "id": it.get("id") or it.get("code"),
            "type": _normalize_asset_type(it.get("type"), cat),
            "slug": it.get("slug", ""),
            "prompt": it.get("prompt") or it.get("prompt_zh") or "",
            "ratio": it.get("ratio", "16:9"),
            "status": st, "path": rel,
            "file": os.path.basename(abs_path) if abs_path else "",
            "md5": it.get("md5", ""),
        })
    return {"assets": out, "name": name}


# ==================== 资产自定义上传 / 替换 / 新增 / 移除 ====================
_ASSET_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
_ASSET_MAX = 50 * 1024 * 1024  # 50MB


def _save_asset_bytes(content: bytes, dst: str) -> None:
    """写上传字节到目标文件（原子写）。"""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".upload.tmp"
    with open(tmp, "wb") as f:
        f.write(content)
    os.replace(tmp, dst)


def _asset_slug(it, aid: str) -> str:
    """从条目或 id 推导落盘文件基名（保留中文/字母数字，剔除路径危险字符）。"""
    s = str(it.get("slug") or it.get("name") or aid or "asset")
    return re.sub(r"[^\w\u4e00-\u9fff\-]+", "-", s).strip("-") or "asset"


def _touch_asset_img_fields(it, rel: str, dst: str) -> None:
    """回写条目的图片引用字段（同时覆盖新老契约形状）。"""
    it["path"] = rel
    it["status"] = "ready"
    it["md5"] = hashlib.md5(open(dst, "rb").read()).hexdigest()
    it["size_kb"] = int(os.path.getsize(dst) // 1024)
    if "file" in it:
        it["file"] = os.path.basename(dst)
    if "views" in it and isinstance(it["views"], list):
        it["views"] = [{"file": rel}]


@router.post("/assets/upload")
async def assets_upload(name: str = Form(...), id: str = Form(...),
                        file: UploadFile = File(...), request: Request = None):
    """自定义上传替换某资产图片：就地覆盖该资产当前引用文件的字节（路径保持不变）。"""
    pdir = _proj_dir(name)
    mp = os.path.join(pdir, "assets", "manifest.json")
    if not os.path.isfile(mp):
        return JSONResponse({"error": "manifest.json 不存在"}, status_code=404)
    data = json.load(open(mp, encoding="utf-8"))
    _pairs, shape = _asset_items(data)
    if shape == "nested":
        data = _flatten_nested(data)
    it = _asset_raw_ref(data, id)
    if it is None:
        return JSONResponse({"error": f"资产 {id} 不存在"}, status_code=404)

    content = await file.read()
    if not content:
        return JSONResponse({"error": "文件为空"}, status_code=400)
    if len(content) > _ASSET_MAX:
        return JSONResponse({"error": f"文件超过大小限制（{_ASSET_MAX // 1024 // 1024}MB）"}, status_code=400)
    ext = os.path.splitext((file.filename or "").lower())[1]
    if ext not in _ASSET_EXT:
        return JSONResponse({"error": f"不支持的图片类型: {ext or '(无扩展名)'}"}, status_code=400)

    dst = _asset_abs_path(it, pdir)
    if not dst:
        dst = os.path.join(pdir, "assets", "images", _asset_slug(it, id) + ext)
    else:
        # 原地覆盖：保留原文件路径/扩展名，manifest 引用不变，缩略图按 mtime 自动失效
        pass
    _save_asset_bytes(content, dst)
    rel = os.path.relpath(dst, pdir).replace("\\", "/")
    _touch_asset_img_fields(it, "assets/" + rel if not rel.startswith("assets/") else rel, dst)
    _dump_manifest(mp, data)
    return {"ok": True, "id": id, "path": rel, "md5": it["md5"]}


@router.post("/assets/add")
async def assets_add(name: str = Form(...), type: str = Form(...), asset_name: str = Form(...),
                     file: UploadFile = File(...), request: Request = None):
    """新增自定义资产：写入 assets/images/ 并追加 manifest 条目（各种形状均就地兼容）。"""
    atype = _normalize_asset_type(type)
    if atype not in _VALID_TYPES:
        return JSONResponse({"error": f"非法的资产类型: {type}"}, status_code=400)
    aname = (asset_name or "").strip()
    if not aname:
        return JSONResponse({"error": "资产名必填"}, status_code=400)

    pdir = _proj_dir(name)
    mp = os.path.join(pdir, "assets", "manifest.json")
    data = []
    shape = "flat"
    if os.path.isfile(mp):
        data = json.load(open(mp, encoding="utf-8"))
        _pairs, shape = _asset_items(data)
        if shape == "nested":
            data = _flatten_nested(data)
            shape = "flat"
        elif shape == "categorized":
            # categorized 顶层 {assets:{characters:[...]}}: 直接把内层都换成列表即可挂载
            inner = data.get("assets")
            if isinstance(inner, dict):
                data = inner
                for k, v in list(data.items()):
                    if isinstance(v, dict) and not isinstance(v, list):
                        data[k] = list(v.values())
                shape = "categorized"

    content = await file.read()
    if not content:
        return JSONResponse({"error": "文件为空"}, status_code=400)
    if len(content) > _ASSET_MAX:
        return JSONResponse({"error": f"文件超过大小限制（{_ASSET_MAX // 1024 // 1024}MB）"}, status_code=400)
    ext = os.path.splitext((file.filename or "").lower())[1]
    if ext not in _ASSET_EXT:
        return JSONResponse({"error": f"不支持的图片类型: {ext or '(无扩展名)'}"}, status_code=400)

    slug = _asset_slug({}, aname)
    dst = os.path.join(pdir, "assets", "images", slug + ext)
    _save_asset_bytes(content, dst)
    rel = "assets/images/" + slug + ext
    it = {"id": slug, "type": atype, "name": aname, "slug": slug, "prompt": "",
          "ratio": "16:9", "status": "ready", "path": rel,
          "md5": hashlib.md5(open(dst, "rb").read()).hexdigest(),
          "size_kb": int(os.path.getsize(dst) // 1024)}

    if shape == "flat" and isinstance(data, list):
        data.append(it)
    elif shape == "wrapped" and isinstance(data, dict) and isinstance(data.get("items"), list):
        data["items"].append(it)
    elif shape == "categorized" and isinstance(data, dict):
        cat_key = {"character": "characters", "scene": "scenes",
                   "prop": "props", "monster": "monsters"}.get(atype, "props")
        bucket = data.setdefault(cat_key, [])
        if isinstance(bucket, dict):
            bucket = data[cat_key] = list(bucket.values())
        bucket.append(it)
    elif shape == "flat" and isinstance(data, dict):
        data["items"] = data.pop("items", [it]) if isinstance(data.get("items"), list) else data.setdefault("items", [it])
    else:
        data = [it]
    _dump_manifest(mp, data)
    return {"ok": True, "id": slug, "type": atype, "path": rel}


@router.post("/assets/delete")
def assets_delete(payload: dict, request: Request):
    """从 manifest 移除资产条目（软删除：不删原始图文件，便于回滚）。"""
    name = str((payload or {}).get("name") or "")
    aid = str((payload or {}).get("id") or "")
    if not name or not aid:
        return JSONResponse({"error": "name/id 必填"}, status_code=400)
    pdir = _proj_dir(name)
    mp = os.path.join(pdir, "assets", "manifest.json")
    if not os.path.isfile(mp):
        return JSONResponse({"error": "manifest.json 不存在"}, status_code=404)
    data = json.load(open(mp, encoding="utf-8"))
    _pairs, shape = _asset_items(data)
    if shape in ("nested", "categorized"):
        # 这两种形状先归一化成顶层列表再删，避免形状里定位麻烦
        if shape == "nested":
            data = _flatten_nested(data)
        elif isinstance(data, dict):
            inner = data.get("assets")
            data = inner if isinstance(inner, dict) else data
            for k, v in list(data.items()):
                if isinstance(v, dict) and not isinstance(v, list):
                    data[k] = list(v.values())
    removed = False
    if isinstance(data, list):
        new = [x for x in data if isinstance(x, dict) and (x.get("id") or x.get("code")) != aid]
        removed = len(new) != len(data)
        data = new
    elif isinstance(data, dict) and isinstance(data.get("items"), list):
        items = data["items"]
        new = [x for x in items if isinstance(x, dict) and (x.get("id") or x.get("code")) != aid]
        removed = len(new) != len(items)
        data["items"] = new
    if not removed:
        return JSONResponse({"error": f"资产 {aid} 不存在"}, status_code=404)
    _dump_manifest(mp, data)
    return {"ok": True, "id": aid}


@router.post("/assets/regenerate")
def assets_regenerate(payload: dict, request: Request):
    name = str((payload or {}).get("name") or "")
    aid = str((payload or {}).get("id") or "")
    prompt = str((payload or {}).get("prompt") or "").strip()
    if not name or not aid or not prompt:
        return JSONResponse({"error": "name/id/prompt 必填"}, status_code=400)
    pdir = _proj_dir(name)
    mp = os.path.join(pdir, "assets", "manifest.json")
    data = []
    if os.path.isfile(mp):
        data = json.load(open(mp, encoding="utf-8"))
    _pairs, shape = _asset_items(data)
    if shape == "nested":
        # 特征图 dict-of-dicts manifest：先在内存扁平成新契约，回写即原地自愈为规范格式。
        data = _flatten_nested(data)
    it = _asset_raw_ref(data, aid)
    if it is None:
        return JSONResponse({"error": f"资产 {aid} 不存在"}, status_code=404)

    tid = _new_task("asset", {"name": name, "id": aid}, jobs=1)
    _prune_tasks()

    def _run():
        _touch(tid, "running", line=f"[{_now()}] 资产 {aid} 重新生成…")
        try:
            it["prompt"] = prompt
            it["status"] = "regenerating"
            _dump_manifest(mp, data)

            cmd = ["python3", MEDIA, "image", "--prompt", prompt,
                   "--ratio", it.get("ratio") or "16:9", "--size", "2K",
                   "--timeout", "3600"]
            ref = _asset_abs_path(it, pdir)
            if ref:
                cmd += ["--ref", ref]
            _touch(tid, line=f"[{_now()}] media.py image 启动")
            pr = _run_proc(cmd, 7200, _token_env(), tid=tid)
            out = (pr.stdout or "") + (pr.stderr or "")
            fm = re.search(r"FILE=(\S+)", out)
            if pr.returncode == 0 and fm and os.path.isfile(fm.group(1).strip()):
                src = fm.group(1).strip()
                ext = os.path.splitext(src)[1] or ".png"
                slug = it.get("slug") or aid
                dst = os.path.join(pdir, "assets", "images", f"{slug}{ext}")
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy(src, dst)
                it["path"] = f"assets/images/{slug}{ext}"
                it["status"] = "ready"
                it["md5"] = hashlib.md5(open(dst, "rb").read()).hexdigest()
                it["size_kb"] = int(os.path.getsize(dst) // 1024)
                _dump_manifest(mp, data)
                _touch(tid, "done", line=f"[{_now()}] OK -> {dst}", inc=1)
            else:
                tail = out[-500:]
                _touch(tid, "failed", line=f"[{_now()}] 生成失败: {tail}")
        except Exception as e:
            _touch(tid, "failed", line=f"[{_now()}] 异常: {e}")

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "task_id": tid, "asset_id": aid}


# ==================== 分镜（解析投喂稿 → manifest 同源 prompt） ====================
def _extract_field(body: str, name: str) -> str:
    m = re.search(r"【%s】([^\n]*(?:\n[^\n]*)*?)(?=\n【|\n##|\Z)" % re.escape(name), body)
    return m.group(1).strip() if m else ""


def _build_seg_prompt(body: str) -> str:
    style = _extract_field(body, "画幅风格")
    staging = _extract_field(body, "站位声明")
    timeline = _extract_field(body, "时间轴分镜")
    sfx = _extract_field(body, "音效")

    tl_clean = re.sub(r"\[镜头\d+\]\s*", "", timeline)
    tl_clean = re.sub(r"\n", " ", tl_clean).strip()
    staging_clean = re.sub(r"\n", " ", staging).strip()

    parts = []
    if style:
        parts.append(style)
    if staging_clean:
        parts.append(f"站位：{staging_clean}")
    if tl_clean:
        parts.append(tl_clean)
    if sfx:
        parts.append("声音：" + re.sub(r"\n", " ", sfx).strip())
    prompt = "，".join(parts)
    prompt = prompt.replace("@", "").replace("【", "").replace("】", "")
    prompt = re.sub(r"[{}<>]|接续状态|末态继承|拼接节点", "", prompt)
    prompt = re.sub(r"\s+", " ", prompt).strip()
    return prompt


# ==================== 段级生成参考图（refs + kf2，≤5，可上传/替换/移除） ====================
def _plan_files(pdir: str):
    """kf_plan.json + kf_plan_epNN.json（跳过 .bak 备份与目录）。"""
    d = os.path.join(pdir, "投喂稿")
    out = []
    try:
        names = os.listdir(d)
    except OSError:
        return out
    for f in sorted(names):
        if f.startswith("kf_plan") and f.endswith(".json") and ".bak" not in f:
            out.append(os.path.join(d, f))
    return out


def _plan_segs(path: str):
    try:
        raw = json.load(open(path, encoding="utf-8"))
    except Exception:
        return []
    eps = raw if isinstance(raw, list) else [raw]
    segs = []
    for ep in eps:
        if isinstance(ep, dict):
            segs += [s for s in (ep.get("segments") or []) if isinstance(s, dict)]
    return segs


def _find_plan_seg(pdir: str, seg: str):
    """定位 seg 所在的 kf_plan 文件与其条目。返回 (plan_path, item) 或 (None, None)。"""
    for kp in _plan_files(pdir):
        for s in _plan_segs(kp):
            if s.get("seg") == seg:
                return kp, s
    return None, None


def _load_plan_seg(pdir: str, seg: str):
    """加载 seg 所在 kf_plan 的**根对象**与条目：返回 (plan_path, root, item)。

    修改 item 后必须 `_dump_json(plan_path, root)` 才能落盘（item 是 root 的子节点，
    直接对 item 改再重新 json.load 写回会丢弃改动）。
    """
    for kp in _plan_files(pdir):
        try:
            raw = json.load(open(kp, encoding="utf-8"))
        except Exception:
            continue
        eps = raw if isinstance(raw, list) else [raw]
        for ep in eps:
            if not isinstance(ep, dict):
                continue
            for s in (ep.get("segments") or []):
                if isinstance(s, dict) and s.get("seg") == seg:
                    return kp, raw, s
    return None, None, None


def _ref_abs(kp: str, r) -> str:
    """ref 可能绝对也可能相对（相对 kf_plan 所在目录的上级）。统一成绝对路径。"""
    r = str(r or "").strip()
    if not r:
        return ""
    if os.path.isabs(r):
        return r
    return os.path.normpath(os.path.join(os.path.dirname(kp), "..", r))


def _frame_label(p: str) -> str:
    b = os.path.splitext(os.path.basename(p))[0]
    b = re.sub(r"^ref[-_]", "", b)
    return (b.replace("_", " ").strip() or "参考图")


def _frame_role(p: str) -> str:
    d = p.replace("\\", "/").lower()
    bn = os.path.basename(p).lower()
    if bn == "kf2.png":
        return "本段分镜构图"
    if "/kf_refs/" in d or bn.startswith("ref-"):
        return "人物形象基准"
    if re.search(r"scn|scene|场景", bn):
        return "场景美术基准"
    if re.search(r"prop|道具", bn):
        return "道具基准"
    return "画面参考"


def _seg_frames(pdir: str, kp, ep: str, seg: str):
    """该段绘制生成所用参考图：kf_plan.refs + kf2 构图图（≤5，逐段个性化编排）。"""
    root = _deliv_root()
    items = []
    refs = [(i, _ref_abs(kp, r)) for i, r in enumerate((kp or {}).get("refs") or [])
            if os.path.isfile(_ref_abs(kp, r))]
    roles = {str((x or {}).get("path")): x for x in ((kp or {}).get("ref_roles") or [])
             if isinstance(x, dict)}
    for i, ap in refs:
        if len(items) >= 5:
            break
        meta = roles.get(str(ap)) or {}
        items.append({"idx": len(items), "kind": "ref", "path": os.path.relpath(ap, root).replace("\\", "/"),
                      "label": _frame_label(ap), "role": str(meta.get("use") or _frame_role(ap)),
                      "controls": str(meta.get("controls") or ""), "not_controls": str(meta.get("not_controls") or "")})
    kf2 = os.path.join(pdir, "投喂稿", ep, seg, "kf2.png")
    if os.path.isfile(kf2) and os.path.getsize(kf2) > 0 and len(items) < 5:
        items.append({"idx": len(items), "kind": "kf2",
                      "path": os.path.relpath(kf2, root).replace("\\", "/"),
                      "label": "kf2 构图图", "role": "本段分镜构图",
                      "controls": "景别、机位、人物站位、构图关系",
                      "not_controls": "人物长相、服装细节",
                      "kf_prompt": str((kp or {}).get("kf2_prompt") or "")})
    return items


def _dump_json(p: str, obj) -> None:
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


@router.get("/storyboard")
async def storyboard(name: str = "", ep: str = "", request: Request = None):
    pdir = _proj_dir(name)
    md = os.path.join(pdir, "投喂稿", ep, f"投喂稿-{ep}.md")
    if not os.path.isfile(md):
        return JSONResponse({"error": f"投喂稿不存在: {ep}"}, status_code=404)
    txt = open(md, encoding="utf-8").read()
    root = _deliv_root()
    kfplan = {s.get("seg"): s for s in _kfplan_flat(pdir)}
    segs = []
    blocks = re.split(r"\n(?=## )", txt)
    for blk in blocks:
        if not re.match(r"## " + re.escape(ep) + r"-\d+", blk):
            continue
        head = blk.split("\n", 1)[0]
        mh = re.match(r"## (" + re.escape(ep) + r"-\d+)(?: · (.*?))?(?:（--duration (\d+)）)?", head)
        if not mh:
            continue
        seg = mh.group(1)
        title = (mh.group(2) or "").strip()
        dur = int(mh.group(3)) if mh.group(3) else 10
        body = blk.split("\n", 1)[1] if "\n" in blk else ""
        prompt = _build_seg_prompt(body)

        video = os.path.join(pdir, seg, seg + ".mp4")
        has_video = os.path.isfile(video)
        vrel = ""
        if has_video and video.startswith(root):
            vrel = os.path.relpath(video, root).replace("\\", "/")
        kf_dir = os.path.join(pdir, "投喂稿", ep, seg)
        kfs = [f"{kf_dir}/kf{i}.png" for i in (1, 2, 3)
               if os.path.exists(f"{kf_dir}/kf{i}.png")]
        vmtime = 0
        if has_video:
            try:
                vmtime = int(os.path.getmtime(video))
            except Exception:
                vmtime = 0
        kp = kfplan.get(seg) or {}
        segs.append({
            "seg": seg, "title": title, "duration": dur, "prompt": prompt,
            "has_video": has_video, "video": vrel,
            "kfs": [os.path.relpath(k, root).replace("\\", "/") for k in kfs],
            "vmtime": vmtime,
            # 审核模式：kf 出图词 + 来源标记（relay=尾帧接力自上游视频末帧，不可独立重出）
            "kf1_prompt": str(kp.get("kf1_prompt") or ""),
            "kf2_prompt": str(kp.get("kf2_prompt") or ""),
            "kf1_origin": "gen" if not kp.get("kf1_from") else "relay",
            "frames": _seg_frames(pdir, kp, ep, seg),
        })
    return {"name": name, "ep": ep, "segments": segs}


@router.post("/storyboard/frames/upload")
async def storyboard_frames_upload(name: str = Form(...), seg: str = Form(...),
                                   replace: str = Form(""),
                                   file: UploadFile = File(...), request: Request = None):
    """上传/替换某段的生成参考图（立即参与下次渲影）。

    replace="" → 追加新参考图（refs+kf2 总数 <5 才允许，写入本段专用 ref{N}.png）；
    replace=N  → 替换第 N 个槽位：
        · ref 槽 → 生成该段专用副本 投喂稿/{ep}/{seg}/ref{N}.png 并改写本段 refs[N]，
          不影响其他段引用的同一人物/场景基准图；
        · kf2 槽 → 直接覆盖该段 kf2.png（本就是段级文件）。
    """
    pdir = _proj_dir(name)
    kp, root, item = _load_plan_seg(pdir, seg)
    if item is None:
        return JSONResponse({"error": f"kf_plan 中无 {seg}"}, status_code=404)
    ep = seg[:3]

    content = await file.read()
    if not content:
        return JSONResponse({"error": "文件为空"}, status_code=400)
    if len(content) > _ASSET_MAX:
        return JSONResponse({"error": f"文件超过大小限制（{_ASSET_MAX // 1024 // 1024}MB）"}, status_code=400)
    ext = os.path.splitext((file.filename or "").lower())[1]
    if ext not in _ASSET_EXT:
        return JSONResponse({"error": f"不支持的图片类型: {ext or '(无扩展名)'}"}, status_code=400)

    refs = [(i, _ref_abs(kp, r)) for i, r in enumerate(item.get("refs") or [])
            if os.path.isfile(_ref_abs(kp, r))]
    segdir = os.path.join(pdir, "投喂稿", ep, seg)
    kf2_exists = os.path.isfile(os.path.join(segdir, "kf2.png"))

    if not replace.strip():
        # ---- 新增 ----
        if len(refs) + (1 if kf2_exists else 0) >= 5:
            return JSONResponse({"error": "该段参考图已达 5 张上限，请先替换或移除"}, status_code=400)
        n = len(refs)
        dst = os.path.join(segdir, f"ref{n}{ext}")
        _save_asset_bytes(content, dst)
        refs_list = item.setdefault("refs", [])
        refs_list.append(dst)
        roles = item.setdefault("ref_roles", [])
        if not isinstance(roles, list):
            roles = item["ref_roles"] = []
        roles.append({"idx": n + 1, "path": dst, "use": "自定义参考图",
                      "controls": "", "not_controls": ""})
        _dump_json(kp, root)
        return {"ok": True, "seg": seg, "slot": n, "frames": _seg_frames(pdir, item, ep, seg)}

    try:
        slot = int(replace)
    except ValueError:
        return JSONResponse({"error": f"replace 参数非法: {replace}"}, status_code=400)

    if slot < len(refs):
        # ---- 替换 ref 槽 ----
        i_old, ap_old = refs[slot]
        local = os.path.normpath(ap_old).startswith(os.path.normpath(segdir))
        cur_ext = os.path.splitext(ap_old)[1].lower()
        if local and cur_ext == ext:
            dst = ap_old  # 已是该段专用文件且扩展名一致：原地覆盖
        else:
            dst = os.path.join(segdir, f"ref{slot}{ext}")
        _save_asset_bytes(content, dst)
        if dst != ap_old:
            item["refs"][i_old] = dst
            roles = item.get("ref_roles")
            if isinstance(roles, list):
                for r in roles:
                    if str((r or {}).get("path", "")) == str(ap_old):
                        r["path"] = dst
                        break
                else:
                    roles.append({"idx": slot + 1, "path": dst, "use": "",
                                  "controls": "", "not_controls": ""})
            _dump_json(kp, root)
        return {"ok": True, "seg": seg, "slot": slot, "frames": _seg_frames(pdir, item, ep, seg)}
    if slot == len(refs) and kf2_exists:
        dst = os.path.join(segdir, "kf2.png")
        _save_asset_bytes(content, dst)
        return {"ok": True, "seg": seg, "slot": slot, "kind": "kf2",
                "frames": _seg_frames(pdir, item, ep, seg)}
    return JSONResponse({"error": f"槽位 {slot} 不存在"}, status_code=404)


@router.post("/storyboard/frames/remove")
def storyboard_frames_remove(payload: dict, request: Request):
    """从某段移除一个 ref 参考槽（kf2 随文件存在/不存在自然呈现，不在此移除）。"""
    name = str((payload or {}).get("name") or "")
    seg = str((payload or {}).get("seg") or "")
    if not name or not seg:
        return JSONResponse({"error": "name/seg 必填"}, status_code=400)
    try:
        slot = int((payload or {}).get("slot"))
    except (TypeError, ValueError):
        return JSONResponse({"error": "slot 必填(0 起)"}, status_code=400)
    pdir = _proj_dir(name)
    kp, root, item = _load_plan_seg(pdir, seg)
    if item is None:
        return JSONResponse({"error": f"kf_plan 中无 {seg}"}, status_code=404)
    ep = seg[:3]
    refs = [(i, _ref_abs(kp, r)) for i, r in enumerate(item.get("refs") or [])
            if os.path.isfile(_ref_abs(kp, r))]
    if slot >= len(refs):
        return JSONResponse({"error": f"槽位 {slot} 不是可移除的参考图槽"}, status_code=404)
    i_old, ap_old = refs[slot]
    del item["refs"][i_old]
    roles = item.get("ref_roles")
    if isinstance(roles, list):
        item["ref_roles"] = [r for r in roles if str((r or {}).get("path", "")) != str(ap_old)]
    # 段级专用副本（位于本段目录内的 ref*.png）随移除一并删除，避免留孤儿；
    # 共享基准图（kf_refs/ 等本段目录外）绝不删除，其他段仍在引用。
    segdir = os.path.join(pdir, "投喂稿", ep, seg)
    if ap_old and os.path.normpath(ap_old).startswith(os.path.normpath(segdir)):
        try:
            os.remove(ap_old)
        except OSError:
            pass
    _dump_json(kp, root)
    return {"ok": True, "seg": seg, "slot": slot, "frames": _seg_frames(pdir, item, ep, seg)}


def _rebuild_manifest(pdir: str, ep: str) -> dict:
    """从投喂稿重建 manifest（与 rebuild_manifest.sh 同源逻辑）。"""
    md = os.path.join(pdir, "投喂稿", ep, f"投喂稿-{ep}.md")
    if not os.path.isfile(md):
        raise FileNotFoundError(md)
    txt = open(md, encoding="utf-8").read()
    segs = []
    blocks = re.split(r"\n(?=## )", txt)
    for blk in blocks:
        if not re.match(r"## " + re.escape(ep) + r"-\d+", blk):
            continue
        head = blk.split("\n", 1)[0]
        mh = re.match(r"## (" + re.escape(ep) + r"-\d+).*?(?:（--duration (\d+)）)?", head)
        if not mh:
            continue
        seg = mh.group(1)
        dur = int(mh.group(2)) if mh.group(2) else 10
        if dur not in (3, 5, 10, 18):
            dur = 10
        body = blk.split("\n", 1)[1] if "\n" in blk else ""
        prompt = _build_seg_prompt(body)
        kf_dir = os.path.join(pdir, "投喂稿", ep, seg)
        kfs = [f"{kf_dir}/kf{i}.png" for i in (1, 2, 3)
               if os.path.exists(f"{kf_dir}/kf{i}.png")]
        segs.append({"seg": seg, "prompt": prompt, "duration": dur, "keyframes": kfs})
    os.makedirs(os.path.join(pdir, "投喂稿", ep), exist_ok=True)
    return {"episode": ep, "project_dir": pdir, "segments": segs}


@router.post("/segments/render")
def segments_render(payload: dict, request: Request):
    name = str((payload or {}).get("name") or "")
    ep = str((payload or {}).get("ep") or "")
    seg = str((payload or {}).get("seg") or "")
    override_prompt = str((payload or {}).get("prompt") or "").strip()
    if not name or not ep or not seg:
        return JSONResponse({"error": "name/ep/seg 必填"}, status_code=400)
    pdir = _proj_dir(name)

    try:
        manifest = _rebuild_manifest(pdir, ep)
    except Exception as e:
        return JSONResponse({"error": f"重建 manifest 失败: {e}"}, status_code=500)

    seg_item = next((s for s in manifest["segments"] if s["seg"] == seg), None)
    if seg_item is None:
        return JSONResponse({"error": f"{ep} 中无 {seg}"}, status_code=404)
    if override_prompt:
        seg_item["prompt"] = override_prompt

    # 覆盖词时写独立的临时 manifest，避免污染投喂稿重建来源
    os.makedirs(os.path.join(pdir, ".review"), exist_ok=True)
    mpath = os.path.join(pdir, ".review", f"{ep}.manifest.json")
    with open(mpath, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    tid = _new_task("segment", {"name": name, "ep": ep, "seg": seg}, jobs=1)
    _prune_tasks()

    def _line(s):
        s = (s or "").strip()
        if s:
            _touch(tid, line=f"[{_now()}] {s[:400]}")

    def _run():
        _touch(tid, "running", line=f"[{_now()}] {seg} 渲染开始 (dur={seg_item['duration']}s)")
        _touch(tid, line=f"[{_now()}] 提示词头: {seg_item['prompt'][:60]}…")
        cmd = ["python3", GENERATOR, mpath, seg, "--force"]
        try:
            pr = _run_proc_stream(cmd, 7200, _render_env(), on_line=_line, tid=tid)
        except subprocess.TimeoutExpired:
            _touch(tid, "failed", line=f"[{_now()}] 渲染超时(2h)")
            return
        out = (pr.stdout or "") + (pr.stderr or "")
        if pr.returncode == 0 and f"[{seg}] 提交" in out:
            _touch(tid, "done", line=f"[{_now()}] {seg} 渲染完成", inc=1)
        elif pr.returncode == 0:
            _touch(tid, "failed", line=f"[{_now()}] 未提交渲染（生成器未提交，检查 prompt/参考图/段号）：{out.strip()[-300:]}")
        else:
            _touch(tid, "failed", line=f"[{_now()}] rc={pr.returncode} {out[-500:]}")

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "task_id": tid, "seg": seg}


# ==================== 级联重渲（尾帧接力）：自某段起顺序渲染到片尾 ====================
@router.post("/segments/cascade")
def segments_cascade(payload: dict, request: Request):
    """自 {seg} 起（含该段）按全片段序顺序级联重渲到片尾，跨集自动接力。

    每段渲染由 gen_kzqs_videos.py 处理尾帧接力（上游视频真实末帧 → 本段 kf1）。
    渲染纪律：单线程串行，禁止并行。
    """
    name = str((payload or {}).get("name") or "")
    start_seg = str((payload or {}).get("seg") or "")
    if not name or not start_seg:
        return JSONResponse({"error": "name/seg 必填"}, status_code=400)
    pdir = _proj_dir(name)
    flat = _kfplan_flat(pdir)
    if not flat:
        return JSONResponse({"error": "kf_plan.json 缺失或为空"}, status_code=404)
    order = [s.get("seg") for s in flat if s.get("seg")]
    if start_seg not in order:
        return JSONResponse({"error": f"kf_plan 中无 {start_seg}"}, status_code=404)
    target = order[order.index(start_seg):]
    if not target:
        return JSONResponse({"error": "无可级联段"}, status_code=400)

    tid = _new_task("cascade", {"name": name, "start": start_seg,
                                "count": len(target), "eps": sorted({s[:3] for s in target})},
                    jobs=len(target))
    _prune_tasks()

    def _line(s):
        s = (s or "").strip()
        if s:
            _touch(tid, line=f"[{_now()}] {s[:400]}")

    def _run():
        _touch(tid, "running", line=f"[{_now()}] 级联重渲开始：{start_seg} → 片尾 共 {len(target)} 段")
        failed = []
        # 按集分组，保持 hEp 顺序（E01→E06）
        by_ep = {}
        for s in flat:
            if s.get("seg") in target:
                by_ep.setdefault(s["seg"][:3], []).append(s["seg"])
        for ep in sorted(by_ep):
            ep_segs = by_ep[ep]
            mpath = os.path.join(pdir, ".review", f"{ep}.cascade-manifest.json")
            try:
                manifest = _rebuild_manifest(pdir, ep)
            except Exception as e:
                _touch(tid, line=f"[{_now()}] {ep}: manifest 重建失败 {e}")
                failed += ep_segs
                continue
            with open(mpath, "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False, indent=2)
            args = ["python3", GENERATOR, mpath, "--force"] + (["--from", start_seg] if ep == start_seg[:3] else [])
            _touch(tid, line=f"[{_now()}] {ep}: 启动 {args[-1] if args[-1] != mpath else '全段'}")
            try:
                pr = _run_proc_stream(args, 10800, _render_env(), on_line=_line, tid=tid)
            except subprocess.TimeoutExpired:
                _touch(tid, line=f"[{_now()}] {ep}: 超时(3h)")
                failed += ep_segs
                continue
            log_tail = ((pr.stdout or "") + (pr.stderr or ""))
            ok = pr.returncode == 0 and "提交" in log_tail
            _touch(tid, line=f"[{_now()}] {ep}: {'OK' if ok else f'rc={pr.returncode} 未提交或失败'}",
                   inc=len(ep_segs) if ok else 0)
            if not ok:
                failed += ep_segs
            else:
                # 记录每段完成（便于定位断点）
                m_last = re.findall(r"\[(\d\d:\d\d:\d\d)\] (E\d\d-\d\d): OK", log_tail)
                for _, s in m_last:
                    _touch(tid, line=f"[{_now()}]   {s} OK")
                if not pr.returncode:
                    _touch(tid, line=f"[{_now()}] {ep} 完成（共 {len(ep_segs)} 段）")
        if failed:
            _touch(tid, "failed", line=f"[{_now()}] 级联重渲结束：失败 {len(failed)} 段 {failed[:10]}")
        else:
            _touch(tid, "done", line=f"[{_now()}] 级联重渲全部完成（{len(target)} 段）")

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "task_id": tid, "start": start_seg, "count": len(target)}


# ==================== 关键帧单帧重出（读 kf_plan 规范化 prompt + refs，保段缝链） ====================
def _kfplan_flat(pdir: str):
    """读 kf_plan.json 并摊平成段列表（含 outdir / kf1_from / refs / prompt）。"""
    p = os.path.join(pdir, "投喂稿", "kf_plan.json")
    if not os.path.isfile(p):
        return []
    try:
        data = json.load(open(p, encoding="utf-8"))
    except Exception:
        return []
    return [s for ep in data for s in ep.get("segments", [])] if isinstance(data, list) else []


# ==================== 批量任务防重（不再有并发硬上限） ====================
#
# 旧实现在这里按「契约 §4.8 出图≤4 / §5 渲影≤2」把前端传的并发硬钳到 4/2，并按
# `kf1_from` 接力依赖分波。reference 模式下段与段之间没有数据依赖（段缝由上一段
# 末帧自然承接），提交后由 media.py 轮询、后端失败自动重建重试，所以只要提交成功
# 即可——上限与分波都失去依据，现已移除。前端传的 workers 仅作为提示，不再截断。

_BATCH_BUSY = set()
_BATCH_BUSY_LOCK = threading.Lock()


def _workers_hint(v, jobs: int) -> int:
    """并发提示值：前端没给或给 0 时按任务量取；**不再向上截断**。"""
    try:
        n = int(v)
    except (TypeError, ValueError):
        n = 0
    return n if n > 0 else max(1, jobs)


def _batch_enter(name: str) -> bool:
    """项目级互斥：同一项目同一时刻只允许一个批量任务。

    限流挡不住「用户手快点两次按钮」，而重出帧/重渲会真实烧额度，
    故对 name 加占位闸；单段端点不受影响（那是既有的逐条操作语义）。
    """
    with _BATCH_BUSY_LOCK:
        if name in _BATCH_BUSY:
            return False
        _BATCH_BUSY.add(name)
        return True


def _batch_exit(name: str) -> None:
    with _BATCH_BUSY_LOCK:
        _BATCH_BUSY.discard(name)


def _kf_generate(pdir: str, item: dict, frame: str, prompt_override: str = "", tid: str = None):
    """出一张关键帧，返回 (ok, msg)。单帧端点与批量端点共用，避免两份实现行为漂移。"""
    seg = str(item.get("seg") or "")
    prompt = (prompt_override or str(item.get(f"{frame}_prompt") or "")).strip()
    if not prompt:
        return False, "无出图 prompt"
    outdir = str(item.get("outdir") or "")
    if not outdir or not os.path.isdir(outdir):
        return False, f"outdir 不存在: {outdir or '(空)'}"
    out_file = os.path.join(outdir, f"{frame}.png")
    refs = [str(r) for r in (item.get("refs") or []) if r and os.path.isfile(str(r))]
    if os.path.exists(out_file):
        try:
            os.remove(out_file)
        except Exception:
            pass
    cmd = ["python3", MEDIA, "image", "--prompt", prompt,
           "--size", "2K", "--ratio", "16:9", "--timeout", "900"]
    for r in refs:
        cmd += ["--ref", r]
    try:
        pr = _run_proc(cmd, 1800, _token_env(), tid=tid)
    except subprocess.TimeoutExpired:
        return False, "出图超时(30min)"
    out = (pr.stdout or "") + (pr.stderr or "")
    fm = re.search(r"FILE=(\S+)", out)
    if pr.returncode != 0 or not fm or not os.path.isfile(fm.group(1).strip()):
        return False, f"rc={pr.returncode} {out[-300:].replace(chr(10), ' ')}"
    src = fm.group(1).strip()
    shutil.copy(src, out_file)
    return True, f"{os.path.getsize(out_file) // 1024}KB refs={len(refs)}"


def _seg_render_one(mpath: str, seg: str, timeout: int = 7200, on_line=None):
    """渲一段，返回 (ok, msg)。批量与单段共用。on_line 把实时输出写入任务日志。

    显式 --force：重渲语义下已存在的段视频也要覆盖，否则生成器会「已完成，跳过」
    并以 rc=0 退出，被误判为成功（实测踩过：批量渲影 5 段 0s「成功」其实没渲）。
    """
    try:
        pr = _run_proc_stream(["python3", GENERATOR, mpath, seg, "--force"], timeout,
                              _render_env(), on_line=on_line)
    except subprocess.TimeoutExpired:
        return False, f"渲染超时({timeout // 3600}h)"
    out = (pr.stdout or "") + (pr.stderr or "")
    if pr.returncode == 0 and f"[{seg}] 提交" in out:
        return True, "OK"
    if pr.returncode == 0:
        tail = " / ".join(l.strip() for l in out.strip().splitlines()[-3:])[:240]
        return False, f"未提交渲染（{tail}）"
    tail = out[-300:].replace("\n", " ")
    return False, f"rc={pr.returncode} {tail}"


def _seg_video(pdir: str, seg: str) -> str:
    return os.path.join(pdir, seg, f"{seg}.mp4")


@router.post("/keyframes/regenerate")
def keyframes_regenerate(payload: dict, request: Request):
    name = str((payload or {}).get("name") or "")
    seg = str((payload or {}).get("seg") or "")
    frame = str((payload or {}).get("frame") or "")  # kf1 | kf2
    prompt_override = str((payload or {}).get("prompt") or "").strip()
    if not name or not seg or frame not in ("kf1", "kf2"):
        return JSONResponse({"error": "name/seg/frame 必填(frame=kf1|kf2)"}, status_code=400)
    pdir = _proj_dir(name)
    flat = _kfplan_flat(pdir)
    if not flat:
        return JSONResponse({"error": "kf_plan.json 缺失或为空"}, status_code=404)
    item = next((s for s in flat if s.get("seg") == seg), None)
    if item is None:
        return JSONResponse({"error": f"kf_plan 中无 {seg}"}, status_code=404)
    # 尾帧接力：非独立首帧（kf1_from 存在）不允许单独重出，走级联重渲
    if frame == "kf1" and item.get("kf1_from"):
        return JSONResponse(
            {"error": f"{seg}/kf1 是尾帧接力帧（自上游视频末帧抽取），不可单独重出；"
                      f"如需修改请重出上游段 kf2 或级联重渲本段及其下游段"},
            status_code=400)
    prompt = prompt_override or str(item.get(f"{frame}_prompt") or "").strip()
    if not prompt:
        return JSONResponse({"error": f"{seg}/{frame} 无出图 prompt"}, status_code=400)
    outdir = str(item.get("outdir") or "")
    if not outdir or not os.path.isdir(outdir):
        return JSONResponse({"error": f"{seg} outdir 不存在: {outdir}"}, status_code=404)
    out_file = os.path.join(outdir, f"{frame}.png")
    refs = [r for r in (item.get("refs") or []) if r and os.path.isfile(str(r))]

    tid = _new_task("keyframe", {"name": name, "seg": seg, "frame": frame}, jobs=1)
    _prune_tasks()

    def _run():
        _touch(tid, "running", line=f"[{_now()}] {seg}/{frame} 关键帧重出…")
        if os.path.exists(out_file):
            _touch(tid, line=f"[{_now()}] 已删除旧帧 {os.path.basename(out_file)}")
        _touch(tid, line=f"[{_now()}] media.py image 启动 refs={[os.path.basename(r) for r in refs]}")
        ok, msg = _kf_generate(pdir, item, frame, prompt_override, tid=tid)
        if not ok:
            _touch(tid, "failed", line=f"[{_now()}] {msg}")
            return
        # 尾帧接力后：kf2 改动不影响相邻段静态图（下游首帧=上游视频真实末帧），
        # 但本段及下游段已渲染视频必须级联重渲才生效。
        _touch(tid, "done", line=f"[{_now()}] OK {msg}"
                                  "。提醒：本段 kf2 已更新，请对本段及下游段做级联重渲以生效。",
               inc=1)

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "task_id": tid, "seg": seg, "frame": frame}


# ==================== 批量：一次全量提交出帧 ====================
@router.post("/keyframes/batch")
def keyframes_batch(payload: dict, request: Request):
    """批量重出关键帧：一次全量提交，并发不再设硬上限。

    与逐段串行点击的区别在于把 N 次出图压进一个任务，失败不中断其余段。
    segs 为空时按 ep（为空则全片）取全部段；frames 默认 kf1+kf2。
    """
    body = payload or {}
    name = str(body.get("name") or "")
    ep = str(body.get("ep") or "")
    segs = [str(x).strip() for x in (body.get("segs") or []) if str(x).strip()]
    frames = [f.strip() for f in (body.get("frames") or ["kf1", "kf2"])
              if str(f).strip() in ("kf1", "kf2")]
    if not name or not frames:
        return JSONResponse({"error": "name/frames 必填(frames=kf1|kf2)"}, status_code=400)

    pdir = _proj_dir(name)
    flat = _kfplan_flat(pdir)
    if not flat:
        return JSONResponse({"error": "kf_plan.json 缺失或为空"}, status_code=404)
    pool = flat
    if ep:
        pool = [s for s in pool if str(s.get("seg", "")).startswith(ep)]
    if segs:
        want = set(segs)
        pool = [s for s in pool if str(s.get("seg")) in want]

    jobs, skipped = [], []
    for s in pool:
        for fr in frames:
            jobs.append((s, fr))
    workers = _workers_hint(body.get("workers"), len(jobs))
    if not jobs:
        return {"ok": True, "jobs": 0, "workers": workers, "skipped": skipped,
                "msg": "无可重出帧（筛选后为空）"}

    tid = _new_task("keyframe-batch",
                    {"name": name, "ep": ep or "全片", "jobs": len(jobs), "workers": workers},
                    jobs=len(jobs))
    _prune_tasks()
    if not _batch_enter(name):
        _touch(tid, "failed", line=f"[{_now()}] 「{name}」已有批量任务在跑，本次拒绝提交"
                                    "（防重复烧额度）")
        return JSONResponse({"error": "该项目已有批量任务在跑，请等它结束再提交"}, status_code=409)

    def _run():
        t0 = time.time()
        ok_n = fail_n = 0
        try:
            _touch(tid, "running", line=f"[{_now()}] 批量出帧 {len(jobs)} 张，并发 {workers}"
                                         f"（不设硬上限）")
            for sk in skipped:
                _touch(tid, line=f"[{_now()}] 跳过 {sk['seg']}/{sk['frame']}：{sk['reason']}")

            def one(job):
                s, fr = job
                return job, _kf_generate(pdir, s, fr, tid=tid)

            with ThreadPoolExecutor(max_workers=workers) as ex:
                for (s, fr), (ok, msg) in ex.map(one, jobs):
                    tag = f"{s.get('seg')}/{fr}"
                    if ok:
                        ok_n += 1
                        _touch(tid, line=f"[{_now()}] {tag} OK {msg}", inc=1)
                    else:
                        fail_n += 1
                        _touch(tid, line=f"[{_now()}] !! {tag} 失败：{msg}", inc=1)
        finally:
            _batch_exit(name)
        _touch(tid, "failed" if fail_n else "done",
               line=f"[{_now()}] 批量出帧结束：成功 {ok_n} / 失败 {fail_n} / "
                    f"跳过 {len(skipped)}，耗时 {int(time.time() - t0)}s")
        _touch(tid, line="提醒：换帧只更新静态图，已渲段视频需显式级联重渲才生效。")

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "task_id": tid, "jobs": len(jobs), "workers": workers,
            "skipped": skipped}


# ==================== 批量：一次全量提交渲影（reference 模式，不分波） ====================
@router.post("/segments/batch")
def segments_batch(payload: dict, request: Request):
    """批量渲影：**串行提交**（默认间隔 60s，可 submit_gap 覆盖）。

    与 /segments/cascade 的区别：cascade 是「某段起串行渲到片尾」（顺序敏感）；
    本端点用于「一批返修段」。段视频走 reference 模式，参考图取自 kf_plan 的 refs。

    实现：按集分组，每集一次调用 gen_kzqs_videos.py --submit-gap N（含这些段的
    过滤 manifest）。生成器逐段提交：一段被上游接受（TASK_ID）后 sleep N 秒再提交
    下一段；某段提交失败则等 N 秒重试该段直到成功（直到全部提交成功）。各段提交
    成功后各自留在后台轮询/落盘，全部提交完后一起等待出片。
    """
    body = payload or {}
    name = str(body.get("name") or "")
    segs = [str(x).strip() for x in (body.get("segs") or []) if str(x).strip()]
    if not name or not segs:
        return JSONResponse({"error": "name/segs 必填(segs 为要批量渲的段号列表)"}, status_code=400)
    workers = _workers_hint(body.get("workers"), len(segs))
    submit_gap = int(body.get("submit_gap") or 60)

    pdir = _proj_dir(name)
    flat = _kfplan_flat(pdir)
    if not flat:
        return JSONResponse({"error": "kf_plan.json 缺失或为空"}, status_code=404)
    kfmap = {str(s.get("seg")): s for s in flat}
    missing = [s for s in segs if s not in kfmap]
    if missing:
        return JSONResponse({"error": f"kf_plan 中无：{missing[:8]}"}, status_code=404)

    # 按集分组并校验段存在于投喂稿 manifest
    by_ep = {}
    for s in segs:
        by_ep.setdefault(s[:3], []).append(s)
    try:
        os.makedirs(os.path.join(pdir, ".review"), exist_ok=True)
        for ep, ep_segs in by_ep.items():
            manifest = _rebuild_manifest(pdir, ep)
            avail = {x["seg"] for x in manifest["segments"]}
            miss = [s for s in ep_segs if s not in avail]
            if miss:
                return JSONResponse({"error": f"{ep} manifest 中无：{miss[:8]}"}, status_code=404)
    except Exception as e:
        return JSONResponse({"error": f"重建 manifest 失败: {e}"}, status_code=500)

    tid = _new_task("segment-batch",
                    {"name": name, "segs": segs, "eps": sorted(by_ep),
                     "jobs": len(segs), "workers": workers, "submit_gap": submit_gap},
                    jobs=len(segs))
    _prune_tasks()
    if not _batch_enter(name):
        _touch(tid, "failed", line=f"[{_now()}] 「{name}」已有批量任务在跑，本次拒绝提交")
        return JSONResponse({"error": "该项目已有批量任务在跑，请等它结束再提交"}, status_code=409)

    def _run():
        t0 = time.time()
        st = {}          # seg -> "ok"/"fail"
        try:
            for ep in sorted(by_ep):
                segs_ep = by_ep[ep]
                manifest = _rebuild_manifest(pdir, ep)
                keep = set(segs_ep)
                manifest["segments"] = [s for s in manifest["segments"]
                                        if str(s.get("seg") or "") in keep]
                mp = os.path.join(pdir, ".review", f"{ep}.batch-manifest.json")
                with open(mp, "w", encoding="utf-8") as f:
                    json.dump(manifest, f, ensure_ascii=False, indent=2)
                _touch(tid, "running",
                       line=f"[{_now()}] {ep} 串行提交 {len(segs_ep)} 段，间隔 {submit_gap}s"
                            f"（提交成功后各段后台轮询）")

                def on_line(s, _ep=ep):
                    _touch(tid, line=f"[{_now()}] {s[:300]}")
                    m = re.search(r"(E\d+-\d+): (OK|FAIL)\b", s)
                    if m:
                        seg = m.group(1)
                        ok = m.group(2) == "OK"
                        st[seg] = "ok" if ok else "fail"
                        _touch(tid, line=f"[{_now()}] {seg} {'OK' if ok else '失败'}", inc=1)

                cmd = ["python3", GENERATOR, mp, "--force",
                       "--submit-gap", str(submit_gap)]
                try:
                    _run_proc_stream(cmd, 7200 * max(1, len(segs)) + submit_gap * len(segs),
                                     _render_env(), on_line=on_line, tid=tid)
                except subprocess.TimeoutExpired:
                    _touch(tid, line=f"[{_now()}] {ep} 渲染超时，已终止")
        finally:
            _batch_exit(name)

        ok_n = sum(1 for v in st.values() if v == "ok")
        bad = sorted(s for s in segs if st.get(s) != "ok")
        _touch(tid, "failed" if bad else "done",
               line=f"[{_now()}] 批量渲影结束：成功 {ok_n} / 失败 {len(bad)}，"
                    f"耗时 {int(time.time() - t0)}s")
        if bad:
            _touch(tid, line="失败段：" + ", ".join(bad[:12]))

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "task_id": tid, "jobs": len(segs), "workers": workers,
            "submit_gap": submit_gap, "mode": "serial-submit"}


# ==================== 剧集审核（完整整集视频） ====================
def _ep_seg_videos(pdir: str, ep: str):
    """本集各段视频文件（pdir/{ep}-NN/{ep}-NN.mp4）按段号排序。"""
    out = []
    try:
        names = os.listdir(pdir)
    except OSError:
        return out
    for d in sorted(names):
        if re.match(rf"^{re.escape(ep)}-0(\d+)$", d):
            v = os.path.join(pdir, d, f"{d}.mp4")
            if os.path.isfile(v):
                out.append(v)
    return out


def _ep_seg_total(md: str) -> int:
    """投喂稿里声明的本集段数。"""
    try:
        txt = open(md, encoding="utf-8").read()
    except Exception:
        return 0
    return len(re.findall(r"^##\s.*-\d+", txt, re.M))


@router.get("/episodes")
async def episodes(name: str = "", request: Request = None):
    """剧集审核：列出各集「完整整集视频」（合并产物 pdir/{ep}.mp4）与全剧连播视频。"""
    pdir = _proj_dir(name)
    root = _deliv_root()
    feed = os.path.join(pdir, "投喂稿")
    eps = []
    if os.path.isdir(feed):
        eps = sorted(d for d in os.listdir(feed)
                     if os.path.isfile(os.path.join(feed, d, f"投喂稿-{d}.md")))
    out = []
    for ep in eps:
        md = os.path.join(feed, ep, f"投喂稿-{ep}.md")
        total = _ep_seg_total(md) or len(_ep_seg_videos(pdir, ep))
        segf = _ep_seg_videos(pdir, ep)
        video = os.path.join(pdir, f"{ep}.mp4")
        has = os.path.isfile(video) and os.path.getsize(video) > 0
        vrel = os.path.relpath(video, root).replace("\\", "/") if has and video.startswith(root) else ""
        out.append({
            "ep": ep, "has_video": has, "video": vrel,
            "vmtime": int(os.path.getmtime(video)) if has else 0,
            "size": os.path.getsize(video) if has else 0,
            "segments_done": len(segf), "segments_total": total,
        })
    full = None
    try:
        for f in sorted(os.listdir(pdir)):
            m = re.match(r"^(\d+)集连播\.mp4$", f)
            if m and os.path.getsize(os.path.join(pdir, f)) > 0:
                fp = os.path.join(pdir, f)
                full = {"video": os.path.relpath(fp, root).replace("\\", "/"),
                        "name": f, "count": int(m.group(1)),
                        "vmtime": int(os.path.getmtime(fp)),
                        "size": os.path.getsize(fp)}
                break
    except OSError:
        pass
    return {"name": name, "episodes": out, "full": full}


# ==================== 合并（确认后） ====================
def _concat_mp4(files, out):
    lst = os.path.join(os.path.dirname(out), ".concat_tmp.txt")
    with open(lst, "w", encoding="utf-8") as f:
        for fp in files:
            f.write(f"file '{fp}'\n")
    try:
        r = subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst,
                            "-c", "copy", out], capture_output=True, text=True, timeout=3600)
        if r.returncode != 0:
            r = subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst,
                                "-c:v", "libx264", "-crf", "20", "-preset", "veryfast", out],
                               capture_output=True, text=True, timeout=7200)
        return r.returncode == 0 and os.path.isfile(out)
    except Exception:
        return False
    finally:
        try:
            os.remove(lst)
        except Exception:
            pass


@router.post("/merge")
def merge(payload: dict, request: Request):
    name = str((payload or {}).get("name") or "")
    eps = [str(x) for x in (payload or {}).get("eps") or [] if str(x).strip()]
    if not name or not eps:
        return JSONResponse({"error": "name/eps 必填"}, status_code=400)
    pdir = _proj_dir(name)
    tid = _new_task("merge", {"name": name, "eps": eps}, jobs=len(eps))
    _prune_tasks()

    def _run():
        _touch(tid, "running", line=f"[{_now()}] 合并 {', '.join(eps)} 开始")
        # 备份将被覆盖的原始整集/连播文件（保留带音轨源，勿破坏真相源）
        bk = os.path.join(pdir, ".review", "merge_backup")
        os.makedirs(bk, exist_ok=True)
        for fn in list(eps) + [f"{len(eps)}集连播.mp4"]:
            src = os.path.join(pdir, fn + ".mp4")
            if os.path.isfile(src):
                dst = os.path.join(bk, fn + ".mp4")
                try:
                    import shutil as _sh
                    _sh.copy2(src, dst)
                    _touch(tid, line=f"[{_now()}] 已备份 {fn}.mp4 -> .review/merge_backup/")
                except Exception:
                    pass
        for ep in eps:
            seg_dirs = sorted(
                d for d in os.listdir(pdir)
                if re.match(rf"^{re.escape(ep)}-0(\d+)$", d)
                and os.path.isfile(os.path.join(pdir, d, f"{d}.mp4"))
            )
            if not seg_dirs:
                _touch(tid, line=f"[{_now()}] {ep}: 无段视频，跳过", inc=1)
                continue
            files = [os.path.join(pdir, d, f"{d}.mp4") for d in seg_dirs]
            out = os.path.join(pdir, f"{ep}.mp4")
            ok = _concat_mp4(files, out)
            _touch(tid, line=f"[{_now()}] {ep}: {'OK' if ok and os.path.exists(out) else '失败'}"
                             f"（{len(files)} 段）", inc=1)
        eps_ok = [ep for ep in eps if os.path.exists(os.path.join(pdir, f"{ep}.mp4"))]
        if len(eps_ok) >= 2:
            full = os.path.join(pdir, f"{len(eps_ok)}集连播.mp4")
            ok = _concat_mp4([os.path.join(pdir, f"{ep}.mp4") for ep in eps_ok], full)
            if ok:
                _touch(tid, line=f"[{_now()}] 连播版 -> {os.path.basename(full)}")
        _touch(tid, "done", line=f"[{_now()}] 合并完成")
        try:
            from app.server.api import git as _git_api
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "task_id": tid}


# ==================== 任务查询 ====================
@router.get("/tasks")
async def tasks(request: Request):
    with _TASKS_LOCK:
        items = [dict(t, log=list(t["log"])) for t in
                 sorted(_TASKS.values(), key=lambda x: x["updated"], reverse=True)[:20]]
    return {"tasks": items}


@router.get("/tasks/{tid}")
async def task(tid: str, request: Request = None):
    with _TASKS_LOCK:
        t = _TASKS.get(tid)
        if t is None:
            return JSONResponse({"error": "任务不存在"}, status_code=404)
        return dict(t, log=list(t["log"]))


@router.post("/tasks/{tid}/cancel")
def task_cancel(tid: str, request: Request = None):
    """取消任务：置为 cancelled 并终止正在运行的生成子进程（进程组）。

    批量渲影等「串行提交 + 失败 60s 重试」的任务在此被中止：_run_proc{,_stream}
    已按 tid 登记 Popen，_kill_process_tree 连同 media.py 孙进程一并终止。
    已取消的任务终态不再被 worker 降级（_touch 保护）。
    """
    with _TASKS_LOCK:
        t = _TASKS.get(tid)
        if t is None:
            return JSONResponse({"error": "任务不存在"}, status_code=404)
        if t["status"] in ("done", "failed", "cancelled"):
            return {"ok": True, "already": True, "status": t["status"]}
        if len(t["log"]) >= 500:
            t["log"] = t["log"][-500:]
        t["log"].append(f"[{_now()}] ⏹ 用户取消任务")
        t["status"] = "cancelled"
        t["updated"] = time.time()
    killed = _kill_task_proc(tid)
    return {"ok": True, "cancelled": True, "killed_proc": killed}