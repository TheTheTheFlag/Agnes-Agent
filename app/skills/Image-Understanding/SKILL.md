---
name: Image-Understanding
description: 通义千问图片理解 — 用 qwen3.7-plus 视觉模型识别图片内容
triggers:
  - 识别图片
  - 图片识别
  - 图片内容
  - 看图
  - 分析图片
  - 理解图片
  - 图片理解
config:
  keys_file: keys.json
  base_url: https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions
  model: qwen3.7-plus
---

# 图片理解

## 基本信息

- **Endpoint**: `POST https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions`
- **模型**: `qwen3.7-plus`（视觉语言模型）
- **认证**: `Authorization: Bearer ***

## 请求格式

```json
{
  "model": "qwen3.7-plus",
  "messages": [
    {
      "role": "user",
      "content": [
        {
          "type": "image_url",
          "image_url": {
            "url": "<图片URL或base64>"
          }
        },
        {
          "type": "text",
          "text": "<识别指令>"
        }
      ]
    }
  ]
}
```

## 调用流程

1. **获取图片**: 用户发的图片 URL 或本地路径
   - 网络 URL：直接使用
   - 本地文件：先下载/读取后转 base64 或上传到可访问URL
2. **组装请求**: `model` + `messages` + `image_url` + `prompt`
3. **发送请求**: POST 到 `/compatible-mode/v1/chat/completions`
4. **返回结果**: 从 `choices[0].message.content` 提取识别结果
5. **回复用户**: 把识别内容发给用户

## 图片输入方式

### 方式一：网络 URL（推荐）

图片 URL 需要可公网访问：

```json
{
  "type": "image_url",
  "image_url": {
    "url": "https://example.com/image.jpg",
    "min_pixels": 3072,
    "max_pixels": 8388608
  }
}
```

### 方式二：Base64 Data URI

如果图片是本地文件或不可公网访问，转 Base64：

```json
{
  "type": "image_url",
  "image_url": {
    "url": "data:image/jpeg;base64,/9j/4AAQSkZJRg...",
    "min_pixels": 3072,
    "max_pixels": 8388608
  }
}
```

## prompt 模板

根据用户需求构造指令，常用模板：

```
请提取图片中的XXX信息，要求准确无误，不要遗漏和捏造虚假信息。返回数据格式以json方式输出。
```

示例（火车票）：
```
请提取车票图像中的发票号码、车次、起始站、终点站、发车日期和时间点、座位号、席别类型、票价、身份证号码、购票人姓名。要求准确无误的提取上述关键信息、不要遗漏和捏造虚假信息，模糊或者强光遮挡的单个文字可以用英文问号?代替。返回数据格式以json方式输出，格式为：{'发票号码'：'xxx', '车次'：'xxx', '起始站'：'xxx', '终点站'：'xxx', '发车日期和时间点'：'xxx', '座位号'：'xxx', '席别类型'：'xxx','票价':'xxx', '身份证号码'：'xxx', '购票人姓名'：'xxx'}
```

## 注意事项

- 图片 URL 必须公网可访问，或使用 base64 编码
- `min_pixels` / `max_pixels` 控制图片Token数量，影响精度和成本
- 该模型也可用于 general vision 任务，不只是 OCR
- API key 从本技能目录 `keys.json` 读取（`json.load`，键名为 `"dashscope"`，即相对项目根
  `app/skills/Image-Understanding/keys.json`），不要再硬编码 key
