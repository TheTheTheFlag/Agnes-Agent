# 🤖 Agnes Agent

> 一个亲手搭建的 LangGraph 智能体：模型自决工具调用、分层记忆 + 知识图谱、多 Key 自动轮换、DAG 多步规划、沉浸式 Web 调试面板。
> 目标不是"调 API 出结果"，而是把 Agent 的"思考—行动—观察"循环一层层拆开，看明白再动手。

![Python](https://img.shields.io/badge/Python-3.12-blue) ![Status](https://img.shields.io/badge/Status-学习中-orange) ![License](https://img.shields.io/badge/License-MIT-green)

🛠️ **学习实验项目** —— 以理解 ReAct / LangGraph / LightRAG 机制为主要目的，欢迎提 Issue 交流，勿期待生产级稳定性。

---

## 📑 目录

- [效果演示](#-效果演示)
- [项目背景](#-项目背景)
- [核心特性](#-核心特性)
- [快速上手](#-快速上手)
- [业务流程（从一次对话到记忆落库）](#-业务流程从一次对话到记忆落库)
- [模块架构（按 Agent 能力分类）](#-模块架构按-agent-能力分类)
- [设计思路与实现过程](#-设计思路与实现过程)
- [踩坑与解决实录](#-踩坑与解决实录)
- [项目文件结构](#-项目文件结构)
- [Roadmap 与致谢](#-roadmap-与致谢)

---

## 🎬 效果演示

启动后，终端会打出模型自决决策的日志；对话过程中每一次工具调用都会在 Web 面板上实时展示（工具名 + 参数 + 结果）。

```text
LLM 类型: <class 'app.llm.llm_factory.RotatingKeyChatOpenAI'> | provider=openai_compatible model=agnes-2.5-flash
🔍 控制面板: http://localhost:8081
[10:17:03] OK    控制台日志已启动（后台服务模式，对话请到调试面板）
[10:17:03] ▶ 节点开始 chatbot
🤖 你最近的完成任务有：1. 开发网页版贪吃蛇游戏 ……（Web 面板同步展示工具卡片与状态行）
```

Web 面板（默认 http://localhost:8081，被占自动顺延）：

- 左侧**历史会话**列表显示每条会话的最后一条用户消息，支持**批量删除**
- 发送框上方**状态行**实时显示正在执行的工具（`list_my_recent_tasks({...})`）
- 需要人工确认的工具（命令执行 / 文件写改删）弹出**审批卡片**，可每次询问 / 本次会话允许 / 永久允许
- 右上角**交付物 / 设置 / 知识库**入口：交付物页展示 Agent 生成的产出文件；知识库页管理 LightRAG 图谱（文档 / 分块 / 检索 / 问答反馈 / 数据看板）；设置页内含 State、提示词、追踪、记忆、Memory DB、定时任务、模型管理等调试能力

---

## 🧭 项目背景

**为什么做这个？** 自学 LLM 应用时发现"光调 API 太无聊"——想亲手实现一遍 Agent 的调度逻辑：模型怎么决定调哪个工具？工具结果怎么回到上下文？多轮循环怎么终止？记忆怎么跨会话留存？

**解决了什么问题？** 一个可本地运行的完整 Agent 骨架：对话 → 规划（DAG）→ 按拓扑层并行执行 → 验收契约校验 → 交付汇总，全程可观察、可审查、可切换模型。知识层用 LightRAG 建实体-关系图谱，随对话自然生长。

**标签**：🛠️ 学习实验项目 · 📚 概念验证。请不要把它当成生产框架来用。

---

## ✨ 核心特性

- ✅ **模型自决路由**：无硬编码意图分类，LLM 自行决定"直接回答 / 调工具 / 进入多步规划"
- ✅ **DAG 模式多步规划**：planner 把目标拆成 `nodes + edges`，executor 按拓扑层并行执行；支持软依赖、失败隔离、局部重规划（上限 3 次）与验收契约硬校验 —— 详见 [DAG 模式设计理念](#-dag-模式plan-and-execute设计理念)
- ✅ **自定义 ReAct 循环**：亲手实现思考—行动—观察闭环（`ReActLoop`），支持工具安全拦截、人工审批、连续拒绝熔断、迭代上限防死循环
- ✅ **分层记忆系统**：L1 对话消息（含工具调用事件） / L2 用户画像 / L3 任务历史（dag_plans） / L6 知识图谱 GraphRAG（实体关系图谱 + 混合检索，详见 [docs/l6-graphrag.md](docs/l6-graphrag.md)；原 L4 命令历史与 L5 语义缓存已并入 L1 事件与 L6 图谱）
- ✅ **实体归一化双保险**：入库前 `normalize_terms` 术语还原 + 入库后 `merge_entities` 图谱合并，抑制 LightRAG 大小写/拼写变体导致的实体节点膨胀（`RAG/Rag`、`GraphRAG/GraphRag` 等只留一个规范节点）
- ✅ **多 Key 自动轮换**：api_key 逗号分隔，限流/超时/鉴权自动换 key + 指数退避重试
- ✅ **知识库（KB）管理**：文档/分块/检索/问答反馈管理，每库可覆盖分块策略与抽取指引；对话图谱默认**全局共享一张图**（所有会话读写同一命名空间 `__global__`，跨会话可共享每轮对话知识）；用户经 `kb_create` 显式创建的知识库是**独立命名空间**（`lightrag_storage/<kb_id>/` 自含目录/图谱/向量索引），消息框可**多选勾选**参与本轮 L6 检索（默认只查 `__global__`）；`GRAPH_NAMESPACE=per_thread` 可退回按线程隔离
- ✅ **技能系统（Skills）**：SKILL.md 即技能，命中本地装、不够用 SkillHub 在线搜装
- ✅ **沉浸式 Web 面板**：流式对话、工具状态行、审批卡片、State/日志/记忆/定时任务调试抽屉、模型一键切换
- ✅ **标准 cron 定时任务**：`*/5 * * * *` 常规 cron 语法驱动 Agent 周期性执行任务

---

## 🚀 快速上手

### 环境准备

- **Python 3.12+**
- 一个 **OpenAI 兼容网关**的 `base_url` + `api_key`（模型凭据在 Web 面板设置页接入，不写死在代码里）
- 可选：Tavily API Key（联网搜索）

### 安装

```bash
git clone <repo-url> && cd Agnes-Agent
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r <(uv export --format requirements)   # 或按 pyproject.toml 安装依赖
```

### 最小运行

```bash
python -m app.main
```

启动预期输出（片段）：

```text
LLM 类型: <class 'app.llm.llm_factory.RotatingKeyChatOpenAI'> | provider=openai_compatible model=agnes-2.5-flash
🔍 控制面板: http://localhost:8081
[10:17:03] OK    控制台日志已启动（后台服务模式，对话请到调试面板）
```

> `python -m app.main` 现在是纯后台服务模式（无终端对话循环），与 `python -m app.service` 基本等价，但端口被占时会自动顺延到 8082+。遗留参数 `--new` 可开启新会话。

打开 http://localhost:8000 即可使用。**首次打开调试面板时会引导你设置账号密码**——凭据以 PBKDF2-SHA256（12 万轮 + 随机 salt）存入 `data/auth.json`（已 `.gitignore`，不含明文密码）；想重置就删掉该文件再刷新页面。若设置了 `.env` 的 `AGENT_USERNAME` / `AGENT_PASSWORD`（两个都填才生效），则优先使用它，适合无人值守部署。首次使用先到右上角 **设置 → 模型** 页接入你的模型（填 Base URL + API Key，可自动拉取模型列表）。

> `.env` 只放非模型密钥（如 `TAVILY_API_KEY`）；模型凭据统一在 `data/.model_config` 由设置页管理。

### 服务模式（HTTP 常驻 + 后台服务）

Agent 以常驻服务方式运行，**没有终端对话循环**——对话、审批、定时任务全部走 Web 调试面板：

```bash
python -m app.main                          # 后台服务（调试面板默认 8081，被占自动顺延）
python -m app.service                       # 同服务模式，默认 0.0.0.0:8081，开启热重载
python -m app.service --port 9000           # 改端口
python -m app.service --no-reload           # 关闭热重载
python -m app.service --new                 # 开新会话（新的 thread_id）
```

预期输出（片段）：

```text
🔍 控制面板: http://localhost:8081
[thread_id] a1b2c3d4-...
[10:17:03] OK    控制台日志已启动（后台服务模式，对话请到调试面板）
[10:17:03] ▶ 节点开始 chatbot
[10:17:03] LLM    planning  1.2s
[10:17:03] TOOL   [chatbot] tavily_search {'query': '...'}
[10:17:03] EXEC   node_1 → success
[10:17:03] OK    任务交付汇总完成 · 2 个产物
```

- **纯 HTTP 驱动**：没有终端输入循环，对话/审批/定时任务全部走 Web 面板（`/api/chat`、`/api/scheduler` 等）；
- **控制台日志**：节点/模型调用/工具/规划/任务等事件以彩色一行实时打印，方便在终端观察 Agent 工作过程；
- **端口固定**：`app.service` 被占用直接报错，不再自动顺延（`app.main` 仍自动顺延）；
- **热重载**：修改 `app/` 目录下的 `.py` 文件，或改 `data/.model_config`（模型配置）后自动重建 graph 并重启，无需手动重启进程。只监控 `app/` 与 `.model_config`，`data/*.db`、`traces/` 等运行期写入不会误触发重启；
- 服务模式与 `app.main` 共用 `.thread_id` 文件与 SQLite 存储，会话、记忆天然连续。

### 部署为开机自启服务（Linux systemd）

```bash
sudo useradd --system --home /opt/agnes-agent agnes   # 首次：创建运行用户
sudo chown -R agnes:agnes /opt/agnes-agent
sudo cp deploy/agnes-agent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now agnes-agent               # 开机自启 + 立即启动
journalctl -u agnes-agent -f                          # 看日志
```

> 服务默认**保留热重载**（改 `app/` 代码或 `data/.model_config` 自动重建）；生产环境想关闭可给启动命令追加 `--no-reload`。脚本/单元文件里的端口、路径按需修改。

---

## 🔄 业务流程（从一次对话到记忆落库）

**一条消息进来之后，Agent 里发生了什么？** 下面按"链路"切分，前两条是高频主路径（对话 + 规划），后两条是落库侧（记忆 + 图谱）。

### 链路 ① 新增对话入库全链路（主路径）

```
POST /api/chat {message, thread_id}
  └─ chat.py 构造 inputs = {"messages":[("user", message)]}
  └─ graph.stream(configurable.thread_id)  →  START → chatbot 节点
```

**chatbot 节点（`graph/builder.py`）内部分步骤：**

| 步骤 | 代码位置 | 做什么 |
|---|---|---|
| 1. 定位会话 | `set_current_context(thread_id)` | 让 L6 图谱工具（record_graph / lightgraph_query）知道当前会话 |
| 2. 落库用户消息 | `mm.add_message(thread_id, "user", ...)` | L1 messages 表（60s 幂等去重，防 interrupt/resume 重放重复插入） |
| 3. 拼 system prompt | `load_prompt_template()` + 技能清单 | 主模板填 `{{os}}/{{cwd}}/{{deliverables_dir}}/{{skills_section}}` |
| 4. 分层记忆注入 | `mm.build_memory_injection(...)` | **history_summary**（Stanford 三因子排序摘要）+ **L2** 用户画像/偏好 + **L3** 近期任务 + **facts** 高价值长期记忆，拼入"=== 分层记忆注入 ==="块 |
| 5. L6 图谱注入 | `get_l6_context(thread_id, user_content)` | 当前问题对 LightRAG 混合检索，命中实体/关系摘要也拼进 prompt |
| 6. 思考—行动 | `ReActLoop.run(...)` | 模型调工具 → `on_tool_before` 安全检查 / `interrupt_handler` 审批 → `on_tool_after` 副作用写入；循环直到无工具调用或达 `MAX_TOOL_CALL_ROUNDS` |
| 7. 落库助手回复 | `mm.add_message(thread_id, "assistant", content)` | 清洗 `<tool_call>` 残留后写入 messages 表 |
| 8. L6 全量喂养（后台） | `_feed_turn_async(thread_id, user, assistant)` | daemon 线程 → `lightrag_insert`，见[链路 ④](#-业务流程从一次对话到记忆落库) |
| 9. 长期记忆固化（后台） | `memory_engine.consolidate(...)` | 后台线程 LLM 抽取事实/偏好，见[链路 ③](#-业务流程从一次对话到记忆落库) |
| 10. 历史压缩（后台） | `compact_in_background(...)` | 消息 token 超预算阈值时，`messages[:-30]` 压成摘要写 `history_summaries` |
| 11. 回复防刷屏 | `apply_reply_guard(...)` | 连续异常回复熔断：首次照常写、第二次替换提示、其后跳过写入 |
| 12. git 快照 | `auto_snapshot(...)` | 本轮对代码/交付物的改动自动 commit |

**chatbot 退出后路由（`route_after_chatbot`）：**

```
pending_plan 有值 ──► planner（多步规划）
DAG 有未完成节点 ─► executor（继续执行）
否则 ───────────► END（本轮直接回答完成）
```

随后 chat.py 的 SSE 双通道把结果推给前端：`updates` 通道按节点推状态（含 summarizer 的 final 兜底文本），`messages` 通道推 token 打字机效果。

### 链路 ② 多步规划 DAG（request_planning → 执行 → 汇总）

```
用户："帮我做网页版贪吃蛇"
  └─ chatbot 里模型调 request_planning(目标) → 返回即写 pending_plan
  └─ route_after_chatbot → planner 节点
```

1. **planner**（`dag_planner.py`）：把目标喂给 LLM，产出 `{"nodes": [...], "edges": [...]}`；
2. **dag_core** 规范化：丢弃悬空边 / 去重边 → DFS 三色环检测（成环回喂模型重规划一次，仍成环退化为单节点计划）→ 落库三表 `dag_plans / dag_nodes / dag_edges`；
3. **executor**（`dag_executor.py`）：每次进入 = 一轮批处理 —— 回收 dangling `running` → 算失败传播/软依赖/当前 ready 批 → **同层节点线程池并行执行**，单节点内部是一次完整 `ReActLoop`（`max_iterations=12`，必须显式 `complete_node`/`fail_node` 收尾，不允许"默默结束"）；
4. **验收契约**：`complete_node` 调用时校验——`expected_artifacts`（planner 声明的产出路径）必须出现在 `files` 里且真实落盘，否则拒绝并回喂原因；
5. **路由**（`route_after_executor`）：还有未完成 → 继续 executor；有 `failed` 且 `replan_count < 3` → 局部重规划（只重做失败子图，`replaces` 记录替代关系）；全部终态 → summarizer；
6. **summarizer**（`dag_summarizer.py`）：汇总各节点结果 + **真实落盘**的产物；存在 failed 节点时**不调 LLM**，直接结构化说明，杜绝"把失败讲成成功"。最终答复走 SSE `step=final`。
7. 路由回 chatbot，用户继续对话。

### 链路 ③ 长期记忆固化（consolidate）

```
memory_engine.consolidate(thread_id, user, assistant, llm, extract=True)
  └─ extract_facts：把「用户：… 助手：…」喂 LLM，出 JSON ops：
       [{op: add/update/delete, category: fact|preference|identity|relation|project,
         content, importance, conflict_detection}]
  └─ apply_fact_ops：写 memory_facts 表（content 唯一，幂等）
  └─ index_texts("fact")：embedding → memory_facts.embedding（向量直存权威表）
```

- **遗忘衰减 daemon**（`start_decay_daemon`，默认每 6 小时）：importance 随时间 *0.995^days 衰减；最近 3 天访问过的记忆**升温**不衰减；跌破 `min_importance=2.0` 删除——"越用越重要，不用就忘"。
- **检索侧**：`search_my_memory` 工具 / `build_memory_injection` 的 facts 层，都只查 `memory_facts` + `dag_plans`（人的记忆），**不含对话原文**——原文的实体关系由 L6 图谱独占，两套检索数据源不重叠。

### 链路 ④ L6 知识图谱入库（lightrag_insert）

```
_feed_turn_async(thread_id, user, assistant)   // 每轮对话全量喂养（daemon 线程）
  └─ lightrag_insert(thread_id, [text])
       ├─ 1. normalize_terms(text)           // 术语还原：Rag→RAG、GraphRag→GraphRAG…
       ├─ 2. rag.ainsert(texts)              // 在专属 worker 事件循环上调用（_run_on_worker）
       │      └─ chunking（策略可配）→ LLM 抽取实体/关系
       │           （entity_types_guidance 注入 prompt，见 模块架构/LLM 层）
       │      └─ 向量化（EmbeddingFunc）→ 写 NanoVectorDB / Faiss
       │      └─ 图谱合并（同名字节可接，仅完全同名去重）
       └─ 3. merge_entity_variants(thread_id)  // 后置合并：把漏网的
              GraphRag/Graphrag… 节点 amerge_entities 并成 GraphRAG
              （边自动重定向、描述保留）
```

- **为什么双保险**：LightRAG 按 chunk 独立抽取，每个 chunk 的 LLM 都看不到其他 chunk 命名 → `RAG`/`Rag`/`GraphRAG/GraphRag` 会各自成节点。前置术语还原从源头压掉变体，后置图谱合并把依然生成的变体节点在图上合并。
- **读取侧**：`lightgraph_query`（混合检索，向量 + 图谱双路召回）/ `get_l6_context`（每轮被动注入）/ `/api/graph/nodes|edges`（全网快照，前端渲染实体关系视图）。
- **KB 管理**：`kb_ingest`（文档/文本/URL 导入）、`kb_reprocess`（重建）、`kb_edit_chunk`（改分块）、`kb_feedback`（问答反馈）。图谱默认全局唯一，`kb_meta.json` 落在全局命名空间下，可覆盖分块策略 / top_k / 抽取指引。
- **存储**：`lightrag_storage/<ns>/`（JsonKV + NetworkX GraphML + NanoVectorDB/faiss 向量索引），`ns = _graph_ns(thread_id)` —— 默认全部收敛到全局命名空间 `__global__`（所有会话/库共享一张图，重启不丢）；`GRAPH_NAMESPACE=per_thread` 时 `ns=thread_id` 按线程独立。

---

## 📦 模块架构（按 Agent 能力分类）

### 1️⃣ 模型层（LLM）· `app/llm/`

| 模块 | 实现方式 |
|---|---|
| `llm_factory.py` | **多 Key 轮换 + 指数退避重试**。`RotatingKeyChatOpenAI`：401/429/5xx/网络/超时 → 换下一个 key；一轮全部失败走 `2^n + jitter`（上限 30s）重试；**400 立即抛不重试**；流式只重"连接建立阶段"（token 已推不重试，避免前端重复）。单 key 也走轮换骨架享受退避保护。`create_llm` 是纯工厂：只认 OpenAI 兼容格式，不读 `.model_config`、不内置厂商凭据 |

### 2️⃣ 上下文层（Context）· `app/graph/`

| 模块 | 实现方式 |
|---|---|
| `state.py` | 极简 LangGraph State：`messages`（add_messages）+ `thread_id` + 触发器 `pending_plan` + 验证反馈字段。**DB 才是唯一真相源**，state 只做节点间管道 |
| `builder.py` | 节点编排 + 条件路由。`chatbot` 统一入口（模型自决：直答 / 调工具 / 规划）。路由决策全部读 DB（任务进度），state 只传标识。见[链路 ①](#-业务流程从一次对话到记忆落库) |
| `utils.py` | token 预算（`ensure_token_limit` 裁剪）、消息上下文组装（`prepare_context_messages`：退化尾巴清理 + 结构清洗 + 按完整回合裁剪，不拆散 assistant↔ToolMessage）、回复防刷屏守卫（`apply_reply_guard`）、prompt 模板加载 |
| `_agent_prompt.py` | 跨节点共享 prompt 片段：`EXECUTION_DISCIPLINE`（完成即停/同参不重复/搜索克制）、`SAFETY_RULES`（deliverables/ 落盘约束、禁写核心路径）、`OUTPUT_FORMAT`、`MEMORY_GUIDE`。planner/executor 拼接继承，避免各自维护漂移 |
| `prompt_template.txt` | 主 chatbot 系统提示模板：角色、工具策略、安全、输出格式 + 四个占位符 `{{os}}/{{os_cmds}}/{{cwd}}/{{deliverables_dir}}/{{skills_section}}` |
| `memory/compaction.py` | 历史压缩（context engineering）：只看"实际发送预算"占比触发（不再用时间兜底），只压 `messages[:-30]`，后台线程执行，写 `history_summaries` 不改 state |

### 3️⃣ 工具层（Tools）· `app/tools/`

| 工具 | 分类 | 实现/约束 |
|---|---|---|
| `ls` / `read_file` / `write_file` / `edit_file` / `delete_file` / `glob_files` / `grep_files` | 文件操作 | 纯 Python 包装（`file_ops.py`），**无需审批**；路径限项目目录内，写入限 `deliverables/`，越界即拒绝并回喂 ToolMessage |
| `execute_command` | 命令执行 | 子进程执行 + 输出解码（Windows GBK/UTF-8 兼容）；**需人工审批** |
| `tavily_search` | 联网搜索 | 搜索结果自动 `_try_ingest_lightrag` 被动入图（调用后钩子） |
| `update_user_info` / `update_user_preference` | 用户画像写 | 调用后钩子写 L2：`user_profile` / `user_preferences` |
| `search_my_memory` / `list_my_recent_tasks` / `get_command_history` | 记忆读取 | 语义检索 facts+tasks / 任务历史（dag_plans）/ L1 工具调用事件 |
| `request_planning` | 规划触发（元工具） | 返回 `Command(update={"pending_plan": 目标})` → state 注入 → 路由跳 planner |
| `record_graph` / `lightgraph_query` | L6 图谱 | 显式三元组建图 / 图谱混合检索（见 `memory/graph_rag_tool.py`） |
| `list_skills` / `read_skill` / `search_skillhub` / `install_skill` | 技能路由 | 技能清单注入 prompt / 读 SKILL.md / SkillHub 在线搜索 / 一键安装 |

> 工具定义全部集中注册于 `tools/__init__.py` 的 `tools` 列表；`bind_tools` 一次注入给所有节点。安全拦截在 `on_tool_before`（危险命令关键字）、审批在 `interrupt_handler`（per_ask 模式中断）、副作用在 `on_tool_after`。

### 4️⃣ 规划层（Planning）· `app/planning/`

| 模块 | 职责 | 实现方式 |
|---|---|---|
| `react_loop.py` | 思考—行动—观察循环 | 自实现：`on_tool_before` 安全拦截 / 审批 interrupt / 连续 3 次同调用熔断、连续拒绝熔断（`_reject_count`）、连续无进展计数强制 break、`max_iterations` 防死循环；纯文本永远不算完成（require_final_marker，executor 用） |
| `dag_core.py` | DAG 计算内核（纯函数） | 规范化（丢悬空边/去重边）、DFS 三色环检测、Kahn 拓扑分层、ready 批计算、失败传播（强依赖失败→迭代传播标 skipped）、验收契约解析（`expected_artifacts` 兼容 Windows 路径） |
| `dag_planner.py` | 目标 → 结构化 DAG | LLM 出 `nodes`/`edges` → 规范化 → 环检测（成环请求重规划一次，再成环退化为单节点计划）→ 落库 |
| `dag_storage.py` | DAG 持久化（SQLite） | 三表 `dag_plans/dag_nodes/dag_edges` + 轻量 checkpoint（重启从最近状态继续）；回收悬空 `running`；文本字段入库前统一 `_as_text` 归一 |
| `dag_executor.py` | 按拓扑层并行执行 | `ThreadPoolExecutor` + `BoundedSemaphore` 同层并行；单节点复用 `ReActLoop`；`complete_node/fail_node` 终止工具 + 验收契约硬校验（`_has_real_artifact`）；失败前缀补上下文（`_missing_soft`）；窄重规划入口 |
| `dag_summarizer.py` | 交付汇总（复命） | 汇总节点结果 + **真实落盘**产物（跳过 deleted）；存在 failed 节点不调 LLM 直接结构化说明；结果写 `AIMessage` → SSE `step=final` → 前端无条件覆盖气泡 |

### 5️⃣ 记忆层（Memory）· `app/memory/`

| 模块 | 职责 | 实现方式 |
|---|---|---|
| `memory_manager.py` | 分层存储 + 注入 | SQLite 权威表：`messages`（L1）/ `user_profile`+`user_preferences`（L2）/ `dag_plans`（L3）/ `memory_facts`（长期记忆，**向量直存本表**，废弃冗余 memory_chunks 表）；`build_memory_injection` 按层聚合注入 prompt：history_summary（Stanford 三因子 Recency+Importance+Relevance 排序）/ L2 / L3 / facts（≥6 高价值常驻） |
| `memory_engine.py` | 记忆固化 + 语义检索 + 遗忘 | `consolidate`（后台线程 LLM 抽取 facts → 冲突检测 → 写库 → 建向量）；`search_semantic`（余弦 + 可选 rerank 精排，兜底 SQL LIKE，只查 fact/task）；`decay_memory_facts` + `start_decay_daemon`（遗忘衰减，越用越重要） |
| `compaction.py` | 历史压缩 | 见[上下文层](#2️⃣-上下文层context--appgraph) |
| `graph_rag_tool.py` | L6 图谱工具 + 注入 | `record_graph`（三元组→文本入图）/ `lightgraph_query`（混合检索）/ `_feed_turn_async`（每轮全量喂养，daemon）/ `get_l6_context`（被动注入 prompt，失败静默降级） |
| `ligraphrag_adapter.py` | LightRAG 桥接 | **专属 worker 事件循环**跑 `ainisert/aquery`（`_run_on_worker` 跨线程桥）；Embedding(vstack) / Rerank / 主模型 LLM 三件套包装；图谱命名空间解析 `_graph_ns`（默认全局共享一张图，`GRAPH_NAMESPACE=per_thread` 按线程隔离）；KB 配置覆盖（分块策略/top_k/抽取指引）；`merge_entity_variants` 后置合并 |
| `entity_normalizer.py` | 术语归一化 | 别名表 `_TERM_ALIASES`（RAG/KAG/OAG/GraphRAG/LLM/OpenSPG 的大小写、缩写、全称变体）→ **ASCII 词边界正则**（lookahead/lookbehind 而非 `\b`，因为 `\b` 对 CJK 无效，会漏掉"了解GraphRag"这类中英混排前缀）→ `normalize_terms` 词级还原 |

### 6️⃣ 技能层（Skills）· `app/skills/`

| 模块 | 实现方式 |
|---|---|
| `loader.py` | 扫描 `app/skills/<name>/SKILL.md`（YAML frontmatter 元数据 + 步骤正文）→ 每轮注入 system prompt → 模型调 `read_skill` 读全文后严格按其步骤执行 |
| `hub.py` | SkillHub 在线市场客户端：`search_skillhub` 搜索 → 用户确认 → `install_skill` 下载即装（立即可用） |
| `agnes-media/` | 内置示例技能（媒体处理） |

### 7️⃣ 服务层（Server/API）· `app/server/`

| 模块 | 实现方式 |
|---|---|
| `api/chat.py` | 对话 API：SSE **双通道**——`updates`（节点状态，含 final 兜底）+ `messages`（token 打字机）；内部 LLM 输出过滤（`_filter_internal_tokens`，按摘要结构 marker 前缀丢弃 update_summary 泄漏的 token）；事件监听桥把 ReActLoop 的工具完成事件实时转 SSE tool 事件；resume 审批决策 |
| `store.py` | 全局 `_GRAPH/_CONFIG`、日志落库（`app_logs`）+ `add_event` 事件流、State 快照、会话删除 |
| `api/system.py` | `/api/state` `/api/prompt` `/api/logs` `/api/events` `/api/trace` `/api/sse` 调试接口 |
| `api/kb.py` | 知识库 CRUD：文档/分块/检索/反馈/看板（见[链路 ④](#-业务流程从一次对话到记忆落库)） |
| `api/memory.py` / `api/tools.py` / `api/graph.py` / `api/skills.py` / `api/git.py` / `api/upload.py` | 记忆 / 工具白名单 / 图谱快照 / 技能浏览 / git 操作与交付物 / 文件上传 |
| `config.py` | 模型目录管理（自定义来源，无内置厂商；写入 `data/.model_config`） |
| `console_log.py` | 控制台彩色一行日志（节点/模型/工具/规划/任务事件） |
| `git_ops.py` | 自动 git 快照（每轮对话完把本轮改动 commit） |
| `auth.py` | PBKDF2-SHA256（12 万轮 + 随机 salt）账号认证 |

### 8️⃣ 入口与配置

| 模块 | 实现方式 |
|---|---|
| `main.py` | 入口：后台服务模式（自动顺延端口）；build_graph/set_graph 失败时打印 `[startup error]` + traceback + 落库日志兜底，不崩进程 |
| `service.py` | 服务模式：HTTP 常驻 + `--reload` 热重载（只监控 `app/` 与 `.model_config`） |
| `config.py` | 路径中心（`DB_PATH` / `CHECKPOINT_DB_PATH` / `PROMPT_TEMPLATE_PATH` 统一指向 `data/`） |
| `trace.py` | 会话级 trace：节点/工具调用写入 `traces/<thread_id>.jsonl`（调试面板追踪页读取） |

---

## 🧠 设计思路与实现过程

### 架构总览（服务模式）

```mermaid
flowchart TB
    U[用户输入] -->|POST /api/chat| C["chatbot 节点<br/>模型自决 + ReActLoop<br/>分层记忆注入 + L6 图谱注入"]
    C -->|"request_planning → pending_plan"| P["planner 节点<br/>目标 → 结构化 DAG"]
    C -->|"DB 里还有未完成计划"| X["executor 节点<br/>按拓扑层并行执行 ready 批"]
    C -->|"直接回答 / 无待办计划"| E1[END]
    P -->|"nodes + edges 落库<br/>dag_plans / dag_nodes / dag_edges"| X
    X -->|"还有 pending/ready/running 节点"| X
    X -->|"有 failed 且 replan_count < 3<br/>（局部重规划）"| X
    X -->|"全部终态 / 连续 3 次空批兜底"| S["summarizer 节点<br/>汇总交付、向用户复命"]
    S --> E2[END]
    subgraph 落库侧
    F1["_feed_turn_async → LightRAG<br/>normalize → ainsert → merge 变体"]
    F2["consolidate → memory_facts<br/>抽取事实 + 向量化"]
    F3["compact_in_background<br/>历史压缩 → history_summaries"]
    end
    C -.后台 daemon.-> F1 & F2 & F3
```

> 所有路由决策**读 DB 而非 state**：`dag_plans` / `dag_nodes` 是任务进度的唯一真相源，LangGraph 的 state 只传标识与消息。这样"进程重启后接着跑"和"多轮会话续聊"无需额外机制。

### 技术选型理由

- **为什么用 LangGraph？** 需要 checkpoint 中断/恢复来支撑"审批挂起"和"多轮会话续聊"，手写状态机成本太高。
- **为什么工具执行不用 LangGraph 的 ToolNode，而是自写 ReActLoop？** 想亲手实现工具调度的完整细节：安全拦截、审批、拒绝熔断、`request_planning` 之类的"元工具"返回值透传——这些在 ToolNode 里会被框架隐藏。
- **为什么 SSE 用双通道（updates + messages）？** 流式 token（messages）负责打字机效果，节点状态（updates）负责最终文本兜底与审批事件。
- **为什么用 LightRAG 而不自己写图谱？** 实体/关系抽取是 LLM 的活，LightRAG 管 chunking → LLM 抽取 → 向量化 → 图持久化闭环，我们只做桥接 + 归一化优化（实体双保险）。

### 🧬 DAG 模式（Plan-and-Execute）设计理念

这一节是规划能力的核心：**为什么多步任务不用"线性子任务列表"，而用"DAG + 状态机 + 局部重规划"**。

#### 1. 动机：线性计划在真实任务上会碎

最早让 planner 输出线性列表（step1 → step2 → …），立刻撞上三类问题：

| 线性列表的问题 | 真实任务的形态 | DAG 的解法 |
|---|---|---|
| 无法表达"谁依赖谁" | "写完 HTML 才能校验它" | `edges` 显式声明依赖方向 |
| 只能串行，慢 | "查资料"与"搭骨架"互不依赖，可同时做 | Kahn 分层 → 同层并行执行 |
| 一个子任务挂了整条链报废 | 删掉某个锦上添花的节点不该影响主流程 | 软依赖（`soft` 边）不阻塞后继，只标注"缺数据" |

#### 2. 四层模型

把 DAG 能力拆成四层，每层职责单一、可单独测试：

| 层 | 做什么 | 落在哪里 |
|---|---|---|
| ① 结构化计划 | 模型输出 nodes + edges → 规范化成可调度 DAG | `dag_planner.py` + `dag_core.normalize_nodes` |
| ② 校验与排序 | 环检测（DFS 三色）、拓扑分层（Kahn） | `dag_core.detect_cycle` / `topo_layers` |
| ③ 状态机与失败传播 | six-state + 软依赖 + 失败隔离 | `dag_storage.py` + `dag_core.compute_*` |
| ④ 局部重规划 | 只重做受影响的子图，不推翻已完成的工作 | `route_after_executor` 触发 + executor 入口 replan |

**关键取舍：为什么环检测交给"重规划"而不是自动断边？**
断边等于替用户猜意图（砍哪条依赖都是错的）。成环说明这次规划没想清楚，正确做法是把环信息回喂模型**重规划一次**；仍成环则退化为单节点直行——保证任务永远能往下走，而不是卡死（`dag_planner.py:88-99`）。

#### 3. 状态机与失败传播

```text
pending ──(强依赖父全部 success)──► ready ──► running ──► success
   │                                              └──► failed ──► 触发局部重规划
   └──(任一强依赖父 failed)──► skipped（连带失效，不再执行）
```

- **ready 批判定**（`compute_ready_batch`）：只看"强依赖父是否全部 success"；软依赖父失败不阻塞，但把"缺数据"写进节点上下文。
- **失败隔离**（`compute_failure_skips`）：强依赖失败 → 迭代传播到稳定；无关分支完全不受影响，软依赖后继不跳。
- **running 悬空回收**（`recover_stale_running`）：进程被杀/重启后 DB 残留 `running`，executor 每轮入口按超时判为 `failed`。

#### 4. 局部重规划（④）：只重做坏掉的那一段

1. `route_after_executor` 发现"仍有 failed 节点，且 `replan_count < MAX_REPLAN(3)`" → 再次进入 executor；
2. executor 入口做**窄重规划**：仅把 failed 节点及其强依赖后继摘出来交给 LLM 重出这一段；
3. 新节点替换回原 DAG（`replaces` 字段记录"谁替代了谁"），其余 `success` 节点原地不动；
4. 上限 3 次，用完交给 summarizer **如实汇报失败**。

> 实测教训（`builder.py:435-446`）：`failed` 分支必须写在"unfinished 为空"判断**之后**。缺了它时"一批节点全失败 → unfinished 为空 → 直接收敛"，重规划上限形同虚设。

#### 5. 不许"假完成"：验收契约（acceptance_criteria）

- planner 被要求在 `acceptance_criteria` 里写明**产出文件路径**；
- executor 执行前解析出 `expected_artifacts` 写进节点 system prompt；
- `complete_node(files=[...])` 是终止工具，但调用时会被拦截校验：
  - 没有"真实存在于磁盘"的产物 → 拒绝，并把拒绝原因回喂模型；
  - 声明产物没出现在 `files` → 契约未满足，同样拒绝。
- 结果是"假完成"尽量在**节点内**被发现并当场改正。

#### 6. 执行器：同层并行 + 单节点 ReAct

`dag_executor.py` 每进入一次 executor 节点 = 一轮批处理：

1. 读 plan/nodes/edges，回收 running 悬空；
2. `dag_core` 算失败传播、软依赖、当前 ready 批；
3. **同层并行**执行（`ThreadPoolExecutor` + `BoundedSemaphore` 限并发），节点内部是一次完整 `ReActLoop`（`max_iterations=12`，`terminate_tools={"complete_node", "fail_node"}`）；
4. 结果写回 DB 并 `save_checkpoint`（重启可从最近状态继续）；
5. 推送 DAG 结构 / 节点状态 / replan 事件给前端。

单节点内工具硬约束（`_on_tool_before`）：探索类 ≤2 次、写入类 ≤8 次、产物路径必须落在 `deliverables/`。

#### 7. 交付汇总：必须有人"复命"，且不许美化失败

- 汇总各节点结果，**跳过 `skipped`** 的作废节点；
- 产物只保留**磁盘上真实存在**的文件；
- **存在 failed 节点时不调 LLM**，直接结构化说明（"任务未完全完成：N 个失败 + 失败项 + 已产出"）；
- 汇总写 `AIMessage` → SSE `step=final,node=summarizer` → 前端**无条件覆盖**当前气泡。

> 易踩语义坑：交付汇总**不写 `history_summaries` 表**——那张表的语义是"历史对话压缩摘要"（compaction.py 写入），任务汇报混进去会让记忆注入层读到错误语义。

#### 8. 失败与并发之外：还有哪些"防呆"

| 场景 | 处理 |
|---|---|
| 节点永不 ready | 连续 3 次空批（`_empty_streak >= 3`）→ 进 summarizer |
| 模型输出非法 JSON | 降级为单节点计划，任务照跑 |
| 边引用不存在节点 / 重复边 | `normalize_nodes` 丢弃并记 warning |
| 写库字段类型不对 | 入库前统一 `_as_text` 归一 |
| 前端渲染大量事件卡死 | 过程事件按"轮"聚合成一行摘要；待办面板拓扑分层收敛 |

#### 设计检查清单（改 DAG 相关代码时照着过一遍）

- [ ] 新逻辑是否保持"DB 是唯一真相源"（state 只放标识与消息）？
- [ ] 新的失败路径会被 `compute_failure_skips` 正确传播吗？软依赖有没有被误伤？
- [ ] 新的重规划入口受 `MAX_REPLAN` 约束吗？有没有可能无限循环？
- [ ] 新的写库字段做过归一吗（SQLite TEXT 只接受 str/bytes/None）？
- [ ] 前端新事件在两种显示模式下都不会退化吗（正文必须可见、最新输出在底部）？

---

## 🔥 踩坑与解决实录（真实经历）

1. **坑：模型工具调用增量以 dict 形态投递，`getattr` 读出来恒为空。**
   → 发送框上方"工具调用状态行"一度永远空白。定位后发现网关把 `tool_call_chunks` 以 `dict` 投递（首个 chunk 带 name，后续是 JSON 片段），`getattr(tc, "name")` 对 dict 恒返回 `None`。解决：兼容 dict/对象两种形态解析，按 `index` 累积参数片段。

2. **坑：审批模式"每次询问"形同虚设——切了 per_ask 却不弹审批卡。**
   → 后端 `_CONFIG` 里 `approval_mode` 会被"新建会话"整体重置回 `session_allow`，而前端按钮仍高亮 per_ask。解决：`/new`、`/resume` 只切换 `thread_id`、保留审批模式；前端切换会话后重新向后端读取实际模式同步按钮。

3. **坑：messages 流对同一次 LLM 调用先推流式 chunk、再推完整消息，回复被显示两遍。**
   → 只处理 `AIMessageChunk`，完整文本由 `updates` 模式的 final 事件兜底。

4. **坑：调试面板 State 里的 messages 永远是空的。**
   → `update_state` 只 import 从未调用，快照表恒空。解决：流结束后用 `graph.get_state()` 取完整 state 落快照。

5. **坑：内部 LLM 调用（update_summary 的摘要生成）token 污染聊天框。**
   → 摘要以"**用户目标"等 marker 开头，token 逐字到达。解决：`_filter_internal_tokens` 按 run_id 分段，累积文本是任一 marker 前缀即判定内部调用整段丢弃；正常回复文本由 updates final 兜底，不受影响。

6. **坑：LightRAG 图谱实体膨胀——`RAG/Rag`、`GraphRAG/GraphRag` 各自成节点。**
   → 根因是 per-chunk 独立抽取，各 chunk 的 LLM 互不可见。解决（双保险）：入库前 `normalize_terms` 术语还原 + 入库后 `merge_entity_variants` 图谱合并；抽取 prompt 再注入 `entity_types_guidance` 约束合并规则。

7. **坑：归一化正则用 `\b`，中文紧邻英文时替换失效（"了解GraphRag"漏替换）。**
   → Python `\b` 把 CJK 当 `\w`，中英之间无边界。解决：改用 ASCII lookahead/lookbehind `(?<![A-Za-z0-9])…(?![A-Za-z0-9])`，同时保住 `config` 这类子串不被误伤。

8. **坑：多 Key 轮换把 `attempt_count` 在 invoke 前清零，无限退避重试卡死 13+ 分钟。**
   → while 条件恒为真。解决：仅在**调用成功之后**复位计数（`llm_factory.py`）。流式同理，只重连接建立阶段，token 中途不重试。

---

## 📂 项目文件结构

```
Agnes-Agent/
├── app/
│   ├── main.py                    # 入口 后台服务（端口自动顺延 + 启动兜底）
│   ├── service.py                 # 入口 服务模式：HTTP 常驻 + 热重载
│   ├── config.py                  # 路径配置中心（统一指向 data/）
│   ├── llm/                       # LLM 层：多 key 轮换 + 退避重试（RotatingKeyChatOpenAI）
│   ├── graph/                     # 上下文层：builder(节点/路由) + state + utils + _agent_prompt + 主模板
│   ├── memory/                    # 记忆层：memory_manager(分层存储/注入) + memory_engine(固化/遗忘)
│   │                              #   + compaction(历史压缩) + ligraphrag_adapter(LightRAG 桥)
│   │                              #   + graph_rag_tool(L6 图谱工具/喂养) + entity_normalizer(术语归一化)
│   ├── planning/                  # 规划层：dag_planner/dag_executor/dag_summarizer
│   │                              #   + dag_core(DAG 计算) + dag_storage(三表+checkpoint) + react_loop
│   ├── tools/                     # 工具层：文件/命令/记忆/搜索/图谱/技能/规划触发（集中注册）
│   ├── skills/                    # 技能层：loader(扫描 SKILL.md) + hub(SkillHub) + 用户技能
│   └── server/                    # 服务层：FastAPI + SSE 流式 + 调试面板前端
│       ├── api/                   # chat(双通道流) / kb(知识库) / memory / tools / graph / system / skills / git / upload
│       ├── store.py               # 日志 / 事件流 / State 快照 / 会话删除
│       ├── config.py              # 模型目录管理（自定义来源，无内置厂商）
│       ├── console_log.py         # 控制台彩色日志
│       ├── git_ops.py             # 自动 git 快照
│       ├── auth.py                # PBKDF2 认证
│       └── static/                # 前端（index.html + app.js + style.css）
├── data/                          # 运行时数据（memory.db / checkpoints.db / app_logs.db / traces/ / auth.json）
├── lightrag_storage/              # L6 知识图谱持久化（默认全局共享一张图：__global__ 子目录含 KV + 图谱 + 向量索引；GRAPH_NAMESPACE=per_thread 时每会话一个子目录）
├── neo4j/                         # 本地 Neo4j 5.26 图数据库（GRAPH_STORAGE=neo4j 时使用；gitignored）
├── .runtime/                      # 本地 JDK21（Neo4j 运行时依赖；gitignored）
├── deliverables/                  # Agent 生成的交付物
├── docs/                          # 设计文档（l6-graphrag.md 等）
├── tests/                         # pytest 回归测试（对话/规划/记忆/图谱/归一化）
├── deploy/                        # systemd 单元文件等部署脚本
├── pyproject.toml                 # 项目元数据 + 依赖
└── README.md
```

---

## 🗺 Roadmap 与致谢

**Roadmap**

- [ ] 接入更多工具（浏览器操作、数据库查询、图片生成）
- [ ] 多模态输入（图片/语音进对话）
- [ ] 记忆检索从"numpy 余弦"升级到专用向量库（已具备 embeddings，换后端即可）
- [ ] 流式中间态可视化（思考过程实时图谱动画）

**致谢**

- ReAct 论文（*Synergizing Reasoning and Acting in Language Models*）
- LangChain / LangGraph 社区
- LightRAG 团队
- 所有在调试面板上被反复试错的模型网关

**许可证**：MIT

**交流**：欢迎在仓库 Issue 区留言，或邮件联系（你的邮箱 / GitHub）。