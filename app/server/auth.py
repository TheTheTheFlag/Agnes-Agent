"""app.server.auth — 多用户登录校验 / 注册 / 管理员审批。

- 账号数据在 `app.server.accounts`（`data/accounts.db`），支持多用户、角色与审批状态。
- 角色：`admin`（管理员，可审批）/ `user`。状态：`pending` / `active` / `rejected`。
- 首次使用：`/api/auth/status` 返回 `initialized=False`，前端显示"设置管理员账号"；
  `/api/auth/setup` 创建**首个管理员**（此后 /setup 一律拒绝）。
- 开放注册：`/api/auth/register` 创建 `pending` 账号，需填写 Agnes 与硅基流动 Key，
  注册时做一次可用性校验；通过后等待管理员在面板审批。
- 登录：`/api/auth/login` 仅允许 `active`；pending/rejected 分别返回 403 + 原因。
- 登录态：随机 token 持久化到 sessions 表（服务重启后仍有效），httpOnly cookie 7 天。
- 管理员接口：`/api/admin/users*`（仅 admin），用于查看/审批/拒绝用户。
- 除 `/api/auth/*` 外的所有 `/api/*` 请求都需要有效 cookie，否则 401。
"""
from __future__ import annotations

import asyncio
import hmac
import os

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from app.server import accounts
from app.server import keycheck

ENV_USERNAME = os.getenv("AGENT_USERNAME")
ENV_PASSWORD = os.getenv("AGENT_PASSWORD")

COOKIE_NAME = "agnes_auth"
TOKEN_TTL_SECONDS = accounts.TOKEN_TTL_SECONDS
MIN_PASSWORD_LEN = accounts.MIN_PASSWORD_LEN

router = APIRouter(prefix="/api/auth", tags=["auth"])
admin_router = APIRouter(prefix="/api/admin", tags=["admin"])

accounts.init_db()


# ==================== 内部：登录态 ====================
def _set_auth_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME, token,
        max_age=TOKEN_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        path="/",
    )


def _env_login_matches(username: str, password: str) -> bool:
    return bool(
        ENV_USERNAME and ENV_PASSWORD
        and hmac.compare_digest(username, ENV_USERNAME)
        and hmac.compare_digest(password, ENV_PASSWORD)
    )


def _current_username(request: Request) -> str | None:
    return accounts.get_session_username(request.cookies.get(COOKIE_NAME))


def is_initialized() -> bool:
    return accounts.is_initialized()


def is_admin(username: str | None) -> bool:
    if not username:
        return False
    if ENV_USERNAME and hmac.compare_digest(username, ENV_USERNAME):
        return True
    return accounts.is_admin(username)


# ==================== 接口：状态 / 初始化 / 注册 / 登录 ====================
@router.post("/setup")
async def setup(payload: dict, response: Response):
    """首次设置**管理员**账号密码。已有任意账号则拒绝。"""
    if is_initialized():
        return JSONResponse(
            {"error": "系统已初始化；如需重置请删除 data/accounts.db 后刷新页面"},
            status_code=409,
        )
    p = payload or {}
    username = str(p.get("username") or "").strip()
    password = str(p.get("password") or "")
    confirm = str(p.get("confirm") if p.get("confirm") is not None else password)
    if not accounts.valid_username(username):
        return JSONResponse(
            {"error": f"账号需 {accounts.MIN_USERNAME_LEN}-{accounts.MAX_USERNAME_LEN} 位，仅限字母/数字/_-. 或中文"},
            status_code=400,
        )
    if len(password) < MIN_PASSWORD_LEN:
        return JSONResponse({"error": f"密码至少 {MIN_PASSWORD_LEN} 位"}, status_code=400)
    if password != confirm:
        return JSONResponse({"error": "两次输入的密码不一致"}, status_code=400)
    try:
        accounts.create_user(
            username, password, role=accounts.ROLE_ADMIN, status=accounts.STATUS_ACTIVE,
            note="首个管理员",
        )
    except ValueError:
        return JSONResponse({"error": "账号已存在"}, status_code=409)
    except Exception as e:
        return JSONResponse({"error": f"创建账号失败：{e}"}, status_code=500)
    _set_auth_cookie(response, accounts.issue_session(username))
    return {"ok": True, "username": username, "role": accounts.ROLE_ADMIN}


@router.post("/register")
async def register(payload: dict):
    """开放注册：需提供 Agnes 与硅基流动 Key；创建 pending 账号等待管理员审批。"""
    p = payload or {}
    username = str(p.get("username") or "").strip()
    password = str(p.get("password") or "")
    confirm = str(p.get("confirm") if p.get("confirm") is not None else password)
    agnes_key = str(p.get("agnes_key") or "").strip()
    siliconflow_key = str(p.get("siliconflow_key") or "").strip()

    if not accounts.valid_username(username):
        return JSONResponse(
            {"error": f"账号需 {accounts.MIN_USERNAME_LEN}-{accounts.MAX_USERNAME_LEN} 位，仅限字母/数字/_-. 或中文"},
            status_code=400,
        )
    if len(password) < MIN_PASSWORD_LEN:
        return JSONResponse({"error": f"密码至少 {MIN_PASSWORD_LEN} 位"}, status_code=400)
    if password != confirm:
        return JSONResponse({"error": "两次输入的密码不一致"}, status_code=400)
    if not agnes_key:
        return JSONResponse({"error": "请填写 Agnes API Key"}, status_code=400)
    if not siliconflow_key:
        return JSONResponse({"error": "请填写硅基流动 API Key"}, status_code=400)
    if accounts.get_user(username):
        return JSONResponse({"error": "用户名已存在"}, status_code=409)

    va, da = await asyncio.to_thread(keycheck.validate_agnes, agnes_key)
    if va == "invalid":
        return JSONResponse({"error": f"Agnes API Key 无效（{da}）"}, status_code=400)
    vs, ds = await asyncio.to_thread(keycheck.validate_siliconflow, siliconflow_key)
    if vs == "invalid":
        return JSONResponse({"error": f"硅基流动 API Key 无效（{ds}）"}, status_code=400)

    notes = []
    if va == "unknown":
        notes.append("Agnes Key 未能在线校验，待人工确认")
    if vs == "unknown":
        notes.append("硅基流动 Key 未能在线校验，待人工确认")
    try:
        accounts.create_user(
            username, password, agnes_key=agnes_key, siliconflow_key=siliconflow_key,
            role=accounts.ROLE_USER, status=accounts.STATUS_PENDING, note="；".join(notes),
        )
    except ValueError:
        return JSONResponse({"error": "用户名已存在"}, status_code=409)
    except Exception as e:
        return JSONResponse({"error": f"注册失败：{e}"}, status_code=500)
    return {
        "ok": True,
        "status": accounts.STATUS_PENDING,
        "message": "注册成功，等待管理员审批通过后即可登录。",
        "warnings": notes,
    }


@router.post("/login")
async def login(payload: dict, response: Response):
    u = str((payload or {}).get("username", ""))
    p = str((payload or {}).get("password", ""))

    if _env_login_matches(u, p):
        _set_auth_cookie(response, accounts.issue_session(u))
        return {"ok": True, "username": u, "role": accounts.ROLE_ADMIN}

    ok, reason, user = accounts.verify_login(u, p)
    if ok and user:
        _set_auth_cookie(response, accounts.issue_session(u))
        return {"ok": True, "username": u, "role": user["role"]}
    if reason == "pending":
        return JSONResponse({"error": "账号待管理员审批，请稍后再试", "status": "pending"}, status_code=403)
    if reason == "rejected":
        return JSONResponse({"error": "账号申请未通过", "status": "rejected"}, status_code=403)
    return JSONResponse({"error": "账号或密码错误"}, status_code=401)


@router.post("/logout")
async def logout(request: Request, response: Response):
    accounts.revoke_session(request.cookies.get(COOKIE_NAME))
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/status")
async def status(request: Request):
    username = _current_username(request)
    info = accounts.get_user(username) if username else None
    return {
        "authenticated": bool(username),
        "initialized": is_initialized(),
        "username": username or "",
        "role": (info or {}).get("role", ""),
        "status": (info or {}).get("status", ""),
    }


@router.get("/me")
async def me(request: Request):
    username = _current_username(request)
    if not username:
        return JSONResponse({"error": "未登录"}, status_code=401)
    info = accounts.get_user(username)
    return {"ok": True, "user": info}


# ==================== 接口：管理员审批 ====================
@admin_router.get("/users")
async def admin_list_users(request: Request):
    return {"ok": True, "users": accounts.list_users()}


@admin_router.post("/users/{username}/approve")
async def admin_approve(username: str, request: Request):
    if not accounts.get_user(username):
        return JSONResponse({"error": "用户不存在"}, status_code=404)
    accounts.set_status(username, accounts.STATUS_ACTIVE, by=_current_username(request) or "", note="审批通过")
    return {"ok": True, "username": username, "status": accounts.STATUS_ACTIVE}


@admin_router.post("/users/{username}/reject")
async def admin_reject(username: str, request: Request, payload: dict | None = None):
    if not accounts.get_user(username):
        return JSONResponse({"error": "用户不存在"}, status_code=404)
    note = str((payload or {}).get("note") or "审批未通过")
    accounts.set_status(username, accounts.STATUS_REJECTED, by=_current_username(request) or "", note=note)
    return {"ok": True, "username": username, "status": accounts.STATUS_REJECTED}


# ==================== 中间件 ====================
def install_auth_middleware(app):
    """保护除 /api/auth/* 外的所有 /api/* 路由；/api/admin/* 额外要求管理员。"""
    @app.middleware("http")
    async def _auth_middleware(request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/") and not path.startswith("/api/auth/"):
            username = _current_username(request)
            if not username:
                return JSONResponse({"error": "未登录或登录已过期"}, status_code=401)
            request.state.username = username
            if path.startswith("/api/admin/") and not is_admin(username):
                return JSONResponse({"error": "需要管理员权限"}, status_code=403)
        return await call_next(request)

    return _auth_middleware
