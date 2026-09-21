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
import csv
import hashlib
import io
import json
import logging
import os
import re
import threading
import time
from typing import Any, List, Optional

import httpx

from app.config import DB_PATH, BASE_DIR


def _load_dotenv() -> None:
    """统一配置加载：优先 data/.model_config（写回环境变量），.env 仅作回退。重复调用安全。"""
    if os.environ.get("_ENV_LOADED"):
        return
    try:
        from app.config_store import bootstrap as _bootstrap_config
        _bootstrap_config()
    except Exception:
        pass
    env_path = os.path.join(BASE_DIR, ".env")
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


_FAISS_LAZY_PATCHED = False


def _patch_faiss_lazy_vectors() -> None:
    """让 FaissVectorDBStorage 载入索引时不再预先把每条向量还原成 Python list。

    LightRAG 上游 `_load_faiss_index` 会对每行执行
    `self._index.reconstruct(fid).tolist()` 并塞进 `_id_to_meta["__vector__"]`。
    22 万条 1024 维向量约合 7GB 常驻内存，仅服务于 `get_vectors_by_ids`，
    在 8GB 服务器上直接 OOM。这里改为完全不预载向量：
      - 主检索路径 `chunks_vdb.query` 直接读 `_id_to_meta` 里的 content，不受影响；
      - `get_by_id(s)` 走 `_format_record`（本就剥离向量），不受影响；
      - 仅 `get_vectors_by_ids` 返回空，上层 `_vector_similarity_chunk_selection`
        会回退到 WEIGHT 排序（纯向量库没有图谱实体，此路径本就不触发）。
    置 LIGHTRAG_LAZY_FAISS_VECTORS=0 可关闭该补丁。
    """
    global _FAISS_LAZY_PATCHED
    if _FAISS_LAZY_PATCHED:
        return
    _FAISS_LAZY_PATCHED = True
    if str(os.environ.get("LIGHTRAG_LAZY_FAISS_VECTORS", "1")).strip() == "0":
        return
    try:
        import faiss
        from lightrag.kg.faiss_impl import FaissVectorDBStorage
        from lightrag.utils import logger
    except Exception:
        return
    if getattr(FaissVectorDBStorage, "_agnes_lazy_vectors", False):
        return

    def _load_faiss_index(self):
        if not os.path.exists(self._faiss_index_file):
            logger.warning(
                f"[{self.workspace}] No existing Faiss index file found for {self.namespace}"
            )
            return
        dim_mismatch = False
        try:
            self._index = faiss.read_index(self._faiss_index_file)
            if self._index.d != self._dim:
                dim_mismatch = True
                raise ValueError(
                    f"Dimension mismatch: loaded Faiss index has dimension {self._index.d}, "
                    f"but embedding function expects dimension {self._dim}."
                )
            with open(self._meta_file, "r", encoding="utf-8") as f:
                stored_dict = json.load(f)
            ntotal = int(self._index.ntotal)
            id_to_meta = {}
            for fid_str, meta in stored_dict.items():
                fid = int(fid_str)
                if fid >= ntotal:
                    logger.warning(
                        f"[{self.workspace}] Skipping metadata row fid={fid}: "
                        f"exceeds index size ({ntotal})"
                    )
                    continue
                # 与上游唯一差异：不重建 __vector__（避免逐条 reconstruct().tolist()）
                if "__vector__" in meta:
                    meta.pop("__vector__", None)
                id_to_meta[fid] = meta
            self._id_to_meta = id_to_meta
            if ntotal > len(self._id_to_meta):
                logger.warning(
                    f"[{self.workspace}] FAISS index has {ntotal} vectors but only "
                    f"{len(self._id_to_meta)} metadata rows — index > meta skew."
                )
            logger.info(
                f"[{self.workspace}] Faiss index loaded with {ntotal} vectors from "
                f"{self._faiss_index_file} (lazy vectors)"
            )
        except Exception as e:
            if dim_mismatch:
                raise
            logger.error(
                f"[{self.workspace}] Failed to load Faiss index or metadata: {e}"
            )
            logger.warning(f"[{self.workspace}] Starting with an empty Faiss index.")
            self._index = faiss.IndexFlatIP(self._dim)
            self._id_to_meta = {}

    FaissVectorDBStorage._load_faiss_index = _load_faiss_index
    FaissVectorDBStorage._agnes_lazy_vectors = True


def _resolve_llm() -> Any:
    """对话 LLM：走项目的主配置（用户当前选的那个模型），并把 base_url/api_key 注入环境变量。

    .model_config 不再存密钥：API Key 用当前用户（管理员=全库轮询池）的 agnes key。
    """
    _load_dotenv()
    from app.server.config import load_model_config, resolve_provider_env
    cfg = load_model_config()
    # resolve_provider_env 内部把 base_url 注入 OPENAI_BASE_URL（api_key 已迁出配置）
    resolve_provider_env(cfg.get("provider", ""))
    try:
        from app.userctx import resolve_user_keys
        _keys = resolve_user_keys().get("agnes") or []
        if _keys:
            import os as _os
            _os.environ["OPENAI_API_KEY"] = ",".join(_keys)
    except Exception:
        pass
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
    # 多用户：Embedding/Rerank 一律用当前用户自己的硅基流动 Key（管理员用轮换池）
    if env_prefix in ("EMBEDDING", "RERANK"):
        try:
            from app.userctx import resolve_user_keys
            _ukeys = resolve_user_keys().get("siliconflow") or []
            if _ukeys:
                api_key = ",".join(_ukeys)
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
    import httpx as _httpx

    ep = _resolve_endpoint("EMBEDDING")
    from openai import OpenAI
    _no_proxy_client = _httpx.Client(trust_env=False)
    client = OpenAI(base_url=ep["base_url"], api_key=ep["api_key"],
                    http_client=_no_proxy_client)

    # 控制单次请求最多同时嵌入的文本数（bge-m3 单次最多 8192 token）。
    # 本地 Ollama（EMBEDDING_MAX_BATCH）可调大提速；默认 16 保持远程 API 保守行为。
    max_batch = int(os.environ.get("EMBEDDING_MAX_BATCH", "16"))
    max_tokens_per_batch = int(os.environ.get("EMBEDDING_MAX_TOKENS", "8000"))

    def _embed_batch(texts: List[str]) -> List[List[float]]:
        resp = client.embeddings.create(input=texts, model=ep["model"])
        return [d.embedding for d in resp.data]

    async def _aembed_batch(texts: List[str], **kwargs: Any) -> np.ndarray:
        # LightRAG 在部分路径会带 context="document" 之类的额外参数，统一忽略；
        # 且其 EmbeddingFunc.__call__ 包装层要求本函数返回单个 ndarray（shape [n, dim]）
        loop = asyncio.get_event_loop()

        def _do() -> np.ndarray:
            vecs: List[np.ndarray] = []
            batch: List[str] = []
            batch_chars = 0
            for t in texts:
                batch.append(t)
                batch_chars += len(t)
                if len(batch) >= max_batch or batch_chars >= max_tokens_per_batch:
                    for emb in _embed_batch(batch):
                        vecs.append(np.asarray(emb, dtype="float32"))
                    batch, batch_chars = [], 0
            if batch:
                for emb in _embed_batch(batch):
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
                proxy=None,
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


def _cache_key(ns: str) -> str:
    """实例缓存键 = 用户 + 命名空间（多用户下同名 KB 不串实例）。"""
    try:
        from app.userctx import current_user
        return f"{current_user()}::{ns}"
    except Exception:
        return str(ns)


def _neo4j_workspace(ns: str) -> str:
    """Neo4j 专属 workspace 标签（并入用户名，实现图谱按用户隔离）。

    Neo4j 是跨用户共享的外部图库（Community 版不支持多数据库），而 File 系存储
    （JsonKV / Faiss / NanoVector）靠 per-user working_dir 已天然隔离。LightRAG 的
    ``workspace`` 参数对 File 系存储只是 working_dir 下的子目录（保持纯命名空间
    即可，不改动既有数据布局），但对 Neo4jGraphStorage 直接作为节点 label——
    所有用户若都用 ``__global__`` 会读写同一张图。因此 Neo4j 侧并入用户名。
    """
    from app.userctx import current_user
    ws = f"{current_user()}_{ns}"
    # 仅用于 Neo4j 节点 label（不参与文件路径）：保留字母/数字/下划线/中文，
    # 其余字符（空格、反引号、分隔符等）归一为下划线，避免同名不同用户碰撞。
    ws = re.sub(r"[^\w\u4e00-\u9fff]+", "_", ws, flags=re.UNICODE) or "_"
    return ws

# 图谱命名空间：
#   - 会话对话图谱默认全局共享一张图：所有会话 thread_id 归一化到 ``__global__``，
#     每轮对话自动喂进同一张图，跨会话可共享知识。
#   - 用户通过 kb_create 显式创建的知识库是独立命名空间（每库独立图谱/存储/
#     Neo4j workspace，kb_id 本身即命名空间），仅在勾选后参与检索。
# 可用环境变量 GRAPH_NAMESPACE=per_thread 退回"每会话独立图"的旧隔离行为
# （仅作用于会话对话图谱；显式创建的知识库始终自带独立命名空间）。
_GLOBAL_GRAPH_NS = "__global__"


def _graph_ns(thread_id: str) -> str:
    """把会话 thread_id 解析为实际图谱命名空间。

    - GRAPH_NAMESPACE=global（默认）：会话对话图谱收敛到全局命名空间
      ``__global__``（跨会话共享）。
    - GRAPH_NAMESPACE=per_thread：返回原值，按会话隔离（旧行为）。
    注意：该层只作用于"会话对话图谱"。用户显式创建的知识库（kb_create）
    走 _kb_ns 直接用 kb_id 作为命名空间，不受这里归一化。
    """
    _load_dotenv()
    mode = (os.getenv("GRAPH_NAMESPACE", "global") or "global").strip().lower()
    if mode in ("per_thread", "per-thread", "session", "thread", "isolated"):
        return thread_id
    return _GLOBAL_GRAPH_NS


def _kb_ns(kb_id: str) -> str:
    """知识库命名空间 = 原始 kb_id（不参与对话线程的全局归一化）。

    用户通过 kb_create 创建的知识库是独立图谱：kb_meta/文档/图谱/Neo4j
    workspace 都以 kb_id 为维度隔离，勾选该库才参与检索。
    """
    return str(kb_id).strip() or _GLOBAL_GRAPH_NS


def _kb_resolve_ns(thread_id: str) -> str:
    """解析某 kb 操作实际生效的命名空间。

    规则：若该 id 是已创建的真实知识库（lightrag_storage/<id>/kb_meta.json），
    用 kb_id 本身（独立命名空间）；否则退回 _graph_ns 的会话归一化
    （全局模式下未建库的任意 id → __global__）。per-thread 模式下即 thread_id。
    """
    tid = _kb_ns(thread_id)
    if _graph_mode_global():
        if os.path.isfile(os.path.join(_storage_root(), tid, "kb_meta.json")):
            return tid
        return _graph_ns(thread_id)
    return tid


def _graph_mode_global() -> bool:
    """当前是否为全局共享图谱模式（默认是）。"""
    return _graph_ns("__probe__") == _GLOBAL_GRAPH_NS

# 当前会话上下文：chatbot 节点每轮进入时 set，供 record_graph / lightgraph_query 工具定位当前线程。
# 用锁保护（多线程并发会话时取"最后活跃"的会话，与 .thread_id 文件的全局语义一致）。
_cur_thread: Optional[str] = None
_cur_thread_lock = threading.Lock()
# 本消息当前勾选参与检索的知识库（含 __global__），chatbot 每轮入口由 /api/chat 的
# selected_kbs 写入，供 lightgraph_query 工具与 L6 注入读取。
_cur_kbs: List[str] = []
_cur_kbs_lock = threading.Lock()


_DOC_LOCATOR_PREFIX = "[文档定位]"

# recursive 分块的分隔符级联：长到短，段落 → 换行 → 中文句读 → 空格 → 字符兜底。
# 与 LightRAG DEFAULT_R_SEPARATORS 保持一致的 CJK 友好语义。
_CJK_R_SEPARATORS = ["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""]


def _extract_doc_locator(content: str) -> str:
    """从文本首行提取 '[文档定位] ...' 行，作为每个 chunk 的元数据前缀；无则返回空。"""
    if not content:
        return ""
    first = content.split("\n", 1)[0].strip()
    if first.startswith(_DOC_LOCATOR_PREFIX) and len(first) > len(_DOC_LOCATOR_PREFIX):
        return first
    return ""


_CHUNK_STRATEGIES = ("F", "R", "V", "P", "C")
_CHUNK_STRATEGY_DEFAULT = "R"
_CHUNK_STRATEGY_LABELS = {
    "F": "固定 Token 窗口（LightRAG 默认）",
    "R": "递归字符分块（推荐，段落/句读边界）",
    "V": "语义向量分块（需 langchain-experimental，缺依赖时回退 R）",
    "P": "段落语义分块（需结构化解析 blocks，纯文本时回退 R）",
    "C": "QA 键值对（问答 CSV 一行一对 chunk，整对不拆分）",
}


def _csv_delim_guess(lines: List[str]) -> Optional[str]:
    """粗判分隔符：取首行分列数最多的那个。"""
    if not lines:
        return None
    best, bestn = None, 1
    for d in (",", "\t", ";"):
        n = len(lines[0].split(d))
        if n > bestn:
            best, bestn = d, n
    return best if bestn >= 2 else None


def _qa_col_index(header: List[str], keys) -> Optional[int]:
    """从列头找匹配列；单字母 q/a 需精确匹配，长关键词做子串匹配。"""
    for i, h in enumerate(header):
        for k in keys:
            if k in ("q", "a"):
                if h == k:
                    return i
            elif k in h:
                return i
    return None


def _parse_qa_pairs(text: str, tokenizer: Any) -> list:
    """把文档全文解析成 QA 键值对分块（策略 C 用）。

    支持两种形态：
      - CSV 表格：首行列头含 question/问题/query 与 answer/答案/回复 等列（自动匹配），
        每行一问一答成一对；不识别列头时回退"第一列=问题、第二列=答案"。
      - Q:/A: 前缀文本：Q 行后紧跟 A 行成对；Q 后无前缀的行视作答案。
    每一对保持完整、不做长度切分。返回 [{"content","tokens"}, ...]。
    """
    chunks: list = []
    raw = (text or "").lstrip("\ufeff").strip()
    if not raw:
        return chunks
    lines = raw.split("\n")

    def _mk(q: str, a: str) -> None:
        q = (q or "").replace("\r", "").strip()
        a = (a or "").replace("\r", "").strip()
        if not q or not a:
            return
        content = f"Q: {q}\nA: {a}"
        chunks.append({"content": content, "tokens": len(tokenizer.encode(content))})

    delim = _csv_delim_guess(lines)
    if delim and len(lines) >= 2:
        table = [row for row in csv.reader(io.StringIO(raw), delimiter=delim)]
        if table and len(table[0]) >= 2 and any(len(r) >= 2 for r in table[1:4]):
            hdr = [str(c).strip().lower().lstrip("\ufeff") for c in table[0]]
            qi = _qa_col_index(hdr, ("question", "问题", "query", "题目", "q"))
            ai = _qa_col_index(hdr, ("answer", "答案", "回复", "回答", "response", "reply", "a"))
            if qi is None:
                qi = 0
            if ai is None:
                ai = qi + 1 if qi + 1 < len(hdr) else 0
            if ai == qi:
                ai = qi + 1
            for row in table[1:]:
                if len(row) > max(qi, ai):
                    _mk(row[qi], row[ai])
            return chunks

    # Q:/A: 前缀模式
    q = ""
    for ln in lines:
        s = (ln or "").strip()
        if not s:
            continue
        low = s.lower()
        if low.startswith("q:"):
            q = s[2:].strip()
        elif low.startswith("a:") and q:
            _mk(q, s[2:])
            q = ""
        elif q:
            _mk(q, s)
            q = ""
    return chunks


def _make_chunking_func(strategy: str):
    """按策略返回绑定好的自定义分块函数（LightRAG legacy 6-arg 扩展点）。

    - ``F`` 固定 token 窗口（LightRAG 原生 legacy chunker）；
    - ``R`` 递归字符分块（默认）+ 元数据前缀；
    - ``V`` 语义向量分块（SemanticChunker）；未装 langchain-experimental 时回退 R；
    - ``P`` 段落语义分块；无 ``.blocks.jsonl`` 侧车文件时 LightRAG 内部回退 R；
    - ``C`` 自定义实现（与 R 相同引擎：递归 + 定位前缀）。

    所有策略统一：若文档首行带 ``[文档定位] ...``，把它前置到每个 chunk 并重算
    token 数，让孤立 chunk 向量化时仍带全文主题上下文（低成本版 Contextual Retrieval）。
    """
    strategy = (strategy or _CHUNK_STRATEGY_DEFAULT).upper()
    if strategy not in _CHUNK_STRATEGIES:
        strategy = _CHUNK_STRATEGY_DEFAULT

    def _apply_locator(chunks: list, locator: str, tokenizer: Any) -> list:
        if locator:
            for ch in chunks:
                ch["content"] = f"{locator}\n{ch['content']}"
                ch["tokens"] = len(tokenizer.encode(ch["content"]))
        return chunks

    def _chunking_func(
        tokenizer: Any,
        content: str,
        split_by_character: Optional[str] = None,
        split_by_character_only: bool = False,
        chunk_overlap_token_size: int = 80,
        chunk_token_size: int = 600,
    ):
        from lightrag.chunker.recursive_character import chunking_by_recursive_character
        from lightrag.chunker.token_size import chunking_by_token_size

        locator = _extract_doc_locator(content)
        body = content.split("\n", 1)[1] if locator else content
        size = int(chunk_token_size)
        overlap = int(chunk_overlap_token_size)
        eff_strat = strategy

        if eff_strat == "F":
            chunks = chunking_by_token_size(
                tokenizer, body, split_by_character, split_by_character_only,
                overlap, size,
            )
            return _apply_locator(chunks, locator, tokenizer)

        if eff_strat == "V":
            try:
                import langchain_experimental  # noqa: F401  # 探测依赖
            except ImportError:
                logging.getLogger(__name__).warning(
                    "chunking_strategy=V 但未安装 langchain-experimental，回退 R"
                )
                eff_strat = "R"
            else:
                from lightrag.chunker.semantic_vector import chunking_by_semantic_vector

                async def _v():
                    raw = await chunking_by_semantic_vector(
                        tokenizer, body, chunk_token_size=size,
                        embedding_func=_make_embedding_func(),
                    )
                    return _apply_locator(raw, locator, tokenizer)

                return _v()  # pipeline 会 await 该协程

        if eff_strat == "P":
            from lightrag.chunker.paragraph_semantic import chunking_by_paragraph_semantic
            chunks = chunking_by_paragraph_semantic(
                tokenizer, body, chunk_token_size=size,
                blocks_path=None, chunk_overlap_token_size=overlap,
            )
            return _apply_locator(chunks, locator, tokenizer)

        if eff_strat == "C":
            # QA 键值对分块：每问一答成一个 chunk，整对不拆分；
            # 解析不出 QA 对时回退递归分块（老 C 行为 = R）。
            chunks = _parse_qa_pairs(body, tokenizer)
            if not chunks:
                chunks = chunking_by_recursive_character(
                    tokenizer, body,
                    chunk_token_size=size, chunk_overlap_token_size=overlap,
                    separators=list(_CJK_R_SEPARATORS),
                )
            return _apply_locator(chunks, locator, tokenizer)

        # R：递归字符分块 + 定位前缀
        chunks = chunking_by_recursive_character(
            tokenizer, body,
            chunk_token_size=size, chunk_overlap_token_size=overlap,
            separators=list(_CJK_R_SEPARATORS),
        )
        return _apply_locator(chunks, locator, tokenizer)

    _chunking_func.__name__ = f"_chunking_func_{strategy.lower()}"
    return _chunking_func


def set_current_context(thread_id: Optional[str]) -> None:
    """记录当前正在处理的会话（每轮 graph 节点入口调用）。"""
    global _cur_thread
    with _cur_thread_lock:
        _cur_thread = thread_id


def get_current_context() -> Optional[str]:
    with _cur_thread_lock:
        return _cur_thread


def set_current_kbs(kb_ids: Optional[List[str]]) -> None:
    """记录本轮勾选参与检索的知识库列表（含 __global__，去重启保序）。

    由 chatbot 节点入口从 /api/chat 的 selected_kbs 写入，供 L6 注入与
    lightgraph_query 工具读取。值为 None 时清空（沿用默认只查全局图）。
    """
    global _cur_kbs
    with _cur_kbs_lock:
        _cur_kbs = []
        for k in (kb_ids or []):
            s = str(k or "").strip()
            if s and s not in _cur_kbs:
                _cur_kbs.append(s)


def get_current_kbs() -> List[str]:
    """当前勾选参与检索的知识库列表；未设置时退化为只查全局图谱。"""
    with _cur_kbs_lock:
        return list(_cur_kbs)


def build_lightrag_instance(thread_id: str, ns: Optional[str] = None) -> Any:
    """为某会话/知识库创建 LightRAG 实例（首次创建后缓存，后续复用）。

    - ns 缺省：按 _graph_ns 归一化会话命名空间（默认全局共享 __global__，
      GRAPH_NAMESPACE=per_thread 时按会话隔离）。
    - ns 显式传入：直接用该命名空间建独立实例（用户创建的知识库用 kb_id）。
    缓存键 / work_dir 均以实际命名空间为准；Neo4j workspace 额外并入用户名（图级隔离）。
    """
    ns = (ns or "").strip() or _graph_ns(thread_id)
    _ck = _cache_key(ns)
    with _lock:
        if _ck in _instances:
            return _instances[_ck]
        _patch_faiss_lazy_vectors()
        import lightrag as _lh
        llm = _make_llm_func()
        emb_fn = _make_embedding_func()
        work_dir = os.path.join(os.path.dirname(os.path.dirname(str(DB_PATH))), "lightrag_storage", ns)
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
            # workspace 用于 Neo4j 节点 label 等隔离维度；显式传命名空间，
            # 避免所有会话共用默认 "base" label 导致图互相污染
            "workspace": ns,
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
            "chunking_func": _make_chunking_func(_CHUNK_STRATEGY_DEFAULT),
            "enable_llm_cache": True,
            "enable_llm_cache_for_entity_extract": True,
            "entity_extraction_use_json": True,
        }
        if rerank is not None:
            kwargs["rerank_model_func"] = rerank
        # 每知识库检索配置覆盖（kb_meta.json 里的 config 段；没有则用默认）
        _kb_cfg = (kb_meta(ns).get("config") or {})
        # 分块策略：按知识库配置重建 chunking_func（默认 R）
        _strat = str(_kb_cfg.get("chunking_strategy") or _CHUNK_STRATEGY_DEFAULT).upper()
        kwargs["chunking_func"] = _make_chunking_func(_strat)
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
        # 实体/关系抽取的类型指引（含同义/大小写变体合并规则），经 addon_params 注入：
        # 知识库级覆盖优先，未配置用内置默认。旧 LightRAG 命名变体膨胀的根因在于
        # 每 chunk 独立抽取，这里从 prompt 层面尽量收敛。
        guidance = (_kb_cfg.get("entity_types_guidance") or _DEFAULT_ENTITY_TYPES_GUIDANCE).strip()
        kwargs["addon_params"] = {"entity_types_guidance": guidance}
        # 图谱存储后端：默认 NetworkX(本地 JSON/GraphML)；GRAPH_STORAGE=neo4j 时走项目内 Neo4j
        graph_backend = os.getenv("GRAPH_STORAGE", "networkx").strip().lower()
        _saved_neo4j_workspace = os.environ.get("NEO4J_WORKSPACE")  # type: Optional[str]
        if graph_backend == "neo4j":
            missing = [k for k in ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD") if not os.getenv(k)]
            if missing:
                logging.getLogger(__name__).warning(
                    "GRAPH_STORAGE=neo4j 但缺少 %s，回退 NetworkX", ", ".join(missing)
                )
            else:
                kwargs["graph_storage"] = "Neo4JStorage"
                # Neo4j 为共享外部图库：将 workspace 标签并入用户名做图级隔离。
                # 构造全程在 _lock 临界区内，改环境变量无并发竞争；其余存储只用
                # 纯命名空间（working_dir 已按用户隔离），故不改动 kwargs["workspace"]。
                os.environ["NEO4J_WORKSPACE"] = _neo4j_workspace(ns)
        # 向量存储后端：默认 faiss（未安装则回退 nano）；VECTOR_STORAGE=faiss|nano 可显式控制。
        # 注意：切换后端后旧向量索引不迁移，需在知识库页对文档执行"重建"重新嵌入。
        vec_backend = os.getenv("VECTOR_STORAGE", "").strip().lower() or (
            "faiss" if _faiss_available() else "nano")
        if vec_backend == "faiss":
            if _faiss_available():
                kwargs["vector_storage"] = "FaissVectorDBStorage"
            else:
                logging.getLogger(__name__).warning("VECTOR_STORAGE=faiss 但未安装 faiss-cpu，回退 NanoVectorDB")
        try:
            inst = _lh.LightRAG(**kwargs)
        finally:
            # 构造完成后恢复 NEO4J_WORKSPACE，避免影响其他用户/其他线程的实例构造
            if _saved_neo4j_workspace is None:
                os.environ.pop("NEO4J_WORKSPACE", None)
            else:
                os.environ["NEO4J_WORKSPACE"] = _saved_neo4j_workspace
        _instances[_ck] = inst
        return inst


def get_lightrag(thread_id: str, ns: Optional[str] = None) -> Any:
    """拿到某会话的 LightRAG 实例（单例）。

    ns 缺省时按 _graph_ns 归一化（会话对话图谱默认到 __global__）；
    ns 显式传入时按该知识库命名空间取独立实例。
    """
    return build_lightrag_instance(thread_id, ns=ns)


def _apply_graph_relevance_threshold(rag: Any) -> None:
    """给图谱向量存储（实体/关系）设置更高的相似度阈值，过滤低相关召回。

    背景：LightRAG 默认 ``cosine_better_than_threshold=0.2``，而对 bge-m3 这类
    嵌入，跨领域无关文本的相似度也有 0.3~0.45，导致对话图（__global__）里与
    本轮问题无关的实体/关系被召进 L6 注入与"知识检索"面板。这里只把实体/关系
    向量库的阈值调高；分块向量库（chunks_vdb）保持默认，避免伤害向量库召回。
    可用 ``GRAPH_COSINE_THRESHOLD`` 覆盖（默认 0.5）。
    """
    try:
        thr = float(os.getenv("GRAPH_COSINE_THRESHOLD", "0.5"))
    except Exception:
        thr = 0.5
    for name in ("entities_vdb", "relationships_vdb"):
        vdb = getattr(rag, name, None)
        if vdb is not None and hasattr(vdb, "cosine_better_than_threshold"):
            vdb.cosine_better_than_threshold = thr


def _ensure_initialized(thread_id: str, ns: Optional[str] = None) -> None:
    """确保某会话/知识库的 storages 已在 worker loop 上初始化过。"""
    rag = get_lightrag(thread_id, ns=ns)
    try:
        from lightrag import LightRAG
        status = getattr(rag, "_storages_status", None)
        if status == LightRAG._storages_status.__class__.INITIALIZED:
            _apply_graph_relevance_threshold(rag)
            return
    except Exception:
        pass
    try:
        _run_on_worker(rag.initialize_storages(), timeout=120)
    except Exception:
        # 并发初始化由 LightRAG 内部去重/排队兜底；失败时让后续操作报错更可诊断
        pass
    _apply_graph_relevance_threshold(rag)


_VECTOR_NOOP_STORES = ("chunks_vdb", "entities_vdb", "relationships_vdb")


def _run_ainsert_patched(rag, texts: List[str], target: str) -> Any:
    """在 ainsert 期间按导入目标临时屏蔽建图或向量落盘。

    - target == "vector"：跳过 LLM 实体/关系抽取（_process_extract_entities
      返回空列表 []，merge 阶段照常跑但无候选），只做分块 + 向量嵌入。
    - target == "graph"：把三个向量存储（chunks/entities/relationships_vdb）
      的 upsert/delete/index_done_callback/flush 临时置为 no-op，只做分块 +
      LLM 建图，不写任何向量（含空索引文件）。
    两种空实现都在 ainsert 执行完毕后由 finally 还原实例方法。
    """

    async def _noop(*_a, **_k):
        return None

    async def _no_extract(*_a, **_k):
        return []

    patches = []
    try:
        if target == "vector" and getattr(rag, "_process_extract_entities", None):
            patches.append(
                (rag, "_process_extract_entities", rag._process_extract_entities)
            )
            rag._process_extract_entities = _no_extract
        elif target == "graph":
            for name in _VECTOR_NOOP_STORES:
                store = getattr(rag, name, None)
                if store is None:
                    continue
                for method in ("upsert", "delete", "index_done_callback", "flush"):
                    orig = getattr(store, method, None)
                    if orig is None:
                        continue
                    setattr(store, method, _noop)
                    patches.append((store, method, orig))
        return _run_on_worker(rag.ainsert(texts), timeout=600)
    finally:
        for obj, method, orig in patches:
            setattr(obj, method, orig)


def lightrag_insert(
    thread_id: str, texts: List[str], ns: Optional[str] = None, target: str = "both"
) -> Any:
    """同步桥：在 worker loop 上执行 ainsert。

    入库前先对文本做术语还原（normalize_terms）：把同一概念的常见变体
    （RAG/Rag、GraphRAG/GraphRag 等）统一成规范名，从源头压掉图谱里的
    冗余实体节点（见 app.memory.entity_normalizer）。
    入库后再执行一次图谱级变体合并（merge_entity_variants），把依然漏网
    的大小写变体节点（如 GraphRAG / GraphRag）并在图上合二为一。
    ns 缺省解析会话命名空间（default: __global__）；显式传 ns 写入该知识库。
    target：both（默认，分块+嵌入+建图）/ vector（仅向量，跳过建图）/
    graph（仅图，跳过向量落盘）。
    """
    _ensure_initialized(thread_id, ns=ns)
    rag = get_lightrag(thread_id, ns=ns)
    from app.memory.entity_normalizer import normalize_terms
    texts = [normalize_terms(t or "") for t in texts if t]
    if not texts:
        return None
    result = _run_ainsert_patched(rag, texts, target)
    if target != "vector":
        try:
            merge_entity_variants(thread_id, ns=ns)
        except Exception:
            pass  # 合并是优化项，失败不阻塞主链路
    return result


def merge_entity_variants(thread_id: str, ns: Optional[str] = None) -> dict:
    """把图上现存的大小写/拼写变体实体并入规范名节点（图谱级后置合并）。

    与插入前 normalize_terms 的前置还原组成"双保险"：前置让 LLM 抽取时
    尽量只看到规范名，后置把依然生成的变体节点在图上合二为一。
    Neo4J / NetworkX 存储都通过 has_nodes_batch 探测存在性（不依赖
    NetworkX 私有的 _graph），再交给存储无关的 amerge_entities 合并。
    返回 {规范名: [已合并的变体列表]}，无变体则为空 dict。
    """
    from app.memory.entity_normalizer import canonical_variants

    try:
        rag = get_lightrag(thread_id, ns=ns)
    except Exception:
        return {}

    variant_map = canonical_variants()
    merged: dict = {}
    _ensure_initialized(thread_id, ns=ns)

    async def _do_merge() -> dict:
        storage = getattr(rag, "chunk_entity_relation_graph", None)
        if storage is None:
            return {}
        all_variants = [v for vs in variant_map.values() for v in vs]
        if not all_variants:
            return {}
        try:
            existing = await storage.has_nodes_batch(all_variants) or set()
        except Exception:
            return {}
        for canonical, variants in variant_map.items():
            present = [v for v in variants if v in existing]
            if present:
                try:
                    await rag.amerge_entities(
                        present, canonical,
                        merge_strategy={"description": "concatenate", "entity_type": "keep_first"},
                    )
                    merged[canonical] = present
                except Exception:
                    continue
        return merged

    try:
        return _run_on_worker(_do_merge(), timeout=120)
    except Exception:
        return merged


def _summarize_retrieval(raw: Any, query: str, ns: str, mode: str, top_k: int,
                         duration_ms: float) -> dict:
    """把 LightRAG aquery_llm 的 raw_data 压成前端可展示的检索摘要。

    失败/无 raw 时返回空命中摘要（前端据此显示"未命中"），不影响主链路。
    """
    data = (raw or {}).get("data") or {}
    meta = (raw or {}).get("metadata") or {}
    pi = meta.get("processing_info") or {}
    chunks = data.get("chunks") or []
    entities = data.get("entities") or []
    relations = data.get("relationships") or []
    refs = data.get("references") or []

    def _el(e):
        if not isinstance(e, dict):
            return {"name": str(e)[:80], "description": ""}
        name = e.get("entity_name") or e.get("name") or ""
        return {"name": str(name)[:80], "description": str(e.get("description") or "")[:160]}

    def _rl(r):
        if not isinstance(r, dict):
            return {"src": "", "tgt": "", "description": str(r)[:200]}
        return {
            "src": str(r.get("src_id") or "")[:60],
            "tgt": str(r.get("tgt_id") or "")[:60],
            "description": str(r.get("description") or "")[:180],
        }

    def _ch(c):
        if not isinstance(c, dict):
            return {"content": str(c)[:600], "file": "", "reference_id": ""}
        return {
            "content": str(c.get("content") or "")[:600],
            "file": str(c.get("file_path") or c.get("source") or "")[:120],
            "reference_id": str(c.get("reference_id") or "")[:24],
        }

    return {
        "query": str(query or "")[:300],
        "namespace": str(ns or _GLOBAL_GRAPH_NS),
        "mode": mode or "hybrid",
        "top_k": int(top_k or 6),
        "duration_ms": round(duration_ms, 1),
        "hits": {
            "entities": len(entities),
            "relations": len(relations),
            "chunks": len(chunks),
        },
        "processing": {k: v for k, v in (pi or {}).items() if isinstance(v, (int, float, str))},
        "keywords": {
            "high_level": [str(k) for k in (meta.get("keywords") or {}).get("high_level") or []],
            "low_level": [str(k) for k in (meta.get("keywords") or {}).get("low_level") or []],
        },
        "refs": [
            {
                "id": str(r.get("reference_id") or r.get("ref_id") or "")[:24],
                "file": str(r.get("file_path") or "")[:120],
            }
            for r in (refs[:12] if isinstance(refs, list) else [])
            if isinstance(r, dict)
        ],
        "entities": [_el(e) for e in (entities[:10] if isinstance(entities, list) else [])],
        "relations": [_rl(r) for r in (relations[:10] if isinstance(relations, list) else [])],
        "chunks": [_ch(c) for c in (chunks[:8] if isinstance(chunks, list) else [])],
    }


def _is_graphless(rag: Any) -> bool:
    """判断该 LightRAG 实例是否"无图谱"（纯向量库）。

    以 entities_vdb 的已载入条目数判定：target=vector 的库不建图，实体向量库为空；
    普通对话图谱/混合库的实体向量库非空。取不到内部结构时按"有图谱"处理，保持原行为。
    """
    try:
        ev = getattr(rag, "entities_vdb", None)
        meta = getattr(ev, "_id_to_meta", None)
        if meta is not None:
            return len(meta) == 0
    except Exception:
        pass
    return False


def _auto_query_mode(rag: Any, mode: str) -> str:
    """纯向量库把图谱依赖的检索模式自动降级为 naive，避免空上下文。

    LightRAG 的 local/global/hybrid 只走图谱（实体/关系），对无图谱的纯向量库
    会返回 [no-context]；这类库必须走 naive（或含 naive 的 mix）才能命中分块。
    显式传 naive/mix 时不改动。
    """
    m = (mode or "hybrid").strip().lower()
    if m in ("local", "global", "hybrid") and _is_graphless(rag):
        return "naive"
    return m


def lightrag_query(thread_id: str, query: str, top_k: int = 12, mode: str = "hybrid",
                   ns: Optional[str] = None) -> Any:
    """同步桥：在 worker loop 上执行查询，返回查询文本（str）。

    ns 缺省解析会话命名空间（default: __global__）；显式传 ns 查该知识库。
    用 aquery_llm（而非 aquery）拿到 raw_data，把命中实体/关系/知识片段
    汇总后经 record_retrieval 广播给前端（"知识检索"过程气泡），
    返回文本与原来一致，不影响任何调用方。
    """
    _ensure_initialized(thread_id, ns=ns)
    rag = get_lightrag(thread_id, ns=ns)
    mode = _auto_query_mode(rag, mode)
    from lightrag import QueryParam
    param = QueryParam(mode=mode, top_k=top_k)
    resolved_ns = ns or _graph_ns(thread_id)
    raw = None
    content = ""
    t0 = time.monotonic()
    try:
        if hasattr(rag, "aquery_llm"):
            try:
                raw = _run_on_worker(rag.aquery_llm(query, param, None), timeout=300)
                _llm = (raw or {}).get("llm_response") or {}
                content = _llm.get("content") or ""
            except Exception:
                raw = None  # 失败回退旧 aquery 路径，不抛（保持原有容错）
        if not content:
            resp = _run_on_worker(rag.aquery(query, param), timeout=300)
            content = str(resp)
    finally:
        try:
            dur = (time.monotonic() - t0) * 1000
            if thread_id and content:
                from app.trace import record_retrieval
                record_retrieval(thread_id, _summarize_retrieval(
                    raw, query, resolved_ns, mode, top_k, dur))
        except Exception:
            pass
    return content


def graph_snapshot(thread_id: str, ns: Optional[str] = None) -> dict:
    """导出某会话/知识库当前知识图谱的节点/边列表（只在 worker loop 上读，线程安全）。

    返回 {nodes: [{id, name, kind, attrs}], edges: [{id, from_id, to_id, label, attrs}]}，
    供 /api/graph/nodes、/api/graph/edges 使用。NetworkX 存储读内网 Graph 对象；
    Neo4J 存储读 Cypher 快照。
    """
    def _read() -> dict:
        rag = get_lightrag(thread_id, ns=ns)
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
        rag = get_lightrag(thread_id, ns=ns)
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


def clear_instance(thread_id: str, ns: Optional[str] = None) -> None:
    """清理某会话/知识库的实例（当前用户维度）。"""
    with _lock:
        _instances.pop(_cache_key((ns or "").strip() or _graph_ns(thread_id)), None)


def reset_all() -> None:
    with _lock:
        _instances.clear()


# ==================== 知识库管理（KB） ====================


def _storage_root() -> str:
    """lightrag_storage 根目录（即项目根/lightrag_storage）。"""
    root = os.path.dirname(os.path.dirname(str(DB_PATH)))
    return os.path.join(root, "lightrag_storage")


def _workspace_dir(thread_id: str, ns: Optional[str] = None) -> str:
    """某会话/知识库的 workspace 数据目录（hku 版把数据落在 working_dir/workspace/ 下）。

    会话对话图谱默认解析到全局命名空间 ``__global__``（ns 缺省）；用户创建的知识库
    传 ns=kb_id，把 kv 存储（doc/chunk/entity 等）落在各自库目录下。
    """
    ns = (ns or "").strip() or _graph_ns(thread_id)
    return os.path.join(_storage_root(), ns, ns)


def kb_threads() -> list:
    """列出所有可参与检索的知识库。

    全局共享模式（默认）：
      1) ``__global__`` 全局对话图谱（跨会话共享，每轮对话自动喂养；消息框默认勾选）
      2) 用户通过 kb_create 显式创建的知识库（每库独立命名空间，目录带 kb_meta.json）
    per-thread 模式：按旧行为列出 lightrag_storage 下全部目录（每个会话即独立知识库）。
    按目录修改时间倒序。
    """
    root = _storage_root()

    def _glob_entry():
        d = os.path.join(root, _GLOBAL_GRAPH_NS)
        try:
            mtime = os.path.getmtime(d) if os.path.isdir(d) else 0.0
        except Exception:
            mtime = 0.0
        meta = _read_json_if_exists(os.path.join(d, "kb_meta.json"))
        return {
            "id": _GLOBAL_GRAPH_NS,
            "label": (meta.get("name") or "全局图谱"),
            "dir": d,
            "mtime": _iso_dt(mtime),
            "kind": "global",
        }

    if not _graph_mode_global():
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
                meta = _read_json_if_exists(os.path.join(root, n, "kb_meta.json"))
                out.append({
                    "id": n,
                    "label": (meta.get("name") or n[:13]),
                    "dir": os.path.join(root, n),
                    "mtime": _iso_dt(mtime),
                    "kind": "kb",
                })
        return out

    # KB 目录判定：lightrag_storage/<id>/ 下存在 kb_meta.json，且 id != __global__
    kb_ids = []
    if os.path.isdir(root):
        try:
            kb_ids = sorted(
                (n for n in os.listdir(root)
                 if os.path.isdir(os.path.join(root, n))
                 and not n.startswith(".")
                 and n != _GLOBAL_GRAPH_NS
                 and os.path.isfile(os.path.join(root, n, "kb_meta.json"))),
                key=lambda n: os.path.getmtime(os.path.join(root, n)),
                reverse=True,
            )
        except Exception:
            kb_ids = []

    out = []
    g = _glob_entry()
    if g:
        out.append(g)
    for n in kb_ids:
        try:
            mtime = os.path.getmtime(os.path.join(root, n))
        except Exception:
            mtime = 0.0
        meta = _read_json_if_exists(os.path.join(root, n, "kb_meta.json"))
        out.append({
            "id": n,
            "label": (meta.get("name") or n[:13]),
            "dir": os.path.join(root, n),
            "mtime": _iso_dt(mtime),
            "kind": "kb",
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
    """与 LightRAG 一致的 doc 主键：md5(内容)，前缀 'doc-'。

    须与 LightRAG 实际写入键完全一致（LightRAG 在 hash 前会先执行
    sanitize_text_for_encoding —— 内含 strip/unescape/去控制字符）。
    """
    from lightrag.utils import sanitize_text_for_encoding

    return "doc-" + hashlib.md5(sanitize_text_for_encoding(text).encode("utf-8")).hexdigest()


def kb_status(thread_id: str) -> dict:
    """会话/知识库概况：文档/分块/实体/关系/图谱/后端信息。"""
    ns = _kb_resolve_ns(thread_id)
    rag = get_lightrag(thread_id, ns=ns)
    try:
        _ensure_initialized(thread_id, ns=ns)
    except Exception:
        pass
    ws = _workspace_dir(thread_id, ns=ns)
    docs = _read_json_if_exists(os.path.join(ws, "kv_store_doc_status.json"))
    chunks = _read_json_if_exists(os.path.join(ws, "kv_store_text_chunks.json"))
    entities = _read_json_if_exists(os.path.join(ws, "kv_store_full_entities.json"))
    relations = _read_json_if_exists(os.path.join(ws, "kv_store_full_relations.json"))
    snap = {}
    try:
        snap = graph_snapshot(thread_id, ns=ns)
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
    """某知识库的文档列表（分页，按更新时间倒序）。"""
    page = max(1, int(page))
    page_size = min(200, max(10, int(page_size)))
    ns = _kb_resolve_ns(thread_id)
    docs_raw = _read_json_if_exists(os.path.join(_workspace_dir(thread_id, ns=ns), "kv_store_doc_status.json"))
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
    ns = _kb_resolve_ns(thread_id)
    chunks = _read_json_if_exists(os.path.join(_workspace_dir(thread_id, ns=ns), "kv_store_text_chunks.json"))
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
    ns = _kb_resolve_ns(thread_id)
    rag = get_lightrag(thread_id, ns=ns)
    try:
        _ensure_initialized(thread_id, ns=ns)
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
        hybrid = str(lightrag_query(thread_id, query, top_k=top_k, mode="hybrid", ns=ns))
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


def _with_doc_locator(text: str) -> str:
    """给知识库文档文本加 '[文档定位] ...' 元数据头（供 _chunking_func 逐块前置）。"""
    summary = text.strip().replace("\n", " ")[:40]
    return f"{_DOC_LOCATOR_PREFIX} {summary}\n{text}"


def kb_ingest(thread_id: str, texts: List[str], strategy: str = "", target: str = "both") -> dict:
    """向某知识库喂入文本（同步，含建图）；返回生成的 doc_id 列表。

    每条文本自动加上 '[文档定位] <摘要>' 元数据头：_chunking_func 会把该行
    前置到每个 chunk，让孤立片段向量化时仍保留全文主题上下文。
    真实创建的知识库按 kb_id 独立喂入；其他 id 全局模式下喂入 __global__。

    strategy：本次喂入临时采用的分块策略（F/R/V/P/C，空串=跟随知识库配置）。
    临时切换：上传时指定 C 会把整份 CSV 按"一问一答"切成键值对 chunk。
    target：both（默认，向量+图）/ vector（仅向量库）/ graph（仅图库）。
    """
    texts = [t for t in (texts or []) if isinstance(t, str) and t.strip()]
    if not texts:
        return {"ok": False, "error": "没有可喂入的文本", "doc_ids": []}
    target = target if target in ("vector", "graph") else "both"
    # doc_id 依据加入定位头后的实际 content 计算，与 LightRAG 内部一致
    prefixed = [_with_doc_locator(t) for t in texts]
    doc_ids = [_doc_id_for_text(t) for t in prefixed]
    ns = _kb_resolve_ns(thread_id)
    strat = (strategy or "").strip().upper()
    try:
        if strat in _CHUNK_STRATEGIES:
            # 临时按本次上传的分块策略跑（ainsert 无 process_options 时走 legacy
            # chunking_func 路径，patch 实例的 chunking_func 即可按策略分块）
            rag = get_lightrag(thread_id, ns=ns)
            old = getattr(rag, "chunking_func", None)
            try:
                rag.chunking_func = _make_chunking_func(strat)
                lightrag_insert(thread_id, prefixed, ns=ns, target=target)
            finally:
                rag.chunking_func = old
        else:
            lightrag_insert(thread_id, prefixed, ns=ns, target=target)
    except Exception as e:
        return {"ok": False, "error": f"喂入失败: {e}", "doc_ids": []}
    # label 用原始文本摘要（不带定位头），doc_ids 用加头后的
    _patch_text_doc_paths(thread_id, texts, doc_ids, ns=ns)
    return {"ok": True, "doc_ids": doc_ids}


# ---------------------------------------------------------------------------
# 后台异步喂入（串行 + 限速 + 429 退避重试 + 进度）
#
# 大文本/大文件自动拆成多段后，若一次性并行喂入，嵌入 API 会瞬间打爆 TPM
# 限流（429），导致整批文档失败。因此改为：后台线程逐段串行喂入，段与段之间
# 间隔 _INGEST_PACE_SECONDS 秒限速；单段喂入后主动核对 doc_status，失败时按
# 指数退避重试。前端轮询 ingest_progress 渲染进度条（含每段状态）。
# ---------------------------------------------------------------------------

_INGEST_JOBS: dict = {}
_INGEST_PACE_SECONDS = 4.0  # 段与段之间的最小间隔（嵌入 API TPM 限速）
_INGEST_MAX_RETRY = 3  # 单段最多额外重试次数
_INGEST_BACKOFF = (20, 40, 80)  # 429 退避秒数
_INGEST_DONE_STATUS = ("processed", "completed", "complete")


def _doc_status_rec(thread_id: str, ns: str, doc_id: str) -> dict:
    """读取 kv_store_doc_status.json 中某文档的记录（键兼容/不含 doc- 前缀两种写法）。"""
    ws = _workspace_dir(thread_id, ns=ns)
    data = _read_json_if_exists(os.path.join(ws, "kv_store_doc_status.json")) or {}
    rec = data.get(doc_id)
    if rec is None and doc_id.startswith("doc-"):
        rec = data.get(doc_id[4:])
    return rec or {}


def _start_ingest_job(thread_id: str, ns: str, entries: List[tuple], strategy: str, target: str, kind: str) -> str:
    """入队一个后台喂入/重试作业，返回 track_id。entries=[(text, doc_id, label_or_None)]。"""
    import uuid

    track_id = f"{kind}_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
    job = {
        "track_id": track_id,
        "kind": kind,
        "status": "running",
        "total": len(entries),
        "done": 0,
        "failed": 0,
        "started": time.time(),
        "error": None,
        "cancelled": False,
        "docs": [{"idx": i, "status": "queued", "doc_id": str(did)} for i, (_, did, _) in enumerate(entries)],
    }
    _INGEST_JOBS[track_id] = job
    t = threading.Thread(
        target=_run_ingest_job, args=(thread_id, ns, entries, strategy, target, job),
        name=f"kb-{kind}-{track_id[:12]}", daemon=True,
    )
    t.start()
    return track_id


def _insert_doc_with_retry(thread_id: str, ns: str, doc_id: str, text: str, target: str, active_chunk_func: Any) -> None:
    """单段喂入，喂完核对 doc_status；非成功则按退避重试（应对嵌入 429 限流）。"""
    nid = doc_id if doc_id.startswith("doc-") else "doc-" + doc_id
    for attempt in range(_INGEST_MAX_RETRY + 1):
        rag = get_lightrag(thread_id, ns=ns)
        old = getattr(rag, "chunking_func", None)
        try:
            if active_chunk_func is not None:
                rag.chunking_func = active_chunk_func
            lightrag_insert(thread_id, [text], ns=ns, target=target)
        finally:
            rag.chunking_func = old
        rec = _doc_status_rec(thread_id, ns, nid)
        status = str(rec.get("status") or "").lower()
        if status in _INGEST_DONE_STATUS:
            return
        if attempt >= _INGEST_MAX_RETRY:
            err = str(rec.get("error_msg") or "")[:160]
            raise RuntimeError(f"文档未处理成功（最终状态: {status or '未知'}；{err}）")
        time.sleep(_INGEST_BACKOFF[min(attempt, len(_INGEST_BACKOFF) - 1)])


def _run_ingest_job(thread_id: str, ns: str, entries: List[tuple], strategy: str, target: str, job: dict) -> None:
    """后台作业主循环：逐段串行喂入 + 限速 + 退避重试，实时写 job['docs']。"""
    strat = (strategy or "").strip().upper()
    active = _make_chunking_func(strat) if strat in _CHUNK_STRATEGIES else None
    try:
        for i, (text, doc_id, label) in enumerate(entries):
            if job.get("cancelled"):
                break
            job["docs"][i] = {"idx": i, "status": "processing", "doc_id": str(doc_id)}
            try:
                _insert_doc_with_retry(thread_id, ns, doc_id, text, target, active)
                job["docs"][i] = {"idx": i, "status": "done", "doc_id": str(doc_id)}
                job["done"] += 1
            except Exception as e:
                job["docs"][i] = {"idx": i, "status": "failed", "doc_id": str(doc_id), "error": str(e)[:300]}
                job["failed"] += 1
            if i < len(entries) - 1 and not job.get("cancelled"):
                time.sleep(_INGEST_PACE_SECONDS)
    except Exception as e:  # 作业级兜底，避免线程无声退出
        job["status"] = "error"
        job["error"] = str(e)
        return
    if not job.get("cancelled"):
        try:
            _patch_text_doc_paths(
                thread_id,
                [label for _, _, label in entries],
                [did for _, did, _ in entries],
                ns=ns,
            )
        except Exception:
            pass
    job["status"] = "cancelled" if job.get("cancelled") else "done"


def kb_ingest_async(thread_id: str, texts: List[str], strategy: str = "", target: str = "both") -> dict:
    """异步喂入多段文本：立即返回 track_id，后台串行处理（限速 + 429 退避重试），
    前端可轮询 kb_ingest_progress 获取进度。返回结构兼容原同步 kb_ingest 的字段。"""
    texts = [t for t in (texts or []) if isinstance(t, str) and t.strip()]
    if not texts:
        return {"ok": False, "error": "没有可喂入的文本", "doc_ids": [], "track_id": "", "splits": 0}
    target = target if target in ("vector", "graph") else "both"
    prefixed = [_with_doc_locator(t) for t in texts]
    doc_ids = [_doc_id_for_text(t) for t in prefixed]
    ns = _kb_resolve_ns(thread_id)
    entries = list(zip(prefixed, doc_ids, texts))
    track_id = _start_ingest_job(thread_id, ns, entries, strategy, target, "ingest")
    return {
        "ok": True,
        "track_id": track_id,
        "splits": len(entries),
        "total_splits": len(entries),
        "auto_split": len(entries) > 1,
        "doc_ids": doc_ids,
    }


def kb_retry_failed_async(thread_id: str, strategy: str = "", target: str = "both") -> dict:
    """把该知识库所有 failed 状态的文档按原内容重新喂入（后台串行 + 限速重试）。"""
    ns = _kb_resolve_ns(thread_id)
    ws = _workspace_dir(thread_id, ns=ns)
    data = _read_json_if_exists(os.path.join(ws, "kv_store_doc_status.json")) or {}
    full = _read_json_if_exists(os.path.join(ws, "kv_store_full_docs.json")) or {}
    entries: List[tuple] = []
    for key, rec in sorted(
        data.items(), key=lambda kv: str((kv[1] or {}).get("updated_at") or ""), reverse=True
    ):
        if str((rec or {}).get("status") or "").lower() != "failed":
            continue
        nid = key if key.startswith("doc-") else "doc-" + key
        fd = full.get(nid)
        if not isinstance(fd, dict):
            fd = full.get(key)
        content = str((fd or {}).get("content") or "")
        if not content.strip():
            continue
        entries.append((content, nid, None))
    if not entries:
        return {"ok": False, "error": "没有可重试的失败文档", "track_id": "", "splits": 0}
    track_id = _start_ingest_job(thread_id, ns, entries, strategy, target, "retry")
    return {"ok": True, "track_id": track_id, "total": len(entries), "splits": len(entries), "doc_ids": [e[1] for e in entries]}


def kb_ingest_progress(track_id: str) -> dict:
    """返回后台喂入作业进度（供前端轮询渲染进度条）。"""
    job = _INGEST_JOBS.get(track_id or "")
    if not job:
        return {"ok": False, "error": f"未知的 feed 任务: {track_id}", "track_id": track_id or ""}
    total = max(1, int(job["total"]))
    return {
        "ok": True,
        "track_id": track_id,
        "kind": job.get("kind"),
        "status": job["status"],
        "total": int(job["total"]),
        "done": int(job["done"]),
        "failed": int(job["failed"]),
        "remaining": max(0, int(job["total"]) - int(job["done"]) - int(job["failed"])),
        "percent": int(int(job["done"]) * 100 / total),
        "error": job.get("error"),
        "started": job.get("started"),
        "elapsed": round(time.time() - float(job.get("started") or time.time()), 1),
        "docs": list(job.get("docs") or []),
    }


def kb_ingest_cancel(track_id: str) -> dict:
    """取消后台喂入作业（进行中的当前段会跑完，之后不再继续）。"""
    job = _INGEST_JOBS.get(track_id or "")
    if not job:
        return {"ok": False, "error": f"未知的 feed 任务: {track_id}"}
    job["cancelled"] = True
    return {"ok": True, "track_id": track_id}


def _patch_text_doc_paths(thread_id: str, texts: List[str], doc_ids: List[str], ns: Optional[str] = None) -> None:
    """文本喂入后，把 doc_status 里的 file_path 从 'unknown_source' 改为有意义的摘要。"""
    ws = _workspace_dir(thread_id, ns=ns)
    path = os.path.join(ws, "kv_store_doc_status.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return
    changed = False
    for text, doc_id in zip(texts, doc_ids):
        if not text:
            continue
        nid = doc_id if doc_id.startswith("doc-") else "doc-" + doc_id
        rec = data.get(nid)
        if not isinstance(rec, dict):
            continue
        if str(rec.get("file_path") or "") == "unknown_source":
            label = "文本粘贴"
            snippet = text.strip().replace("\n", " ")[:40]
            if snippet:
                label += f" - {snippet}"
            rec["file_path"] = label
            changed = True
    if changed:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


def kb_delete_doc(thread_id: str, doc_id: str) -> dict:
    """删除某文档及其分块/图谱/向量数据。"""
    nid = doc_id if str(doc_id).startswith("doc-") else "doc-" + str(doc_id)
    ns = _kb_resolve_ns(thread_id)
    rag = get_lightrag(thread_id, ns=ns)
    try:
        _ensure_initialized(thread_id, ns=ns)
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
    ns = _kb_resolve_ns(thread_id)
    rag = get_lightrag(thread_id, ns=ns)
    try:
        _ensure_initialized(thread_id, ns=ns)
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
        lightrag_insert(thread_id, [str(content)], ns=ns)
        return {"ok": True, "doc_id": nid}
    except Exception as e:
        return {"ok": False, "doc_id": nid, "error": f"重建失败: {e}"}


# ==================== RAG 管理台（dashboard / 知识库元信息 / 检索调试） ====================

# 实体抽取的类型指引，注入每次实体/关系抽取 prompt 的 ---Entity Types--- 段。
# 相比 LightRAG 默认值，额外强约束"同义/大小写变体合并为一个实体名"，
# 抑制同一概念被抽成 RAG/Rag、GraphRAG/GraphRag、Microsoft/Microsoft
# Research 等多个节点导致的图谱膨胀。用户可在 kb_meta.json 的
# config.entity_types_guidance 里按知识库覆盖。
_DEFAULT_ENTITY_TYPES_GUIDANCE = """Classify each entity using one of the following types. If no type fits, use `Other`.

- Person: Human individuals, real or fictional
- Creature: Non-human living beings (animals, mythical beings, etc.)
- Organization: Companies, institutions, government bodies, groups
- Location: Geographic places (cities, countries, buildings, regions)
- Event: Occurrences, incidents, ceremonies, meetings
- Concept: Abstract ideas, theories, principles, beliefs
- Method: Procedures, techniques, algorithms, workflows
- Content: Creative or informational works (books, articles, films, reports)
- Data: Quantitative or structured information (statistics, datasets, measurements)
- Artifact: Physical or digital objects created by humans (tools, software, devices)
- NaturalObject: Natural non-living objects (minerals, celestial bodies, chemical compounds)

Consolidation rules (apply BEFORE outputting any entity):
- Case/accent variants of the same term are ONE entity: choose ONE canonical name
  (prefer the widely used official form, e.g. `RAG`, `KAG`, `OAG`, `GraphRAG`
  or the full spelled-out term) and use it consistently everywhere.
- Abbreviations and their full forms (e.g. `LLM` and `Large Language Model`)
  are merged into ONE entity using a single canonical name.
- Different spelling of the same real-world object (e.g. `GraphRAG` vs
  `GraphRag` vs `Graphrag`) must NOT produce separate entities.
- Only create a new entity when it is a genuinely different concept."""

_KB_DEFAULT_CONFIG = {
    "top_k": 12,
    "threshold": 0.2,           # 相似度阈值（余弦，前端"仅检索"过滤用）
    "rerank": True,             # 重排开关（应用于实例构建）
    "hybrid": True,             # 混合检索默认开关（检索调试面板默认 mode）
    "chunk_token_size": 600,
    "chunk_overlap_token_size": 80,
    "chunking_strategy": _CHUNK_STRATEGY_DEFAULT,
    "entity_extract_max_entities": 30,
    "max_entity_tokens": 3000,
    "max_relation_tokens": 5000,
    "entity_types_guidance": _DEFAULT_ENTITY_TYPES_GUIDANCE,
}


def _kb_meta_path(thread_id: str, ns: Optional[str] = None) -> str:
    return os.path.join(_storage_root(), (ns or "").strip() or _graph_ns(thread_id), "kb_meta.json")


def kb_meta(thread_id: str) -> dict:
    """读某知识库元信息（kb_meta.json）；不存在则返回带默认名的占位。

    命名空间解析：真实创建的知识库用 kb_id 本身；其他 id（全局模式下）
    落到 __global__（对话全局图谱的元信息）。
    """
    p = _kb_meta_path(thread_id, ns=_kb_resolve_ns(thread_id))
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {"name": thread_id[:13], "description": "", "config": _KB_DEFAULT_CONFIG, "created_at": ""}


def kb_save_meta(thread_id: str, meta: dict) -> None:
    ns = _kb_resolve_ns(thread_id)
    os.makedirs(os.path.dirname(_kb_meta_path(thread_id, ns=ns)), exist_ok=True)
    with open(_kb_meta_path(thread_id, ns=ns), "w", encoding="utf-8") as f:
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
    clear_instance(thread_id, ns=_kb_resolve_ns(thread_id))
    return kb_get_config(thread_id)


def kb_create(thread_id: Optional[str] = None, name: str = "", description: str = "") -> dict:
    """新建知识库：生成独立 kb_id（命名空间）、建目录结构、写元信息。

    与 __global__ 对话全局图谱完全独立：每个库有自己的 lightrag_storage/<kb_id>/
    目录、Neo4j workspace 与检索配置。返回的 thread_id 即 kb_id，勾选该库后
    L6 检索会在其独立图谱上执行。
    """
    import uuid
    tid = (thread_id or "").strip() or uuid.uuid4().hex[:20]
    if tid == _GLOBAL_GRAPH_NS:
        # 全局图谱由对话自动喂养，不允许被"新建知识库"抢占
        tid = uuid.uuid4().hex[:20]
    ns = _kb_ns(tid)
    # 库刚创建时 _kb_resolve_ns 看不到 kb_meta.json（还没写），必须先建目录
    # 再直接往 kb 自身命名空间落元信息，避免被归到 __global__。
    ws = _workspace_dir(tid, ns=ns)
    os.makedirs(ws, exist_ok=True)
    meta = {
        "name": (name or "").strip() or tid[:13],
        "description": (description or "").strip(),
        "config": _KB_DEFAULT_CONFIG,
        "created_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
    }
    os.makedirs(os.path.dirname(_kb_meta_path(tid, ns=ns)), exist_ok=True)
    with open(_kb_meta_path(tid, ns=ns), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return {"ok": True, "thread_id": tid, "name": meta["name"], "description": meta["description"]}


def kb_list() -> list:
    """所有知识库：元信息 + 轻量计数（直接扫文件，不初始化实例）。"""
    out = []
    for t in kb_threads():
        tid = t["id"]
        ns = _kb_resolve_ns(tid)
        ws = _workspace_dir(tid, ns=ns)
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
            "kind": t.get("kind", ""),
        })
    return out


def kb_delete_thread(thread_id: str) -> dict:
    """删除整个知识库（本地目录 + 尽力删 Neo4j 该 label 节点 + 清除实例）。

    只删除显式 kb_id 对应的独立命名空间目录；__global__ 全局对话图谱不允许删除。
    """
    import shutil
    tid = _kb_ns(str(thread_id))
    if tid == _GLOBAL_GRAPH_NS:
        return {"ok": False, "thread_id": tid, "error": "全局对话图谱不允许删除"}
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
    clear_instance(tid, ns=tid)
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
            ns = _kb_resolve_ns(t["id"])
            recs = _read_json_if_exists(os.path.join(_workspace_dir(t["id"], ns=ns), "kv_store_doc_status.json"))
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
                         headers={"User-Agent": "Mozilla/5.0"},
                         proxy=None)
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
    ns = _kb_resolve_ns(thread_id)
    rag = get_lightrag(thread_id, ns=ns)
    try:
        _ensure_initialized(thread_id, ns=ns)
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
    ns = _kb_resolve_ns(thread_id)
    rag = get_lightrag(thread_id, ns=ns)
    try:
        _ensure_initialized(thread_id, ns=ns)
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
