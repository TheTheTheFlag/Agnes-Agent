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
import json
import logging
import os
import threading
from typing import Any, List, Optional

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
