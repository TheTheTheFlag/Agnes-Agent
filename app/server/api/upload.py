"""app.server.api.upload — 文件上传 API。

用户通过对话面板上传图片/文件，统一存放到项目根 `uploads/` 目录，
Agent 可直接访问该目录下的文件（如视觉技能读取图片）。
受登录中间件保护。
"""
import os
import re
import uuid

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from app.config import BASE_DIR

UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
MAX_SIZE = 20 * 1024 * 1024  # 20MB

# 允许的扩展名：图片为主（视觉技能），辅以常见文档
_ALLOWED_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg",
    ".pdf", ".txt", ".md", ".json", ".csv", ".html",
    ".docx", ".pptx", ".xlsx", ".xls", ".epub", ".ipynb",
}
_EXT_RE = re.compile(r"^[A-Za-z0-9]{1,10}$")

router = APIRouter(prefix="/api", tags=["upload"])

os.makedirs(UPLOAD_DIR, exist_ok=True)


@router.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    """接收上传文件，保存到 uploads/ 并返回项目内相对路径。payload: multipart form, 字段名 file"""
    orig = (file.filename or "file").replace("\\", "/").split("/")[-1]
    ext = os.path.splitext(orig)[1].lower()
    if ext not in _ALLOWED_EXT:
        return JSONResponse({"error": f"不支持的文件类型: {ext or '(无扩展名)'}"}, status_code=400)
    content = await file.read()
    if not content:
        return JSONResponse({"error": "文件为空"}, status_code=400)
    if len(content) > MAX_SIZE:
        return JSONResponse({"error": f"文件超过大小限制（{MAX_SIZE // 1024 // 1024}MB）"}, status_code=400)
    # 文件名用随机短 id 保留扩展名：防覆盖/防路径穿越
    name = f"up_{uuid.uuid4().hex[:12]}{ext}"
    with open(os.path.join(UPLOAD_DIR, name), "wb") as f:
        f.write(content)
    return {
        "ok": True,
        "path": f"uploads/{name}",          # 相对项目根：agent 可直接访问，前端渲染时转 /api/uploads/
        "name": orig,
        "size": len(content),
        "content_type": file.content_type,
    }


@router.get("/uploads/{name}")
async def get_upload(name: str):
    """serve uploads/ 下的文件（前端 <img> 渲染、下载）。"""
    safe = os.path.basename((name or "").replace("\\", "/"))
    fp = os.path.join(UPLOAD_DIR, safe)
    if not os.path.isfile(fp):
        return JSONResponse({"error": "文件不存在"}, status_code=404)
    return FileResponse(fp)
