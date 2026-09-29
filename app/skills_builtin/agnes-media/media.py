#!/usr/bin/env python3
"""
Agnes AI 媒体统一生成脚本（skill: agnes-media）

【复用创作台逻辑】本脚本不再直连上游 API，而是通过内部通道调用
本地创作台服务器（127.0.0.1:8081）与页面完全相同的端点：

  image  →  POST /api/image/generate（异步任务）→ GET /api/image/task/{id} 轮询 → 产物已入库
  video  →  POST /api/video/generate（异步任务）→ GET /api/video/task/{id} 轮询 → 产物已入库
  fetch  →  GET /api/video/task/{id} / /api/image/task/{id} 查询任务状态
  cancel →  POST /api/{video,image}/cancel 取消任务（停止自动重试/重建）

- 生成失败由后端自动重试直到成功（图片 worker 线程；视频失败自动重建任务）；
  脚本侧在轮询超限前一直等待，超时应改用 fetch 续查、不要重新创建任务；
  需要停止时用 cancel 子命令（停止后续重试/重建；在途上游请求无法中断）。
- 产物直接落在创建任务的用户「作品库」（data/.../image_library|video_library），
  创作台页面与本脚本产物唯一、不重复。stdout 输出 FILE=（作品库绝对路径）供后续处理。
- 日志仍追加写 <skill_dir>/output/YYYYMMDD/log.md（只写日志，不重复落产物文件）。
- 内部鉴权：X-Agnes-Internal-Token（AGENT_INTERNAL_TOKEN 或 data/internal_token，
  服务启动时自动生成）+ X-Agnes-Internal-User（当前用户，默认 Mirror）。

子命令与参数保持与旧版一致（批量脚本无需改动）。
"""

import argparse
import base64
import json
import mimetypes
import os
import re
import sys
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import requests

SKILL_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SKILL_DIR.resolve().parents[3]          # .../Agnes-Agent
OUTPUT_ROOT = SKILL_DIR / "output"

# 模型/默认参数（与 SKILL.md front-matter 保持一致）
MODEL_IMAGE_DEFAULT = "agnes-image-2.5-flash"
MODEL_IMAGE_LEGACY = "agnes-image-2.0-flash"
MODEL_VIDEO = "agnes-video-v2.0"
IMAGE_SIZES = ("1K", "2K", "3K", "4K")
IMAGE_RATIOS = ("1:1", "3:4", "4:3", "16:9", "9:16", "2:3", "3:2", "21:9")
VIDEO_PRESETS = {3: 81, 5: 121, 10: 241, 18: 441}  # @24fps 的 num_frames

IMG_RATIO_DEFAULT = "16:9"
HTTP_TIMEOUT = 300


# ==================== 内部调用通道（复用创作台端点） ====================
def _base_url() -> str:
    return (os.getenv("AGNES_SERVER_BASE") or "http://127.0.0.1:8081").rstrip("/")


def _internal_token() -> str:
    v = (os.getenv("AGENT_INTERNAL_TOKEN") or "").strip()
    if v:
        return v
    try:
        tok = (PROJECT_ROOT / "data" / "internal_token").read_text(encoding="utf-8").strip()
        return tok
    except Exception:
        return ""


def _internal_user() -> str:
    """调用身份：优先当前用户上下文 → 环境变量 → 平台默认。"""
    try:
        from app.userctx import current_user
        u = current_user()
        if u:
            return str(u)
    except Exception:
        pass
    return os.getenv("AGENT_USERNAME") or "Mirror"


def _hdrs() -> dict:
    tok = _internal_token()
    if not tok:
        raise SystemExit("[media] 找不到内部令牌（AGENT_INTERNAL_TOKEN 或 data/internal_token 缺失，请先启动服务）")
    return {
        "X-Agnes-Internal-Token": tok,
        "X-Agnes-Internal-User": _internal_user(),
        "Content-Type": "application/json",
    }


def _api_get(path: str, allow_404: bool = False):
    r = requests.get(_base_url() + path, headers=_hdrs(), timeout=HTTP_TIMEOUT)
    if r.status_code == 401:
        raise SystemExit("[media] 内部令牌校验失败（401）：请检查 data/internal_token 是否与服务端一致")
    if r.status_code == 404 and allow_404:
        return None
    if r.status_code >= 400:
        raise SystemExit(f"[media] GET {path} HTTP {r.status_code}: {r.text[:400]}")
    return r.json()


def _api_post(path: str, body: dict):
    r = requests.post(_base_url() + path, headers=_hdrs(), json=body, timeout=HTTP_TIMEOUT)
    if r.status_code == 401:
        raise SystemExit("[media] 内部令牌校验失败（401）：请检查 data/internal_token 是否与服务端一致")
    if r.status_code >= 400:
        raise SystemExit(f"[media] POST {path} HTTP {r.status_code}: {r.text[:600]}")
    return r.json()


def _api_upload_ref(local_path: str) -> str:
    """把本地参考图经 /api/upload（页面同一上传端点）入库，返回 uploads/<name> 供 refs 使用。"""
    p = Path(local_path)
    if not p.exists():
        raise SystemExit(f"[media] 参考图不存在: {p}")
    with open(p, "rb") as f:
        files = {"file": (p.name.split("/")[-1], f)}
        r = requests.post(_base_url() + "/api/upload", files=files,
                          headers={"X-Agnes-Internal-Token": _internal_token(),
                                   "X-Agnes-Internal-User": _internal_user()},
                          timeout=HTTP_TIMEOUT)
    if r.status_code == 401:
        raise SystemExit("[media] 内部令牌校验失败（401）")
    if r.status_code >= 400:
        raise SystemExit(f"[media] 上传参考图 HTTP {r.status_code}: {r.text[:400]}")
    ud = r.json()
    path = ud.get("path") or ""
    if not path:
        raise SystemExit(f"[media] 上传参考图未返回 path: {json.dumps(ud, ensure_ascii=False)[:200]}")
    return path


def _ref_to_local(ref: str) -> str:
    """参考图统一转成服务器本机可读的本地路径（URL/Data URI 先落到临时文件）。"""
    ref = (ref or "").strip()
    if ref.startswith("data:"):
        hdr, _, b64 = ref.partition(",")
        mime = hdr.replace("data:", "").split(";")[0] or "image/png"
        ext = mimetypes.guess_extension(mime) or ".png"
        tmp = Path(tempfile.gettempdir()) / f"media_ref_{uuid.uuid4().hex}{ext}"
        tmp.write_bytes(base64.b64decode(b64))
        return str(tmp)
    if ref.startswith("http://") or ref.startswith("https://"):
        tmp = Path(tempfile.gettempdir()) / f"media_ref_{uuid.uuid4().hex}{guess_ext(ref, '.png')}"
        download(ref, tmp)
        return str(tmp)
    return ref


def _user_lib_dir(kind: str) -> str:
    """解析当前用户的作品库目录（与后端 user_subdir 同源）。

    kind 为 API 端点名（video/image），作品库目录名带 _library 后缀，
    在此做映射：video -> video_library，image -> image_library。
    standalone 运行（本脚本以 `python3 media.py` 执行，app.* 不可导入）时
    按已知布局直接定位 data/users/<user>/data/<lib_kind>，避免兜底下载副本
    被服务器 _sync_skill_outputs 二次登记进作品库（重复条目根因，2026-09-26 修订）。
    """
    lib_kind = {"video": "video_library", "image": "image_library"}.get(kind, kind)
    p = ""
    try:
        from app.userctx import set_current_user
        from app.config import user_subdir
        set_current_user(_internal_user())
        p = str(user_subdir("data_dir", lib_kind))
    except Exception:
        p = ""
    if p and os.path.isdir(p):
        return p
    user = _internal_user() or "Mirror"
    cand = ""
    for root in (Path(__file__).resolve().parents[i] for i in range(2, 6)):
        t = root / "data" / "users" / user / "data" / lib_kind
        if t.is_dir():
            cand = str(t)
            break
    if cand:
        return cand
    return p


def _api_download(path: str, dst: Path) -> None:
    hdr = {"X-Agnes-Internal-Token": _internal_token(), "X-Agnes-Internal-User": _internal_user()}
    with requests.get(_base_url() + path, headers=hdr, stream=True, timeout=HTTP_TIMEOUT) as r:
        if r.status_code >= 400:
            raise SystemExit(f"[media] 下载 {path} HTTP {r.status_code}: {r.text[:200]}")
        with open(dst, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 16):
                f.write(chunk)


# ==================== 轮询（复用页面 task 端点） ====================
def _attempt_of(item: dict) -> int:
    """尝试次数：图片任务用 attempt；视频旧条目无 attempt 时用 rebuild_count + 1 回落。"""
    try:
        a = int(item.get("attempt") or 0)
    except (TypeError, ValueError):
        a = 0
    if a:
        return a
    try:
        return int(item.get("rebuild_count") or 0) + 1
    except (TypeError, ValueError):
        return 1


def poll_task(kind: str, item_id: str, interval: int, max_wait: int) -> dict:
    """轮询 /api/{kind}/task/{id} 直到 completed/failed/cancelled；超时抛错提示 fetch 续查。"""
    t0 = time.time()
    last_st = ""
    last_attempt = 0
    while True:
        r = _api_get(f"/api/{kind}/task/{item_id}")
        item = r.get("item") or {}
        st = item.get("status") or "queued"
        attempt = _attempt_of(item)
        hb = item.get("last_heartbeat") or ""
        if st != last_st or attempt != last_attempt:
            line = f"[media] {kind} 任务状态: {st}"
            if item.get("progress"):
                line += f" {item['progress']}%"
            if attempt > 1:
                line += f"（第 {attempt} 次尝试）"
            if hb:
                line += f" 心跳 {hb}"
            if item.get("error"):
                line += f"（{item['error']}）"
            print(line, file=sys.stderr)
            if attempt > last_attempt > 0:
                print(f"[media] {kind} 后端自动重试中（第 {attempt} 次尝试），无需重建任务", file=sys.stderr)
            last_st = st
            last_attempt = attempt
        if st == "completed" and item.get("file"):
            return item
        if st == "cancelled":
            raise SystemExit(f"[media] 任务已取消: {item.get('error') or '（不再自动重试）'}（TASK_ID={item_id}）")
        if st == "failed":
            raise SystemExit(f"[media] 任务失败: {item.get('error') or '未知错误'}（TASK_ID={item_id}，多次自动重试仍未成功）")
        if time.time() - t0 > max_wait:
            raise SystemExit(f"[media] 轮询超时（{max_wait}s），TASK_ID={item_id}，可稍后用 fetch 子命令续查（后端仍在自动重试）；"
                             f"确认不再需要时用 cancel 停止（不再重试/重建）")
        time.sleep(interval)


def _resolve_lib_file(kind: str, basename: str, item: dict, seq_prefix: str) -> Path:
    """产物路径：优先返回作品库内文件（本脚本已通过页面端点入库）；解析失败时下载到技能 output 兜底。"""
    libdir = _user_lib_dir(kind)
    if libdir:
        fp = Path(libdir) / basename
        if fp.is_file():
            return fp
    d = today_dir()
    seq = next_seq(d, seq_prefix)
    ext = os.path.splitext(basename)[1] or (".mp4" if kind == "video" else ".png")
    dst = d / f"{seq_prefix}_{seq:04d}{ext}"
    _api_download(f"/api/{kind}/file/{item['id']}", dst)
    return dst


# ==================== 工具 ====================
def today_dir() -> Path:
    d = OUTPUT_ROOT / datetime.now().strftime("%Y%m%d")
    d.mkdir(parents=True, exist_ok=True)
    return d


def next_seq(d: Path, prefix: str) -> int:
    n = 1
    for p in d.glob(f"{prefix}_*"):
        m = re.search(rf"{prefix}_(\d+)", p.name)
        if m:
            n = max(n, int(m.group(1)) + 1)
    return n


def guess_ext(url: str, fallback: str = ".png") -> str:
    m = re.search(r"\.(png|jpe?g|gif|webp|mp4)(\?|$)", url, re.I)
    ext = m.group(1).lower() if m else fallback
    return ".jpg" if ext == "jpeg" else f".{ext}" if not ext.startswith(".") else ext


def _media_source() -> str:
    """来源标识：短剧流程（sd 工具链）调用时置 AGENT_MEDIA_SOURCE=short-drama-director，
    后端会把任务打标并在创作台作品库中隐藏（审核页经 manifest/文件直接使用，不受影响）。"""
    return (os.environ.get("AGENT_MEDIA_SOURCE") or "").strip()


def download(url: str, save_path: Path, api_key: str = "") -> None:
    hdr = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    with requests.get(url, headers=hdr, stream=True, timeout=HTTP_TIMEOUT) as r:
        r.raise_for_status()
        with open(save_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 16):
                f.write(chunk)


def append_log(log_file: Path, **fields) -> None:
    block = [f"## {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"]
    for k, v in fields.items():
        if v not in (None, ""):
            block.append(f"**{k}**: {v}")
    block.append("---")
    block.append("")
    with open(log_file, "a", encoding="utf-8") as f:
        f.write("\n".join(block))


# ==================== Image ====================
def cmd_image(args) -> int:
    refs = []
    mode = "txt2img"
    if args.refs:
        mode = "multi" if len(args.refs) > 1 else "img2img"
        refs = [_api_upload_ref(_ref_to_local(x)) for x in args.refs]
    body: dict = {
        "mode": mode,
        "model": args.model,
        "prompt": args.prompt,
        "size": args.size,
        "ratio": args.ratio,
        "refs": refs,
        "count": 1,
    }
    if _media_source():
        body["source"] = _media_source()
    if args.extra:
        try:
            body.update({k: v for k, v in json.loads(args.extra).items() if k not in body})
        except Exception:
            raise SystemExit(f"[media] --extra 不是合法 JSON: {args.extra}")

    if args.dry_run:
        print("[dry-run] POST", _base_url() + "/api/image/generate", json.dumps(body, ensure_ascii=False)[:2000])
        return 0
    if args.return_base64 or args.format != "url":
        print("[media] 提示：--format/--return-base64 已并入页面异步入库，产物统一为 FILE（作品库）", file=sys.stderr)

    r = _api_post("/api/image/generate", body)
    items = r.get("items") or []
    if not items:
        raise SystemExit("[media] 图片任务未返回条目")
    item = items[0]
    print(f"TASK_ID={item.get('id')}")

    item = poll_task("image", item["id"], interval=args.interval, max_wait=args.timeout)

    fp = _resolve_lib_file("image", item["file"], item, "img")
    print(f"FILE={fp}")
    url = item.get("url") or item.get("remote_url") or ""
    if url:
        print(f"URL={url}")
    log_file = today_dir() / "log.md"
    append_log(log_file, Prompt=args.prompt, Model=args.model, Size=args.size,
               Ratio=args.ratio, File=str(fp))
    print(f"LOG={log_file}")
    return 0


# ==================== Video ====================
def build_page_body(args) -> dict:
    body: dict = {"model": args.model, "prompt": args.prompt}
    is_flash = "flash" in args.model

    if args.keyframes:
        if is_flash and len(args.keyframes) < 2:
            raise SystemExit("[media] flash 关键帧模式需要 ≥2 张参考图")
        body["mode"] = "keyframe" if is_flash else "keyframes"
        refs = list(args.keyframes)
    elif args.image:
        body["mode"] = "reference" if is_flash else "img2vid"
        refs = [args.image]
    elif args.mode and args.mode in (("text", "keyframe", "reference") if is_flash else ("ti2vid", "img2vid", "keyframes")):
        body["mode"] = args.mode
        refs = []
    else:
        body["mode"] = "text" if is_flash else "ti2vid"
        refs = []
    if refs:
        body["refs"] = [_api_upload_ref(_ref_to_local(x)) for x in refs]

    if is_flash:
        body["seconds"] = str(args.duration or 5)
        if args.seed is not None:
            body["seed"] = args.seed
    else:
        frames = args.frames or (VIDEO_PRESETS.get(args.duration, 0) if args.duration else 0)
        if not frames:
            frames = 121                       # 默认约 5s @24fps
        if frames > 441:
            raise SystemExit(f"[media] num_frames 最大 441（当前 {frames}）")
        if (frames - 1) % 8 != 0:
            raise SystemExit(f"[media] num_frames 必须满足 8n+1（当前 {frames}，建议 81/121/241/441）")
        body["num_frames"] = frames
        if args.fps:
            body["frame_rate"] = args.fps
        if args.width:
            body["width"] = args.width
        if args.height:
            body["height"] = args.height
        if args.seed is not None:
            body["seed"] = args.seed

    if args.negative_prompt:
        body["negative_prompt"] = args.negative_prompt
    if _media_source():
        body["source"] = _media_source()
    if args.steps:
        print("[media] 提示：--steps 在创作台后端不支持，已忽略（固定质量档）", file=sys.stderr)
    if args.extra:
        try:
            body.update({k: v for k, v in json.loads(args.extra).items() if k not in body})
        except Exception:
            raise SystemExit(f"[media] --extra 不是合法 JSON: {args.extra}")
    return body


def cmd_video(args) -> int:
    body = build_page_body(args)
    if args.dry_run:
        print("[dry-run] POST", _base_url() + "/api/video/generate", json.dumps(body, ensure_ascii=False)[:2000])
        return 0

    r = _api_post("/api/video/generate", body)
    items = r.get("items") or []
    if not items:
        raise SystemExit("[media] 视频任务未返回条目")
    item = items[0]
    vid = item.get("video_id") or item.get("id")
    print(f"TASK_ID={item.get('id')}")
    print(f"VIDEO_ID={vid}")

    item = poll_task("video", item["id"], interval=args.interval, max_wait=args.timeout)

    fp = _resolve_lib_file("video", item["file"], item, "video")
    url = item.get("remote_url") or ""
    print(f"URL={url}")
    print(f"FILE={fp}")
    log_file = today_dir() / "log.md"
    append_log(log_file, Prompt=args.prompt, Model=args.model,
               Duration=item.get("seconds") or item.get("duration"),
               Size=item.get("size"), Task=vid, URL=url or "(已入库)", File=str(fp))
    print(f"LOG={log_file}")
    return 0


def cmd_fetch(args) -> int:
    ident = (args.video_id or args.task_id or "").strip()
    if not ident:
        raise SystemExit("[media] fetch 需要 --video-id 或 --task-id")
    for kind in ("video", "image"):
        r = _api_get(f"/api/{kind}/task/{ident}", allow_404=True)
        if r:
            print(json.dumps(r, ensure_ascii=False, indent=2))
            return 0
    raise SystemExit(f"[media] 未找到任务: {ident}")


def cmd_cancel(args) -> int:
    """取消任务（video 优先，其次 image）：停止后端自动重试/重建。在途上游请求无法中断。"""
    ident = (args.video_id or args.task_id or "").strip()
    if not ident:
        raise SystemExit("[media] cancel 需要 --video-id 或 --task-id")
    for kind in ("video", "image"):
        r = _api_get(f"/api/{kind}/task/{ident}", allow_404=True)
        if not r:
            continue
        item = r.get("item") or {}
        iid = item.get("id") or ident
        st = item.get("status") or ""
        if st in ("completed", "failed", "cancelled"):
            print(f"[media] {kind} 任务已是终态（{st}），无需取消: {iid}")
            return 0
        out = _api_post(f"/api/{kind}/cancel", {"ids": [iid]})
        print(f"[media] {kind} 取消已提交: cancelled={out.get('cancelled')} ids={out.get('ids')}")
        return 0
    raise SystemExit(f"[media] 未找到任务: {ident}")


# ==================== CLI ====================
def add_common_image_args(p):
    p.add_argument("--prompt", required=True, help="图像描述 / 编辑指令")
    p.add_argument("--model", default=MODEL_IMAGE_DEFAULT,
                   help=f"模型（默认 {MODEL_IMAGE_DEFAULT}；旧版 {MODEL_IMAGE_LEGACY}）")
    p.add_argument("--size", default="2K", choices=IMAGE_SIZES, help="输出尺寸档位（默认 2K）")
    p.add_argument("--ratio", default=IMG_RATIO_DEFAULT, choices=IMAGE_RATIOS,
                   help=f"宽高比（默认 {IMG_RATIO_DEFAULT}）")
    p.add_argument("--ref", action="append", dest="refs", default=[],
                   help="参考图：URL 或本地路径（图生图/多图合成，可重复传多次；自动上传并复用创作台上传端点）")
    p.add_argument("--format", default="url", choices=["url", "b64_json"], help="输出格式（并入异步入库，恒为 FILE）")
    p.add_argument("--return-base64", action="store_true", help="文生图直接以 Base64 返回（并入异步入库，恒为 FILE）")
    p.add_argument("--extra", default="", help='附加参数 JSON（如 \'{"seed":42}\'）')
    p.add_argument("--interval", type=int, default=5, help="轮询间隔秒数（默认 5）")
    p.add_argument("--timeout", type=int, default=600,
                   help="最大等待秒数（默认 600；后端 6h 内持续自动重试；超时后用 fetch 续查、不要重建任务）")
    p.add_argument("--dry-run", action="store_true", help="只打印将发送的请求体，不发请求")


def add_common_video_args(p):
    p.add_argument("--prompt", required=True, help="视频内容描述（[主体]+[动作]+[场景]+[镜头]+[光线]+[风格]）")
    p.add_argument("--model", default=MODEL_VIDEO, help=f"模型（默认 {MODEL_VIDEO}；flash 用 agnes-video-2.5-flash）")
    p.add_argument("--image", default="", help="图生视频：单张图片 URL 或本地路径")
    p.add_argument("--keyframes", action="append", dest="keyframes", default=[],
                   help="关键帧动画：关键帧图片（URL/本地路径，≥2 张，可重复传）")
    p.add_argument("--duration", type=int, default=0, choices=sorted(VIDEO_PRESETS),
                   help="快捷时长（秒，@24fps）：3/5/10/18")
    p.add_argument("--frames", type=int, default=0, help="num_frames（≤441，满足 8n+1；默认 121≈5s@24fps）")
    p.add_argument("--fps", type=int, default=0, help="frame_rate（1-60，默认 24）")
    p.add_argument("--width", type=int, default=0, help="宽度（由后端标准化）")
    p.add_argument("--height", type=int, default=0, help="高度（由后端标准化）")
    p.add_argument("--negative-prompt", default="", help="反向提示词")
    p.add_argument("--seed", type=int, default=None, help="随机种子（可复现）")
    p.add_argument("--steps", type=int, default=None, help="num_inference_steps（后端不支持，忽略）")
    p.add_argument("--mode", default="", help="其它生成模式标记（keyframes 请用 --keyframes 自动设置）")
    p.add_argument("--interval", type=int, default=8, help="轮询间隔秒数（默认 8）")
    p.add_argument("--timeout", type=int, default=1800,
                   help="最大等待秒数（默认 1800；视频渲染较慢，超时后用 fetch 续查、不要重建任务）")
    p.add_argument("--extra", default="", help='附加参数 JSON（如 \'{"resolution":"720p"}\'）')
    p.add_argument("--dry-run", action="store_true", help="只打印将发送的请求体，不发请求")


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="agnes-media",
        description="Agnes AI 图片/视频统一生成脚本（复用创作台后端：提交→轮询→入库→日志）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_img = sub.add_parser("image", help="文生图 / 图生图 / 多图合成（异步提交+自动重试）")
    add_common_image_args(p_img)
    p_img.set_defaults(fn=cmd_image)

    p_vid = sub.add_parser("video", help="文生视频 / 图生视频 / 关键帧动画（自动轮询+入库）")
    add_common_video_args(p_vid)
    p_vid.set_defaults(fn=cmd_video)

    p_fetch = sub.add_parser("fetch", help="查询任务状态（按 video_id / task_id，兼容 image）")
    p_fetch.add_argument("--video-id", default="", help="创建任务返回的 video_id / 任务 id")
    p_fetch.add_argument("--task-id", default="", help="task_id（兼容旧版查询）")
    p_fetch.set_defaults(fn=cmd_fetch)

    p_cancel = sub.add_parser("cancel", help="取消任务（停止自动重试/重建；在途上游请求无法中断）")
    p_cancel.add_argument("--video-id", default="", help="创建任务返回的 video_id / 任务 id")
    p_cancel.add_argument("--task-id", default="", help="task_id（兼容旧版查询）")
    p_cancel.set_defaults(fn=cmd_cancel)

    args = parser.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())