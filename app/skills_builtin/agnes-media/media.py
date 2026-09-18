#!/usr/bin/env python3
"""
Agnes AI 媒体统一生成脚本（skill: agnes-media）

把「生成 → 轮询 → 下载产物 → 写日志」整合为同一个脚本入口，供大模型按实际
需求传参调用，不再手写/复制固定生成脚本。

子命令：
  image   文生图 / 图生图 / 多图合成（同步接口，返回即产物）
  video   文生视频 / 图生视频 / 关键帧动画（异步接口：创建任务 → 自动轮询 → 下载）
  fetch   按 task_id / video_id 查询任务状态（调试用）

公共行为：
  - API Key 一律从本文件同目录 keys.json 读取（不硬编码、不入库）。
  - 产物与日志统一写入 <skill_dir>/output/YYYYMMDD/（log.md 追加，含时间/Prompt/参数/URL/File）。
  - stdout 输出 URL= / FILE= / LOG= 等机器可读行，方便 Agent 回话或继续处理。
  - 参考图既可传 URL 也可传本地路径（本地文件自动转 Data URI，无需手工 base64）。

按官方文档实现（2026-09 核对）：
  - Image  : POST /v1/images/generations · agnes-image-2.5-flash
             图生图/多图参考图放 extra_body.image[]（URL 或 Data URI）；输出格式放 extra_body.response_format
  - Video  : POST /v1/videos · agnes-video-v2.0（异步）
             创建后 GET /agnesapi?video_id=<id> 轮询；completed 后取 metadata.url 下载
             num_frames ≤ 441 且满足 8n+1；关键帧动画用 extra_body.image[] + extra_body.mode="keyframes"
"""

import argparse
import base64
import json
import mimetypes
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import requests

SKILL_DIR = Path(__file__).resolve().parent
BASE_URL = "https://api.agnes-ai.cn/v1"
QUERY_URL = "https://api.agnes-ai.cn/agnesapi"
OUTPUT_ROOT = SKILL_DIR / "output"

# 模型/默认参数（与 SKILL.md front-matter 保持一致）
MODEL_IMAGE_DEFAULT = "agnes-image-2.5-flash"
MODEL_IMAGE_LEGACY = "agnes-image-2.0-flash"
MODEL_VIDEO = "agnes-video-v2.0"
IMAGE_SIZES = ("1K", "2K", "3K", "4K")
IMAGE_RATIOS = ("1:1", "3:4", "4:3", "16:9", "9:16", "2:3", "3:2", "21:9")
VIDEO_PRESETS = {3: 81, 5: 121, 10: 241, 18: 441}  # @24fps 的 num_frames

IMG_RATIO_DEFAULT = "1:1"

# 统一超时：全部请求/任务等待按 5 分钟（300s）执行
HTTP_TIMEOUT = 300


def load_api_key() -> str:
    keys_file = SKILL_DIR / "keys.json"
    if not keys_file.exists():
        raise SystemExit(f"[media] 找不到 {keys_file}，请先配置 API Key")
    keys = json.loads(keys_file.read_text(encoding="utf-8"))
    key = keys.get("agnes") or keys.get("api_key") or ""
    if not key:
        raise SystemExit(f"[media] keys.json 缺少 'agnes' 字段: {keys_file}")
    return key


def headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


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


def to_data_uri(ref: str) -> str:
    """参考图：URL 直接返回；本地路径转 Data URI。"""
    ref = ref.strip()
    if ref.startswith("data:") or ref.startswith("http://") or ref.startswith("https://"):
        return ref
    p = Path(ref)
    if not p.exists():
        raise SystemExit(f"[media] 参考图不存在: {p}")
    mime = mimetypes.guess_type(p.name)[0] or "image/png"
    b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def guess_ext(url: str, fallback: str = ".png") -> str:
    m = re.search(r"\.(png|jpe?g|gif|webp|mp4)(\?|$)", url, re.I)
    ext = m.group(1).lower() if m else fallback
    return ".jpg" if ext == "jpeg" else f".{ext}" if not ext.startswith(".") else ext


def download(url: str, save_path: Path, api_key: str = "") -> None:
    hdr = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    with requests.get(url, headers=hdr, stream=True, timeout=HTTP_TIMEOUT) as r:
        r.raise_for_status()
        with open(save_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 16):
                f.write(chunk)


def append_log(log_file: Path, **fields) -> None:
    """统一日志：每次成功产物追加一条（时间/Prompt/参数/URL/File）。"""
    block = [f"## {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"]
    for k, v in fields.items():
        if v not in (None, ""):
            block.append(f"**{k}**: {v}")
    block.append("---")
    block.append("")
    with open(log_file, "a", encoding="utf-8") as f:
        f.write("\n".join(block))


# ============================================================ Image
def cmd_image(args, api_key: str) -> int:
    payload: dict = {"model": args.model, "prompt": args.prompt, "size": args.size}
    if args.ratio:
        payload["ratio"] = args.ratio
    if args.return_base64:
        payload["return_base64"] = True
    extra: dict = {"response_format": args.format}
    if args.refs:  # 图生图 / 多图合成
        extra["image"] = [to_data_uri(x) for x in args.refs]
    if args.extra:
        extra.update(json.loads(args.extra))
    payload["extra_body"] = extra

    if args.dry_run:
        print("[dry-run] POST", f"{BASE_URL}/images/generations", json.dumps(payload, ensure_ascii=False)[:2000])
        return 0

    r = requests.post(f"{BASE_URL}/images/generations", headers=headers(api_key), json=payload, timeout=HTTP_TIMEOUT)
    if r.status_code >= 400:
        raise SystemExit(f"[media] HTTP {r.status_code}: {r.text[:500]}")
    data = r.json()["data"][0]

    d = today_dir()
    log_file = d / "log.md"
    if args.format == "b64_json" or data.get("b64_json"):
        raw = base64.b64decode(data["b64_json"])
        seq = next_seq(d, "img")
        path = d / f"img_{seq:04d}.png"
        path.write_bytes(raw)
        url = data.get("url") or ""
        print(f"FILE={path}")
    else:
        url = data["url"]
        seq = next_seq(d, "img")
        path = d / f"img_{seq:04d}{guess_ext(url, '.png')}"
        download(url, path)
        print(f"URL={url}")
        print(f"FILE={path}")

    append_log(log_file, Prompt=args.prompt, Model=args.model, Size=args.size,
               Ratio=args.ratio, Image=url or "(base64)", File=str(path))
    print(f"LOG={log_file}")
    return 0


# ============================================================ Video
def build_video_payload(args) -> dict:
    payload: dict = {"model": args.model, "prompt": args.prompt}
    # 时长：--duration 快捷档 或 --frames/--fps
    if args.duration:
        if args.duration not in VIDEO_PRESETS:
            raise SystemExit(f"[media] --duration 支持 {sorted(VIDEO_PRESETS)} 秒（@24fps）")
        args.frames = VIDEO_PRESETS[args.duration]
        if not args.fps:
            args.fps = 24
    if args.frames:
        payload["num_frames"] = args.frames
    if args.fps:
        payload["frame_rate"] = args.fps
    if args.width:
        payload["width"] = args.width
    if args.height:
        payload["height"] = args.height
    if args.image:  # 图生视频（单图，顶层 image）
        payload["image"] = to_data_uri(args.image)
    if args.negative_prompt:
        payload["negative_prompt"] = args.negative_prompt
    if args.seed is not None:
        payload["seed"] = args.seed
    if args.steps is not None:
        payload["num_inference_steps"] = args.steps

    extra: dict = {}
    if args.keyframes:  # 关键帧动画：extra_body.image[] + extra_body.mode（官方 curl 示例）
        extra["image"] = [to_data_uri(x) for x in args.keyframes]
        extra["mode"] = "keyframes"
    elif args.mode:
        extra["mode"] = args.mode  # 其它模式标记也放 extra_body（与官方示例一致）
    if args.extra:
        extra.update(json.loads(args.extra))
    if extra:
        payload["extra_body"] = extra

    nf = payload.get("num_frames")
    if nf is not None:
        if nf > 441:
            raise SystemExit(f"[media] num_frames 最大 441（当前 {nf}）")
        if (nf - 1) % 8 != 0:
            raise SystemExit(f"[media] num_frames 必须满足 8n+1（当前 {nf}，建议 81/121/241/441）")
    return payload


def video_create(args, api_key: str) -> dict:
    payload = build_video_payload(args)
    if args.dry_run:
        print("[dry-run] POST", f"{BASE_URL}/videos", json.dumps(payload, ensure_ascii=False)[:2000])
        raise SystemExit(0)
    r = requests.post(f"{BASE_URL}/videos", headers=headers(api_key), json=payload, timeout=HTTP_TIMEOUT)
    if r.status_code >= 400:
        raise SystemExit(f"[media] HTTP {r.status_code}: {r.text[:500]}")
    return r.json()


def video_poll(video_id: str, api_key: str, task_id: str = "", interval: int = 8, max_wait: int = 1800):
    """轮询视频任务，completed 返回响应 dict；失败抛错。"""
    t0 = time.time()
    while True:
        params = {"video_id": video_id}
        if task_id:  # 兼容旧版：也允许 task_id
            params = {"video_id": task_id}
        r = requests.get(QUERY_URL, params=params, headers=headers(api_key), timeout=HTTP_TIMEOUT)
        if r.status_code >= 400:
            raise SystemExit(f"[media] 查询 HTTP {r.status_code}: {r.text[:400]}")
        data = r.json()
        status = data.get("status") or data.get("state")
        if status == "completed":
            return data
        if status in ("failed", "error", "cancelled"):
            raise SystemExit(f"[media] 视频任务失败: {json.dumps(data.get('error') or data, ensure_ascii=False)[:500]}")
        if time.time() - t0 > max_wait:
            raise SystemExit(f"[media] 轮询超时（{max_wait}s），video_id={video_id}，可稍后用 fetch 子命令续查")
        print(f"[media] 视频生成中… status={status} progress={data.get('progress')} （已等待 {int(time.time() - t0)}s）", file=sys.stderr)
        time.sleep(interval)


def cmd_video(args, api_key: str) -> int:
    created = video_create(args, api_key)
    video_id = created.get("video_id") or created.get("task_id") or created.get("id")
    print(f"TASK_ID={created.get('task_id') or created.get('id')}")
    print(f"VIDEO_ID={video_id}")
    data = video_poll(video_id, api_key, interval=args.interval, max_wait=args.timeout)

    url = (data.get("metadata") or {}).get("url") or data.get("url")
    if not url:
        raise SystemExit(f"[media] 任务完成但响应中无视频 URL: {json.dumps(data, ensure_ascii=False)[:500]}")

    d = today_dir()
    seq = next_seq(d, "video")
    path = d / f"video_{seq:04d}{guess_ext(url, '.mp4')}"
    download(url, path)
    print(f"URL={url}")
    print(f"FILE={path}")
    log_file = d / "log.md"
    append_log(log_file, Prompt=args.prompt, Model=args.model, Task=video_id,
               Duration=created.get("seconds"), Size=created.get("size"), URL=url, File=str(path))
    print(f"LOG={log_file}")
    return 0


def cmd_fetch(args, api_key: str) -> int:
    params = {}
    if args.video_id:
        params = {"video_id": args.video_id}
    elif args.task_id:
        params = {"video_id": args.task_id}
    else:
        raise SystemExit("[media] fetch 需要 --video-id 或 --task-id")
    r = requests.get(QUERY_URL, params=params, headers=headers(api_key), timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


# ============================================================ CLI
def add_common_image_args(p):
    p.add_argument("--prompt", required=True, help="图像描述 / 编辑指令")
    p.add_argument("--model", default=MODEL_IMAGE_DEFAULT,
                   help=f"模型（默认 {MODEL_IMAGE_DEFAULT}；旧版 {MODEL_IMAGE_LEGACY}）")
    p.add_argument("--size", default="1K", choices=IMAGE_SIZES, help="输出尺寸档位（默认 1K）")
    p.add_argument("--ratio", default=IMG_RATIO_DEFAULT, choices=IMAGE_RATIOS,
                   help=f"宽高比（默认 {IMG_RATIO_DEFAULT}）")
    p.add_argument("--ref", action="append", dest="refs", default=[],
                   help="参考图：URL 或本地路径（图生图/多图合成，可重复传多次；本地自动转 Data URI）")
    p.add_argument("--format", default="url", choices=["url", "b64_json"], help="输出格式")
    p.add_argument("--return-base64", action="store_true", help="文生图直接以 Base64 返回")
    p.add_argument("--extra", default="", help='附加参数 JSON（合并进 extra_body，如 \'{"seed":42}\'）')
    p.add_argument("--dry-run", action="store_true", help="只打印将发送的请求体，不发请求")


def add_common_video_args(p):
    p.add_argument("--prompt", required=True, help="视频内容描述（[主体]+[动作]+[场景]+[镜头]+[光线]+[风格]）")
    p.add_argument("--model", default=MODEL_VIDEO, help=f"模型（默认 {MODEL_VIDEO}）")
    p.add_argument("--image", default="", help="图生视频：单张图片 URL 或本地路径")
    p.add_argument("--keyframes", action="append", dest="keyframes", default=[],
                   help="关键帧动画：关键帧图片（URL/本地路径，≥2 张，可重复传）")
    p.add_argument("--duration", type=int, default=0, choices=sorted(VIDEO_PRESETS),
                   help="快捷时长（秒，@24fps）：3/5/10/18")
    p.add_argument("--frames", type=int, default=0, help="num_frames（≤441，满足 8n+1；默认 121≈5s@24fps）")
    p.add_argument("--fps", type=int, default=0, help="frame_rate（1-60，默认 24）")
    p.add_argument("--width", type=int, default=0, help="宽度（默认由服务标准化，标准场景 1152/16:9）")
    p.add_argument("--height", type=int, default=0, help="高度（默认 768/16:9）")
    p.add_argument("--negative-prompt", default="", help="反向提示词")
    p.add_argument("--seed", type=int, default=None, help="随机种子（可复现）")
    p.add_argument("--steps", type=int, default=None, help="num_inference_steps")
    p.add_argument("--mode", default="", help="其它生成模式标记（keyframes 请用 --keyframes 自动设置）")
    p.add_argument("--interval", type=int, default=8, help="轮询间隔秒数（默认 8）")
    p.add_argument("--timeout", type=int, default=HTTP_TIMEOUT, help="最大等待秒数（默认 300，即 5 分钟）")
    p.add_argument("--extra", default="", help='附加参数 JSON（合并进 extra_body，如 \'{"negative_prompt":"x"}\'）')
    p.add_argument("--dry-run", action="store_true", help="只打印将发送的请求体，不发请求")


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="agnes-media",
        description="Agnes AI 图片/视频统一生成脚本（生成→轮询→下载→日志一条龙）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_img = sub.add_parser("image", help="文生图 / 图生图 / 多图合成")
    add_common_image_args(p_img)
    p_img.set_defaults(fn=cmd_image)

    p_vid = sub.add_parser("video", help="文生视频 / 图生视频 / 关键帧动画（自动轮询+下载）")
    add_common_video_args(p_vid)
    p_vid.set_defaults(fn=cmd_video)

    p_fetch = sub.add_parser("fetch", help="查询视频任务状态（调试）")
    p_fetch.add_argument("--video-id", default="", help="创建任务返回的 video_id")
    p_fetch.add_argument("--task-id", default="", help="task_id（兼容旧版查询）")
    p_fetch.set_defaults(fn=cmd_fetch)

    args = parser.parse_args()
    api_key = load_api_key()
    return args.fn(args, api_key)


if __name__ == "__main__":
    raise SystemExit(main())
