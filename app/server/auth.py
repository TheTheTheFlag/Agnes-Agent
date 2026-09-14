"""app.server.auth — 调试面板登录校验。

凭据在**首次打开面板时由使用者自己设置**，存放在 `data/auth.json`（不入版本库）：
  - `/api/auth/status` 带回 `initialized` 字段；未初始化时前端显示"设置账号密码"表单，
    提交到 `/api/auth/setup` 完成初始化（完成后直接登录，不必再输一遍）；
  - 密码以 PBKDF2-SHA256（12 万轮 + 16 字节随机 salt）存储，不落明文；
  - 仍可用 `.env` 的 `AGENT_USERNAME` / `AGENT_PASSWORD` 覆盖（两者同时存在时优先），
    便于 CI / 无人值守部署；
  - 登录成功签发随机 token（内存存储，服务重启后失效需重新登录），以 httpOnly cookie 下发（7 天有效）；
  - 除 `/api/auth/*` 外的所有 `/api/*` 请求都需携带有效 cookie，否则 401。

重置账号：删掉 `data/auth.json`，刷新面板即可重新设置。

注意：token 存内存，多 worker（uvicorn --workers）下各自独立，登录态可能漂移；
本项目单进程运行（含 reloader 子进程单实例），无此问题。
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from app.config import DATA_DIR

# 凭据文件：首次设置时写入（已由 .gitignore 忽略）
AUTH_FILE = os.path.join(DATA_DIR, "auth.json")

# .env 覆盖：两个变量都提供时才生效
ENV_USERNAME = os.getenv("AGENT_USERNAME")
ENV_PASSWORD = os.getenv("AGENT_PASSWORD")

COOKIE_NAME = "agnes_auth"
TOKEN_TTL_SECONDS = 7 * 24 * 3600  # 7 天
PBKDF2_ROUNDS = 120_000
MIN_PASSWORD_LEN = 6

# token -> {"username": str, "expires_at": float}
_TOKENS: dict[str, dict] = {}

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ==================== 凭据读写 ====================
def _hash_password(password: str, salt: bytes) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return base64.b64encode(dk).decode("ascii")


def load_credential() -> dict | None:
    """读取 data/auth.json；文件不存在或结构损坏时返回 None。"""
    try:
        with open(AUTH_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and data.get("username") and data.get("salt") and data.get("hash"):
            return data
    except Exception:
        pass
    return None


def is_initialized() -> bool:
    """是否已能登录：.env 覆盖存在，或 data/auth.json 已设置。"""
    return bool(ENV_USERNAME and ENV_PASSWORD) or load_credential() is not None


def save_credential(username: str, password: str) -> None:
    """原子写入 data/auth.json（先写 .tmp 再 replace，避免半截文件导致再也登不上）。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    salt = secrets.token_bytes(16)
    payload = {
        "username": username,
        "salt": base64.b64encode(salt).decode("ascii"),
        "hash": _hash_password(password, salt),
        "algo": f"pbkdf2_sha256:{PBKDF2_ROUNDS}",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    tmp = AUTH_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, AUTH_FILE)
    try:
        os.chmod(AUTH_FILE, 0o600)  # Windows 上无效，Linux 上收紧权限
    except Exception:
        pass


def verify_credential(username: str, password: str) -> bool:
    if ENV_USERNAME and ENV_PASSWORD:
        return (hmac.compare_digest(username, ENV_USERNAME)
                and hmac.compare_digest(password, ENV_PASSWORD))
    cred = load_credential()
    if not cred or not hmac.compare_digest(username, str(cred.get("username", ""))):
        return False
    try:
        salt = base64.b64decode(cred["salt"])
    except Exception:
        return False
    return hmac.compare_digest(_hash_password(password, salt), str(cred.get("hash", "")))


# ==================== 登录态 ====================
def _issue_token(username: str) -> str:
    token = secrets.token_urlsafe(32)
    _TOKENS[token] = {"username": username, "expires_at": time.time() + TOKEN_TTL_SECONDS}
    return token


def _valid_token(token: str | None) -> bool:
    if not token:
        return False
    entry = _TOKENS.get(token)
    if not entry:
        return False
    if entry["expires_at"] < time.time():
        _TOKENS.pop(token, None)
        return False
    return True


def _set_auth_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME, token,
        max_age=TOKEN_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        path="/",
    )


# ==================== 接口 ====================
@router.post("/setup")
async def setup(payload: dict, response: Response):
    """首次设置账号密码。已初始化则拒绝（避免误改掉现有凭据）。"""
    if is_initialized():
        return JSONResponse(
            {"error": "账号已初始化；如需重置请删除 data/auth.json 后刷新页面"},
            status_code=409,
        )
    p = payload or {}
    username = str(p.get("username") or "").strip()
    password = str(p.get("password") or "")
    confirm = str(p.get("confirm") if p.get("confirm") is not None else password)
    if not username:
        return JSONResponse({"error": "账号不能为空"}, status_code=400)
    if len(password) < MIN_PASSWORD_LEN:
        return JSONResponse({"error": f"密码至少 {MIN_PASSWORD_LEN} 位"}, status_code=400)
    if password != confirm:
        return JSONResponse({"error": "两次输入的密码不一致"}, status_code=400)
    try:
        save_credential(username, password)
    except Exception as e:
        return JSONResponse({"error": f"写入 {AUTH_FILE} 失败：{e}"}, status_code=500)
    _set_auth_cookie(response, _issue_token(username))  # 设置完直接登录
    return {"ok": True, "username": username}


@router.post("/login")
async def login(payload: dict, response: Response):
    u = str((payload or {}).get("username", ""))
    p = str((payload or {}).get("password", ""))
    if verify_credential(u, p):
        _set_auth_cookie(response, _issue_token(u))
        return {"ok": True}
    return JSONResponse({"error": "账号或密码错误"}, status_code=401)


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/status")
async def status(request: Request):
    token = request.cookies.get(COOKIE_NAME)
    return {"authenticated": _valid_token(token), "initialized": is_initialized()}


def install_auth_middleware(app):
    """保护除 /api/auth/* 之外的所有 /api/* 路由。
    页面 / 与 /static/* 不拦截（前端据此显示登录/设置界面）。"""
    @app.middleware("http")
    async def _auth_middleware(request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/") and not path.startswith("/api/auth/"):
            token = request.cookies.get(COOKIE_NAME)
            if not _valid_token(token):
                return JSONResponse({"error": "未登录或登录已过期"}, status_code=401)
        return await call_next(request)

    return _auth_middleware
