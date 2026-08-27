from typing import Annotated, List, Dict, Optional, Any
from typing_extensions import TypedDict
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field
from datetime import datetime

# ----- 任务规划数据模型 -----
class Subtask(BaseModel):
    id: str
    description: str
    dependencies: List[str] = Field(default_factory=list)
    status: str = "pending"
    result: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

class TaskPlan(BaseModel):
    goal: str
    subtasks: List[Subtask] = Field(default_factory=list)
    status: str = "planning"
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    final_summary: Optional[str] = None
    current_subtask_index: int = 0

class State(TypedDict):
    messages: Annotated[list, add_messages]
    name: str
    birthday: str
    profile: Dict[str, str]
    preferences: Dict[str, str]
    recent_tasks: List[Dict]
    current_task_id: Optional[int]
    current_task_messages: List[Dict]
    _new_profile: Optional[Dict[str, str]]
    _new_preference: Optional[Dict[str, str]]
    summary: str
    summary_message_count: int

    # 任务规划字段
    task_plan: Optional[TaskPlan]
    _planning_triggered: bool
    _executor_fail_count: int
    _total_replans: int
    _validation_passed: bool

    # 审批模式："per_ask" | "session_allow" | "always_allow"
    approval_mode: str

    # 由 request_planning 工具注入；chatbot 检测后 Command(goto="planner")
    pending_plan: Optional[str]

    # 内部用
    thread_id: str