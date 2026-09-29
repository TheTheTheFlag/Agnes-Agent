# agnes-media 服务运维手册

> 服务 = agnes-agent 创作台（LangGraph HTTP 服务）。**本部署实测为 systemd 托管**：
> 单位 `agnes-agent.service`（`/etc/systemd/system/agnes-agent.service`，ExecStart 见下），
> 带 drop-in `50-MemoryMax.conf`（MemoryMax=2GiB）与 `public-base.conf`（AGNES_PUBLIC_BASE_URL=http://god.makeup:8081）；
> 日志走 journald（`journalctl -u agnes-agent.service`）。

## 1. 健康检查
```bash
systemctl is-active agnes-agent.service                       # active / failed
curl -sS -m 5 -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8081/   # 端口通不通
journalctl -u agnes-agent.service -n 50                        # 服务日志
python <skills_builtin/agnes-media/media.py> fetch --video-id <id>      # 查单个任务
```

## 2. 重启
```bash
# 本部署
sudo systemctl restart agnes-agent.service ; sudo journalctl -u agnes-agent -n 30
# 备选（原手工方式，非当前托管）：cd …/Agnes-Agent && nohup .venv/bin/python -m app.service --host 0.0.0.0 --port 8081 >> ag-service.log 2>&1 &
```
> **polkit（2026-09-29 实测）**：不带 sudo 的 `systemctl restart agnes-agent` 会被拒
> （`Interactive authentication required`）。本部署 sudo 为 **NOPASSWD**，脚本里用
> **`sudo -n`**（非交互）即可直接重启；`sudo -n systemctl restart agnes-agent.service`。
> **热重载经验（2026-09-27 实测）**：服务默认开 `--reload`（uvicorn WatchFiles 监控 app/）。改**单文件**会正常 reload；
> **多文件补丁（如同时改 `__init__.py`+新 api 模块）可能让 reloader 卡住**——日志停在 `WatchFiles detected changes … Reloading...`，
> 8081 出现连接积压但不响应（curl 000 / 超时）。此时**直接 `systemctl restart` 即可**，不要反复等待。
> 规范：**改 agnes 后端 .py 涉及多文件 → 直接重启**；改前端静态文件（index.html/app.js/*.js）无需重启。

## 3. 客户端契约（media.py，唯一出图/出视频通道）
- 鉴权：`export AGENT_INTERNAL_TOKEN=$(cat /home/mirror/Agnes-Agent/data/internal_token)`（或自动读 data/internal_token），另带 X-Agnes-Internal-User（默认 Mirror）。
- `image`：`--prompt --ratio 16:9|9:16 --size 2K [--ref 本地路径/URL 可多次]`
- `video`：`--duration 3|5|10|18 --keyframes <图> --keyframes <图>（≥2 张）[--image 单图|纯文本 ti2vid] [--width 1280 --height 720 锁 16:9] --timeout <秒>`
- 端点：`POST /api/image|video/generate` + `GET .../task/{id}` 轮询；后端自动重试直到成功。
- 产物：stdout 输出 `FILE=`（作品库绝对路径，`data/users/<用户>/data/image_library|video_library`），`URL=` 可展示，`LOG=` 日志路径。**不要编造路径**。
- 轮询超时 → 用 `fetch` 续查，**禁止重复创建任务**。

### 3a. 超时分级（2026-09-28 新增，替换 356400s 空转）

`--timeout 356400`（≈99 小时）是历史遗留值：任务静默死亡时会空转数天，
且因为后端「自动重试直到成功」，**失败也照样扣费**。改为分阶段超时：

| 阶段 | 建议 `--timeout` | 超时后动作 |
|---|---|---|
| 出图 `image` | 300 | 先 `fetch` 确认一次 → 仍无 → 判 FAIL，写 `FAILED` 标记并上报 |
| 视频 `video` / 渲影 | 1800 | 同上 |

- **重试预算**：同一输入连续原样重投 ≤2 次；之后必须改内容或退回负责环节。
  禁「自动重试直到成功」这种无上限策略。
- **silent-fail 自检**（出图/出视频后必做，否则空产物会被当成成功）：
  ```bash
  [ -f "$FILE" ] && [ "$(stat -c%s "$FILE")" -gt 1000 ] && ffprobe -v error -show_entries format=duration -of csv=p=0 "$FILE"
  ```
  三条全过才算成功；任一不过即判失败并记日志。
- **提交号解析**：`submit_id` 可能被 stderr 污染，抽 UUID 时**合并 stdout + stderr 两路**
  再用严格正则 `[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}` 取，比只看一路抗污染。
- **禁止 `tail -N | grep` 解析日志**：日志尾部会被截断导致误判。永远解析结构化字段
  （`FILE=` / `URL=` / `LOG=`）。§4 第 1 条「看调用输出尾部」仅用于**人眼定位报错**，
  不得用于脚本化判定。

**★ 超时 ≠ 失败：rc≠0 后必须先 `fetch` 续查终态（Fix C 契约，2026-09-29 沉淀）**

`media.py` 非零退出只说明**本地轮询**结束，**不代表后端任务死了**。EP03-12 实测事故：
驱动在 media.py 超时后 3 秒即重投，而原任务在后端仍在跑 → 同一段**双份扣费**。
驱动/脚本必须实现三态判决（**重投的唯一判据是终态 `failed`**）：

| `fetch --task-id` 返回 | 处置 |
|---|---|
| `completed` 且产物可定位 | **认领产物**（补 `-an` + 校验尺寸/时长）→ 判 OK，**不重投** |
| `running` / `queued` / 状态未知 / fetch 自身超时 / `completed` 但产物未定位 | 判 **`FAIL(task_running)`** 并落台账（含 task_id）→ **停止等人工继续 fetch，禁止重投** |
| `failed`（终态确认） | 才允许本段重投 |
| 输出里取不到 `TASK_ID` | 无从续查，按可重投处置，但台账要记「未取到 TASK_ID」 |

- ⚠️ `FAIL(task_running)` **不是**「可重投的失败」——它恰恰是**最禁止重投**的一类。
  台账 `reason="task_running"` 的段只能 `fetch` 续查或人工介入。
- 前提：**驱动必须把 `TASK_ID=` 打到输出**（stdout/stderr 均可抽出），否则无法续查。
- 实现参考：`patch_fixC.py`（`_drama/zhulong/`，幂等补丁；给 `gen_videos_*.py` 注入
  `recover_task()` 三态返回 OK/RUNNING/DEAD；备份 `.bak-fixC-<date>`）。

## 3b. 短剧审核台 API（8081 工作台「短剧审核」tab，2026-09-27 新增）
- 模块：`app/server/api/drama_review.py`（前缀 `/api/drama-review`），已注册 `app/server/__init__.py`；前端 `app/server/static/drama-review.js` + index.html `data-tab="review"`。
- **鉴权（直调 API 时必带，2026-09-29 实测）**：内部通道需**双头**——
  `X-Agnes-Internal-Token: $(cat /home/mirror/Agnes-Agent/data/internal_token)` **且** `X-Agnes-Internal-User: Mirror`；
  缺任一 → 401。注意头名是 `X-Agnes-Internal-Token`，与 media.py 用的环境变量 `AGENT_INTERNAL_TOKEN` 同名不同物。
- `GET /projects`：列作品（`deliverables/短剧/*`）+ 集数清单。
- `GET /assets?name=<剧名>`：读 `assets/manifest.json`，**按顶层扁平列表遍历**（每项含 id/type/name/prompt/ratio/status/path）。
  新项目必须写扁平列表；嵌套分类字典（`{assets:{characters,scenes,props}}`）或 `{assets:[…]}` 包裹实测直接 500
  ——2026-09-29 已为审核台加三形态容错（兼容读），但**写入侧契约不变，仍须扁平**。
- `POST /assets/regenerate` `{name,id,prompt}`：改提示词重出资产图（原图 `--ref` 锁形象），异步任务，回写 manifest。
- `GET /storyboard?name=<剧名>&ep=E01`：解析投喂稿 md，返回每段分镜 prompt + 视频 rel path + 关键帧数。
  ⚠️ **解析契约（2026-09-29 查明）**：md 段头必须是 **h2 `## <EP>-NN · 标题（--duration N）`**
  （如 `## E03-01 · 看台骤雨·披衣护她（--duration 10）`）。若投喂稿用别的 schema
  （如 `## 段列表` 下的 `### 段NN` 规划表），**接口不报错但返回 `segments: []`（静默空）**；
  此类项目的段清单权威来源是 `投喂稿/manifest.json`。排查"分镜审核列不出段落"先对照段头格式。
- `POST /segments/render` `{name,ep,seg,prompt?}`：重渲单段（prompt 空=直接重渲；填=改词重渲，写 `.review/<EP>.manifest.json` 独立 manifest）。
- `POST /merge` `{name,eps}`：确认后合并整集/N集连播（合并前把将被覆盖的旧 `E0X.mp4` 自动备份到 `.review/merge_backup/`）。
- `GET /tasks/{tid}`：任务轮询（status: queued/running/done/failed + log 尾部）。

## 4. 常见故障排查清单（按序）
1. 看调用输出**尾部**（FAIL rc= / HTTP 4xx/5xx / stderr），先定位再重试；同一命令不要原样重试 3 次（会话熔断）。
2. 检查 token 是否已导出、`--timeout` 是否按 §3a 分级设置（出图 300 / 视频 1800）。
3. 服务健康（§1）：systemd `is-active` 非 active→`systemctl restart`；8081 端口积压不响应（curl 000/超时）→热重载卡死，直接重启。
4. 生成了但不是目标内容（人物不一致/画风跑偏）→ **按 `skill-optimization-loop.md` §缺陷处置五选换层级**，
   优先查 `--ref` 数量与是否误挂资产板（见 `render-contracts.md` §4.2/§4.3），
   再查 prompt 风格档与 `[人数]` 段是否前置，最后查 `--width/--height` 锁。
   **禁止在同一条提示词上叠加形容词**。
5. **传了文件但内容不是目标内容** → 先跑 silent-fail 自检（§3a），排除空产物/坏文件被当成成功。
6. `media.py` 本体有 bug 需修补：**先备份** `cp media.py media.py.bak-<原因>-<日期>` 再改，改完复测，保留备份链。
7. **往远端装脚本后行为异常（无输出/静默通过）** → 优先怀疑 scp 静默失败留下 0 字节文件。
   `install_*.sh` 必须带 `stat -c%s > 200` 非空断言 + `bash -n` 语法自检 + 装后复检；
   只用 `[ -f ]` 检查会把备份好的好文件覆盖成空的（实测踩过）。
8. 历史已修复问题（备份文件名即履历）：deploy-20260925（部署）、dupfix-20260926（任务去重/重复产物）、kindmap-20260926（kind/类型映射）。

## 4a. 远端脚本安装（QA/拼接类，2026-09-28 新增）

`scripts/*.sh` 的**本地副本是权威**，远端 `/home/mirror/stage-kzqs/` 是运行位。
改完必须走「本地改 → scp 到 `/tmp/<暂存>/` → 远端 install 脚本安装」三步：

1. **暂存目录先清空**：`rm -f /tmp/qa_fix/*.sh; mkdir -p /tmp/qa_fix`——
   否则残留的旧文件会被当成本次源文件装上去。
2. **install 脚本必须带四道断言**（缺一不可）：
   - `stat -c%s "$SRC/$f" -gt 200` 非空断言（**防 scp 静默失败留下 0 字节**）
   - `bash -n "$SRC/$f"` 语法自检
   - 装后复检：文件大小 + `bash -n` 复跑
   - `grep -q '<旧项目名>'` 确认无写死项目路径残留
3. **改前备份**：`cp -p "$DST/$f" "$DST/$f.bak-$(date +%Y%m%d)"`。
4. **装后实测**：拿一个真实项目跑一遍，**看退出码**——
   静默退出 0 且无输出 = 脚本是空的或路径没匹配上。

> ⚠️ 实测事故：`scp` 报 `Connection closed` 但 PowerShell 吞掉了输出，安装脚本又只写
> `[ -f "$SRC/$f" ]`（空文件也存在）→ `cp` 把备份好的 `qa_videos.sh`（2685 B）覆盖成 0 字节，
> 之后 `qa_videos.sh` 对任何项目都「无输出 + exit 0」，看起来像通过。

## 4b. 长任务后台化与监控（2026-09-29 新增，出帧/渲影实测）

**后台化**：`setsid nohup "$PY" driver.py … > .review/<run>.log 2>&1 < /dev/null &`，
然后**只轮询日志与产物**，禁止前台等待。启动前先 `pgrep` 守卫，防重复建任务（=重复扣费）。

**★ `pgrep -f` 自匹配陷阱**：监控命令自身的命令行若含被搜索的字面串，
`pgrep -f` 会把监控 shell 也算成命中 → 守护脚本永久误判「任务仍在跑」。
实测 PID 1983280 自匹配。规避：**一律用括号技巧**——

```bash
pgrep -f "gen_videos_[z]huLong.py"     # ✅ 正则匹配 zhuLong，但自身命令行是 [z]huLong，不自匹配
pgrep -f "zl_[c]hain_video.sh"         # ✅ 同理
pgrep -f "gen_videos_zhuLong.py"       # ❌ 会命中自己
```

**★ 不得编辑正在运行的 bash 脚本**：bash 按字节增量读取脚本文件，
运行中改动会让后续语句从错误的偏移继续解析。要改就等它退出，或另存新文件名再启动。

**★ ssh 会话可能被远端后台任务挂住**：即便 `setsid … </dev/null &`，ssh 通道仍可能不退出，
导致本地工具调用超时转后台。**远端 `setsid` 进程不受影响**，清理本地挂起会话即可，
不要去 kill 远端 PID。稳妥做法：监控命令外包 `timeout <秒> ssh -o ServerAliveInterval=30 …`。

**守护脚本审计成败要读结构化台账，不要 grep 日志**：
`grep -cE " FAIL "` 会被驱动自己打的汇总行 `===== …：OK 14 / SKIP 0 / FAIL 0 =====` 假阳性命中，
把成功判成失败并中止续渲。正解：读 `.review/video_status.json` 统计 `state=="FAIL"`，
日志兜底时**必须剔除 `=====` 行**。

## 5. 日志
- 服务日志：systemd 托管 → `journalctl -u agnes-agent.service`；非托管时 `ag-service.log`（项目根）。
- 生成日志：`app/skills_builtin/agnes-media/output/YYYYMMDD/log.md`（每次成功追加一条，只写日志不落产物副本）。

## 6. 已修复问题（备份文件名即履历）

### 2026-09-28：压缩摘要模板对齐用户六小节规范（compaction.py.bak-template/-template2-20260928）
- **背景**：用户要求压缩摘要遵循其指定的会话总结模板（Objective / Requirements / Decisions / Work State
  / Next Move / Relevant Files），并指出此前 `_COMPACT_SYS` 把 Requirements+Decisions 合成一条
  Important Details 不对版。
- **改动（两份增量，最终 = bak-template2 后版本）**：
  1. `_COMPACT_SYS` 精确改为六小节 + **few-shot 完整示例**（deepseek-v4-pro 指令遵循弱，纯说明容易跑偏）；
  2. 新增 `_is_template_compacted(text)` 校验六小节骨架；
  3. `_work()` 落库前校验，缺失则**重试一次 LLM**，仍失败保底使用首次输出（不丢信息）。
- **单测（远端 venv，FakeLLM）**：纯函数 TPL/OLD/EMPTY 判定正确；场景1（首旧→重试→模板落库，CALLS=2，
  气泡同步落库）✅；场景2（两次全旧→保留首次输出不丢信息）✅。
- **端到端（e2e-toolclean-20260928，12:07 触发）**：摘要 1199 字，六小节齐全，Requirements 正确提取
  「只读文件并总结，不写文件、不执行命令」，Relevant Files 全带路径说明。
- **坑**：模型完全无视纯文本小节指令（11:51/11:52 两次压缩输出仍是旧格式）——必须 few-shot + 校验重试双保险。
- **v4→v6 演进（12:16/12:20 实测驱动）**：
  - v5（compaction.py.bak-bubblefull-20260928）：气泡升级为内嵌摘要全文（`📦 历史已压缩（HH:MM:SS）：…\n\n{摘要全文}`），
    用户翻开会话即可直接看到结构化压缩内容；气泡写在 memory.db messages（assistant/chat），
    仅前端展示用——`build_memory_injection`/`build_recent_work_summary` 都不读该表（注入源是
    history_summaries/画像/任务/facts + checkpoint messages），不会污染 LLM 上下文。
  - v6（compaction.py.bak-normalize-20260928）：实测发现 deepseek-v4-pro 会输出「旧格式+模板」**拼接混合体**
    （12:16 摘要 2057 字=要点总结+六小节），旧校验只查标题存在被放行 → 收紧为必须以 `## Objective` 开头 +
    新增 `_normalize_template()`：非纯模板但含模板段 → 裁剪到模板段；完全无模板段才重试；仍失败保底原样不丢信息。
    端到端 12:20:12 摘要 1399 字 = 纯模板开头、六小节齐全、无旧格式前缀。
  - 手工修复存量：6 条气泡按时间对齐 6 份摘要全文（11:23/11:26/11:51/11:52 旧格式 + 12:07/12:16 模板版），
    12:16 混合摘要 UPDATE 裁剪为纯模板段。

### 2026-09-28：压缩可见气泡（A 方案 · compaction.py.bak-bubble-20260928）
- **背景**：压缩触发是后台动作（只写 app_logs + history_summaries），聊天界面无感知，用户要求
  「压缩时给会话补一条系统气泡」+ 补回本次 e2e 会话已有的两次压缩痕迹。
- **改动**：`compaction.py` compact_in_background 的 `_work()` 在摘要落库成功后，向 memory.db
  messages 表插入 `role='assistant'` / `kind='chat'` 气泡
  「📦 历史已压缩（HH:MM:SS）：较早的对话已转为摘要，最近 30 条保留原文…」
  ——`/api/messages?type=chat` 渲染为正常气泡；文案带时间戳规避 add_message 60s 去重；
  不碰 checkpoint（不影响 LLM 上下文），messages 表与 checkpoint 相互独立。
- **补回**：一次性脚本向 e2e-toolclean-20260928 补 2 条气泡（11:23:06、11:26:31，对应两次真实
  压缩时间戳），API 验证 44 条 chat 消息中含 2 条气泡且位于 7/8、8/8 讨论的用户消息与回复之间。
- **坑**：`systemctl restart` 时曾现 `deactivating`（旧进程 graceful shutdown 慢），25s 后恢复
  active/HTTP 200——后端重启等待时间建议放宽到 ≥25s。

### 2026-09-28：压缩 & 工具清理端到端验证（新会话 e2e-toolclean-20260928）
- **投递**：本机 SSH 隧道经 `POST /api/chat` + 内网令牌通道
  （`X-Agnes-Internal-Token: <data/internal_token>` + `X-Agnes-Internal-User: Mirror`）新建会话，
  投 12 轮文件盘点任务 + 8 轮延伸讨论，历史涨到 **44 条 / 25,021 tokens**。
- **压缩 20k 触发实证**：真实链路两次触发 `app_logs`「已触发后台历史压缩（不阻塞本轮回复）」
  （11:23:06、11:26:31），`history_summaries` 落库 2 条摘要（1050 字/597 字，score=6.0，
  由 compaction.py `_COMPACT_SYS` 提示词的 LLM 后台压缩生成；全程每轮 HTTP 200 不阻塞）。
- **备注**：deepseek-v4-pro 指令遵循弱，摘要格式未严格遵循五小节（内容仍保路径/要点）；
  工具消息走 ReActLoop/executor 子路径不进 checkpoint 的 messages 层，工具清理以单测 8 项全绿为准。
- **登录通道备用**：`POST /api/auth/login`（Mirror / admin123）→ `agnes_auth` cookie（role=admin），已验证可用。

### 2026-09-28：工具结果清理（tool result clearing · utils.py.bak-toolclean2-20260928）
- **背景**：每轮把全量工具输入/输出原样发给模型；本部署模型上下文过长会乱来，且 read_file 等大输出徒占预算。
- **修复**：`app/graph/utils.py` 新增 `clear_stale_tool_results`，接入两条发送路径
  （`prepare_context_messages` / `ensure_token_limit`）：
  - 早于最近 KEEP_RECENT（30）条 + content 超 800 字符的 ToolMessage → 替换为短占位
    （保留 tool_call_id/name，不拆 assistant↔tool 配对；深拷贝不改原始 state）；
  - 最近 30 条内工具结果永远保留原文；小结果不受影响。
- **教训**：首版 `cut` 判向写反（把"最近"段当"较早"段清），靠投递测试 TEST2 抓出后修正；
  测试断言也要对准语义（最近大结果应保留，不是全清）。
- **测试**：8 项全绿——旧大→占位、旧小→保留、最近大→保留、原始列表不被污染、
  `prepare_context_messages`/`ensure_token_limit` 两路径集成生效（3692→756 tokens）。

### 2026-09-28：压缩触发点固定 20k（用户指定 · compaction.py.bak-compact20k-20260928）
- **背景**：`compaction.py` 旧阈值 `max(_COMPACT_MIN_TOKENS=20k, TOKEN_LIMIT×0.6)`；本部署 `MODEL_CONTEXT_LIMIT=512k` →
  `TOKEN_LIMIT≈367k` → 实际触发点被 ratio 顶到 ~220k，压缩形同虚设。
- **修复**：`should_compact` 固定 `tokens >= 20000`；删除 `_budget_tokens` 死代码；`_COMPACT_RATIO` 标 LEGACY。
- **测试（投递任务实测）**：短历史→False；31 条短消息→False；40 条长消息 64,361 tokens→True；
  真实后台压缩（服务同款 llm）→ 398 字结构化摘要写入 `history_summaries`（importance_score=6.0）。链路全通。
- **调用位置**：`builder.py` 每轮 `compact_in_background(thread_id, messages, llm, DB_PATH, user)`；
  只压 `messages[:-30]`（最近 30 条保留原文）；后台线程、不阻塞回复；防重入锁。

### 2026-09-28：删除作品「复活」问题（墓碑机制修复 · tombstones）
- **症状**：在作品库删除的图片/视频，再次打开作品库后又出现。
- **根因**：`image.py` / `video.py` 的 `_sync_skill_outputs` 在每次 `GET /history` 时扫描
  `agnes-media/output/` 目录，用 `(src_name, size_bytes, src_mtime)` 指纹判定「是否已在 manifest」。
  而 `delete` 接口只从 manifest 移除记录（不删源文件）——指纹不在 manifest 里即被判为「新作品」重新导入 → 复活。
- **修复**：墓碑机制（`image.py.bak-tombstone-20260928` / `video.py.bak-tombstone-20260928` 可回滚）：
  1. `delete` 时：移除 manifest 记录 + 物理删除作品库副本（`file`/`thumb`）+ 把源文件指纹写入墓碑
     `data/users/<user>/data/deleted_sources_image.json` / `deleted_sources_video.json`；
  2. `_sync_skill_outputs` 加载墓碑，指纹命中墓碑即跳过，不再导入。
- **用户操作要求**：修复前「已复活」的记录仍需**手动再删一次**（本次删除才会写墓碑，之后才不复活）。
- **回滚**：`cp api/image.py.bak-tombstone-20260928 api/image.py`（video 同理）→ 重启。

### 2026-09-26：任务去重 / 重复产物（media.py.bak-dupfix-20260926）