"""app.server.api.tools — 工具列表 API。"""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()

@router.get("/api/tools")
async def get_tools():
    """列出当前用户可见的工具及其参数 schema（用于前端"工具"面板）。

    execute_command 默认仅管理员可见；普通用户只在「短剧创作」工作台作用域内
    （当前用户×当前线程，见 app/workbench.py）临时可见，风险由审批流 + path_guard 兜底；
    联网搜索 tavily_search 全员可见，key 按用户各自从 accounts.db 读取。
    """
    try:
        from app.tools import get_tools_for_user
    except Exception as e:
        return {"tools": [], "error": str(e)}
    user_tools = get_tools_for_user()
    result = []
    for t in user_tools:
        try:
            args = t.args if hasattr(t, "args") else {}
            result.append({
                "name": getattr(t, "name", "?"),
                "description": (getattr(t, "description", "") or "")[:500],
                "args_schema": args,
            })
        except Exception as e:
            result.append({"name": getattr(t, "name", "?"), "description": "", "args_schema": {}, "error": str(e)})
    return {"tools": result, "total": len(result)}


