"""app.server.api.settings — 全局配置读写（仅管理员）。

路径前缀 `/api/admin/settings`，由认证中间件统一要求管理员权限。
数据来源/落盘均为 `data/.model_config`（见 app.config_store）。
"""
import os

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app import config_store

router = APIRouter(prefix="/api/admin/settings", tags=["settings"])


@router.get("")
async def get_settings():
    return {
        "ok": True,
        "config": config_store.mask_config(),
        "editable": list(config_store.EDITABLE_SECTIONS),
        "env_file_present": os.path.isfile(config_store.ENV_FILE),
    }


@router.post("")
async def update_settings(payload: dict):
    patch = (payload or {}).get("config")
    if not isinstance(patch, dict):
        return JSONResponse({"error": "缺少 config 对象"}, status_code=400)
    try:
        config_store.update_config(patch)
        config_store.apply_to_env()
        # Embedding/Rerank/网关变更会影响每用户 LLM/RAG：清缓存，下次请求按新配置重建
        from app.userctx import clear_llm_cache
        clear_llm_cache()
        from app.server import config as srv_cfg
        srv_cfg.clear_user_graphs()
        try:
            from app.memory.ligraphrag_adapter import reset_all as _reset_rag
            _reset_rag()
        except Exception:
            pass
    except Exception as e:
        return JSONResponse({"error": f"保存失败：{e}"}, status_code=500)
    try:
        from app.server.store import add_log_entry
        add_log_entry("info", "全局配置已更新（/api/admin/settings）")
    except Exception:
        pass
    return {
        "ok": True,
        "config": config_store.mask_config(),
        "note": "已保存并写入环境变量；Embedding/Rerank/企业微信等在下一次重建或重启后完全生效。",
    }
