#!/bin/bash
# qa_videos.sh <项目根> <EP>
# 核验 <EP>-NN/<EP>-NN.mp4：存在性、缺段检测、分辨率 16:9、时长 vs manifest、流构成
# 默认要求含 audio（视频自带配音=交付物）；NO_AUDIO=1 时反向验收：必须无 audio
# STATIC_FALLBACK 标记段（<段>/.STATIC_FALLBACK）豁免 audio 必检
# 退出码：0=全绿；1=有 FAIL；2=用法/文件缺失
set -u
R="$1"; EP="${2:-}"
[ -n "$EP" ] || { echo "用法: qa_videos.sh <项目根> <EP>"; exit 2; }
[ -d "$R" ] || { echo "缺项目目录: $R"; exit 2; }

echo "== $EP 段视频 QA (NO_AUDIO=${NO_AUDIO:-0}) =="

python3 - "$R" "$EP" "${NO_AUDIO:-0}" <<'PYEOF'
import json, os, re, subprocess, sys

R, EP, NOAUDIO = sys.argv[1], sys.argv[2], sys.argv[3]
fails = []
tol_ratio, tol_dur = 0.05, 1.0


def probe(f):
    """返回 (w, h, dur, [streams], size)"""
    def run(args):
        return subprocess.run(args, capture_output=True, text=True).stdout.strip()
    info = run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height", "-of", "csv=p=0", f])
    dur = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "csv=p=0", f])
    st = run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
              "-of", "csv=p=0", f])
    w, h = (info.split(",") + ["", ""])[:2] if info else ("", "")
    try:
        d = float(dur)
    except Exception:
        d = None
    return w, h, d, [x for x in st.split("\n") if x], (os.path.getsize(f) if os.path.exists(f) else 0)


# 期望时长：manifest 优先
exp = {}
mf = os.path.join(R, "投喂稿", EP, "manifest.json")
if os.path.exists(mf):
    m = json.load(open(mf, encoding="utf-8"))
    for s in m.get("segments", []):
        exp[s.get("seg")] = s.get("duration")
    print("-- manifest 段数: %d --" % len(exp))
else:
    print("-- 无 manifest.json，跳过时长比对 --")

# 实际段视频：<EP>-NN 目录（注意不能写成 ${EP}-0*，会漏掉 -10/-11/-12）
found = {}
base = os.path.join(R, "%s-%s" % (EP, r"\d\d"))
pat = re.compile(r"^%s-\d\d$" % re.escape(EP))
for name in sorted(os.listdir(R)):
    d = os.path.join(R, name)
    if pat.match(name) and os.path.isdir(d):
        v = os.path.join(d, "%s.mp4" % name)
        if os.path.exists(v):
            found[name] = v

for seg in sorted(found):
    f = found[seg]
    w, h, dur, streams, sz = probe(f)
    ds = "%.3f" % dur if dur is not None else "?"
    print("  %s | %sx%s | %ss | 流[%s] | %dB"
          % (seg, w, h, ds, ",".join(streams), sz))

    if not w or not h:
        fails.append("%s 无法探测分辨率" % seg)
    else:
        try:
            if abs(int(w) / int(h) - 16 / 9) > tol_ratio:
                fails.append("%s 分辨率 %sx%s 偏离 16:9 超 %.2f（交付实为 16:9 归一化前，"
                             "见 render-contracts.md §1.1）" % (seg, w, h, tol_ratio))
        except Exception:
            fails.append("%s 分辨率解析失败 %sx%s" % (seg, w, h))

    if dur is None:
        fails.append("%s 无法探测时长" % seg)
    elif seg in exp and exp[seg] is not None:
        if abs(dur - float(exp[seg])) > tol_dur:
            fails.append("%s 时长 %.3fs 与 manifest %ss 偏差 >%.1fs"
                         % (seg, dur, exp[seg], tol_dur))
    else:
        fails.append("%s 不在 manifest 段列表中（孤儿段视频）" % seg)

    # 流构成：默认必须含 audio（自带配音）；NO_AUDIO=1 反向；subtitle 一律禁
    static_fb = 0
    if os.path.exists(os.path.join(R, seg, "STATIC_FALLBACK")):
        static_fb = 1
        print("  [%s 静帧兜底段：豁免 audio 必检]" % seg)
    if NOAUDIO == "1":
        if "audio" in streams:
            fails.append("%s NO_AUDIO=1 但仍含 audio 流" % seg)
    else:
        if "audio" not in streams and not static_fb:
            fails.append("%s 缺 audio 流（视频应自带配音；静帧兜底段需带 STATIC_FALLBACK 标记）" % seg)
    if "subtitle" in streams:
        fails.append("%s 存在 subtitle 流（本链路不烧字幕）" % seg)

# 缺段检测
missing = [s for s in sorted(exp) if s not in found]
for s in missing:
    fails.append("%s manifest 有该段但无段视频产物" % s)
empty = [n for n in sorted(os.listdir(R))
         if pat.match(n) and os.path.isdir(os.path.join(R, n)) and n not in found]
for n in empty:
    fails.append("%s 目录存在但缺 %s.mp4" % (n, n))

print("-- 段视频数: %d / 期望 %d --" % (len(found), len(exp) or len(found)))
if not found:
    fails.append("未找到任何段视频（期望 %s/%s-NN/%s-NN.mp4）" % (R, EP, EP))

print()
if fails:
    print("===== qa_videos %s 失败 %d 项 =====" % (EP, len(fails)))
    for f in fails:
        print("  - %s" % f)
    sys.exit(1)
print("===== qa_videos %s 全绿 ✓ =====" % EP)
PYEOF
exit $?
