"""app.memory.ligraphrag_adapter — 把项目现有 LLM/Embedding 桥接到 LightRAG。

LightRAG（hku-webdatalab/lightRAG）是最新的 GraphRAG 实现（v1.x），支持：
  - 实体提取 → 知识图谱（存储在 graph_storage 里，默认 NetworkX + JsonKV + NanoVectorDB）
  - 混合检索：向量 + 图谱双路召回 + Rerank
  - async / sync 两种调用方式
  - OpenAI 兼容接口（base_url + api_key），因此可以直接对接项目的 RotatingKeyChatOpenAI

本模块的职责是"把现有 LLM 包装成 LightRAG 能用的形式"，具体设计：
  1. LLM：直接传 RotatingKeyChatOpenAI 实例（它已有 invoke(messages) -> str 语义，符合 LightRAG 要求）
  2. Embedding：取 OPENAI_BASE_URL + OPENAI_API_KEY（从 data/.model_config 读），
     构造一个同步 + 异步 embedding 函数，让 LightRAG 在 entity extraction 阶段做向量化
  3. 实例按 thread_id 分桶（每个会话独立图索引），会话切换时 clean 掉老的实例避免内存堆积
  4. work_dir = data/lightrag_storage/<thread_id>/ —— 持久化到磁盘，重启不丢图谱
"""
from __future__ import annotations
import asyncio
import os
import threading
from typing import Any, List, Optional

from app.config import DB_PATH


def _resolve_llm() -> Any:
    """延迟导入，避免启动时模型配置缺失就报错。"""
    from app.llm.llm_factory import create_llm
    cfg: dict = {}
    try:
        from app.server.config import load_model_config
        cfg = load_model_config()
    except Exception:
        pass
    return create_llm(
        provider=cfg.get("provider", "openai_compatible"),
        model=cfg.get("model", ""),
    )


def _make_embedding_func() -> tuple:
    """构造 OpenAI 兼容的同步 + 异步 embedding 函数，给 LightRAG 用。

    回源顺序：环境变量 OPENAI_BASE_URL/OPENAI_API_KEY > data/.model_config 里的 base_url/api_key。
    """
    try:
        from openai import OpenAI
        import app.server.config as _sc
        mc = _sc.load_model_config()
        base_url = os.getenv("OPENAI_BASE_URL") or mc.get("base_url") or ""
        api_key = os.getenv("OPENAI_API_KEY") or mc.get("api_key") or ""
        if not base_url or not api_key:
            raise RuntimeError(
                "缺少 OPENAI_BASE_URL / OPENAI_API_KEY "
                "（或 data/.model_config 里未配置 base_url/api_key）"
            )
        client = OpenAI(base_url=base_url or None, api_key=api_key or None)
        model = os.getenv("EMBEDDING_MODEL") or "text-embedding-3-small"

        def _embed_batch(texts: List[str]) -> List[List[float]]:
            resp = client.embeddings.create(input=texts, model=model)
            # 按 input 顺序返回；lightRAG 要求 list[list[float]]
            return [d.embedding for d in resp.data]

        async def _aembed_batch(texts: List[str]) -> List[List[float]]:
            # openai 客户端不支持 async embed，用线程池包一下
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, _embed_batch, texts)

        return _embed_batch, _aembed_batch
    except Exception as e:
        raise RuntimeError(f"构造 LightRAG embedding 失败: {e}") from e


# 线程安全：不同线程各实例互不影响
_lock = threading.Lock()
_instances: dict = {}


def build_lightrag_instance(thread_id: str) -> Any:
    """为某会话创建 LightRAG 实例（首次创建后缓存，后续复用）。

    work_dir = data/lightrag_storage/<thread_id>/，LightRAG 内部会把图谱 + 向量索引持久化进
    这个目录（JsonKV + NetworkX + NanoVectorDB），重启进程不会丢知识。
    """
    with _lock:
        if thread_id in _instances:
            return _instances[thread_id]
        import lightrag as _lh
        llm = _resolve_llm()
        emb_fn, aemb_fn = _make_embedding_func()
        work_dir = os.path.join(str(DB_PATH.parent), "lightrag_storage", thread_id)
        os.makedirs(work_dir, exist_ok=True)

        # 让 LightRAG 走 OpenAI 兼容路由
        openai_kwargs = {
            "base_url": _resolve_openai_base_url(),
            "api_key": os.getenv("OPENAI_API_KEY") or "",
            "timeout": 120,
        }
        llm_name = os.getenv("LLM_MODEL") or ""
        kwargs = {
            "working_dir": work_dir,
            "llm_model_func": llm,
            "llm_model_kwargs": openai_kwargs,
            "llm_model_name": llm_name,
            "llm_model_max_async": 2,  # 并发别太高，避免超限
            "embedding_func": emb_fn,
            "async_embedding_func": aemb_fn,
            "top_k": 12,                # 检索返回 top-k 块数
            "max_entity_tokens": 3000,
            "max_relation_tokens": 5000,
            "entity_extraction_max_entities": 30,
            "entity_extract_max_gleaning": 1,  # 不重试，省 token
            "chunk_token_size": 600,
            "chunk_overlap_token_size": 80,
            "enable_llm_cache": True,
            "enable_llm_cache_for_entity_extract": True,
            "entity_extraction_use_json": True,  # 强制 JSON 输出，结构化更好
        }
        inst = _lh.LightRAG(**kwargs)
        _instances[thread_id] = inst
        return inst


def _resolve_openai_base_url() -> str:
    """从 data/.model_config 读 base_url（兼容多种格式）。"""
    try:
        from app.server.config import load_model_config
        mc = load_model_config()
        return str(mc.get("base_url") or "")
    except Exception:
        return ""


def get_lightrag(thread_id: str) -> Any:
    """拿到某个会话的 LightRAG 实例（单例）。"""
    return build_lightrag_instance(thread_id)


def clear_instance(thread_id: str) -> None:
    """清理某会话的实例（比如会话删除 / 切换时释放内存）。"""
    with _lock:
        _instances.pop(thread_id, None)


def reset_all() -> None:
    with _lock:
        _instances.clear()
