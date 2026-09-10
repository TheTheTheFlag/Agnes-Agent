# 个人助手Agent开发教程 - 代码示例与最佳实践

> 整理时间：2026年7月 | 配套文档：技术选型对比报告

---

## 一、依赖安装指南

### 1.1 基础环境准备

```bash
# 创建虚拟环境（推荐）
python -m venv agent-venv
source agent-venv/bin/activate  # Linux/Mac
# agent-venv\Scripts\activate   # Windows

# 安装核心依赖
pip install langchain langchain-openai langchain-anthropic langgraph

# 向量数据库（按需选择）
pip install chromadb  # 本地轻量级
# 或
pip install psycopg2-binary  # PostgreSQL + pgvector
# 或
pip install qdrant-client  # Qdrant

# 工具集成
pip install langchain-community  # 包含多个社区工具
pip install mcp                  # MCP协议支持（2025新兴标准）

# 可选：DAG任务编排（生产级）
pip install prefect temporal-client

# 可选：多Agent协作
pip install crewai               # 角色化Agent团队（快速原型）
# 或
pip install ag2                  # AutoGen社区fork（2025新兴）
```

### 1.2 requirements.txt 示例

```txt
# 核心框架
langchain>=0.3.0
langchain-openai>=0.2.0
langchain-anthropic>=0.2.0
langgraph>=0.2.0

# 向量存储（二选一）
chromadb>=1.0.0
# 或 qdrant-client>=1.9.0

# 工具与协议
langchain-community>=0.3.0
mcp>=0.1.0

# 可选：任务编排
prefect>=2.19.0
# 或 temporal-client>=2.8.0

# 可选：多Agent框架
crewai>=0.30.0
# 或 ag2>=0.3.0

# 评估与监控
langsmith>=0.1.0
langfuse>=2.0.0
```

---

## 二、配置结构最佳实践

### 2.1 项目目录结构

```
personal-agent/
├── app/
│   ├── agents/                 # Agent定义
│   │   ├── __init__.py
│   │   ├── planner.py          # 规划Agent
│   │   ├── researcher.py       # 研究Agent
│   │   └── executor.py         # 执行Agent
│   ├── tools/                  # 工具实现
│   │   ├── __init__.py
│   │   ├── search_tool.py
│   │   └── memory_tool.py
│   ├── graph/                  # LangGraph工作流
│   │   └── workflow.py
│   └── main.py                 # 入口
├── config/
│   ├── settings.py             # 配置管理
│   └── prompts.py              # Prompt模板
├── data/                       # 数据目录（向量库、记忆等）
├── tests/                      # 测试
├── .env                        # 环境变量（勿提交）
├── requirements.txt
└── README.md
```

### 2.2 配置管理示例

```python
# config/settings.py
from pydantic_settings import BaseSettings
from typing import Optional, List

class Settings(BaseSettings):
    # LLM提供商配置
    OPENAI_API_KEY: str
    ANTHROPIC_API_KEY: Optional[str] = None
    
    # 模型选择
    DEFAULT_MODEL: str = "gpt-4o"
    FAST_MODEL: str = "gpt-4o-mini"
    
    # 向量存储
    VECTOR_STORE_TYPE: str = "chroma"  # chroma | pgvector | qdrant
    VECTOR_STORE_PATH: str = "./data/chroma_db"
    
    # Agent配置
    MAX_ITERATIONS: int = 10           # 最大迭代次数
    TOOL_TIMEOUT: int = 30             # 工具调用超时（秒）
    
    # 日志与监控
    LOG_LEVEL: str = "INFO"
    LANGSMITH_TRACING: bool = True
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings()
```

### 2.3 .env 环境变量模板

```bash
# 必填：LLM API密钥
OPENAI_API_KEY=sk-xxx
# ANTHROPIC_API_KEY=sk-ant-xxx

# 可选：向量存储
VECTOR_STORE_PATH=./data/chroma_db

# 可选：监控
LANGSMITH_API_KEY=lsv2_...
LANGSMITH_PROJECT=my-agent
```

---

## 三、最小可用实现（MVP）

### 3.1 基础Agent类

```python
# app/agents/base.py
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage, AIMessage
from typing import List, Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)

class BaseAgent:
    """Agent基类，定义核心接口"""
    
    def __init__(self, name: str, llm, tools: Optional[List] = None):
        self.name = name
        self.llm = llm
        self.tools = tools or []
        self.memory: List[Dict[str, Any]] = []
    
    @tool
    def think(self, thought: str) -> str:
        """记录思考过程"""
        logger.debug(f"[{self.name}] 思考: {thought}")
        return f"已记录: {thought}"
    
    def add_message(self, role: str, content: str):
        """添加消息到历史"""
        self.memory.append({"role": role, "content": content})
    
    def get_context(self) -> List[Dict[str, str]]:
        """获取对话上下文"""
        return [
            {"role": msg["role"], "content": msg["content"]}
            for msg in self.memory[-10:]  # 只保留最近10条
        ]
    
    async def act(self, user_input: str) -> str:
        """Agent核心动作方法（子类实现）"""
        raise NotImplementedError
```

### 3.2 规划Agent（Planner）

```python
# app/agents/planner.py
from .base import BaseAgent
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from pydantic import BaseModel, Field
from typing import List, Optional

class TaskPlan(BaseModel):
    """任务计划结构"""
    subtasks: List[str] = Field(description="子任务列表")
    dependencies: dict = Field(
        description="依赖关系，key为子任务，value为前置任务列表"
    )
    estimated_steps: int = Field(description="预估执行步骤")

class PlannerAgent(BaseAgent):
    """负责将复杂任务分解为子任务序列"""
    
    def __init__(self, llm: Optional[ChatOpenAI] = None):
        super().__init__(
            name="planner",
            llm=llm or ChatOpenAI(model="gpt-4o-mini"),
        )
        
        self.prompt = ChatPromptTemplate.from_messages([
            ("system", """你是一个任务规划专家。
            将用户的复杂任务分解为可执行的子任务序列。
            返回JSON格式的任务计划。"""),
            ("human", "请规划以下任务：{user_input}"),
        ])
        
        self.parser = JsonOutputParser(pydantic_object=TaskPlan)
        self.chain = self.prompt | self.llm | self.parser
    
    async def act(self, user_input: str) -> dict:
        """分解任务"""
        self.add_message("user", user_input)
        
        plan = await self.chain.ainvoke({"user_input": user_input})
        
        self.add_message("assistant", f"已生成计划：{len(plan['subtasks'])}个子任务")
        logger.info(f"[Planner] 生成任务计划: {plan}")
        
        return plan
```

### 3.3 执行Agent（Executor）

```python
# app/agents/executor.py
from .base import BaseAgent
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage
import asyncio

class ExecutorAgent(BaseAgent):
    """负责执行具体子任务"""
    
    def __init__(self, llm: Optional[ChatOpenAI] = None):
        super().__init__(
            name="executor",
            llm=llm or ChatOpenAI(model="gpt-4o"),
        )
    
    async def execute_subtask(self, subtask: str, context: dict = None) -> str:
        """执行单个子任务"""
        self.add_message("user", f"执行子任务: {subtask}")
        
        system_prompt = SystemMessage(content="""你是一个高效的执行助手。
        根据用户输入执行具体任务，返回清晰的结果。
        如果有工具可用，优先使用工具获取信息。""")
        
        messages = [system_prompt] + self.get_context()
        
        response = await self.llm.ainvoke(messages)
        result = response.content
        
        self.add_message("assistant", result)
        logger.info(f"[Executor] 完成子任务: {subtask[:50]}...")
        
        return result
    
    async def act(self, user_input: str, subtask: str) -> str:
        """执行子任务入口"""
        return await self.execute_subtask(subtask, {"user_input": user_input})
```

### 3.4 LangGraph工作流编排

```python
# app/graph/workflow.py
from langgraph.graph import StateGraph, START, END
from typing import TypedDict, Annotated
import operator
from ..agents.planner import PlannerAgent
from ..agents.executor import ExecutorAgent
import asyncio

class AgentState(TypedDict):
    """状态定义"""
    user_input: str
    plan: dict
    results: Annotated[list, operator.add]  # 自动合并列表
    current_step: int
    is_complete: bool

class AgentWorkflow:
    """Agent工作流编排"""
    
    def __init__(self):
        self.planner = PlannerAgent()
        self.executor = ExecutorAgent()
        
        # 构建图
        self.graph = StateGraph(AgentState)
        
        # 添加节点
        self.graph.add_node("plan", self._plan_task)
        self.graph.add_node("execute", self._execute_step)
        self.graph.add_node("check", self._check_complete)
        
        # 添加边
        self.graph.add_edge(START, "plan")
        self.graph.add_edge("plan", "execute")
        self.graph.add_edge("execute", "check")
        
        # 条件边：检查是否完成
        self.graph.add_conditional_edges(
            "check",
            self._route_from_check,
            {"continue": "execute", "complete": END}
        )
        
        self.compiled_graph = self.graph.compile()
    
    async def _plan_task(self, state: AgentState) -> dict:
        """规划任务节点"""
        plan = await self.planner.act(state["user_input"])
        return {
            "plan": plan,
            "current_step": 0,
            "results": []
        }
    
    async def _execute_step(self, state: AgentState) -> dict:
        """执行单步节点"""
        subtasks = state["plan"]["subtasks"]
        current_idx = state["current_step"]
        
        if current_idx < len(subtasks):
            subtask = subtasks[current_idx]
            result = await self.executor.act(state["user_input"], subtask)
            return {
                "results": [f"子任务{current_idx+1}: {result[:100]}..."],
                "current_step": current_idx + 1
            }
        return state
    
    async def _check_complete(self, state: AgentState) -> str:
        """检查是否完成"""
        total_steps = len(state["plan"]["subtasks"])
        is_complete = state["current_step"] >= total_steps
        return {"is_complete": is_complete}
    
    def _route_from_check(self, state: AgentState) -> str:
        """路由逻辑"""
        return "complete" if state["is_complete"] else "continue"
    
    async def run(self, user_input: str) -> dict:
        """运行完整工作流"""
        initial_state = {
            "user_input": user_input,
            "plan": {},
            "results": [],
            "current_step": 0,
            "is_complete": False
        }
        
        result = await self.compiled_graph.ainvoke(initial_state)
        return result
```

### 3.5 主入口

```python
# app/main.py
import asyncio
import logging
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from .graph.workflow import AgentWorkflow

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def main():
    # 初始化工作流
    workflow = AgentWorkflow()
    
    # 用户输入
    user_input = "帮我调研LangChain和LlamaIndex的优缺点，并整理成表格"
    
    logger.info(f"处理请求: {user_input}")
    
    # 执行Agent工作流
    result = await workflow.run(user_input)
    
    # 输出结果
    print("\n=== Agent执行结果 ===")
    for i, r in enumerate(result["results"], 1):
        print(f"\n[{i}] {r}")
    
    print(f"\n总步骤: {result['current_step']}")
    print(f"完成状态: {result['is_complete']}")

if __name__ == "__main__":
    asyncio.run(main())
```

---

## 四、核心代码模式

### 4.1 工具定义模式

```python
# app/tools/search_tool.py
from langchain_core.tools import tool
import asyncio

@tool
async def web_search(query: str, max_results: int = 5) -> str:
    """搜索引擎工具"""
    # 实际实现调用搜索API
    return f"搜索结果（共{max_results}条）: {query}"

@tool
async def read_file(file_path: str) -> str:
    """读取文件内容"""
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    return content[:2000]  # 限制长度

# 注册工具
tools = [web_search, read_file]
```

### 4.2 记忆管理模式

```python
# app/tools/memory_tool.py
from langchain_core.tools import tool
import json
from pathlib import Path
from datetime import datetime

MEMORY_FILE = Path("./data/memory.json")

@tool
def save_memory(key: str, value: str) -> str:
    """保存长期记忆"""
    memories = {}
    if MEMORY_FILE.exists():
        memories = json.loads(MEMORY_FILE.read_text())
    
    memories[key] = {
        "value": value,
        "timestamp": datetime.now().isoformat()
    }
    
    MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    MEMORY_FILE.write_text(json.dumps(memories, ensure_ascii=False, indent=2))
    
    return f"已保存记忆: {key}"

@tool
def recall_memory(key: str) -> str:
    """检索记忆"""
    if not MEMORY_FILE.exists():
        return "暂无记忆"
    
    memories = json.loads(MEMORY_FILE.read_text())
    return memories.get(key, {}).get("value", "未找到该记忆")

memory_tools = [save_memory, recall_memory]
```

### 4.3 错误处理模式

```python
# app/agents/base.py
import asyncio
from functools import wraps

def retry_with_backoff(max_retries=3, base_delay=1.0):
    """指数退避重试装饰器"""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_retries - 1:
                        raise
                    delay = base_delay * (2 ** attempt)
                    await asyncio.sleep(delay)
        return wrapper
    return decorator

# 使用示例
@retry_with_backoff(max_retries=3)
async def call_llm_with_retry(messages):
    return await llm.ainvoke(messages)
```

---

## 五、最佳实践清单

### 5.1 设计原则

| 原则 | 说明 | 示例 |
|------|------|------|
| **单一职责** | 每个Agent专注一个任务 | Planner只做规划，Executor只做执行 |
| **状态隔离** | Agent状态独立，避免共享可变状态 | 使用TypedDict定义状态，不可变传递 |
| **容错设计** | 工具调用要有超时和重试 | 使用`retry_with_backoff`装饰器 |
| **可观测性** | 记录关键日志，便于调试 | 每个节点打印输入输出 |
| **渐进增强** | 从简单到复杂，逐步添加功能 | 先实现单Agent，再扩展到多Agent |

### 5.2 生产环境建议

```python
# 1. 配置管理
# 使用pydantic-settings统一管理配置，避免硬编码

# 2. 监控集成
from langsmith import Client
from langsmith.wrappers import wrap_openai

client = Client()
# 自动追踪所有LLM调用

# 3. 缓存策略
from langchain_community.cache import SQLiteCache
from langchain.globals import set_llm_cache

set_llm_cache(SQLiteCache(database_path="./llm_cache.db"))

#