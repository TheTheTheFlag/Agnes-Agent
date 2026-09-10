# DAG任务规划器主流实现技术栈调研报告

## 一、概述

DAG（有向无环图）任务规划器是现代AI Agent系统的核心组件，负责将复杂任务分解为可执行的子任务序列。本报告调研了2024-2025年的主流实现技术栈，涵盖Python框架、LLM集成方式和任务编排库。

---

## 二、核心Python框架对比

### 2.1 工作流编排框架

| 框架 | 核心特点 | 适用场景 | 学习曲线 |
|------|---------|---------|---------|
| **Prefect** | Pythonic API、动态DAG构建、状态管理 | ML流水线、快速原型开发 | ⭐⭐ 低 |
| **Temporal** | 耐久执行（durable execution）、自动状态恢复 | 长生命周期任务、容错要求高 | ⭐⭐⭐ 中 |
| **Apache Airflow** | 成熟稳定、丰富插件生态 | 企业级ETL、数据管道 | ⭐⭐⭐ 中 |
| **Kestra** | YAML驱动、可视化界面 | 低代码需求、可视化编排 | ⭐ 极低 |

**Prefect核心代码示例：**
```python
from prefect import flow, task
from typing import List

@task
def extract_data() -> dict:
    return {"source": "database", "records": 1000}

@task
def transform_data(data: dict) -> dict:
    return {"transformed": True, "count": data["records"] * 2}

@task
def load_data(data: dict) -> bool:
    print(f"Loading {data['count']} records")
    return True

@flow
def etl_pipeline():
    extracted = extract_data()
    transformed = transform_data(extracted)
    return load_data(transformed)
```

**Temporal核心特性：**
- Workflow代码即工作流，服务器崩溃后可恢复
- 内置事件历史（event history）维护状态
- 支持长时间运行的工作流（周/月级别）

---

### 2.2 LLM Agent框架

| 框架 | 核心架构 | LLM集成方式 | 特点 |
|------|---------|------------|------|
| **LangGraph** | 状态图（Stateful Graph） | 原生支持多LLMProvider | 显式编排、图理论、调试工具 |
| **AutoGen** | 多Agent对话 | 函数自动包装为Tool | 对话式编程、低代码Studio |
| **CrewAI** | 角色化Agent团队 | 声明式角色定义 | 易于上手、角色扮演 |
| **DSPy** | 声明式编译器 | 自改进Pipeline | 自动化Prompt优化 |
| **OpenAI Agents SDK** | Agent+Handoff模式 | 100+ LLM兼容 | 生产级、Guardrails |
| **Microsoft Agent Framework** | 模块化Agent | 微软生态集成 | 企业级、On-premise支持 |

**LangGraph核心概念：**
```python
from langgraph.graph import StateGraph, START, END

# 定义状态
class AgentState(TypedDict):
    messages: Annotated[list, operator.add]
    tools_used: list

# 构建图
graph = StateGraph(AgentState)
graph.add_node("planner", plan_task)
graph.add_node("executor", execute_task)
graph.add_edge(START, "planner")
graph.add_edge("planner", "executor")
graph.add_edge("executor", END)
```

**DSPy独特优势：**
- 将LLM调用视为编程而非手动Prompt工程
- 自动优化器（Optimizers）改进Pipeline
- 支持LM适配器（Adapter）桥接不同模型

---

## 三、LLM集成方式

### 3.1 模型适配层

主流框架均采用统一的LLM接口抽象：

```python
# LangChain式统一接口
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic

llm = ChatOpenAI(model="gpt-4o")
# 或
llm = ChatAnthropic(model="claude-sonnet-4-20250514")
```

### 3.2 Tool集成模式

| 模式 | 实现方式 | 代表框架 |
|------|---------|---------|
| **函数自动包装** | Python函数直接转为Tool Schema | AutoGen、CrewAI |
| **显式Tool定义** | 通过Schema声明Tool参数 | LangGraph、OpenAI SDK |
| **协议标准化** | MCP（Model Context Protocol） | Claude、多种新框架 |

**MCP协议示例（2025新兴标准）：**
```json
{
  "mcpServers": {
    "playwright": {
      "command": "npx",
      "args": ["@playwright/mcp@latest"]
    }
  }
}
```

### 3.3 多LLM协作架构

- **Planner-Executor分离**：规划Agent负责分解任务，执行Agent负责具体操作
- **MetaGPT模式**：元编程式多Agent协作，每个角色有独立职责
- **GPTSwarm**：可优化的Agent图结构，类似ANN的梯度下降优化

---

## 四、任务编排库选型建议

### 4.1 按场景选择

| 场景需求 | 推荐框架 | 理由 |
|---------|---------|------|
| **快速原型/ML流水线** | Prefect | 零学习曲线、Jupyter友好 |
| **生产级容错任务** | Temporal | 耐久执行、自动恢复 |
| **复杂多Agent系统** | LangGraph | 显式状态管理、调试工具 |
| **低代码可视化** | AutoGen Studio / Kestra | 拖拽式编排 |
| **Prompt优化型应用** | DSPy | 自动化Prompt工程 |
| **企业级Agent应用** | OpenAI Agents SDK | 生产就绪、Guardrails |

### 4.2 混合架构趋势

2025年的趋势是**分层架构**：
```
┌─────────────────────────────────────┐
│   应用层：业务逻辑                    │
├─────────────────────────────────────┤
│   Agent框架：LangGraph/AutoGen        │
├─────────────────────────────────────┤
│   编排层：Prefect/Temporal           │
├─────────────────────────────────────┤
│   LLM适配层：LangChain/Litellm       │
└─────────────────────────────────────┘
```

---

## 五、关键特性对比

### 5.1 核心功能矩阵

| 功能 | Prefect | Temporal | LangGraph | AutoGen |
|-----|---------|---------|-----------|---------|
| 动态DAG | ✅ | ✅ | ✅ | ⚠️ 有限 |
| 状态持久化 | ✅ | ✅✅ | ✅ | ❌ |
| 错误恢复 | ✅ | ✅✅✅ | ✅ | ✅ |
| 可视化调试 | ✅ | ✅ | ✅✅ | ✅ |
| 多LLM支持 | ✅ | ✅ | ✅✅ | ✅ |
| Tool集成 | ✅ | ✅ | ✅✅ | ✅✅ |
| 并发控制 | ✅✅ | ✅✅ | ⚠️ 需手动 | ✅ |

### 5.2 新兴框架（2025）

- **OpenAI Agents SDK**：Swarm的生产级替代，26,900+ GitHub stars
- **Microsoft Agent Framework**：开源企业级Agent引擎（2025年10月发布）
- **Google ADK**：Google生态集成
- **Anthropic Claude Agent SDK**：Claude原生支持

---

## 六、技术选型决策树

```
需要DAG任务规划器？
├── 快速原型/实验 → Prefect
├── 生产级可靠执行 → Temporal
├── 多Agent复杂交互 → LangGraph
├── 低代码/可视化 → AutoGen Studio
├── Prompt自动优化 → DSPy
└── 企业级部署 → OpenAI Agents SDK / MS Agent Framework
```

---

## 七、结论与建议

### 7.1 主流技术栈推荐

**个人助手Agent开发的最佳组合：**
1. **编排层**：Temporal（保证任务可靠执行）或 Prefect（快速迭代）
2. **Agent框架**：LangGraph（细粒度控制）或 OpenAI Agents SDK（生产级）
3. **LLM适配**：LangChain/Litellm（多模型支持）
4. **工具集成**：MCP协议（标准化）

### 7.2 关键趋势

1. **框架融合**：纯编排框架（Temporal）与Agent框架（LangGraph）边界模糊
2. **协议标准化**：MCP成为Tool集成事实标准
3. **生产就绪**：2025年大量框架从研究原型转向生产级SDK
4. **Guardrails内置**：安全验证、输入输出过滤成为标配

### 7.3 开发建议

- **MVP阶段**：使用Prefect + LangChain快速验证
- **生产化阶段**：迁移至Temporal + OpenAI Agents SDK
- **复杂场景**：LangGraph的图编排能力更适合多Agent协作

---

## 参考资料

1. LangGraph官方文档：https://langchain-ai.github.io/langgraph/
2. Temporal Durable Execution：https://temporal.io/
3. DSPy论文：https://arxiv.org/abs/2310.03714
4. AutoGen论文：https://arxiv.org/abs/2308.08155
5. OpenAI Agents SDK（2025年3月发布）
6. Microsoft Agent Framework（2025年10月发布）
