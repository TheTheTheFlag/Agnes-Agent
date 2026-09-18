---
name: agnes-media
description: Agnes AI — 图片 & 视频统一生成（文生图 / 图生图 / 多图合成 / 文生视频 / 图生视频 / 关键帧动画），生成、下载、日志一条龙
triggers:
  - 生成图片
  - 生成视频
  - 文生图
  - 文生视频
  - 图生图
  - 图生视频
  - 关键帧
  - 生图
  - 生视频
  - Agnes
  - agnes-image
  - agnes-video
  - Agnes AI
config:
  keys_file: keys.json
  base_url: https://api.agnes-ai.cn
  model_image_default: agnes-image-2.5-flash
  model_image_legacy: agnes-image-2.0-flash
  model_video: agnes-video-v2.0
  image_size_default: "1K"
  image_ratio_default: "1:1"
  video_duration_default: 5
---

# Agnes AI — 图片 & 视频生成（单脚本工作流）

> 技能目录 `<skill_dir>` = `app/skills/agnes-media/`。
> **一切生成统一调用 `<skill_dir>/media.py`**：提交 → 轮询（视频）→ 下载产物 → 追加日志，
> 全在一个脚本里完成。**禁止**再手写 `generate_*.py`、内联 curl、或复制固定生成脚本。
> 调用参数（prompt / 模型 / 尺寸 / 参考图 / 时长 / seed 等）由你根据用户需求与下文参数表实时构造。

## 使用前确认

- 先 `ls <skill_dir>/` 确认 `media.py`、`keys.json` 存在。
- 接口速查与尺寸表见 `<skill_dir>/references/agnes-api-quickref.md`。

## 快速上手

```bash
# 文生图（默认 1K，1:1）
python <skill_dir>/media.py image --prompt "一个穿和服撑伞的少女站在樱花树下，电影级光影，写实风格" --size 2K --ratio 3:4

# 图生图（本地路径或 URL 均可，自动转 Data URI；多张参考图重复传 --ref）
python <skill_dir>/media.py image --prompt "把人物换成赛博朋克夜景背景，保留人物构图" --ref C:/Users/xxx/photo.png --ratio 9:16

# 文生视频（默认 5 秒 @24fps；--duration 可选 3/5/10/18；竖版短视频可传 --width 672 --height 1152 提示 9:16）
python <skill_dir>/media.py video --prompt "A young woman turns to camera, smiling, cinematic" --duration 5

# 关键帧动画（≥2 张参考图，URL 或本地路径）
python <skill_dir>/media.py video --prompt "在两张关键帧之间平滑转场，保持角色一致" --keyframes <url1> --keyframes <url2> --duration 5
```

脚本结束会在 stdout 输出 `URL=` / `FILE=` / `LOG=`（产物文件与日志路径），据此向用户回话并展示图片；不要编造路径。

## 子命令与参数

### `image` — 文生图 / 图生图 / 多图合成（同步）

| 参数 | 说明 |
|------|------|
| `--prompt` | 描述。文生图结构：`[主体]+[场景/环境]+[风格]+[光照]+[构图]+[质量]`；图生图：`[改变]+[新风格]+[增删元素]+[保留元素]` |
| `--model` | 默认 `agnes-image-2.5-flash`；用户明确要旧版/2.0 时用 `agnes-image-2.0-flash` |
| `--size` | `1K`/`2K`/`3K`/`4K`（默认 1K）。高清海报/壁纸用 2K-4K |
| `--ratio` | `1:1 3:4 4:3 16:9 9:16 2:3 3:2 21:9`（默认 1:1）。竖屏头像 3:4、短视频封面 9:16 |
| `--ref` | 参考图（URL **或本地路径**），可重复传多张 = 多图合成；脚本自动把本地文件转 Data URI |
| `--format` | `url`（默认）或 `b64_json` |
| `--extra` | 附加参数 JSON（合并进 extra_body） |
| `--dry-run` | 只打印请求体 |

示例（官方尺寸 → 实际像素见 quickref 表）：`--size 2K --ratio 16:9` → `2624x1472`。

### `video` — 文生视频 / 图生视频 / 关键帧动画（异步，自动轮询+下载）

| 参数 | 说明 |
|------|------|
| `--prompt` | `[主体]+[动作]+[场景]+[镜头运动]+[光线]+[风格]`；图生视频描述"什么动、什么保持稳定" |
| `--model` | 默认 `agnes-video-v2.0`（唯一） |
| `--duration` | `3`/`5`/`10`/`18` 秒（@24fps，自动 num_frames=81/121/241/441）；精确控制用 `--frames --fps` |
| `--frames` / `--fps` | `num_frames ≤ 441` 且满足 `8n+1`（脚本会校验）；`frame_rate` 1-60 |
| `--image` | 图生视频：**单张可公网访问的图片 URL** |
| `--keyframes` | 关键帧动画：≥2 张 URL（或本地路径），重复传多次；自动 `extra_body.mode="keyframes"` |
| `--width`/`--height` | 会被标准化到 480p/720p/1080p；竖向 9:16 内容可用 `--width 672 --height 1152` 之类后看 `metadata.size_mapping` |
| `--seed` | 固定种子可复现 |
| `--negative-prompt` | 反向提示词 |
| `--interval` / `--timeout` | 轮询间隔（默认 8s）/ 最大等待（默认 300s，即 5 分钟） |

**耗时提示**：5 秒视频通常需 1-3 分钟轮询，属正常；若轮询超时，用 `fetch` 续查不要重新创建任务。

### `fetch` — 调试：按 `--video-id`（推荐）或 `--task-id` 查询任务状态

## 产物与日志（脚本自动完成）

- 图片：`<skill_dir>/output/YYYYMMDD/img_XXXX.png`
- 视频：`<skill_dir>/output/YYYYMMDD/video_XXXX.mp4`
- 日志：`<skill_dir>/output/YYYYMMDD/log.md`，每次成功产物追加一条：

```markdown
## 2026-09-07 10:12:33

**Prompt**: <完整提示词>

**Model**: agnes-image-2.5-flash
**Size**: 2K
**Ratio**: 3:4
**URL**: https://…

**File**: …/output/20260907/img_0003.png

---
```

> `output/` 与 `keys.json` 已在技能目录 `.gitignore` 忽略，不会入库。

## 执行与收尾铁律（防"能出图却不收敛"）

1. **成功即停**：`media.py` 的 stdout 出现 `FILE=`（本地文件）与 `URL=` 即为生成完成。此时**立即停止调用任何工具**，把文件/链接展示给用户并结束回复。不要为了"多生成几张/确认效果"再次执行相同或相似命令，也不要再去 `ls`、`read_file` 反复确认——系统会自动拦截重复成功命令。
2. 若确需再多张不同图片：**修改参数**（prompt / size / seed / --extra）后发一条新命令，而不是原样重跑。
3. **默认 `--size 1K`**：1K 生成较快（约几十秒内）。`2K`/`4K` 单张耗时更长，可能超过命令执行上限（约 60 秒）而超时——确需大图时，给 `execute_command` 显式传较长 `timeout`（如 180 秒）并耐心等待；若仍超时，改回 1K 或换更短 prompt。
4. **Windows 环境命令**：本机 `execute_command` 走 Windows cmd——用 `dir` / `type` / `echo`，**不要用** `ls` / `cat` / `pwd` / `grep` / `rm` / `mv`（会报"不是内部或外部命令"）。需要看文件就用 `type`，列目录就用 `dir`（或 `python -c "import os;print(os.listdir('...'))"`）。

## 避坑清单（来自线上事故复盘）

1. **绝不硬编码 API Key / 绝不手写生成脚本**：key 由 `media.py` 从 `keys.json` 读；prompt 与参数通过命令行传给 `media.py`。
2. **命令失败先读 stderr**：`HTTP 4xx` 会打印响应体（参数错/未授权/无 URL 权限等）。定位并修正后再重试；**同一命令不要原样重试 3 次**——会触发会话熔断。
3. 参考图：图片接口本地路径/URL 均可；**视频（image/keyframes）要求可公网访问的 URL**，本地文件需先上传。
4. `num_frames` 上限 441 且必须 8n+1；`response_format`/参考图数组都在 `extra_body` 内（`media.py` 已按官方文档处理，不要手工再包一层）。
5. 视频完成标志是 `status=completed` 且 URL 取 `metadata.url`（旧脚本取顶层 `url` 是错的——已修复）。
6. 图生图不需要 `tags: ["img2img"]`。
7. 生成给用户看的结果时，回话附上 `FILE=` 本地路径（可展示），不要把整段 base64 贴进对话。

## 参考素材

- 角色/风格化示例提示词：`references/yae_miko_prompt_examples.md`
- 官方接口速查与尺寸表：`references/agnes-api-quickref.md`
- API Key 配置说明：`references/api-keys-config.md`
