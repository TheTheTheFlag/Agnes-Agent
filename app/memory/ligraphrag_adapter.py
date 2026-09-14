"""app.memory.ligraphrag_adapter — 把项目现有 LLM/Embedding 桥接到 LightRAG。

LightRAG（hku-webdatalab/lightRAG）是最新的 GraphRAG 实现（v1.x），支持：
  - 实体提取 → 知识图谱（存储在 graph_storage 里，默认 NetworkX + JsonKV + NanoVectorDB）
  - 混合检索：向量 + 图谱双路召回 + Rerank
  - async / sync 两种调用方式
  - OpenAI 兼容接口（base_url + api_key），因此可以直接对接项目的 RotatingKeyChatOpenAI

本模块的职责是"把现有 LLM 包装成 LightRAG 能用的形式"：
  1. 对话 LLM：直接传 RotatingKeyChatOpenAI（用户当前会话用的那个）
  2. Embedding：独立走一个 OpenAI 兼容端点（推荐用专门的嵌入模型如 BAAI/bge-m3），
     配置顺序：环境变量 EMBEDDING_BASE_URL/API_KEY/MODEL > data/.model_config（提取 base_url/api_key）
  3. Rerank：同上，env RERANK_BASE_URL/... > model_config
  4. 实例按 thread_id 分桶（每个会话独立图索引），会话切换时 clean 掉老的实例避免内存堆积
  5. work_dir = <repo_root>/lightrag_storage/<thread_id>/ —— 持久化到磁盘，重启不丢图谱
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import logging
import os
import threading
from typing import Any, List, Optional

import httpx

from app.config import DB_PATH


def _load_dotenv() -> None:
    """轻量 .env 加载（不依赖 python-dotenv），重复调用安全。"""
    if os.environ.get("_ENV_LOADED"):
        return
    env_path = os.path.join(os.path.dirname(os.path.dirname(str(DB_PATH))), ".env")
    if not os.path.isfile(env_path):
        return
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                os.environ.setdefault(k, v)
    except Exception:
        pass
    os.environ["_ENV_LOADED"] = "1"


def _faiss_available() -> bool:
    """faiss-cpu 是否可用（决定向量后端默认值）。"""
    try:
        import faiss  # noqa: F401
        return True
    except Exception:
        return False


def _resolve_llm() -> Any:
    """对话 LLM：走项目的主配置（用户当前选的那个模型），并把 base_url/api_key 注入环境变量。"""
    _load_dotenv()
    from app.server.config import load_model_config, resolve_provider_env
    cfg = load_model_config()
    # resolve_provider_env 内部把 base_url/api_key 注入 OPENAI_BASE_URL/OPENAI_API_KEY
    resolve_provider_env(cfg.get("provider", ""))
    from app.llm.llm_factory import create_llm
    return create_llm(
        provider=cfg.get("provider", "openai_compatible"),
        model=cfg.get("model", ""),
    )


def _resolve_endpoint(env_prefix: str) -> dict:
    """从 env / model_config 取一组 (base_url, api_key, model)。

    顺序：env EMBEDDING_BASE_URL/API_KEY/MODEL > model_config 的 base_url/api_key + env MODEL
    缺任何一项就抛 RuntimeError（不在 try/except 里被静默吞掉，方便定位问题）。
    """
    _load_dotenv()
    base_url = os.getenv(f"{env_prefix}_BASE_URL")
    api_key = os.getenv(f"{env_prefix}_API_KEY")
    model = os.getenv(f"{env_prefix}_MODEL")
    if not (base_url and api_key and model):
        # 退化到 model_config 的 base_url/api_key（如果项目主网关支持 embedding）
        try:
            from app.server.config import load_model_config
            mc = load_model_config()
            if not base_url:
                base_url = mc.get("base_url")
            if not api_key:
                api_key = mc.get("api_key")
        except Exception:
            pass
    if not (base_url and api_key):
        raise RuntimeError(
            f"缺少 {env_prefix}_BASE_URL / {env_prefix}_API_KEY（env 或 model_config）"
        )
    return {"base_url": base_url, "api_key": api_key, "model": model or ""}


def _make_llm_func() -> Any:
    """把项目的主对话 LLM（RotatingKeyChatOpenAI，同步 invoke）包装成 LightRAG 能用的异步函数。

    LightRAG v1.5.7 的 role LLM 包装器会向 llm_model_func 传这些参数：
      - 实体/关系抽取：prompt、system_prompt、history_messages、keyword_extraction 等
      - 关键词抽取：  prompt 文本、response_format={"type": "json_object"}
      - 查询回答：    prompt 文本、system_prompt 等
    统一归一成 (prompt, system_prompt, history_messages) 再调主网关；
    返回类型统一转成字符串（结构化场景让 LLM 输出 JSON，解析交给 LightRAG）。
    """
    llm = _resolve_llm()  # RotatingKeyChatOpenAI（同步 invoke）

    def _call(prompt: str, system_prompt: Optional[str] = None,
              history_messages: Optional[List] = None) -> str:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": str(system_prompt)})
        for m in history_messages or []:
            if isinstance(m, dict):
                messages.append({"role": m.get("role", "user"),
                                 "content": str(m.get("content", ""))})
        messages.append({"role": "user", "content": prompt})
        resp = llm.invoke(messages)
        return str(getattr(resp, "content", resp))

    async def _async_call(*args: Any, **kw: Any) -> str:
        # 参数归一：首个位置参数一定是 prompt（抽取/关键词/回答场景都满足）
        prompt = str(args[0] if args else kw.pop("prompt", "") or kw.pop("user_query", ""))
        system_prompt = kw.pop("system_prompt", None)
        history = kw.pop("history_messages", None) or kw.pop("conversation_history", None)
        # 关键词/回答场景都要求 JSON 输出，套一层系统提示即可（内容解析归 LightRAG）
        if kw.pop("response_format", None) and isinstance(system_prompt, str):
            system_prompt = system_prompt + "\n\n你必须只输出合法 JSON，不要输出解释性文字。"
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _call, prompt, system_prompt, history)

    return _async_call


def _make_embedding_func() -> "EmbeddingFunc":
    """构造给 LightRAG 用的 EmbeddingFunc。

    v1.5.7 的 __post_init__ 期望 `embedding_func` 是 lightrag.utils.EmbeddingFunc
    包装的 dataclass（有 .func / .embedding_dim / .max_token_size 属性），
    普通函数直接传会被当作没有 .func 回调而报错。
    BAAI/bge-m3 的输出维度是 1024（已在硅基流动实测）。
    """
    from lightrag.utils import EmbeddingFunc
    import numpy as np

    ep = _resolve_endpoint("EMBEDDING")
    from openai import OpenAI
    client = OpenAI(base_url=ep["base_url"], api_key=ep["api_key"])

    # 控制单次请求最多同时嵌入的文本数（bge-m3 单次最多 8192 token）
    max_batch = 16
    max_tokens_per_batch = 8000

    def _embed_batch(texts: List[str]) -> List[List[float]]:
        resp = client.embeddings.create(input=texts, model=ep["model"])
        return [d.embedding for d in resp.data]

    async def _aembed_batch(texts: List[str], **kwargs: Any) -> np.ndarray:
        # LightRAG 在部分路径会带 context="document" 之类的额外参数，统一忽略；
        # 且其 EmbeddingFunc.__call__ 包装层要求本函数返回单个 ndarray（shape [n, dim]）
        loop = asyncio.get_event_loop()

        def _do() -> np.ndarray:
            vecs: List[np.ndarray] = []
            for i in range(0, len(texts), max_batch):
                for emb in _embed_batch(texts[i : i + max_batch]):
                    vecs.append(np.asarray(emb, dtype="float32"))
            return np.vstack(vecs) if vecs else np.zeros((0, 1024), dtype="float32")

        return await loop.run_in_executor(None, _do)

    return EmbeddingFunc(embedding_dim=1024, func=_aembed_batch,
                         max_token_size=max_tokens_per_batch)


def _make_rerank_func():
    """构造 Rerank 函数（如果配置了 env）。返回 None 表示不启用。

    LightRAG 会以 async 方式调用：rerank_func(query=..., documents=[...], top_n=...)
    返回 [{index, relevance_score}, ...]（硅基流动 /rerank 的响应形状）。
    用 httpx 直接打硅基流动协议（OpenAI SDK 没有 /rerank 端点）。
    """
    try:
        ep = _resolve_endpoint("RERANK")
    except RuntimeError:
        return None
    import httpx

    def _rerank(query: str, documents: List[str], top_n: Optional[int] = None) -> list:
        try:
            # top_n 为 None 时由服务端按全部文档排序返回
            payload = {
                "model": ep["model"],
                "query": query,
                "documents": documents,
                "top_n": top_n or len(documents),
            }
            resp = httpx.post(
                ep["base_url"].rstrip("/") + "/rerank",
                json=payload,
                headers={"Authorization": f"Bearer {ep['api_key']}"},
                timeout=30,
            )
            resp.raise_for_status()
            results = (resp.json() or {}).get("results", [])
            return [{"index": r["index"], "relevance_score": r["relevance_score"]}
                    for r in results if isinstance(r, dict)]
        except Exception:
            # rerank 失败时返回全 0（不阻塞主流程）
            return [{"index": i, "relevance_score": 0.0} for i in range(len(documents))]

    async def _arerank(query: str, documents: List[str], top_n: Optional[int] = None) -> list:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _rerank, query, documents, top_n)

    return _arerank


# ---- LightRAG 事件循环桥 ----
# LightRAG 1.5.7 需要 initialize_storages()，且其内部锁绑定单个事件循环。
# 项目主流程（LangGraph 同步节点 + FastAPI handler）没有可复用的运行中循环，
# 所以这里起一个专属 worker 线程跑一个常驻 loop，所有 a* 操作统一调度到它上面，
# 避免 "loop is already running" / "Lock is bound to a different event loop" 两类问题。
_worker_loop: Optional[asyncio.AbstractEventLoop] = None
_worker_thread: Optional[threading.Thread] = None
_worker_ready = threading.Event()


def _ensure_worker() -> asyncio.AbstractEventLoop:
    global _worker_loop, _worker_thread
    if _worker_loop is not None and _worker_loop.is_running():
        return _worker_loop
    loop = asyncio.new_event_loop()

    def _run() -> None:
        asyncio.set_event_loop(loop)
        loop.run_forever()

    t = threading.Thread(target=_run, name="lightrag-worker", daemon=True)
    t.start()
    _worker_loop, _worker_thread = loop, t
    _worker_ready.set()
    return loop


def _run_on_worker(coro: Any, timeout: float = 300.0) -> Any:
    """把协程调度到 worker loop 上同步等结果（跨线程桥）。"""
    loop = _ensure_worker()
    fut = asyncio.run_coroutine_threadsafe(coro, loop)
    return fut.result(timeout=timeout)


# 线程安全：不同线程各实例互不影响
_lock = threading.Lock()
_instances: dict = {}

# 当前会话上下文：chatbot 节点每轮进入时 set，供 record_graph / lightgraph_query 工具定位当前线程。
# 用锁保护（多线程并发会话时取"最后活跃"的会话，与 .thread_id 文件的全局语义一致）。
_cur_thread: Optional[str] = None
_cur_thread_lock = threading.Lock()


def set_current_context(thread_id: Optional[str]) -> None:
    """记录当前正在处理的会话（每轮 graph 节点入口调用）。"""
    global _cur_thread
    with _cur_thread_lock:
        _cur_thread = thread_id


def get_current_context() -> Optional[str]:
    with _cur_thread_lock:
        return _cur_thread


def build_lightrag_instance(thread_id: str) -> Any:
    """为某会话创建 LightRAG 实例（首次创建后缓存，后续复用）。"""
    with _lock:
        if thread_id in _instances:
            return _instances[thread_id]
        import lightrag as _lh
        llm = _make_llm_func()
        emb_fn = _make_embedding_func()
        work_dir = os.path.join(os.path.dirname(os.path.dirname(str(DB_PATH))), "lightrag_storage", thread_id)
        os.makedirs(work_dir, exist_ok=True)

        # LLM 走主对话网关（base_url/api_key 注入环境变量供 LightRAG 内部使用）
        _load_dotenv()
        from app.server.config import resolve_provider_env
        # 走主配置注入 OPENAI_BASE_URL/OPENAI_API_KEY
        try:
            from app.server.config import load_model_config as _lmc
            main_provider = _lmc().get("provider", "")
            resolve_provider_env(main_provider)
        except Exception:
            pass

        rerank = _make_rerank_func()
        kwargs = {
            "working_dir": work_dir,
            # workspace 用于 Neo4j 节点 label 等隔离维度；显式传 thread_id，
            # 避免所有会话共用默认 "base" label 导致图互相污染
            "workspace": thread_id,
            "llm_model_func": llm,
            "llm_model_kwargs": {
                "base_url": os.getenv("OPENAI_BASE_URL", ""),
                "api_key": os.getenv("OPENAI_API_KEY", ""),
                "timeout": 120,
            },
            "llm_model_name": os.getenv("LLM_MODEL", ""),
            "llm_model_max_async": 2,  # 并发别太高，避免超限
            "embedding_func": emb_fn,

            "top_k": 12,                # 检索返回 top-k 块数
            "max_entity_tokens": 3000,
            "max_relation_tokens": 5000,
            "entity_extract_max_entities": 30,  # 正确的构造参数名（无多余的 i）
            "entity_extract_max_gleaning": 1,
            "chunk_token_size": 600,
            "chunk_overlap_token_size": 80,
            "enable_llm_cache": True,
            "enable_llm_cache_for_entity_extract": True,
            "entity_extraction_use_json": True,
        }
        if rerank is not None:
            kwargs["rerank_model_func"] = rerank
        # 每知识库检索配置覆盖（kb_meta.json 里的 config 段；新建库/默认库读默认值）
        _kb_cfg = (kb_meta(thread_id).get("config") or {})
        if _kb_cfg.get("top_k"):
            kwargs["top_k"] = int(_kb_cfg["top_k"])
        if _kb_cfg.get("chunk_token_size"):
            kwargs["chunk_token_size"] = int(_kb_cfg["chunk_token_size"])
        if _kb_cfg.get("chunk_overlap_token_size"):
            kwargs["chunk_overlap_token_size"] = int(_kb_cfg["chunk_overlap_token_size"])
        if _kb_cfg.get("entity_extract_max_entities"):
            kwargs["entity_extract_max_entities"] = int(_kb_cfg["entity_extract_max_entities"])
        if _kb_cfg.get("max_entity_tokens"):
            kwargs["max_entity_tokens"] = int(_kb_cfg["max_entity_tokens"])
        if _kb_cfg.get("max_relation_tokens"):
            kwargs["max_relation_tokens"] = int(_kb_cfg["max_relation_tokens"])
        if _kb_cfg.get("rerank") is False:
            kwargs.pop("rerank_model_func", None)
        # 图谱存储后端：默认 NetworkX(本地 JSON/GraphML)；GRAPH_STORAGE=neo4j 时走项目内 Neo4j
        graph_backend = os.getenv("GRAPH_STORAGE", "networkx").strip().lower()
        if graph_backend == "neo4j":
            missing = [k for k in ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD") if not os.getenv(k)]
            if missing:
                logging.getLogger(__name__).warning(
                    "GRAPH_STORAGE=neo4j 但缺少 %s，回退 NetworkX", ", ".join(missing)
                )
            else:
                kwargs["graph_storage"] = "Neo4JStorage"
        # 向量存储后端：默认 faiss（未安装则回退 nano）；VECTOR_STORAGE=faiss|nano 可显式控制。
        # 注意：切换后端后旧向量索引不迁移，需在知识库页对文档执行"重建"重新嵌入。
        vec_backend = os.getenv("VECTOR_STORAGE", "").strip().lower() or (
            "faiss" if _faiss_available() else "nano")
        if vec_backend == "faiss":
            if _faiss_available():
                kwargs["vector_storage"] = "FaissVectorDBStorage"
            else:
                logging.getLogger(__name__).warning("VECTOR_STORAGE=faiss 但未安装 faiss-cpu，回退 NanoVectorDB")
        inst = _lh.LightRAG(**kwargs)
        _instances[thread_id] = inst
        return inst


def get_lightrag(thread_id: str) -> Any:
    """拿到某个会话的 LightRAG 实例（单例）。"""
    return build_lightrag_instance(thread_id)


def _ensure_initialized(thread_id: str) -> None:
    """确保某会话的 storages 已在 worker loop 上初始化过。"""
    rag = get_lightrag(thread_id)
    try:
        from lightrag import LightRAG
        status = getattr(rag, "_storages_status", None)
        if status == LightRAG._storages_status.__class__.INITIALIZED:
            return
    except Exception:
        pass
    try:
        _run_on_worker(rag.initialize_storages(), timeout=120)
    except Exception:
        # 并发初始化由 LightRAG 内部去重/排队兜底；失败时让后续操作报错更可诊断
        pass


def lightrag_insert(thread_id: str, texts: List[str]) -> Any:
    """同步桥：在 worker loop 上执行 ainsert。"""
    _ensure_initialized(thread_id)
    rag = get_lightrag(thread_id)
    return _run_on_worker(rag.ainsert(texts), timeout=600)


def lightrag_query(thread_id: str, query: str, top_k: int = 12, mode: str = "hybrid") -> Any:
    """同步桥：在 worker loop 上执行 aquery，返回查询文本（str）。"""
    _ensure_initialized(thread_id)
    rag = get_lightrag(thread_id)
    from lightrag import QueryParam
    resp = _run_on_worker(rag.aquery(query, param=QueryParam(mode=mode, top_k=top_k)), timeout=300)
    return str(resp)


def graph_snapshot(thread_id: str) -> dict:
    """导出某会话当前知识图谱的节点/边列表（只在 worker loop 上读，线程安全）。

    返回 {nodes: [{id, name, kind, attrs}], edges: [{id, from_id, to_id, label, attrs}]}，
    供 /api/graph/nodes、/api/graph/edges 使用。NetworkX 存储读内网 Graph 对象；
    Neo4J 存储读 Cypher 快照。
    """
    def _read() -> dict:
        rag = get_lightrag(thread_id)
        storage = getattr(rag, "chunk_entity_relation_graph", None)
        g = getattr(storage, "_graph", None) if storage is not None else None
        nodes, edges = [], []
        if g is not None:
            for nid in g.nodes():
                data = g.nodes[nid] or {}
                nodes.append({
                    "id": str(nid),
                    "name": str(nid),
                    "kind": str(data.get("kind", "Entity")),
                    "attrs": json.dumps(data, ensure_ascii=False, default=str),
                })
            for u, v, data in g.edges(data=True):
                edges.append({
                    "id": f"{u}|{v}",
                    "from_id": str(u),
                    "to_id": str(v),
                    "label": (data or {}).get("description", "") or "",
                    "attrs": json.dumps(data or {}, ensure_ascii=False, default=str),
                })
        return {"nodes": nodes[:500], "edges": edges[:1000]}

    async def _ensure_then_read() -> dict:
        rag = get_lightrag(thread_id)
        try:
            await rag.initialize_storages()
        except Exception:
            pass  # 已初始化过时由 LightRAG 内部容忍；失败交 _read 兜底
        storage = getattr(rag, "chunk_entity_relation_graph", None)
        if storage is not None and type(storage).__name__ == "Neo4JStorage":
            return await _neo4j_snapshot(storage)
        return _read()

    try:
        return _run_on_worker(_ensure_then_read(), timeout=120)
    except Exception:
        return {"nodes": [], "edges": []}


async def _neo4j_snapshot(storage: Any) -> dict:
    """从 Neo4JStorage 读全图快照（worker loop 上的 async Cypher 读取）。

    LightRAG 的 Neo4j 节点属性含 entity_id/kind/description 等，边属性含
    source/target/description 等。
    """
    try:
        nodes = await storage.get_all_nodes() or []
        edges = await storage.get_all_edges() or []
    except Exception:
        return {"nodes": [], "edges": []}
    node_list = []
    for n in nodes:
        nid = str(n.get("id") or n.get("entity_id") or "")
        if not nid:
            continue
        node_list.append({
            "id": nid,
            "name": str(n.get("name") or nid),
            "kind": str(n.get("kind") or "Entity"),
            "attrs": json.dumps(n, ensure_ascii=False, default=str),
        })
    edge_list = []
    for e in edges:
        src = str(e.get("source") or "")
        tgt = str(e.get("target") or "")
        if not src or not tgt:
            continue
        edge_list.append({
            "id": f"{src}|{tgt}",
            "from_id": src,
            "to_id": tgt,
            "label": str((e.get("description") or "") or ""),
            "attrs": json.dumps(e, ensure_ascii=False, default=str),
        })
    return {"nodes": node_list[:500], "edges": edge_list[:1000]}


def clear_instance(thread_id: str) -> None:
    """清理某会话的实例。"""
    with _lock:
        _instances.pop(thread_id, None)


def reset_all() -> None:
    with _lock:
        _instances.clear()


# ==================== 知识库管理（KB） ====================


def _storage_root() -> str:
    """lightrag_storage 根目录（即项目根/lightrag_storage）。"""
    root = os.path.dirname(os.path.dirname(str(DB_PATH)))
    return os.path.join(root, "lightrag_storage")


def _workspace_dir(thread_id: str) -> str:
    """某会话的 workspace 数据目录（hku 版把数据落在 working_dir/workspace/ 下）。"""
    return os.path.join(_storage_root(), thread_id, thread_id)


def kb_threads() -> list:
    """列出所有已有会话（按目录修改时间倒序）。"""
    root = _storage_root()
    out = []
    if os.path.isdir(root):
        try:
            entries = sorted(
                [(n, os.path.getmtime(os.path.join(root, n)))
                 for n in os.listdir(root)
                 if os.path.isdir(os.path.join(root, n)) and not n.startswith(".")],
                key=lambda kv: kv[1], reverse=True,
            )
        except Exception:
            entries = []
        for n, mtime in entries:
            out.append({
                "id": n,
                "label": n[:13],
                "dir": os.path.join(root, n),
                "mtime": _iso_dt(mtime),
            })
    return out


def _iso_dt(ts: float) -> str:
    try:
        import datetime
        return datetime.datetime.fromtimestamp(ts).isoformat(timespec="seconds")
    except Exception:
        return ""


def _read_json_if_exists(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _doc_id_for_text(text: str) -> str:
    """与 LightRAG 一致的 doc 主键：md5(内容)，前缀 'doc-'。"""
    return "doc-" + hashlib.md5(text.encode("utf-8")).hexdigest()


def kb_status(thread_id: str) -> dict:
    """会话知识库概况：文档/分块/实体/关系/图谱/后端信息。"""
    rag = get_lightrag(thread_id)
    try:
        _ensure_initialized(thread_id)
    except Exception:
        pass
    ws = _workspace_dir(thread_id)
    docs = _read_json_if_exists(os.path.join(ws, "kv_store_doc_status.json"))
    chunks = _read_json_if_exists(os.path.join(ws, "kv_store_text_chunks.json"))
    entities = _read_json_if_exists(os.path.join(ws, "kv_store_full_entities.json"))
    relations = _read_json_if_exists(os.path.join(ws, "kv_store_full_relations.json"))
    snap = {}
    try:
        snap = graph_snapshot(thread_id)
    except Exception:
        pass
    vec_cls = type(getattr(rag, "entities_vdb", None)).__name__ if getattr(rag, "entities_vdb", None) else "?"
    graph_cls = type(getattr(rag, "chunk_entity_relation_graph", None)).__name__ if getattr(rag, "chunk_entity_relation_graph", None) else "?"
    status_counts: dict = {}
    for rec in docs.values():
        st = (rec.get("status") or "unknown").lower()
        status_counts[st] = status_counts.get(st, 0) + 1
    meta = kb_meta(thread_id)
    return {
        "thread_id": thread_id,
        "name": meta.get("name") or thread_id[:13],
        "description": meta.get("description") or "",
        "vector_backend": "faiss" if vec_cls == "FaissVectorDBStorage" else ("nano" if vec_cls == "NanoVectorDBStorage" else vec_cls),
        "graph_backend": "neo4j" if graph_cls == "Neo4JStorage" else ("networkx" if graph_cls == "NetworkXStorage" else graph_cls),
        "doc_count": len(docs),
        "doc_statuses": status_counts,
        "chunk_count": len(chunks),
        "entity_count": len(entities),
        "relation_count": len(relations),
        "graph_nodes": len(snap.get("nodes", [])),
        "graph_edges": len(snap.get("edges", [])),
        "workspace_dir": ws,
    }


def kb_documents(thread_id: str, page: int = 1, page_size: int = 50) -> dict:
    """某会话的文档列表（分页，按更新时间倒序）。"""
    page = max(1, int(page))
    page_size = min(200, max(10, int(page_size)))
    docs_raw = _read_json_if_exists(os.path.join(_workspace_dir(thread_id), "kv_store_doc_status.json"))
    items = sorted(docs_raw.items(),
                   key=lambda kv: str((kv[1] or {}).get("updated_at", "") or ""), reverse=True)
    total = len(items)
    page_items = items[(page - 1) * page_size: page * page_size]
    docs = []
    for doc_id, rec in page_items:
        rec = rec or {}
        nid = str(doc_id)
        if not nid.startswith("doc-"):
            nid = "doc-" + nid
        docs.append({
            "id": nid,
            "title": str(rec.get("file_path") or rec.get("content_summary") or nid)[:120],
            "file_path": str(rec.get("file_path") or "")[:120],
            "status": str(rec.get("status") or "unknown"),
            "error_msg": str(rec.get("error_msg") or ""),
            "content_summary": str(rec.get("content_summary") or ""),
            "content_length": int(rec.get("content_length") or 0),
            "chunks_count": int(rec.get("chunks_count") or (rec.get("chunks_list") and len(rec["chunks_list"])) or 0),
            "created_at": rec.get("created_at") or "",
            "updated_at": rec.get("updated_at") or "",
            "track_id": rec.get("track_id") or "",
        })
    return {"thread_id": thread_id, "total": total, "page": page, "page_size": page_size, "docs": docs}


def kb_chunks(thread_id: str, doc_id: str = "") -> dict:
    """某文档的分块列表（内容 + token 数）；doc_id 为空时返回该知识库全部切片。"""
    nid = ""
    if doc_id:
        nid = doc_id if str(doc_id).startswith("doc-") else "doc-" + str(doc_id)
    chunks = _read_json_if_exists(os.path.join(_workspace_dir(thread_id), "kv_store_text_chunks.json"))
    out = []
    for key, rec in chunks.items():
        rec = rec or {}
        if nid:
            if not str(key).startswith(nid + "-"):
                continue
        out.append({
            "id": str(key),
            "doc_id": nid or str(rec.get("full_doc_id") or ""),
            "order": int(rec.get("chunk_order_index") or 0),
            "tokens": int(rec.get("tokens") or 0),
            "content": str(rec.get("content") or ""),
        })
    if nid:
        out.sort(key=lambda c: c["order"])
    else:
        out.sort(key=lambda c: (c["doc_id"], c["order"]))
    return {"doc_id": nid or "", "total": len(out), "chunks": out}


def kb_search(thread_id: str, query: str, top_k: int = 8) -> dict:
    """检索测试：混合检索文本 + 原始向量命中（分块/实体/关系）。"""
    query = (query or "").strip()
    if not query:
        return {"hybrid": "", "vector_hits": []}
    rag = get_lightrag(thread_id)
    try:
        _ensure_initialized(thread_id)
    except Exception:
        pass

    def _vec_hits(vdb, k: int) -> list:
        try:
            rows = _run_on_worker(vdb.query(query, int(k)), timeout=60) or []
        except Exception:
            return []
        out = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            nid = str(r.get("id") or "")
            out.append({
                "id": nid,
                "score": float(r.get("distance") if "distance" in r else r.get("score", 0)) or 0.0,
                "content": str(r.get("content") or r.get("text") or "")[:300] or nid,
            })
        return out

    hybrid = ""
    try:
        hybrid = str(lightrag_query(thread_id, query, top_k=top_k, mode="hybrid"))
    except Exception:
        pass
    return {
        "hybrid": hybrid,
        "vector_hits": {
            "chunks": _vec_hits(getattr(rag, "chunks_vdb", None), top_k),
            "entities": _vec_hits(getattr(rag, "entities_vdb", None), top_k),
            "relations": _vec_hits(getattr(rag, "relationships_vdb", None), top_k),
        },
    }


def kb_ingest(thread_id: str, texts: List[str]) -> dict:
    """向会话知识库喂入文本（同步，含建图）；返回生成的 doc_id 列表。"""
    texts = [t for t in (texts or []) if isinstance(t, str) and t.strip()]
    if not texts:
        return {"ok": False, "error": "没有可喂入的文本", "doc_ids": []}
    try:
        lightrag_insert(thread_id, texts)
    except Exception as e:
        return {"ok": False, "error": f"喂入失败: {e}", "doc_ids": []}
    return {"ok": True, "doc_ids": [_doc_id_for_text(t) for t in texts]}


def kb_delete_doc(thread_id: str, doc_id: str) -> dict:
    """删除某文档及其分块/图谱/向量数据。"""
    nid = doc_id if str(doc_id).startswith("doc-") else "doc-" + str(doc_id)
    rag = get_lightrag(thread_id)
    try:
        _ensure_initialized(thread_id)
    except Exception:
        pass
    try:
        _run_on_worker(rag.adelete_by_doc_id(nid), timeout=180)
        return {"ok": True, "doc_id": nid}
    except Exception as e:
        return {"ok": False, "doc_id": nid, "error": f"删除失败: {e}"}


def kb_reprocess(thread_id: str, doc_id: str) -> dict:
    """重建某文档：先删除、再按原内容重新喂入（向量后端切换后用于重新嵌入）。"""
    nid = doc_id if str(doc_id).startswith("doc-") else "doc-" + str(doc_id)
    rag = get_lightrag(thread_id)
    try:
        _ensure_initialized(thread_id)
    except Exception:
        pass
    try:
        rec = _run_on_worker(rag.full_docs.get_by_id(nid), timeout=60)
    except Exception:
        rec = None
    content = (rec or {}).get("content") if isinstance(rec, dict) else None
    if not content:
        return {"ok": False, "doc_id": nid, "error": "找不到原文档内容"}
    try:
        _run_on_worker(rag.adelete_by_doc_id(nid), timeout=180)
    except Exception:
        pass
    try:
        lightrag_insert(thread_id, [str(content)])
        return {"ok": True, "doc_id": nid}
    except Exception as e:
        return {"ok": False, "doc_id": nid, "error": f"重建失败: {e}"}


# ==================== RAG 管理台（dashboard / 知识库元信息 / 检索调试） ====================

_KB_DEFAULT_CONFIG = {
    "top_k": 12,
    "threshold": 0.2,           # 相似度阈值（余弦，前端"仅检索"过滤用）
    "rerank": True,             # 重排开关（应用于实例构建）
    "hybrid": True,             # 混合检索默认开关（检索调试面板默认 mode）
    "chunk_token_size": 600,
    "chunk_overlap_token_size": 80,
    "entity_extract_max_entities": 30,
    "max_entity_tokens": 3000,
    "max_relation_tokens": 5000,
}


def _kb_meta_path(thread_id: str) -> str:
    return os.path.join(_storage_root(), thread_id, "kb_meta.json")


def kb_meta(thread_id: str) -> dict:
    """读某知识库元信息（kb_meta.json）；不存在则返回带默认名的占位。"""
    p = _kb_meta_path(thread_id)
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {"name": thread_id[:13], "description": "", "config": _KB_DEFAULT_CONFIG, "created_at": ""}


def kb_save_meta(thread_id: str, meta: dict) -> None:
    os.makedirs(os.path.dirname(_kb_meta_path(thread_id)), exist_ok=True)
    with open(_kb_meta_path(thread_id), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def kb_get_config(thread_id: str) -> dict:
    meta = kb_meta(thread_id)
    cfg = dict(_KB_DEFAULT_CONFIG)
    cfg.update(meta.get("config") or {})
    return cfg


def kb_set_config(thread_id: str, cfg: dict) -> dict:
    clean = {k: v for k, v in (cfg or {}).items() if k in _KB_DEFAULT_CONFIG}
    meta = kb_meta(thread_id)
    meta["config"] = {**_KB_DEFAULT_CONFIG, **(meta.get("config") or {}), **clean}
    kb_save_meta(thread_id, meta)
    # 检索配置作用于实例构建；已有实例需重建才生效
    clear_instance(thread_id)
    return kb_get_config(thread_id)


def kb_create(thread_id: Optional[str] = None, name: str = "", description: str = "") -> dict:
    """新建知识库：生成 thread_id、建目录结构、写元信息。"""
    import uuid
    tid = (thread_id or "").strip() or uuid.uuid4().hex[:20]
    ws = _workspace_dir(tid)
    os.makedirs(ws, exist_ok=True)
    meta = {
        "name": (name or "").strip() or tid[:13],
        "description": (description or "").strip(),
        "config": _KB_DEFAULT_CONFIG,
        "created_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
    }
    kb_save_meta(tid, meta)
    return {"ok": True, "thread_id": tid, "name": meta["name"], "description": meta["description"]}


def kb_list() -> list:
    """所有知识库：元信息 + 轻量计数（直接扫文件，不初始化实例）。"""
    out = []
    for t in kb_threads():
        tid = t["id"]
        ws = _workspace_dir(tid)
        docs = _read_json_if_exists(os.path.join(ws, "kv_store_doc_status.json"))
        chunks = _read_json_if_exists(os.path.join(ws, "kv_store_text_chunks.json"))
        entities = _read_json_if_exists(os.path.join(ws, "kv_store_full_entities.json"))
        relations = _read_json_if_exists(os.path.join(ws, "kv_store_full_relations.json"))
        meta = kb_meta(tid)
        st_counts = {}
        for rec in docs.values():
            k = str((rec or {}).get("status") or "unknown").lower()
            st_counts[k] = st_counts.get(k, 0) + 1
        out.append({
            "thread_id": tid,
            "name": meta.get("name") or tid[:13],
            "description": meta.get("description") or "",
            "doc_count": len(docs),
            "chunk_count": len(chunks),
            "entity_count": len(entities),
            "relation_count": len(relations),
            "status_counts": st_counts,
            "created_at": meta.get("created_at") or "",
            "updated_at": t.get("mtime") or "",
        })
    return out


def kb_delete_thread(thread_id: str) -> dict:
    """删除整个知识库（本地目录 + 尽力删 Neo4j 该 label 节点 + 清除实例）。"""
    import shutil
    tid = str(thread_id)
    root_dir = os.path.join(_storage_root(), tid)
    # 尽力删 Neo4j 节点（label=thread_id，需反引号）
    try:
        _run_on_worker(_neo4j_delete_workspace(tid), timeout=120)
    except Exception:
        pass
    if os.path.isdir(root_dir):
        try:
            shutil.rmtree(root_dir, ignore_errors=True)
        except Exception:
            pass
    clear_instance(tid)
    return {"ok": True, "thread_id": tid}


async def _neo4j_delete_workspace(tid: str) -> None:
    """Neo4j 侧删除某 workspace label 的全部节点（尽力而为，失败不影响本地删除）。"""
    label = str(tid).replace("`", "")
    if not (os.getenv("NEO4J_URI") or ""):
        return
    try:
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(
            os.getenv("NEO4J_URI", "bolt://localhost:7687"),
            auth=(os.getenv("NEO4J_USERNAME", "neo4j"), os.getenv("NEO4J_PASSWORD", "")),
        )
        db = os.getenv("NEO4J_DATABASE", "neo4j")
        try:
            with driver.session(database=db) as ses:
                ses.run(f"MATCH (n:`{label}`) DETACH DELETE n")
        finally:
            driver.close()
    except Exception as e:
        logging.getLogger(__name__).warning("Neo4j 清理 label=%s 失败: %s", tid, e)


def kb_dashboard() -> dict:
    """全局大盘：跨库统计 + 7 日问答/文档趋势 + 反馈占比。"""
    libs = kb_list()
    doc_total = sum(d["doc_count"] for d in libs)
    chunk_total = sum(d["chunk_count"] for d in libs)
    entity_total = sum(d["entity_count"] for d in libs)
    relation_total = sum(d["relation_count"] for d in libs)
    status_total = {}
    for d in libs:
        for k, v in (d.get("status_counts") or {}).items():
            status_total[k] = status_total.get(k, 0) + v
    messages7, docs7 = _trends_7d()
    fb = _feedback_stats()
    return {
        "kb_count": len(libs),
        "doc_total": doc_total,
        "chunk_total": chunk_total,
        "entity_total": entity_total,
        "relation_total": relation_total,
        "status_counts": status_total,
        "trend_days": [m[0] for m in messages7],
        "trend_qa": [m[1] for m in messages7],
        "trend_docs": [d[1] for d in docs7],
        "feedback": fb,
        "vector_backend": os.getenv("VECTOR_STORAGE", "faiss"),
        "graph_backend": os.getenv("GRAPH_STORAGE", "neo4j"),
    }


def _trends_7d():
    """近 7 天：{(月-日): 问答次数(user 消息数)} 与 文档新增数（按 doc 创建日）。"""
    from datetime import date, datetime, timedelta
    today = date.today()
    days = [(today - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
    qa = {d: 0 for d in days}
    docs = {d: 0 for d in days}

    try:
        import sqlite3
        with sqlite3.connect(str(DB_PATH)) as conn:
            rows = conn.execute(
                "SELECT substr(timestamp,1,10) AS d, COUNT(*) FROM messages "
                "WHERE role='user' GROUP BY substr(timestamp,1,10)").fetchall()
        for d, n in rows:
            if isinstance(d, str) and d in qa:
                qa[d] = int(n or 0)
    except Exception:
        pass

    for t in kb_threads():
        try:
            recs = _read_json_if_exists(os.path.join(_workspace_dir(t["id"]), "kv_store_doc_status.json"))
        except Exception:
            continue
        for rec in recs.values():
            ct = str((rec or {}).get("created_at") or "")[:10]
            if ct in docs:
                docs[ct] += 1
    labels = []
    for d in days:
        try:
            labels.append(str(int(d[5:7])) + "-" + str(int(d[8:10])))
        except Exception:
            labels.append(d)
    return (list(zip(labels, [qa[d] for d in days])) or []), \
           (list(zip(labels, [docs[d] for d in days])) or [])


def _feedback_stats() -> dict:
    try:
        import sqlite3
        with sqlite3.connect(str(DB_PATH)) as conn:
            rows = conn.execute(
                "SELECT verdict, COUNT(*) FROM qa_feedback GROUP BY verdict").fetchall()
        cnt = {"like": 0, "dislike": 0}
        for v, n in rows:
            if v in cnt:
                cnt[v] = int(n or 0)
        total = cnt["like"] + cnt["dislike"]
        ratio = round(cnt["like"] / (total or 1) * 100, 1)
        return {"like": cnt["like"], "dislike": cnt["dislike"], "total": total, "ratio": ratio}
    except Exception:
        return {"like": 0, "dislike": 0, "total": 0, "ratio": 0.0}


_QA_FEEDBACK_SQL = """
CREATE TABLE IF NOT EXISTS qa_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL,
    query TEXT NOT NULL,
    answer TEXT,
    verdict TEXT NOT NULL,
    top_k INTEGER,
    mode TEXT,
    note TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
"""


def kb_feedback(thread_id: str, query: str, answer: str, verdict: str,
                top_k: int = 0, mode: str = "", note: str = "") -> dict:
    import sqlite3
    verdict = "like" if verdict == "like" else "dislike"
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.execute(_QA_FEEDBACK_SQL)
        cur = conn.execute(
            "INSERT INTO qa_feedback (thread_id, query, answer, verdict, top_k, mode, note) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (thread_id, query, answer, verdict, int(top_k or 0), mode, note))
        conn.commit()
        new_id = cur.lastrowid
    return {"ok": True, "id": new_id, "verdict": verdict}


def kb_feedback_list(thread_id: Optional[str] = None, limit: int = 100) -> list:
    import sqlite3
    try:
        with sqlite3.connect(str(DB_PATH)) as conn:
            conn.row_factory = sqlite3.Row
            if thread_id:
                rows = conn.execute(
                    "SELECT * FROM qa_feedback WHERE thread_id=? ORDER BY id DESC LIMIT ?",
                    (thread_id, limit)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM qa_feedback ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []


def kb_import_url(thread_id: str, url: str) -> dict:
    """抓取 URL 网页正文并喂入知识库。"""
    import urllib.parse
    import re
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return {"ok": False, "error": "URL 需以 http(s):// 开头"}
    try:
        resp = httpx.get(url, timeout=30, follow_redirects=True,
                         headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        ctype = resp.headers.get("content-type", "")
        text = resp.text
        if "html" in ctype:
            text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", text, flags=re.I)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text)
            text = text.strip()
        if not text.strip():
            return {"ok": False, "error": "页面没有可提取的文本内容"}
        return kb_ingest(thread_id, [f"[来源URL: {url}]\n{text}"])
    except Exception as e:
        return {"ok": False, "error": f"抓取失败: {e}"}


def kb_delete_chunk(thread_id: str, chunk_key: str) -> dict:
    """删除某个切片：KV + 向量 + 所属文档的 chunks 计数。"""
    key = str(chunk_key)
    rag = get_lightrag(thread_id)
    try:
        _ensure_initialized(thread_id)
    except Exception:
        pass
    doc_id = str(key).split("-chunk-")[0]
    try:
        rec = _run_on_worker(rag.doc_status.get_by_id(doc_id), timeout=60)
        if isinstance(rec, dict):
            chunks_list = list(rec.get("chunks_list") or [])
            if chunks_list and key in chunks_list:
                chunks_list.remove(key)
                rec["chunks_list"] = chunks_list
                rec["chunks_count"] = max(0, int(rec.get("chunks_count") or 1) - 1)
                _run_on_worker(rag.doc_status.upsert({doc_id: rec}), timeout=60)
        _run_on_worker(rag.text_chunks.delete([key]), timeout=60)
        _run_on_worker(rag.chunks_vdb.delete([key]), timeout=60)
        return {"ok": True, "doc_id": doc_id, "chunk_key": key}
    except Exception as e:
        return {"ok": False, "chunk_key": key, "error": f"删除切片失败: {e}"}


def kb_edit_chunk(thread_id: str, chunk_key: str, content: str, tags: str = "") -> dict:
    """编辑某个切片：更新 KV 文本 + 重新嵌入向量。"""
    key = str(chunk_key)
    if not (content or "").strip():
        return {"ok": False, "chunk_key": key, "error": "内容为空"}
    rag = get_lightrag(thread_id)
    try:
        _ensure_initialized(thread_id)
    except Exception:
        pass
    try:
        rec = _run_on_worker(rag.text_chunks.get_by_id(key), timeout=60)
        if not isinstance(rec, dict):
            return {"ok": False, "chunk_key": key, "error": "找不到该切片"}
        rec["content"] = content
        if tags:
            rec["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
        _run_on_worker(rag.text_chunks.upsert({key: rec}), timeout=60)
        # 重新嵌入该切片（vdb.upsert 内部会 embedding + 写索引）
        vrec = dict(rec)
        vrec.pop("content", None)
        _run_on_worker(rag.chunks_vdb.upsert({key: {"content": content, **vrec}}), timeout=120)
        return {"ok": True, "chunk_key": key}
    except Exception as e:
        return {"ok": False, "chunk_key": key, "error": f"编辑切片失败: {e}"}
