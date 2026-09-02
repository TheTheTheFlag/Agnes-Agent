"""app.server.auth — 调试面板登录校验。

只允许指定账号登录，其余 API 一律 401：
  - 凭据默认 Mirror / Jxh1997.，可用 .env 覆盖（AGENT_USERNAME / AGENT_PASSWORD）；
  - 登录成功签发随机 token（内存存储，服务重启后失效需重新登录），
    以 httpOnly cookie 下发（7 天有效）；
  - 除 /api/auth/* 外的所有 /api/* 请求都需携带有效 cookie，否则 401。

注意：token 存内存，多 worker（uvicorn --workers）下各自独立，登录态可能漂移；
本项目单进程运行（含 reloader 子进程单实例），无此问题。
"""
import os
import secrets
import time

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

# 凭据：优先 .env，缺省用内置默认
USERNAME = os.getenv("AGENT_USERNAME", "Mirror")
PASSWORD = os.getenv("AGENT_PASSWORD", "Jxh1997.")

COOKIE_NAME = "agnes_auth"
TOKEN_TTL_SECONDS = 7 * 24 * 3600  # 7 天

# token -> {"username": str, "expires_at": float}
_TOKENS: dict[str, dict] = {}

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _issue_token() -> str:
    token = secrets.token_urlsafe(32)
    _TOKENS[token] = {"username": USERNAME, "expires_at": time.time() + TOKEN_TTL_SECONDS}
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


@router.post("/login")
async def login(payload: dict, response: Response):
    u = (payload or {}).get("username", "")
    p = (payload or {}).get("password", "")
    if u == USERNAME and p == PASSWORD:
        token = _issue_token()
        response.set_cookie(
            COOKIE_NAME, token,
            max_age=TOKEN_TTL_SECONDS,
            httponly=True,
            samesite="lax",
            path="/",
        )
        return {"ok": True}
    return JSONResponse({"error": "账号或密码错误"}, status_code=401)


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/status")
async def status(request: Request):
    token = request.cookies.get(COOKIE_NAME)
    return {"authenticated": _valid_token(token)}


def install_auth_middleware(app):
    """保护除 /api/auth/* 之外的所有 /api/* 路由。
    页面 / 与 /static/* 不拦截（前端据此显示登录界面）。"""
    @app.middleware("http")
    async def _auth_middleware(request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/") and not path.startswith("/api/auth/"):
            token = request.cookies.get(COOKIE_NAME)
            if not _valid_token(token):
                return JSONResponse({"error": "未登录或登录已过期"}, status_code=401)
        return await call_next(request)

    return _auth_middleware
