# AI Agent 领域前置知识与核心技能清单（从零到实战）

> 子任务产出：调研 AI Agent 领域所需的前置知识与核心技能（编程基础 / 机器学习与 LLM 原理 / Agent 框架 / 实战工具），形成结构化技能清单。
> 用途：作为《从零基础到实战的 AI Agent 学习路径》的技能底稿，供路径规划、自评与选课参考。
> 定位读者：零基础学习者、转行者、需要带队做 Agent 项目的技术负责人。

---

## 一、技能全景图（一图看懂）

```
                     ┌─────────────────────────────────────────────┐
                     │             L5 实战落地（部署/评测/运维）      │
                     ├─────────────────────────────────────────────┤
                     │             L4 Agent 工程（框架/编排）        │
                     ├─────────────────────────────────────────────┤
                     │             L3 Agent 核心原理（ReAct/工具调用）│
                     ├─────────────────────────────────────────────┤
                     │             L2 机器学习 / LLM 原理            │
                     ├─────────────────────────────────────────────┤
                     │             L1 编程基础（Python/工程化）      │
                     └─────────────────────────────────────────────┘
```

- **L1 编程基础**：所有上层的地基，Python 为主，配套工程化工具。
- **L2 ML/LLM 原理**：理解"模型为什么会这样工作"，决定你能走多远。
- **L3 Agent 核心原理**：Prompt 工程、工具调用、记忆、规划、反思等概念。
- **L4 Agent 框架**：LangChain / LangGraph 等抽象与编排能力。
- **L5 实战工具链**：API、向量库、评测、部署、可观测性。

> 技能间依赖关系：L1 ⊃ L2 ⊃ L3 ⊃ L4 ⊃ L5（可并行，但大体有先后）。

---

## 二、L1 编程基础（前置必学）

### 2.1 Python 核心语法（★★★ 必备）
| 技能点 | 掌握标准 | 用途 |
|---|---|---|
| 变量、类型、流程控制 | 能写 50 行以内脚本 | 日常脚本、测试 prompt |
| 函数、类、模块/包管理 | 能用 `import` 组织代码 | 写 Agent、封装工具 |
| 异常处理与日志 | try/except、logging | Agent 运行稳定性、排错 |
| 文件与 JSON/YAML 读写 | 能读写配置、数据 | 配置化、数据持久化 |
| 列表/字典推导、生成器 | 能写出简洁高效代码 | 数据处理、工具封装 |
| 类型注解（typing） | 能给函数/类加类型 | LangChain/LangGraph 大量依赖类型系统 |
| 装饰器（进阶） | 理解函数式扩展机制 | 框架源码阅读、工具注册 |

**推荐资源**：Python 官方 Tutorial、《Python 编程：从入门到实践》、廖雪峰 Python 教程。

### 2.2 数据结构与算法基础（★★☆ 重要）
- 数组、链表、栈、队列、哈希表、树/图：用于理解框架状态管理与上下文。
- 时间复杂度概念：评估 Agent 多轮循环的性能。
- 不需要刷到竞赛级别，能看懂/写 LeetCode 简单~中等题即可。

### 2.3 异步与并发（★★★ 必备）
- `async / await`、`asyncio` 事件循环：LLM API 调用是 I/O 密集，必须会异步并发。
- 并发调用多模型/多工具：大幅提升 Agent 吞吐。
- **推荐**：《Using Asyncio in Python》或官方文档。

### 2.4 HTTP / API 工程（★★★ 必备）
- HTTP 方法、状态码、鉴权（API Key / Bearer Token）、JSON。
- `requests` / `httpx`（本环境已装 httpx 0.28.1）。
- OpenAI 兼容 API 调用范式（`/chat/completions`）：国内通义、DeepSeek 等均兼容，学会一次处处可用。

### 2.5 命令行与开发环境（★★★ 必备）
- 终端基础（cd/ls/mkdir/curl/pip/uv）、环境变量。
- 虚拟环境（venv / uv，本环境已用 uv）。
- Git 与 GitHub（clone/commit/push/PR）：协作与版本管理。

### 2.6 工程化习惯（★★☆ 进阶）
- 单元测试（pytest）：Agent 行为不可控，测试是质量底线。
- 配置管理（dotenv / pydantic-settings）：API Key 等敏感信息不入库。
- 基础设计模式：工厂、策略、观察者——框架内部大量使用。

---

## 三、L2 机器学习 / LLM 原理（知其所以然）

### 3.1 机器学习基础（★★☆ 建议掌握概念层）
- 监督/无监督/强化学习：理解 Agent 训练与 RLHF 的源头。
- 训练集/验证集/测试集、过拟合：做评估时必用。
- 不需要会手推反向传播，但要知道"模型 = 参数 + 推理"。

### 3.2 深度学习与 Transformer（★★★ 核心）
- 神经网络基础：embedding（向量）、前向传播、损失函数。
- **Attention / Self-Attention**：LLM 的基石，必须理解"注意力"。
- **Transformer 架构**：Encoder-Decoder、多头注意力、位置编码。
- 预训练（Pre-training）→ 微调（Fine-tuning）→ 推理（Inference）三段式。

### 3.3 LLM 关键概念（★★★ 核心）
| 概念 | 说明 | 为什么重要 |
|---|---|---|
| Tokenization | 文本 → token 的分词 | 理解上下文窗口与计费 |
| 上下文窗口（Context Window） | 一次能处理的最大 token 数 | 决定 RAG/长文策略 |
| 温度（Temperature）/ Top-p | 采样随机性控制 | 创意 vs 稳定的平衡 |
| 幻觉（Hallucination） | 模型编造事实 | Agent 必须接工具/检索纠偏 |
| 指令遵循（Instruction Following） | 按 system prompt 执行 | Prompt 工程的基础 |
| 涌现能力（Emergent Ability） | 规模增大后出现的新能力 | Agent 规划能力的来源 |
| RLHF / 对齐（Alignment） | 人类反馈强化学习 | 理解模型"守规矩"的原因 |
| 多模态 | 文本/图像/音频/视频输入输出 | 新一代 Agent 的感知能力 |

### 3.4 Prompt 工程（★★★ 必备实战技能）
- 结构化 Prompt：角色（Role）+ 指令（Instruction）+ 上下文（Context）+ 输出格式（Output Format）。
- 少样本示例（Few-shot）、思维链（Chain-of-Thought, CoT）。
- 输出约束：JSON Schema / 枚举 / 正则（配合工具调用解析）。
- 防注入（Prompt Injection）：Agent 面对不可信内容时的安全基线。

### 3.5 RAG 检索增强生成（★★★ 必备实战技能）
- 流程：文档加载 → 切分（Chunking）→ Embedding → 向量存储 → 检索 → 生成。
- 向量检索原理：余弦相似度、ANN（近似最近邻）、HNSW。
- 进阶：混合检索（关键词 BM25 + 向量）、重排序（Rerank）、查询改写。

### 3.6 模型评估与微调（★★☆ 进阶）
- 评估指标：准确率、BLEU/ROUGE、LLM-as-a-Judge、人工评估。
- 微调 vs RAG vs Prompt 的取舍：90% 场景用 RAG + Prompt，微调只在风格/格式/私有知识需固化时用。
- LoRA 等参数高效微调（PEFT）：理解概念即可，不必训练。

---

## 四、L3 Agent 核心原理（本领域专精）

### 4.1 Agent 概念与范式（★★★ 必备）
- 定义：**Agent = LLM（大脑）+ 记忆 + 规划 + 工具 + 行动闭环**。
- 与 Chatbot / Copilot 的区别：自主性、多步执行、持续状态。
- 主流范式：
  - **ReAct**（Reasoning + Acting）：推理 → 行动 → 观察 → 再推理，核心循环。
  - **Plan-and-Execute**：先整体规划，再逐步执行。
  - **Reflexion / Self-Refine**：执行后反思改进。
  - **Tree of Thoughts（ToT）**：多分支探索（进阶）。
  - **Function Calling / Tool Calling**：模型输出结构化调用参数，由运行时执行——当前工程主流。

### 4.2 工具调用（Tool Calling）（★★★ 必备）
- Tool 描述（name + description + parameters JSON Schema）如何影响模型选择。
- 工具注册与执行循环：`llm.choose_tool(question) → execute → return result → llm.continue`。
- 工具失败重试、并发工具调用（parallel tool calls）。
- 实践：自己写 3~5 个工具（天气/计算器/文件读写/搜索）并用 LangChain/LangGraph 串起来。

### 4.3 记忆系统（★★☆ 重要）
- 短期记忆：对话上下文 / 工作区状态。
- 长期记忆：向量库 + 摘要存储历史、用户画像。
- 记忆的读写策略：写入时机、检索时机、遗忘策略。

### 4.4 规划与任务分解（★★☆ 重要）
- 子任务拆分、任务依赖图、动态重规划。
- 失败恢复与重试策略（决定 Agent 可用性）。
- 人工介入（Human-in-the-Loop）：授权、确认、纠偏点设计。

### 4.5 多智能体协作（★★☆ 进阶）
- 角色分工（Planner / Executor / Critic / Supervisor）。
- 通信协议、共享状态、冲突解决。
- 适用场景与陷阱（别为多而多）。

### 4.6 安全与可靠性（★★☆ 进阶）
- Prompt 注入防护、工具权限最小化、敏感操作审批。
- 沙箱执行（代码、Shell）、限流、超时。
- 可观测性：每次调用的输入输出、token 消耗、成本追踪。

---

## 五、L4 Agent 框架（选型与熟练使用）

### 5.1 主流框架横向对比（★★★ 必备认知）
| 框架 | 语言 | 定位/特点 | 适用场景 |
|---|---|---|---|
| **LangChain** | Python/JS | 最普及，组件丰富（LCEL、Tool、Memory） | 快速原型、生态丰富 |
| **LangGraph** | Python/JS | 图式状态机，可控性强，支持人机协作/多 Agent | 生产级复杂流程（本项目采用） |
| **LlamaIndex** | Python | 数据/RAG 见长 | 知识库问答、文档 Agent |
| **AutoGen** | Python | 微软，多 Agent 对话自动化 | 多智能体研究/实验 |
| **CrewAI** | Python | 角色化团队编排，上手快 | 业务团队式 Agent |
| **Semantic Kernel** | C#/Python | 微软，企业级、强类型 | .NET 生态、企业集成 |
| **OpenAI Assistants/Function Calling** | API | 官方原生能力，轻量 | 直接调 API 的简单 Agent |
| **Dify / Coze / 百炼** | 平台 | 低代码可视化编排 | 非工程师快速落地 |
| **Qwen-Agent / MetaGPT / AutoGPT** | Python | 国产/开源实验性 | 学习与研究 |

**选型建议**：入门用 LangChain 快速理解抽象；生产追求可控性用 LangGraph；纯 RAG 用 LlamaIndex；无代码场景用 Dify/Coze。

### 5.2 LangChain 核心技能（★★★ 必备）
- Model I/O：ChatModel、PromptTemplate、OutputParser。
- LCEL（LangChain Expression Language）：`prompt | model | parser` 管道。
- 工具与 AgentExecutor：Tool、`create_tool_calling_agent`。
- 记忆组件：ConversationBufferMemory、向量存储回退。
- 文档加载器（Document Loader）+ 文本分割器（Text Splitter）+ 向量存储（VectorStore）集成。

### 5.3 LangGraph 核心技能（★★★ 必备，项目采用）
- **StateGraph**：定义状态（TypedDict）、节点（Node）、边（Edge）、条件边（Conditional Edge）。
- 状态管理：Reducer、消息列表追加（messages add）。
- 工具节点（ToolNode）与模型节点、循环执行（Agent 循环）。
- Checkpointer（持久化/断点续跑）、interrupt（人工介入）。
- 流式输出（Streaming）、LangSmith 集成。

### 5.4 框架原理与抽象能力（★★☆ 进阶）
- 能读懂框架源码关键路径（LangChain callback、LangGraph runtime）。
- 知道"框架帮你做了什么、没帮你做什么"——避免黑盒依赖。
- 自己实现一个 50 行内的极简 Agent 循环（有助于彻底理解）。

---

## 六、L5 实战工具链（环境与生态）

### 6.1 LLM 服务与 API（★★★ 必备）
| 服务 | 说明 |
|---|---|
| OpenAI（GPT-4o/o系列） | 国际标杆，Function Calling 成熟 |
| Anthropic Claude | 长上下文、代码能力强（本环境已装 anthropic SDK） |
| 通义千问（DashScope，已装 1.27.0） | 国内，阿里云，兼容 OpenAI 接口 |
| DeepSeek | 国内开源/低价，推理强 |
| Ollama / vLLM | 本地/私有化部署开源模型（Qwen、Llama 等） |

要点：掌握 **OpenAI 兼容接口**统一调用范式 + 各家 API Key 管理与成本控制。

### 6.2 Embedding 与向量数据库（★★★ 必备）
- Embedding 模型：OpenAI text-embedding、BGE、通义 text-embedding。
- 向量库选型：
  - **Chroma / FAISS**：轻量入门、本地跑。
  - **Milvus / Qdrant / Weaviate**：生产级、海量数据。
  - **pgvector**：与 PostgreSQL 一体化（本环境有数据库配置，可选用）。
- 元数据过滤 + 混合检索 + 重排。

### 6.3 工具与集成（★★☆ 重要）
- 搜索：Tavily（本项目已用）、SerpAPI、Bing/Google API。
- 浏览器自动化：Playwright / Selenium（网页型 Agent）。
- 代码执行沙箱：E2B、Code Interpreter。
- 数据库与 API：SQLAlchemy、FastAPI（本环境已装 0.141.1）暴露 Agent 服务。

### 6.4 可观测性 / 评测 / 安全（★★☆ 进阶）
- **LangSmith / Langfuse**：链路追踪、token 成本、评测集。
- 评测框架：Ragas（RAG 评测）、Promptfoo、DeepEval。
- 安全：提示注入检测（llm-guard）、红队测试。

### 6.5 部署与产品化（★★☆ 进阶）
- FastAPI + Streamlit / Gradio 快速做 Demo（本环境已有 FastAPI）。
- Docker 容器化、环境隔离。
- 任务队列（Celery / 消息队列）支撑长时间运行任务。
- 缓存、限流、并发控制、灰度发布。

---

## 七、技能清单总表（自评用）

> 等级说明：★☆☆ 了解即可｜★★☆ 能独立完成常规任务｜★★★ 精通、能解决疑难。

| # | 技能域 | 技能项 | 重要度 | 建议阶段 |
|---|---|---|---|---|
| 1 | 编程基础 | Python 语法与函数/类/模块 | ★★★ | 入门 |
| 2 | 编程基础 | 类型注解、装饰器 | ★★☆ | 入门 |
| 3 | 编程基础 | 异步编程 asyncio | ★★★ | 入门 |
| 4 | 编程基础 | HTTP/API 调用、OpenAI 兼容接口 | ★★★ | 入门 |
| 5 | 编程基础 | Git、命令行、虚拟环境 | ★★★ | 入门 |
| 6 | 编程基础 | pytest 单元测试 | ★★☆ | 进阶 |
| 7 | ML/LLM | Transformer、Attention 原理 | ★★★ | 进阶 |
| 8 | ML/LLM | Tokenization、上下文窗口、采样参数 | ★★★ | 入门 |
| 9 | ML/LLM | 幻觉、RLHF、对齐 | ★★☆ | 进阶 |
| 10 | ML/LLM | Prompt 工程（角色/少样本/CoT/JSON 约束） | ★★★ | 入门 |
| 11 | ML/LLM | RAG（切分/Embedding/检索/重排） | ★★★ | 进阶 |
| 12 | ML/LLM | 评估指标、LLM-as-Judge | ★★☆ | 进阶 |
| 13 | ML/LLM | 微调与 LoRA（概念层） | ★☆☆ | 进阶 |
| 14 | Agent 原理 | Agent 范式（ReAct/Plan-Execute/反思） | ★★★ | 进阶 |
| 15 | Agent 原理 | Function Calling / 工具调用循环 | ★★★ | 进阶 |
| 16 | Agent 原理 | 记忆系统（短期/长期） | ★★☆ | 进阶 |
| 17 | Agent 原理 | 规划与任务分解、失败重试 | ★★☆ | 进阶 |
| 18 | Agent 原理 | 多智能体协作 | ★★☆ | 实战 |
| 19 | Agent 原理 | Prompt 注入防护、安全与可靠 | ★★☆ | 实战 |
| 20 | Agent 框架 | LangChain（LCEL/Agent/Memory） | ★★★ | 进阶 |
| 21 | Agent 框架 | LangGraph（StateGraph/循环/Checkpointer） | ★★★ | 实战 |
| 22 | Agent 框架 | LlamaIndex（RAG） | ★★☆ | 实战 |
| 23 | Agent 框架 | 框架选型与源码阅读 | ★★☆ | 实战 |
| 24 | 实战工具 | 多模型 API（OpenAI/Claude/通义/DeepSeek） | ★★★ | 实战 |
| 25 | 实战工具 | Embedding + 向量库（Chroma/Milvus/pgvector） | ★★★ | 实战 |
| 26 | 实战工具 | 搜索/浏览器/代码沙箱工具 | ★★☆ | 实战 |
| 27 | 实战工具 | LangSmith/Langfuse 观测与评测 | ★★☆ | 实战 |
| 28 | 实战工具 | FastAPI/Streamlit/Docker 部署 | ★★☆ | 实战 |

**关键结论（优先级金字塔）**：
1. **必学（★★★，共 12 项）**：Python、异步、API、Prompt 工程、RAG、工具调用、LangChain、LangGraph、多模型 API、向量库——这些构成"能干活"的最低充分集。
2. **重要（★★☆，共 13 项）**：工程化测试、LLM 原理、记忆/规划、多 Agent、评测部署——决定质量与深度。
3. **了解（★☆☆，共 3 项）**：微调、ToT 等——按需补。

---

## 八、从零到实战的学习路径（阶段划分）

### 阶段 0：环境准备（0.5 周）
- 装 Python 3.10+（本环境 3.12）、VS Code、Git。
- 学会用虚拟环境（venv/uv）与 pip 安装依赖。
- **里程碑**：跑通第一个调用 LLM API 的 Hello World 脚本。

### 阶段 1：Python 与 API 工程（2~3 周）
- 核心语法 + 文件/JSON + 异常处理。
- `requests/httpx` 调通 OpenAI 兼容接口（通义/DeepSeek）。
- 异步并发调多个模型。
- **里程碑**：写一个命令行聊天脚本（支持多轮 + 流式输出）。

### 阶段 2：LLM 原理与 Prompt 工程（2~3 周）
- Transformer/Attention/Tokenization 概念。
- Prompt 结构、Few-shot、CoT、JSON 输出约束。
- 实测：温度、上下文截断、幻觉案例。
- **里程碑**：做一个结构化信息抽取器 / 文本总结器（输出 JSON）。

### 阶段 3：RAG 与记忆（2~3 周）
- 文档加载 → 切分 → Embedding → 向量检索 → 生成。
- 用 Chroma/FAISS 本地跑通；理解混合检索与重排。
- **里程碑**：做一个"基于本地文档的知识库问答机器人"。

### 阶段 4：Agent 原理与 LangChain（3~4 周）
- ReAct 与 Tool Calling 概念，手写极简 Agent 循环（50 行）。
- LangChain：Model I/O、LCEL、Tool、AgentExecutor、Memory。
- **里程碑**：做一个会查天气/计算/搜索的多工具 Agent。

### 阶段 5：LangGraph 生产化（4~6 周，核心实战）
- StateGraph 状态机、节点/边/条件边、Agent 循环。
- ToolNode、Checkpointer 持久化、interrupt 人工介入、流式输出。
- **里程碑**：做一个带记忆、可人工审批、可断点续跑的业务 Agent（对齐本项目 LangGraph 实践）。

### 阶段 6：评测、部署与产品化（2~3 周）
- LangSmith/Langfuse 追踪 + 评测集。
- FastAPI 封装成服务 + Streamlit/Gradio 做界面 + Docker 部署。
- 安全：注入防护、权限、限流。
- **里程碑**：把阶段 5 的 Agent 部署上线，附评测报告。

### 阶段 7（可选进阶）：多智能体与前沿
- AutoGen/CrewAI 多 Agent、ToT/Reflexion、MCP（模型上下文协议）、Computer Use。
- **里程碑**：做一个多角色协作 Agent 并对比单 Agent 的收益。

**总周期参考**：每天 2~3 小时，约 4~6 个月到达"能独立交付生产级 Agent"。

---

## 九、学习资源推荐

### 官方文档（首选）
- LangChain 官方 Docs & Cookbook
- LangGraph 官方 Docs（StateGraph 教程、Agent 示例）
- OpenAI Function Calling / Assistants 文档
- Anthropic Claude Docs（本环境已用 anthropic SDK）
- 通义千问 DashScope 文档 / DeepSeek 文档

### 经典论文（按需精读）
- 《Attention Is All You Need》（Transformer）
- 《ReAct: Synergizing Reasoning and Acting in Language Models》
- 《Tree of Thoughts》《Reflexion》《Plan-and-Solve》
- 《Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks》（RAG 原始论文）
- 《Lost in the Middle》（长上下文问题）

### 教程与书籍
- 《Building LLM-Powered Applications》（LangChain 作者著）
- 《Designing Machine Learning Systems》（工程思维）
- DeepLearning.AI 的《Building Systems with the ChatGPT API》《LangChain for LLM Application Development》
- Hugging Face 官方 NLP 课程
- 中文本地化：知乎/掘金/CSDN 的 LangChain 实战系列（注意甄别版本过时）

### 开源项目（动手必刷）
- langchain-ai/langgraph（官方 examples）
- langchain-ai/langchain（源码阅读）
- run-llama/llama_index（RAG 参考）
- 本项目仓库（LangGraph Demo，作为实战样例持续迭代）

---

## 十、给学习者的 5 条建议

1. **先跑通再深究**：Agent 领域变化快，先让代码跑起来，再回头补原理，避免陷入理论漩涡。
2. **亲手写最小循环**：不要只会调框架——手写 50 行 ReAct 循环，理解就到位了。
3. **以项目驱动**：每阶段一个里程碑项目，作品比证书更有说服力。
4. **关注 Token 成本与可靠性**：生产级 Agent 拼的是稳定、省钱、可观测，不是炫技。
5. **保持跟进**：跟随 OpenAI/Anthropic/DeepSeek 官方博客 + LangChain 更新日志，社区周刊（如 The Rundown、AGI 相关 Newsletter）可作为日常输入。

---

*本文档由调研子任务产出，作为《AI Agent 从零到实战学习路径》的技能底稿，可与路径规划文档配套使用。*
