"""app.memory.graph_rag_tool — GraphRAG（L6）层：把对话知识落成 LightRAG 图谱 + 查询接口。

设计：
  0. 全量喂养：chatbot 每轮把 用户消息+助手回答 异步喂进全局对话图谱（_feed_turn_async），
     图谱随对话自动长，跨会话共享（__global__）。
  1. record_graph 工具：让 LLM 在合适时机显式"记住"几个实体关系（比如从搜索结果里抽三元组）。
     LLM 调用这个工具后，内容会被写进 LightRAG 的文本库，触发自动的实体提取 + 关系抽取。
  2. lightgraph_query 工具：给 LLM 提供一个直接查图检索的能力——检索范围完全由
     本轮勾选决定，勾选多个库则各自查询并合并结果。
  3. 被动注入：chatbot 每轮构造 system prompt 时，会用当前 query 做一次 LightRAG 查询，
     把命中实体/关系摘要拼入 "=== 分层记忆注入 ===" 块末尾（L6 层）。
     检索范围 = 本轮勾选的知识库（selected_kbs）；勾了什么查什么，什么都没勾就不检索。
     全局对话图谱（__global__）只是列表里的一项，勾选才参与。

存储：
  LightRAG 把知识持久化在 <repo_root>/lightrag_storage/<ns>/ 下（JsonKV + NetworkX + NanoVectorDB），
  进程重启后图谱不丢。__global__ 是跨会话共享的对话图谱；用户创建的知识库是独立命名空间。
"""
from __future__ import annotations
import json
import threading
from typing import Any, Dict, List, Optional

from app.memory.ligraphrag_adapter import (
    get_lightrag, clear_instance, lightrag_insert, lightrag_query,
    _graph_ns, _GLOBAL_GRAPH_NS, get_current_kbs,
)


def _namespaces(thread_id: str, kb_ids: Optional[List[str]] = None) -> List[str]:
    """本次查询实际覆盖的命名空间列表（去重保序）。

    检索范围完全以勾选列表为准：**勾了什么查什么，什么都没勾就什么都不查**
    （不再默认追加会话对话图谱）。
      - kb_ids 显式传入：直接使用该列表；
      - kb_ids 为 None：取当前会话勾选（get_current_kbs()）；
      - 结果为空 → 返回空列表，调用方据此跳过全部图谱检索。

    列表里的 ``__global__`` 视为"会话对话图谱"别名，解析为 _graph_ns(thread_id)
    （全局模式下即 __global__，per_thread 模式下即会话 id）。
    """
    cur = list(kb_ids) if kb_ids is not None else list(get_current_kbs())
    session_ns = _graph_ns(thread_id)
    out: List[str] = []
    for k in cur:
        s = str(k or "").strip()
        if not s:
            continue
        if s == _GLOBAL_GRAPH_NS:
            s = session_ns
        if s not in out:
            out.append(s)
    return out


def lightgraph_query(thread_id: str, query: str, top_k: int = 12,
                     kb_ids: Optional[List[str]] = None) -> str:
    """对 LightRAG 发起混合检索（向量 + 图谱双路召回），范围 = 本轮勾选的知识库。

    返回格式化后的上下文；每个命名空间的结果用 [库名] 前缀区分。
    未勾选任何库时返回空字符串；单个命名空间失败不阻塞其余空间。
    """
    parts = []
    for ns in _namespaces(thread_id, kb_ids):
        try:
            rag = get_lightrag(thread_id, ns=ns)
            result = lightrag_query(thread_id, query, top_k=top_k, ns=ns)
            ctx = str(result or "").strip()
            if not ctx:
                continue
            nslabel = "全局图谱" if ns == _graph_ns(thread_id) and ns.startswith("__") else str(ns)[:16]
            parts.append(f"【{nslabel}】\n{ctx}")
        except Exception as e:
            # 静默降级：单个命名空间不可用时不阻塞主对话
            parts.append(f"【{ns}】检索失败：{e}")
    return "\n\n".join(parts)


def record_graph(thread_id: str, triplets_json: str, source: str = "user") -> str:
    """把一组 (head, relation, tail) 三元组写进 LightRAG。

    triplets_json：JSON 字符串，格式为 [{"head": "...", "rel": "...", "tail": "..."}]。
    source：来源标识（如 "user"/"search"/"llm_extracted"），方便后续追溯。

    返回摘要（实际写入的三元组数量）。
    """
    try:
        triplets = _triage_triplets_json(triplets_json)
    except Exception:
        return "JSON 解析失败，已忽略"
    if not isinstance(triplets, list) or not triplets:
        return "无有效三元组"
    try:
        rag = get_lightrag(thread_id)
        # LightRAG 的 ainsert 接受纯文本；我们把三元组序列化成可读文本再喂进去
        docs = []
        for t in triplets:
            head = str(t.get("head", "")).strip()
            rel = str(t.get("rel", "")).strip()
            tail = str(t.get("tail", "")).strip()
            if not head or not rel or not tail:
                continue
            docs.append(f"{head} {rel} {tail} (来源: {source})")
        if not docs:
            return "无有效三元组（需要 head/rel/tail 全非空）"
        lightrag_insert(thread_id, docs)
        return f"已记录 {len(docs)} 条实体关系"
    except Exception as e:
        return f"写入失败：{e}"


def _try_ingest_lightrag(thread_id: str, text: str) -> None:
    """轻量被动注入：把一段文本（如 tavily 搜索结果）异步塞进 LightRAG。
    不等待、不阻塞主流程；出错静默吞掉。
    """
    if not text or len(text) < 30:
        return
    try:
        rag = get_lightrag(thread_id)
        # LightRAG 内部会做 chunking + 实体提取 + 关系抽取，这里只喂文本
        lightrag_insert(thread_id, [text[:4000]])
    except Exception:
        pass


def _feed_turn_async(thread_id: str, user_text: str, assistant_text: str) -> None:
    """对话全量喂养（L6）：每个回合把 用户消息 + 助手回答 塞进 LightRAG 建图。

    在后台 daemon 线程里执行，不阻塞主回复；LightRAG 自动做实体/关系抽取，
    让图谱随对话自然增长。失败静默吞掉（图谱只是增强，不能拖垮主流程）。
    """
    if not thread_id:
        return
    parts = []
    if user_text:
        parts.append(f"[用户] {user_text.strip()[:2000]}")
    if assistant_text:
        parts.append(f"[助手] {assistant_text.strip()[:4000]}")
    text = "\n".join(p for p in parts if p)
    if len(text) < 30:
        return

    def _run() -> None:
        try:
            rag = get_lightrag(thread_id)
            lightrag_insert(thread_id, [text[:6000]])
        except Exception:
            pass  # 静默：图谱不可用时不阻塞对话

    threading.Thread(target=_run, name=f"lightrag-feed-{thread_id[:8]}", daemon=True).start()


def _triage_triplets_json(raw: str) -> list:
    """把各种形状的用户输入归一成 list[dict]。"""
    raw = (raw or "").strip()
    if not raw:
        return []
    # 已经是 JSON 数组就直返
    if raw.startswith("["):
        try:
            return json.loads(raw)
        except Exception:
            pass
    # 形如 "head - rel > tail" 的文本行，按行拆分
    lines = [l.strip() for l in raw.split("\n") if l.strip()]
    out = []
    for line in lines:
        # 尝试解析单行 JSON 对象
        if line.startswith("{"):
            try:
                obj = json.loads(line)
                if isinstance(obj, dict) and obj.get("head") and obj.get("rel") and obj.get("tail"):
                    out.append(obj)
                continue
            except Exception:
                pass
        # 朴素分隔符：head - rel > tail / head → rel → tail / head,rel,tail
        for sep in [" - ", " → ", ",", "|"]:
            parts = [p.strip() for p in line.split(sep) if p.strip()]
            if len(parts) >= 3:
                out.append({"head": parts[0], "rel": parts[1], "tail": parts[2]})
                break
    return out


def get_l6_context(thread_id: str, query: str, limit_chars: int = 1500,
                   kb_ids: Optional[List[str]] = None) -> str:
    """给 chatbot system prompt 的 L6 注入片段。

    仅覆盖本轮勾选的知识库（含勾选时才有的全局对话图谱），每个命名空间回退到多行摘要。
    返回形如：
      【GraphRAG（知识图谱检索）】
      - Entity: HarmonyOS 5.0  supports  DeepSeek V4
      - Entity: 纳塔  releases  2026-08-28
      （最多 limit_chars 字符）
    """
    ns_list = _namespaces(thread_id, kb_ids)
    blocks = []
    budget = limit_chars
    per = max(limit_chars // max(len(ns_list), 1), 60)
    for ns in ns_list:
        if budget <= 0:
            break
        try:
            ctx = str(lightrag_query(thread_id, query, top_k=6, ns=ns) or "").strip()
            nslabel = "全局图谱" if (ns == _graph_ns(thread_id) and ns.startswith("__")) else str(ns)[:16]
            if ctx:
                lines = []
                for line in ctx.splitlines()[:10]:
                    s = line.strip()
                    if not s:
                        continue
                    if len(s) > per // 3:
                        s = s[: per // 3] + "…"
                    lines.append(f"- [{nslabel}] {s}")
                if lines:
                    blocks.append("【GraphRAG（知识图谱检索）】\n" + "\n".join(lines))
        except Exception:
            pass  # 单个命名空间失败静默，不阻塞 L6 注入
        budget -= per
    return "\n\n".join(blocks)


# ---- 给 LLM 注册的工具描述（让 planner/chatbot 知道什么时候该用） ----
RECORD_GRAPH_DESC = """
record_graph 工具：把实体关系三元组显式记录下来。适合这些场景：
  - 搜索结果/长文本里出现多个明确的事实关系（如 "A 支持 B"、"C 由 D 发布"）
  - 用户明确说"记住 X 和 Y 的关系"
格式：triplets_json 是 JSON 数组，每项 {"head":"...", "rel":"...", "tail":"..."}。
返回：已记录的三元组数。如果无法解析会返回失败原因。
注意：不需要把整段话复制进来——只抽最关键的实体关系（3~10 条足够）。
"""

LIGHTGRAPH_QUERY_DESC = """
lightgraph_query 工具：在会话的知识图谱里做混合检索（向量 + 图谱双路召回）。
适合：
  - 用户问"之前我提到过的 XX 是啥"（跨消息/跨时间的关联）
  - 用户问"根据你记住的知识，XX 和 YY 有什么关系"
  - 需要综合多个文档/会话里的分散信息时
返回：相关实体/关系摘要。空结果表示图谱里没命中。
"""
