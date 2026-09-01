# Agent（AI 智能体）介绍文档

> **文档定位**：面向开发者的 Agent 入门介绍文档
> **目标读者**：具备基础编程经验、希望快速理解并上手 Agent 的开发者
> **覆盖范围**：概念定义、架构组成、核心功能、使用方式四大章节

---

## 目录

- [第一章：概念定义](#第一章概念定义)
  - [1.1 什么是 Agent（AI 智能体）](#11-什么是-agentai-智能体)
  - [1.2 关键术语辨析](#12-关键术语辨析)
  - [1.3 为什么需要 Agent](#13-为什么需要-agent)
  - [1.4 Agent 的分类](#14-agent-的分类)
- [第二章：架构组成](#第二章架构组成)
  - [2.1 核心组件（五要素）](#21-核心组件五要素)
  - [2.2 参考架构模式](#22-参考架构模式)
  - [2.3 数据流与调用链路](#23-数据流与调用链路)
  - [2.4 架构图示](#24-架构图示)
- [第三章：核心功能](#第三章核心功能)
  - [3.1 自主任务规划与分解](#31-自主任务规划与分解)
  - [3.2 工具调用与外部系统集成](#32-工具调用与外部系统集成)
  - [3.3 记忆管理与上下文持久化](#33-记忆管理与上下文持久化)
  - [3.4 自我反思与纠错](#34-自我反思与纠错self-correction)
  - [3.5 多智能体协作](#35-多智能体协作multi-agent)
  - [3.6 安全与合规能力](#36-安全与合规能力)
- [第四章：使用方式](#第四章使用方式)
  - [4.1 开发框架选型](#41-开发框架选型)
  - [4.2 接入模型与运行环境](#42-接入模型与运行环境)
  - [4.3 快速上手指南](#43-快速上手指南)
  - [4.4 典型应用场景](#44-典型应用场景)
  - [4.5 最佳实践与避坑指南](#45-最佳实践与避坑指南)
- [附录 A：术语表](#附录-a术语表)
- [附录 B：参考资料](#附录-b参考资料)

---

## 第一章：概念定义

### 1.1 什么是 Agent（AI 智能体）

**定义**：Agent（AI 智能体）是一类以 LLM（大语言模型）为"大脑"，通过**感知环境**、**自主规划**、**调用工具/执行动作**，并以**迭代循环**方式完成目标的软件系统。

**与对话机器人（Chatbot）的本质区别**：

| 维度 | Chatbot | Agent |
|---|---|---|
| 交互方式 | 单轮/多轮问答 | 持续感知-行动闭环 |
| 自主性 | 被动响应 | 主动规划与决策 |
| 行动能力 | 仅生成文本 | 可调用工具、执行动作 |
| 目标导向 | 弱 | 强（围绕目标迭代） |

**一句话总结**：

> **Agent = LLM（推理核心）+ 记忆（上下文持久化）+ 工具（行动手段）+ 环境（反馈回路）**

### 1.2 关键术语辨析

| 术语 | 说明 |
|---|---|
| **LLM（大语言模型）** | 纯文本生成/推理模型，是 Agent 的推理引擎（如 GPT-4、Claude、Llama） |
| **Agent（智能体）** | 具备规划、记忆、工具调用能力的自主系统 |
| **Workflow / Pipeline** | 预定义固定流程，无自主决策，区别于 Agent 的动态规划 |
| **RAG（检索增强生成）** | 通过检索外部知识增强回答，可视为 Agent 的"知识工具"之一 |
| **Tool / Function Calling** | 模型按结构化 schema 调用外部函数/API 的能力 |
| **CoT（思维链）** | Chain-of-Thought，让模型分步推理以提升复杂问题表现 |
| **ReAct** | Reasoning + Acting 交替循环的范式 |

### 1.3 为什么需要 Agent

LLM 本身存在四大局限：

1. **知识截止**：训练数据有截止时间，无法获知最新事件；
2. **无实时数据**：无法直接访问数据库、API 实时信息；
3. **无法执行操作**：只能生成文本，不能"动手做事"；
4. **幻觉风险**：在不确定领域可能编造事实。

Agent 通过以下方式系统性解决这些问题：

- **接入实时工具**：调用搜索、API、数据库获取最新信息；
- **外挂记忆**：向量数据库实现长期知识沉淀与个性化；
- **分步验证**：推理-行动-观察循环降低单步错误；
- **可解释轨迹**：完整记录每一步决策，便于回溯与审计。

### 1.4 Agent 的分类

**按形态**：

- **单智能体（Single-Agent）**：单一 Agent 独立完成所有规划与执行；
- **多智能体（Multi-Agent）**：多个角色化 Agent 协作（Planner / Executor / Critic）；
- **人机协同（Human-in-the-loop）**：关键节点保留人工确认。

**按决策方式**：

- **ReAct 式**：边推理边行动（Thought → Action → Observation 循环）；
- **Plan-and-Execute 式**：先整体规划，再按计划逐步执行，适合长任务；
- **反射式（Reflexion）**：执行后自我反思，改进下一轮策略。

---

## 第二章：架构组成

### 2.1 核心组件（五要素）

一个完整的 Agent 系统由以下五个核心组件构成：

#### 1. 模型（Model）

LLM 推理引擎，负责理解、推理与决策。常见选择：

- **云端 API**：OpenAI GPT-4/4o、Anthropic Claude、Google Gemini、各家国内大模型；
- **本地部署**：vLLM、Ollama、Llama.cpp。

#### 2. 记忆（Memory）

- **短期记忆（Short-term / Context）**：当前会话上下文，受模型 context window 限制；
- **长期记忆（Long-term）**：向量数据库（Chroma、Pinecone、Milvus）中的历史知识、用户画像、经验沉淀。

#### 3. 工具（Tools）

Agent 的"手脚"，包括：

- 函数调用（Function Calling）
- 外部 API（OpenAPI / REST）
- 代码解释器（Python sandbox）
- 浏览器/搜索
- 数据库读写
- 文件操作
- 图像/音视频生成

#### 4. 规划（Planning）

- **任务分解（Task Decomposition）**：将复杂目标拆为可执行子任务；
- **子目标编排**：处理依赖关系（DAG 或顺序）；
- **自我反思（Self-Reflection）**：评估执行结果并调整策略。

#### 5. 执行与环境（Execution & Environment）

- 执行动作并接收反馈（状态、结果、报错）；
- 形成"感知-决策-行动-观察"的闭环。

### 2.2 参考架构模式

#### 模式一：ReAct（Reasoning + Acting）

**最主流的范式**，推理与行动交替进行：

```
Thought: 我需要先查询天气
Action: search_weather("北京")
Observation: 晴，25°C
Thought: 温度适宜，可以建议外出
Action: respond("今天适合外出...")
```

**适用场景**：需要根据中间结果动态调整策略的复杂任务。

#### 模式二：Plan-and-Execute

**先规划后执行**，适合长任务：

1. **Planning 阶段**：LLM 一次性产出完整子任务列表（可包含依赖关系）；
2. **Executing 阶段**：按计划逐项执行，每个子任务内部可用 ReAct；
3. **Replanning 阶段**：根据执行结果动态重排计划。

**优势**：宏观结构清晰、便于回溯、易于并行子任务。

#### 模式三：Reflexion（反射式）

在 ReAct 基础上加入**显式反思**：

- **Actor**：执行动作；
- **Evaluator**：评估结果是否成功；
- **Self-Reflection**：失败时生成反思文本，存入记忆，下一轮改进。

#### 模式四：Multi-Agent 协作

角色化分工，典型拓扑：

- **Orchestrator**：任务分发与汇总；
- **Planner**：拆解任务；
- **Executor**：执行具体动作；
- **Critic / Reviewer**：评审结果质量；
- **Memory Manager**：统一管理共享记忆。

### 2.3 数据流与调用链路

典型的 Agent 单轮调用时序：

```
┌─────────┐   1. 输入目标    ┌──────────┐
│   User  │ ───────────────► │ Planner  │
└─────────┘                  │  (LLM)   │
                             └────┬─────┘
                                  │ 2. 子任务列表
                                  ▼
                             ┌──────────┐
                             │ Executor │ ◄──┐
                             └────┬─────┘    │
                                  │ 3. 调用工具│
                                  ▼           │
                             ┌──────────┐    │
                             │  Tools   │    │
                             └────┬─────┘    │
                                  │ 4. 结果   │
                                  ▼           │
                             ┌──────────┐    │
                             │Observer  │────┘ (循环)
                             └────┬─────┘
                                  │ 5. 决策
                                  ▼
                             ┌──────────┐
                             │ Finalize │
                             └──────────┘
```

**关键步骤**：

1. 用户输入目标 → 写入短期记忆；
2. 规划器（LLM）将目标拆分为子任务；
3. 对每个子任务：**检索长期记忆 → 决定调用哪个工具 → 执行工具 → 观察结果 → 继续推理**；
4. 循环直至任务完成 → 汇总输出 → （可选）将关键结果写入长期记忆。

### 2.4 架构图示

> 注：以下为文字版结构示意，正式文档中可替换为 SVG/PlantUML 渲染图。

**图 1：Agent 五要素关系图**

```
              ┌──────────────┐
              │   Model      │
              │   (LLM)      │
              └──────┬───────┘
                     │
       ┌─────────────┼─────────────┐
       │             │             │
       ▼             ▼             ▼
  ┌────────┐   ┌──────────┐   ┌────────┐
  │ Memory │   │ Planning │   │ Tools  │
  │(短期/  │   │ (规划器) │   │(工具集)│
  │ 长期)  │   └────┬─────┘   └────┬───┘
  └────────┘        │             │
                    └──────┬──────┘
                           ▼
                  ┌────────────────┐
                  │   Execution    │
                  │   & Feedback   │
                  │   (执行/环境)  │
                  └────────────────┘
```

**图 2：ReAct 循环流程图**

```
   ┌───────────┐
   │  User Goal│
   └─────┬─────┘
         ▼
   ┌───────────┐
   │  Thought  │◄────────┐
   │  (推理)   │         │
   └─────┬─────┘         │
         ▼               │
   ┌───────────┐         │
   │  Action   │         │
   │  (行动)   │         │
   └─────┬─────┘         │
         ▼               │
   ┌───────────┐         │
   │Observation│─────────┘
   │  (观察)   │
   └─────┬─────┘
         ▼
     [Done?]
      yes → Final Answer
      no  → back to Thought
```

**图 3：多智能体协作拓扑图**

```
              ┌──────────────┐
              │ Orchestrator │
              └──────┬───────┘
        ┌────────────┼────────────┐
        ▼            ▼            ▼
   ┌────────┐   ┌────────┐   ┌────────┐
   │ Planner│   │Executor│   │ Critic │
   └────┬───┘   └───┬────┘   └───┬────┘
        │           │            │
        └────────────┴────────────┘
              Shared Memory
```

---

## 第三章：核心功能

### 3.1 自主任务规划与分解

**能力描述**：Agent 接收高层目标后，自主拆解为可执行子任务，并按依赖关系编排执行顺序。

**实现机制**：

- **Chain-of-Thought (CoT)**：让 LLM 在生成最终答案前先"逐步思考"；
- **Task Decomposition**：通过提示词或专用 Planner 模型将目标拆解为 DAG；
- **Subgoal Ordering**：自动识别子任务的前后依赖与并行机会。

**示例提示词片段**：

```text
你是一个任务规划助手。请将用户目标拆解为有序的子任务列表，
每个子任务包含：id、description、dependencies、tools_needed。
返回 JSON 格式。
```

**代码示例：使用 OpenAI Function Calling 拆解任务**

```python
from openai import OpenAI
import json

client = OpenAI()

PLANNING_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_plan",
        "description": "提交任务分解计划",
        "parameters": {
            "type": "object",
            "properties": {
                "subtasks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "description": {"type": "string"},
                            "depends_on": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["id", "description"],
                    },
                },
            },
            "required": ["subtasks"],
        },
    },
}

def plan(goal: str) -> list[dict]:
    resp = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "你是任务规划助手。"},
            {"role": "user", "content": f"目标：{goal}\n请拆解为子任务。"},
        ],
        tools=[PLANNING_TOOL],
        tool_choice={"type": "function", "function": {"name": "submit_plan"}},
    )
    args = resp.choices[0].message.tool_calls[0].function.arguments
    return json.loads(args)["subtasks"]

# 使用
plan("为新产品发布准备一份市场分析报告")
```

### 3.2 工具调用与外部系统集成

**能力描述**：Agent 通过结构化 schema 调用外部函数/API，扩展 LLM 的行动能力。

**支持的工具类型**：

| 工具类型 | 典型实现 | 用途 |
|---|---|---|
| 函数调用 | Function Calling | 任何自定义逻辑 |
| Web API | OpenAPI/REST | 第三方服务集成 |
| 代码执行 | Python sandbox | 数值计算、数据处理 |
| 网页浏览 | Playwright/Selenium | 信息抓取 |
| 数据库 | SQL/NoSQL Client | 业务数据查询 |
| 文件 I/O | 读写本地/云端文件 | 文档处理 |
| 多模态生成 | DALL·E / TTS / 视频 | 内容创作 |

**工具注册示例（OpenAI 风格）**：

```python
import requests

WEATHER_TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "获取指定城市的实时天气",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "城市名称，如 '北京'"},
                "unit": {"type": "enum", "enum": ["celsius", "fahrenheit"]},
            },
            "required": ["city"],
        },
    },
}

def get_weather(city: str, unit: str = "celsius") -> dict:
    """真实实现：调用天气 API。"""
    # 实际项目中替换为真实 endpoint
    r = requests.get(f"https://api.weather.example/v1/current?city={city}")
    data = r.json()
    return {"city": city, "temp": data["temp"], "unit": unit, "condition": data["condition"]}

# 注册到工具表
TOOL_REGISTRY = {
    "get_weather": get_weather,
}
```

**关键最佳实践**：

- 工具描述（description）务必清晰具体——这是 LLM 决定何时调用的依据；
- schema 严格校验，避免模型输出非法参数；
- 工具执行必须有超时、错误处理与降级方案。

### 3.3 记忆管理与上下文持久化

**能力描述**：Agent 通过多层记忆系统突破 LLM 的上下文窗口限制，并实现个性化与知识沉淀。

**记忆分层**：

| 层级 | 存储位置 | 生命周期 | 用途 |
|---|---|---|---|
| 短期记忆 | Context Window | 单次会话 | 当前对话上下文 |
| 工作记忆 | 内存数据结构 | 单次任务 | 中间变量、待办 |
| 长期记忆 | 向量数据库 | 跨会话 | 用户偏好、经验、事实 |
| 共享记忆 | 外部存储 | 跨 Agent | 多 Agent 协作 |

**实现模式**：

```python
from typing import List
from datetime import datetime

class Memory:
    """简化版 Agent 记忆系统"""

    def __init__(self, vector_store=None):
        self.short_term: List[dict] = []           # 短期
        self.long_term = vector_store or []        # 长期（实际用向量 DB）

    def add_message(self, role: str, content: str):
        self.short_term.append({
            "role": role,
            "content": content,
            "ts": datetime.utcnow().isoformat(),
        })
        # 触发压缩：超过阈值时摘要
        if self._token_count() > 6000:
            self._compress()

    def recall(self, query: str, k: int = 3) -> List[dict]:
        """从长期记忆检索相关历史。"""
        # 实际：embedding + 向量相似度搜索
        return self.long_term[:k]

    def _token_count(self) -> int:
        return sum(len(m["content"]) for m in self.short_term)

    def _compress(self):
        """摘要压缩：把前 N 轮对话浓缩为一段。"""
        # 实际可调用 LLM 做摘要
        summary = "[摘要] " + self.short_term[0]["content"][:200]
        self.short_term = [{"role": "system", "content": summary}] + self.short_term[-5:]

    def persist(self, key: str, value: str):
        """将重要信息写入长期记忆。"""
        self.long_term.append({"key": key, "value": value, "ts": datetime.utcnow()})
```

**关键技巧**：

- **摘要压缩**：超出 context window 时用 LLM 摘要历史；
- **检索式记忆**：每次只把与当前任务相关的 top-k 条目塞入 context；
- **记忆分层**：用户偏好、事实知识与临时对话分离存储。

### 3.4 自我反思与纠错（Self-Correction）

**能力描述**：Agent 在执行过程中校验结果、捕获错误、自我反思并改进下一轮策略。

**实现机制**：

1. **结果校验（Verification）**：执行后调用 Critic/Verifier 评估输出质量；
2. **错误捕获与重试（Retry with Backoff）**：网络错误、API 限流自动重试；
3. **Reflexion 反思**：失败时让 LLM 生成自然语言反思，存入记忆；
4. **置信度判断**：对关键决策输出置信度，低置信度触发人工确认。

**Reflexion 实现示意**：

```python
def execute_with_reflexion(agent, task, max_trials=3):
    """带反思的执行循环。"""
    memory = []
    for trial in range(max_trials):
        # 1. 执行
        action = agent.decide(task, memory)
        result = agent.act(action)
        memory.append({"trial": trial, "action": action, "result": result})

        # 2. 评估
        success, feedback = agent.evaluate(result)

        if success:
            return result

        # 3. 反思
        reflection = agent.reflect(task, result, feedback)
        memory.append({"trial": trial, "reflection": reflection})

    raise RuntimeError(f"任务在 {max_trials} 次尝试后失败：{memory}")
```

**Human-in-the-Loop 闸门**：

```python
CONFIDENT_ACTIONS = {"send_email", "delete_file", "make_payment"}

def guarded_execute(agent, action):
    if action["tool"] in CONFIDENT_ACTIONS:
        # 关键动作需要人工确认
        if not human_confirm(action):
            return {"status": "cancelled", "reason": "user denied"}
    return agent.act(action)
```

### 3.5 多智能体协作（Multi-Agent）

**能力描述**：将复杂任务分配给多个角色化 Agent 协作完成，通过分工提升质量与可解释性。

**典型角色**：

| 角色 | 职责 |
|---|---|
| **Orchestrator** | 任务分发、结果汇总、流程控制 |
| **Planner** | 任务分解、计划生成 |
| **Executor** | 执行具体工具调用 |
| **Critic / Reviewer** | 评估结果质量、给出修改意见 |
| **Researcher** | 专门负责信息检索 |
| **Coder** | 专门负责代码生成与测试 |

**CrewAI 风格的多 Agent 示例**：

```python
from crewai import Agent, Task, Crew

researcher = Agent(
    role="Researcher",
    goal="收集某主题的最新行业数据",
    backstory="你是一位资深行业研究员",
    tools=[search_tool, fetch_webpage_tool],
)

writer = Agent(
    role="Writer",
    goal="基于研究数据撰写深度报告",
    backstory="你是一位专业财经作家",
    tools=[],
)

reviewer = Agent(
    role="Reviewer",
    goal="审查报告的事实准确性与结构",
    backstory="你是一位严谨的编辑",
    tools=[],
)

task_research = Task(description="研究 2024 年 AI Agent 行业趋势", agent=researcher)
task_write    = Task(description="撰写 3000 字行业报告",       agent=writer)
task_review   = Task(description="审校并提出修改意见",          agent=reviewer)

crew = Crew(agents=[researcher, writer, reviewer], tasks=[task_research, task_write, task_review])
result = crew.kickoff()
```

**消息传递与共识机制**：

- **黑板模型（Blackboard）**：所有 Agent 共享一个结构化"黑板"，读写中间结果；
- **消息总线（Message Bus）**：通过发布/订阅模式传递消息；
- **投票/评审**：多个 Executor 并行执行，Reviewer 综合评审。

### 3.6 安全与合规能力

**能力描述**：在生产环境中，Agent 系统必须具备完善的安全防护与合规能力。

**关键能力清单**：

| 能力 | 实现方式 |
|---|---|
| **权限控制** | RBAC / ABAC，最小权限原则 |
| **敏感信息过滤** | 工具输入/输出脱敏（邮箱、手机号、Token） |
| **工具白名单** | 仅允许调用预注册的工具，禁止任意函数执行 |
| **Prompt Injection 防护** | 系统提示词与用户输入隔离、输入清洗 |
| **可观测性** | 完整轨迹日志（每一步 Thought/Action/Observation） |
| **审计与回放** | 支持按 session 回放完整执行过程 |
| **成本控制** | 单次任务 max tokens / max iterations 硬上限 |
| **超时熔断** | 工具调用超时自动熔断，避免无限等待 |

**最小安全模板**：

```python
import re

SENSITIVE_PATTERNS = [
    re.compile(r"\b\d{16}\b"),              # 信用卡号
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),  # 邮箱
    re.compile(r"\b\d{11}\b"),              # 手机号
]

def redact(text: str) -> str:
    for p in SENSITIVE_PATTERNS:
        text = p.sub("[REDACTED]", text)
    return text

# 在写入日志/外部调用前脱敏
safe_input = redact(user_input)
safe_output = redact(tool_output)
```

---

## 第四章：使用方式

### 4.1 开发框架选型

| 框架 | 语言 | 特点 | 适用场景 |
|---|---|---|---|
| **LangChain / LangGraph** | Python/JS | 生态成熟、组件丰富、图式编排 | 通用生产级应用 |
| **OpenAI Assistants API** | REST | 官方托管、内置工具与检索 | 快速原型 |
| **AutoGen（Microsoft）** | Python | 多智能体对话编排 | 研究/多智能体协作 |
| **CrewAI** | Python | 角色化 Crew 编排、直观 | 轻量多角色任务 |
| **LlamaIndex** | Python | 数据/知识索引强 | RAG 型 Agent |
| **开源自研（提示词 + 函数调用）** | 任意 | 轻量、可控 | 学习理解原理 |

**选型建议**：

- **学习原理**：从零自研最小循环（≈ 100 行代码）；
- **快速原型**：OpenAI Assistants API 或 LangChain；
- **复杂生产应用**：LangGraph（可控的图式编排）；
- **多智能体研究**：AutoGen 或 CrewAI。

### 4.2 接入模型与运行环境

**部署方式对比**：

| 方式 | 代表 | 优点 | 缺点 |
|---|---|---|---|
| 云端 API | OpenAI、Anthropic、国产大模型 | 性能强、按量付费、无运维 | 数据外发、成本不可控 |
| 本地部署 | vLLM、Ollama、Llama.cpp | 数据私有、可定制 | 需 GPU、运维成本高 |
| 混合 | 关键任务本地 + 通用任务云端 | 平衡成本与隐私 | 架构复杂 |

**关键配置项**：

```python
AGENT_CONFIG = {
    "model": "gpt-4o",
    "temperature": 0.2,            # 低温度 → 更确定性的规划
    "max_tokens": 4096,
    "timeout": 30,                 # 单次工具调用超时（秒）
    "max_iterations": 15,          # 单任务最大循环次数
    "max_cost_usd": 0.5,           # 单任务成本上限
    "retry": {"attempts": 3, "backoff": "exponential"},
    "tools_whitelist": ["get_weather", "search_web", "query_db"],
}
```

### 4.3 快速上手指南

下面给出一个**最小可运行**的 Agent 示例（OpenAI Function Calling + Python），覆盖 4.3 节中描述的标准 6 步流程。

#### 完整示例：天气查询 Agent

```python
"""
最小 Agent 示例：用户问"北京今天天气如何？"
Agent 自动决定调用 get_weather 工具并返回自然语言答案。
"""
import os
import json
from openai import OpenAI

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# === Step 1: 定义系统提示词（角色 + 目标 + 约束）===
SYSTEM_PROMPT = """你是一个乐于助人的天气助手。
- 必须通过 get_weather 工具获取实时数据
- 基于工具返回结果用中文回答
- 不要编造数据"""

# === Step 2: 声明工具列表（schema）===
TOOLS = [{
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "获取指定城市的实时天气",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "城市名，如 '北京'"},
            },
            "required": ["city"],
        },
    },
}]

# === Step 3: 实现工具执行函数 ===
def get_weather(city: str) -> str:
    """实际项目：调用真实天气 API。"""
    # 模拟实现
    return json.dumps({"city": city, "temp": "25°C", "condition": "晴", "humidity": "40%"}, ensure_ascii=False)

TOOL_FUNCS = {"get_weather": get_weather}

# === Step 4: 主循环（组装上下文 → 调 LLM → 解析 → 执行 → 回填）===
def run_agent(user_query: str, max_iter: int = 5) -> str:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_query},
    ]

    for i in range(max_iter):
        print(f"\n--- Iteration {i+1} ---")
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            tools=TOOLS,
        )
        msg = resp.choices[0].message
        messages.append(msg)  # 加入上下文

        # 终止条件：模型给出最终答案
        if not msg.tool_calls:
            return msg.content

        # 执行工具调用
        for tool_call in msg.tool_calls:
            func_name = tool_call.function.name
            args = json.loads(tool_call.function.arguments)
            print(f"[Tool Call] {func_name}({args})")

            result = TOOL_FUNCS[func_name](**args)
            print(f"[Tool Result] {result}")

            # 回填 observation
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result,
            })

    return "达到最大迭代次数，任务未完成。"

# === Step 5-6: 测试 ===
if __name__ == "__main__":
    answer = run_agent("北京今天天气怎么样？")
    print(f"\n[Final Answer] {answer}")
```

**运行输出示例**：

```
--- Iteration 1 ---
[Tool Call] get_weather({'city': '北京'})
[Tool Result] {"city": "北京", "temp": "25°C", "condition": "晴", "humidity": "40%"}

[Final Answer] 北京今天天气晴，气温 25°C，湿度 40%，非常适合户外活动！
```

**最小 Agent 的 6 步标准流程总结**：

1. **定义系统提示词**：角色 + 目标 + 约束；
2. **声明工具列表**：名称、描述、入参 schema；
3. **实现工具函数**：带错误处理与超时；
4. **编写主循环**：组装上下文 → 调 LLM → 解析工具调用 → 执行 → 回填 observation；
5. **加入记忆与日志**：短期/长期记忆、轨迹日志；
6. **测试 → 评估 → 迭代**：成功率、延迟、成本三项核心指标。

### 4.4 典型应用场景

| 场景 | 典型任务流 | 价值 |
|---|---|---|
| **智能客服/个人助理** | 理解意图 → 查询订单/日程 → 调用 API → 生成回复 | 7×24 自动化服务 |
| **数据分析与报表** | 理解业务问题 → 查数据库 → 写 Python 分析 → 出图表 → 写结论 | 零代码数据分析 |
| **软件开发助手** | 阅读 issue → 定位代码 → 修改 → 跑测试 → 提 PR | 研发提效 |
| **自动化运维 (AIOps)** | 监控告警 → 诊断根因 → 执行修复脚本 → 验证恢复 | 减少人工介入 |
| **研究助手** | 文献检索 → 阅读理解 → 综述 → 生成报告 | 知识工作自动化 |
| **销售/营销自动化** | 抓取线索 → 调研公司 → 生成个性化邮件 → 跟进 | 规模化触达 |
| **教育辅导** | 答疑 → 出题 → 批改 → 个性化推荐 | 因材施教 |

### 4.5 最佳实践与避坑指南

#### ✅ 最佳实践

1. **工具描述要"足够好"**：`description` 是 LLM 决定何时调用的唯一依据，要清晰、具体、给出示例；
2. **Schema 严格校验**：用 Pydantic / Zod 在执行前校验所有入参，错误时让模型重试；
3. **关键动作加人工确认闸门**：支付、删除、发送等操作必须 Human-in-the-loop；
4. **限制最大迭代次数与成本**：单任务 `max_iterations` 与 `max_cost_usd` 硬上限；
5. **记录完整轨迹**：每一步 Thought / Action / Observation 落库，便于复盘与 A/B；
6. **采用结构化输出**：优先用 Function Calling / JSON Mode，减少解析错误；
7. **分层记忆**：短期、长期、共享记忆分离，按需检索；
8. **可观测性先行**：从 Day 1 接入日志/Trace 系统（LangSmith、Phoenix、Langfuse）。

#### ⚠️ 常见坑

| 坑 | 现象 | 解法 |
|---|---|---|
| 工具描述模糊 | 模型乱调工具 | 重写 description，给出 positive/negative examples |
| 缺乏错误处理 | 一次报错整个任务挂掉 | 每个工具 try/except，返回可恢复的 error message |
| Context 过长 | 越往后越"健忘" | 摘要压缩 + 检索式记忆 |
| 无成本控制 | 单次任务烧掉几美元 | 硬性 max_tokens + max_iterations |
| Prompt Injection | 用户注入恶意指令覆盖 system prompt | 系统/用户内容强隔离、输入清洗 |
| 缺少评估 | 改 prompt 全凭感觉 | 准备测试集，自动跑成功率/延迟/成本 |
| 工具过度 | 一次塞 50 个工具 | 按场景分组，动态注入当前任务相关工具 |
| 无回退方案 | 工具失败直接挂 | 设计 fallback 工具或人工兜底 |

#### 评估指标建议

```python
EVAL_METRICS = {
    "task_success_rate": 0.85,        # 任务完成率（人工标注）
    "avg_iterations": 4.2,            # 平均迭代次数
    "avg_latency_sec": 8.5,           # 平均端到端延迟
    "avg_cost_usd": 0.08,             # 平均单任务成本
    "tool_call_accuracy": 0.92,       # 工具调用准确率
    "human_intervention_rate": 0.10,  # 人工介入比例
}
```

---

## 附录 A：术语表

| 术语 | 全称 | 解释 |
|---|---|---|
| **Agent** | AI Agent | 具备自主规划、记忆、工具调用能力的 AI 系统 |
| **LLM** | Large Language Model | 大语言模型，Agent 的推理核心 |
| **CoT** | Chain-of-Thought | 思维链，让 LLM 分步推理 |
| **ReAct** | Reasoning + Acting | 推理-行动交替循环范式 |
| **RAG** | Retrieval-Augmented Generation | 检索增强生成 |
| **Tool / Function Calling** | — | LLM 调用结构化外部函数的能力 |
| **Memory** | — | Agent 的记忆系统（短期/长期/共享） |
| **Vector DB** | Vector Database | 向量数据库，用于长期记忆 |
| **Reflexion** | — | 执行后自我反思、改进下一轮的机制 |
| **Plan-and-Execute** | — | 先规划后执行的范式 |
| **Multi-Agent** | Multi-Agent System | 多个角色化 Agent 协作 |
| **Human-in-the-loop** | HITL | 关键节点保留人工确认 |
| **Orchestrator** | — | 多 Agent 系统的任务分发与汇总角色 |
| **Critic** | — | 评估其他 Agent 输出质量的角色 |
| **Prompt Injection** | — | 通过用户输入覆盖系统指令的攻击 |

---

## 附录 B：参考资料

### 经典论文

- **ReAct**: Yao et al., *ReAct: Synergizing Reasoning and Acting in Language Models*, ICLR 2023.
- **Reflexion**: Shinn et al., *Reflexion: Language Agents with Verbal Reinforcement Learning*, NeurIPS 2023.
- **Chain-of-Thought**: Wei et al., *Chain-of-Thought Prompting Elicits Reasoning in Large Language Models*, NeurIPS 2022.
- **AutoGen**: Wu et al., *AutoGen: Enabling Next-Gen LLM Applications via Multi-Agent Conversation*, 2023.
- **Toolformer**: Schick et al., *Toolformer: Language Models Can Teach Themselves to Use Tools*, NeurIPS 2023.

### 官方文档

- [OpenAI Function Calling & Assistants API](https://platform.openai.com/docs)
- [LangChain & LangGraph 官方文档](https://python.langchain.com/)
- [AutoGen (Microsoft) 文档](https://microsoft.github.io/autogen/)
- [CrewAI 文档](https://docs.crewai.com/)
- [LlamaIndex 文档](https://docs.llamaindex.ai/)

### 推荐学习资源

- Lilian Weng《LLM Powered Autonomous Agents》—— Agent 领域经典综述
- LangChain 官方教程《Build an Agent》
- Chip Huyen《Building LLM-based Applications》—— 工程化视角
- 各家大模型厂商的 Cookbook（OpenAI Cookbook、Anthropic Cookbook 等）

---

## 文档元信息

- **版本**：v1.0
- **章节结构**：4 主章 + 2 附录
- **代码示例**：Python（OpenAI SDK 风格），可直接运行
- **面向受众**：有基础编程经验、想快速理解并上手 Agent 的开发者

> **下一步学习建议**：
> 1. 运行 4.3 节的最小示例，亲手体验一个 Agent 完整生命周期；
> 2. 选择 LangChain 或 LangGraph，尝试构建多工具 Agent；
> 3. 阅读 AutoGen/CrewAI 源码，理解多 Agent 协作；
> 4. 在真实业务场景中评估成功率、延迟、成本三项核心指标并持续迭代。
