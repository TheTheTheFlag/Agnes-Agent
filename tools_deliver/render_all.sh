#!/bin/bash
# render_all.sh — 尾帧接力全片渲染（E01→E06 顺序，KEEP_AUDIO=1，单线程串行）
# 用法: bash render_all.sh   （日志: <项目>/.review/render_all.log）
PROJ="/home/mirror/Agnes-Agent/data/users/Mirror/deliverables/短剧/晨光与咖啡心动予你"
LOG="$PROJ/.review/render_all.log"
mkdir -p "$PROJ/.review"
export KEEP_AUDIO=1

echo "===== render_all $(date '+%F %T') =====" | tee -a "$LOG"
t0=$(date +%s)
for EP in E01 E02 E03 E04 E05 E06; do
  MF="$PROJ/投喂稿/$EP/manifest.json"
  if [ ! -f "$MF" ]; then
    echo "[$EP] manifest 缺失，跳过" | tee -a "$LOG"
    continue
  fi
  echo ">>> [$EP] 开始 $(date '+%T')" | tee -a "$LOG"
  python3 /home/mirror/Agnes-Agent/tools_deliver/gen_kzqs_videos.py "$MF" 2>&1 | tee -a "$LOG"
  rc=${PIPESTATUS[0]}
  echo ">>> [$EP] 结束 rc=$rc" | tee -a "$LOG"
  if [ "$rc" != "0" ]; then
    echo "[$EP] 失败（rc=$rc），暂停后续集" | tee -a "$LOG"
    exit 3
  fi
done
t1=$(date +%s)
echo "===== render_all 完成，耗时 $((t1-t0))s $(date '+%F %T') =====" | tee -a "$LOG"