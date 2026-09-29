"""app.server.api.drama_review — 短剧审核台 API。

在 8081 创作工作台「短剧审核」tab 上提供：
  · 资产审核：列出 assets/manifest.json 的角色/场景/道具（提示词 + 出图），
    可改提示词重新生成（media.py image --ref 锁形象），产物回写 manifest。
  · 分镜审核：逐集解析投喂稿，列出每段分镜提示词与已产出视频；
    可「直接重渲」或「修改提示词后重渲」单段（gen_kzqs_videos.py 单段跑），
    确认后合并成完整视频（ffmpeg concat，整集 + 连播）。

渲染链路约定（2026-09-27 尾帧接力版）：
  段视频默认保留模型自带音轨（KEEP_AUDIO=1，本项目特例，QA 需确认含音频流），
  不再默认 -an 去音轨；配音由 local-tts-dubbing（Qwen3-TTS）按需接入。
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

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.userctx import current_user

router = APIRouter(prefix="/api/drama-review", tags=["drama-review"])

MEDIA = "/home/mirror/Agnes-Agent/app/skills_builtin/agnes-media/media.py"
GENERATOR = "/home/mirror/Agnes-Agent/tools_deliver/gen_kzqs_videos.py"
TOKEN_PATH = "/home/mirror/Agnes-Agent/data/internal_token"
AGNES_ROOT = "/home/mirror/Agnes-Agent"

_TASKS = {}
_TASKS_LOCK = threading.RLock()


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
    return env


def _render_env():
    """渲染子进程环境：token + KEEP_AUDIO=1（本项目保留模型音轨，不与默认去音轨口径冲突）。"""
    env = _token_env()
    env["KEEP_AUDIO"] = "1"
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


def _run_proc(cmd, timeout: int, env):
    """subprocess.run 的进程组版：超时先杀整条进程组再抛 TimeoutExpired。

    subprocess.run 超时只杀直接子进程，渲染脚本（gen_kzqs_videos.py）派生的
    media.py 孙进程会变孤儿继续跑，重试即产生重复 take。
    """
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, env=env, start_new_session=True)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_process_tree(proc)
        try:
            proc.communicate(timeout=5)
        except Exception:
            pass
        raise
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


# ==================== 任务登记（前端轮询） ====================
def _new_task(kind: str, meta: dict) -> str:
    tid = uuid.uuid4().hex[:12]
    with _TASKS_LOCK:
        _TASKS[tid] = {"id": tid, "kind": kind, "meta": meta, "status": "queued",
                       "log": [], "created": time.time(), "updated": time.time()}
    return tid


def _touch(tid: str, status=None, line=None):
    with _TASKS_LOCK:
        t = _TASKS.get(tid)
        if not t:
            return
        if line:
            t["log"].append(line)
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


# ==================== 资产 manifest 结构容错 ====================
# 历史 manifest 为 {assets:{characters,scenes,props}} 嵌套（条目含 views[].file），
# 新契约为顶层扁平列表（§2c·补）。审核台必须两种都能读、都能回写，
# 且回写不得改变原结构形状（legacy 继续 legacy）。
_TYPE_ALIAS = {"characters": "character", "character": "character",
               "scenes": "scene", "scene": "scene",
               "interior": "scene", "exterior": "scene", "location": "scene",
               "props": "prop", "prop": "prop", "item": "prop"}


def _normalize_asset_type(t, cat=None):
    """type 越界时回落分类键（单数化），最终缺省 prop。"""
    t = _TYPE_ALIAS.get(str(t or "").strip(), str(t or "").strip())
    if t in ("character", "scene", "prop"):
        return t
    return _TYPE_ALIAS.get(str(cat or "").strip(), "prop")


def _asset_items(data):
    """→ (pairs: [(cat|None, item)], shape)，shape ∈ flat|wrapped|categorized|unknown。

    item 为原结构内对象的引用，就地改即写回原形状。
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
    return [], "unknown"


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
    it = _asset_raw_ref(data, aid)
    if it is None:
        return JSONResponse({"error": f"资产 {aid} 不存在"}, status_code=404)

    tid = _new_task("asset", {"name": name, "id": aid})
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
            pr = _run_proc(cmd, 7200, _token_env())
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
                _touch(tid, "done", line=f"[{_now()}] OK -> {dst}")
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
        })
    return {"name": name, "ep": ep, "segments": segs}


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

    tid = _new_task("segment", {"name": name, "ep": ep, "seg": seg})
    _prune_tasks()

    def _run():
        _touch(tid, "running", line=f"[{_now()}] {seg} 渲染开始 (dur={seg_item['duration']}s)")
        _touch(tid, line=f"[{_now()}] 提示词头: {seg_item['prompt'][:60]}…")
        cmd = ["python3", GENERATOR, mpath, seg]
        try:
            pr = _run_proc(cmd, 7200, _render_env())
        except subprocess.TimeoutExpired:
            _touch(tid, "failed", line=f"[{_now()}] 渲染超时(2h)")
            return
        if pr.returncode == 0:
            _touch(tid, "done", line=f"[{_now()}] {seg} 渲染完成")
        else:
            _touch(tid, "failed", line=f"[{_now()}] rc={pr.returncode} {((pr.stdout or '')+(pr.stderr or ''))[-500:]}")

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
                                "count": len(target), "eps": sorted({s[:3] for s in target})})
    _prune_tasks()

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
            args = ["python3", GENERATOR, mpath] + (["--from", start_seg] if ep == start_seg[:3] else [])
            _touch(tid, line=f"[{_now()}] {ep}: 启动 {args[-1] if args[-1] != mpath else '全段'}")
            try:
                pr = _run_proc(args, 10800, _render_env())
            except subprocess.TimeoutExpired:
                _touch(tid, line=f"[{_now()}] {ep}: 超时(3h)")
                failed += ep_segs
                continue
            log_tail = ((pr.stdout or "") + (pr.stderr or ""))
            ok = pr.returncode == 0
            _touch(tid, line=f"[{_now()}] {ep}: {'OK' if ok else f'rc={pr.returncode}'}")
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

    tid = _new_task("keyframe", {"name": name, "seg": seg, "frame": frame})
    _prune_tasks()

    def _run():
        _touch(tid, "running", line=f"[{_now()}] {seg}/{frame} 关键帧重出…")
        if os.path.exists(out_file):
            _touch(tid, line=f"[{_now()}] 已删除旧帧 {os.path.basename(out_file)}")
            try:
                os.remove(out_file)
            except Exception:
                pass
        cmd = ["python3", MEDIA, "image", "--prompt", prompt,
               "--size", "2K", "--ratio", "16:9", "--timeout", "900"]
        for r in refs:
            cmd += ["--ref", r]
        _touch(tid, line=f"[{_now()}] media.py image 启动 refs={[os.path.basename(r) for r in refs]}")
        try:
            pr = _run_proc(cmd, 1800, _token_env())
        except subprocess.TimeoutExpired:
            _touch(tid, "failed", line=f"[{_now()}] 出图超时(30min)")
            return
        out = (pr.stdout or "") + (pr.stderr or "")
        fm = re.search(r"FILE=(\S+)", out)
        if pr.returncode != 0 or not fm or not os.path.isfile(fm.group(1).strip()):
            _touch(tid, "failed", line=f"[{_now()}] rc={pr.returncode} {out[-400:]}")
            return
        src = fm.group(1).strip()
        shutil.copy(src, out_file)
        # 尾帧接力后：kf2 改动不影响相邻段静态图（下游首帧=上游视频真实末帧），
        # 但本段及下游段已渲染视频必须级联重渲才生效。
        _touch(tid, "done", line=f"[{_now()}] OK {os.path.basename(src)}"
                                  "。提醒：本段 kf2 已更新，请对本段及下游段做级联重渲以生效。")

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "task_id": tid, "seg": seg, "frame": frame}


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
    tid = _new_task("merge", {"name": name, "eps": eps})
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
                _touch(tid, line=f"[{_now()}] {ep}: 无段视频，跳过")
                continue
            files = [os.path.join(pdir, d, f"{d}.mp4") for d in seg_dirs]
            out = os.path.join(pdir, f"{ep}.mp4")
            ok = _concat_mp4(files, out)
            _touch(tid, line=f"[{_now()}] {ep}: {'OK' if ok and os.path.exists(out) else '失败'}"
                             f"（{len(files)} 段）")
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