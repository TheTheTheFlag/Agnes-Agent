"""
search_my_memory：在 L3（任务/对话历史）和 L5（语义缓存）里按关键词搜索。
模型想"我记得之前做过什么"时主动调用。
"""
import json
from typing import Annotated
from langchain_core.tools import tool
from app.config import DB_PATH
from app.graph.state import State
import os



@tool
def search_my_memory(
    keyword: str,
    layers: str = "L3,L5",
    limit: int = 5,
) -> str:
    """
    在历史任务/对话/外部知识缓存中按关键词搜索。

    参数:
        keyword: 搜索关键词（中英文均可，会用 LIKE %keyword% 模糊匹配）
        layers: 搜索范围，逗号分隔。可选 "L3"（任务+消息历史）、"L5"（tavily/文档缓存）。
                默认为 "L3,L5"。
        limit: 每层最多返回条数，默认 5

    返回:
        JSON 字符串，包含匹配的条目。优先用 L3 找历史任务，用 L5 避免重复网络搜索。
    """
    from app.memory import MemoryManager
    mm = MemoryManager(db_path=DB_PATH)
    result = {}
    wanted = set(layers.split(","))
    if "L3" in wanted:
        result["L3_episodes"] = mm.search_episodes(keyword, limit=limit)
    if "L5" in wanted:
        result["L5_knowledge"] = mm.search_knowledge(keyword, limit=limit)
    if not result:
        return "未指定有效层；请用 'L3' 或 'L5' 或 'L3,L5'"
    return json.dumps(result, ensure_ascii=False, indent=2, default=str)
