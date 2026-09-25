#!/bin/bash
# pipeline_scripts.sh — 无人值守：剧本批次 START..END 流水线（跑完这批自动推进下一批）
# 用法: bash pipeline_scripts.sh [START] [END]   （默认 2 20）
# 策略：
#   每批独立新 UUID 线程投递（防同线程 ack 惯性）
#   投递后轮询日志直到 deliver_task 收尾；产物未落盘则换新线程重投（最多 3 次）
#   validate 未全绿 → 生成修复任务（新线程）重投（最多 3 次）
#   服务器不可用 → 等 60s 重试（最多 30 分钟窗口）；崩溃恢复后从状态文件续跑
# 状态: logs/tasks/pipeline-scripts.state（当前批次）；总日志 logs/tasks/pipeline-scripts.log
set -u
# —— 单实例锁：防止误启动多个驱动实例并发投递 ——
LOCK=/home/mirror/Agnes-Agent/logs/tasks/pipeline-scripts.lock
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "[$(date +%H:%M:%S)] 已有 pipeline_scripts 实例在跑，本次启动退出"
  exit 0
fi

BASE=/home/mirror/Agnes-Agent
D="$BASE/data/users/Mirror/deliverables/短剧/美人图"
TOOLSDIR="$BASE/tools_deliver"
LOGDIR="$BASE/logs/tasks"
SBDIR="$BASE/app/skills/novel-script/scripts"
SCRIPTS_MJS="$SBDIR/novel-script.mjs"
STATE="$LOGDIR/pipeline-scripts.state"
LOGF="$LOGDIR/pipeline-scripts.log"
START="${1:-2}"; END="${2:-20}"
EPS_PER_BATCH=3
mkdir -p "$TOOLSDIR" "$LOGDIR" "$D"

log(){ echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOGF"; }

new_thread(){ cat /proc/sys/kernel/random/uuid 2>/dev/null || python3 -c "import uuid;print(uuid.uuid4())"; }

wait_server(){
  for i in $(seq 1 30); do
    if curl -sf -m 8 -o /dev/null -H "X-Agnes-Internal-Token: $(cat "$BASE/data/internal_token")" \
        -H "X-Agnes-Internal-User: Mirror" http://127.0.0.1:8081/api/workbench/drama; then
      return 0
    fi
    log "⏳ 服务不可用，60s 后重试 ($i/30)"
    sleep 60
  done
  log "❌ 服务 30 分钟不可用，本批放弃，等待外部重启后手动续跑"; return 1
}

validate_batch(){
  local fn="$1" vlog="$2"
  ( cd "$D" && node "$SCRIPTS_MJS" validate "$fn" --outline outline.json --art art.json ) > "$vlog" 2>&1
  echo $?
}

dispatch_wait(){
  # $1=msgfile $2=logfile $3=timeout_sec
  local msg="$1" lg="$2" to="$3" tid th pid
  tid=$(new_thread)
  nohup python3 "$TOOLSDIR/deliver_task.py" --thread "$tid" --msgfile "$msg" --log "$lg" \
      < /dev/null > "$lg.nohup" 2>&1 &
  pid=$!
  local deadline=$(( $(date +%s) + to ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if ! kill -0 "$pid" 2>/dev/null; then break; fi
    if grep -q "FINAL status=" "$lg" 2>/dev/null; then break; fi
    sleep 30
  done
  if kill -0 "$pid" 2>/dev/null; then kill "$pid" 2>/dev/null; wait "$pid" 2>/dev/null; fi
  grep "FINAL status=" "$lg" 2>/dev/null | tail -1 | cut -c1-200
}

# ===== 主循环 =====
log "===== pipeline_scripts START=$START END=$END $(date) ====="
echo "$START" > "$STATE"

for n in $(seq "$START" "$END"); do
  b=$(printf "%02d" "$n"); p=$(printf "%02d" $((n-1)))
  a=$(( (n-1)*EPS_PER_BATCH + 1 )); c=$(( n*EPS_PER_BATCH ))
  prev_lo=$(( (n-2)*EPS_PER_BATCH + 1 )); prev_hi=$(( (n-1)*EPS_PER_BATCH ))
  fn="script-b${b}.json"
  echo "$n" > "$STATE"
  log "========== 批次 $n：第 $a-$c 集 → $fn =========="

  # 已全绿则跳过（崩溃恢复续跑）
  vlog="$LOGDIR/t2-b${b}.validate"
  vr=$(validate_batch "$fn" "$vlog")
  if [ $vr -eq 0 ]; then log "批次 $n 已全绿，跳过"; continue; fi

  # 1) 任务消息
  MSG="$TOOLSDIR/msg-t2-b${b}n.txt"
  cat > "$MSG" <<EOF
【自动化任务 T2｜novel-script 写剧本《美人图》批次 $n（第 $a-$c 集）】

交付目录：data/users/Mirror/deliverables/短剧/美人图/
上游产物（先读取核对）：outline.json（60 集，每集时长按 duration_seconds）、art.json（场景/道具/光照）、cast.json、上一批 script-b${p}.json（第 $prev_lo-$prev_hi 集）。

要求按 novel-script SKILL.md 写第 $a-$c 集剧本：

1) seed：在服务根目录执行
   node app/skills/novel-script/scripts/novel-script.mjs seed outline.json --eps $a-$c > data/users/Mirror/deliverables/短剧/美人图/${fn}
2) 承接：先读 script-b${p}.json 的最后一拍（第 $prev_hi 集结尾悬念），第 $a 集开场必须接住它；人物性情（cast.json）、场景锚点与光照（art.json）保持一致。
3) 写戏要求：动作节拍与台词行交替的结构化节拍流，每句台词带说话人与语气；每集估时落在目标 120s ±15%（102-138s），写足 45-55 拍；每集冷开场钩子（hookBeat 认领，前 3 拍内）+ 结尾悬念；写完删除 seedNote。
4) 校验（不能跳）：cd data/users/Mirror/deliverables/短剧/美人图/ && node app/skills/novel-script/scripts/novel-script.mjs validate ${fn} --outline outline.json --art art.json；有违规逐条修并重跑直到全绿。
5) 本批只产出 ${fn}，不要 render 整剧报告。

完成后汇报：本批集数、总场次、总台词句数、每集实际估时 vs 目标、validate 各门结果。
EOF

  # 2) 投递 + 产物检查（最多 3 次尝试，新线程）
  if ! wait_server; then continue; fi
  acc=""
  for attempt in 1 2 3; do
    t0=$(date +%s)
    acc=$(dispatch_wait "$MSG" "$LOGDIR/t2-b${b}.log" 6000)
    log "批次 $n attempt $attempt: $acc"
    if [ -f "$D/$fn" ]; then
      sz=$(stat -c%s "$D/$fn" 2>/dev/null || echo 0)
      if [ "$sz" -gt 5000 ]; then log "批次 $n 产物已落盘 ($sz B)，结束投递"; break; fi
    fi
    if ! wait_server; then break; fi
    sleep 5
    log "批次 $n attempt $attempt 产物缺失，换新线程补投"
  done

  # 3) validate
  sleep 5
  vr=$(validate_batch "$fn" "$vlog")
  log "批次 $n validate exit=$vr"
  tail -6 "$vlog" >> "$LOGF"

  # 3.5) 机械门程序化兜底（不占模型配额）：source/beatsClaimed/光照/台词长度/时长
  if [ "$vr" -ne 0 ]; then
    python3 "$TOOLSDIR/fix_script_auto.py" "$D/$fn" > "$LOGDIR/t2-b${b}.fixauto" 2>&1
    vr=$(validate_batch "$fn" "$vlog")
    log "批次 $n fixauto 后 validate exit=$vr"
    tail -6 "$vlog" >> "$LOGF"
  fi

  # 4) 修复循环（最多 3 轮）
  fix=0
  while [ "$vr" -ne 0 ] && [ "$fix" -lt 3 ]; do
    fix=$((fix+1))
    # 产物缺失视为特殊修复：直接重投原任务
    if [ ! -s "$D/$fn" ]; then
      log "批次 $n 产物缺失，修复#$fix 重投原任务"
      acc=$(dispatch_wait "$MSG" "$LOGDIR/t2-b${b}-r${fix}.log" 6000)
      log "  -> $acc"
    else
      FMSG="$TOOLSDIR/msg-t2-b${b}-fix${fix}.txt"
      cat > "$FMSG" <<EOF
【自动化任务 T2｜剧本批次 $n 修复（第 $a-$c 集）】

工作目录：data/users/Mirror/deliverables/短剧/美人图/
目标文件：${fn}（先 read_file 读现状）
上游：outline.json / art.json / cast.json / script-b${p}.json
刚跑的 validate 未全绿，输出（末尾 80 行）：

$(tail -80 "$vlog")

⚠️ 重要判断：如果 ${fn} 是"空壳/半成品"（每集只有 1-2 场、估时远低于 100s、只有 seed 骨架），
不要在这个骨架上面修补——请直接删除该文件，然后用 SKILL.md 的完整流程重新生成第 $a-$c 集：
   node app/skills/novel-script/scripts/novel-script.mjs seed outline.json --eps $a-$c > data/users/Mirror/deliverables/短剧/美人图/${fn}
   然后填充每集 4-5 场、45-55 拍、估时 120s±15%，接续 script-b${p}.json 的结尾悬念。
如果文件内容已较完整，则逐条修 validate 列出的违规（光照要用 art.json 登记的中文状态名、
台词 ≤35 字、动作与台词交替节拍、爽点认领）即可。
修完重跑：cd data/users/Mirror/deliverables/短剧/美人图/ && node /home/mirror/Agnes-Agent/app/skills/novel-script/scripts/novel-script.mjs validate ${fn} --outline outline.json --art art.json
直到全绿。完成后汇报修复内容与最终 validate 摘要。不要动其它批次文件。
EOF
      log "批次 $n 修复#$fix thread 新开"
      acc=$(dispatch_wait "$FMSG" "$LOGDIR/t2-b${b}-fix${fix}.log" 6000)
      log "  -> $acc"
    fi
    if ! wait_server; then break; fi
    sleep 5
    vr=$(validate_batch "$fn" "$vlog")
    log "批次 $n 修复#$fix 后 validate exit=$vr"
  done

  if [ "$vr" -eq 0 ]; then
    log "批次 $n ✅ 全绿（第 $a-$c 集完成）"
  else
    log "批次 $n ❌ 仍非全绿（fix=$fix），记入失败清单继续下一批"
    echo "BATCH $n FAILED" >> "$LOGDIR/pipeline-scripts.state.failed"
  fi
done
log "===== pipeline_scripts 全部结束 $(date) ====="