# 个人助手Agent核心逻辑架构分析

## 一、架构概览

基于Agnes-Agent项目的实现，个人助手Agent采用**LangGraph + ReAct循环 + DAG执行**的三层架构：

```
用户输入 → Chatbot节点 → (自决: 直接回答 / request_planning)
                           ↓
                        Planner节点 → 生成DAG计划
                           ↓
                        Executor节点 → DAG并行执行
                           ↓
                      Validator → Summarizer → 返回Chatbot
```

---

## 二、状态管理机制

### 2.1 极简State设计原则

**核心思想**：DB + LangGraph configurable 是唯一真相源，state只做节点间管道。

```python
# app/graph/state.py
class State(TypedDict):
    messages: Annotated[list, add_messages]   # LLM对话消息流
    thread_id: str                            # 会话ID（DB主键索引）
    pending_plan: Optional[str]              # 触发器：chatbot→planner
    _validation_passed: Optional[bool]       # 验证结果闭环
    _validation_message: Optional[str]        # 验证失败原因
    _fix_attempts: Optional[int]             # 修复尝试次数
    _total_replans: Optional[int]            # 重规划次数
    _empty_streak: Optional[int]             # 连续空批计数（防死循环）
```

### 2.2 状态存储分层

| 层级 | 存储位置 | 用途 |
|------|----------|------|
| L1 会话状态 | LangGraph Checkpointer (SQLite) | 消息历史、节点间传递 |
| L2 用户画像 | DB user_profile表 | 长期用户信息 |
| L3 任务计划 | DB task_plans + subtasks | 多步任务分解与进度 |
| L4 命令历史 | DB command_history | 操作审计与上下文 |
| L5 语义缓存 | DB semantic_cache | 外部知识TTL缓存 |

### 2.3 Checkpoint机制

- 使用 `SqliteSaver` 作为LangGraph的checkpointer
- 支持中断/恢复（审批挂起场景）
- 每轮节点执行后自动保存快照

---

## 三、工具调用链

### 3.1 ReActLoop核心循环

```python
# app/planning/react_loop.py
class ReActLoop:
    def run(self, messages, tools, on_tool_before=None, on_tool_after=None,
            interrupt_handler=None, state=None, on_before_llm=None):
        
        while iteration < max_iterations:
            # 1. LLM推理 → 获取tool_calls
            response = self._invoke_llm(messages)
            
            # 2. 提取工具调用
            tool_calls = self._extract_tool_calls(response)
            
            # 3. 安全拦截 + 审批
            for tc in tool_calls:
                gate = on_tool_before(tc.name, tc.args)  # 安全检查
                if not gate: 
                    continue  # 拒绝并记录
                
                allow = interrupt_handler(tc.name, tc.args)  # 人工审批
                if not allow:
                    continue
                
                # 4. 执行工具
                result = self._execute_tool(tc.name, tc.args, tools)
                
                # 5. 写入ToolMessage
                messages.append(ToolMessage(content=result, tool_call_id=tc.id))
                
                # 6. 结构化终止工具检测
                if tc.name in self._terminate_tools:
                    break  # 立即退出循环
```

### 3.2 工具调用链路由

**Chatbot节点工具链**：
- `update_user_info` / `update_user_preference` → 更新L2/L3记忆
- `tavily_search` → 联网搜索 → L5语义缓存
- `execute_command` → 系统命令执行
- `write_file` / `edit_file` / `delete_file` → 文件操作
- `request_planning` → **触发跳转Planner节点**

**Executor节点工具链**：
- 基础工具集（搜索/命令/文件等）
- `complete_node` / `fail_node` → **结构化终止工具**

### 3.3 工具调用硬约束

```python
# 探索类工具：最多2次（ls/glob/read_file/execute_command）
# 写入类工具：最多8次（write_file/edit_file/delete_file）
# 路径必须 deliverables/ 开头
```

---

## 四、错误处理机制

### 4.1 多层熔断策略

| 错误类型 | 检测机制 | 处理方式 |
|----------|----------|----------|
| 连续相同工具调用 | `same_call_count >= 3` | 强制终止，提示"信息不足" |
| 连续被拒工具 | `_reject_count >= 3` | 强制终止，提示调整参数 |
| 连续空回复 | `_NO_PROGRESS_LIMIT = 3` | 注入引导→强制break |
| 异常回复刷屏 | `apply_reply_guard()` | 首次写库，后续跳过写入 |
| LLM API失败 | tenacity重试（3次指数退避） | 15s→30s→60s |

### 4.2 安全拦截层

```python
def on_tool_before(name, params):
    # 1. 危险命令拦截
    if name == "execute_command":
        for d in ['rm -rf /', 'dd ', 'mkfs', 'format', 'shutdown']:
            if d in cmd:
                return False, f"安全策略阻止: {cmd}"
    
    # 2. 探索/写入次数上限
    if name in _EXPLORE_TOOLS and explore_count >= 2:
        return False, "探索次数已达上限"
    if name in _WRITE_TOOLS and write_count >= 8:
        return False, "写入次数已达上限"
    
    # 3. 路径安全校验
    if not path.startswith('deliverables/'):
        return False, "路径必须以deliverables/开头"
```

### 4.3 审批中断机制

```python
def _interrupt_handler(name, params):
    if name in _APPROVAL_TOOLS:
        mode = config.get("approval_mode")  # per_ask/session_allow/always_allow
        
        if mode == "per_ask":
            resp = interrupt({
                "question": f"执行操作？\n{name}: {desc}",
                "command": desc,
                "mode": mode
            })
            return resp.get("allow", False), None
```

- **GraphInterrupt** 异常必须冒泡到graph层，不能被catch
- 审批模式跨进程共享（`_srv_cfg._CONFIG`）

### 4.4 DAG失败隔离与局部重规划

```python
# app/planning/dag_core.py
def compute_failure_skips(nodes, edges):
    """强依赖失败 → 递归传播skipped"""
    # 仅强依赖（非软依赖）失败才阻塞后继
    
def _do_local_replan(plan, failed_ids, dag, node_map, edges):
    """④ 局部重规划：锁定已完成，只重规划受影响子图"""
    # 1. 计算受影响子图（failed节点 + 强依赖后继）
    # 2. 保留success/skipped节点
    # 3. LLM重生成受影响部分
    # 4. 替换回DAG继续执行（replan_count++）
```

**重规划上限**：`replan_count < 3`

---

## 五、关键设计决策

### 5.1 为什么不用LangGraph的ToolNode？

**原因**：需要亲手实现完整工具调度细节：
- 安全拦截（危险命令过滤）
- 人工审批（interrupt机制）
- 拒绝熔断（连续3次同调用终止）
- 元工具返回值透传（`request_planning`的Command.update）

### 5.2 为什么用结构化终止工具？

**替代FINAL_ANSWER文本约定**：
```python
# 旧方案：模型输出"FINAL_ANSWER: ..."
# 新方案：模型调用 complete_node/fail_node 工具
@tool
def complete_node(description: str, files: List[str]):
    _outcome["status"] = "success"
    _outcome["files"] = files  # 系统校验文件存在性

@tool
def fail_node(reason: str):
    _outcome["status"] = "failed"
    _outcome["reason"] = reason
```

**优势**：工业级可靠性，不依赖LLM自觉。

### 5.3 双通道SSE推送

- **messages通道**：流式token（打字机效果）
- **updates通道**：节点状态 + 审批事件（最终兜底）

---

## 六、核心文件映射

| 模块 | 文件 | 职责 |
|------|------|------|
| 状态管理 | `app/graph/state.py` | LangGraph State定义 |
| ReAct循环 | `app/planning/react_loop.py` | 思考-行动-观察闭环 |
| DAG执行 | `app/planning/dag_executor.py` | 并行执行 + 局部重规划 |
| DAG核心 | `app/planning/dag_core.py` | 环检测/拓扑排序/状态机 |
| 记忆系统 | `app/memory/memory_manager.py` | 5层记忆管理 |
| 图构建 | `app/graph/builder.py` | 节点编排 + 条件路由 |
| 工具安全 | `app/tools/path_guard.py` | 路径白名单校验 |

---

## 七、总结

个人助手Agent的核心逻辑架构呈现以下特征：

1. **状态极简**：state只传thread_id和触发器，业务数据全部存DB
2. **工具链可控**：ReActLoop实现完整调度，支持安全拦截+审批+熔断
3. **错误处理分层**：
   - 工具层：安全拦截
   - 循环层：连续失败熔断
   - 规划层：DAG失败隔离 + 局部重规划
   - 记忆层：重复消息去重 + 摘要节流
4. **工业级设计**：结构化终止工具替代文本约定，Checkpoint支持中断恢复

这套架构在保持灵活性的同时，通过多重约束机制确保了agent的稳定性和安全性。
