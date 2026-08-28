# AI Agent 从零到实战：分阶段学习路径

> **子任务产出**：基于零基础学习者背景，设计分阶段学习路径（入门基础 → 核心概念 → 框架实践 → 项目实战），明确各阶段目标、学习资源与练习任务，并附实战案例、资源汇总与进度追踪表。
> **配套文档**：本路径的"学什么"依据《agent-skill-inventory.md》技能清单（L1–L5 五层技能模型）；本文档负责"怎么学、按什么顺序学、每阶段学到什么程度、用什么检验、如何追踪进度"。
> **定位读者**：零编程基础的转行者 / 在校生 / 需要带队落地 Agent 项目的工程师。

---

## 目录

- [〇、文档使用说明](#〇文档使用说明)
- [一、阶段一：入门基础（L1 编程与 API 工程）](#一阶段一入门基础l1-编程与-api-工程)
- [二、阶段二：核心概念（L2 LLM 原理 + L3 Agent 原理）](#二阶段二核心概念l2-llm-原理--l3-agent-原理)
- [三、阶段三：框架实践（L4 LangChain → LangGraph）](#三阶段三框架实践l4-langchain--langgraph)
- [四、阶段四：项目实战（L5 工具链）](#四阶段四项目实战l5-工具链评测--部署--安全--产品化)
- [五、实战案例：一条主线贯穿四阶段](#五实战案例一条主线贯穿四阶段)
- [六、学习节奏与时间投入参考](#六学习节奏与时间投入参考)
- [七、进度追踪表（可勾选）](#七进度追踪表可勾选)
- [八、资源推荐汇总](#八资源推荐汇总)
- [九、四阶段成果一览（作品集目录）](#九四阶段成果一览作品集目录)
- [十、常见问题与避坑指南](#十常见问题与避坑指南)
- [十一、进阶方向（阶段四之后，可选）](#十一进阶方向阶段四之后可选)

---

## 〇、文档使用说明

- **总周期参考**：每天投入 2~3 小时，约 4~6 个月可到达"能独立交付生产级 Agent"；全职学习可压缩到 8~12 周。
- **学习方法论**：每个阶段遵循 **「理解 20% → 动手 60% → 复盘 20%」** 的比例，动手永远优先。
- **通关规则**：每阶段末尾有"通关自测清单"，全部勾选后方可进入下一阶段；不建议跳级。
- **里程碑项目**：4 个阶段各有一个可放进作品集的交付物，全部完成后你将拥有一条完整的 Agent 项目简历。
- **进度追踪**：使用 [第七章的进度追踪表](#七进度追踪表可勾选) 每周更新一次，周日复盘时勾选完成项。

### 学习路径总览（一图看懂）

```
┌─────────────────────────────────────────────────────────────────┐
│ 阶段一 入门基础 (3~4 周)       Python / API / 命令行 / 异步      │
│   里程碑：命令行多轮聊天机器人（调通真实 LLM API）               │
├─────────────────────────────────────────────────────────────────┤
│ 阶段二 核心概念 (4~6 周)       LLM 原理 / Prompt / RAG / Agent  │
│   里程碑①：结构化信息抽取器  里程碑②：本地文档知识库问答机器人   │
├─────────────────────────────────────────────────────────────────┤
│ 阶段三 框架实践 (4~6 周)       LangChain → LangGraph             │
│   里程碑：带记忆 + 工具调用 + 人工审批的业务 Agent               │
├─────────────────────────────────────────────────────────────────┤
│ 阶段四 项目实战 (4~6 周)       评测 / 部署 / 安全 / 产品化       │
│   里程碑：把阶段三的 Agent 打磨成可上线服务（含评测报告）        │
└─────────────────────────────────────────────────────────────────┘
```

> 对应技能模型：阶段一 ⊃ L1 编程基础；阶段二 ⊃ L2 LLM 原理 + L3 Agent 原理；阶段三 ⊃ L4 框架；阶段四 ⊃ L5 实战工具链。

---

## 一、阶段一：入门基础（L1 编程与 API 工程）

### 1.1 阶段目标

学完本阶段，你应该能够：

- 用 Python 独立编写 50~100 行的脚本（变量、流程控制、函数、类、文件读写）。
- 理解并使用 `async/await` 异步编程，能并发调用多个 API。
- 掌握 HTTP 与 OpenAI 兼容接口的调用范式（`/chat/completions`），会处理鉴权、JSON、错误。
- 熟练使用终端、虚拟环境、Git，具备基本的工程化习惯（异常处理、日志、单元测试入门）。

**一句话目标：让代码"能跑、能调通大模型 API、能并发"。**

### 1.2 学习内容与资源

| 主题 | 核心知识点 | 学习资源 |
|---|---|---|
| Python 语法 | 变量/类型/流程控制/函数/类/模块/异常 | Python 官方 Tutorial；《Python 编程：从入门到实践》；廖雪峰 Python 教程 |
| 数据结构 | 列表/字典/集合/推导式、简单算法思维 | LeetCode 简单题 20 道（不求多） |
| 异步并发 | asyncio、事件循环、`async/await` | Python 官方 asyncio 文档；《Using Asyncio in Python》 |
| HTTP/API | HTTP 方法/状态码、鉴权、JSON、OpenAI 兼容接口 | OpenAI 官方 Docs（chat completions）；通义 DashScope / DeepSeek 文档；`requests` / `httpx` 文档 |
| 开发环境 | 终端命令、venv/uv、Git/GitHub、VS Code | GitHub Docs；《Pro Git》前 3 章 |
| 工程习惯 | logging、pytest、dotenv 配置管理 | pytest 官方文档；Real Python 系列教程 |

### 1.3 练习任务（按周推进）

**第 1 周：环境与 Python 地基**
- 任务 1（必做）：安装 Python 3.10+、VS Code、Git；用 `uv` 创建虚拟环境并安装 `httpx`。
- 任务 2（必做）：写一个"天气数据模拟器"脚本——用字典/列表存储城市数据，提供查询函数，处理 KeyError。
- 任务 3（可选）：LeetCode 每天 1 道简单题（数组、哈希表）。

**第 2 周：调通真实 LLM API（关键突破）**
- 任务 4（必做）：申请一个 API Key（通义/DeepSeek/OpenAI 任选），用 `httpx` 写 30 行脚本调用 chat completions，打印回复。
- 任务 5（必做）：把上面的脚本改造成**多轮对话**：维护消息列表，支持连续问答。
- 任务 6（必做）：加入异常处理（网络超时、401、429）与 `logging` 日志。

**第 3 周：异步与流式**
- 任务 7（必做）：用 `asyncio` 并发调用 3 个不同模型的 API，统计总耗时并对比串行/并行的差异。
- 任务 8（必做）：实现**流式输出**（`stream=True`），让回复逐字打印。
- 任务 9（必做）：用 pytest 给"多轮对话消息构建"函数写 3 个单元测试。

**第 4 周：Git 与里程碑交付**
- 任务 10（必做）：把全部代码推上 GitHub，写 README（含使用说明与截图）。
- 里程碑：**命令行多轮聊天机器人**——支持上下文、流式输出、优雅退出、错误提示，代码有日志有测试。

### 1.4 通关自测清单

- [ ] 能不看资料写出 50 行 Python 脚本并解释每行作用
- [ ] 能独立完成 API Key 配置与一次成功的 LLM 调用
- [ ] 能说清 `async/await` 与线程的区别，并写过并发调用代码
- [ ] 能使用 Git 完成 commit/push，并创建了公开仓库
- [ ] 里程碑项目已上线 GitHub，README 完整

---

## 二、阶段二：核心概念（L2 LLM 原理 + L3 Agent 原理）

### 2.1 阶段目标

学完本阶段，你应该能够：

- 理解 Tokenization、上下文窗口、温度/采样、幻觉、指令遵循等 LLM 关键概念，并能在实践中利用或规避它们。
- 掌握 Prompt 工程的核心技法：角色/指令/上下文/输出格式四要素、少样本、思维链（CoT）、JSON 输出约束。
- 理解 RAG 全流程（加载 → 切分 → Embedding → 向量检索 → 生成），能独立搭建知识库问答机器人。
- 理解 Agent 的定义与主流范式（ReAct、Plan-and-Execute、Tool Calling），能手写一个 50 行的极简 Agent 循环。

**一句话目标：知道模型"为什么这样工作"，并掌握让模型"按你想要的方式工作"的手段。**

### 2.2 学习内容与资源

| 主题 | 核心知识点 | 学习资源 |
|---|---|---|
| 机器学习基础 | 监督/无监督/强化学习概念、训练集/测试集 | 吴恩达《Machine Learning》专项课程（可只看概念部分） |
| Transformer | Attention、Self-Attention、多头注意力、位置编码 | 论文《Attention Is All You Need》；3Blue1Brown 注意力可视化视频 |
| LLM 关键概念 | Token、上下文窗口、Temperature/Top-p、幻觉、RLHF | OpenAI 官方博客；Anthropic Docs；Hugging Face NLP 课程 |
| Prompt 工程 | 结构化 Prompt、Few-shot、CoT、JSON Schema 约束、防注入 | OpenAI Prompt Engineering 指南；Anthropic Prompt 最佳实践 |
| RAG | 加载/切分/Embedding/检索/重排、向量库原理 | 论文《Retrieval-Augmented Generation》；LlamaIndex 文档；DeepLearning.AI RAG 课程 |
| Agent 原理 | Agent 定义、ReAct、Plan-and-Execute、Tool Calling、记忆、规划 | 论文《ReAct: Synergizing Reasoning and Acting》；LangChain 官方概念文档 |

### 2.3 练习任务（按周推进）

**第 5~6 周：LLM 概念与 Prompt 工程**
- 任务 1（必做）：写 5 组"同一问题不同 Prompt"的对比实验，观察温度/角色设定/少样本对输出的影响，记录成实验报告。
- 任务 2（必做）：做一个**结构化信息抽取器**：给定一段合同/简历文本，用 JSON Schema 约束输出为结构化字段（姓名/金额/日期等）。
- 任务 3（必做）：做一个**文本总结器**：输入长文（>上下文窗口），用切分 + 分块总结 + 合并的方式输出摘要，体会上下文窗口限制。
- 任务 4（可选）：尝试一次"幻觉探测"——让模型编造不存在的引用并找出错误。

**第 7~8 周：RAG 知识库问答**
- 任务 5（必做）：选取 10~20 篇本地文档（Markdown/PDF），实现"加载 → 切分（chunk）→ Embedding → 存入 Chroma/FAISS → 检索 → 生成"全流程。
- 任务 6（必做）：对比"直接问模型 vs 检索增强后问模型"在事实性上的差异，写对比结论。
- 任务 7（必做）：实现**引用溯源**——回答时附带来源文档与片段。
- 任务 8（可选）：进阶——加入混合检索（BM25+向量）或重排序（Rerank）。
- 里程碑②：**本地文档知识库问答机器人**（命令行版即可），支持引用溯源。

**第 9 周：Agent 原理与手写循环**
- 任务 9（必做）：阅读 ReAct 论文摘要 + 一个 LangChain 官方 ReAct 示例。
- 任务 10（必做，核心）：**手写 50 行极简 Agent 循环**——不依赖框架，用 `while` + 工具函数字典 + LLM 调用实现"推理→行动→观察"循环。
- 任务 11（必做）：给 Agent 挂 3 个自制工具（计算器 / 当前时间 / 文件读取），验证循环能正确选择并执行工具。
- 任务 12（必做）：在 Agent 循环中加入"最大步数限制"与"失败重试"逻辑，体会可靠性的重要性。

### 2.4 通关自测清单

- [ ] 能向别人解释 Token、上下文窗口、温度、幻觉（各用一句话）
- [ ] 能用 JSON Schema 稳定约束模型输出，解析成功率 ≥ 95%
- [ ] 知识库机器人能回答事实性问题并给出引用来源
- [ ] 能默写/手写 50 行 Agent 循环，说清每一步在做什么
- [ ] 已交付里程碑①和里程碑②，并写了 README 与实验结果

---

## 三、阶段三：框架实践（L4 LangChain → LangGraph）

### 3.1 阶段目标

学完本阶段，你应该能够：

- 熟练使用 LangChain：Model I/O、PromptTemplate、OutputParser、LCEL 管道、Tool、AgentExecutor、Memory。
- 熟练使用 LangGraph：StateGraph 状态机、节点/边/条件边、ToolNode、Agent 循环、Checkpointer 持久化、interrupt 人工介入、流式输出。
- 理解框架"帮你做了什么、没帮你做什么"，能读懂关键源码路径，具备框架选型判断力。
- 能独立设计并实现一个**带记忆、多工具、可人工审批、可断点续跑**的业务 Agent。

**一句话目标：用框架把阶段二的能力"工程化、可控化、可复用化"。**

### 3.2 学习内容与资源

| 主题 | 核心知识点 | 学习资源 |
|---|---|---|
| LangChain | ChatModel、PromptTemplate、OutputParser、LCEL、Tool、AgentExecutor、Memory | LangChain 官方 Docs & Cookbook；DeepLearning.AI《LangChain for LLM Application Development》 |
| LangGraph | StateGraph、TypedDict 状态、节点/边/条件边、ToolNode、Checkpointer、interrupt、Streaming、LangSmith | LangGraph 官方 Docs（StateGraph 教程、Agent 示例）；langchain-ai/langgraph 官方 examples |
| 框架原理 | 回调机制、运行时、自定义节点/工具 | 阅读 LangChain/LangGraph 源码关键路径 |
| 框架对比 | LangChain / LangGraph / LlamaIndex / AutoGen / CrewAI / Dify 定位差异 | 官方文档横向对比；本环境《agent-skill-inventory.md》§5.1 表格 |

### 3.3 练习任务（按周推进）

**第 10~11 周：LangChain 上手**
- 任务 1（必做）：用 LCEL 重写阶段二的"信息抽取器"：`prompt | model | parser`，体会管道化。
- 任务 2（必做）：用 LangChain Tool + AgentExecutor 复刻阶段二的手写 Agent（3 个工具），对比框架版与手写版的差异。
- 任务 3（必做）：接入 ConversationBufferMemory，让 Agent 记住多轮对话历史。
- 任务 4（必做）：用 Document Loader + Text Splitter + VectorStore 把阶段二的 RAG 机器人迁移到 LangChain 生态。

**第 12~13 周：LangGraph 核心（本阶段重点）**
- 任务 5（必做）：从官方教程抄写并理解第一个 StateGraph 示例，逐行注释状态/节点/边的作用。
- 任务 6（必做）：实现一个 **Tool-Calling Agent 循环**：LLM 节点 + ToolNode + 条件边（有工具调用就继续，否则结束）。
- 任务 7（必做）：给 Agent 加 **Checkpointer 持久化**，实现多会话记忆与断点续跑。
- 任务 8（必做）：在"执行危险操作"前加入 **interrupt 人工审批**节点，理解 Human-in-the-Loop 设计。
- 任务 9（必做）：实现**流式输出**（Streaming），并接入 LangSmith 观察一次完整的调用链路（token 数、耗时）。

**第 14 周：综合与里程碑**
- 任务 10（必做）：设计一个业务场景 Agent（如"客服工单处理 / 旅行行程规划 / 数据查询助手"），要求：多工具 + 长期记忆 + 人工审批 + 断点续跑 + 流式输出。
- 任务 11（必做）：画一张状态图（StateGraph 示意图）放 README，写清楚每个节点的职责。
- 里程碑：**带记忆、可人工审批、可断点续跑的业务 Agent**（LangGraph 实现，代码结构清晰、有注释、有 README）。

### 3.4 通关自测清单

- [ ] 能用 LCEL 一句话串联 prompt→model→parser 并成功运行
- [ ] 能默画 StateGraph 的"模型-工具-条件边"循环结构图
- [ ] 能实现 Checkpointer 持久化并在新会话恢复旧状态
- [ ] 能解释 interrupt 与普通节点的区别，并演示一次人工审批流程
- [ ] 里程碑项目已包含状态图、README、LangSmith 链路截图

---

## 四、阶段四：项目实战（L5 工具链：评测 / 部署 / 安全 / 产品化）

### 4.1 阶段目标

学完本阶段，你应该能够：

- 建立评测体系：构建评测集、用 LLM-as-a-Judge / 规则 / 人工进行多维评估，产出评测报告。
- 掌握部署链路：用 FastAPI 封装 Agent 服务、Streamlit/Gradio 做界面、Docker 容器化部署。
- 具备安全意识：Prompt 注入防护、工具权限最小化、敏感操作审批、限流与超时。
- 具备可观测性：链路追踪、token 成本统计、日志与告警。
- 完成一次"从需求到上线"的完整项目闭环，并输出作品集级别的交付物。

**一句话目标：把"能跑的 Demo"升级为"能上线、能评测、能维护、讲得清成本与风险"的产品。**

### 4.2 学习内容与资源

| 主题 | 核心知识点 | 学习资源 |
|---|---|---|
| 评测 | 评测集构建、准确率/ROUGE、LLM-as-a-Judge、人工评估 | Ragas 文档；Promptfoo 文档；DeepEval 文档 |
| 可观测性 | LangSmith / Langfuse 链路追踪、成本统计 | LangSmith 官方文档；Langfuse 文档 |
| 服务化 | FastAPI、Pydantic、Streamlit / Gradio、Docker | FastAPI 官方教程；Docker 官方 Get Started |
| 安全 | Prompt 注入、权限最小化、沙箱执行、限流 | OWASP LLM Top 10；llm-guard 文档 |
| 产品化 | 配置管理、缓存、并发控制、灰度、运维 | 12-Factor App；《Designing Machine Learning Systems》 |

### 4.3 练习任务（按周推进）

**第 15 周：评测体系**
- 任务 1（必做）：为阶段三的 Agent 编写 30 条评测用例（覆盖正常/边界/恶意输入三类）。
- 任务 2（必做）：用 LLM-as-a-Judge + 规则两种方式给 Agent 打分，对比两者一致性，输出评测报告。
- 任务 3（必做）：针对评测暴露的问题迭代 Prompt / 工具描述，验证评分提升，记录"评测→改进"闭环。

**第 16 周：服务化与界面**
- 任务 4（必做）：用 FastAPI 把 Agent 封装为 REST API（POST /chat、GET /health、鉴权）。
- 任务 5（必做）：用 Streamlit 或 Gradio 做一个聊天界面，支持上传附件/切换会话。
- 任务 6（必做）：本地跑通 Dockerfile，把服务容器化；确认"换台机器也能一键启动"。

**第 17 周：安全与可靠性加固**
- 任务 7（必做）：做 5 个 Prompt 注入攻击测试（如让 Agent 泄露 system prompt），实现防护并复测。
- 任务 8（必做）：给工具调用加权限控制（只读工具 vs 写操作工具分开、写操作必须审批）。
- 任务 9（必做）：为 API 加入限流、超时、并发控制，压测 50 并发确认稳定。

**第 18 周：上线与交付**
- 任务 10（必做）：接入 Langfuse/LangSmith 统计真实用户调用的 token 成本，估算单次对话成本。
- 任务 11（必做）：写完整项目文档：README、架构图、API 文档、评测报告、部署手册、成本说明。
- 里程碑：**可上线运行的 Agent 服务**（FastAPI + 界面 + Docker + 评测报告 + 安全测试记录）。

### 4.4 通关自测清单

- [ ] 有 ≥30 条评测用例与一份评测报告，能说清改进前后的分数变化
- [ ] FastAPI 接口在 Docker 中稳定运行，界面可正常使用
- [ ] 能演示一次注入攻击被成功拦截
- [ ] 能估算单次对话的 token 成本与并发上限
- [ ] 全部文档齐全，可在简历/作品集直接展示

---

## 五、实战案例：一条主线贯穿四阶段

> 本路径的四个阶段看起来是"打基础"，实际可以围绕**同一个业务案例**逐阶段叠加能力，最终形成一份完整作品。下面以 **「智能旅行行程规划 Agent」** 为例（该案例已在本环境产出《舟山亲子游攻略.md》等可参考素材），演示每个阶段你为该案例交付什么。

### 案例总览：智能旅行行程规划 Agent

**最终形态**：用户输入目的地、人数、天数、偏好（亲子/美食/预算），Agent 自动生成行程并给出依据与备选方案；支持查询实时信息（天气、票价）、记忆用户偏好、危险/付费操作前人工确认。

| 阶段 | 该阶段为案例交付什么 | 用到本阶段技能 |
|---|---|---|
| 阶段一 | `cli-travel-bot/`：命令行对话脚本，能根据用户输入打印固定模板行程（先写死逻辑，不调 LLM），并调通真实 LLM API 输出自由文本建议 | Python、API、异步、Git |
| 阶段二 | `extractor/`：从用户描述中结构化抽取「目的地/人数/天数/预算/禁忌」；`rag-qa/`：把 10 篇本地攻略文档做成知识库，回答"某地适合带孩子吗"并给引用；`handwritten-agent/`：手写 Agent 循环，自动调用"天气查询/费用计算"两个函数工具 | Prompt、JSON Schema、RAG、ReAct 手写循环 |
| 阶段三 | `langgraph-travel-agent/`：LangGraph 重写——节点化（收集需求 → 检索攻略 → 规划行程 → 工具查询 → 审批确认），Checkpointer 记住用户偏好，预订/付款前 interrupt 人工审批 | LangChain、LangGraph、记忆、审批 |
| 阶段四 | `travel-agent-service/`：FastAPI + Gradio 界面 + Docker 上线；30 条评测用例（正常/边界/恶意注入）；安全加固（防 Prompt 注入、写操作审批）；成本统计（单次行程约 X token ≈ Y 元） | 评测、部署、安全、可观测性 |

### 案例实现要点（阶段三/四核心代码骨架）

```python
# langgraph-travel-agent/graph.py —— 核心状态图（示意）
from typing import TypedDict, Annotated
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages

class TravelState(TypedDict):
    messages: Annotated[list, add_messages]
    requirement: dict      # 目的地/人数/天数/偏好
    itinerary: list        # 生成的行程
    needs_approval: bool   # 是否需要人工审批

def collect_requirement(state): ...   # 节点1：抽取需求
def retrieve_knowledge(state): ...    # 节点2：检索本地攻略
def plan_itinerary(state): ...        # 节点3：生成行程
def query_tools(state): ...           # 节点4：查询天气/票价（ToolNode）
def human_approval(state): ...        # 节点5：interrupt 人工审批
def route_after_plan(state):          # 条件边：是否需要审批/继续

g = StateGraph(TravelState)
g.add_node("collect", collect_requirement)
g.add_node("retrieve", retrieve_knowledge)
g.add_node("plan", plan_itinerary)
g.add_node("tools", query_tools)
g.add_node("approve", human_approval)
g.set_entry_point("collect")
g.add_edge("collect", "retrieve")
g.add_edge("retrieve", "plan")
g.add_conditional_edges("plan", route_after_plan,
    {"need_tools": "tools", "need_approval": "approve", "done": END})
g.add_edge("tools", "plan")
g.add_edge("approve", END)
```

### 案例交付清单（作品集）

```
travel-agent-portfolio/
├── stage1-cli-travel-bot/      # 命令行原型（阶段一）
├── stage2-extractor/           # 需求结构化抽取（阶段二①）
├── stage2-rag-qa/              # 攻略知识库问答 + 引用（阶段二②）
├── stage2-handwritten-agent/   # 手写 Agent 循环（阶段二③）
├── stage3-langgraph-travel-agent/  # 状态图版 Agent（阶段三）
├── stage4-travel-agent-service/    # 上线服务（阶段四）
│   ├── README.md               # 项目总览 + 架构图 + 状态图
│   ├── EVALUATION.md           # 30 条评测用例 + 打分报告
│   ├── SECURITY.md             # 注入测试记录 + 防护方案
│   ├── COST.md                 # token 成本估算
│   └── DEPLOY.md               # Docker 部署手册
```

> **换案例原则**：如果你对旅行不感兴趣，换成「客服工单处理 Agent」「数据查询助手」「健身/饮食规划 Agent」均可——结构完全一致，只替换工具与知识库内容。**案例不在多，在于一条线做透。**

---

## 六、学习节奏与时间投入参考

| 场景 | 每日投入 | 阶段一 | 阶段二 | 阶段三 | 阶段四 | 总计 |
|---|---|---|---|---|---|---|
| 上班族/学生 | 2~3 小时 | 4 周 | 5 周 | 5 周 | 4 周 | ≈ 18 周（4.5 个月） |
| 全职学习 | 6~8 小时 | 2 周 | 3 周 | 3 周 | 2 周 | ≈ 10 周（2.5 个月） |

**每周固定动作**：周日 30 分钟复盘——本周学会了什么 / 卡在哪 / 下周重点；更新第七章进度追踪表；用 Git 提交本周全部代码。

---

## 七、进度追踪表（可勾选）

> 使用方法：每周日复盘时，将已完成任务的复选框勾选为 `[x]`，并在「完成日期 / 备注」列记录。**4 个阶段共 41 个必做任务 + 6 个里程碑**，全部勾选即视为路径完成。

### 阶段一进度表（第 1~4 周）

| # | 任务 | 完成 | 完成日期 | 备注 |
|---|---|---|---|---|
| 1 | 安装环境（Python/VS Code/Git/uv） | [ ] | | |
| 2 | 天气数据模拟器脚本 | [ ] | | |
| 3 | （可选）LeetCode 每日 1 题 | [ ] | | |
| 4 | 调通真实 LLM API | [ ] | | |
| 5 | 多轮对话改造 | [ ] | | |
| 6 | 异常处理 + logging | [ ] | | |
| 7 | asyncio 并发调用对比 | [ ] | | |
| 8 | 流式输出 | [ ] | | |
| 9 | pytest 单元测试 ×3 | [ ] | | |
| 10 | 代码推 GitHub + README | [ ] | | |
| 🏁 | **里程碑①：命令行多轮聊天机器人** | [ ] | | |
| ✅ | 1.4 通关自测清单全部勾选 | [ ] | | |

### 阶段二进度表（第 5~9 周）

| # | 任务 | 完成 | 完成日期 | 备注 |
|---|---|---|---|---|
| 1 | 5 组 Prompt 对比实验报告 | [ ] | | |
| 2 | 结构化信息抽取器（JSON Schema） | [ ] | | |
| 3 | 长文分块总结器 | [ ] | | |
| 4 | （可选）幻觉探测实验 | [ ] | | |
| 5 | RAG 全流程（加载→切分→向量→检索→生成） | [ ] | | |
| 6 | 直接问 vs 检索增强对比结论 | [ ] | | |
| 7 | 引用溯源实现 | [ ] | | |
| 8 | （可选）混合检索/重排序 | [ ] | | |
| 🏁 | **里程碑②：本地文档知识库问答机器人** | [ ] | | |
| 9 | 阅读 ReAct 论文摘要 + 官方示例 | [ ] | | |
| 10 | 手写 50 行极简 Agent 循环 | [ ] | | |
| 11 | 挂载 3 个自制工具 | [ ] | | |
| 12 | 最大步数限制 + 失败重试 | [ ] | | |
| ✅ | 2.4 通关自测清单全部勾选 | [ ] | | |

### 阶段三进度表（第 10~14 周）

| # | 任务 | 完成 | 完成日期 | 备注 |
|---|---|---|---|---|
| 1 | LCEL 重写信息抽取器 | [ ] | | |
| 2 | LangChain AgentExecutor 复刻手写 Agent | [ ] | | |
| 3 | 接入 ConversationBufferMemory | [ ] | | |
| 4 | RAG 机器人迁移到 LangChain | [ ] | | |
| 5 | 抄写并注释第一个 StateGraph 示例 | [ ] | | |
| 6 | Tool-Calling Agent 循环（LLM+ToolNode+条件边） | [ ] | | |
| 7 | Checkpointer 持久化 + 断点续跑 | [ ] | | |
| 8 | interrupt 人工审批节点 | [ ] | | |
| 9 | 流式输出 + LangSmith 链路观察 | [ ] | | |
| 10 | 业务场景 Agent 综合实现 | [ ] | | |
| 11 | StateGraph 状态图 + README | [ ] | | |
| 🏁 | **里程碑③：带记忆/审批/断点的业务 Agent** | [ ] | | |
| ✅ | 3.4 通关自测清单全部勾选 | [ ] | | |

### 阶段四进度表（第 15~18 周）

| # | 任务 | 完成 | 完成日期 | 备注 |
|---|---|---|---|---|
| 1 | 编写 30 条评测用例（正常/边界/恶意） | [ ] | | |
| 2 | LLM-as-a-Judge + 规则双打分，输出评测报告 | [ ] | | |
| 3 | 评测→改进闭环，记录分数变化 | [ ] | | |
| 4 | FastAPI 封装 REST API（/chat、/health、鉴权） | [ ] | | |
| 5 | Streamlit/Gradio 聊天界面 | [ ] | | |
| 6 | Docker 容器化，验证一键启动 | [ ] | | |
| 7 | 5 个 Prompt 注入测试 + 防护复测 | [ ] | | |
| 8 | 工具权限控制（只读/写操作分离） | [ ] | | |
| 9 | 限流/超时/并发控制 + 50 并发压测 | [ ] | | |
| 10 | Langfuse/LangSmith 成本统计 | [ ] | | |
| 11 | 完整项目文档（README/架构/API/评测/部署/成本） | [ ] | | |
| 🏁 | **里程碑④：可上线运行的 Agent 服务** | [ ] | | |
| ✅ | 4.4 通关自测清单全部勾选 | [ ] | | |

### 总体进度速览（每周更新）

| 阶段 | 必做任务完成数 | 里程碑完成 | 通关自测完成 | 周次 |
|---|---|---|---|---|
| 阶段一 | 0 / 10 | ⬜ | ⬜ | 第 1~4 周 |
| 阶段二 | 0 / 12 | ⬜ | ⬜ | 第 5~9 周 |
| 阶段三 | 0 / 11 | ⬜ | ⬜ | 第 10~14 周 |
| 阶段四 | 0 / 11 | ⬜ | ⬜ | 第 15~18 周 |
| **合计** | **0 / 44** | **0 / 4** | **0 / 4** | |

---

## 八、资源推荐汇总

> 按优先级分三档：**⭐ 必学**（路径内必需，精读官方文档）、**🌟 重点**（关键补充，按需精读）、**✨ 拓展**（学有余力再看）。全部资源均为免费/官方可获取（除标注图书外）。

### A. 官方文档与教程（优先级最高，版本最新）

| 资源 | 档位 | 用途 |
|---|---|---|
| [Python 官方 Tutorial](https://docs.python.org/3/tutorial/) | ⭐ 必学 | 阶段一语法地基 |
| [OpenAI API Docs（Chat Completions）](https://platform.openai.com/docs) | ⭐ 必学 | 阶段一 API 范式 + 阶段二 Prompt 指南 |
| [Anthropic Prompt 最佳实践](https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering) | ⭐ 必学 | 阶段二 Prompt 工程 |
| [LangChain 官方 Docs & Cookbook](https://python.langchain.com/docs) | ⭐ 必学 | 阶段三框架 |
| [LangGraph 官方 Docs](https://langchain-ai.github.io/langgraph/) | ⭐ 必学 | 阶段三状态图（StateGraph 教程） |
| [FastAPI 官方教程](https://fastapi.tiangolo.com/tutorial/) | ⭐ 必学 | 阶段四服务化 |
| [Docker Get Started](https://docs.docker.com/get-started/) | ⭐ 必学 | 阶段四部署 |
| [Hugging Face NLP Course](https://huggingface.co/learn/nlp-course) | 🌟 重点 | 阶段二 LLM 原理补充 |
| [LlamaIndex 文档](https://docs.llamaindex.ai) | 🌟 重点 | 阶段二 RAG 对比参考 |
| [OWASP LLM Top 10](https://owasp.org/www-project-top-10-for-large-language-model-applications/) | 🌟 重点 | 阶段四安全 |
| [Ragas 文档](https://docs.ragas.io) / [Promptfoo](https://www.promptfoo.dev) / [DeepEval](https://docs.confident-ai.com) | 🌟 重点 | 阶段四评测（三选一深入） |
| [Langfuse 文档](https://langfuse.com/docs) | ✨ 拓展 | 阶段四可观测性（自托管替代 LangSmith） |

### B. 优质课程

| 课程 | 档位 | 说明 |
|---|---|---|
| 吴恩达《Machine Learning Specialization》 | 🌟 重点 | 只看概念部分即可，不必做全部编程作业 |
| DeepLearning.AI《ChatGPT Prompt Engineering for Developers》 | ⭐ 必学 | 1 小时，阶段二 Prompt 入门最佳 |
| DeepLearning.AI《LangChain for LLM Application Development》 | 🌟 重点 | 阶段三 LangChain 快速上手 |
| DeepLearning.AI《Building and Evaluating Advanced RAG》 | ✨ 拓展 | 阶段二 RAG 进阶 |

### C. 图书（可选，按需购买）

| 图书 | 档位 | 建议阅读时机 |
|---|---|---|
| 《Python 编程：从入门到实践》（Eric Matthes） | ⭐ 必学 | 阶段一，读到函数/类即可 |
| 《Pro Git》（Scott Chacon） | 🌟 重点 | 阶段一，前 3 章 |
| 《Using Asyncio in Python》（Caleb Hattingh） | ✨ 拓展 | 阶段一异步进阶 |
| 《Designing Machine Learning Systems》（Chip Huyen） | ✨ 拓展 | 阶段四产品化思维 |

### D. 论文与视频（建立直觉）

| 资源 | 档位 | 说明 |
|---|---|---|
| 《Attention Is All You Need》（2017） | 🌟 重点 | 阶段二末精读；先看 3Blue1Brown 视频建立直觉 |
| 《Retrieval-Augmented Generation》（2020） | 🌟 重点 | RAG 原始论文，读摘要+框架图即可 |
| 《ReAct: Synergizing Reasoning and Acting》（2022） | ⭐ 必学 | Agent 循环思想源头，读摘要+图 1 |
| 3Blue1Brown「注意力机制」系列视频 | ⭐ 必学 | Transformer 直觉最佳入门 |

### E. 社区与提问渠道

| 渠道 | 用途 |
|---|---|
| GitHub Issues（langchain-ai/langgraph 等） | 遇到框架 bug 先搜这里 |
| Stack Overflow（tag: langchain） | 常见工程问题 |
| X / Hugging Face 社区 | 追踪前沿（MCP、Computer Use 等） |
| 本地团队/学习搭子 | 每周复盘，互相 code review |

---

## 九、四阶段成果一览（作品集目录）

> 以第五章"旅行 Agent"案例为最终主线；若选其他案例，结构同理。这是你 4~6 个月后的**完整作品集目录**：

```
my-agent-portfolio/
├── stage1-cli-chatbot/        # 阶段一：命令行多轮聊天机器人
├── stage2-extractor/          # 阶段二①：结构化信息抽取器
├── stage2-rag-qa/             # 阶段二②：本地文档知识库问答机器人
├── stage2-handwritten-agent/  # 阶段二③：手写 50 行极简 Agent 循环
├── stage3-langgraph-agent/    # 阶段三：带记忆/审批/断点的业务 Agent
├── stage4-agent-service/      # 阶段四：FastAPI + Docker 上线服务
│   ├── README.md              # 项目总览 + 架构图
│   ├── EVALUATION.md          # 评测报告
│   ├── SECURITY.md            # 安全测试记录
│   └── COST.md                # 成本估算
```

**作品集写作模板（每个里程碑项目 README 至少包含）**：
1. 项目一句话简介 + 演示截图/GIF
2. 架构图（阶段三起必须含 StateGraph 状态图）
3. 核心代码路径说明（`src/` 目录树 + 每个模块职责）
4. 本地运行方式（环境变量、依赖、启动命令）
5. 评测/测试结果（阶段四起必须）
6. 已知限制与下一步改进

---

## 十、常见问题与避坑指南

1. **要不要先学完 Python 再学 Agent？** 不用。阶段一 3 周 Python 够用，边做边补；语法细节（装饰器、生成器）遇到再学。
2. **要不要先啃完《Attention Is All You Need》？** 不用。先看 3Blue1Brown 视频 + 中文图解建立直觉，论文留到阶段二末或需要时精读。
3. **框架版本变化快怎么办？** 优先看官方文档（而非过时博客）；API 变化时以官方升级指南为准；本项目统一锁定 LangGraph 版本并在 `pyproject.toml` 固定。
4. **卡住了怎么办？** 先跑通最小用例 → 看官方示例 → 看 GitHub Issues → 提问时附完整报错与最小复现代码。
5. **API Key 花钱怎么办？** 用国产低价模型（DeepSeek/通义/GLM）学习完全够用；阶段一~二每月几块钱以内，阶段四成本统计本身就是作业。
6. **要不要学微调/多智能体？** 阶段四完成后按需选学（微调 LoRA、AutoGen/CrewAI 多智能体、MCP、Computer Use），它们属于"加分项"而非"必需项"。
7. **代码全抄官方示例算不算作弊？** 抄写 + 逐行注释 + 改造功能 = 有效学习；只抄不注释 = 无效。检验标准：能否关掉教程独立重写。
8. **中间断了几天怎么办？** 不要补进度，从断点继续；用进度追踪表记录断点位置，先跑通最小用例恢复手感。

---

## 十一、进阶方向（阶段四之后，可选）

- **多智能体协作**：AutoGen / CrewAI 角色分工（Planner/Executor/Critic/Supervisor），对比单 Agent 收益。
- **MCP（Model Context Protocol）**：标准化工具接入协议，成为跨应用 Agent 互操作的基础设施。
- **前沿范式**：Reflexion（反思）、Tree of Thoughts（多分支）、Computer Use / 浏览器 Agent。
- **领域深耕**：代码 Agent（SWE-bench）、数据分析 Agent、客服 Agent、具身智能。
- **研究向**：Agent 评测基准（AgentBench、GAIA）、安全红队、可解释性。

---

*本文档由学习路径设计子任务产出，与《agent-skill-inventory.md》技能清单配套使用；阶段划分与里程碑可直接对应简历项目经历。*
*配套文档：《agent-skill-inventory.md》（技能清单）、《agent-intro.md》（Agent 概念入门）、《agent-research-materials.md》（调研材料）。*
