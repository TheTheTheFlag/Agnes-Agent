"""app.server.api.tools — 工具列表 API。"""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()

@router.get("/api/tools")
async def get_tools():
    """列出当前用户可见的工具及其参数 schema（用于前端"工具"面板）。

    非管理员不展示 execute_command / tavily_search（与 agent 实际下发的工具集一致）。
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


