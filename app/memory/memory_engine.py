"""app.memory.memory_engine — 长期记忆引擎（记忆固化 / 冲突检测 / 语义检索 / 遗忘衰减）。

对标 Mem0 / Zep 的四个能力，全部落地到 memory_facts + memory_chunks 两张表：

  1. 记忆固化（consolidation）：对话结束后，后台异步把值得长期记住的用户事实/偏好
     提取成"成熟记忆"写入 memory_facts，高 importance 的自动注入 system prompt。
  2. 冲突检测与更新：抽取时把新记忆与既有记忆一起交给 LLM，产出 create/update/delete。
  3. 统一语义检索 + 重排：对话、任务、事实统一向量化（复用 SiliconFlow embedding），
     查询时余弦召回 top-N，再走 rerank 精排（复用 SiliconFlow rerank）；embedding/rerank
     不可用时自动降级为 SQL LIKE。
  4. 遗忘与衰减：importance 随时间指数衰减，跌破阈值的记忆被主动遗忘；近期被反复
     访问的记忆升温不衰减。由 server 启动的 daemon 线程周期驱动。

所有环节失败静默降级，不阻塞对话主流程。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger("memory_engine")

# 向量检索召回规模：先取 top(N) 再交给 rerank 精排到 limit
_RECALL = 20


def _configure() -> None:
    """确保 .env 已加载（embedding/rerank/LLM 配置依赖）。"""
    try:
        from app.memory.ligraphrag_adapter import _load_dotenv
        _load_dotenv()
    except Exception:
        pass


def embed_texts(texts: List[str]) -> Optional[List[List[float]]]:
    """同步 Embedding（OpenAI 兼容端点，绕过系统代理）。失败返回 None。"""
    try:
        from app.memory.ligraphrag_adapter import _resolve_endpoint
        ep = _resolve_endpoint("EMBEDDING")
        import httpx as _httpx
        from openai import OpenAI
        _no_proxy_client = _httpx.Client(trust_env=False)
        client = OpenAI(base_url=ep["base_url"], api_key=ep["api_key"],
                        http_client=_no_proxy_client)
        out: List[List[float]] = []
        for i in range(0, len(texts), 16):
            resp = client.embeddings.create(input=texts[i:i + 16], model=ep["model"])
            out.extend(d.embedding for d in resp.data)
        return out
    except Exception as e:
        logger.warning(f"embedding 失败，降级: {e}")
        return None


def rerank(query: str, docs: List[str], top_n: Optional[int] = None) -> Optional[List[int]]:
    """Rerank 精排（SiliconFlow /rerank）。返回按相关度降序的 doc 下标。失败返回 None。"""
    if not docs:
        return []
    try:
        from app.memory.ligraphrag_adapter import _resolve_endpoint
        ep = _resolve_endpoint("RERANK")
        import httpx
        payload = {
            "model": ep["model"],
            "query": query,
            "documents": docs,
            "top_n": top_n or len(docs),
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
        ranked = sorted(results, key=lambda r: r.get("index", 0))
        ranked.sort(key=lambda r: r.get("relevance_score", 0), reverse=True)
        return [int(r["index"]) for r in ranked if isinstance(r, dict) and "index" in r]
    except Exception as e:
        logger.warning(f"rerank 失败，降级: {e}")
        return None


def _cosine(q: np.ndarray, docs: np.ndarray) -> np.ndarray:
    qn = q / (np.linalg.norm(q) + 1e-9)
    dn = docs / (np.linalg.norm(docs, axis=1, keepdims=True) + 1e-9)
    return (dn @ qn).astype(float)


def _embed_bytes_vec(embedding: bytes, dim: int) -> Optional[np.ndarray]:
    if not embedding or dim <= 0:
        return None
    try:
        return np.frombuffer(embedding, dtype=np.float32).reshape(1, dim)
    except Exception:
        return None


def index_texts(kind: str, thread_id: str, texts: List[str],
                ref_id: int = None) -> int:
    """把文本向量化并写入 memory_chunks（kind=conversation/task/fact）。返回写入条数。

    已有完全相同文本的块跳过（按 thread + text 去重）。
    """
    from app.memory import MemoryManager
    from app.config import DB_PATH
    mm = MemoryManager(db_path=DB_PATH)
    texts = [t for t in texts if t and t.strip()]
    if not texts:
        return 0
    vecs = embed_texts(texts)
    if vecs is None:
        return 0
    written = 0
    for t, vec in zip(texts, vecs):
        if not vec:
            continue
        dup = mm.get_embedding_chunks(kinds=(kind,), thread_id=thread_id, limit=100000)
        if any(c["text"] == t for c in dup):
            continue
        blob = np.asarray(vec, dtype=np.float32).tobytes()
        cid = mm.add_memory_chunk(kind, thread_id, t, ref_id=ref_id)
        mm.update_chunk_embedding(cid, blob, len(vec))
        written += 1
    return written


def search_semantic(query: str, limit: int = 5, thread_id: str = None) -> List[Dict]:
    """统一语义检索：跨对话/任务/事实。降级路径为 SQL LIKE。

    返回: [{kind, text, score, source, ref_id, thread_id}]
    """
    from app.memory import MemoryManager
    from app.config import DB_PATH
    mm = MemoryManager(db_path=DB_PATH)

    chunks = mm.get_embedding_chunks(thread_id=thread_id, limit=2000)
    if not chunks:
        return _like_fallback(query, limit, thread_id, mm)

    qvec = embed_texts([query])
    if qvec is None or not chunks:
        return _like_fallback(query, limit, thread_id, mm)

    q = np.asarray(qvec[0], dtype=np.float32)
    rows = []
    for c in chunks:
        v = _embed_bytes_vec(c["vector"], c["dim"])
        if v is None:
            continue
        score = float(_cosine(q, v)[0])
        rows.append({"kind": c["kind"], "text": c["text"], "score": score,
                     "source": c.get("source", ""), "ref_id": c.get("ref_id"),
                     "thread_id": c.get("thread_id", "")})
    rows.sort(key=lambda x: x["score"], reverse=True)
    top = rows[:_RECALL]

    # Rerank 精排（可选）
    ordered = rerank(query, [r["text"] for r in top], top_n=limit) if top else None
    if ordered is not None:
        by_idx = {i: r for i, r in enumerate(top)}
        result = []
        for i in ordered:
            r = by_idx.get(i)
            if r:
                r["score"] = round(r["score"], 4)
                result.append(r)
        return result[:limit]

    for r in top:
        r["score"] = round(r["score"], 4)
    return top[:limit]


def _like_fallback(query: str, limit: int, thread_id: str, mm) -> List[Dict]:
    """keyword fallback：语义检索不可用时的 LIKE 兜底（同 search_my_memory）。"""
    import sqlite3
    from app.config import DB_PATH
    out = []
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.execute(
                """SELECT 'conversation' AS kind, content AS text, '' AS source, thread_id
                   FROM messages WHERE content LIKE ? ORDER BY timestamp DESC LIMIT ?""",
                (f"%{query}%", limit)
            )
            out.extend({"kind": "conversation", "text": (r[1] or "")[:300], "score": 0.0,
                        "source": "keyword", "ref_id": None, "thread_id": r[3]} for r in cur.fetchall())
            cur = conn.execute(
                """SELECT 'task' AS kind, goal AS text, '' AS source, thread_id
                   FROM dag_plans WHERE goal LIKE ? ORDER BY updated_at DESC LIMIT ?""",
                (f"%{query}%", limit)
            )
            out.extend({"kind": "task", "text": (r[1] or "")[:300], "score": 0.0,
                        "source": "keyword", "ref_id": None, "thread_id": r[3]} for r in cur.fetchall())
            cur = conn.execute(
                """SELECT 'fact' AS kind, content AS text, '' AS source, thread_id
                   FROM memory_facts WHERE content LIKE ? ORDER BY importance DESC LIMIT ?""",
                (f"%{query}%", limit)
            )
            out.extend({"kind": "fact", "text": (r[1] or "")[:300], "score": 0.0,
                        "source": "keyword", "ref_id": None, "thread_id": r[3]} for r in cur.fetchall())
    except Exception:
        pass
    return out[:limit]


# ---------------- 记忆固化 + 冲突检测 ----------------

def _triage_json(raw: str) -> Optional[dict]:
    """从 LLM 输出里提取 JSON 对象（容忍 markdown 代码块与前后杂文）。"""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        pass
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```", 1)[-1]
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except Exception:
            return None
    return None


_FACT_CATS = {"fact", "preference", "identity", "relation", "project"}

_EXTRACT_SYSTEM = (
    "你是用户记忆抽取器。从对话中提取值得长期记住的用户信息。\n"
    "只输出 JSON（不要 markdown）："
    '{"memories":[{"content":"一句话","category":"fact|preference|identity|relation|project",'
    '"importance":1到10整数,"action":"create|update|delete","conflict_with":"完全相同的既有记忆文本，否则空串"}]}\n'
    "规则：\n"
    "1. 只抽长期有效的信息（身份、稳定偏好、重要背景、关键关系、进行中的项目），闲聊不抽。\n"
    "2. 若某信息与已列出的记忆相同，action=update 且 conflict_with 填那条旧记忆的完整文本。\n"
    "3. 若用户明确否认/纠正/作废了某条旧记忆，action=delete 且 conflict_with 填它。\n"
    "4. 无新信息则输出 {\"memories\":[]}。最多 3 条。"
)


def extract_facts(llm, conversation_text: str, existing: List[str]) -> List[Dict]:
    """LLM 抽取 + 冲突判定。返回预期 ops。失败返回 []。"""
    if not conversation_text or not conversation_text.strip():
        return []
    existing_block = "\n".join(f"- {f}" for f in existing[:30]) or "（暂无）"
    user_msg = (
        f"已保存记忆：\n{existing_block}\n\n"
        f"最近对话：\n{conversation_text}\n\n"
        "请按规则抽取/更新记忆，只输出 JSON。"
    )
    try:
        from langchain_core.messages import SystemMessage, HumanMessage
        resp = llm.invoke([SystemMessage(content=_EXTRACT_SYSTEM),
                           HumanMessage(content=user_msg)])
        raw = getattr(resp, "content", "") or ""
    except Exception as e:
        logger.warning(f"记忆抽取 LLM 失败: {e}")
        return []
    data = _triage_json(raw)
    if not data:
        return []
    ops = []
    for m in data.get("memories", []) or []:
        if not isinstance(m, dict) or not m.get("content"):
            continue
        content = str(m["content"]).strip()
        if len(content) > 300:
            content = content[:300]
        action = str(m.get("action") or "create")
        if action not in ("create", "update", "delete"):
            action = "create"
        cat = str(m.get("category") or "fact") if str(m.get("category") or "fact") in _FACT_CATS else "fact"
        try:
            imp = float(m.get("importance") or 5.0)
        except Exception:
            imp = 5.0
        imp = max(1.0, min(10.0, imp))
        ops.append({"content": content, "category": cat, "importance": imp,
                    "action": action, "conflict_with": str(m.get("conflict_with") or "")})
    return ops


def apply_fact_ops(mm, ops: List[Dict], thread_id: str) -> Dict:
    """应用抽取结果到 memory_facts。返回 {created, updated, deleted, skipped}。"""
    stat = {"created": 0, "updated": 0, "deleted": 0, "skipped": 0}
    for op in ops:
        action = op["action"]
        conflict = (op.get("conflict_with") or "").strip()
        try:
            if action == "delete" and conflict:
                if mm.delete_memory_fact(content=conflict):
                    stat["deleted"] += 1
                else:
                    stat["skipped"] += 1
            elif action in ("create", "update") and conflict:
                # 更新既有记忆：直接覆盖 content（保留原 id），重要性取较大者
                existing = [f for f in mm.get_memory_facts(limit=100) if f["content"] == conflict]
                if existing:
                    f = existing[0]
                    if f["content"] != op["content"]:
                        mm.delete_memory_fact(fact_id=f["id"])
                    new_imp = max(op["importance"], f.get("importance") or 0)
                    mm.add_memory_fact(op["content"], category=op["category"],
                                       importance=new_imp, source="extraction",
                                       thread_id=thread_id)
                    stat["updated"] += 1
                else:
                    mm.add_memory_fact(op["content"], category=op["category"],
                                       importance=op["importance"], source="extraction",
                                       thread_id=thread_id)
                    stat["created"] += 1
            else:
                mm.add_memory_fact(op["content"], category=op["category"],
                                   importance=op["importance"], source="extraction",
                                   thread_id=thread_id)
                stat["created"] += 1
        except Exception as e:
            logger.warning(f"记忆写入失败: {e}")
            stat["skipped"] += 1
    return stat


def _chunk_text(text: str, size: int = 180, stride: int = 90) -> List[str]:
    """把一段对话切成可向量化的块（带重叠）。"""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    chunks = []
    i = 0
    while i < len(text):
        chunks.append(text[i:i + size])
        i += stride
    return chunks


def consolidate(thread_id: str, user_text: str, assistant_text: str,
                llm=None, extract: bool = True) -> Dict:
    """单轮记忆固化（后台线程调用，不阻塞对话）：

    1. 索引对话块（user + assistant）到 memory_chunks（向量）。
    2. LLM 抽取事实/偏好 → 冲突检测 → 更新 memory_facts。
    3. 新事实同步建立 fact 向量块供检索。

    返回 {"indexed": n, "facts": {...}}。任何失败静默降级。
    """
    from app.memory import MemoryManager
    from app.config import DB_PATH
    mm = MemoryManager(db_path=DB_PATH)
    n = 0
    try:
        texts = _chunk_text(user_text) + _chunk_text(assistant_text)
        n += index_texts("conversation", thread_id, texts)
    except Exception as e:
        logger.warning(f"对话索引失败: {e}")
    stat = {"created": 0, "updated": 0, "deleted": 0, "skipped": 0}
    if extract and llm is not None:
        try:
            existing = [f["content"] for f in mm.get_memory_facts(limit=30)]
            ops = extract_facts(llm, f"用户：{user_text}\n助手：{assistant_text}", existing)
            if ops:
                stat = apply_fact_ops(mm, ops, thread_id)
                # 新/更新的 fact 入检索索引
                recent = mm.get_memory_facts(limit=10)
                for f in recent:
                    index_texts("fact", thread_id, [f["content"]], ref_id=f["id"])
        except Exception as e:
            logger.warning(f"记忆固化失败: {e}")
    return {"indexed": n, "facts": stat}


# ---------------- 遗忘与衰减 daemon ----------------

def decay_cycle(min_importance: float = 2.0) -> Dict:
    """执行一次遗忘衰减。返回 {"decayed": n, "deleted": n}。"""
    from app.memory import MemoryManager
    from app.config import DB_PATH
    mm = MemoryManager(db_path=DB_PATH)
    try:
        return mm.decay_memory_facts(min_importance=min_importance)
    except Exception as e:
        logger.warning(f"记忆衰减失败: {e}")
        return {"decayed": 0, "deleted": 0}


_decay_stop = threading.Event()
_decay_thread: Optional[threading.Thread] = None


def start_decay_daemon(interval_minutes: int = 360) -> threading.Thread:
    """启动后台遗忘/衰减 daemon（默认每 6 小时跑一次）。幂等。"""
    global _decay_thread
    if _decay_thread and _decay_thread.is_alive():
        return _decay_thread
    _decay_stop.clear()

    def _loop():
        while not _decay_stop.is_set():
            try:
                recession = decay_cycle()
                if recession.get("deleted"):
                    logger.info(f"[memory] 主动遗忘: {recession}")
                elif recession.get("decayed"):
                    logger.info(f"[memory] 衰减: {recession}")
            except Exception:
                pass
            _decay_stop.wait(interval_minutes * 60)

    t = threading.Thread(target=_loop, name="memory-decay", daemon=True)
    t.start()
    _decay_thread = t
    return t


def stop_decay_daemon() -> None:
    _decay_stop.set()


def bootstrap(thread_id: str = "") -> None:
    """启动入口：拉衰减 daemon；无 LLM 依赖，轻量。"""
    start_decay_daemon()


if __name__ == "__main__":  # 调试入口：python -m app.memory.memory_engine
    _configure()
    print("decay:", decay_cycle())
    print("daemon started:", start_decay_daemon().name)