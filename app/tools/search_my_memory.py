"""
search_my_memory：在"长期记忆"（固化事实/偏好 + 对话历史 + 任务历史）里做统一语义检索。
模型想"我记得之前做过什么 / 用户的偏好"时主动调用。
"""
import json
from langchain_core.tools import tool
from app.config import DB_PATH



@tool
def search_my_memory(
    keyword: str,
    layers: str = "all",
    limit: int = 5,
) -> str:
    """
    在长期记忆中按语义检索（跨对话历史 / 任务历史 / 固化事实）。

    参数:
        keyword: 检索语义（不需要逐字一致，embedding 会做语义匹配）
        layers: 保留参数（兼容旧调用），当前忽略，恒为跨层检索
        limit: 最多返回条数，默认 5

    返回:
        JSON 字符串数组，按相关度排序，含 kind/text/score。
    """
    from app.memory import memory_engine
    rows = memory_engine.search_semantic(keyword, limit=limit)
    return json.dumps(rows, ensure_ascii=False, indent=2, default=str)