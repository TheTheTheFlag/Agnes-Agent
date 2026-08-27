import json
from typing import Annotated
from langchain_core.tools import tool
from app.config import DB_PATH
from langchain_core.tools import InjectedToolCallId
from langchain_core.messages import ToolMessage
from langgraph.types import Command

@tool
def update_user_info(
    info_json: str,
    tool_call_id: Annotated[str, InjectedToolCallId]
) -> Command:
    """
    记录用户的个人信息（任意键值对）。用户提供的信息应以 JSON 格式传入。
    例如：{"name": "张三", "birthday": "1990-01-01", "gender": "男", "children": "女儿（2024-11-21）"}
    """
    try:
        new_profile = json.loads(info_json)
        if not isinstance(new_profile, dict):
            raise ValueError("info_json 必须是一个 JSON 对象")
    except json.JSONDecodeError as e:
        error_msg = f"JSON 解析失败: {e}，请提供有效的 JSON 格式。"
        return Command(update={
            "messages": [ToolMessage(content=error_msg, tool_call_id=tool_call_id)]
        })

    # 构建确认消息
    parts = [f"{k}: {v}" for k, v in new_profile.items()]
    response_msg = "已记录：" + "；".join(parts)

    # 返回 ToolMessage 和 _new_profile 字段（用于合并）
    return Command(update={
        "messages": [ToolMessage(content=response_msg, tool_call_id=tool_call_id)],
        "_new_profile": new_profile   # 新增字段，不覆盖 profile
    })