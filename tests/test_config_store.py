"""tests/test_config_store.py — 统一配置中心（.env → data/.model_config）回归测试。"""
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


def test_migrate_from_dotenv_is_idempotent(store):
    assert store.migrate_from_dotenv() is True
    cfg = store.get_config()
    assert cfg["embedding"]["api_key"] == "sk-embed-secret-1234"
    assert cfg["embedding"]["model"] == "BAAI/bge-m3"
    assert cfg["tavily"]["api_key"] == "tvly-abcdefghijkl"
    assert cfg["graph"]["storage"] == "neo4j"
    assert cfg["graph"]["neo4j_password"] == "neo4jpass"
    assert cfg["vector_storage"] == "faiss"
    assert cfg["wechat"]["bot_id"] == "bot123"
    # 第二次不再改动
    assert store.migrate_from_dotenv() is False


def test_apply_to_env_writes_values(store, monkeypatch):
    store.migrate_from_dotenv()
    store.apply_to_env()
    import os
    assert os.environ["EMBEDDING_API_KEY"] == "sk-embed-secret-1234"
    assert os.environ["TAVILY_API_KEY"] == "tvly-abcdefghijkl"
    assert os.environ["WECHAT_BOT_ID"] == "bot123"
    # 未配置的空值不注入
    assert "WECHAT_BOT_WELCOME" not in os.environ


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
    assert masked["embedding"]["api_key"].endswith("1234")
    assert "secret" not in masked["embedding"]["api_key"]
    assert masked["embedding"]["api_key"].startswith("*")
    assert masked["wechat"]["secret"].endswith("456")
