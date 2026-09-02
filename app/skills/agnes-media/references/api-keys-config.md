# Agnes API Keys 配置

## 文件位置

`~/.hermes/agnes_keys.json`

## 内容格式

```json
{
  "agnes": "<Agnes API key>",
  "dashscope": "<DashScope API key>"
}
```

## 读取方式

```python
import json, os
keys = json.load(open(os.path.expanduser("~/.hermes/agnes_keys.json")))
agnes_key = keys["agnes"]
dashscope_key = keys["dashscope"]
```

## 为什么用配置文件

sandbox 环境的 terminal 工具会对含 `sk-` 前缀的字符串做过滤（替换为 `***`），导致 401 "无效的令牌"。execute_code 工具无此限制，但 key 写死在 skill 里不方便维护。

**经验**：所有含 API key 的 skill 都读这个配置文件，不要硬编码 key。
