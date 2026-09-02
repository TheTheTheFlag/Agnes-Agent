"""app.server.api.skills — 技能相关 API：本地技能浏览 + SkillHub 搜索/安装。

受登录中间件保护（与其他 /api/* 一致）。
"""
import os
import re

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

from app.skills import loader
from app.skills.hub import search as _hub_search, fetch_detail as _hub_detail, install as _hub_install

router = APIRouter(prefix="/api", tags=["skills"])

_SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")
_IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}


@router.get("/skills")
async def list_skills():
    """列出 app/skills/ 下所有本地技能元数据。"""
    return {"skills": loader.load_all_skills(), "dir": loader.SKILLS_DIR}


@router.get("/skills/{name}")
async def get_skill(name: str):
    """读取单个技能的元数据 + 正文（含原文），供前端展示。"""
    s = loader.load_skill(name)
    if not s:
        return JSONResponse({"error": f"技能不存在: {name}"}, status_code=404)
    return s


@router.get("/skillhub/search")
async def skillhub_search(q: str = "", limit: int = 8):
    """到 SkillHub 搜索技能（免 key）。q 为空返回空列表。"""
    results = _hub_search(q, limit=min(max(limit, 1), 20))
    if results and results[0].get("error"):
        return JSONResponse({"error": results[0]["error"]}, status_code=502)
    return {"skills": results, "total": len(results)}


@router.get("/skillhub/detail/{skill_id}")
async def skillhub_detail(skill_id: str):
    """拉取 SkillHub 上某个技能详情（用于安装前预览）。"""
    try:
        return _hub_detail(skill_id)
    except Exception as e:
        return JSONResponse({"error": f"获取详情失败: {e}"}, status_code=502)


@router.post("/skillhub/install")
async def skillhub_install(payload: dict):
    """把 SkillHub 技能下载安装到 app/skills/<name>/SKILL.md。payload: {id}（uuid 或技能名均可）"""
    sid = (payload or {}).get("id", "")
    if not sid:
        return JSONResponse({"error": "缺少技能 id"}, status_code=400)
    try:
        result = _hub_install(sid)
    except Exception as e:
        return JSONResponse({"error": f"安装失败: {e}"}, status_code=502)
    return {"ok": True, **result}


@router.get("/skill-media/{skill_name}/{rest:path}")
async def skill_media(skill_name: str, rest: str):
    """提供技能产物文件（图片/视频/日志），供前端 <img> 渲染。

    仅暴露 app/skills/<skill_name>/output/ 目录（生成产物），
    技能的其他文件（SKILL.md、keys.json 等）不可通过此端点访问。
    """
    if not _SKILL_NAME_RE.match(skill_name or ""):
        return JSONResponse({"error": "非法技能名"}, status_code=400)
    if not rest or ".." in rest or rest.startswith("/") or "\\" in rest:
        return JSONResponse({"error": "非法路径"}, status_code=400)
    ext = os.path.splitext(rest)[1].lower()
    if ext not in _IMG_EXT:
        return JSONResponse({"error": "仅支持图片文件"}, status_code=400)
    fp = os.path.join(loader.SKILLS_DIR, skill_name, "output", rest)
    if not os.path.isfile(fp):
        return JSONResponse({"error": "文件不存在"}, status_code=404)
    return FileResponse(fp)
