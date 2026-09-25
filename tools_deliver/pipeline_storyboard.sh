#!/bin/bash
# pipeline_storyboard.sh — 剧本全部完成后：T5 角色图重出 → 60 集合并 → 逐集 分镜(含出图) → 直跑视频
# 用法: bash pipeline_storyboard.sh [START_EP] [END_EP]
set -u
BASE=/home/mirror/Agnes-Agent
D="$BASE/data/users/Mirror/deliverables/短剧/美人图"
TOOLSDIR="$BASE/tools_deliver"
LOGDIR="$BASE/logs/tasks"
SB_MJS="$BASE/app/skills/novel-storyboard/scripts/novel-storyboard.mjs"
SC_MJS="$BASE/app/skills/novel-script/scripts/novel-script.mjs"
STATE="$LOGDIR/pipeline-storyboard.state"
LOGF="$LOGDIR/pipeline-storyboard.log"
START="${1:-1}"; END="${2:-60}"
mkdir -p "$TOOLSDIR" "$LOGDIR" "$D"

log(){ echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOGF"; }
new_thread(){ cat /proc/sys/kernel/random/uuid 2>/dev/null || python3 -c "import uuid;print(uuid.uuid4())"; }
wait_server(){
  for i in $(seq 1 30); do
    if curl -sf -m 8 -o /dev/null -H "X-Agnes-Internal-Token: $(cat "$BASE/data/internal_token")" \
        -H "X-Agnes-Internal-User: Mirror" http://127.0.0.1:8081/api/workbench/drama; then return 0; fi
    log "⏳ 服务不可用，60s 后重试 ($i/30)"; sleep 60
  done; log "❌ 服务长期不可用"; return 1
}
dispatch_wait(){
  local msg="$1" lg="$2" to="$3" tid pid
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
val_sb(){
  local fn="$1" vlog="$2"
  ( cd "$D" && node "$SB_MJS" validate "$fn" --script 美人图-script.json --outline outline.json --cast cast.json --art art.json ) > "$vlog" 2>&1
  echo $?
}

log "===== pipeline_storyboard START=$START END=$END $(date) ====="

# -------- 前置 1：T5 角色图重出（新基线） --------
if [ ! -f "$LOGDIR/t5-done.flag" ]; then
  log "== 前置 T5：角色设定图用新基线全部重出 =="
  if wait_server; then
    cat > "$TOOLSDIR/msg-t5.txt" <<'EOF'
【自动化任务 T5｜novel-characters 用新质量基线重出《美人图》全部角色设定图】

工作目录：data/users/Mirror/deliverables/短剧/美人图/
先 read_skill 读 app/skills/novel-characters/SKILL.md 与 references/*.md。
上游：cast.json（9 角色）、美人图-art.json（场景/道具）、现有 images/（旧角色图 7 张可作构图参考但必须重出）。

要求：
1) 按 novel-characters SKILL.md 生成/更新每个角色的 images/<角色名>-sheet.png（全部 9 个角色，一个不少）
2) 出图统一走 agnes-media（python app/skills_builtin/agnes-media/media.py image），提示词必须达到新质量基线：
   增强模板 = 角色描述 + 光影词2-3 + 质感词2-3 + 氛围词1-2 + 固定质量尾锚：
   Photorealistic, hyper-realistic, 超写实国风，现代风格，写实风格，质感光照，自然光线，质感十足，8K高清纹理，布料褶皱自然，艺术写实风格，营造出震撼的视觉效果
3) cast.json 里每个角色的 image.sheet 更新为最终落盘路径（绝对或 deliverables 相对路径）
4) stdout 出现 FILE= 后 cp 到 images/<角色名>-sheet.png
5) 一角色一张可多角度（挂其它角色/场景图作参考保一致）；单张失败重试一次，最后汇报每角色路径
完成后汇报：9 个角色各自的 sheet 路径 + cast.json 是否已同步。
EOF
    acc=$(dispatch_wait "$TOOLSDIR/msg-t5.txt" "$LOGDIR/t5.log" 9600)
    log "T5 -> $acc"
    sleep 5
    cnt=$(ls "$D"/images/*-sheet.png 2>/dev/null | wc -l)
    log "T5 后 sheet 图数量: $cnt"
    if [ "$cnt" -ge 9 ]; then touch "$LOGDIR/t5-done.flag"; fi
  fi
fi

# -------- 前置 2：60 集合并 美人图-script.json --------
if [ ! -f "$D/美人图-script.json" ] || [ ! -f "$LOGDIR/merge-done.flag" ]; then
  log "== 前置 合并：script-b01..b20 → 美人图-script.json =="
  if wait_server; then
    cat > "$TOOLSDIR/msg-t2-merge.txt" <<EOF
【自动化任务 T2-merge｜合并 60 集剧本为 美人图-script.json】

工作目录：data/users/Mirror/deliverables/短剧/美人图/
1) 读取 script-b01.json ~ script-b20.json（每份 3 集），按集号顺序合并为一份 美人图-script.json：
   - 顶层 params/source/lang/style 取第一份为准，episodes 数组按集号升序拼接（去重、不得缺集）
   - 合并后总集数必须为 60
2) 校验：cd . && node /home/mirror/Agnes-Agent/app/skills/novel-script/scripts/novel-script.mjs validate 美人图-script.json --outline outline.json --art art.json；必须全绿。
3) 汇报：总集数、总场次、总台词句数、总估时 vs 总目标（60×120s）、validate 结果。
不渲染报告，只合并+校验。
EOF
    acc=$(dispatch_wait "$TOOLSDIR/msg-t2-merge.txt" "$LOGDIR/t2-merge.log" 7200)
    log "merge -> $acc"
    sleep 5
    if [ -f "$D/美人图-script.json" ]; then
      n=$(grep -c '"ep"' "$D/美人图-script.json" 2>/dev/null)
      log "合并后集数条目: $n"
      ( cd "$D" && node "$SC_MJS" validate 美人图-script.json --outline outline.json --art art.json ) >> "$LOGDIR/t2-merge.log" 2>&1 && touch "$LOGDIR/merge-done.flag"
    fi
  fi
fi

if [ ! -f "$D/美人图-script.json" ]; then log "❌ 合并未完成，分镜无法开始"; exit 1; fi

# -------- 逐集：分镜(含出图) → 直跑视频 --------
echo "$START" > "$STATE"
for i in $(seq "$START" "$END"); do
  e=$(printf "%02d" "$i")
  fn="storyboard-e${e}.json"
  echo "$i" > "$STATE"
  log "========== 第 $i 集 分镜+视频 → $fn =========="

  # 已全绿跳过（崩溃恢复）
  vlog="$LOGDIR/sb-e${e}.validate"
  vr=$(val_sb "$fn" "$vlog")
  if [ $vr -eq 0 ]; then log "第 $i 集分镜已通过，跳过"; else
    cat > "$TOOLSDIR/msg-sb-e${e}.txt" <<EOF
【自动化任务 T3｜novel-storyboard 分镜《美人图》第 $i 集（单集闭环）】

工作目录：data/users/Mirror/deliverables/短剧/美人图/
先 read_skill 读 app/skills/novel-storyboard/SKILL.md，再读 references/storyboard-pass.md、schema.md、frame.md、h3-prompt.md，照它们做。
上游：美人图-script.json（60 集完整剧本）、outline.json、cast.json（角色设定图）、art.json（场景/道具设定图）、images/（设定图）。

本任务是「单集闭环」：只做第 $i 集，分镜 → 出图全部完成后再交，不要做其它集。
1) seed：node app/skills/novel-storyboard/scripts/novel-storyboard.mjs seed 美人图-script.json --eps $i > data/users/Mirror/deliverables/短剧/美人图/${fn}
2) 切镜：按 storyboard-pass.md 把第 $i 集切成段与分镜（段≤15s、每切2-5s、节拍全覆盖、台词装得下），每段写完整 h3Prompt（对齐指令与切点时刻由分镜秒数推导，台词逐字进 <d>）；删掉 seedScenes。
3) 校验（不能跳）：
   cd . && node app/skills/novel-storyboard/scripts/novel-storyboard.mjs validate ${fn} --script 美人图-script.json --outline outline.json --cast cast.json --art art.json
   有违规逐条修，重跑直到全绿。
4) 出图（本集全部段，一次出齐）：按 SKILL.md Step 4 与 frame.md，逐段逐镜调 agnes-media image 出分镜图到 <段号>/f<切序>.png：
   - 挂场景/角色/道具设定图当 --ref（画面里有谁挂谁）；提示词 = 英文 frame 提示词 + 光影/质感/氛围词 + 固定质量尾锚 + 收尾句 No text, no watermark, no borders
   - 每段至少 f1.png，多格全出；缺图的段在汇报里点名
5) 汇报：第 $i 集共几段几镜、validate 各门结果、每段 f*.png 路径。
不渲染 HTML 报告，不要碰其它集文件。
EOF
    if ! wait_server; then exit 1; fi
    acc=$(dispatch_wait "$TOOLSDIR/msg-sb-e${e}.txt" "$LOGDIR/sb-e${e}.log" 10800)
    log "第 $i 集 分镜任务 -> $acc"
    # 修复循环
    fix=0
    while [ "$vr" -ne 0 ] && [ "$fix" -lt 3 ]; do
      fix=$((fix+1))
      if [ ! -s "$D/$fn" ]; then
        acc=$(dispatch_wait "$TOOLSDIR/msg-sb-e${e}.txt" "$LOGDIR/sb-e${e}-r${fix}.log" 10800)
      else
        cat > "$TOOLSDIR/msg-sb-e${e}-fix${fix}.txt" <<EOF
【自动化任务 T3｜分镜第 $i 集修复】

工作目录：data/users/Mirror/deliverables/短剧/美人图/
目标文件：${fn}（先 read_file 读现状；缺图段先补图）
刚跑的 validate 未全绿，输出（末尾 80 行）：

$(tail -80 "$vlog")

逐条修复（节拍覆盖、秒数、h3Prompt 对齐指令/切点/台词 <d>、景别短语、风格统一等），修完重跑 validate 直到全绿。完成后汇报。
EOF
        acc=$(dispatch_wait "$TOOLSDIR/msg-sb-e${e}-fix${fix}.txt" "$LOGDIR/sb-e${e}-fix${fix}.log" 10800)
      fi
      log "  -> fix#$fix $acc"
      if ! wait_server; then exit 1; fi
      sleep 5
      vr=$(val_sb "$fn" "$vlog")
      log "  validate exit=$vr"
    done
    if [ "$vr" -ne 0 ]; then log "第 $i 集分镜 3 轮修复仍未过，跳过视频"; echo "SB $i FAILED" >> "$LOGDIR/pipeline-storyboard.failed"; continue; fi
  fi

  # -------- 直跑视频（不经 LLM） --------
  log "第 $i 集：直跑整集视频"
  if wait_server; then
    python3 "$TOOLSDIR/run_episode_videos.py" "$D/$fn" E${e} > "$LOGDIR/video-e${e}.log" 2>&1 &
    VPID=$!
    # 串行：等待完成（视频最长 2h/段）
    while kill -0 $VPID 2>/dev/null; do sleep 60; done
    grep -E "完成:|OK \(|FAIL" "$LOGDIR/video-e${e}.log" | tail -12 >> "$LOGF"
  fi
done
log "===== pipeline_storyboard 全部结束 $(date) ====="