"""tests/test_config_store.py — 统一配置中心（.env → data/.model_config）回归测试。

密钥已迁入 accounts.db：.env/配置里的密钥不再进入 .model_config；
`migrate_keys_from_config_store()` 负责一次性把旧密钥迁入 DB 并从配置剥除。
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.config_store as cs  # noqa: E402


@pytest.fixture()
def store(tmp_path, monkeypatch):
    cfg_path = tmp_path / ".model_config"
    env_path = tmp_path / ".env"
    cfg_path.write_text(json.dumps({"provider": "custom-agnes", "model": "agnes-2.5-flash",
                                    "custom": []}, ensure_ascii=False), encoding="utf-8")
    env_path.write_text(
        "TAVILY_API_KEY=tvly-abcdefghijkl\n"
        "EMBEDDING_BASE_URL=https://api.siliconflow.cn/v1\n"
        "EMBEDDING_API_KEY=sk-embed-secret-1234\n"
        "EMBEDDING_MODEL=BAAI/bge-m3\n"
        "GRAPH_STORAGE=neo4j\n"
        "NEO4J_PASSWORD=neo4jpass\n"
        "VECTOR_STORAGE=faiss\n"
        "WECHAT_BOT_ID=bot123\n"
        "WECHAT_BOT_SECRET=sec456\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cs, "MODEL_CONFIG_PATH", str(cfg_path))
    monkeypatch.setattr(cs, "ENV_FILE", str(env_path))
    for k in ("TAVILY_API_KEY", "EMBEDDING_BASE_URL", "EMBEDDING_API_KEY",
              "EMBEDDING_MODEL", "GRAPH_STORAGE", "NEO4J_PASSWORD",
              "VECTOR_STORAGE", "WECHAT_BOT_ID", "WECHAT_BOT_SECRET"):
        monkeypatch.delenv(k, raising=False)
    return cs


@pytest.fixture()
def accounts_tmp(tmp_path, monkeypatch):
    """临时 accounts.db + 用户目录，避免污染真实 data/。"""
    from app.server import accounts
    from app import userctx
    monkeypatch.setattr(accounts, "ACCOUNTS_DB_PATH", str(tmp_path / "accounts.db"))
    monkeypatch.setattr(accounts, "LEGACY_AUTH_FILE", str(tmp_path / "auth.json"))
    monkeypatch.setattr(userctx, "USERS_DIR", str(tmp_path / "users"))
    accounts.init_db()
    return accounts


def _keys_cleanup(monkeypatch):
    import os
    for k in ("TAVILY_API_KEY", "WECHAT_BOT_ID", "WECHAT_BOT_SECRET",
              "EMBEDDING_API_KEY", "RERANK_API_KEY"):
        monkeypatch.delenv(k, raising=False)


def test_migrate_from_dotenv_moves_non_secret(store):
    assert store.migrate_from_dotenv() is True
    cfg = store.get_config()
    # 非密钥字段照常迁移
    assert cfg["embedding"]["base_url"] == "https://api.siliconflow.cn/v1"
    assert cfg["embedding"]["model"] == "BAAI/bge-m3"
    assert cfg["graph"]["storage"] == "neo4j"
    assert cfg["graph"]["neo4j_password"] == "neo4jpass"
    assert cfg["vector_storage"] == "faiss"
    # 密钥不再进入 .model_config
    assert not (cfg["embedding"].get("api_key") or "")
    assert not (cfg["tavily"].get("api_key") or "")
    assert not (cfg["wechat"].get("bot_id") or "")
    assert not (cfg["wechat"].get("secret") or "")
    # 第二次不再改动
    assert store.migrate_from_dotenv() is False


def test_apply_to_env_skips_secrets(store, monkeypatch):
    _keys_cleanup(monkeypatch)
    store.migrate_from_dotenv()
    store.apply_to_env()
    import os
    assert os.environ["EMBEDDING_BASE_URL"] == "https://api.siliconflow.cn/v1"
    assert os.environ["EMBEDDING_MODEL"] == "BAAI/bge-m3"
    assert os.environ["GRAPH_STORAGE"] == "neo4j"
    assert os.environ["NEO4J_PASSWORD"] == "neo4jpass"
    # 密钥由 DB 负责注入 env，config 不再导出
    assert "EMBEDDING_API_KEY" not in os.environ
    assert "TAVILY_API_KEY" not in os.environ
    assert "WECHAT_BOT_ID" not in os.environ
    assert "WECHAT_BOT_SECRET" not in os.environ


def test_update_config_merges_and_filters(store):
    store.update_config({"embedding": {"model": "new-model"}, "provider": "HACK",
                         "vector_storage": "nano"})
    cfg = store.get_config()
    assert cfg["embedding"]["model"] == "new-model"
    assert cfg["vector_storage"] == "nano"
    # 非可编辑键不允许通过 settings 改动
    assert cfg["provider"] == "custom-agnes"


def test_mask_config_hides_secrets(store):
    store.migrate_from_dotenv()
    masked = store.mask_config()
    # embedding.api_key 已迁出配置 → 不再出现明文；graph 密码照常脱敏
    assert not (masked["embedding"].get("api_key") or "")
    assert masked["graph"]["neo4j_password"].endswith("pass")
    assert masked["graph"]["neo4j_password"].startswith("*")
    assert "oncpass" not in masked["graph"]["neo4j_password"]


def test_migrate_keys_seeds_admin_db(store, accounts_tmp, monkeypatch):
    _keys_cleanup(monkeypatch)
    from app.server import accounts
    accounts.create_user("Mirror", "pw123456", "", "", role="admin", status="active")
    store._write_file({
        "provider": "custom-agnes", "model": "m",
        "custom": [{"id": "agnes-gw", "label": "agnes",
                    "base_url": "https://api.agnes-ai.cn/v1",
                    "api_key": "agn-k1", "models": ["m"]}],
        "embedding": {"base_url": "https://api.siliconflow.cn/v1",
                      "api_key": "sf-1", "model": "bge"},
        "rerank": {"base_url": "", "api_key": "sf-1", "model": ""},
        "tavily": {"api_key": "tv-1"},
        "wechat": {"bot_id": "wb1", "secret": "ws1", "enabled": "1"},
    })
    store.migrate_keys_from_config_store()
    full = accounts.get_user("Mirror", include_secrets=True)
    assert "agn-k1" in (full["agnes_key"] or "")
    assert "sf-1" in (full["siliconflow_key"] or "")
    assert full["extra"]["tavily_api_key"] == "tv-1"
    assert full["extra"]["wechat_bot_id"] == "wb1"
    assert full["extra"]["wechat_secret"] == "ws1"
    # 已入 DB → 配置剥除
    cfg = store.get_config()
    assert cfg["embedding"]["api_key"] == ""
    assert cfg["tavily"]["api_key"] == ""
    assert cfg["wechat"]["secret"] == ""
    # 幂等：重复迁移不改变 DB 值也不覆盖
    store.migrate_keys_from_config_store()
    full2 = accounts.get_user("Mirror", include_secrets=True)
    assert full2["agnes_key"] == full["agnes_key"]
    assert full2["extra"] == full["extra"]


def test_migrate_keys_from_dotenv_fallback(store, accounts_tmp, monkeypatch):
    """密钥只在 .env（未入配置）时，也能兜底迁入 DB。"""
    from app.server import accounts
    accounts.init_db()
    accounts.create_user("Mirror", "pw123456", "", "", role="admin", status="active")
    store._write_file({"provider": "custom-agnes", "model": "m", "custom": []})
    store.migrate_keys_from_config_store()
    full = accounts.get_user("Mirror", include_secrets=True)
    assert full["extra"]["tavily_api_key"] == "tvly-abcdefghijkl"
    assert full["extra"]["wechat_bot_id"] == "bot123"
    assert "sk-embed-secret-1234" in (full["siliconflow_key"] or "")