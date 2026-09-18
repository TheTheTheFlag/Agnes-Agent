"""tests/test_auth_setup.py — 首次设置管理员 + 注册/审批/登录 的回归测试。

覆盖 app/server/auth.py（账号存于 app/server/accounts.py 的 SQLite）：
  - 未初始化时 /api/auth/status 返回 initialized=False，login 直接 401
  - /api/auth/setup 的输入校验（空账号 / 弱密码 / 两次不一致）
  - setup 创建管理员并以哈希落库（不出现明文密码）并顺带完成登录
  - 复用账号的登录成功/失败、重复 setup 被拒（409）
  - 注册：必须提供两个 Key；创建 pending；登录被 403 拦住
  - 管理员审批通过后可登录；拒绝后仍不可登录
  - 认证中间件拦截未登录的 /api/*，但放行 /api/auth/*
"""
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.server.accounts as accounts  # noqa: E402
import app.server.auth as auth  # noqa: E402

GOOD = {"username": "alice", "password": "secret123", "confirm": "secret123"}
REG = {
    "username": "bob", "password": "secret123", "confirm": "secret123",
    "agnes_key": "sk-agnes-test", "siliconflow_key": "sk-sf-test",
}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """每个用例一个独立 accounts.db（不碰真实 data/accounts.db 与旧 auth.json）。"""
    monkeypatch.setattr(accounts, "ACCOUNTS_DB_PATH", str(tmp_path / "accounts.db"))
    monkeypatch.setattr(accounts, "LEGACY_AUTH_FILE", str(tmp_path / "no_legacy.json"))
    monkeypatch.setattr(auth.keycheck, "validate_agnes", lambda k: ("ok", ""))
    monkeypatch.setattr(auth.keycheck, "validate_siliconflow", lambda k: ("ok", ""))
    accounts.init_db()

    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(auth.admin_router)
    auth.install_auth_middleware(app)

    @app.get("/api/ping")
    async def _ping():  # 受中间件保护的业务接口
        return {"pong": True}

    @app.get("/api/admin/ping")
    async def _admin_ping():  # 仅管理员的接口
        return {"admin": True}

    return TestClient(app)


def test_uninitialized_status_and_login_rejected(client):
    st = client.get("/api/auth/status").json()
    assert st["authenticated"] is False and st["initialized"] is False
    assert client.post("/api/auth/login", json={"username": "a", "password": "b"}).status_code == 401


def test_setup_validates_input(client):
    r = client.post("/api/auth/setup", json={"username": "alice", "password": "123", "confirm": "123"})
    assert r.status_code == 400 and "至少" in r.json()["error"]

    r = client.post("/api/auth/setup", json={"username": "  ", "password": "secret123", "confirm": "secret123"})
    assert r.status_code == 400 and "账号" in r.json()["error"]

    r = client.post("/api/auth/setup", json={"username": "alice", "password": "secret123", "confirm": "nope"})
    assert r.status_code == 400 and "不一致" in r.json()["error"]


def test_setup_creates_admin_and_logs_in(client, tmp_path):
    r = client.post("/api/auth/setup", json=GOOD)
    assert r.status_code == 200 and r.json()["username"] == "alice" and r.json()["role"] == "admin"

    raw = (tmp_path / "accounts.db").read_bytes()
    assert b"secret123" not in raw, "账号库不能出现明文密码"

    st = client.get("/api/auth/status").json()
    assert st["authenticated"] is True and st["username"] == "alice" and st["role"] == "admin"
    assert client.get("/api/ping").status_code == 200  # setup 后 cookie 已生效


def test_login_reuses_saved_credential(client):
    client.post("/api/auth/setup", json=GOOD)
    fresh = TestClient(client.app)  # 全新会话：只能靠库里的账号
    assert fresh.get("/api/auth/status").json()["authenticated"] is False
    assert fresh.post("/api/auth/login", json={"username": "alice", "password": "secret123"}).status_code == 200
    assert fresh.post("/api/auth/login", json={"username": "alice", "password": "wrong"}).status_code == 401
    assert fresh.post("/api/auth/login", json={"username": "bob", "password": "secret123"}).status_code == 401


def test_setup_rejected_once_initialized(client):
    assert client.post("/api/auth/setup", json=GOOD).status_code == 200
    r = client.post("/api/auth/setup", json={"username": "bob", "password": "other123", "confirm": "other123"})
    assert r.status_code == 409  # 已有账号时不允许覆盖


def test_register_requires_keys_and_creates_pending(client):
    r = client.post("/api/auth/register", json={"username": "bob", "password": "secret123", "confirm": "secret123"})
    assert r.status_code == 400 and "Agnes" in r.json()["error"]

    bad = {**REG, "agnes_key": ""}
    assert client.post("/api/auth/register", json=bad).status_code == 400

    r = client.post("/api/auth/register", json=REG)
    assert r.status_code == 200 and r.json()["status"] == "pending"

    # pending 不能登录
    fresh = TestClient(client.app)
    r = fresh.post("/api/auth/login", json={"username": "bob", "password": "secret123"})
    assert r.status_code == 403 and r.json()["status"] == "pending"


def test_admin_approve_then_login_and_reject(client):
    client.post("/api/auth/setup", json=GOOD)  # alice = admin
    client.post("/api/auth/register", json=REG)

    # 非管理员不能访问 admin 接口
    assert TestClient(client.app).get("/api/admin/users").status_code == 401

    users = client.get("/api/admin/users").json()["users"]
    assert any(u["username"] == "bob" and u["status"] == "pending" for u in users)
    assert client.get("/api/admin/ping").status_code == 200  # admin 放行

    r = client.post("/api/admin/users/bob/approve")
    assert r.status_code == 200 and r.json()["status"] == "active"

    fresh = TestClient(client.app)
    assert fresh.post("/api/auth/login", json={"username": "bob", "password": "secret123"}).status_code == 200

    # 拒绝另一个用户后仍不可登录
    client.post("/api/auth/register", json={**REG, "username": "carol"})
    assert client.post("/api/admin/users/carol/reject").status_code == 200
    fresh2 = TestClient(client.app)
    r = fresh2.post("/api/auth/login", json={"username": "carol", "password": "secret123"})
    assert r.status_code == 403 and r.json()["status"] == "rejected"


def test_middleware_blocks_api_without_cookie(client):
    assert client.get("/api/ping").status_code == 401
    assert client.get("/api/auth/status").status_code == 200  # auth 接口本身放行
