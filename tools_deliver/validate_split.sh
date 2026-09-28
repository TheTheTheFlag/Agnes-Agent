#!/bin/bash
# split_validate.sh <md路径> — 按 ## E01- 段头拆分投喂稿，逐段 validate_prompt.py --model agnes；汇总 FAIL 段
set -u
MD="$1"
BASE="/home/mirror/Agnes-Agent/app/skills/short-drama-director"
DIR=$(dirname "$MD")
TMP="$DIR/.validate"
rm -rf "$TMP"; mkdir -p "$TMP"
cd /home/mirror/Agnes-Agent

python3 - "$MD" "$TMP" <<'PYEOF'
import re, os, sys
txt = open(sys.argv[1], encoding="utf-8").read()
tmp = sys.argv[2]
parts = re.split(r"(?m)^## (E0\d-\d+)\s*·", txt)
segs = {}
cur = None
for tok in parts:
    if re.fullmatch(r"E0\d-\d+", tok):
        cur = tok; segs[cur] = []
    elif cur is not None:
        segs[cur].append(tok)
for s, body in segs.items():
    open(os.path.join(tmp, s + ".md"), "w", encoding="utf-8").write("".join(body).strip() + "\n")
print("拆分段数:", len(segs), "->", sorted(segs.keys()))
PYEOF

echo "===== 逐段机检（--model agnes）====="
FAIL_TOTAL=0
for f in "$TMP"/*.md; do
  seg=$(basename "$f" .md)
  echo "---- [$seg] ----"
  python3 "$BASE/scripts/validate_prompt.py" "$f" --model agnes 2>&1 | tail -25
  rc=${PIPESTATUS[0]}
  if [ "$rc" -ne 0 ]; then echo "[$seg] ==> 存在 FAIL（rc=$rc）"; FAIL_TOTAL=$((FAIL_TOTAL+1)); else echo "[$seg] ==> 通过"; fi
done
echo "===== FAIL 段数: $FAIL_TOTAL / $(ls "$TMP"/*.md | wc -l) ====="
exit "$FAIL_TOTAL"