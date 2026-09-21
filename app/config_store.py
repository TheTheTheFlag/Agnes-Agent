"""app.config_store — 统一配置中心（data/.model_config 为唯一事实来源）。

历史上，除模型接入（provider/model/custom）写在 `.model_config` 外，其余配置
（Tavily / Embedding / Rerank / Neo4j / 向量后端 / 企业微信 / 公开基础地址等）
散落在项目根 `.env`。本模块把这些配置统一收进 `.model_config` 的顶层分节：

    {
      "provider": ..., "model": ..., "custom": [...],
      "embedding":   {"base_url","model"},
      "rerank":      {"base_url","model"},
      "tavily":      {},
      "graph":       {"storage","neo4j_uri","neo4j_username","neo4j_password",
                      "neo4j_database","cosine_threshold","namespace"},
      "vector_storage": "faiss" | "nano",
      "wechat":      {"enabled","welcome","prefix","timeout","concurrency","kbs"},
      "public_base_url": "...",
      "limits":      {"embedding_max_batch","embedding_max_tokens"},
      "auth":        {"username","password"}
    }

**密钥不入 .model_config**：所有密钥（用户 agnes/siliconflow key；管理员的
tavily / 企微 BotID+/Secret）都存放在 accounts.db（见 app.server.accounts）。
`migrate_keys_from_config_store()` 在启动时把旧配置里的密钥迁入 DB 并从配置剥除。

能力：
  - get_config()/update_config(patch)：读写（深合并）；
  - migrate_from_dotenv()：把 `.env` 中的值一次性并入 `.model_config`（幂等）；
  - apply_to_env()：把配置写回 os.environ，兼容仍用 os.getenv 读取的旧代码；
  - bootstrap()：启动时依次执行用户目录迁移、.env 迁移、密钥入 DB、DB→env。

`.model_config` 是权威来源；`.env` 仅作为历史回退（当分节缺值时）。
"""
from __future__ import annotations

import copy
import json
import os
import threading
from typing import Any, Dict, Optional, Tuple

from app.config import BASE_DIR, MODEL_CONFIG_PATH

_LOCK = threading.RLock()

ENV_FILE = os.path.join(BASE_DIR, ".env")

# 扩展分节默认值（仅补全缺失键；不覆盖文件已有内容）
# 注意：密钥轴（embedding.api_key / rerank.api_key / tavily.api_key / wechat.bot_id /
#       wechat.secret / custom[].api_key）已全部迁移到 accounts.db，这里不再保留。
DEFAULT_SECTIONS: Dict[str, Any] = {
    "embedding": {"base_url": "", "model": ""},
    "rerank": {"base_url": "", "model": ""},
    "tavily": {},
    "graph": {
        "storage": "",
        "neo4j_uri": "",
        "neo4j_username": "",
        "neo4j_password": "",
        "neo4j_database": "",
        "cosine_threshold": None,
        "namespace": "",
    },
    "vector_storage": "",
    "wechat": {
        "enabled": "",
        "welcome": "",
        "prefix": "",
        "timeout": "",
        "concurrency": "",
        "kbs": "",
    },
    "public_base_url": "",
    "limits": {"embedding_max_batch": None, "embedding_max_tokens": None},
    "auth": {"username": "", "password": ""},
}

# (分节, 键, 环境变量名)；键为 None 表示分节本身就是标量
# 密钥项（tavily.api_key / wechat.bot_id / wechat.secret / embedding.api_key /
#           rerank.api_key）= 已迁移进 accounts.db，由 apply_db_secrets_to_env() 处理。
_ENV_KEYS: Tuple[Tuple[str, Optional[str], str], ...] = (
    ("embedding", "base_url", "EMBEDDING_BASE_URL"),
    ("embedding", "model", "EMBEDDING_MODEL"),
    ("rerank", "base_url", "RERANK_BASE_URL"),
    ("rerank", "model", "RERANK_MODEL"),
    ("graph", "storage", "GRAPH_STORAGE"),
    ("graph", "neo4j_uri", "NEO4J_URI"),
    ("graph", "neo4j_username", "NEO4J_USERNAME"),
    ("graph", "neo4j_password", "NEO4J_PASSWORD"),
    ("graph", "neo4j_database", "NEO4J_DATABASE"),
    ("graph", "cosine_threshold", "GRAPH_COSINE_THRESHOLD"),
    ("graph", "namespace", "GRAPH_NAMESPACE"),
    ("vector_storage", None, "VECTOR_STORAGE"),
    ("wechat", "enabled", "WECHAT_BOT_ENABLED"),
    ("wechat", "welcome", "WECHAT_BOT_WELCOME"),
    ("wechat", "prefix", "WECHAT_BOT_THREAD_PREFIX"),
    ("wechat", "timeout", "WECHAT_BOT_TIMEOUT"),
    ("wechat", "concurrency", "WECHAT_BOT_CONCURRENCY"),
    ("wechat", "kbs", "WECHAT_BOT_KBS"),
    ("public_base_url", None, "AGNES_PUBLIC_BASE_URL"),
    ("limits", "embedding_max_batch", "EMBEDDING_MAX_BATCH"),
    ("limits", "embedding_max_tokens", "EMBEDDING_MAX_TOKENS"),
    ("auth", "username", "AGENT_USERNAME"),
    ("auth", "password", "AGENT_PASSWORD"),
)

# 设置面板允许编辑的分节（其余键如 provider/model/custom 由模型页管理）
EDITABLE_SECTIONS = ("embedding", "rerank", "tavily", "graph", "vector_storage",
                     "wechat", "public_base_url", "limits")

# 需要脱敏返回前端的键
_SECRET_HINTS = ("api_key", "password", "secret")


# ==================== 读取 / 写入 ====================
def _read_file() -> dict:
    try:
        with open(MODEL_CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        if isinstance(cfg, dict):
            return cfg
    except Exception:
        pass
    return {}


def _write_file(cfg: dict) -> None:
    os.makedirs(os.path.dirname(MODEL_CONFIG_PATH), exist_ok=True)
    with open(MODEL_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def _merge_defaults(cfg: dict) -> dict:
    """用 DEFAULT_SECTIONS 补齐缺失键（不改动已有值）。"""
    out = copy.deepcopy(cfg)
    for sec, default in DEFAULT_SECTIONS.items():
        if isinstance(default, dict):
            cur = out.get(sec)
            if not isinstance(cur, dict):
                cur = {}
            merged = copy.deepcopy(default)
            merged.update(cur)
            out[sec] = merged
        elif sec not in out:
            out[sec] = default
    return out


def get_config() -> dict:
    """返回补齐默认值后的完整配置。"""
    with _LOCK:
        return _merge_defaults(_read_file())


def _is_mask(v) -> bool:
    """判断是否为 mask_config 生成的脱敏值（形如 ****abcd），是则跳过不写入。"""
    if not isinstance(v, str) or not v.startswith("*") or "*" not in v:
        return False
    n = len(v)
    return v == ("*" * max(0, n - 4)) + v[-4:]


def update_config(patch: dict) -> dict:
    """把 patch 深合并进 .model_config 并落盘，返回更新后的完整配置。

    脱敏回显值（****xxxx）会被忽略，避免前端把掩码写回覆盖真实密钥。
    """
    if not isinstance(patch, dict):
        raise ValueError("patch 必须是对象")
    with _LOCK:
        cfg = _read_file()
        changed = False
        for sec, val in patch.items():
            if sec not in EDITABLE_SECTIONS:
                continue
            if isinstance(val, dict):
                cur = cfg.get(sec)
                if not isinstance(cur, dict):
                    cur = {}
                for k, v in val.items():
                    if v is None or _is_mask(v):
                        continue
                    if cur.get(k) != v:
                        cur[k] = v
                        changed = True
                cfg[sec] = cur
            else:
                if val is not None and not _is_mask(val) and cfg.get(sec) != val:
                    cfg[sec] = val
                    changed = True
        if changed:
            _write_file(cfg)
        return _merge_defaults(cfg)


def mask_config(cfg: Optional[dict] = None) -> dict:
    """返回脱敏配置（api_key/password/secret 只留尾部 4 位）。"""
    src = get_config() if cfg is None else cfg
    out = copy.deepcopy(src)

    def _mask(v):
        if not isinstance(v, str) or not v:
            return v
        return ("*" * max(0, len(v) - 4)) + v[-4:]

    def walk(d):
        if isinstance(d, dict):
            for k, v in d.items():
                if isinstance(v, (dict, list)):
                    walk(v)
                elif any(h in k for h in _SECRET_HINTS) and isinstance(v, str):
                    d[k] = _mask(v)
        elif isinstance(d, list):
            for it in d:
                walk(it)

    for sec in EDITABLE_SECTIONS:
        walk(out.get(sec))
    # custom 里的 provider key 也要脱敏
    for cm in out.get("custom", []) or []:
        if isinstance(cm, dict) and cm.get("api_key"):
            cm["api_key"] = _mask(cm["api_key"])
    return out


# ==================== .env 迁移 ====================
def _read_dotenv() -> Dict[str, str]:
    """读取项目根 .env（优先 python-dotenv，失败则手工解析）。"""
    vals: Dict[str, str] = {}
    try:
        from dotenv import dotenv_values
        for k, v in (dotenv_values(ENV_FILE) or {}).items():
            if v is not None and str(v).strip():
                vals[k] = str(v).strip()
        return vals
    except Exception:
        pass
    try:
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                v = v.strip().strip('"').strip("'")
                if v:
                    vals[k.strip()] = v
    except Exception:
        pass
    return vals


def migrate_from_dotenv() -> bool:
    """把 .env 中的已知键并入 .model_config（仅填充当前为空的项）。幂等。"""
    env_vals = _read_dotenv()
    if not env_vals:
        return False
    with _LOCK:
        cfg = _read_file()
        changed = False
        for section, key, env_name in _ENV_KEYS:
            val = env_vals.get(env_name)
            if not val:
                continue
            if key is None:
                if not cfg.get(section):
                    cfg[section] = val
                    changed = True
            else:
                sec = cfg.get(section)
                if not isinstance(sec, dict):
                    sec = {}
                if not sec.get(key):
                    sec[key] = val
                    changed = True
                cfg[section] = sec
        if changed:
            _write_file(cfg)
        return changed


# ==================== 应用到环境变量 ====================
def apply_to_env(cfg: Optional[dict] = None) -> None:
    """把配置写回 os.environ（非空值覆盖），兼容仍读 os.getenv 的旧代码。"""
    src = get_config() if cfg is None else cfg
    for section, key, env_name in _ENV_KEYS:
        if key is None:
            val = src.get(section)
        else:
            sec = src.get(section)
            val = sec.get(key) if isinstance(sec, dict) else None
        if val is None or val == "":
            continue
        os.environ[env_name] = str(val)


def _split(raw) -> List[str]:
    return [k.strip() for k in str(raw or "").split(",") if k.strip()]


def _strip_key(cfg: dict, sec: str, key: str) -> bool:
    secd = cfg.get(sec)
    if isinstance(secd, dict) and secd.get(key) is not None:
        secd[key] = ""
        return True
    return False


def migrate_keys_from_config_store() -> bool:
    """把 .model_config 里遗留的密钥一次性迁入 accounts.db，并从配置剥除。

    幂等：只填充 DB 中为空的字段；某项密钥只有确认已入 DB 后才从配置清空。
    返回是否发生了迁移/清理。必须在 accounts.init_db() 之后调用。
    """
    from app.server import accounts
    cfg = _read_file()
    changed_cfg = False

    # 1) 从配置提取候选密钥（缺失时兜底读 .env：密钥从 .env 直达 DB，不再进配置）
    env_vals = _read_dotenv()
    agnes = []
    for cm in cfg.get("custom", []) or []:
        if "agnes-ai.cn" in str((cm or {}).get("base_url") or ""):
            agnes += _split(cm.get("api_key"))
    sf = []
    for sec in ("embedding", "rerank"):
        sf += _split((cfg.get(sec) or {}).get("api_key"))
    if not sf:
        sf += _split(env_vals.get("EMBEDDING_API_KEY")) + _split(env_vals.get("RERANK_API_KEY"))
    cand_extra = {
        "tavily_api_key": ((cfg.get("tavily") or {}).get("api_key") or "").strip()
                          or (env_vals.get("TAVILY_API_KEY") or "").strip(),
        "wechat_bot_id": ((cfg.get("wechat") or {}).get("bot_id") or "").strip()
                         or (env_vals.get("WECHAT_BOT_ID") or "").strip(),
        "wechat_secret": ((cfg.get("wechat") or {}).get("secret") or "").strip()
                         or (env_vals.get("WECHAT_BOT_SECRET") or "").strip(),
    }

    # 2) 管理员（Mirror）行：缺失字段用配置值填充
    admin_row = None
    try:
        for u in accounts.list_users():
            if u.get("role") == "admin":
                admin_row = accounts.get_user(u["username"], include_secrets=True)
                break
    except Exception:
        admin_row = None

    if admin_row:
        uname = admin_row["username"]
        cur_agnes = _split(admin_row.get("agnes_key"))
        cur_sf = _split(admin_row.get("siliconflow_key"))
        cur_extra = admin_row.get("extra") or {}
        wrote = False
        if (not cur_agnes and agnes) or (not cur_sf and sf):
            want_agnes = ",".join(dict.fromkeys(cur_agnes + agnes))
            want_sf = ",".join(dict.fromkeys(cur_sf + sf))
            accounts.set_keys(uname, want_agnes, want_sf)
            wrote = True
        extra_patch = {k: v for k, v in cand_extra.items() if v and not cur_extra.get(k)}
        if extra_patch:
            merged = dict(cur_extra)
            merged.update(extra_patch)
            accounts.set_user_extra(uname, merged)
            wrote = True
        if wrote:
            accounts.apply_db_secrets_to_env()

    # 3) 剥除已入 DB 的配置密钥（缺 DB 值则保留，保证功能不中断）
    try:
        db_agnes = set(accounts.all_agnes_keys())
    except Exception:
        db_agnes = set()
    db_sf: set = set()
    db_extra: dict = {}
    try:
        if admin_row:
            fresh = accounts.get_user(admin_row["username"], include_secrets=True) or {}
            db_sf = set(_split(fresh.get("siliconflow_key")))
            db_extra = fresh.get("extra") or {}
    except Exception:
        pass

    for cm in cfg.get("custom", []) or []:
        if "agnes-ai.cn" in str((cm or {}).get("base_url") or "") and cm.get("api_key"):
            kk = _split(cm["api_key"])
            if kk and db_agnes and all(k in db_agnes for k in kk):
                cm["api_key"] = ""
                changed_cfg = True
    # embedding/rerank 剥除：配置里的 key 已全部进 db_sf 才清空
    for sec in ("embedding", "rerank"):
        secd = cfg.get(sec)
        if isinstance(secd, dict) and secd.get("api_key"):
            kk = _split(secd["api_key"])
            if kk and db_sf and all(k in db_sf for k in kk):
                secd["api_key"] = ""
                changed_cfg = True
    # tavily.api_key / wechat.bot_id / wechat.secret → 已写入 admin extra 才清空
    if cand_extra.get("tavily_api_key") and db_extra.get("tavily_api_key"):
        changed_cfg = _strip_key(cfg, "tavily", "api_key") or changed_cfg
    if cand_extra.get("wechat_bot_id") and db_extra.get("wechat_bot_id"):
        changed_cfg = _strip_key(cfg, "wechat", "bot_id") or changed_cfg
    if cand_extra.get("wechat_secret") and db_extra.get("wechat_secret"):
        changed_cfg = _strip_key(cfg, "wechat", "secret") or changed_cfg

    if changed_cfg:
        _write_file(cfg)
    return bool(admin_row)


def bootstrap() -> None:
    """启动时调用：迁移 .env → .model_config，密钥入 DB，并写入环境变量。

    同时把旧单用户数据迁移到 users/Mirror/（必须在 app.server 打开数据库前执行）。
    必须在 DB 打开前初始化 accounts 表并完成密钥迁移，否则 auth 导入时读到的
    全局密钥仍是旧配置路径。
    """
    try:
        from app.userctx import migrate_legacy_to_mirror
        _r = migrate_legacy_to_mirror()
        if any(v == "moved" for v in _r.values()):
            print(f"[config] 旧数据已迁移到 users/Mirror: "
                  f"{[k for k, v in _r.items() if v == 'moved']}")
    except Exception:
        pass
    try:
        migrate_from_dotenv()
    except Exception:
        pass
    # 密钥入 DB：必须先 init_db（accounts 表），再迁移 .model_config 遗留密钥，
    # 最后把 DB 里的服务级密钥（tavily / 企微）覆盖写回 env。
    try:
        from app.server import accounts
        accounts.init_db()
    except Exception:
        pass
    try:
        migrate_keys_from_config_store()
    except Exception:
        pass
    try:
        apply_to_env()
    except Exception:
        pass
    try:
        from app.server.accounts import apply_db_secrets_to_env
        apply_db_secrets_to_env()
    except Exception:
        pass
