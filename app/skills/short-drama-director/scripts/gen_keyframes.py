#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
短剧关键帧批量生成 + 帧齐自动渲 —— 短剧流水线步骤 ⑤⑥ 串联器

用法
----
  python3 gen_keyframes.py <项目根> [--ep E01] [--only E01-03] [--frames kf1,kf2]
      [--workers 0] [--render-workers 0] [--no-auto-render] [--force-frame] [--force-render]

并发
----
  · **出图**：并发 `--workers`（默认不限；>1200 字长帧词建议降到 1~2 以免请求堆积）。
  · **渲影**：并发 `--render-workers`（默认不限）。段视频走 **reference 模式**，
    参考图来自 `投喂稿/kf_plan*.json` 每段的 `refs`（人物+场景，逐段差异化）
    加本段 kf2 构图图，恒 ≤5 张。各段互不依赖，可一次全量提交；
    提交后由 `media.py` 轮询、后端失败自动重建重试，无需分波、也不设硬上限。
  · 唯一硬约束仍是「同一段不得同时开两个任务；段内 出帧→渲影」。

两条铁律（本脚本与审核页「改词重出」的分工）
------------------------------------------------
  · **reference 集就绪 → 自动渲**：某段 refs 齐备后，本脚本立即渲染该段视频，不等人工点。
  · **改帧 → 不自动渲**：审核页 `keyframes/regenerate` 只换帧，**不**烧视频额度；
    本脚本对**已存在段视频**的段一律跳过自动渲，需显式 `--force-render`。
    （避免「改一个词 → 16 段全部重渲」的额度陷阱；改帧后要生效请人工级联重渲。）

reference 就绪判据
------------------
  * `kf_plan*.json` 里该段有 ≥1 张可用 `refs`（人物形象基准 `kf_refs/ref-*.png`
    与场景母版，逐段差异化编排，并带 `ref_roles` 说明各图职责）。
  * 本段 `kf2.png` **可选**：有就作为构图槽接在 refs 之后（总数 ≤5），没有也照渲。
  * **不再是「kf1+kf2 齐」** —— 视频只吃 refs，kf1/kf2 都只是出图产物。

落位约定
--------
  关键帧 → <项目根>/投喂稿/<EP>/<EP>-NN/{kf1.png,kf2.png}
  段视频 → <项目根>/<EP>-NN/<EP>-NN.mp4
  日志   → <项目根>/.review/kf_run.log
"""

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

AGENT_ROOT = os.environ.get("AGENT_ROOT", "/home/mirror/Agnes-Agent")
MEDIA = os.environ.get("AGNES_MEDIA_PY",
                       os.path.join(AGENT_ROOT, "app/skills_builtin/agnes-media/media.py"))
TOKEN = os.environ.get("AGNES_INTERNAL_TOKEN_FILE",
                       os.path.join(AGENT_ROOT, "data/internal_token"))
GENERATOR = os.environ.get("AGNES_VIDEO_GEN",
                           os.path.join(AGENT_ROOT, "tools_deliver/gen_kzqs_videos.py"))
VENV_PY = os.path.join(AGENT_ROOT, ".venv/bin/python3")

IMG_SIZE = "2K"
IMG_RATIO = "16:9"
IMG_TIMEOUT = "1800"
MAX_WORKERS = 0          # 0 = 不限并发（reference 模式由后端轮询/重试承接）
MAX_RENDER_WORKERS = 0   # 0 = 不限并发（各段互不依赖，一次全量提交）

_log_lock = threading.Lock()
_LOGF = None


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    with _log_lock:
        print(line, flush=True)
        if _LOGF:
            try:
                with open(_LOGF, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except Exception:
                pass


def py_exe():
    if os.path.isfile(VENV_PY) and os.access(VENV_PY, os.X_OK):
        return VENV_PY
    return sys.executable or "python3"


def load_plan(pdir):
    p = os.path.join(pdir, "投喂稿", "kf_plan.json")
    if not os.path.isfile(p):
        raise SystemExit("!! 缺 投喂稿/kf_plan.json —— 先写投喂稿并过 validate_prompt.py 机检，"
                         "再由机检结果生成 kf_plan.json（见 SKILL.md 步骤 ④）")
    with open(p, encoding="utf-8") as f:
        return json.load(f), p


def seg_kf_dir(pdir, seg):
    m = re.match(r"^(E\d+)-(\d+)$", seg or "")
    return os.path.join(pdir, "投喂稿", m.group(1), seg) if m else ""


def seg_mp4(pdir, seg):
    return os.path.join(pdir, seg, "%s.mp4" % seg)


def is_relay(s):
    return bool(str(s.get("kf1_from") or "").strip())


# ---------------------------------------------------------------- 出帧


def gen_frame(pdir, s, frame, env):
    """出一张关键帧。成功返回 (True, 路径)，失败返回 (False, 原因)。"""
    seg = s["seg"]
    od = s.get("outdir") or seg_kf_dir(pdir, seg)
    if not od:
        return False, "无法确定帧目录（段名非法）"
    os.makedirs(od, exist_ok=True)
    dst = os.path.join(od, frame + ".png")

    prompt = str(s.get("%s_prompt" % frame) or "").strip()
    if not prompt:
        return False, "kf_plan 缺 %s_prompt" % frame

    refs = [str(r) for r in (s.get("refs") or []) if os.path.isfile(str(r))]
    cmd = [py_exe(), MEDIA, "image", "--prompt", prompt,
           "--size", IMG_SIZE, "--ratio", IMG_RATIO, "--timeout", IMG_TIMEOUT]
    for r in refs:
        cmd += ["--ref", r]

    log("%s/%s 出图开始 refs=%d" % (seg, frame, len(refs)))
    t0 = time.time()
    try:
        pr = subprocess.run(cmd, capture_output=True, text=True,
                            timeout=int(IMG_TIMEOUT) + 300, env=env)
    except subprocess.TimeoutExpired:
        return False, "出图超时（%ss）" % (int(IMG_TIMEOUT) + 300)
    out = (pr.stdout or "") + (pr.stderr or "")
    fm = re.search(r"FILE=(\S+)", out)
    if pr.returncode == 0 and fm and os.path.isfile(fm.group(1).strip()):
        shutil_copy(fm.group(1).strip(), dst)
        kb = os.path.getsize(dst) // 1024 if os.path.isfile(dst) else 0
        log("%s/%s OK (%ds, %dKB) -> %s" % (seg, frame, int(time.time() - t0), kb, dst))
        return True, dst
    return False, "rc=%d %s" % (pr.returncode, out[-260:].replace("\n", " "))


def shutil_copy(src, dst):
    import shutil
    shutil.copy(src, dst)


# ---------------------------------------------------------------- 渲影


def build_manifest(pdir, ep):
    """生成渲染 manifest（.review/<EP>.manifest.json）。

    优先用 app.server.api.drama_review._rebuild_manifest（从投喂稿 md 拼 prompt，
    与审核页/级联重渲同一份逻辑）；拿不到则回落到已有的 投喂稿/<EP>/manifest.json。
    """
    out = os.path.join(pdir, ".review", "%s.manifest.json" % ep)
    os.makedirs(os.path.dirname(out), exist_ok=True)

    if AGENT_ROOT not in sys.path:
        sys.path.insert(0, AGENT_ROOT)
    try:
        from app.server.api.drama_review import _rebuild_manifest
        m = _rebuild_manifest(pdir, ep)
        if m and m.get("segments"):
            m["project_dir"] = pdir
            with open(out, "w", encoding="utf-8") as f:
                json.dump(m, f, ensure_ascii=False, indent=2)
            log("manifest 重建 OK（_rebuild_manifest，%d 段）-> %s" % (len(m["segments"]), out))
            return out
    except Exception as e:
        log("!! _rebuild_manifest 不可用（%s: %s），回落到落盘 manifest" % (type(e).__name__, e))

    fb = os.path.join(pdir, "投喂稿", ep, "manifest.json")
    if os.path.isfile(fb):
        with open(fb, encoding="utf-8") as f:
            m = json.load(f)
        if m.get("segments"):
            m["project_dir"] = pdir
            with open(out, "w", encoding="utf-8") as f:
                json.dump(m, f, ensure_ascii=False, indent=2)
            log("manifest 回落 OK（投喂稿/%s/manifest.json，%d 段）" % (ep, len(m["segments"])))
            return out
    return None


_manifest_cache = {}


def manifest_for(pdir, ep):
    if ep not in _manifest_cache:
        _manifest_cache[ep] = build_manifest(pdir, ep)
    return _manifest_cache[ep]


def render_seg(pdir, ep, seg, env, force=False):
    """渲染单段（帧齐自动渲 / 显式级联重渲共用）。返回 (state, msg)。"""
    mp4 = seg_mp4(pdir, seg)
    if not force and os.path.isfile(mp4) and os.path.getsize(mp4) > 0:
        return "skip", "已存在段视频（改帧不自动渲）"
    mf = manifest_for(pdir, ep)
    if not mf:
        return "fail", "无可用渲染 manifest（投喂稿 md 缺段或 _rebuild_manifest 失败）"

    log("%s 渲染开始（reference 模式）" % seg)
    t0 = time.time()
    try:
        # 必须 ≥ 驱动的 VIDEO_TIMEOUT(356400) + 余量，否则会把仍在后端重试的任务
        # 当成超时掐掉，制造重复提交。
        pr = subprocess.run([py_exe(), GENERATOR, mf, seg],
                            capture_output=True, text=True, timeout=360000, env=env)
    except subprocess.TimeoutExpired:
        return "fail", "本地等待超时（100h，后端可能仍在自动重试；勿重复提交）"
    tail = ((pr.stdout or "") + (pr.stderr or ""))[-300:].replace("\n", " ")
    if pr.returncode == 0 and os.path.isfile(mp4) and os.path.getsize(mp4) > 0:
        kb = os.path.getsize(mp4) // 1024
        log("%s 渲染 OK (%ds, %dKB)" % (seg, int(time.time() - t0), kb))
        return "ok", "%dKB" % kb
    return "fail", "rc=%d %s" % (pr.returncode, tail)


def refs_ready(pdir, s):
    """reference 集就绪判据。返回 (ready, 说明)。

    就绪 = kf_plan 里有 ≥1 张可用 refs（人物形象基准 / 场景母版）。
    kf2 **不是必需**（晨光 30 段仅 1 段有 kf2，当门禁会挡掉 29 段）；driver 会把
    kf2 当可选构图槽接在 refs 之后。不再判「kf1+kf2 齐」。
    """
    seg = s["seg"]
    refs = [r for r in (s.get("refs") or []) if os.path.isfile(r)]
    if not refs:
        return False, "kf_plan 无可用 refs（该段未编排人物/场景基准图）"
    od = s.get("outdir") or seg_kf_dir(pdir, seg)
    kf2 = os.path.join(od, "kf2.png")
    extra = "，另有 kf2 构图" if (os.path.isfile(kf2) and os.path.getsize(kf2) > 0) else ""
    return True, "refs %d 张%s" % (min(len(refs), 5), extra)


def render_all(pdir, picked, env, rworkers, force_render=False, dry_run=False):
    """阶段二：reference 集就绪即渲。各段互不依赖，**一次全量并发提交**。

    旧实现按 `kf1_from` 接力依赖分波、并把并发硬压到 ≤2。reference 模式下段与段
    之间没有数据依赖（段缝由上一段末帧自然承接），分波与并发上限都失去依据：
    提交后由 media.py 轮询、后端失败自动重建重试，所以只要提交成功即可。

    返回 {seg: (state, msg)}，state ∈ ok/fail/skip/wait/dry。
    """
    rworkers = rworkers if rworkers > 0 else len(picked) or 1
    rend = {}
    ready, waiting = [], []

    for ep, s in picked:
        seg = s["seg"]
        has_refs, why = refs_ready(pdir, s)
        if not has_refs:
            rend[seg] = ("wait", "reference 未就绪：%s" % why)
            log("%s 不渲：%s" % (seg, why))
            continue
        mp4 = seg_mp4(pdir, seg)
        if os.path.isfile(mp4) and os.path.getsize(mp4) > 0 and not force_render:
            rend[seg] = ("skip", "已出片")
            log("%s 不渲：已存在段视频（改帧不自动渲；--force-render 可重渲）" % seg)
            continue
        ready.append((ep, s))

    if not ready:
        return rend

    log("可渲 %d 段，并发 %d（不设硬上限）" % (len(ready), rworkers))
    if dry_run:
        for ep, s in ready:
            rend[s["seg"]] = ("dry", "reference 就绪")
            log("[dry-run] %s 将渲染（reference 模式）" % s["seg"])
        return rend

    with ThreadPoolExecutor(max_workers=rworkers) as ex:
        futs = {ex.submit(render_seg, pdir, ep, s["seg"], env, force_render): s["seg"]
                for ep, s in ready}
        for fut, seg in futs.items():
            try:
                st, msg = fut.result()
            except Exception as e:
                st, msg = "fail", "异常: %s: %s" % (type(e).__name__, e)
            rend[seg] = (st, msg)
            if st == "fail":
                log("!! %s 渲染失败：%s" % (seg, msg))
    return rend


# ---------------------------------------------------------------- 主流程


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="短剧关键帧批量生成 + 帧齐自动渲（帧齐自动渲 / 改帧不自动渲）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("project_dir", help="项目根绝对路径")
    ap.add_argument("--ep", default="", help="只处理某集，如 E01（默认全部）")
    ap.add_argument("--only", default="", help="只处理某段，如 E01-03（可逗号分隔）")
    ap.add_argument("--frames", default="kf1,kf2", help="要出的帧（默认 kf1,kf2）")
    ap.add_argument("--workers", type=int, default=0,
                    help="出图并发；0=不限（默认）。长帧词建议 1~2 以免请求堆积")
    ap.add_argument("--render-workers", type=int, default=0,
                    help="渲影并发；0=不限（默认，各段互不依赖，一次全量提交）")
    ap.add_argument("--no-auto-render", action="store_true", help="只出帧，不触发自动渲")
    ap.add_argument("--force-frame", action="store_true", help="已存在的帧也重出")
    ap.add_argument("--force-render", action="store_true", help="已存在段视频也重渲（级联重渲用）")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不调模型")
    a = ap.parse_args(argv)

    global _LOGF
    pdir = os.path.abspath(a.project_dir)
    if not os.path.isdir(pdir):
        raise SystemExit("!! 项目根不存在：%s（先跑 init_project.py）" % pdir)
    os.makedirs(os.path.join(pdir, ".review"), exist_ok=True)
    _LOGF = os.path.join(pdir, ".review", "kf_run.log")

    plan, plan_path = load_plan(pdir)
    only_segs = {x.strip() for x in a.only.split(",") if x.strip()}
    want_frames = [x.strip() for x in a.frames.split(",") if x.strip() in ("kf1", "kf2")]

    eps = plan if isinstance(plan, list) else plan.get("episodes", [])
    picked = []
    for epb in eps:
        ep = str(epb.get("episode") or "")
        if a.ep and ep.upper() != a.ep.upper():
            continue
        for s in epb.get("segments", []):
            if only_segs and s.get("seg") not in only_segs:
                continue
            picked.append((ep, s))
    if not picked:
        if not eps:
            raise SystemExit("!! 投喂稿/kf_plan.json 为空 —— 先写 投喂稿/<EP>/投喂稿-<EP>.md 并过 "
                             "validate_prompt.py 机检，再由机检结果生成 kf_plan.json（见 SKILL.md 步骤 ④）")
        raise SystemExit("!! kf_plan 里没筛到任何段：kf_plan 有 %d 集，但 --ep=%s / --only=%s 全部越界"
                         % (len(eps), a.ep or "*", a.only or "*"))

    workers = a.workers if a.workers > 0 else len(picked)
    rworkers = a.render_workers if a.render_workers > 0 else len(picked)

    env = dict(os.environ)
    if os.path.isfile(TOKEN):
        try:
            env["AGENT_INTERNAL_TOKEN"] = open(TOKEN, encoding="utf-8").read().strip()
        except Exception:
            pass
    env["AGENT_MEDIA_SOURCE"] = "short-drama-director"

    log("=" * 68)
    log("关键帧生成 + reference 集就绪自动渲  %s" % pdir)
    log("  段数=%d  帧=%s  出图并发=%d  渲影并发=%d（均不设硬上限）"
        % (len(picked), ",".join(want_frames), workers, rworkers))
    log("  自动渲=%s  force-frame=%s  force-render=%s  dry-run=%s"
        % (not a.no_auto_render, a.force_frame, a.force_render, a.dry_run))
    log("=" * 68)

    # ---------------- 阶段一：出帧 ----------------
    jobs = []
    for ep, s in picked:
        od = s.get("outdir") or seg_kf_dir(pdir, s["seg"])
        for fr in want_frames:
            if fr == "kf1" and is_relay(s):
                log("%s/kf1 跳过生成（接力段，渲染时从 %s 末帧抽取）"
                    % (s["seg"], s.get("kf1_from")))
                continue
            dst = os.path.join(od, fr + ".png")
            if os.path.isfile(dst) and os.path.getsize(dst) > 0 and not a.force_frame:
                log("%s/%s 已存在，跳过（--force-frame 可重出）" % (s["seg"], fr))
                continue
            jobs.append((ep, s, fr, dst))
    log("")
    log("阶段一 待出帧 %d 张（并发 %d）" % (len(jobs), workers))

    ok_jobs, fail_jobs = [], []
    if a.dry_run:
        for ep, s, fr, dst in jobs:
            log("  [dry-run] %s/%s -> %s" % (s["seg"], fr, dst))
    else:
        def one(j):
            ep, s, fr, dst = j
            r, why = gen_frame(pdir, s, fr, env)
            return (j, r, why)

        with ThreadPoolExecutor(max_workers=workers) as ex:
            for j, r, why in ex.map(one, jobs):
                (ok_jobs if r else fail_jobs).append((j, why))
    for j, why in fail_jobs:
        log("!! %s/%s 出帧失败：%s" % (j[1]["seg"], j[2], why))

    # ---------------- 阶段二：reference 集就绪自动渲 ----------------
    log("")
    if a.no_auto_render:
        log("阶段二 跳过（--no-auto-render）")
        rend = {}
    else:
        log("阶段二 reference 集就绪即渲（并发 %d，不分波、不设硬上限）" % rworkers)
        rend = render_all(pdir, picked, env, rworkers,
                          force_render=a.force_render, dry_run=a.dry_run)

    # ---------------- 账目 ----------------
    log("")
    log("=" * 68)
    log("阶段一 出帧：成功 %d / 失败 %d / 跳过（已存在或接力）%d"
        % (len(ok_jobs), len(fail_jobs), len(picked) * len(want_frames) - len(jobs)))
    if not a.no_auto_render:
        from collections import Counter
        c = Counter(v[0] for v in rend.values())
        log("阶段二 渲影：%s" % dict(c))
        for seg, (st, msg) in rend.items():
            if st in ("fail",):
                log("  !! %s 渲染失败：%s" % (seg, msg))
            elif st == "wait":
                log("  -- %s 未渲（%s）" % (seg, msg))
    log("")
    log("下一步：")
    log("  · 全部段视频就位 → bash concat_episodes.sh <项目根> %s 拼接 → qa_deliver.sh 门禁"
        % (a.ep or eps[0].get("episode") or "E01"))
    log("  · 有段要重做 → 审核页「改词重出」换帧后，bash gen_keyframes.py <项目根> --only <段号> "
        "--force-render 显式重渲（改帧不会自动烧额度）")
    log("=" * 68)
    return 1 if fail_jobs else 0


if __name__ == "__main__":
    sys.exit(main())
