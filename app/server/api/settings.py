"""app.server.api.settings — 密钥读写作（所有用户）。

路径前缀 `/api/admin/settings`。分两部分：
  - accounts.db：所有密钥 —— 每登录用户只能读写**自己那一行**的 agnes / siliconflow
    （users 对应列）与 tavily / 企微 BotID/Secret（users.extra JSON），明文返回。
    `.model_config` 不再存任何密钥。
  - `.model_config`：非密钥配置（embedding/rerank 的 base_url+model、graph、wechat
    非密钥字段、limits 等），仅管理员可改，见 app.config_store。
前端「设置」页只显示 5 个密钥输入框（明文）；配置管理仅限管理员。
"""
import os
from typing import List, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app import config_store

router = APIRouter(prefix="/api/admin/settings", tags=["settings"])

# 管理员 DB 里可直接编辑的密钥列 / extra 键
_DB_KEY_FIELDS = ("agnes_key", "siliconflow_key")
_EXTRA_KEY_FIELDS = ("tavily_api_key", "wechat_bot_id", "wechat_secret")

# .model_config 分节里等同密钥的字段 → 写请求时重定向到 DB 的哪个目标
#   ("sf" → users.siliconflow_key；str → users.extra 里的键)
_CONFIG_SECRET_ROUTES = (
    ("embedding", "api_key", "sf"),
    ("rerank", "api_key", "sf"),
    ("tavily", "api_key", "tavily_api_key"),
    ("wechat", "bot_id", "wechat_bot_id"),
    ("wechat", "secret", "wechat_secret"),
)


def _split(raw) -> List[str]:
    return [k.strip() for k in str(raw or "").split(",") if k.strip()]


def _uniq(keys: List[str]) -> List[str]:
    out = []
    for k in keys:
        if k and k not in out:
            out.append(k)
    return out


def _is_admin_request(request: Optional[Request]) -> bool:
    user = getattr(getattr(request, "state", None), "username", None)
    if not user:
        return True
    try:
        from app.server.auth import is_admin
        return is_admin(user)
    except Exception:
        from app.server import accounts
        return accounts.is_admin(user)


def _db_keys_plain(username: str) -> dict:
    """当前用户自己那行密钥的明文视图（仅本人可见，供设置页明文显示）。"""
    from app.server import accounts
    full = accounts.get_user(username, include_secrets=True) or {}
    extra = full.get("extra") or {}
    out = {}
    for k in _DB_KEY_FIELDS:
        out[k] = str(full.get(k) or "")
    for k in _EXTRA_KEY_FIELDS:
        out[k] = str(extra.get(k) or "")
    return out


@router.get("")
async def get_settings(request: Request = None):
    from app.userctx import DEFAULT_USER
    username = getattr(getattr(request, "state", None), "username", None) or DEFAULT_USER
    return {
        "ok": True,
        "config": config_store.mask_config(),
        "editable": list(config_store.EDITABLE_SECTIONS),
        "env_file_present": os.path.isfile(config_store.ENV_FILE),
        "db_keys": _db_keys_plain(username),
    }


def _apply_db_update(username: str, patch_config: dict, db_keys: dict) -> None:
    """把 config 分节里的密钥字段 + db_keys 显式字段合并写入管理员 DB 行。"""
    from app.server import accounts
    full = accounts.get_user(username, include_secrets=True) or {}
    cur_extra = dict(full.get("extra") or {})
    agnes = _split(full.get("agnes_key"))
    sf = _split(full.get("siliconflow_key"))
    extra = dict(cur_extra)

    # 1) config 分节密钥 → DB（同时对 patch_config 就地剥除）
    for sec, cfgkey, dest in _CONFIG_SECRET_ROUTES:
        secd = patch_config.get(sec)
        if not isinstance(secd, dict):
            continue
        raw = secd.get(cfgkey)
        if not isinstance(raw, str) or config_store._is_mask(raw):
            secd.pop(cfgkey, None)
            continue
        secd.pop(cfgkey, None)
        val = raw.strip()
        if dest == "sf":
            sf = _split(val) if val else []
        else:
            if val:
                extra[dest] = val
            else:
                extra.pop(dest, None)
    # 2) custom 列表里 agnes 网关的 api_key → 管理员 agnes_key（剥除 + 替换）
    custom = patch_config.get("custom")
    if isinstance(custom, list):
        for cm in custom:
            if not isinstance(cm, dict) or "agnes-ai.cn" not in str(cm.get("base_url") or ""):
                continue
            raw = cm.get("api_key")
            if isinstance(raw, str) and not config_store._is_mask(raw):
                val = raw.strip()
                agnes = _split(val) if val else []
            cm["api_key"] = ""
    # 3) db_keys 显式字段（掩码=不改动；空串=清空；新值=替换）
    for k in _DB_KEY_FIELDS:
        if k not in db_keys:
            continue
        raw = db_keys[k]
        if not isinstance(raw, str) or config_store._is_mask(raw):
            continue
        if k == "agnes_key":
            agnes = _split(raw)
        else:
            sf = _split(raw)
    for k in _EXTRA_KEY_FIELDS:
        if k not in db_keys:
            continue
        raw = db_keys[k]
        if not isinstance(raw, str) or (config_store._is_mask(raw) and not raw.strip()):
            continue
        if config_store._is_mask(raw):
            continue
        val = raw.strip()
        if val:
            extra[k] = val
        else:
            extra.pop(k, None)

    accounts.set_keys(username, ",".join(_uniq(agnes)), ",".join(_uniq(sf)))
    accounts.set_user_extra(username, extra)


@router.post("")
async def update_settings(payload: dict, request: Request = None):
    payload = payload or {}
    patch = (payload.get("config") or {}) if isinstance(payload.get("config"), dict) else None
    db_keys = payload.get("db_keys") or {}
    if not isinstance(db_keys, dict):
        db_keys = {}
    admin = _is_admin_request(request)
    username = getattr(getattr(request, "state", None), "username", None)
    from app.userctx import DEFAULT_USER
    username = username or DEFAULT_USER
    if not admin:
        # 非管理员：只能保存自己那行的 db_keys；任何 config 变更均拒绝
        if patch and any(patch.values()):
            return JSONResponse({"error": "仅管理员可修改全局配置"}, status_code=403)
        patch = {}
    if not db_keys and not (patch and any(patch.values())):
        return JSONResponse({"error": "没有可保存的内容"}, status_code=400)
    try:
        # 密钥字段先落 DB（每个用户自己的行），其余（非密钥）配置仅管理员走 .model_config
        _apply_db_update(username, patch, db_keys)
        if admin:
            config_store.update_config(patch)
            config_store.apply_to_env()
            from app.server.accounts import apply_db_secrets_to_env
            apply_db_secrets_to_env()
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
        add_log_entry("info", f"设置已更新（/api/admin/settings, user={username}）")
    except Exception:
        pass
    return {
        "ok": True,
        "config": config_store.mask_config(),
        "db_keys": _db_keys_plain(username),
        "note": "已保存到 accounts.db。",
    }