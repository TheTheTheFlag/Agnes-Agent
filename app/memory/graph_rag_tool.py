"""app.memory.graph_rag_tool — GraphRAG（L6）层：把对话知识落成 LightRAG 图谱 + 查询接口。

设计：
  1. record_graph 工具：让 LLM 在合适时机显式"记住"几个实体关系（比如从搜索结果里抽三元组）。
     LLM 调用这个工具后，内容会被写进 LightRAG 的文本库，触发自动的实体提取 + 关系抽取。
  2. lightgraph_query 工具：给 LLM 提供一个直接查图检索的能力——当用户问"上次你提到的 HarmonyOS 5.0 支持哪家大模型"这类跨会话/跨文档关联问题时使用。
  3. 被动注入：chatbot 每轮构造 system prompt 时，会用当前 query 做一次 LightRAG 查询，
     把命中实体/关系摘要拼入 "=== 分层记忆注入 ===" 块末尾（L6 层）。

存储：
  LightRAG 把知识持久化在 <repo_root>/lightrag_storage/<thread_id>/ 下（JsonKV + NetworkX + NanoVectorDB），
  进程重启后图谱不丢。每个 thread 独立索引，避免不同会话的知识串扰。
"""
from __future__ import annotations
import json
from typing import Any, Dict, List, Optional

from app.memory.ligraphrag_adapter import get_lightrag, clear_instance, lightrag_insert, lightrag_query


def lightgraph_query(thread_id: str, query: str, top_k: int = 12) -> str:
    """对 LightRAG 发起混合检索（向量 + 图谱双路召回），返回格式化后的上下文。

    返回空字符串表示未命中；命中时会把实体名称 / 关系描述 / 相关段落摘要一起返回，
    让 chatbot 能看到"知识图谱层面"的信息，而不只是文本片段。
    """
    try:
        rag = get_lightrag(thread_id)
        result = lightrag_query(thread_id, query, top_k=top_k)
        # LightRAG v1.5.7 的 aquery 返回 QueryResult/str；转换并截断避免撑爆 system prompt。
        ctx = str(result or "").strip()
        return ctx if ctx else ""
    except Exception as e:
        # 静默降级：LightRAG 不可用时不阻塞主对话
        return f"[GraphRAG 检索失败：{e}]"


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


def get_l6_context(thread_id: str, query: str, limit_chars: int = 1500) -> str:
    """给 chatbot system prompt 的 L6 注入片段。

    返回形如：
      【GraphRAG（知识图谱检索）】
      - Entity: HarmonyOS 5.0  supports  DeepSeek V4
      - Entity: 纳塔  releases  2026-08-28
      （最多 limit_chars 字符）
    """
    ctx = lightgraph_query(thread_id, query, top_k=6)
    if not ctx:
        return ""
    # 把 LightRAG 返回的混合文本截断到合理长度，避免撑爆 system prompt
    lines = []
    for line in str(ctx).splitlines()[:20]:
        s = line.strip()
        if not s:
            continue
        if len(s) > limit_chars // 3:
            s = s[:limit_chars // 3] + "…"
        lines.append(f"- {s}")
    if not lines:
        return ""
    return "【GraphRAG（知识图谱检索）】\n" + "\n".join(lines[:10])


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
