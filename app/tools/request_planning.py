"""
request_planning 是一个"虚拟"工具，不做实际工作，
仅用来在 LangGraph 工作流中让 LLM 自助触发"任务规划"分支。

调用语义：模型判断当前用户输入是一个多步骤 / 需要拆解的复杂任务时，
应主动调用此工具，把目标 goal 传进来；chatbot 节点检查到
state["pending_plan"] 后会 Command(goto="planner") 跳到规划节点。
"""
import json
from typing import Annotated
from langchain_core.tools import tool
from app.config import DB_PATH
from langchain_core.tools import InjectedToolCallId
from langchain_core.messages import ToolMessage
from langgraph.types import Command


@tool
def request_planning(
    goal: str,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> Command:
    """
    当用户的目标明显是多步骤、结构化、产出物较复杂（>= 2 个子任务，且有依赖关系）时，
    调用此工具请求进入"规划-执行-验证"流程。

    参数:
        goal: 用户的高层目标（用一句话概括即可，例如"写一篇博客并部署到 GitHub Pages"）

    注意:
        - 仅在确实需要拆解为多个子任务时才调用；普通问答 / 单步工具调用不要用这个工具。
        - 工具本身不产生副作用；它会触发工作流跳转到规划节点。
    """
    if not goal or not goal.strip():
        return Command(update={
            "messages": [ToolMessage(
                content="request_planning 缺少 goal 参数，无法进入规划流程。",
                tool_call_id=tool_call_id,
            )]
        })

    response_msg = f"已接收规划请求，目标：{goal[:200]}"
    return Command(update={
        "messages": [ToolMessage(content=response_msg, tool_call_id=tool_call_id)],
        "pending_plan": goal.strip(),  # chatbot 检测到该字段后跳转到 planner
    })
