"""
tavily_search.py — 网络搜索工具。
网络不可用/失败时快速返回降级提示，避免模型无限重试拖慢执行。
"""
import os
import json
from dotenv import load_dotenv
load_dotenv()

from langchain_core.tools import tool


@tool
def tavily_search(query: str, search_depth: str = "basic") -> str:
    """网络搜索（实时信息/外部知识）。

    参数:
      query: 搜索查询字符串
      search_depth: 'basic'（快）或 'advanced'（深）

    注意: 当网络/搜索服务不可用时会返回降级提示——请基于你的既有知识直接作答，
    不要反复重试本工具（每次失败会等待超时，拖慢任务）。
    """
    try:
        from langchain_tavily import TavilySearch
        t = TavilySearch(max_results=3, search_depth=search_depth, timeout=5)
        result = t.invoke(query)
        if isinstance(result, dict) and result.get("results"):
            items = result["results"]
            text = "\n\n".join(f"{r.get('title', '')}: {r.get('content', '')}" for r in items[:3])
            return text[:3000] if text else "（搜索无结果）"
        return str(result)[:3000]
    except Exception as e:
        # 快速失败 + 降级引导：阻止模型反复重试（每次失败等超时是任务卡住的主因）
        err = str(e)[:100]
        if "Connection" in err or "timeout" in err.lower() or "reset" in err.lower():
            return "[搜索不可用] 网络/搜索服务暂时不可用，请基于你的既有知识直接撰写或作答，不要再次调用 tavily_search。"
        return f"[搜索失败] {err} 请基于既有知识继续，不要反复重试本工具。"
