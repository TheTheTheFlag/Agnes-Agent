#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""逐段流水线（并行版）：段级线程池并发，帧齐即渲影，不等全量帧。

流程（每段一个 worker）:
  1. 检查 <EP>/<seg>/kf1.png + kf2.png 是否齐整（存在且 >50KB）
  2. 缺帧 → 调 regen_all_frames.py <seg> 补齐该段两帧（段内两帧串行，段间并行）
  3. 帧齐 → 调 gen_kzqs_videos.py <EP manifest> <seg>（KEEP_AUDIO=1）渲影该段
  4. 下一段

并行规则（media.py 为 POST 建任务→轮询 异步模式，后端排队+自动重试）:
  - 段级线程池并发（默认 --workers 2，可调）；
  - 同刻多个段同时出图 / 多个段同时渲影均可；
  - 唯一硬约束：同一段不得同时开两个任务（段内 出帧→渲影 顺序，天然满足）。

日志: <项目>/.review/pipe_run.log
用法: python3 pipe_render.py --workers N [seg,seg,...]
  省略 segs 则按全集顺序跑；指定则只跑这些段。
"""
import json, os, re, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed

PROJ = "/home/mirror/Agnes-Agent/data/users/Mirror/deliverables/短剧/晨光与咖啡心动予你"
GEN = "/home/mirror/Agnes-Agent/tools_deliver/gen_kzqs_videos.py"
REGEN = "/home/mirror/Agnes-Agent/tools_deliver/regen_all_frames.py"
TOKFILE = "/home/mirror/Agnes-Agent/data/internal_token"
REVIEW = os.path.join(PROJ, ".review")
os.makedirs(REVIEW, exist_ok=True)
LOGF = os.path.join(REVIEW, "pipe_run.log")

WORKERS = 2
want = []
av = sys.argv[1:]
i = 0
while i < len(av):
    if av[i] == "--workers" and i + 1 < len(av):
        WORKERS = max(1, int(av[i + 1]))
        i += 2
        continue
    if re.match(r"^E\d+-\d+$", av[i]):
        want.append(av[i])
    i += 1

def log(s):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), s)
    print(line, flush=True)
    with open(LOGF, "a", encoding="utf-8") as f:
        f.write(line + "\n")

env = dict(os.environ)
env["KEEP_AUDIO"] = "1"   # 本项目特例：保留模型自带音轨
tok = ""
try:
    tok = open(TOKFILE, encoding="utf-8").read().strip()
except Exception:
    pass
if tok:
    env["AGENT_INTERNAL_TOKEN"] = tok

# 收集全部段（按集序、段序）
segs = []
for ep in sorted(os.listdir(os.path.join(PROJ, "投喂稿"))):
    mf = os.path.join(PROJ, "投喂稿", ep, "manifest.json")
    if not (ep.startswith("E") and os.path.isfile(mf)):
        continue
    m = json.load(open(mf, encoding="utf-8"))
    for s in m["segments"]:
        segs.append({"seg": s["seg"], "ep": ep})

if want:
    segs = [s for s in segs if s["seg"] in want]

log("===== pipe_render 并行流水开始，workers=%d，共 %d 段 =====" % (WORKERS, len(segs)))
log("顺序: %s" % ",".join(s["seg"] for s in segs))

def frames_ok(it):
    d = os.path.join(PROJ, "投喂稿", it["ep"], it["seg"])
    for f in ("kf1.png", "kf2.png"):
        p = os.path.join(d, f)
        if not os.path.isfile(p) or os.path.getsize(p) < 50 * 1024:
            return False
    return True

def process_seg(it):
    t0 = time.time()
    seg = it["seg"]
    if not frames_ok(it):
        log("%s 帧不全，出图补齐(并行)…" % seg)
        r = subprocess.run(["python3", REGEN, seg], capture_output=True, text=True, env=env, timeout=60 * 30)
        if r.returncode != 0:
            log("%s 补帧失败 rc=%d %s" % (seg, r.returncode, (r.stdout or "")[-200:] + (r.stderr or "")[-200:]))
            return (seg, False, "补帧失败")
        if not frames_ok(it):
            log("%s 补帧后仍不全，本轮跳过" % seg)
            return (seg, False, "补帧后帧仍不全")
    mf = os.path.join(PROJ, "投喂稿", it["ep"], "manifest.json")
    log("%s 帧齐，渲影开始(并行)…" % seg)
    r = subprocess.run(["python3", GEN, mf, seg], capture_output=True, text=True, env=env, timeout=60 * 60)
    out = (r.stdout or "") + (r.stderr or "")
    segfile = os.path.join(PROJ, seg, seg + ".mp4")
    head = out.replace("\n", " ")[-300:]
    if r.returncode == 0 and os.path.isfile(segfile) and os.path.getsize(segfile) > 100 * 1024:
        kb = os.path.getsize(segfile) // 1024
        log("%s 渲影 OK (%ds, %dKB) rc=%d" % (seg, int(time.time() - t0), kb, r.returncode))
        return (seg, True, "OK")
    log("%s 渲影 FAIL rc=%d %s" % (seg, r.returncode, head))
    return (seg, False, "渲影失败")

t_all = time.time()
results = []
with ThreadPoolExecutor(max_workers=WORKERS) as ex:
    futs = {ex.submit(process_seg, it): it["seg"] for it in segs}
    for f in as_completed(futs):
        try:
            results.append(f.result())
        except Exception as e:
            results.append((futs[f], False, "异常: %s" % e))

ok = sum(1 for r in results if r[1])
fail = len(results) - ok
log("===== pipe_render 完成: 成功 %d, 失败 %d, 总耗时 %dmin =====" % (ok, fail, int((time.time() - t_all) / 60)))
for r in results:
    log("  结果: %s %s %s" % (r[0], "OK" if r[1] else "FAIL", r[2]))
sys.exit(0 if fail == 0 else 3)