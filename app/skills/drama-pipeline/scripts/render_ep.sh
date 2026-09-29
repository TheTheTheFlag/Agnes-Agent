#!/bin/bash
# render_ep.sh <manifest.json> <段号> [段号...] — 渲染指定段（tools_deliver 驱动，16:9 720p 注入）
# 远端执行；先 export AGENT_INTERNAL_TOKEN；manifest 需已由 rebuild_manifest.sh 重建
set -u
export AGENT_INTERNAL_TOKEN=$(cat /home/mirror/Agnes-Agent/data/internal_token)
G=/home/mirror/Agnes-Agent/tools_deliver/gen_kzqs_videos.py
MF="$1"; shift
[ -f "$MF" ] || { echo "缺 manifest: $MF"; exit 2; }
[ -f "$G" ] || { echo "缺驱动: $G（须用 tools_deliver 版，含 1280x720 注入）"; exit 2; }
rc=0
for seg in "$@"; do
  echo "===== $seg ====="
  python3 "$G" "$MF" "$seg" || { echo "[FAIL] $seg"; rc=1; }
done
echo "render done rc=$rc"
exit $rc