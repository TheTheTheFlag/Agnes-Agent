#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""彻底清空重出（尾帧接力最小集）：备份全部帧 → 删除全部 kf1/kf2 →
按 kf_plan 出图词串行重出：每段 kf2（30 张，叙事落点）+ E01-01 kf1（1 张，全片独立首帧）。
段 N≥2 的首帧由渲染时自动抽上段视频真实末帧，不再提前出图。

用法: python3 regen_kfs.py
日志: <项目>/.review/kf_regen.log
"""
import json, os, re, shutil, subprocess, sys, time

PROJ = "/home/mirror/Agnes-Agent/data/users/Mirror/deliverables/短剧/晨光与咖啡心动予你"
MEDIA = "/home/mirror/Agnes-Agent/app/skills_builtin/agnes-media/media.py"
TOKFILE = "/home/mirror/Agnes-Agent/data/internal_token"
KFP = os.path.join(PROJ, "投喂稿", "kf_plan.json")
REVIEW = os.path.join(PROJ, ".review")
os.makedirs(REVIEW, exist_ok=True)
LOGF = os.path.join(REVIEW, "kf_regen.log")

def log(s):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), s)
    print(line, flush=True)
    try:
        with open(LOGF, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

data = json.load(open(KFP, encoding="utf-8"))
flat = [s for ep in data for s in ep.get("segments", [])]
log("===== regen_kfs 开始：备份/删除/重出 =====")
log("总段数 %d" % len(flat))

# ---- 1. 备份 ----
bak = os.path.join(REVIEW, "kf_backup", time.strftime("%Y%m%d_%H%M%S"))
os.makedirs(bak, exist_ok=True)
n_backed = 0
for it in flat:
    od = str(it.get("outdir") or "")
    if not os.path.isdir(od):
        continue
    sd = os.path.join(bak, it["seg"])
    os.makedirs(sd, exist_ok=True)
    for f in ("kf1.png", "kf2.png"):
        p = os.path.join(od, f)
        if os.path.isfile(p):
            shutil.copy2(p, os.path.join(sd, f))
            n_backed += 1
log("备份 %d 张 -> %s" % (n_backed, bak))

# ---- 2. 删除全部帧 ----
n_del = 0
for it in flat:
    od = str(it.get("outdir") or "")
    for f in ("kf1.png", "kf2.png"):
        p = os.path.join(od, f)
        if os.path.isfile(p):
            os.remove(p)
            n_del += 1
log("已删除 %d 张（kf1/kf2 全清）" % n_del)

# ---- 3. 出图任务清单 ----
jobs = []
for it in flat:
    seg = it["seg"]
    p2 = str(it.get("kf2_prompt") or "").strip()
    if p2:
        jobs.append((seg, "kf2", p2, it.get("refs") or []))
    # 独立首帧：kf1_from 为空的段（仅 E01-01）才需要提前出图 kf1
    if not it.get("kf1_from"):
        p1 = str(it.get("kf1_prompt") or "").strip()
        if p1:
            jobs.append((seg, "kf1", p1, it.get("refs") or []))
outdir_map = {it["seg"]: str(it.get("outdir") or "") for it in flat}
log("出图任务 %d 张（每段 kf2 + E01-01 kf1；段N≥2 首帧由渲染接力生成）" % len(jobs))

tok = ""
try:
    tok = open(TOKFILE, encoding="utf-8").read().strip()
except Exception:
    pass
env = dict(os.environ)
if tok:
    env["AGENT_INTERNAL_TOKEN"] = tok
env["AGENT_MEDIA_SOURCE"] = "short-drama-director"

ok_cnt = fail_cnt = 0
for seg, frame, prompt, refs in jobs:
    od = outdir_map.get(seg, "")
    if not od or not os.path.isdir(od):
        log("%s/%s outdir 缺失，跳过" % (seg, frame))
        fail_cnt += 1
        continue
    dst = os.path.join(od, frame + ".png")
    cmd = ["python3", MEDIA, "image", "--prompt", prompt,
           "--size", "2K", "--ratio", "16:9", "--timeout", "1800"]
    refs_ok = [str(r) for r in (refs or []) if os.path.isfile(str(r))]
    for r in refs_ok:
        cmd += ["--ref", r]
    log("%s/%s 出图开始 dur_refs=%d" % (seg, frame, len(refs_ok)))
    t0 = time.time()
    try:
        pr = subprocess.run(cmd, capture_output=True, text=True, timeout=2100, env=env)
    except subprocess.TimeoutExpired:
        log("%s/%s 出图超时(35min)" % (seg, frame))
        fail_cnt += 1
        continue
    out = (pr.stdout or "") + (pr.stderr or "")
    fm = re.search(r"FILE=(\S+)", out)
    if pr.returncode == 0 and fm and os.path.isfile(fm.group(1).strip()):
        shutil.copy(fm.group(1).strip(), dst)
        sz = os.path.getsize(dst) // 1024 if os.path.isfile(dst) else 0
        log("%s/%s OK (%ds, %dKB) -> %s" % (seg, frame, int(time.time() - t0), sz, dst))
        ok_cnt += 1
    else:
        log("%s/%s FAIL rc=%d %s" % (seg, frame, pr.returncode, out[-300:]))
        fail_cnt += 1
    time.sleep(2)

log("===== 重出完成: 成功 %d, 失败 %d =====" % (ok_cnt, fail_cnt))
sys.exit(0 if fail_cnt == 0 else 3)