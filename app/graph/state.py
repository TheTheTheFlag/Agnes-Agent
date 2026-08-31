"""app.graph.state — LangGraph State 定义（极简版）。

设计原则：DB + LangGraph configurable 是唯一真相源，state 只做节点间管道。
- 用户信息/偏好：DB 存储，chatbot 节点每轮从 DB 读并注入 system prompt
- 任务规划：DB 存所有数据，state 只传 thread_id；当前任务用 get_task_plan_by_thread 查
- 审批模式：存 _srv_cfg._CONFIG['configurable']（跨进程生效，CLI / API 共享）
- pending_plan：唯一需要在 state 跨节点传的——chatbot 工具触发 → planner 节点消费
"""
from typing import Annotated, Optional
from typing_extensions import TypedDict
from langgraph.graph.message import add_messages


class State(TypedDict):
    """LangGraph 图状态：极简到只剩 LangGraph 框架和触发器必须的字段。"""
    # ===== LangGraph 框架字段 =====
    messages: Annotated[list, add_messages]   # LLM 对话消息流
    thread_id: str                            # 当前会话 ID（DB 主键索引，定位用户/任务/记忆）

    # ===== 触发器：chatbot 调 request_planning 后注入，planner 节点消费后清空 =====
    pending_plan: Optional[str]

    # ===== 验证反馈闭环（validator → executor 修复）：跨节点传递失败原因 + 修复计数 =====
    _validation_passed: Optional[bool]   # 上次验证结果（validator 设置）
    _validation_message: Optional[str]   # 验证失败原因（结构化文本，executor 修复时读取）
    _fix_attempts: Optional[int]         # 反馈修复尝试次数（validator 递增，executor 重置）
    _total_replans: Optional[int]        # 重规划次数（executor/validator 递增，路由读取）
