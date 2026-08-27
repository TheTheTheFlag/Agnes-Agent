"""app.server.api.tools — 工具列表 API。"""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.tools import tools

router = APIRouter()

@router.get("/api/tools")
async def get_tools():
    """列出已注册的工具及其参数 schema（用于前端"工具"面板）。"""
    try:
        from app.tools import tools
    except Exception as e:
        return {"tools": [], "error": str(e)}
    result = []
    for t in tools:
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


