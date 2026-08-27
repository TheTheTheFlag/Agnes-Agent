import json
from typing import Annotated
from langchain_core.tools import tool
from app.config import DB_PATH
from langchain_core.tools import InjectedToolCallId
from langchain_core.messages import ToolMessage
from langgraph.types import Command

@tool
def update_user_preference(
    info_json: str,
    tool_call_id: Annotated[str, InjectedToolCallId]
) -> Command:
    """
    记录用户偏好（任意键值对）。用户提供的偏好信息应以 JSON 格式传入。
    例如：{"language": "中文", "theme": "简洁", "response_length": "简短"}
    """
    try:
        new_preferences = json.loads(info_json)
        if not isinstance(new_preferences, dict):
            raise ValueError("info_json 必须是一个 JSON 对象")
    except json.JSONDecodeError as e:
        error_msg = f"JSON 解析失败: {e}，请提供有效的 JSON 格式。"
        return Command(update={
            "messages": [ToolMessage(content=error_msg, tool_call_id=tool_call_id)]
        })

    # 构建确认消息
    parts = [f"{k}: {v}" for k, v in new_preferences.items()]
    response_msg = "已记录偏好：" + "；".join(parts)

    return Command(update={
        "messages": [ToolMessage(content=response_msg, tool_call_id=tool_call_id)],
        "_new_preference": new_preferences   # 用于合并到 state["preferences"]
    })