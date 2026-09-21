"""
tavily_search.py — 网络搜索工具。

每用户一个 key：调用时按当前用户（current_user()）从 accounts.db 的
users.extra.tavily_api_key 读取，显式传给 TavilySearch（不再依赖全局 TAVILY_API_KEY env）。

网络不可用/失败时快速返回降级提示，避免模型无限重试拖慢执行。
未配置 key 时给出一句明确的引导，同样不要反复重试。
"""
from langchain_core.tools import tool


def _current_tavily_key() -> str:
    """取当前登录用户自己的 tavily key（accounts.db users.extra）。"""
    try:
        from app.userctx import current_user, DEFAULT_USER
        from app.server import accounts
        username = current_user() or DEFAULT_USER
        full = accounts.get_user(username, include_secrets=True) or {}
        return str((full.get("extra") or {}).get("tavily_api_key") or "").strip()
    except Exception:
        return ""


@tool
def tavily_search(query: str, search_depth: str = "basic") -> str:
    """网络搜索（实时信息/外部知识）。

    参数:
      query: 搜索查询字符串
      search_depth: 'basic'（快）或 'advanced'（深）

    注意: 使用发起本任务的用户自己的 Tavily Key。未配置时会提示去「设置」页填写；
    当网络/搜索服务不可用时返回降级提示——请基于你的既有知识直接作答，不要反复重试本工具。
    """
    key = _current_tavily_key()
    if not key:
        return "[搜索不可用] 当前账号未配置 Tavily Key：请在左侧「设置」页填写 Tavily Key 后再试，不要反复重试本工具。"
    try:
        from langchain_tavily import TavilySearch
        t = TavilySearch(max_results=3, search_depth=search_depth, timeout=5, tavily_api_key=key)
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