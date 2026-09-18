"""app.server.accounts — 多用户账号 / 角色 / 审批 / 登录态（SQLite）。

账号数据存放 `data/accounts.db`（不入版本库，Linux 下收紧为 0600）：
  users(username PK, salt, hash, role, status, agnes_key, siliconflow_key,
        created_at, approved_at, approved_by, note)
  sessions(token PK, username, expires_at, created_at)

- 角色 role：`admin`（管理员，如 Mirror，可审批用户）/ `user`。
- 状态 status：`pending`（待审批）/ `active`（已通过）/ `rejected`（已拒绝）。
  只有 `active` 能登录；pending/rejected 登录会返回明确的待审批/被拒原因。
- 密码：PBKDF2-SHA256（12 万轮 + 16 字节随机 salt），不落明文。
- 用户密钥（Agnes / 硅基流动）：仅服务端保存，任何列表/状态接口都**不返回**。
- 登录态：token 持久化到 sessions 表，服务重启后仍然有效（区别于旧的纯内存 token）。

首次启动时若 accounts.db 为空、而旧的 `data/auth.json` 存在，则把旧账号迁移为
`admin` / `active`，实现无感升级。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple

from app.config import DATA_DIR

ACCOUNTS_DB_PATH = os.path.join(DATA_DIR, "accounts.db")
LEGACY_AUTH_FILE = os.path.join(DATA_DIR, "auth.json")

PBKDF2_ROUNDS = 120_000
MIN_PASSWORD_LEN = 6
MIN_USERNAME_LEN = 2
MAX_USERNAME_LEN = 32
TOKEN_TTL_SECONDS = 7 * 24 * 3600  # 7 天

ROLE_ADMIN = "admin"
ROLE_USER = "user"
STATUS_PENDING = "pending"
STATUS_ACTIVE = "active"
STATUS_REJECTED = "rejected"
_VALID_STATUS = {STATUS_PENDING, STATUS_ACTIVE, STATUS_REJECTED}

# 用户名：字母/数字/下划线/连字符/点，或中文等 Unicode 字符；用于将来的用户目录名
_USERNAME_RE = re.compile(r"^[\w.\-]{%d,%d}$" % (MIN_USERNAME_LEN, MAX_USERNAME_LEN), re.UNICODE)


# ==================== 密码 ====================
def hash_password(password: str, salt: bytes) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return base64.b64encode(dk).decode("ascii")


def _new_salt() -> bytes:
    return secrets.token_bytes(16)


def valid_username(username: str) -> bool:
    return bool(_USERNAME_RE.match(username or ""))


# ==================== 连接 / 建表 ====================
def _connect() -> sqlite3.Connection:
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(ACCOUNTS_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


_DDL = """
CREATE TABLE IF NOT EXISTS users (
    username        TEXT PRIMARY KEY,
    salt            TEXT NOT NULL,
    hash            TEXT NOT NULL,
    role            TEXT NOT NULL DEFAULT 'user',
    status          TEXT NOT NULL DEFAULT 'pending',
    agnes_key       TEXT,
    siliconflow_key TEXT,
    created_at      TEXT,
    approved_at     TEXT,
    approved_by     TEXT,
    note            TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT PRIMARY KEY,
    username   TEXT NOT NULL,
    expires_at REAL NOT NULL,
    created_at TEXT
);
"""


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def init_db() -> None:
    """建表 + 迁移旧 auth.json（幂等，可重复调用）。"""
    try:
        with _connect() as conn:
            conn.executescript(_DDL)
    except Exception:
        return
    try:
        _migrate_legacy()
    except Exception:
        pass


def _migrate_legacy() -> None:
    """accounts 为空且存在旧 data/auth.json 时，把旧账号迁为 admin/active。"""
    with _connect() as conn:
        n = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        if n:
            return
    try:
        with open(LEGACY_AUTH_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return
    if not (isinstance(d, dict) and d.get("username") and d.get("salt") and d.get("hash")):
        return
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (username, salt, hash, role, status, created_at, approved_at, approved_by, note) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                str(d["username"]), str(d["salt"]), str(d["hash"]),
                ROLE_ADMIN, STATUS_ACTIVE, d.get("created_at") or _now(), _now(),
                str(d["username"]), "由旧版单账号自动迁移",
            ),
        )


# ==================== 用户读写 ====================
def _row_to_user(row: sqlite3.Row, include_secrets: bool = False) -> Dict[str, Any]:
    u = {
        "username": row["username"],
        "role": row["role"],
        "status": row["status"],
        "created_at": row["created_at"],
        "approved_at": row["approved_at"],
        "approved_by": row["approved_by"],
        "note": row["note"],
    }
    if include_secrets:
        u["agnes_key"] = row["agnes_key"]
        u["siliconflow_key"] = row["siliconflow_key"]
    return u


def get_user(username: str, include_secrets: bool = False) -> Optional[Dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    return _row_to_user(row, include_secrets) if row else None


def list_users() -> List[Dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM users ORDER BY (status='pending') DESC, created_at DESC"
        ).fetchall()
    return [_row_to_user(r) for r in rows]


def count_users() -> int:
    try:
        with _connect() as conn:
            return conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
    except Exception:
        return 0


def is_initialized() -> bool:
    """是否已有任意账号（首个账号即管理员）。"""
    return count_users() > 0


def create_user(
    username: str,
    password: str,
    agnes_key: str = "",
    siliconflow_key: str = "",
    role: str = ROLE_USER,
    status: str = STATUS_PENDING,
    note: str = "",
) -> Dict[str, Any]:
    """新建账号。用户名重复抛 ValueError。"""
    salt = _new_salt()
    with _connect() as conn:
        exists = conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone()
        if exists:
            raise ValueError("用户名已存在")
        conn.execute(
            "INSERT INTO users (username, salt, hash, role, status, agnes_key, siliconflow_key, created_at, note) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                username, base64.b64encode(salt).decode("ascii"), hash_password(password, salt),
                role, status, agnes_key or "", siliconflow_key or "", _now(), note,
            ),
        )
    try:
        from app.userctx import ensure_user_dirs
        ensure_user_dirs(username)
    except Exception:
        pass
    return get_user(username)


def verify_login(username: str, password: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """校验登录。

    返回 (ok, reason, user)：reason ∈ {"", "bad", "pending", "rejected", "disabled"}。
    只有 status=active 的账号可通过；待审批/被拒会返回对应原因供前端提示。
    """
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if not row:
        return False, "bad", None
    try:
        salt = base64.b64decode(row["salt"])
    except Exception:
        return False, "bad", None
    if not hmac.compare_digest(hash_password(password, salt), str(row["hash"])):
        return False, "bad", None
    status = row["status"]
    if status == STATUS_ACTIVE:
        return True, "", _row_to_user(row)
    if status == STATUS_PENDING:
        return False, "pending", _row_to_user(row)
    if status == STATUS_REJECTED:
        return False, "rejected", _row_to_user(row)
    return False, "disabled", _row_to_user(row)


def set_status(username: str, status: str, by: str = "", note: str = "") -> bool:
    if status not in _VALID_STATUS:
        raise ValueError(f"非法状态: {status}")
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE users SET status=?, approved_at=?, approved_by=?, note=? WHERE username=?",
            (status, _now() if status != STATUS_PENDING else None, by or "", note, username),
        )
        changed = cur.rowcount > 0
    if changed:
        _invalidate_user_caches(username)
        if status != STATUS_ACTIVE:
            revoke_user_sessions(username)  # 被拒/停用后立即失效
    return changed


def set_keys(username: str, agnes_key: str, siliconflow_key: str) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE users SET agnes_key=?, siliconflow_key=? WHERE username=?",
            (agnes_key or "", siliconflow_key or "", username),
        )
        changed = cur.rowcount > 0
    if changed:
        _invalidate_user_caches(username)
    return changed


def _invalidate_user_caches(username: str) -> None:
    """用户密钥/状态变化后失效其 LLM 与 graph 缓存。"""
    try:
        from app.userctx import clear_llm_cache
        clear_llm_cache(username)
    except Exception:
        pass
    try:
        from app.server import config as _cfg
        with _cfg._GRAPHS_LOCK:
            _cfg._GRAPHS.pop(username, None)
    except Exception:
        pass


def get_user_keys(username: str) -> Dict[str, str]:
    """仅服务端内部使用：取某用户的密钥。"""
    with _connect() as conn:
        row = conn.execute(
            "SELECT agnes_key, siliconflow_key FROM users WHERE username=?", (username,)
        ).fetchone()
    if not row:
        return {"agnes_key": "", "siliconflow_key": ""}
    return {"agnes_key": row["agnes_key"] or "", "siliconflow_key": row["siliconflow_key"] or ""}


def is_admin(username: str) -> bool:
    u = get_user(username)
    return bool(u and u["role"] == ROLE_ADMIN and u["status"] == STATUS_ACTIVE)


# ==================== 登录态（持久化） ====================
def issue_session(username: str, ttl: int = TOKEN_TTL_SECONDS) -> str:
    token = secrets.token_urlsafe(32)
    with _connect() as conn:
        conn.execute(
            "INSERT INTO sessions (token, username, expires_at, created_at) VALUES (?,?,?,?)",
            (token, username, time.time() + ttl, _now()),
        )
    return token


def get_session_username(token: Optional[str]) -> Optional[str]:
    if not token:
        return None
    with _connect() as conn:
        row = conn.execute("SELECT username, expires_at FROM sessions WHERE token=?", (token,)).fetchone()
        if not row:
            return None
        if float(row["expires_at"]) < time.time():
            conn.execute("DELETE FROM sessions WHERE token=?", (token,))
            return None
    return row["username"]


def revoke_session(token: Optional[str]) -> None:
    if not token:
        return
    with _connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token=?", (token,))


def revoke_user_sessions(username: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM sessions WHERE username=?", (username,))
