# Agnes API Keys 配置

## 文件位置

`<skill_dir>/keys.json`（技能目录下，即相对项目根 `app/skills/agnes-media/keys.json`）。
技能自包含：不读取 `~/.hermes/agnes_keys.json` 等外部路径。

## 内容格式

```json
{
  "agnes": "<Agnes API key>"
}
```

## 读取方式

```python
import json, os
skill_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # references/.. = 技能目录
keys = json.load(open(os.path.join(skill_dir, "keys.json")))
agnes_key = keys["agnes"]
```

## 为什么用配置文件

sandbox 环境的 terminal 工具会对含 `sk-` 前缀的字符串做过滤（替换为 `***`），导致 401 "无效的令牌"。
execute_code 工具无此限制，但 key 写死在 skill 里不方便维护。

**经验**：所有含 API key 的 skill 都读技能目录下的 `keys.json`，不要硬编码 key。
keys.json 已被技能目录的 `.gitignore` 忽略，不会入库。
