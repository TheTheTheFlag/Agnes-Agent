#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_episode_videos.py — 直跑单集视频：读 storyboard，逐段调 agnes-media video，产物落 <段号>.mp4
用法: python3 run_episode_videos.py <storyboard.json> [E01|1|all]
不经 LLM；日志落 storyboard 同目录 video-run.log。"""
import sys, re, os, subprocess, time, json, shutil

MEDIA = "/home/mirror/Agnes-Agent/app/skills_builtin/agnes-media/media.py"
PLANMJS = "/home/mirror/Agnes-Agent/app/skills/novel-storyboard/scripts/storyboard-video-plan.mjs"

import os
_tok = os.environ.get("AGENT_INTERNAL_TOKEN", "").strip()
if not _tok:
    try:
        _tok = open("/home/mirror/Agnes-Agent/data/internal_token", encoding="utf-8").read().strip()
    except Exception:
        pass
if _tok:
    os.environ["AGENT_INTERNAL_TOKEN"] = _tok
import os
# 视频暂停门控：存在 video-pause.flag 时立即退出，不生成任何视频
if os.path.exists("/home/mirror/Agnes-Agent/tools_deliver/video-pause.flag"):
    print("[video] 已暂停（video-pause.flag 存在），本次退出")
    sys.exit(0)
sb_path = sys.argv[1]
ep = sys.argv[2] if len(sys.argv) > 2 else "all"
base = os.path.dirname(os.path.abspath(sb_path))
log_path = os.path.join(base, "video-run.log")

def log(s):
    line = f"[{time.strftime('%H:%M:%S')}] {s}"
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    print(line, flush=True)

log(f"===== run_episode_videos {sb_path} ep={ep} =====")
extra = []
if ep != "all":
    ep_up = ep.upper().replace("EP", "E")
    extra = ["--episode", ep_up]
r = subprocess.run(["node", PLANMJS, sb_path] + extra, capture_output=True, text=True)
plan = r.stdout or ""
if "VIDEO_PLAN_DONE" not in plan:
    log("命令单生成失败: " + (r.stderr or "")[-300:])
    sys.exit(1)

blocks = re.split(r"^## ", plan, flags=re.M)
done = fail = skipped = 0
for b in blocks[1:]:
    head, _, rest = b.partition("\n")
    if "⚠️" in head:
        log(f"跳过(缺图) {head.strip()}")
        skipped += 1
        continue
    m = re.search(r"^([A-Z]?\d+-\d+)", head.strip())
    seg = m.group(1) if m else "UNKNOWN"
    code = re.search(r"```bash\n(.*?)```", rest, re.S)
    if not code:
        log(f"{seg}: 命令解析失败")
        fail += 1
        continue
    cmd = " ".join(line.rstrip().rstrip("\\").strip() for line in code.group(1).splitlines() if line.strip())
    cmd = cmd.replace("python app/skills_builtin", "python3 app/skills_builtin", 1)
    log(f"{seg}: 开始生成（{head.strip()}）")
    t0 = time.time()
    try:
        pr = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=7200)
    except subprocess.TimeoutExpired:
        log(f"{seg}: 超时(2h)")
        fail += 1
        continue
    out = (pr.stdout or "") + (pr.stderr or "")
    fm = re.search(r"FILE=(\S+)", out)
    if pr.returncode == 0 and fm:
        src = fm.group(1).strip()
        dst = os.path.join(base, seg, seg + ".mp4")
        try:
            os.makedirs(os.path.join(base, seg), exist_ok=True)
            shutil.copy(src, dst)
            log(f"{seg}: OK ({int(time.time()-t0)}s) -> {dst}")
            done += 1
        except Exception as e:
            log(f"{seg}: 复制失败 {e}")
            fail += 1
    else:
        log(f"{seg}: FAIL rc={pr.returncode} {out[-300:]}")
        fail += 1
    time.sleep(2)

log(f"===== 完成: 成功 {done}, 失败 {fail}, 跳过 {skipped} =====")
sys.exit(0 if fail == 0 else 3)