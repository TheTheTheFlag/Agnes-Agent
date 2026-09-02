# Agnes Image API 实测参考

> 官方文档 (agnes-ai.com/doc/agnes-image-21-flash) 时常超时不可访问，以下为实际调用验证结果。

## 图生图格式（已验证 ✅）

```
POST /v1/images/generations
Body:
{
  "model": "agnes-image-2.1-flash",
  "prompt": "描述",
  "size": "1024x1024",
  "image": ["data:image/png;base64,<base64字符串>"],
  "extra_body": {"response_format": "url"}
}
```

**关键点**：
- `image` 是 base64 data URI 数组，不是 URL 数组
- `data:image/png;base64,` 或 `data:image/jpeg;base64,` 前缀必须有
- `image` 放请求体**顶层**，不是 `extra_body` 里
- `response_format` 依然放 `extra_body` 内

## 响应格式

```json
{"data": [{"url": "https://...", "b64_json": null, "revised_prompt": null}]}
```

图片 URL 在 `data[0].url`，下载后用 `MEDIA:/路径` 发飞书。

## Keys 读取方式

```python
import json, os
keys = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "keys.json")))
agnes_key = keys["agnes"]       # Agnes API key（keys.json 与本脚本同目录）
```

## 已知限制

- terminal 工具会过滤含 key 的字符串导致 401，**必须用 execute_code** 发起含 key 的请求
- sandbox 环境偶尔随机报 `无效的令牌`，重试通常成功
