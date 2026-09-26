# Agnes 本地化媒体执行适配（★ 部署铁律 · 覆盖原 E 法则 与 A 锁）

> 本文件是 **Agnes Agent 部署**对本 skill 的本地化适配总控，优先级高于文档库内
> Seedance / Seedance 2.0 / MiniMax H3 等原生模型契约的同名条款。凡是与本文冲突的
> 写法，一律以本文为准。落地日期 2026-09-26（用户指示：出图/出视频统一指定 agnes-media）。

## 0. 通道唯一性（出图 + 出视频）

- 本部署出图/出视频 **唯一执行通道 = agnes-media**：
  `/home/mirror/Agnes-Agent/app/skills_builtin/agnes-media/media.py`
  （内部创作台 `127.0.0.1:8081`，复用页面 `/api/image/generate` 与 `/api/video/generate` 端点）。
- 禁止调用其它出图/出视频工具或直接访问上游 API；禁止以「只给提示词」代替执行（见 §3 对 E 法则的覆盖）。
- 调用身份：`X-Agnes-Internal-Token`（= `data/internal_token` 文件内容）＋ `X-Agnes-Internal-User: Mirror`。
- **子进程必须显式注入 `AGENT_INTERNAL_TOKEN`**：media.py 的 PROJECT_ROOT（parents[3]）在本机解析为
  `/home/mirror`（非 `Agnes-Agent`），默认 token 文件查找失败。调用前必须：
  `export AGENT_INTERNAL_TOKEN=$(cat /home/mirror/Agnes-Agent/data/internal_token)`
- 产物唯一落点 = 「作品库」绝对路径，stdout 打印 `FILE=` 供后续处理；不做二次落盘。

## 1. 出图（image）

```
export AGENT_INTERNAL_TOKEN=$(cat /home/mirror/Agnes-Agent/data/internal_token)
python3 /home/mirror/Agnes-Agent/app/skills_builtin/agnes-media/media.py image \
  --prompt "<出图词>" --ratio <16:9|9:16|21:9|1:1|3:4|4:3|2:3|3:2> --size 2K
```

- 默认模型 `agnes-image-2.5-flash`（旧档 `agnes-image-2.0-flash` 可备选）；`--size` 可选 1K/2K/3K/4K（默认 2K）。
- 画幅 `--ratio` 必须按 B 锁显式指定（默认 16:9，禁止靠默认）。
- `--ref <路径/URL>` 可多次传参考图（锁形象）；`--extra '{"seed":42}'` 可加种子复现。
- stdout：`TASK_ID=` → 轮询 → `FILE=<作品库绝对路径>`、`URL=`、`LOG=`。

## 2. 出视频（video）

```
export AGENT_INTERNAL_TOKEN=$(cat /home/mirror/Agnes-Agent/data/internal_token)
python3 /home/mirror/Agnes-Agent/app/skills_builtin/agnes-media/media.py video \
  --prompt "<投喂正文>" --keyframes <f1图> --keyframes <中段图> --keyframes <末段图> --duration 5
```

- 默认视频模型 `agnes-video-v2.0`；flash 档 `agnes-video-2.5-flash`。
- **keyframes 关键帧动画：`--keyframes` 是 append 参数，必须重复传 flag**（`--keyframes A --keyframes B [--keyframes C]`），
  列表长度 **2~3 张**（服务端要求 ≥2 张；`--keyframes` 单 flag 后只跟一张图路径）；
  首/中/末三图锁定过渡轨迹，**锁形象一致性首选**。`--image` 单图 = img2vid（锁首帧形象 → 动作延续）；
  无图无关键帧 = ti2vid 文生视频。参考图一律用已入库作品或本地 PNG。
- `--duration` 预设 3/5/10/18（→ num_frames 81/121/241/441 @24fps，必须 8n+1）；不传默认 121 ≈ 5s。
- 超时默认 300s；超时后 **禁止重建任务**，用 `fetch --task-id <TASK_ID>` 续查（后端 6h 内自动重试）。
- stdout：`TASK_ID=` / `VIDEO_ID=` → 轮询 → `FILE=`（作品库 mp4）、`URL=`。

## 3. 对 E 法则的覆盖（用户已确认）

- 原「E · 默认只出提示词，不直接出图」在本部署 **被覆盖**：**默认直接调用 agnes-media 执行出图、出视频**。
- 保留的分寸：P2 数字资产包仍按 `asset-first-pipeline.md` 门禁，先给《资产清单》确认批次；
  但确认后立即执行，不再让用户手工跑图。
- 出视频同理：分镜/投喂稿出稿机检（validate_prompt.py）通过后直接 `media.py video` 执行。

## 4. 对 A 锁（模型版本三选一）的覆盖

- 原「A · Seedance 2.5 / Seedance 2.0 / MiniMax H3 强制三选一」在本部署 **统一收敛为 agnes 单通道**：
  - 出图 = `agnes-image-2.5-flash`；出视频 = `agnes-video-v2.0`（`agnes-video-2.5-flash` / `agnes-image-2.0-flash` 为备选档）。
  - 不再询问用户「模型版本三选一」；投喂正文**不写 Seedance 官方字符**（`{}` / `<>` / `()` / `【】`）、
    **不投 H3 原生字段**——正文即纯文本投喂词，声音/台词以文字写入正文。
- skill 的创作方法论（Asset-First、七段式、资产锁、语气位、人物账、首镜锚定、禁令三改法、
  FACS、时长守恒、机检 C1~C20）**全部照旧执行**，仅最终封装为 agnes 的 prompt 文本。

## 5. 对 B/C/D 锁的映射

- **B 画幅**：image 用 `--ratio`；video 用 `--width/--height` 或 `--extra`（后端能力回填，如 `{"aspect_ratio":"9:16"}`）。
- **C 交付形态**：照旧（md + 单文件看板，默认 ③两者都要）。
- **D 段缝衔接**：照旧三选一（末态双保险 / 换机位硬切 / 空镜过渡）；段缝参考图走 image 出、按 keyframes 投喂。

## 6. 常用命令速查

```bash
TOKEN=$(cat /home/mirror/Agnes-Agent/data/internal_token)
export AGENT_INTERNAL_TOKEN=$TOKEN
M=/home/mirror/Agnes-Agent/app/skills_builtin/agnes-media/media.py

# 出角色三视图资产板（9:16 竖屏）
python3 $M image --prompt "…三视图资产板…" --ratio 9:16 --size 2K

# 出面部特写（锁形象）
python3 $M image --prompt "…面部特写…" --ratio 9:16 --size 2K

# 文生视频（ti2vid，5s）
python3 $M video --prompt "…" --duration 5

# 图生视频（img2vid，单图锁首帧）
python3 $M video --prompt "…" --image <f1.png> --duration 5

# 关键帧视频（keyframes，2~3 图：首/中/末；--keyframes 重复传 flag）
python3 $M video --prompt "…" --keyframes <f1.png> --keyframes <fm.png> --keyframes <fe.png> --duration 5
```

## 7. 与画风基准的衔接

出图词必须并入 `character-art-style.md`（★ 画风基准）的布局模板与外观锚词；
分镜首帧图→资产板/关键帧均按该画风出图后再投喂。
