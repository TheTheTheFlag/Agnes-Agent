"""app.graph.utils — 工具函数（token 计数/压缩、工具调用解析、提示词加载）。"""
import os
import re
import time
from typing import List, Dict, Any
from langchain_core.messages import AIMessage, SystemMessage
from app.config import PROMPT_TEMPLATE_PATH, DB_PATH
from app.graph.state import State
from app.memory import MemoryManager

# tiktoken 全局：与 builder.py 原先一致，首次用 gpt-4 编码，失败 fallback cl100k_base
try:
    import tiktoken_ext.openai_public as _tiktoken_ext  # noqa: F401 确保扩展注册
    try:
        import tiktoken
        _orig_read = tiktoken.load.read_file
        def _fast_read(blobpath):
            import requests as _req
            resp = _req.get(blobpath, timeout=3)
            resp.raise_for_status()
            return resp.content
        tiktoken.load.read_file = _fast_read  # 3s 超时下载
        tokenizer = tiktoken.encoding_for_model("gpt-4")
    except Exception:
        tiktoken.load.read_file = _orig_read
        tokenizer = tiktoken.get_encoding("cl100k_base")
except Exception:
    try:
        import tiktoken
        tokenizer = tiktoken.get_encoding("cl100k_base")
    except Exception:
        tokenizer = None

# token 预算常量
MODEL_CONTEXT_LIMIT = 524288
TOKEN_LIMIT_RATIO = 0.7
TOKEN_LIMIT = int(MODEL_CONTEXT_LIMIT * TOKEN_LIMIT_RATIO)
KEEP_RECENT = 30
MAX_TOOL_CALL_ROUNDS = 15

def count_tokens(messages: List) -> int:
    if tokenizer is None:
        return 0
    text = ""
    for msg in messages:
        if hasattr(msg, 'content') and msg.content:
            text += str(msg.content)
        if hasattr(msg, 'type'):
            text += str(msg.type)
    return len(tokenizer.encode(text))


# ==================== 消息结构安全（业界做法：回合原子性 / 清洗 / 退化尾巴 / 熔断） ====================
# OpenAI 兼容网关要求 assistant(tool_calls) 与其 ToolMessage 成对、序列不以 tool 消息结尾；
# 历史组装若从中间硬切，会产生孤儿 tool 消息 → 网关 400 或静默空返回 → 表现为"复读兜底文案"。
# 因此：截断只允许按"完整回合"进行，发送前一律清洗，异常兜底文案只落库一次。

_DEGENERATE_MARKERS = (
    "[模型未返回内容]",          # react_loop 空回复兜底
    "请检查模型配置或 API 额度",   # 兜底文案
    "LLM 调用失败",              # LLM 调用异常兜底
    "执行错误",                  # chatbot 异常兜底
    "无法获取有效信息",           # 连续相同工具调用熔断兜底
)
STUCK_REPLY_HINT = "⚠️ 模型连续多轮未正常回复（可能是该会话上下文异常）。为避免刷屏，本轮不再追加记录，建议点击左上角「新建会话」重试。"


def _is_ai_message(msg) -> bool:
    return isinstance(msg, AIMessage) or type(msg).__name__ in ("AIMessage", "AIMessageChunk")


def _is_tool_message(msg) -> bool:
    return type(msg).__name__ in ("ToolMessage", "ToolMessageChunk") or bool(getattr(msg, "tool_call_id", None))


def _message_text(msg) -> str:
    raw = getattr(msg, "content", "") or ""
    if isinstance(raw, list):
        parts = []
        for x in raw:
            if isinstance(x, dict):
                parts.append(str(x.get("text", "") or ""))
            else:
                parts.append(str(x))
        return "".join(parts)
    return str(raw)


def is_degenerate_text(text: str) -> bool:
    """判定一段 assistant 文本是否为"模型异常"类兜底/错误文案。"""
    t = str(text or "").strip()
    return any(marker in t for marker in _DEGENERATE_MARKERS)


def split_turn_blocks(messages):
    """把消息流切成以 user/assistant 开头、含其后连续 tool 消息的回合块。

    跨轮组装/截断只允许整块保留或丢弃，绝不从中切断，保证
    assistant(tool_calls) 与其 ToolMessage 的配对不被拆散。
    游离的 tool 消息独立成块，交由 sanitize_messages 丢弃。
    """
    blocks = []
    cur = []
    for m in messages:
        if _is_tool_message(m):
            cur.append(m)
            continue
        if cur:
            blocks.append(cur)
        cur = [m]
    if cur:
        blocks.append(cur)
    return blocks


def sanitize_messages(messages):
    """（幂等）发送给 LLM 前的结构清洗：
      1. 丢弃孤儿 ToolMessage（前方无对应 assistant tool_calls）；
      2. 丢弃"带 tool_calls 却无 ToolMessage 收尾"的半截回合（截断/压缩产物）；
      3. 序列不以 tool 消息结尾（OpenAI 兼容 API 禁止）。
    结构正常的历史消息原样保留。"""
    out = []
    for blk in split_turn_blocks(messages):
        head = blk[0]
        if _is_tool_message(head):
            continue  # 孤儿 tool 消息块 → 丢弃
        if _is_ai_message(head) and getattr(head, "tool_calls", None):
            tools = [m for m in blk[1:] if _is_tool_message(m)]
            if not tools and not _message_text(head).strip():
                continue  # 半截 tool 回合（无 ToolMessage 也无正文）→ 整块丢弃
            out.append(head)
            out.extend(tools)
            continue
        out.extend(blk)
    # 结尾收尾：不得以 tool 消息 / 半截 tool_calls assistant 结束
    while out and (_is_tool_message(out[-1])
                   or (_is_ai_message(out[-1]) and getattr(out[-1], "tool_calls", None)
                       and not _message_text(out[-1]).strip())):
        out.pop()
    return out


def trim_history_by_turns(messages, max_tokens, keep_recent=KEEP_RECENT):
    """回合安全的上下文裁剪：从头部整块丢弃，直到 块数 <= keep_recent 且 token <= max_tokens。
    与旧实现（硬切最近 N 条 / 逐条删头）不同：不拆散 assistant↔ToolMessage 配对。"""
    blocks = split_turn_blocks(messages)
    if len(blocks) > keep_recent:
        blocks = blocks[-keep_recent:]
    while len(blocks) > 1:
        if count_tokens([m for b in blocks for m in b]) <= max_tokens:
            break
        blocks = blocks[1:]
    return sanitize_messages([m for b in blocks for m in b])


def strip_degenerate_tail(messages):
    """清理历史尾部的"退化尾巴"（异常兜底文案反复刷屏的那一段）：
    从结尾往前弹出退化/重复的 assistant 文本与残留 tool 残块，
    让已卡死的会话在下一轮请求时拿到干净上下文、实现自愈。
    判定保守：仅当文本命中兜底标记、或同一文本在全历史重复出现 >=3 次才移除，
    避免误删正常回答。"""
    if not messages:
        return messages
    counts = {}
    for m in messages:
        if _is_ai_message(m) and not getattr(m, "tool_calls", None):
            t = _message_text(m).strip()
            if t:
                counts[t] = counts.get(t, 0) + 1
    out = list(messages)
    while out:
        m = out[-1]
        if _is_tool_message(m):
            out.pop()
            continue
        if _is_ai_message(m):
            if getattr(m, "tool_calls", None):
                break  # 工具调用回合 = 正常历史边界
            t = _message_text(m).strip()
            if is_degenerate_text(t) or (t and counts.get(t, 0) >= 3):
                if t:
                    counts[t] = max(counts.get(t, 0) - 1, 0)
                out.pop()
                continue
        break
    return out


def apply_reply_guard(history, content):
    """连续异常回复熔断（业界：错误不进上下文）。规则：
      - 正常回答永不抑制；
      - 首次异常文案照常落库（用户需要知道模型没答上来）；
      - 上一条 assistant 已是异常文案 → 本条替换为一次性提示 STUCK_REPLY_HINT；
      - 上一条已是该提示 → 返回 (None, True)：调用方跳过写入，历史停止膨胀。
    返回 (final_content, skip)。skip=True 时不应把回复写入历史/记忆。"""
    txt = str(content or "").strip()
    if not is_degenerate_text(txt):
        return content, False
    prev = None
    for m in reversed(history):
        if _is_ai_message(m) and not getattr(m, "tool_calls", None):
            prev = _message_text(m).strip()
            break
    if prev and is_degenerate_text(prev):
        return (None, True) if prev == STUCK_REPLY_HINT else (STUCK_REPLY_HINT, False)
    return content, False


def prepare_context_messages(messages, system_text, keep_recent=KEEP_RECENT):
    """组装发给 LLM 的上下文（业界组合拳）：
    尾部退化清理 → 结构清洗 → 按完整回合裁剪到 token 预算 → 前置 system。"""
    cleaned = sanitize_messages(strip_degenerate_tail(messages))
    system_tokens = count_tokens([SystemMessage(content=system_text)])
    max_other = max(TOKEN_LIMIT - system_tokens, 1)
    history = trim_history_by_turns(cleaned, max_other, keep_recent=keep_recent)
    return [SystemMessage(content=system_text)] + history


def retry_llm_call(func, *args, **kwargs):
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception(lambda e: not isinstance(e, RateLimitError)),
        before_sleep=lambda rs: print(f"[重试] 第 {rs.attempt_number} 次...")
    )
    def _wrapper(): return func(*args, **kwargs)
    return _wrapper()


def parse_tool_calls_from_content(content: str) -> List[Dict]:
    tool_calls = []
    if "<invoke" in content or "<tool_call" in content:
        invoke_pattern = r'<invoke\s+name=["\']?([^"\'<>]+)["\']?\s*>(.*?)</invoke>'
        for name, params_xml in re.findall(invoke_pattern, content, re.DOTALL | re.IGNORECASE):
            param_pattern = r'<parameter\s+name=["\']?([^"\'<>]+)["\']?\s*>(.*?)</parameter>'
            params = {pname.strip(): pvalue.strip() for pname, pvalue in re.findall(param_pattern, params_xml, re.DOTALL | re.IGNORECASE)}
            tool_calls.append({"name": name.strip(), "args": params, "id": f"manual_{int(time.time())}_{len(tool_calls)}"})
        if tool_calls:
            return tool_calls
        short_pattern = r'<tool_call>.*?<function=([^>]+)>.*?<parameter=([^>]+)>(.*?)</parameter>.*?</tool_call>'
        for name, pname, pvalue in re.findall(short_pattern, content, re.DOTALL | re.IGNORECASE):
            tool_calls.append({"name": name.strip(), "args": {pname.strip(): pvalue.strip()}, "id": f"manual_{int(time.time())}_{len(tool_calls)}"})
    return tool_calls


def ensure_tool_calls(response: AIMessage) -> AIMessage:
    if hasattr(response, 'tool_calls') and response.tool_calls:
        return response
    if "<tool_calls>" in response.content or "<tool_call>" in response.content or "<invoke" in response.content:
        parsed = parse_tool_calls_from_content(response.content)
        if parsed:
            response.content = re.sub(r'<tool_calls>.*?</tool_calls>', '', response.content, flags=re.DOTALL | re.IGNORECASE).strip()
            response.content = re.sub(r'<tool_call>.*?</tool_call>', '', response.content, flags=re.DOTALL | re.IGNORECASE).strip()
            response.tool_calls = parsed
            response.additional_kwargs["tool_calls"] = parsed
        else:
            response.tool_calls = []
    else:
        response.tool_calls = []
    return response


def compress_messages(messages: List, llm_instance, max_tokens: int, thread_id: str = None, depth: int = 0) -> List:
    if depth > 3:
        if len(messages) > KEEP_RECENT:
            truncated = messages[-KEEP_RECENT:]
            while count_tokens(truncated) > max_tokens and len(truncated) > 2:
                truncated = truncated[1:]
            return [SystemMessage(content="[已截断早期对话]")] + truncated
        return messages
    current_tokens = count_tokens(messages)
    if current_tokens <= max_tokens:
        return messages
    mm = MemoryManager(db_path=DB_PATH)
    classified = mm.importance_filter(messages, llm_instance)
    compressed = []
    if classified['summarize']:
        summary_text = mm.generate_summary_from_messages(classified['summarize'], llm_instance)
        if len(summary_text) > 1000:
            summary_text = summary_text[:1000] + "..."
        if thread_id:
            mm.save_summary(thread_id, summary_text)
        compressed.append(SystemMessage(content=f"[对话摘要]\n{summary_text}"))
    for msg in classified['keep']:
        if hasattr(msg, 'content') and isinstance(msg.content, str) and len(msg.content) > 2000:
            msg.content = msg.content[:2000] + "..."
        compressed.append(msg)
    new_tokens = count_tokens(compressed)
    if new_tokens > max_tokens:
        if len(compressed) > 30:
            compressed = compressed[-30:]
        while count_tokens(compressed) > max_tokens:
            longest = max(compressed, key=lambda m: len(getattr(m, 'content', '')))
            if hasattr(longest, 'content') and isinstance(longest.content, str):
                longest.content = longest.content[:int(len(longest.content)*0.8)] + "..."
            else:
                break
    return compressed


def ensure_token_limit(messages: List, system_text: str, thread_id: str = None) -> List:
    system_msg = SystemMessage(content=system_text)
    system_tokens = count_tokens([system_msg])
    max_other_tokens = TOKEN_LIMIT - system_tokens
    other_messages = [msg for msg in messages if not (isinstance(msg, SystemMessage) and msg.content == system_text)]
    if count_tokens(other_messages) <= max_other_tokens:
        return messages
    return [system_msg] + compress_messages(other_messages, llm, max_other_tokens, thread_id)


def sync_state_to_db(state: State, mm: MemoryManager):
    if state.get("profile"):
        for key, value in state["profile"].items():
            if value is not None:
                mm.set_profile(key, value)
        state["_new_profile"] = None
    if state.get("preferences"):
        for key, value in state["preferences"].items():
            if value is not None:
                mm.set_preference(key, value)
        state["_new_preference"] = None


def load_prompt_template() -> str:
    if not os.path.exists(PROMPT_TEMPLATE_PATH):
        raise FileNotFoundError(f"提示词模板不存在: {PROMPT_TEMPLATE_PATH}")
    with open(PROMPT_TEMPLATE_PATH, 'r', encoding='utf-8') as f:
        return f.read()




def _tool_params_summary(name: str, params: dict) -> str:
    """提取工具参数的关键摘要（供命令历史/审计记录）。"""
    if not isinstance(params, dict):
        return str(params)[:100]
    for key in ("command", "path", "query", "pattern", "keyword", "directory", "file_path"):
        if key in params:
            v = str(params[key])
            return v if len(v) <= 120 else v[:120] + "..."
    try:
        return json.dumps(params, ensure_ascii=False)[:120]
    except Exception:
        return str(params)[:120]
