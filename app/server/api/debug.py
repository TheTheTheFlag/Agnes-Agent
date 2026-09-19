"""调试API - 接收前端日志"""
from fastapi import APIRouter, Request, Query
from datetime import datetime
import os

router = APIRouter(prefix="/api/debug", tags=["debug"])

LOG_FILE = "/tmp/video_ref_upload.log"

@router.get("/log")
async def log_message(msg: str = Query(...)):
    """接收前端日志并写入文件"""
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        log_line = f"[{timestamp}] {msg}\n"
        
        with open(LOG_FILE, "a") as f:
            f.write(log_line)
        
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@router.get("/logs")
async def get_logs(lines: int = Query(100)):
    """读取日志文件"""
    try:
        if not os.path.exists(LOG_FILE):
            return {"logs": []}
        
        with open(LOG_FILE, "r") as f:
            content = f.read()
        
        log_lines = content.strip().split("\n")
        return {"logs": log_lines[-lines:]}
    except Exception as e:
        return {"error": str(e)}
