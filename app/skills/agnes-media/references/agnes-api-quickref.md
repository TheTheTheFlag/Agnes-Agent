# Agnes AI 官方接口速查（核对日期：2026-09）

> 来源：https://www.agnes-ai.cn/zh-Hans/docs/agnes-image-25-flash 与
> https://www.agnes-ai.cn/zh-Hans/docs/agnes-video-v20
> **一切调用统一走本技能目录的 `media.py`（读 keys.json → 生成 → 轮询 → 下载 → 写日志），
> 不要手动拼 curl / 手写生成脚本。** 以下为接口事实备忘，供排查与参数设计参考。

## 通用

- 域名：`https://api.agnes-ai.cn`
- 认证：`Authorization: Bearer <key>`（key 在 `<skill_dir>/keys.json`，已 .gitignore）
- 返回错误时先读 stderr/HTTP body，再调整参数；不要无脑重跑同一命令。

## 图片 Image — `agnes-image-2.5-flash`

- 创建：`POST /v1/images/generations`（同步，直接返回产物）
- 必填：`model`、`prompt`、`size`
- `size`：`1K`/`2K`/`3K`/`4K` 档位；`ratio`：`1:1 3:4 4:3 16:9 9:16 2:3 3:2 21:9`（默认 1:1）。
  精确尺寸（如 `1920x1080`）可能被标准化映射，推荐档位+ratio，见下表。
- **图生图/多图合成**：参考图放 `extra_body.image`（数组，URL 或 `data:...;base64,` Data URI）；
  用 `media.py image --ref <url|本地路径> [--ref …]`（本地自动转 Data URI）
- 输出格式放 **`extra_body.response_format`**：`url` 或 `b64_json`（**不能放请求体顶层**）。
  文生图 Base64 也可用顶层 `return_base64: true`
- 返回：`data[0].url` 或 `data[0].b64_json`
- 尺寸参考（宽 x 高）：

| ratio | 1K | 2K | 3K | 4K |
|---|---|---|---|---|
| 1:1 | 1024x1024 | 2048x2048 | 3072x3072 | 4096x4096 |
| 16:9 | 1312x736 | 2624x1472 | 3936x2208 | 5248x2944 |
| 9:16 | 736x1312 | 1472x2624 | 2208x3936 | 2944x5248 |
| 3:4 | 864x1152 | 1728x2304 | 2592x3456 | 3456x4608 |
| 4:3 | 1152x864 | 2304x1728 | 3456x2592 | 4608x3456 |
| 2:3 | 832x1248 | 1664x2496 | 2496x3744 | 3328x4992 |
| 3:2 | 1248x832 | 2496x1664 | 3744x2496 | 4992x3328 |
| 21:9 | 1568x672 | 3136x1344 | 4704x2016 | 6272x2688 |

## 视频 Video — `agnes-video-v2.0`（异步）

- 创建：`POST /v1/videos` → 返回 `task_id` + `video_id` + `status=queued`
- 查询（推荐）：`GET /agnesapi?video_id=<VIDEO_ID>`；兼容旧版 `GET /v1/videos/<TASK_ID>`
- 状态：`queued` / `in_progress` / `completed` / `failed`
- 完成时视频 URL 在 **`metadata.url`**（不是顶层 url）
- 时长：`seconds = num_frames / frame_rate`；`num_frames ≤ 441` 且满足 **8n+1**
  - 约 3 秒：81 帧 / 约 5 秒：121 帧 / 约 10 秒：241 帧 / 约 18 秒：441 帧（均 @24fps）
  - `frame_rate` 支持 1-60
- 尺寸：`width`/`height` 会被服务标准化到 480p / 720p / 1080p 三档
  （响应 `size` 与 `metadata.size_mapping` 是实际值；宽高比 16:9 默认 width1152 height768）
- 图生视频：顶层 `image`（需**可公开访问的 URL**）
- 关键帧动画：`extra_body.image`（URL 数组，≥2）+ `extra_body.mode: "keyframes"`
- 其它：`seed`（复现）、`num_inference_steps`、`negative_prompt`、`extra_body.mode`
- 错误码：400 参数错 / 401 未授权 / 404 找不到 / 503 繁忙稍后重试
- 提示词结构：文生视频 `[主体]+[动作]+[场景]+[镜头]+[光线]+[风格]`；图生视频描述"什么该动、什么保持稳定"；
  关键帧描述帧间过渡关系
