"""
record_graph / lightgraph_query：L6 知识图谱工具。

让 LLM 在生产回合里能主动：
  - record_graph：把实体关系三元组显式写进当前会话的 LightRAG 图谱（建图）
  - lightgraph_query：在当前会话图谱里做混合检索（向量 + 图谱）

当前会话由 ligraphrag_adapter.set_current_context(thread_id) 提供（chatbot 节点每轮入口设置），
兜底回退到 .thread_id 文件（console 模式习惯）。
"""
import os
from typing import Annotated, Optional
from langchain_core.tools import tool


def _resolve_thread() -> str:
    """定位"当前会话"：优先取运行中上下文，兜底读 .thread_id 文件。"""
    from app.memory.ligraphrag_adapter import get_current_context
    tid = get_current_context()
    if tid:
        return tid
    for base in (os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                 os.path.dirname(os.path.dirname(os.path.abspath(__file__)))):
        f = os.path.join(base, ".thread_id")
        try:
            with open(f, "r", encoding="utf-8") as fh:
                v = fh.read().strip()
            if v:
                return v
        except Exception:
            continue
    return ""


@tool
def lightgraph_query(query: str, top_k: int = 12) -> str:
    """在当前会话的知识图谱里做混合检索（向量 + 图谱双路召回）。

    参数:
        query: 检索问题/关键词（如 "HarmonyOS 5.0 支持哪些大模型"）
        top_k: 返回候选数，默认 12

    返回:
        命中的实体/关系/相关段落摘要；空结果表示图谱里没命中。
    """
    thread_id = _resolve_thread()
    if not thread_id:
        return "[GraphRAG] 未定位到当前会话，请先开始一段对话再查询图谱。"
    from app.memory.graph_rag_tool import lightgraph_query as _query
    return _query(thread_id, query, top_k=top_k)


@tool
def record_graph(triplets_json: str, source: str = "user") -> str:
    """把实体关系三元组显式记录到当前会话的知识图谱。

    参数:
        triplets_json: JSON 数组，每项 {"head": "...", "rel": "...", "tail": "..."}
        source: 来源标识（user / search / llm_extracted 等），便于追溯

    返回:
        已记录的三元组数；无法解析会返回失败原因。
    """
    thread_id = _resolve_thread()
    if not thread_id:
        return "[GraphRAG] 未定位到当前会话，请先开始一段对话再记录."
    from app.memory.graph_rag_tool import record_graph as _record
    return _record(thread_id, triplets_json, source=source)