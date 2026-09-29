#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gen_kzqs_videos.py — 段视频渲染驱动 · 首尾帧模式（2026-09-27 定稿）。

读取每集 manifest.json，逐段调 agnes-media media.py video（keyframes 首尾帧双图插值），
产物复制到 <项目目录>/<段号>/<段号>.mp4。

【首尾帧模式】每段 kf1（首帧）+ kf2（尾帧）均为 AI 出图（各自帧画面提示词，
由 regen_all_frames.py 重建），渲染以 [kf1, kf2] 双图 keyframes 插值。
两端可控出图锚定 → 单段模型发挥缺陷在段内被尾帧拉回，坏帧不会跨段传染。

段缝：段 N≥2 的 kf1 = 上游段 kf2 出图（像素级一致，regen_all_frames.py 复制生成）。
E01-01 为全片唯一独立首帧出图。

用法:
  python3 gen_kzqs_videos.py <manifest.json>            # 跑全部段
  python3 gen_kzqs_videos.py <manifest.json> E01-03     # 只跑某段
  python3 gen_kzqs_videos.py <manifest.json> --from E02-03  # 从某段跑到本集末
"""
import sys, os, json, re, time, subprocess, shutil

MEDIA = "/home/mirror/Agnes-Agent/app/skills_builtin/agnes-media/media.py"
TOKEN_PATH = "/home/mirror/Agnes-Agent/data/internal_token"
KF_SIZE = "2624x1472"  # 关键帧统一 2K 16:9，与出图 kf2 一致


def _seg_order(pdir):
    """读 kf_plan.json 得到全片段序（跨集）。无则按 EXX-NN 推算。"""
    order = []
    kp = os.path.join(pdir, "投喂稿", "kf_plan.json")
    if os.path.isfile(kp):
        try:
            data = json.load(open(kp, encoding="utf-8"))
            order = [s.get("seg") for ep in data for s in ep.get("segments", [])]
        except Exception:
            order = []
    if order:
        return order
    eps = sorted({re.match(r"(E\d+)-", d).group(1)
                  for d in os.listdir(os.path.join(pdir, "投喂稿"))
                  if re.match(r"E\d+-", str(d)) and os.path.isdir(os.path.join(pdir, "投喂稿", d))})
    for ep in eps:
        d = os.path.join(pdir, "投喂稿", ep)
        segs = [x for x in os.listdir(d) if re.match(rf"^{ep}-\d+$", x)]
        order += sorted(segs, key=lambda x: int(x.split("-")[1]))
    return order


def _prev_seg(pdir: str, seg: str):
    order = _seg_order(pdir)
    if seg in order:
        i = order.index(seg)
        return order[i - 1] if i > 0 else ""
    m = re.match(r"^E(\d+)-(\d+)$", seg)
    if not m:
        return ""
    ep, n = int(m.group(1)), int(m.group(2))
    if n > 1:
        return f"E{ep:02d}-{n - 1:02d}"
    segs_in_ep = len([x for x in order if x.startswith(f"E{ep:02d}-")]) if order else 5
    if ep > 1:
        return f"E{ep - 1:02d}-{segs_in_ep:02d}"
    return ""


def extract_last_frame(video: str, out_png: str) -> bool:
    """抽视频真实末帧并 resize 到关键帧尺寸（2624x1472）。"""
    out_png = os.path.abspath(out_png)
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-sseof", "-0.05", "-i", video,
             "-vf", f"scale={KF_SIZE}:flags=lanczos", "-q:v", "2",
             "-update", "1", "-frames:v", "1", out_png],
            capture_output=True, text=True, timeout=120)
        return r.returncode == 0 and os.path.isfile(out_png) and os.path.getsize(out_png) > 0
    except Exception:
        return False


def main():
    if len(sys.argv) < 2:
        print("用法: gen_kzqs_videos.py <manifest.json> [段号|-from 段号]")
        sys.exit(2)
    mpath = sys.argv[1]
    args = sys.argv[2:]
    only = None
    from_seg = None
    i = 0
    while i < len(args):
        if args[i] == "--from" and i + 1 < len(args):
            from_seg = args[i + 1]
            i += 2
        else:
            only = args[i]
            i += 1
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

    log(f"===== gen_kzqs_videos {mpath} only={only} from={from_seg} =====")
    segs = m.get("segments", [])
    done = fail = skip = 0
    for s in segs:
        seg = str(s.get("seg", ""))
        if not seg:
            continue
        if only and seg != only:
            continue
        if from_seg:
            # 按段序比较：from_seg 之前的不渲染
            order = _seg_order(pdir)
            if seg in order and from_seg in order and order.index(seg) < order.index(from_seg):
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
        cmd += ["--width", "1280", "--height", "720"]

        # ---- 首尾帧模式：kf_dir 从段名确定性推导（不依赖 manifest keyframes 字段） ----
        m_seg = re.match(r"^(E\d+)-(\d+)$", seg)
        kf_dir = os.path.join(pdir, "投喂稿", m_seg.group(1), seg) if m_seg else ""
        kf1 = os.path.join(kf_dir, "kf1.png") if kf_dir else ""
        kf2 = os.path.join(kf_dir, "kf2.png") if kf_dir else ""

        # ---- 首尾帧：kf1+kf2 双图 keyframes 插值（两端可控出图，坏帧段内拉回） ----
        kfs = [k for k in ([kf1, kf2]) if k and os.path.exists(k)]
        if s.get("ti2vid"):
            pass
        elif len(kfs) >= 2:
            log(f"{seg}: keyframes 首尾帧 {os.path.basename(kfs[0])}+{os.path.basename(kfs[1])}")
            for k in kfs[:2]:
                cmd += ["--keyframes", k]
        elif len(kfs) == 1:
            log(f"{seg}: 仅 {len(kfs)} 张图，img2vid 兜底")
            cmd += ["--image", kfs[0]]
        elif s.get("image") and os.path.exists(s["image"]):
            cmd += ["--image", s["image"]]
        else:
            log(f"{seg}: 无 keyframes/image，ti2vid 纯文本模式")
        log(f"{seg}: 开始生成 dur={dur} kfs={len(kfs)} prompt_head={prompt[:60]}…")
        t0 = time.time()
        env = dict(os.environ)
        if tok:
            env["AGENT_INTERNAL_TOKEN"] = tok
        env["AGENT_MEDIA_SOURCE"] = "short-drama-director"
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
                if os.path.exists(dst):
                    # 默认保留模型自带配音音轨（交付物）；NO_AUDIO=1 才去音轨
                    if os.environ.get("NO_AUDIO") == "1":
                        tmp = dst + ".muted.mp4"
                        try:
                            mr = subprocess.run(
                                ["ffmpeg", "-y", "-i", dst, "-an", "-c:v", "copy", tmp],
                                capture_output=True, text=True, timeout=600)
                            if mr.returncode == 0 and os.path.exists(tmp):
                                os.replace(tmp, dst)
                                log(f"{seg}: OK ({int(time.time()-t0)}s, NO_AUDIO=1 已去音轨) -> {dst}")
                            else:
                                log(f"{seg}: OK ({int(time.time()-t0)}s, 音轨保留) -> {dst}")
                        except Exception as me:
                            log(f"{seg}: OK 但去音轨失败 {me} -> {dst}")
                    else:
                        log(f"{seg}: OK ({int(time.time()-t0)}s, 自带音轨保留) -> {dst}")
                else:
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