#!/bin/bash
# concat_episodes.sh <项目根> <EP1> [EP2] ...
# 各集内 EPN-NN/NN.mp4 逐段拼接为 EPN.mp4，再把各集拼成 连播.mp4
# 覆盖前自动备份到 <项目根>/.review/merge_backup/ 并登记 INDEX.jsonl（路径 + sha256）
# 退出码：0=全绿；1=有 FAIL/拼接失败；2=用法/目录缺失
set -u
R="$1"; shift
EPISODES=("$@")
[ -d "$R" ] || { echo "缺项目目录: $R"; exit 2; }
[ ${#EPISODES[@]} -gt 0 ] || { echo "用法: concat_episodes.sh <项目根> <EP1> [EP2] ..."; exit 2; }

BK="$R/.review/merge_backup"
mkdir -p "$BK"
IDX="$BK/INDEX.jsonl"
ts=$(date +%Y%m%d-%H%M%S)
FAIL=()

backup_one() {
  f="$1"
  [ -f "$f" ] || return 0
  b="$BK/$(basename "$f").$ts.bak"
  cp -p "$f" "$b" || return 1
  sha=$(sha256sum "$f" | cut -d' ' -f1)
  printf '{"file":"%s","backup":"%s","sha256":"%s","at":"%s"}\n' \
    "$f" "$b" "$sha" "$(date -Is)" >> "$IDX"
  echo "  [备份] $(basename "$f") -> $(basename "$b")"
}

# 输出时长 vs 输入段时长合计 对账（容差 0.5s）。静默丢段是本脚本最阴的失败模式：
# 2026-09-28 实测用 "$ep"-0* 通配，只匹配到 -01..-09，漏掉 -10..-12，
# 拼出 132s 的"成品"却 exit 0。
dur_of() { ffprobe -v error -show_entries format=duration -of csv=p=0 "$1" 2>/dev/null; }

cd "$R" || exit 2

for ep in "${EPISODES[@]}"; do
  list="$R/.concat_$ep.txt"
  : > "$list"
  n=0
  sum=0
  # ⚠️ 不能写 "$ep"-0*：段号位数不定（01..16），会漏掉 -10..-16。
  # 用 find + 纯数字后缀校验，稳定排序。
  for d in $(find "$R" -maxdepth 1 -mindepth 1 -type d -name "$ep-*" -printf '%f\n' 2>/dev/null | sort); do
    suf="${d#"$ep-"}"
    case "$suf" in ''|*[!0-9]*) continue ;; esac
    f="$R/$d/$d.mp4"
    if [ ! -f "$f" ]; then
      echo "  !! $d 目录存在但缺 $d.mp4" >&2
      FAIL+=("$ep/$d 段视频缺失")
      continue
    fi
    printf "file '%s'\n" "$f" >> "$list"
    n=$((n+1))
    sd=$(dur_of "$f")
    case "$sd" in ''|*[!0-9.]*) : ;; *) sum=$(python3 -c "print($sum+$sd)") ;; esac
  done
  if [ "$n" -lt 1 ]; then
    echo "!! $ep 未找到段视频" >&2
    FAIL+=("$ep 无任何段视频")
    continue
  fi

  # 段数与 manifest 对账
  want_n=$(python3 - "$R" "$ep" <<'PYEOF' 2>/dev/null || echo 0
import json, os, sys
p = os.path.join(sys.argv[1], '投喂稿', sys.argv[2], 'manifest.json')
print(len(json.load(open(p, encoding='utf-8')).get('segments', [])) if os.path.exists(p) else 0)
PYEOF
)
  if [ "$want_n" != "0" ] && [ "$want_n" != "$n" ]; then
    echo "!! $ep 段数不符：磁盘 $n 段 vs manifest $want_n 段" >&2
    FAIL+=("$ep 段数磁盘 $n ≠ manifest $want_n")
  fi

  echo "== $ep: $n 段 开始拼接（输入合计 ${sum}s）=="
  backup_one "$R/$ep.mp4"
  if ! ffmpeg -y -f concat -safe 0 -i "$list" -c copy "$ep.mp4" > /dev/null 2>&1; then
    ffmpeg -y -f concat -safe 0 -i "$list" -c:v libx264 -crf 20 -preset veryfast "$ep.mp4" \
      || { echo "!! $ep 拼接失败" >&2; FAIL+=("$ep 拼接失败"); continue; }
  fi
  od=$(dur_of "$ep.mp4")
  echo "   -> $(basename "$ep.mp4") ${od}s $(ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=p=0 "$ep.mp4" 2>/dev/null)"
  # 丢段检测：输出时长必须≈输入段合计
  if [ -n "$od" ] && [ -n "$sum" ] && [ "$sum" != "0" ]; then
    python3 -c "
import sys
a,b=float('$od'),float('$sum')
sys.exit(0 if abs(a-b)<=0.5 else 1)" || FAIL+=("$ep 拼接后 ${od}s 与输入段合计 ${sum}s 差 >0.5s（疑似丢段）")
  fi
  rm -f "$list"
done

# 全集拼接
all="$R/.concat_all.txt"
: > "$all"
m=0
for ep in "${EPISODES[@]}"; do
  [ -f "$R/$ep.mp4" ] || { FAIL+=("$ep 整集文件缺失，无法连播"); continue; }
  printf "file '%s.mp4'\n" "$ep" >> "$all"
  m=$((m+1))
done
OUT="$R/连播.mp4"
if [ "$m" -ge 1 ]; then
  echo "== $m 集连播 拼接 -> $(basename "$OUT") =="
  backup_one "$OUT"
  if ! ffmpeg -y -f concat -safe 0 -i "$all" -c copy "$OUT" > /dev/null 2>&1; then
    ffmpeg -y -f concat -safe 0 -i "$all" -c:v libx264 -crf 20 -preset veryfast "$OUT" \
      || { echo "!! 连播拼接失败" >&2; FAIL+=("连播拼接失败"); }
  fi
  [ -f "$OUT" ] && ls -la "$OUT"
  rm -f "$all"
else
  echo "!! 无可拼接的整集文件" >&2
  FAIL+=("无任何整集文件")
fi
echo "== 备份索引: $IDX =="

if [ ${#FAIL[@]} -gt 0 ]; then
  echo "===== concat 失败 ${#FAIL[@]} 项 ====="
  for f in "${FAIL[@]}"; do echo "  - $f"; done
  exit 1
fi
exit 0
