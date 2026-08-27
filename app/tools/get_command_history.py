"""
get_command_history：读取 L4 程序化记忆（最近执行过的系统命令）。
模型在重做类似操作或排错时可主动调用，避免重复犯错。
"""
import json
import os
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
    读取最近执行过的系统命令历史。

    参数:
        limit: 最多返回条数，默认 10
        pattern: 命令关键词过滤（SQL LIKE），可选。例如 'git' 只看 git 命令
        thread_id: 指定 thread 看该 thread 的命令；留空看全部

    返回:
        JSON 字符串数组，含 command, exit_code, success, duration_ms, created_at
    """
    from app.memory import MemoryManager
    mm = MemoryManager(db_path=DB_PATH)
    rows = mm.get_command_history(
        thread_id=thread_id or None,
        limit=limit,
        pattern=pattern or None,
    )
    return json.dumps(rows, ensure_ascii=False, indent=2, default=str)
