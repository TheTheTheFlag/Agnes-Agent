# Agent（智能体）调研资料整理：核心概念 · 主要类型 · 应用场景 · 代表性产品

> **子任务产出**：调研并整理 Agent（智能体）领域的资料，为《Agent（智能体）介绍文档》（`agent-intro.md`）提供素材支撑。
> **调研时间**：2026 年 8 月 ｜ **方法**：聚合权威来源（甲子光年、IBM、Authing、MIT AI Agent Index、SSD Nodes、CSDN/知乎综述等）+ 项目背景（Agnes-Agent / LangGraph 分层记忆智能体）
> **用途**：本文是「资料汇编」，侧重素材与事实整理；正式叙述请见 `deliverables/agent-intro.md`。

---

## 一、核心概念（Core Concepts）

### 1.1 定义：学术视角 vs 产业视角

| 视角 | 定义 | 出处 |
|---|---|---|
| **广义（学术）** | Agent 是能够**感知环境（Perception）→ 做出决策（Decision）→ 执行行动（Action）**以实现特定目标的自主系统 | 斯坦福李飞飞团队《Agent AI: Surveying the Horizons of Multimodal Interaction》 |
| **LLM 语境（产业）** | 以**大语言模型（LLM）为大脑**，通过**工具调用（Tool Use）**与外部世界交互、自主完成复杂任务的系统，又称 LLM Agent / AI Agent | IBM《什么是 AI 智能体》、Authing、甲子光年 |
| **企业级（产业）** | 一种软件组件，具备**代表（代理）用户或系统执行任务的自主能力**；围绕"工作"展开，**工具调用是其最核心特征** | 麦肯锡 / 甲子光年《2025 企业级 AI Agent 价值及应用报告》 |

**厂商定义共识**（微软 / 谷歌 / IBM / Salesforce / 华为等表述各异但本质一致）：

> **Agent = LLM（大脑）+ 记忆（Memory）+ 规划（Planning）+ 工具（Tools）+ 行动（Action）**

### 1.2 核心组件（学术界模块划分）

- **复旦大学 NLP**：《The rise and potential of large language model based agents》→ **Brain / Perception / Action** 三大模块。
- **人大高瓴 AI**：《A survey on large language model based autonomous agents》→ **Profile / Memory / Planning / Action** 四大模块。
- **产业界归纳**：**感知 → 决策 → 规划 → 行动 → 记忆 → 反思** 的能力闭环。

### 1.3 与传统 AI / Chatbot / Copilot 的对比（演进阶梯）

```
Chatbot  →  Copilot  →  Agent
(人类主导)   (人机协作)   (AI 主导)
```

| 对比维度 | 传统 AI / Chatbot | AI Agent |
|---|---|---|
| 交互方式 | 单轮、响应式、"一问一答" | 多轮、任务导向、可长时间持续运行 |
| 行动能力 | 只输出内容、回答问题 | 主动调用工具、执行多步任务、改变外部世界 |
| 记忆 | 无或弱 | 短/长期记忆、状态保持、跨会话延续 |
| 规划 | 无，逐问逐答 | 自主分解任务、按依赖排序、动态调整 |
| 目标导向 | 被动响应请求 | 主动朝目标推进，失败可重试/反思 |
| 典型形态 | 客服机器人、OCR/识别系统 | 数字员工、自主研究助手、多智能体团队 |

> 一句话：**Chatbot 回答问题，Agent 解决问题。**
> 责任转移：从"AI 辅助人"（Chatbot）→"AI 与人协作"（Copilot）→"AI 替人完成整条任务链路"（Agent）。

### 1.4 关键特性（Key Characteristics）

1. **自主性（Autonomy）**：接收目标后自主拆解任务、选择路径并执行，低监管依赖。
2. **工具调用（Tool Use）**：调用 API、数据库、搜索引擎、代码执行器、甚至其他 Agent（MCP / Computer Use 等协议是当前热点）。
3. **记忆系统（Memory）**：工作记忆（对话上下文）+ 长期记忆（历史、偏好、画像），支撑连续性、个性化。
4. **规划能力（Planning）**：任务分解 + 动态调整 + 自我修正（ReAct、反思机制、子任务编排）。
5. **感知-行动闭环**：持续监测环境反馈，判断是否达成预期，形成循环。
6. **协作能力（Collaboration）**：多智能体协作、人机协作（Human-in-the-Loop）、工具间协同。
7. **适应与学习**：随交互积累优化策略，"越用越好用"（企业级报告结论）。

---

## 二、主要类型（Main Types / Taxonomy）

> Agent 分类存在多个维度，常见的有**认知架构**、**系统架构**、**产品形态**、**应用范围**、**自主程度**五种划分，实践中多为混合型。

### 2.1 按认知架构分类（经典 AI 教科书分类法）

源自标准 AI 教材，按"行动前掌握的信息量 / 能提前规划多远"划分：

| 类型 | 特点 | 典型例子 |
|---|---|---|
| **简单反射型（Simple Reflex）** | 当前输入 → 直接映射到操作，无记忆 | Webhook 触发固定流程、规则机器人 |
| **基于模型的反射型（Model-based Reflex）** | 保存环境状态，依据内部模型决策 | 带状态的工作流自动化 |
| **基于目标型（Goal-based）** | 围绕目标状态进行规划 | 任务规划 Agent、路径规划 |
| **基于效用型（Utility-based）** | 评估多个可接受结果，选择得分最高 | 推荐/调度类 Agent |
| **学习型（Learning）** | 根据反馈修改自身策略（权重/策略更新，区别于"带记忆"） | 自我进化的算法设计 Agent（如 AlphaEvolve） |

> ⚠️ 注意区分："能读取历史笔记的 Agent"是带记忆的基于模型型；真正"学习"需要评估组件 + 策略更新闭环，目前生产环境中很少。

### 2.2 按系统架构分类（单体 vs 群体）

| 架构 | 说明 | 代表 |
|---|---|---|
| **单智能体（Single-Agent）** | 一个 Agent 独立完成目标，ReAct 循环 + 工具调用 | ChatGPT Agent、Claude Code |
| **多智能体（Multi-Agent）** | 多个专业 Agent 协作，通过路由/消息机制分工，共享环境 | MetaGPT、ChatDev、AutoGen、CrewAI |
| **分层系统（Hierarchical）** | 监督者（Supervisor/Coordinator）在上，拆分任务、分发、合并结果 | LangGraph Supervisor、企业级 Agent 团队 |
| **混合型（Hybrid）** | 大多数真实系统是混合型：先单 Agent 循环，遇瓶颈再拆分多 Agent | 大多数生产系统 |

> 工程经验：**"一个真正能工作的智能体，胜过四个大多时候能工作的智能体"**——每次交接都可能丢失信息，先从一个循环开始，只有能明确指出瓶颈步骤时才拆分。

### 2.3 按产品形态分类（产业视角）

| 形态 | 说明 | 代表 |
|---|---|---|
| **通用智能体应用** | 开箱即用，面向终端用户，自然语言描述目标即可自动执行多步任务 | ChatGPT Agent、Manus、Deep Research、Kimi OK Computer |
| **垂直行业智能体** | 面向特定行业（医疗、金融、制造、法律等），依赖私有语料 + 领域插件 | 医疗临床决策支持、金融合规、法律助理等 |
| **智能体搭建平台 / 开发框架** | 面向开发者/企业，低代码/模块化构建 Agent（LLM 接入、工具集成、记忆、编排、插件市场） | Coze、Dify、LangGraph、AutoGen、CrewAI、百炼、元器 |

> 产业观察：通用型智能体核心是**工具生态**；垂直行业智能体核心是**私有语料 + 领域插件**。

### 2.4 按自主程度 / 能力阶梯分类

- **Chatbot**（对话能力 ★）：AI 提供信息与建议，人类完成绝大部分工作。
- **Copilot**（对话+推理 ★★）：人机协作，AI 出初稿，人类设定目标、修改确认。
- **Agent**（对话+推理+记忆+工具+规划+行动 ★★★）：AI 完成绝大部分工作，人类设定目标、提供资源、监督结果。

### 2.5 按运行载体分类（MIT AI Agent Index 的三分法）

- **Chat 类**（对话界面承载）：ChatGPT、Gemini、Claude、Manus、AutoGLM、Kimi OK Computer、MiniMax Agent 等。
- **Browser 类**（浏览器/电脑操作承载）：ChatGPT Atlas、Opera Neon、Perplexity Comet、Alibaba Mobile-Agent、ByteDance UI-TARS-desktop。
- **Enterprise 类**（企业系统承载）：Microsoft Copilot Agents、Salesforce Agentforce、ServiceNow AI Agents、SAP Joule Agents、IBM watsonx Orchestrate、Glean Agents、HubSpot Breeze Agents、n8n AI agent builder 等。

---

## 三、应用场景（Application Scenarios）

### 3.1 通用场景（横切行业）

| 场景 | 说明 | 典型示例 |
|---|---|---|
| **智能客服** | 多轮对话、自动查询、工单处理、售前咨询 | 客服自动响应、7×24 智能客服 |
| **代码生成与研发提效** | 代码编写、审查、调试、自动修复、自动化测试 | AI 编程助手（Claude Code、Codex） |
| **数据分析与洞察** | 查数、取数、报表生成、商业洞察 | 经营分析、市场调研（Deep Research） |
| **办公与流程自动化** | 邮件、日程、文档、跨系统流程 | 数字员工、会议纪要、日程排期 |
| **信息检索增强（RAG）** | 联网搜索、知识库问答、长文档理解 | 文档助手、企业知识库 |
| **IT 运维自动化** | 故障排查、自动化运维、告警处理 | IT 自动化（ServiceNow、IBM watsonx） |
| **多智能体协作** | 多角色分工协同完成复杂任务 | 多智能体客服、Agent 团队（MetaGPT/CrewAI） |

### 3.2 行业场景（MIT AI Agent Index 综述 + 案例）

| 行业 | 典型应用 | 说明 |
|---|---|---|
| **医疗** | ICU 临床决策支持、影像学诊断、疫情响应 | 诊断 Agent + 病史检索 Agent + 治疗规划 Agent，协调器整合、冲突交人工审查 |
| **金融** | 合规分析、风控、投研、反欺诈 | 企业级 Agent 处理合规、研究报告（如券商深度研究） |
| **制造/农业** | 机器人协作收割、路径规划、智能质检 | 测绘/采摘/运输多 Agent 分工，协调器按故障动态调整任务 |
| **科研/教育** | 文献综述、资助申请撰写、专利检索 | AutoGen/CrewAI 的检索器-摘要器-综合器-引用格式化器流水线 |
| **网络安全** | 威胁分类、合规分析、缓解规划 | 网络安全事件响应 Agent 团队 |
| **法律** | 条款审查、判例检索、文书起草 | 法律助理 Agent 提交前自校验 |
| **营销/销售** | 内容创作、客户运营、线索跟进 | HubSpot Breeze、Salesforce Agentforce |

---

## 四、代表性产品（Representative Products）

> 来源：MIT AI Agent Index（2025，收录 30 个 Agent）、公开资料。下表按「国际厂商 / 国内厂商 / 开源框架生态」分组。

### 4.1 国际厂商产品

| 厂商 | 产品 | 类型 | 简介 |
|---|---|---|---|
| **OpenAI** | ChatGPT Agent | Chat | 2025 年中上线"Agent 模式"，用虚拟电脑浏览网页、调用应用，端到端完成多步任务（查日历、规划晚餐、竞品分析出 PPT） |
| **OpenAI** | Operator | Browser | 通用计算机操作智能体，可代为操作网页/应用完成任务 |
| **OpenAI** | Deep Research | Chat | 类人类专家的多轮联网搜索、验证、深度研究，输出研究报告 |
| **OpenAI** | Codex / Agent Builder | Chat / Enterprise | 代码智能体；面向企业构建定制 Agent 的工具 |
| **Anthropic** | Claude / Claude Code | Chat | Claude 对话智能体；Claude Code 是终端内的编码智能体，广受开发者欢迎 |
| **Google** | Gemini / Gemini CLI / Gemini Enterprise | Chat / Enterprise | Gemini 的 Agent 模式：目标驱动自主规划执行；Gemini CLI 终端版；企业版 |
| **Microsoft** | Copilot Agents | Enterprise | 微软 365 生态的企业智能体，注入办公流程（邮件、会议、文档） |
| **Salesforce** | Agentforce | Enterprise | CRM 数字劳动力，处理客服、销售、营销自动化 |
| **IBM** | watsonx Orchestrate | Enterprise | 企业业务流程自动化平台，支持客服自动响应、文档助手 |
| **ServiceNow** | AI Agents | Enterprise | IT 运维与工作流自动化智能体 |
| **SAP** | Joule Agents | Enterprise | 企业资源管理场景的智能助手 |
| **HubSpot** | Breeze Agents | Enterprise | 营销/销售/客服场景的智能体 |
| **Glean** | Glean Agents | Enterprise | 企业知识搜索与工作助手 |
| **Perplexity** | Comet | Browser | 浏览器操作型研究智能体 |
| **n8n** | AI Agent Builder | Enterprise | 开源工作流自动化平台的 Agent 构建器 |
| **Opera** | Opera Neon | Browser | 浏览器厂商推出的 AI 浏览器智能体 |

### 4.2 国内厂商 / 产品

| 厂商 | 产品 | 类型 | 简介 |
|---|---|---|---|
| **蝴蝶效应** | Manus | Chat | 现象级通用 Agent，自主规划执行复杂任务并产出成果（报表/文档），2025 年完成 7500 万美元融资、估值约 20 亿美元 |
| **字节跳动** | Coze / 扣子空间 | 平台 | 零代码智能体搭建平台（国内称"扣子"），多模型接入、多插件、多渠道发布（企微/钉钉/飞书/微信）；"扣子空间"为通用任务空间 |
| **字节跳动** | UI-TARS-desktop | Browser | 电脑操作型智能体，可操作 GUI 完成多步任务 |
| **月之暗面** | Kimi OK Computer | Chat | Kimi 的电脑操作 Agent，可操作浏览器/应用完成任务 |
| **智谱 AI** | AutoGLM | Chat | 端侧/云侧手机电脑操作智能体，自动执行 App 内任务 |
| **阿里** | 百炼 / Mobile-Agent | 平台 / Browser | 百炼为智能体开发平台；Mobile-Agent 为手机操作智能体 |
| **腾讯** | 腾讯元器 | 平台 | 腾讯智能体开放平台，依托混元大模型，一键分发到微信/QQ |
| **MiniMax** | MiniMax Agent | Chat | 通用对话型智能体 |
| **DeepSeek / 通义等基座** | — | 基座 | 为国内 Agent 提供底层大模型能力 |

### 4.3 开源框架与开发生态（Agent 基础设施）

| 框架 | GitHub Stars（约） | 特点 | 适合谁 |
|---|---|---|---|
| **LangChain / LangGraph** | ~108k / 高 | Agent 组件全家桶；LangGraph 以图结构（节点/边/状态/条件边）编排复杂工作流与多智能体，支持 checkpoint、人机协作 | AI 工程师构建复杂 Agent（本项目 Agnes-Agent 即基于 LangGraph） |
| **AutoGen** | ~45k | 多 Agent 对话框架，可定制智能体、混合人机+工具交互 | 研究/实验、多 Agent 协作 |
| **CrewAI** | ~32k | 多智能体团队协作（角色扮演 + Flow 事件驱动） | 模拟智能体团队、复杂工作流自动化 |
| **MetaGPT / ChatDev** | — | 模拟 CEO/工程师/审查员角色的多 Agent 软件开发流水线 | 多 Agent 研究、软件协作开发 |
| **AutoGPT / BabyAGI** | — | 自主规划任务链的早期代表 | 自主 Agent 概念验证 |
| **Dify** | ~99k | 开源低代码 LLM 应用平台（RAG、Agent、工作流编排、模型管理），支持私有化部署 | 开发者快速搭垂直问答/知识检索助手 |
| **Coze（开源版）** | — | 零代码可视化平台 | 产品/运营快速验证 |
| **Semantic Kernel** | ~25k | 微软企业级 SDK，插件化技能、跨语言、可观测可审计 | 企业开发向传统应用注入 AI |
| **Letta (MemGPT)** | ~17k | 持久记忆 Agent 平台，长期记忆、透明追踪 | 长期助手、个性化助理 |
| **LangFlow** | ~64k | 可视化 LangChain 流水线编辑器 | 前后端协同、原型设计 |

> **生态趋势（MIT AI Agent Index 2025）**：20/30 个 Agent 支持 MCP（Model Context Protocol）做工具集成；多数产品级 Agent 为闭源；仅前沿实验室与中国开发者自研模型，其余依赖 GPT/Claude/Gemini 系基座 → 形成结构性依赖。

---

## 五、关键事实速记（供写作引用）

1. **Gartner 预测**：到 2028 年，至少 15% 的日常工作决策将通过 AI Agent 完成。
2. **演进结论**：2023—2024 是"大模型之年"，2025 年之后进入"智能体之年"；企业级 Agent 被视为生成式 AI 从试点走向规模化落地的核心载体。
3. **企业级 Agent 硬性标准**：高可靠性、高生产力、可扩展性、集成性、治理性、安全性与合规性。
4. **能力来源**：Agent 的规划与执行全自动能力建立在"工具调用"之上——不再局限于信息处理与对话，而是主动与数字/物理世界交互（预订、查数、控设备等多步骤复杂任务）。
5. **挑战清单**：可靠性（幻觉/静默失败）、安全与合规、成本（长上下文/多轮调用）、治理与可审计性、自主性边界（伦理）。

---

## 六、参考资料清单

1. 甲子光年《2025 企业级 AI Agent（智能体）价值及应用报告》
2. IBM《什么是 AI 智能体（AI agent）？》— https://www.ibm.com/cn-zh/think/topics/ai-agents
3. Authing《AI Agent 到底是什么？一文带你快速了解 AI Agent》— https://www.authing.cn/blog/1140
4. MIT《The 2025 AI Agent Index》— https://aiagentindex.mit.edu
5. SSD Nodes《AI Agent 有哪些类型？简单反射到多智能体》— https://www.ssdnodes.com/learn/lang/zh-hans/types-of-ai-agents-explained
6. 知乎综述《AI 智能体与智能体式 AI：概念分类法、应用与挑战》
7. CSDN《AI Agent 框架大盘点：Coze、Dify 到 LangChain》
8. 博客园叶小钗《2025AI 元年，常见智能体盘点》
9. LinkedIn《AI Agents in 2025: The New Digital Co-Pilots》
10. 复旦大学《The rise and potential of large language model based agents: a survey》
11. 中国人民大学《A survey on large language model based autonomous agents》
12. 斯坦福李飞飞团队《Agent AI: Surveying the Horizons of Multimodal Interaction》
13. 普林斯顿《AI Agents That Matter》
14. 项目 README（Agnes-Agent / LangGraph 分层记忆智能体）

---

*本文件为资料汇编，不构成最终交付文档；正式介绍文档见 `deliverables/agent-intro.md`。*
