#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gen_kzqs_videos.py — 《困在七岁》视频直跑驱动（agnese 单通道，不依赖任何 novel-*）
读取每集 manifest.json，逐段调 agnes-media media.py video（keyframes 2~3 图 / img2vid / ti2vid），
产物复制到 <项目目录>/<段号>/<段号>.mp4。
用法:
  python3 gen_kzqs_videos.py <manifest.json>          # 跑全部段
  python3 gen_kzqs_videos.py <manifest.json> E01-03  # 只跑某段
manifest 结构:
  {"episode": "E01", "project_dir": "<绝对路径>",
   "segments": [{"seg": "E01-01", "prompt": "...", "duration": 10,
                 "keyframes": ["<绝对路径 kf1.png>", "<绝对路径 kf2.png>"],
                 "image": "<可选 img2vid 单图路径>", "ti2vid": true}]}
"""
import sys, os, json, re, time, subprocess, shutil

MEDIA = "/home/mirror/Agnes-Agent/app/skills_builtin/agnes-media/media.py"
TOKEN_PATH = "/home/mirror/Agnes-Agent/data/internal_token"


def main():
    if len(sys.argv) < 2:
        print("用法: gen_kzqs_videos.py <manifest.json> [段号]")
        sys.exit(2)
    mpath = sys.argv[1]
    only = sys.argv[2] if len(sys.argv) > 2 else None
    m = json.load(open(mpath, encoding="utf-8"))
    pdir = m.get("project_dir") or os.path.dirname(os.path.abspath(mpath))
    logfile = os.path.join(pdir, "video-run.log")

    tok = ""
    try:
        tok = open(TOKEN_PATH, encoding="utf-8").read().strip()
    except Exception:
        pass

    def log(s):
        line = f"[{time.strftime('%H:%M:%S')}] {s}"
        print(line, flush=True)
        try:
            with open(logfile, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

    log(f"===== gen_kzqs_videos {mpath} only={only} =====")
    segs = m.get("segments", [])
    done = fail = skip = 0
    for s in segs:
        seg = str(s.get("seg", ""))
        if not seg or (only and seg != only):
            continue
        prompt = str(s.get("prompt", "")).strip()
        if not prompt:
            log(f"{seg}: 缺 prompt，跳过")
            skip += 1
            continue
        dur = int(s.get("duration") or 5)
        if dur not in (3, 5, 10, 18):
            log(f"{seg}: duration 异常={dur}，按 5 处理")
            dur = 5
        cmd = ["python3", MEDIA, "video", "--duration", str(dur),
               "--timeout", "356400", "--prompt", prompt]
        kfs = [k for k in (s.get("keyframes") or []) if k and os.path.exists(k)]
        if s.get("ti2vid"):
            pass
        elif kfs:
            if len(kfs) < 2:
                log(f"{seg}: 关键帧仅 {len(kfs)} 张（需≥2），改用 img2vid 首帧或补图")
                if not os.path.exists(kfs[0]):
                    log(f"{seg}: 无可用图，跳过")
                    skip += 1
                    continue
                cmd += ["--image", kfs[0]]
            else:
                for k in kfs[:3]:
                    cmd += ["--keyframes", k]
        elif s.get("image") and os.path.exists(s["image"]):
            cmd += ["--image", s["image"]]
        else:
            log(f"{seg}: 无 keyframes/image，ti2vid 纯文本模式")
        # 16:9 横屏显式提示（关键帧图本身即 16:9）
        log(f"{seg}: 开始生成 dur={dur} kfs={len(kfs)} prompt_head={prompt[:60]}…")
        t0 = time.time()
        env = dict(os.environ)
        if tok:
            env["AGENT_INTERNAL_TOKEN"] = tok
        try:
            pr = subprocess.run(cmd, capture_output=True, text=True, timeout=7200, env=env)
        except subprocess.TimeoutExpired:
            log(f"{seg}: 超时(2h)")
            fail += 1
            continue
        out = (pr.stdout or "") + (pr.stderr or "")
        fm = re.search(r"FILE=(\S+)", out)
        if pr.returncode == 0 and fm:
            src = fm.group(1).strip()
            dst_dir = os.path.join(pdir, seg)
            os.makedirs(dst_dir, exist_ok=True)
            dst = os.path.join(dst_dir, seg + ".mp4")
            try:
                shutil.copy(src, dst)
                log(f"{seg}: OK ({int(time.time()-t0)}s) -> {dst}")
                done += 1
            except Exception as e:
                log(f"{seg}: 复制失败 {e}")
                fail += 1
        else:
            log(f"{seg}: FAIL rc={pr.returncode} {out[-400:]}")
            fail += 1
        time.sleep(2)
    log(f"===== 完成: 成功 {done}, 失败 {fail}, 跳过 {skip} =====")
    sys.exit(0 if fail == 0 else 3)


if __name__ == "__main__":
    main()