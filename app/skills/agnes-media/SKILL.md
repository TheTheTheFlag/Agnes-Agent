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
  keys_file: ~/.hermes/agnes_keys.json
  base_url: https://api.agnes-ai.cn
  model_image_default: agnes-image-2.1-flash
  model_image_legacy: agnes-image-2.0-flash
  model_video: agnes-video-v2.0
  size_default: "2K"
  ratio_default: "16:9"
---

# Agnes AI — 图片 & 视频生成

## 默认模型

| 类型 | 默认模型 | 说明 |
|------|----------|------|
| 图片 | `agnes-image-2.1-flash` | 用户说「2.0」时用 `agnes-image-2.0-flash` |
| 视频 | `agnes-video-v2.0` | 视频生成专用模型 |

## 判断类型

- 关键词：图、图片、image → 调用图片 API
- 关键词：视频、video、生成视频 → 调用视频 API
- 两者都有时优先按用户明确说的来

---

# 图片生成

## 基本信息

- **Endpoint**: `POST https://api.agnes-ai.cn/v1/images/generations`
- **认证**: `Authorization: Bearer <your_api_key>`（从 `~/.hermes/agnes_keys.json` 读取）
  **⚠️** `response_format` 必须放在 `extra_body` 里，不能放请求体顶层

## 模型选择

| 用户说法 | 使用模型 |
|----------|----------|
| 生成图片（默认） | `agnes-image-2.1-flash` |
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

**日志文件路径**: 与图片同目录，即 `/home/mirror/agnes-media/YYYYMMDD/log.md`

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

所有生成的图片**必须**保存到 `/home/mirror/agnes-media/` 目录下，按日期组织：

- **根目录**: `/home/mirror/agnes-media/`
- **每日子目录**: 格式为 `YYYYMMDD`，例如今天 `20260615`
- **完整路径示例**: `/home/mirror/agnes-media/20260615/img_0001.png`

**生成图片时**:
1. 先获取当天日期目录：`$(date +%Y%m%d)`
2. 确保目录存在：`mkdir -p /home/mirror/agnes-media/$(date +%Y%m%d)`
3. 图片保存到该日期目录下，文件名格式：`img_XXXX.png`（四位序号，从0001递增）
4. 日志中的 File 路径也要更新为 `/home/mirror/agnes-media/YYYYMMDD/img_XXXX.png`

**历史迁移**:
- 旧位置的文件（如 `/home/mirror/asian_girls_*/`, `/home/mirror/random_poses/`, `/home/mirror/knee_pose_*.png`, `/home/mirror/stand_pose_*.png`, `/home/mirror/girl_portrait*.png`）应迁移到对应日期的目录下
- 迁移后旧目录可删除

**日志格式（更新后）**:
```markdown
## 2026-06-15 14:30:25

**Prompt**: <完整提示词>

**URL**: https://platform-outputs.agnes-ai.space/images/text-to-image/2026/06/xxxx.png

**File**: /home/mirror/agnes-media/20260615/img_0001.png

---
```
- 视频生成也同理，使用日志文件 `~/.openclaw/agents/main/sessions/agnes_video_log.md`

## 异步批量生成

图片 API 为同步接口，批量生成需用 `ThreadPoolExecutor` 并发请求。

**使用场景**：同一 prompt 生成多张图、或不同 prompt 并发生成（最多 10 并发）。

**核心代码：**

```python
import json, requests, os
from concurrent.futures import ThreadPoolExecutor, as_completed

with open(os.path.expanduser("~/.hermes/agnes_keys.json")) as f:
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

output_dir = "/home/mirror/batch_imgs"
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

**发送批量图片到飞书**：按顺序遍历 `output_dir` 下的文件，逐个 `send_message(message="MEDIA:/home/mirror/batch_imgs/img_000.png")` 即可。

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
- **认证**: `Authorization: Bearer <your_api_key>`（从 `~/.hermes/agnes_keys.json` 读取）
  **⚠️** 视频是**异步任务**，需轮询

## 四种模式

| 模式 | 说明 | 关键参数 |
|------|------|----------|
| Text-to-Video | 文生视频 | `prompt` + `height`/`width` + `num_frames`/`frame_rate` |
| Image-to-Video | 图生视频 | `prompt` + `image`（单图URL） |
| Multi-Image Video | 多图视频 | `prompt` + `extra_body.image`（多图URL数组） |
| Keyframe Animation | 关键帧动画 | `prompt` + `extra_body.image` + `extra_body.mode: "keyframes"` |

### 1. Text-to-Video（文生视频）

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

### 2. Image-to-Video（图生视频）

```json
{
  "model": "agnes-video-v2.0",
  "prompt": "The woman slowly turns around, natural facial expression, cinematic camera movement",
  "image": "https://example.com/image.png",
  "num_frames": 121,
  "frame_rate": 24
}
```

### 3. Multi-Image Video（多图视频）

```json
{
  "model": "agnes-video-v2.0",
  "prompt": "Smooth transformation scene between the two images, cinematic lighting, consistent identity",
  "extra_body": {
    "image": ["https://example.com/1.png", "https://example.com/2.png"]
  },
  "num_frames": 121,
  "frame_rate": 24
}
```

### 4. Keyframe Animation（关键帧动画）

```json
{
  "model": "agnes-video-v2.0",
  "prompt": "Smooth cinematic transition between keyframes, natural camera movement",
  "extra_body": {
    "image": ["https://example.com/kf1.png", "https://example.com/kf2.png"],
    "mode": "keyframes"
  },
  "num_frames": 121,
  "frame_rate": 24
}
```

## 分辨率与帧数限制（实测）

| 分辨率 | 最大 num_frames | 17秒参数（24fps） |
|--------|-----------------|-------------------|
| 720p | 409 | height=720, width=1280, num_frames=409, frame_rate=24 |
| 480p | 961 | height=480, width=840, num_frames=441, frame_rate=24 |

**⚠️ num_frames 限制规则：**
- 必须 ≤ 分辨率对应的最大值
- 必须满足 `8n + 1`（即 1, 9, 17, 25, 33, 41, 49...）
- 超出限制返回 400 错误：`num_frames 441 exceeds maximum 409 for 720p`

**常用帧数速查（24fps）：**

| 时长 | num_frames | 适用分辨率 |
|------|------------|-----------|
| ~5秒 | 121 | 任意 |
| ~10秒 | 241 | 480p |
| ~17秒 | 409 | 720p |
| ~18秒 | 441 | 480p |

## 调用流程

1. **创建任务**: POST 到 `/v1/videos`，获取 `video_id`
2. **轮询查询**: 每 8 秒 GET `/agnesapi?video_id=<ID>`（避免 429 限速）
3. **等待完成**: 状态 `queued` → `processing` → `completed` / `failed`
4. **获取视频**: 从 `remixed_from_video_id` 提取视频 URL
5. **发送到飞书**: 下载到 `/home/mirror/` 后用 `MEDIA:/路径` 发送

## 响应格式

创建返回:
```json
{
  "video_id": "video_xxxxxx",
  "status": "queued",
  "seconds": "5.0",
  "size": "1152x768"
}
```

查询返回（完成时）:
```json
{
  "status": "completed",
  "progress": 100,
  "remixed_from_video_id": "https://storage.googleapis.com/agnes-aigc/.../video_xxxxxx.mp4",
  "error": null
}
```

## 响应状态

| status | 说明 |
|--------|------|
| queued | 排队中 |
| processing | 生成中 |
| completed | 完成，`remixed_from_video_id` 含视频 URL |
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
        video_url = data["remixed_from_video_id"]
        break
    elif data.get("status") == "failed":
        raise Exception(f"Video failed: {data.get('error')}")
    time.sleep(8)  # ≥8s to avoid 429 rate limit
```

## 参数说明

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
| seed | integer | ❌ | 随机种子，用于可复现结果 |
| negative_prompt | string | ❌ | 反向提示词，描述需要避免的内容 |
| extra_body.image | array | ❌ | 关键帧模式下的输入图片 URL 数组 |
| extra_body.mode | string | ❌ | 附加模式设置，例如 `keyframes` |

## 注意事项

- **图片**: `response_format` 必须放 `extra_body` 里，不能放请求体顶层，否则 400
- **视频**: 异步任务，耗时 30-120 秒，需轮询
- **发送文件**: 下载到 `/home/mirror/` 后用 `MEDIA:/路径` 发飞书
- **API key**: 从 `~/.hermes/agnes_keys.json` 读取（`json.load`），键名为 `"agnes"`，不要再硬编码 key
- **发送文件**: 下载到 `/home/mirror/` 后用 `MEDIA:/路径` 发飞书
---

## 🐍 Python SDK

已提供两个 Python 脚本简化调用：
- `image_generator.py` — 文生图 / 图生图
- `video_generator.py` — 文生视频 / 图生视频 / 关键帧动画

参数字典格式，不常用参数有默认值可省略。详见脚本文件。
