"""app.config_store — 统一配置中心（data/.model_config 为唯一事实来源）。

历史上，除模型接入（provider/model/custom）写在 `.model_config` 外，其余配置
（Tavily / Embedding / Rerank / Neo4j / 向量后端 / 企业微信 / 公开基础地址等）
散落在项目根 `.env`。本模块把这些配置统一收进 `.model_config` 的顶层分节：

    {
      "provider": ..., "model": ..., "custom": [...],
      "embedding":   {"base_url","api_key","model"},
      "rerank":      {"base_url","api_key","model"},
      "tavily":      {"api_key"},
      "graph":       {"storage","neo4j_uri","neo4j_username","neo4j_password",
                      "neo4j_database","cosine_threshold","namespace"},
      "vector_storage": "faiss" | "nano",
      "wechat":      {"bot_id","secret","enabled","welcome","prefix","timeout",
                      "concurrency","kbs"},
      "public_base_url": "...",
      "limits":      {"embedding_max_batch","embedding_max_tokens"},
      "auth":        {"username","password"}
    }

能力：
  - get_config()/update_config(patch)：读写（深合并）；
  - migrate_from_dotenv()：把 `.env` 中的值一次性并入 `.model_config`（幂等）；
  - apply_to_env()：把配置写回 os.environ，兼容仍用 os.getenv 读取的旧代码。

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
DEFAULT_SECTIONS: Dict[str, Any] = {
    "embedding": {"base_url": "", "api_key": "", "model": ""},
    "rerank": {"base_url": "", "api_key": "", "model": ""},
    "tavily": {"api_key": ""},
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
        "bot_id": "",
        "secret": "",
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
_ENV_KEYS: Tuple[Tuple[str, Optional[str], str], ...] = (
    ("tavily", "api_key", "TAVILY_API_KEY"),
    ("embedding", "base_url", "EMBEDDING_BASE_URL"),
    ("embedding", "api_key", "EMBEDDING_API_KEY"),
    ("embedding", "model", "EMBEDDING_MODEL"),
    ("rerank", "base_url", "RERANK_BASE_URL"),
    ("rerank", "api_key", "RERANK_API_KEY"),
    ("rerank", "model", "RERANK_MODEL"),
    ("graph", "storage", "GRAPH_STORAGE"),
    ("graph", "neo4j_uri", "NEO4J_URI"),
    ("graph", "neo4j_username", "NEO4J_USERNAME"),
    ("graph", "neo4j_password", "NEO4J_PASSWORD"),
    ("graph", "neo4j_database", "NEO4J_DATABASE"),
    ("graph", "cosine_threshold", "GRAPH_COSINE_THRESHOLD"),
    ("graph", "namespace", "GRAPH_NAMESPACE"),
    ("vector_storage", None, "VECTOR_STORAGE"),
    ("wechat", "bot_id", "WECHAT_BOT_ID"),
    ("wechat", "secret", "WECHAT_BOT_SECRET"),
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


def bootstrap() -> None:
    """启动时调用：迁移 .env → .model_config，并写入环境变量。

    同时把旧单用户数据迁移到 users/Mirror/（必须在 app.server 打开数据库前执行）。
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
    try:
        apply_to_env()
    except Exception:
        pass
