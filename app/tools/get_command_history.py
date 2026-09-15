"""
get_command_history：读取最近执行过的工具/命令记录（L1 messages 表 kind='tool_call' 事件，
原独立 L4 command_history 表已移除）。模型在重做类似操作或排错时可主动调用，避免重复犯错。
"""
import json
from typing import Annotated
from langchain_core.tools import tool
from app.config import DB_PATH



@tool
def get_command_history(
    limit: int = 10,
    pattern: str = "",
    thread_id: str = "",
) -> str:
    """
    读取最近执行过的工具调用/命令历史。

    参数:
        limit: 最多返回条数，默认 10
        pattern: 关键词过滤（工具名/参数/结果模糊匹配），可选。例如 'git' 只看 git 相关
        thread_id: 指定 thread 看该 thread 的记录；留空看全部

    返回:
        JSON 字符串数组，含 command, stdout_preview, created_at（来自 L1 消息事件）
    """
    from app.memory import MemoryManager
    mm = MemoryManager(db_path=DB_PATH)
    rows = mm.get_tool_call_history(
        thread_id=thread_id or None,
        limit=limit,
        pattern=pattern or None,
    )
    return json.dumps(rows, ensure_ascii=False, indent=2, default=str)