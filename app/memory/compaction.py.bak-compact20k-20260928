"""app.memory.compaction — 对话历史的异步压缩（context engineering）

对齐业界做法（LangGraph summarization / Anthropic context compaction / MemGPT 虚拟上下文）：

  触发：按**实际发送预算**（TOKEN_LIMIT）的占比，不再用"距上次 N 秒"这类时间兜底。
        （时间触发是反模式——对话短就不该压缩。旧实现阈值用了 MODEL_CONTEXT_LIMIT×0.8，
         比实际工作上限 TOKEN_LIMIT 还高，导致条件永不成立、退化成"每 120 秒必压一次"。）
  对象：只压缩 messages[:-KEEP_RECENT]，最近 KEEP_RECENT 条**永远保留原文**。
        摘要会丢精确信息（文件路径/命令/变量名），而最近的消息最可能需要它们。
  代价：**单次 LLM 调用**产出结构化摘要。旧实现一次触发要 3 次调用
        （逐条打分 → 生成摘要 → 再给摘要评分），且第 3 次纯属多余。
  位置：在**后台线程**执行，不占用用户等待的同步路径（旧实现串在 chatbot 节点里，
        每 2 分钟额外拖慢 3 次 LLM 往返）。
"""
import threading
from typing import List

from langchain_core.messages import HumanMessage, SystemMessage

# 触发阈值：占实际工作预算的比例；再加一个绝对下限，避免短对话被误压
_COMPACT_RATIO = 0.6
_COMPACT_MIN_TOKENS = 20000

# 同一会话同时在压缩时跳过（防重入）
_LOCK = threading.Lock()
_INFLIGHT = set()

_COMPACT_SYS = (
    "你是对话记忆压缩器。把【较早的对话】压缩成一份结构化里程碑摘要，供后续轮次继续使用。\n"
    "严格输出以下五个 markdown 小节（标题保持英文，正文用中文）：\n"
    "## Objective\n"
    "- 本轮会话当前要达成的目标（一句话）\n"
    "## Important Details\n"
    "- 必须记住的关键约束、决策与事实（含用户明确表态的方向/取舍）\n"
    "## Work State\n"
    "### Completed\n"
    "- 已完成的事（带简要结果或证据）\n"
    "### Active\n"
    "- 正在做、尚未收尾的事\n"
    "### Blocked\n"
    "- 卡住的事与原因（若有则写，无则写 (none)）\n"
    "## Next Move\n"
    "- 下一步 1-2 条（编号列表）\n"
    "## Relevant Files\n"
    "- 提到过的文件路径，每行一个 `- 路径 — 说明`\n"
    "要求：\n"
    "- 逐字保留关键标识：文件路径、命令名、变量名、数字、报错信息，不要泛化\n"
    "- 丢弃寒暄、重复表述与过程性描述\n"
    "- 用中文，总计不超过 600 字\n"
    "- 只输出摘要本身，不要任何额外说明或前后缀\n"
)


def _budget_tokens() -> int:
    """实际工作预算（延迟导入，避免与 app.graph.utils 形成导入环）。"""
    try:
        from app.graph.utils import TOKEN_LIMIT
        return int(TOKEN_LIMIT)
    except Exception:
        return 0


def _keep_recent() -> int:
    try:
        from app.graph.utils import KEEP_RECENT
        return int(KEEP_RECENT)
    except Exception:
        return 30


def should_compact(messages) -> bool:
    """是否值得压缩：只看"实际发送预算的占比"，不看时间。"""
    if not messages:
        return False
    keep = _keep_recent()
    if len(messages) <= keep + 1:
        return False
    try:
        from app.graph.utils import count_tokens
        tokens = count_tokens(list(messages))
    except Exception:
        return False
    budget = _budget_tokens()
    threshold = max(_COMPACT_MIN_TOKENS, int(budget * _COMPACT_RATIO)) if budget else _COMPACT_MIN_TOKENS
    return tokens >= threshold


def _messages_to_text(messages) -> str:
    """把较早的消息拍成纯文本，供压缩 prompt 使用。"""
    lines: List[str] = []
    for m in messages:
        role = getattr(m, "type", None) or getattr(m, "role", "?")
        c = getattr(m, "content", "")
        if isinstance(c, list):
            c = " ".join(str(x.get("text", "")) if isinstance(x, dict) else str(x) for x in c)
        c = str(c or "").strip()
        if c:
            lines.append(f"[{role}] {c[:800]}")
    return "\n".join(lines)


def compact_in_background(thread_id: str, messages: List, llm, db_path: str, user: str = None) -> bool:
    """按需在**后台线程**压缩历史。返回是否已启动（False = 不需要/已在压缩）。

    只写 DB（history_summaries 表），**不改 LangGraph state**——
    下一轮 chatbot 通过 `mm.build_context()` / `build_memory_injection` 自然读到新摘要。
    """
    if not should_compact(messages):
        return False

    keep = _keep_recent()
    older = list(messages)[:-keep] if keep > 0 else list(messages)
    if not older:
        return False

    with _LOCK:
        if thread_id in _INFLIGHT:
            return False
        _INFLIGHT.add(thread_id)

    def _work():
        try:
            from app.memory.memory_manager import MemoryManager
            text = _messages_to_text(older)
            resp = llm.invoke([
                SystemMessage(content=_COMPACT_SYS),
                HumanMessage(content=text[:60000]),
            ])
            summary = (getattr(resp, "content", "") or "").strip()
            if summary:
                # 不再传 llm：避免内部再调一次 LLM 给摘要打分（旧实现的第 3 次调用）
                mm = MemoryManager(db_path=db_path, thread_id=thread_id)
                mm.save_history_summary_scored(thread_id, summary, importance_score=6.0)
        except Exception as e:
            try:
                from app.server import add_log_entry
                add_log_entry("warn", f"后台历史压缩失败: {type(e).__name__}: {str(e)[:160]}")
            except Exception:
                pass
        finally:
            with _LOCK:
                _INFLIGHT.discard(thread_id)

    try:
        from app.userctx import run_in_user_thread
        run_in_user_thread(user, _work)
    except Exception:
        threading.Thread(target=_work, name=f"compact-{thread_id[:8]}", daemon=True).start()
    return True
