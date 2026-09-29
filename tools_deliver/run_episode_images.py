#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_episode_images.py — 去 LLM 直跑分镜图（对标 run_episode_videos.py）
读 storyboard，逐段逐切调 media.py image 出 <段号>/f<切序>.png（f1 钉 0.00s，fk 钉切点）。
用法: python3 run_episode_images.py <storyboard.json> [E01|1|all] [--workers 3] [--only <segId>]
"""
import sys, os, json, subprocess, time, shutil
from concurrent.futures import ThreadPoolExecutor, as_completed

AGENT_ROOT = "/home/mirror/Agnes-Agent"
MEDIA = os.path.join(AGENT_ROOT, "app/skills_builtin/agnes-media/media.py")

QUALITY = ("Photorealistic, hyper-realistic, photorealistic skin texture, natural lighting, "
           "8K detail, realistic fabric folds, cinematic film still, stunning visual impact")
TAIL = "No text, no watermark, no borders"

TOKEN = ""
try:
    with open(os.path.join(AGENT_ROOT, "data", "internal_token"), encoding="utf-8") as _tf:
        TOKEN = _tf.read().strip()
except Exception:
    pass

LOG_PATH = None

def log(s):
    line = f"[{time.strftime('%H:%M:%S')}] {s}"
    if LOG_PATH:
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass
    print(line, flush=True)

def main():
    if len(sys.argv) < 2:
        print("用法: run_episode_images.py <storyboard.json> [E01|1|all] [--workers N] [--only seg]")
        sys.exit(2)
    sb_path = sys.argv[1]
    ep = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else "all"
    workers = 3
    only = None
    for i, a in enumerate(sys.argv):
        if a == "--workers":
            workers = int(sys.argv[i + 1])
        elif a == "--only":
            only = sys.argv[i + 1]
    base = os.path.dirname(os.path.abspath(sb_path))
    global LOG_PATH
    LOG_PATH = os.path.join(base, "image-run.log")

    sb = json.load(open(sb_path, encoding="utf-8"))
    cast = json.load(open(os.path.join(base, "cast.json"), encoding="utf-8"))
    name_of = {}
    for c in cast.get("characters", []):
        name_of[c.get("id")] = c.get("name")
    sheet_of = {}
    for c in cast.get("characters", []):
        sh = (c.get("image") or {}).get("sheet", "")
        if sh:
            if os.path.exists(sh):
                sheet_of[c.get("id")] = sh
            elif os.path.exists(os.path.join(base, sh)):
                sheet_of[c.get("id")] = os.path.join(base, sh)

    tasks = []  # (segid, k, prompt, refs, dest)
    log(f"===== run_episode_images {sb_path} ep={ep} =====")
    for e in sb.get("episodes", []):
        tag = f"E{int(e.get('ep', 0)):02d}"
        if ep.lower() != "all" and tag.upper() != ep.upper().replace("EP", "E"):
            continue
        for seg in e.get("segments", []):
            sid = str(seg.get("id", ""))
            if not sid or (only and sid != only):
                continue
            dirp = os.path.join(base, sid)
            cuts = seg.get("cuts", [])
            if not cuts:
                log(f"{sid}: 无 cuts（段内没有切），跳过")
                continue
            for k, cut in enumerate(cuts, 1):
                dest = os.path.join(dirp, f"f{k}.png")
                if os.path.exists(dest) and os.path.getsize(dest) > 0:
                    continue
                frame = String(cut.get("frame", "")).strip()
                if not frame:
                    frame = "cinematic film still, medium shot, ambient lighting"
                prompt = f"{frame}, {QUALITY}, {TAIL}"
                refs = []
                for cid in list(cut.get("characters") or [])[:3]:
                    sh = sheet_of.get(cid)
                    if sh:
                        refs.append(sh)
                tasks.append((sid, k, prompt, refs, dest))
    log(f"待出图 {len(tasks)} 张（会重建已有空/缺文件）")
    if not tasks:
        log("无待出图任务，退出")
        return

    done = 0
    fail = 0

    def run_one(t):
        sid, k, prompt, refs, dest = t
        cmd = [sys.executable, MEDIA, "image", "--prompt", prompt, "--size", "2K",
               "--ratio", "16:9", "--timeout", "1200"]
        for r in refs:
            cmd += ["--ref", r]
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        env = dict(os.environ)
        if TOKEN:
            env["AGENT_INTERNAL_TOKEN"] = TOKEN
        env["AGENT_MEDIA_SOURCE"] = "short-drama-director"
        try:
            pr = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, cwd=AGENT_ROOT, env=env)
        except subprocess.TimeoutExpired:
            return (t, False, "超时")
        out = (pr.stdout or "") + "\n" + (pr.stderr or "")
        fm = re_search(r"FILE=(\S+)", out)
        if pr.returncode == 0 and fm:
            src = fm.group(1).strip()
            try:
                shutil.copy(src, dest)
                return (t, True, f"{int(time.time() - 0)}s")
            except Exception as ex:
                return (t, False, f"复制失败 {ex}")
        return (t, False, (out or "")[-300:])

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(run_one, t) for t in tasks]
        for fut in as_completed(futs):
            t, ok, info = fut.result()
            sid, k, _, _, dest = t
            if ok:
                done += 1
                log(f"{sid}/f{k}.png OK -> {dest} ({info})")
            else:
                fail += 1
                log(f"{sid}/f{k}.png FAIL {info}")
    log(f"===== 完成: 成功 {done}, 失败 {fail} =====")

import re
def String(s):
    return str(s or "")
def re_search(pat, s):
    return re.search(pat, s)

if __name__ == "__main__":
    main()