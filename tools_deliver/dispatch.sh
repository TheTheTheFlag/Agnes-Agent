#!/bin/bash
# dispatch.sh — 后台投递一条任务消息到 drama 线程 d2d55eb2
# 用法: bash dispatch.sh <msgfile> <logname>
cd /home/mirror/Agnes-Agent || exit 1
MSG="$1"; LOGN="$2"
if [ -z "$LOGN" ]; then LOGN="task-$(date +%H%M%S)"; fi
nohup python3 tools_deliver/deliver_task.py \
  --thread d2d55eb2-4e15-4f44-9b73-88bd01783bbf \
  --msgfile "$MSG" \
  --log "logs/tasks/$LOGN.log" < /dev/null > "logs/tasks/$LOGN.nohup" 2>&1 &
echo "DISPATCHED $MSG -> $LOGN"
exit 0