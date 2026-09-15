"""
search_my_memory：在"人的记忆"里做统一语义检索——固化事实/偏好（memory_facts）+ 任务历史（dag_plans）。
模型想"我记得 / 用户的偏好 / 用户是什么样的人"时主动调用。
只查提炼后的记忆，不查对话原文；要查对话原文里的实体-关系，用 lightgraph_query。
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
    在人的记忆里按语义检索（跨固化事实/偏好 + 任务历史）。

    参数:
        keyword: 检索语义（不需要逐字一致，embedding 会做语义匹配）
        layers: 保留参数（兼容旧调用），当前忽略，恒为跨层检索
        limit: 最多返回条数，默认 5

    返回:
        JSON 字符串数组，按相关度排序，含 kind/text/score。

    适用场景：用户问"我记得/我之前是不是/我的偏好/我的身份/做过什么任务"。
    不适用：需要对话原文里的实体关系或多跳知识——用 lightgraph_query。
    """
    from app.memory import memory_engine
    rows = memory_engine.search_semantic(keyword, limit=limit)
    return json.dumps(rows, ensure_ascii=False, indent=2, default=str)