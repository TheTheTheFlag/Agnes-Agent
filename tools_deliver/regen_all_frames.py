#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""重建全量首尾帧（首尾帧模式定稿）：
- 每段 kf2.png：用帧专用提示词 kf2_prompt 出图（30 张，叙事落点，两端可控锚定）
- E01-01 kf1.png：用帧专用提示词 kf1_prompt 出图（1 张，全片唯一独立首帧）
- 段 N>=2 kf1.png：直接复制上游段 kf2.png（像素级段缝一致，不重新生成）
渲染器以 [kf1, kf2] keyframes 双图插值 → 坏帧被尾帧拉回，不跨段传染。

用法: python3 regen_all_frames.py [seg,seg,...]   日志: <项目>/.review/kf_regen2.log
  省略 segs 则重建全部 30 段；指定则只重建这些段（复制依赖按段序自动满足）。
"""
import json, os, re, shutil, subprocess, sys, time

PROJ = "/home/mirror/Agnes-Agent/data/users/Mirror/deliverables/短剧/晨光与咖啡心动予你"
MEDIA = "/home/mirror/Agnes-Agent/app/skills_builtin/agnes-media/media.py"
TOKFILE = "/home/mirror/Agnes-Agent/data/internal_token"
KFP = os.path.join(PROJ, "投喂稿", "kf_plan.json")
REVIEW = os.path.join(PROJ, ".review")
os.makedirs(REVIEW, exist_ok=True)
LOGF = os.path.join(REVIEW, "kf_regen2.log")

def log(s):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), s)
    print(line, flush=True)
    with open(LOGF, "a", encoding="utf-8") as f:
        f.write(line + "\n")

data = json.load(open(KFP, encoding="utf-8"))
flat_all = [s for ep in data for s in ep.get("segments", [])]
# 可选段筛选
want = [x.strip() for x in sys.argv[1:] if x.strip()] if len(sys.argv) > 1 else []
if want:
    order_all = [s["seg"] for s in flat_all]
    keep = set(want)
    # 保留复制依赖所需的上游段（kf1_from=copy:xxx:kf2 的 xxx）
    extra = set()
    for it in flat_all:
        kf1_from = it.get("kf1_from")
        if str(kf1_from or "").startswith("copy:") and it["seg"] in keep:
            extra.add(str(kf1_from).split(":")[1])
    keep |= extra
    flat = [it for it in flat_all if it["seg"] in keep]
else:
    flat = flat_all[:]
od_map = {s["seg"]: os.path.join(PROJ, "投喂稿", s["seg"].split("-")[0], s["seg"]) for s in flat}
log("===== regen_all_frames 开始：全量帧重建（首尾帧模式） =====")
log("处理段数 %d（含复制依赖上游段）: %s" % (len(flat), ",".join(s["seg"] for s in flat)))

# ---- 1. 清空现有 frame（kf1/kf2 全清，避免旧文件混入） ----
n_del = 0
for it in flat:
    od = od_map[it["seg"]]
    for f in ("kf1.png", "kf2.png"):
        p = os.path.join(od, f)
        if os.path.isfile(p):
            os.remove(p)
            n_del += 1
log("已清空 %d 张旧帧" % n_del)

tok = ""
try:
    tok = open(TOKFILE, encoding="utf-8").read().strip()
except Exception:
    pass
env = dict(os.environ)
if tok:
    env["AGENT_INTERNAL_TOKEN"] = tok

def gen_image(seg, frame, prompt, refs):
    """AI 出图一张关键帧，存在 outdir/<frame>.png。"""
    od = od_map[seg]
    os.makedirs(od, exist_ok=True)
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
        return False
    out = (pr.stdout or "") + (pr.stderr or "")
    fm = re.search(r"FILE=(\S+)", out)
    if pr.returncode == 0 and fm and os.path.isfile(fm.group(1).strip()):
        shutil.copy(fm.group(1).strip(), dst)
        kb = os.path.getsize(dst) // 1024 if os.path.isfile(dst) else 0
        log("%s/%s OK (%ds, %dKB) -> %s" % (seg, frame, int(time.time() - t0), kb, dst))
        return True
    log("%s/%s FAIL rc=%d %s" % (seg, frame, pr.returncode, out[-300:]))
    return False

# ---- 2. 按段序处理：每段 kf1、kf2 全部独立 AI 出图（段缝不复制/不接力） ----
ok = fail = 0
for it in flat:
    seg = it["seg"]
    # kf2 永远 AI 出图（帧专用词）
    if gen_image(seg, "kf2", str(it.get("kf2_prompt") or ""), it.get("refs") or []):
        ok += 1
    else:
        fail += 1
    # kf1 一律独立出图（帧专用词），不再复制上游 kf2
    if gen_image(seg, "kf1", str(it.get("kf1_prompt") or ""), it.get("refs") or []):
        ok += 1
    else:
        fail += 1
    time.sleep(1)

log("===== 帧重建完成: 成功 %d, 失败 %d =====" % (ok, fail))
sys.exit(0 if fail == 0 else 3)