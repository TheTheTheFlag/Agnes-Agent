# 🤖 Agnes Agent

基于 **LangGraph** 的分层记忆智能体：模型自决工具调用、5 层记忆系统、多 Key 自动轮换、沉浸式 Web 调试面板。

---

## ✨ 功能特性

| 特性 | 说明 |
|---|---|
| 🧠 **模型自决** | 无 L1/L2/L3 硬路由，LLM 自行决定"直接回答 / 调工具 / 进入规划" |
| 🛠 **工具调用** | 9 个工具：系统命令、Tavily 搜索、用户信息/偏好、HTML 校验、记忆读取、规划触发 |
| 🔑 **多 Key 轮换** | api_key 逗号分隔多 key，报错（限流/连接/超时/鉴权）自动换下一个 + 指数退避 |
| 🗂 **5 层记忆** | Working / Thread / User Profile / Episodic / Procedural / Semantic Cache |
| 💬 **沉浸式面板** | 全屏对话 + 工具调用卡片可视化 + 调试抽屉（State/日志/记忆/Memory DB/定时任务） |
| ⚙ **模型管理** | 设置页接入任意 OpenAI 兼容模型，自动拉取模型列表，设默认持久化 |
| 📋 **可审查** | 输入/输出流、token 统计、工具耗时、checkpoint 深度解包、Thread 回放 |

---

## 📂 目录结构

```
Agnes-Agent/
├── app/                        # 应用主包
│   ├── main.py                 # 入口（python -m app.main）
│   ├── config.py               # 配置中心（统一路径，数据指向 data/）
│   ├── graph/                  # LangGraph 工作流
│   │   ├── builder.py          # build_graph + 节点 + 路由
│   │   ├── state.py            # State / TaskPlan / Subtask
│   │   ├── utils.py            # token 计数/压缩、工具调用解析、prompt 加载
│   │   └── prompt_template.txt # 系统提示词模板
│   ├── llm/                    # LLM 纯工厂（零配置，多 key 轮换）
│   ├── memory/                 # 分层记忆管理器（SQLite）
│   ├── planning/               # 规划-执行-验证-总结 + ReAct 循环
│   ├── tools/                  # 工具集（9 个）
│   └── server/                 # Web 服务
│       ├── __init__.py         # FastAPI 组装 + start_debug_server + 定时任务
│       ├── store.py            # 内存快照 / 日志 / 事件流
│       ├── config.py           # 模型配置管理（厂商目录/凭据/默认模型）
│       ├── api/                # API 子路由（system/memory/tools/chat + models）
│       └── static/             # 前端（index.html + marked.min.js 离线可用）
├── data/                       # 运行时数据
│   ├── memory.db               # 记忆 SQLite（画像/偏好/任务/命令/语义缓存）
│   ├── checkpoints.db          # LangGraph checkpoint
│   └── .model_config           # 模型接入/默认模型配置
├── configs/                    # 配置示例（预留）
├── tests/                      # 测试（预留）
├── docs/                       # 文档（预留）
├── deliverables/               # Agent 生成的交付物
├── .env                        # 非模型密钥（DASHSCOPE/TAVILY/DEEPSEEK）
├── pyproject.toml              # 项目元数据 + 依赖 + 命令行入口
├── uv.lock                     # 锁定依赖（uv）
└── README.md
```

---

## 🚀 环境准备

### 1. 依赖工具

- **Python 3.12+**（项目 `.python-version` 指定 3.12）
- **uv**（推荐，依赖管理）或 pip
- Git（可选）

### 2. 安装依赖

```powershell
# 方式一：uv（推荐，自动创建 .venv 并安装）
cd C:\Users\17625\Agnes-Agent
uv sync

# 方式二：已有 .venv + pip
cd C:\Users\17625\Agnes-Agent
python -m venv .venv
.venv\Scripts\pip install -r <(uv export --format requirements)   # 或手动装 pyproject.toml 里的依赖
```

> 若使用项目自带 `.venv`（已装好依赖），可跳过安装步骤。

---

## 💻 进入虚拟环境

### Windows PowerShell

```powershell
cd C:\Users\17625\Agnes-Agent
.\.venv\Scripts\Activate.ps1
# 激活后提示符出现 (Agnes-Agent)，验证：
python --version        # 应输出 Python 3.12.x
```

> 若 PowerShell 禁止执行脚本，先运行：`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

### Windows CMD

```cmd
cd C:\Users\17625\Agnes-Agent
.venv\Scripts\activate.bat
```

### Git Bash / WSL

```bash
cd /c/Users/17625/Agnes-Agent
source .venv/Scripts/activate
```

### 退出虚拟环境

```powershell
deactivate
```

---

## ▶️ 启动

### 方式一：虚拟环境内（推荐）

```powershell
# 先激活虚拟环境（见上文），然后：
python -m app.main
```

### 方式二：直接用 venv 的 python（不激活）

```powershell
.\.venv\Scripts\python.exe -m app.main
```

### 方式三：命令行入口（已 pip install -e . 时）

```powershell
agnes-agent
```

启动后输出示例：

```
LLM 类型: <class 'app.llm.llm_factory.RotatingKeyChatOpenAI'> | provider=openai_compatible model=agnes-2.5-flash
🔍 控制面板: http://localhost:8000
[thread_id] 618a8084-869b-42a1-a29c-dda3dec389a7
🤖 Agent 已启动，输入 'quit' 或 'exit' 退出。
```

- **Web 面板**：浏览器打开 http://localhost:8000（8000 被占用自动顺延 8001+）
- **CLI 对话**：终端直接输入消息
- `--new` 参数开启新会话：`python -m app.main --new`

---

## 🖥 使用说明

### 沉浸式对话面板（http://localhost:8000）

- 默认进入 **💬 对话**：全屏聊天，输入即发送（Enter），`/` 触发命令提示
- 对话过程中**工具调用以卡片展示**（工具名 + 状态 + 输入/输出），审批类工具弹出确认气泡
- 右上角 **⚙ 面板** 打开调试抽屉：

| Tab | 用途 |
|---|---|
| 📊 State | 当前 LangGraph state |
| 📄 提示词 | 当前 system prompt（含分层记忆注入） |
| 📝 日志 / 📋 事件 | ReAct 执行日志与事件流 |
| 🧠 5层记忆 | 各层记忆快照 + 会话选择器 + Prompt 注入预览 |
| 🗄 Memory DB | SQLite 表浏览器（增删改查） |
| 🔧 工具 | 已注册工具列表 |
| 🗓 定时任务 | interval / 每日任务调度 |
| ⚙ 设置 | 默认模型 + 模型接入管理 |

### 斜杠命令

| 命令 | 功能 |
|---|---|
| `/new` | 开新对话（新 thread_id） |
| `/resume [tid]` | 继续指定 thread |
| `/threads` | 列出最近会话 |
| `/model` | 查看当前模型 |
| `/system [m]` | 切换审批模式（per_ask / session_allow / always_allow） |
| `/clear` | 清空日志/事件 |
| `/save` | 导出当前会话消息 |
| `/help` | 帮助 |

---

## ⚙️ 模型管理

### 配置存储

模型凭据统一存放在 `data/.model_config`：

```json
{
  "provider": "agnes2.0",
  "model": "agnes-2.5-flash",
  "custom": [
    {
      "id": "openai_compatible",
      "label": "OpenAI 兼容网关 (llm.chatops.fun)",
      "base_url": "https://llm.chatops.fun/v1",
      "api_key": "sk-xxxx",
      "models": ["deepseek-v4-pro", "glm-5.1", "MiniMax-M3"]
    }
  ]
}
```

### 接入自定义模型（⚙ 设置页）

1. 填 **名称** + **base_url**（如 `https://api.openai.com/v1`）+ **api_key**（多个 key 用英文逗号隔开自动轮换）
2. 点 **🔄 获取** 自动从网关拉取模型列表（GET /v1/models），或手动填
3. 点 **✅ 接入** → 列表出现，可「使用」或「设为默认」

### 多 Key 自动轮换

api_key 填 `key1,key2,key3`：单个 key 报错（限流/连接/超时/鉴权）自动换下一个，全部失败后指数退避重试（2/4/8/16/30s，最多 5 轮）。

### 内置厂商

| provider | 说明 |
|---|---|
| `openai_compatible` | 任意 OpenAI 兼容网关 |
| `agnes2.0` | Agnes 网关 |
| `deepseek` | DeepSeek 官方 |
| `tongyi` | 通义千问（DashScope） |

---

## 🧠 5 层记忆系统

| 层 | 内容 | 存储 | 触发 |
|---|---|---|---|
| **L0** Working | 当前对话上下文 | LangGraph state | 自动 |
| **L1** Thread | 会话摘要 | task_summaries | summarizer 节点 |
| **L2** User Profile | 用户画像/偏好 | user_profile / user_preferences | 写工具 + 每轮注入 prompt |
| **L3** Episodic | 历史任务/对话 | task_plans / subtasks / messages | 任务执行 + 每轮注入 |
| **L4** Procedural | 命令历史 | command_history | system_command 后自动记录 |
| **L5** Semantic | 外部知识缓存 | semantic_cache（TTL 24h） | tavily 搜索后自动缓存 |

**记忆读取**：每轮自动把 L2/L3/L4 摘要注入 system prompt（模型"自然记住"用户）；模型也可主动调 `search_my_memory` / `list_my_recent_tasks` / `get_command_history` 查详情。

---

## 🔧 配置

### .env（非模型密钥）

```
DASHSCOPE_API_KEY=sk-...
TAVILY_API_KEY=tvly-...
DEEPSEEK_API_KEY=sk-...
```

> 模型 key（OPENAI/AGNES）**不放在 .env**，统一在 `data/.model_config` 管理（页面设置操作）。

### 数据文件

| 文件 | 说明 |
|---|---|
| `data/memory.db` | 记忆 + 消息 + 命令历史 + 语义缓存 |
| `data/checkpoints.db` | LangGraph 会话 checkpoint |
| `data/.model_config` | 模型接入 + 默认模型 |

---

## ❓ 常见问题

**Q: 端口 8000 被占用？**
A: 自动顺延到 8001/8002…，以启动输出为准。

**Q: 对话很慢 / 卡住？**
A: 网关多 key 轮换会逐个尝试（每个 key 30s 超时）。可在 `app/llm/llm_factory.py` 的 `_build_openai_client` 调小 `timeout`（如 10s）。

**Q: tiktoken 首次下载慢？**
A: 已内置 3s 超时 fallback 到本地编码，不会卡 20s。

**Q: 已接入模型显示 0 个？**
A: `data/.model_config` 的 `custom` 为空即 0 个，接入后显示。

**Q: 切换了模型但没生效？**
A: 模型切换会重建 graph 并持久化到 `.model_config`；重启自动用默认模型。

---

## 🧪 开发

```powershell
# 运行测试（预留目录）
python -m pytest tests/

# 依赖锁定
uv lock
```
