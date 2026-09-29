#!/bin/bash
# qa_deliver.sh <项目目录> <EP1> [EP2] ... [最大总时长秒，默认240]
# 远端执行。QA（manifest+段视频）→ 拼接（带备份）→ 验收（整集+连播 时长/分辨率/流构成）
# 退出码：0=全绿；1=有 FAIL；2=用法/目录缺失
# 依赖同目录 qa_manifest.sh / qa_videos.sh / concat_episodes.sh
set -u
R="$1"; shift
if [ $# -lt 1 ]; then
  echo "用法: qa_deliver.sh <项目目录> <EP1> [EP2] ... [最大总时长秒]"; exit 2
fi
MAXSEC=240
# 末位若是纯数字则视为时长上限
LASTF="${!#}"
if [[ "$LASTF" =~ ^[0-9]+$ ]]; then
  MAXSEC="$LASTF"
  set -- "${@:1:$#-1}"
fi
EPISODES=("$@")
[ -d "$R" ] || { echo "缺项目目录: $R"; exit 2; }
[ ${#EPISODES[@]} -gt 0 ] || { echo "未指定集号"; exit 2; }

HERE="$(cd "$(dirname "$0")" && pwd)"
QM="$HERE/qa_manifest.sh"; QV="$HERE/qa_videos.sh"; CC="$HERE/concat_episodes.sh"
for f in "$QM" "$QV" "$CC"; do
  [ -f "$f" ] || { echo "缺依赖脚本: $f"; exit 2; }
done

FAIL=()
echo "############## 1. manifest QA ##############"
for EP in "${EPISODES[@]}"; do
  echo "---------- $EP ----------"
  bash "$QM" "$R" "$EP" "$MAXSEC" || FAIL+=("qa_manifest $EP 未通过")
done

echo "############## 2. 段视频 QA ##############"
for EP in "${EPISODES[@]}"; do
  echo "---------- $EP ----------"
  bash "$QV" "$R" "$EP" || FAIL+=("qa_videos $EP 未通过")
done

echo "############## 3. 拼接 ##############"
bash "$CC" "$R" "${EPISODES[@]}" || FAIL+=("concat 失败")

echo "############## 4. 验收 ##############"
# 2026-09-28 重写为 python 主体。原 bash+awk 版本两处容差判断方向写反
# （`exit !(d>tol)` 在「未超容差」时才返回失败；时长那处更是「超上限反而通过」），
# 且空值会被 awk 当 0 静默放行。
python3 - "$R" "$MAXSEC" "${KEEP_AUDIO:-0}" "${EPISODES[@]}" <<'PYEOF'
import json, os, subprocess, sys

R, MAXSEC, KEEP = sys.argv[1], int(sys.argv[2]), sys.argv[3]
eps = sys.argv[4:]
fails = []


def probe(f):
    def run(a):
        return subprocess.run(a, capture_output=True, text=True).stdout.strip()
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
    return w, h, d, [x for x in st.split("\n") if x]


targets = [os.path.join(R, "%s.mp4" % e) for e in eps] + [os.path.join(R, "连播.mp4")]

for f in targets:
    b = os.path.basename(f)
    if not os.path.exists(f):
        fails.append("验收目标缺失: %s" % f)
        continue
    w, h, dur, streams = probe(f)
    print("  %s | %sx%s | %ss | 流[%s]"
          % (b, w, h, "%.3f" % dur if dur is not None else "?", ",".join(streams)))

    if not w or not h:
        fails.append("%s 无法探测分辨率" % b)
    else:
        try:
            if abs(int(w) / int(h) - 16 / 9) > 0.05:
                fails.append("%s 分辨率 %sx%s 偏离 16:9 超 0.05" % (b, w, h))
        except Exception:
            fails.append("%s 分辨率解析失败 %sx%s" % (b, w, h))

    if dur is None:
        fails.append("%s 无法探测时长" % b)
    elif dur > MAXSEC:
        fails.append("%s 时长 %.2fs > 上限 %ds" % (b, dur, MAXSEC))

    if KEEP == "1":
        if "audio" not in streams:
            fails.append("%s KEEP_AUDIO=1 但无 audio 流" % b)
    else:
        if "audio" in streams:
            fails.append("%s 存在 audio 流（渲染应已 -an 去模型音轨）" % b)
        if "subtitle" in streams:
            fails.append("%s 存在 subtitle 流（本链路不烧字幕）" % b)

# 时长账目：manifest 段时长合计 vs 连播实测（对账，不阻断）
want = 0
for ep in eps:
    mf = os.path.join(R, "投喂稿", ep, "manifest.json")
    if os.path.exists(mf):
        m = json.load(open(mf, encoding="utf-8"))
        want += sum(int(s.get("duration") or 0) for s in m.get("segments", []))
lp = os.path.join(R, "连播.mp4")
if want and os.path.exists(lp):
    _, _, gd, _ = probe(lp)
    if gd is not None:
        print("  时长账目: manifest 合计 %ds vs 连播实测 %.2fs（差 %+.2fs，不阻断）"
              % (want, gd, gd - want))

if fails:
    print("  -- 验收细项失败 %d 项（并入总 FAIL）--" % len(fails))
    for x in fails:
        print("    - %s" % x)
    sys.exit(1)
PYEOF
[ $? -ne 0 ] && FAIL+=("交付验收细项未通过（见上）")

echo
if [ ${#FAIL[@]} -gt 0 ]; then
  echo "===== 验收失败 ${#FAIL[@]} 项 ====="
  for f in "${FAIL[@]}"; do echo "  - $f"; done
  exit 1
fi
echo "===== 验收全绿 ✓ ====="
exit 0
