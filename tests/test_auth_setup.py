"""tests/test_auth_setup.py — 首次设置凭据 + 登录校验的回归测试。

覆盖 app/server/auth.py 的关键行为：
  - 未初始化时 /api/auth/status 返回 initialized=False，login 直接 401
  - /api/auth/setup 的输入校验（空账号 / 弱密码 / 两次不一致）
  - setup 成功后凭据以 JSON 落到 AUTH_FILE（不含明文密码）并顺带完成登录
  - 复用磁盘凭据的登录成功/失败、重复 setup 被拒（409）
  - 认证中间件拦截未登录的 /api/*，但放行 /api/auth/*
"""
import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.server.auth as auth  # noqa: E402

GOOD = {"username": "alice", "password": "secret123", "confirm": "secret123"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """每个用例一个独立凭据文件 + 全新的 FastAPI app（不碰真实 data/auth.json）。"""
    monkeypatch.setattr(auth, "AUTH_FILE", str(tmp_path / "auth.json"))
    monkeypatch.setattr(auth, "_TOKENS", {})
    app = FastAPI()
    app.include_router(auth.router)
    auth.install_auth_middleware(app)

    @app.get("/api/ping")
    async def _ping():  # 受中间件保护的业务接口
        return {"pong": True}

    return TestClient(app)


def test_uninitialized_status_and_login_rejected(client):
    assert client.get("/api/auth/status").json() == {"authenticated": False, "initialized": False}
    r = client.post("/api/auth/login", json={"username": "a", "password": "b"})
    assert r.status_code == 401


def test_setup_validates_input(client):
    r = client.post("/api/auth/setup", json={"username": "alice", "password": "123", "confirm": "123"})
    assert r.status_code == 400 and "至少" in r.json()["error"]

    r = client.post("/api/auth/setup", json={"username": "  ", "password": "secret123", "confirm": "secret123"})
    assert r.status_code == 400 and "账号" in r.json()["error"]

    r = client.post("/api/auth/setup", json={"username": "alice", "password": "secret123", "confirm": "nope"})
    assert r.status_code == 400 and "不一致" in r.json()["error"]


def test_setup_writes_hashed_credential_and_logs_in(client, tmp_path):
    r = client.post("/api/auth/setup", json=GOOD)
    assert r.status_code == 200 and r.json()["username"] == "alice"

    raw = (tmp_path / "auth.json").read_text(encoding="utf-8")
    assert "secret123" not in raw, "凭据文件不能出现明文密码"
    data = json.loads(raw)
    for key in ("username", "salt", "hash", "algo", "created_at"):
        assert key in data

    assert client.get("/api/auth/status").json() == {"authenticated": True, "initialized": True}
    assert client.get("/api/ping").status_code == 200  # setup 后 cookie 已生效


def test_login_reuses_saved_credential(client):
    client.post("/api/auth/setup", json=GOOD)
    fresh = TestClient(client.app)  # 全新会话：只能靠磁盘上的凭据
    assert fresh.get("/api/auth/status").json()["authenticated"] is False
    assert fresh.post("/api/auth/login", json={"username": "alice", "password": "secret123"}).status_code == 200
    assert fresh.post("/api/auth/login", json={"username": "alice", "password": "wrong"}).status_code == 401
    assert fresh.post("/api/auth/login", json={"username": "bob", "password": "secret123"}).status_code == 401
    assert fresh.get("/api/auth/status").json()["authenticated"] is True


def test_setup_rejected_once_initialized(client):
    assert client.post("/api/auth/setup", json=GOOD).status_code == 200
    r = client.post("/api/auth/setup", json={"username": "bob", "password": "other123", "confirm": "other123"})
    assert r.status_code == 409  # 已有凭据时不允许覆盖


def test_middleware_blocks_api_without_cookie(client):
    assert client.get("/api/ping").status_code == 401
    assert client.get("/api/auth/status").status_code == 200  # auth 接口本身放行
