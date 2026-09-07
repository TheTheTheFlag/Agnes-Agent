---
name: agnes-media
description: Agnes AI — image and video generation (text-to-image, image-to-image, text-to-video, image-to-video, multi-image video, keyframe animation)
triggers:
  - 生成图片
  - 生成视频
  - 文生图
  - 文生视频
  - 图生图
  - 图生视频
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
  size_default: "720P"
  ratio_default: "16:9"
---

# Agnes AI — 图片 & 视频生成

> **本技能目录 `<skill_dir>`** = `app/skills/agnes-media/`（相对项目根；SKILL.md、image_generator.py、video_generator.py、keys.json、output/ 均在此目录）。
> 密钥一律从 `<skill_dir>/keys.json` 读取；所有生成产物（图片/视频/日志）一律保存到 `<skill_dir>/output/` 下。执行前可先 `ls app/skills/agnes-media/` 确认。

## 默认模型

| 类型 | 默认模型 | 说明 |
|------|----------|------|
| 图片 | `agnes-image-2.5-flash` | 用户说「2.0」时用 `agnes-image-2.0-flash` |
| 视频 | `agnes-video-v2.0` | 视频生成唯一模型，需指定 height/width/num_frames |

## 判断类型

- 关键词：图、图片、image → 调用图片 API
- 关键词：视频、video、生成视频 → 调用视频 API
- 两者都有时优先按用户明确说的来

---

# 图片生成

## 基本信息

- **Endpoint**: `POST https://api.agnes-ai.cn/v1/images/generations`
- **认证**: `Authorization: Bearer <your_api_key>`（从 `<skill_dir>/keys.json` 读取）
  **⚠️** `response_format` 必须放在 `extra_body` 里，不能放请求体顶层

## 模型选择

| 用户说法 | 使用模型 |
|----------|----------|
| 生成图片（默认） | `agnes-image-2.5-flash` |
| 用 2.0 / Agnes Image 2.0 Flash | `agnes-image-2.0-flash` |

## 请求格式

```json
{
  "model": "<选择的模型，默认 agnes-image-2.1-flash>",
  "prompt": "<描述>",
  "size": "1K",      // 推荐值: 1K、2K、3K、4K；也支持精确尺寸如 1024x1024
  "ratio": "1:1",   // 可选: 1:1、3:4、4:3、16:9、9:16、2:3、3:2、21:9
  "return_base64": false,  // 如需 Base64 返回设为 true
  "extra_body": {
    "response_format": "url"   // 或 "b64_json"
  }
}
```

## 尺寸与宽高比

推荐使用 `size` + `ratio` 组合获得可预期输出：
- **size**: `1K`、`2K`、`3K`、`4K`
- **ratio**: `1:1`、`3:4`、`4:3`、`16:9`、`9:16`、`2:3`、`3:2`、`21:9`

> ⚠️ 直接使用精确尺寸（如 1920x1080）可能会被标准化映射到最接近的档位。

## 三种模式

### 1. Text-to-Image（文生图）

```json
{
  "model": "agnes-image-2.1-flash",
  "prompt": "A beautiful sunset over the ocean, photorealistic",
  "size": "1024x1024",
  "extra_body": { "response_format": "url" }
}
```

### 2. Image-to-Image（图生图）

`image` 为 base64 data URI 数组（放请求体顶层，不是 extra_body）：

```json
{
  "model": "agnes-image-2.1-flash",
  "prompt": "Transform into cyberpunk style, preserve main subject",
  "size": "1024x1024",
  "image": ["data:image/png;base64,iVBORw0KGgo..."],
  "extra_body": { "response_format": "url" }
}
```

**调用方式**：用户给参考图时，读取本地图片 → base64 编码 → 放入 `image` 数组

### 3. Multi-Image Composition（多图合成）

`image` 为 base64 data URI 数组：

```json
{
  "model": "agnes-image-2.1-flash",
  "prompt": "Combine these two characters into one scene",
  "size": "1024x1024",
  "image": ["data:image/png;base64,iVBOR...", "data:image/png;base64,iVBOR..."],
  "extra_body": { "response_format": "url" }
}
```

## 响应

| 字段 | 说明 |
|------|------|
| data[0].url | 图片 URL（`response_format: url`） |
| data[0].b64_json | Base64 数据（`response_format: b64_json`） |

## 调用流程

1. 判断模型：默认 `agnes-image-2.1-flash`，用户说「2.0」才切换
2. 组装请求体（`response_format` 必须放 `extra_body` 内）
3. POST 到 `/v1/images/generations`
4. 从 `data[0].url` 提取图片 URL
5. **将 prompt 和 URL 追加写入日志文件**（见下方日志格式）
6. **图片必须保存到指定目录**（见下方存储路径要求）
7. 下载到本地后用 `MEDIA:/路径` 发送到飞书

## 调用日志

每次图片生成完成后，必须将 prompt 和 URL 追加写入日志文件。

**日志文件路径**: 与图片同目录，即 `<skill_dir>/output/YYYYMMDD/log.md`

**日志格式**:
```markdown
## 2026-06-14 15:30:25

**Prompt**: <完整提示词>

**URL**: https://platform-outputs.agnes-ai.space/images/text-to-image/2026/06/xxxx.png

---
```

**写入时机**: 每次成功获取图片 URL 后立即追加写入（使用 `exec` 执行 `cat >>` 或 `echo` 追加），不要等到任务全部完成。

**注意**:
- prompt 写入前不做截断，写完整内容
- 批量生成时每张都要单独追加一条记录

## 图片存储路径要求

所有生成的图片**必须**保存到 `<skill_dir>/output/` 目录下，按日期组织：

- **根目录**: `<skill_dir>/output/`
- **每日子目录**: 格式为 `YYYYMMDD`，例如今天 `20260615`
- **完整路径示例**: `<skill_dir>/output/20260615/img_0001.png`

**生成图片时**:
1. 先获取当天日期目录：`$(date +%Y%m%d)`
2. 确保目录存在：`mkdir -p <skill_dir>/output/$(date +%Y%m%d)`
3. 图片保存到该日期目录下，文件名格式：`img_XXXX.png`（四位序号，从0001递增）
4. 日志中的 File 路径也要更新为 `<skill_dir>/output/YYYYMMDD/img_XXXX.png`

**历史迁移**:
- 旧位置的文件（如 `/home/mirror/asian_girls_*/`, `/home/mirror/random_poses/`, `/home/mirror/knee_pose_*.png`, `/home/mirror/stand_pose_*.png`, `/home/mirror/girl_portrait*.png`）应迁移到对应日期的目录下
- 迁移后旧目录可删除

**日志格式（更新后）**:
```markdown
## 2026-06-15 14:30:25

**Prompt**: <完整提示词>

**URL**: https://platform-outputs.agnes-ai.space/images/text-to-image/2026/06/xxxx.png

**File**: <skill_dir>/output/20260615/img_0001.png

---
```
- 视频生成也同理，使用日志文件 `<skill_dir>/output/video_log.md`

## 异步批量生成

图片 API 为同步接口，批量生成需用 `ThreadPoolExecutor` 并发请求。

**使用场景**：同一 prompt 生成多张图、或不同 prompt 并发生成（最多 10 并发）。

**核心代码：**

```python
import json, requests, os
from concurrent.futures import ThreadPoolExecutor, as_completed

with open("<skill_dir>/keys.json") as f:
    keys = json.load(f)
api_key = keys["agnes"]

url = "https://api.agnes-ai.cn/v1/images/generations"
headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

def generate_one(args):
    idx, prompt, model, size = args
    payload = {
        "model": model,
        "prompt": prompt,
        "size": size,
        "extra_body": {"response_format": "url"}
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    data = resp.json()
    return idx, data["data"][0]["url"]

# N 个任务
tasks = [
    (0, "prompt 1", "agnes-image-2.1-flash", "1024x1024"),
    (1, "prompt 2", "agnes-image-2.1-flash", "1024x1024"),
    # ...
]

output_dir = "<skill_dir>/output/batch"
os.makedirs(output_dir, exist_ok=True)

with ThreadPoolExecutor(max_workers=5) as executor:
    futures = {executor.submit(generate_one, t): t[0] for t in tasks}
    for future in as_completed(futures):
        idx, img_url = future.result()
        path = f"{output_dir}/img_{idx:03d}.png"
        with open(path, "wb") as f:
            f.write(requests.get(img_url).content)
        print(f"Saved {path}")
```

**参数说明：**

| 参数 | 说明 |
|------|------|
| `max_workers` | 并发数，建议 ≤10；超过可能触发 API 限流 |
| `timeout=60` | 单次请求超时，避免阻塞 |
| `size` | 支持 `1024x1024`、`1024x1280`、`1280x1024` |

**发送批量图片到飞书**：按顺序遍历 `output_dir` 下的文件，逐个 `send_message(message="MEDIA:<skill_dir>/output/batch/img_000.png")` 即可。

## Portrait（人物）Prompt 指南

生成人物写真时，prompt 必须包含以下三类元素，否则图片会显得呆板：

| 类别 | 要写什么 | 示例 |
|------|----------|------|
| 表情 | 自然的情绪描写，不能只写"beautiful" | `a subtle natural half-smile`, `warm flirtatious gaze`, `soft gentle expression` |
| 动作/姿态 | 具体的身体动作，不是站立的正面照 | `one hand gently touching her collarbone`, `head slightly tilted`, `hair flowing in breeze`, `elegant contrapposto stance` |
| 氛围/光线 | 让角色活起来的光影 | `cinematic side lighting`, `warm golden rim light`, `soft dreamy bokeh` |

**常见错误**：只写 `beautiful girl with gold jewelry` → 结果是僵硬的正面证件照感。
**正确写法**：主体 + 表情 + 动作 + 场景 + 光线 + 风格，全部写全。

英文写生图的参考结构：
```
[主体外观] + [表情] + [动作/姿态] + [配饰/服装细节] + [背景/场景] + [光线/氛围] + [艺术风格]
```

---

# 视频生成

## 基本信息

- **创建任务**: `POST https://api.agnes-ai.cn/v1/videos`
- **查询结果（推荐）**: `GET https://api.agnes-ai.cn/agnesapi?video_id=<VIDEO_ID>`
- **认证**: `Authorization: Bearer <your_api_key>`（从 `<skill_dir>/keys.json` 读取）
  **⚠️** 视频是**异步任务**，需轮询

## 视频模型

当前唯一支持的模型是 `agnes-video-v2.0`，需要手动指定分辨率和帧数。

### agnes-video-v2.0 参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| model | string | ✅ | `agnes-video-v2.5-flash`（推荐）或 `agnes-video-v2.0`（旧版） |
| prompt | string | ✅ | 视频内容描述 |
| size | string | ✅ | 固定 `"720P"`（2.5-flash 专用） |
| seconds | integer | ✅ | 时长（秒），2.5-flash 专用 |
| ratio | string | ❌ | 宽高比：`21:9`, `16:9`, `4:3`, `1:1`, `3:4`, `9:16`（2.5-flash 专用） |
| duration | string | ❌ | 时长字符串，兼容旧格式 |
| image | string/array | ❌ | 图片 URL，单图或数组（最多 5 张） |
| mode | string | ❌ | 生成模式：`"ti2vid"`（图生视频）、`"keyframes"`（关键帧） |
| seed | integer | ❌ | 随机种子 |
| num_inference_steps | integer | ❌ | 推理步数 |

**固定规格**：
- 分辨率：720p（固定）
- 各比例对应像素：`16:9`=1280×720, `9:16`=720×1280, `1:1`=720×720, `21:9`=1680×720, `4:3`=960×720, `3:4`=720×960

### agnes-video-v2.0 参数（旧版）

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| model | string | ✅ | `agnes-video-v2.0` |
| prompt | string | ✅ | 视频内容描述 |
| height | integer | ❌ | 视频高度（默认 768） |
| width | integer | ❌ | 视频宽度（默认 1152） |
| num_frames | integer | ❌ | 帧数（必须 ≤ 441 且满足 8n+1） |
| frame_rate | number | ❌ | 帧率（1-60，默认 24） |
| image | string | ❌ | 图生视频使用的图片 URL |
| extra_body.image | array | ❌ | 多图/关键帧图片数组 |
| extra_body.mode | string | ❌ | 模式：`"keyframes"` |
| negative_prompt | string | ❌ | 反向提示词 |
| seed | integer | ❌ | 随机种子 |
| num_inference_steps | integer | ❌ | 推理步数 |

## 调用示例

### Text-to-Video（文生视频）

```json
{
  "model": "agnes-video-v2.0",
  "prompt": "A cat walking on the beach at sunset, soft ocean waves, warm golden lighting, realistic motion",
  "height": 768,
  "width": 1152,
  "num_frames": 121,
  "frame_rate": 24
}
```

### Image-to-Video（图生视频）

```json
{
  "model": "agnes-video-v2.0",
  "prompt": "The woman slowly turns around, natural facial expression, cinematic camera movement",
  "height": 768,
  "width": 1152,
  "num_frames": 121,
  "image": "https://example.com/image.png"
}
```

## 调用流程

1. **创建任务**: POST 到 `/v1/videos`，获取 `video_id`
2. **轮询查询**: 每 8 秒 GET `/agnesapi?video_id=<ID>`（避免 429 限速）
3. **等待完成**: 状态 `queued` → `processing` → `completed` / `failed`
4. **获取视频**: 从顶层 `url` 字段提取视频 URL
5. **发送到飞书**: 下载到 `<skill_dir>/output/` 后用 `MEDIA:/路径` 发送

## 响应格式

### 创建返回（v2.0）
```json
{
  "id": "task_xxxxxx",
  "video_id": "video_xxxxxx",
  "task_id": "task_xxxxxx",
  "object": "video",
  "model": "agnes-video-v2.0",
  "status": "queued",
  "progress": 0,
  "created_at": 1788493348,
  "seconds": "5.0",
  "size": "1088x832"
}
```

### 查询返回（完成时，v2.0）
```json
{
  "status": "completed",
  "url": "https://cos-platform-outputs.agnes-ai.cn/videos/agnes-video-v2.0/video_xxxxxx.mp4",
  "progress": 100,
  "error": null
}
```

> **注意**：v2.0 使用顶层 `url` 字段，2.5-flash 可能使用 `remixed_from_video_id`

## 响应状态

| status | 说明 |
|--------|------|
| queued | 排队中 |
| in_progress | 生成中 |
| completed | 完成，`url` 含视频 URL |
| failed | 失败，`error` 字段含错误信息 |

## 轮询代码

```python
import time, requests

video_id = "<video_id>"
while True:
    resp = requests.get(
        "https://api.agnes-ai.cn/agnesapi",
        params={"video_id": video_id},
        headers={"Authorization": "Bearer <KEY>"}
    )
    data = resp.json()
    if data.get("status") == "completed":
        video_url = data.get("url")
        break
    elif data.get("status") == "failed":
        raise Exception(f"Video failed: {data.get('error')}")
    time.sleep(8)  # ≥8s to avoid 429 rate limit
```

## 参数说明

### agnes-video-v2.0 参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| model | string | ✅ | 模型名称，使用 `agnes-video-v2.0` |
| prompt | string | ✅ | 视频内容的文本描述 |
| image | string | ❌ | 图生视频使用的图片 URL |
| mode | string | ❌ | 生成模式，例如 `ti2vid` 或 `keyframes` |
| height | integer | ❌ | 视频高度，默认 768 |
| width | integer | ❌ | 视频宽度，默认 1152 |
| num_frames | integer | ❌ | 视频帧数，必须 ≤ 441 且遵循 8n + 1 规则 |
| frame_rate | number | ❌ | 视频帧率，支持范围 1–60 |
| num_inference_steps | integer | ❌ | 推理步数 |
| seed | integer | ❌ | 随机种子 |
| negative_prompt | string | ❌ | 反向提示词 |
| extra_body.image | array | ❌ | 关键帧模式下的输入图片 URL 数组 |
| extra_body.mode | string | ❌ | 附加模式设置，例如 `keyframes` |

## 注意事项

- **图片**: `response_format` 必须放 `extra_body` 里，不能放请求体顶层，否则 400
- **视频**: 异步任务，耗时 30-120 秒，需轮询
- **发送文件**: 下载到 `<skill_dir>/output/` 后用 `MEDIA:/路径` 发飞书
- **API key**: 从 `<skill_dir>/keys.json` 读取（`json.load`），键名为 `"agnes"`，不要再硬编码 key
- **发送文件**: 下载到 `<skill_dir>/output/` 后用 `MEDIA:/路径` 发飞书
---

## 🐍 Python SDK

已提供两个 Python 脚本简化调用：
- `image_generator.py` — 文生图 / 图生图
- `video_generator.py` — 文生视频 / 图生视频 / 关键帧动画

参数字典格式，不常用参数有默认值可省略。详见脚本文件。
