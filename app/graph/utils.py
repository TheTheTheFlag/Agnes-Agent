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
    """把消息流按"用户回合"分块：每条 user 消息与其后（直到下一条 user 前）的
    assistant / tool / system 消息同属一个回合块。上下文裁剪只允许整块保留或丢弃，
    保证 assistant(tool_calls) 与其 ToolMessage 的配对不被拆散。
    游离在回合外的孤儿消息（如孤立 tool / system）独立成块，由 sanitize_messages 处理。"""
    blocks = []
    cur = []
    for m in messages:
        if type(m).__name__ == "HumanMessage":
            if cur:
                blocks.append(cur)
            cur = [m]
        elif cur:
            cur.append(m)
        else:
            blocks.append([m])
    if cur:
        blocks.append(cur)
    return blocks


def sanitize_messages(messages):
    """（幂等）发送给 LLM 前的结构清洗：
      1. 丢弃孤儿 ToolMessage（前方无对应 assistant tool_calls）；
      2. 丢弃"带 tool_calls 却无 ToolMessage 收尾"的半截 assistant 残块（截断/压缩产物）；
      3. 结尾清理只针对"真正孤儿"：末尾"从未被回填 tool_calls"的 assistant 残块删除；
         已配好对的末尾 ToolMessage 必须保留——那是 ReActLoop 工具执行后、下一轮
         LLM 调用前的合法中间态。删掉它会留下孤儿 assistant(tool_calls)（尤其当
         assistant 带正文时只删 ToolMessage 不删 assistant）→ 网关 400。
    结构正常的历史消息原样保留。"""
    out = []
    pending = set()  # 期待被 ToolMessage 回填的 assistant tool_call id
    matched_tool_ids = set()  # 已被 ToolMessage 成功回填的 tool_call id
    for m in messages:
        if _is_tool_message(m):
            tid = getattr(m, "tool_call_id", None)
            if tid is not None and str(tid) in pending:
                out.append(m)
                pending.discard(str(tid))
                matched_tool_ids.add(str(tid))
            # 无主/重复 tool 消息 → 丢弃
            continue
        if pending:
            # 上一个 assistant(tool_calls) 没等到 ToolMessage 就出现新消息 → 回删其残块
            while out and _is_ai_message(out[-1]) and getattr(out[-1], "tool_calls", None) \
                    and not _message_text(out[-1]).strip():
                out.pop()
            pending = set()
        out.append(m)
        if _is_ai_message(m):
            tcs = getattr(m, "tool_calls", None) or []
            pending = set()
            for tc in tcs:
                i = tc.get("id") or tc.get("tool_call_id") if isinstance(tc, dict) else getattr(tc, "id", None)
                if i is not None:
                    pending.add(str(i))
        else:
            pending = set()
    # 结尾收尾：仅清理"真正孤儿"结尾。
    # out 里的 ToolMessage 都在主循环中配过对（无主的已被丢弃），因此末尾若为 tool 消息
    # 就是合法中间态，必须保留——不能像旧逻辑那样一律删掉（会把配好对的 ToolMessage 删除，
    # 使前面的 assistant(tool_calls) 变孤儿 → 网关 400）。
    if out and _is_ai_message(out[-1]) and getattr(out[-1], "tool_calls", None):
        ids = set()
        for tc in out[-1].tool_calls:
            i = tc.get("id") or tc.get("tool_call_id") if isinstance(tc, dict) else getattr(tc, "id", None)
            if i is not None:
                ids.add(str(i))
        # 末尾 assistant 带 tool_calls 但从未被回填（截断/压缩残块）→ 整体删除；
        # 已全部回填（理论上不会以 tool_calls 结尾，防御）→ 保留
        if ids and not ids.issubset(matched_tool_ids):
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


def strip_degenerate_replies(messages):
    """清理历史中"模型异常"类回复（业界：错误/兜底文案不是有效对话内容，不进上下文）。

    两段式清理：
      1. 全局移除命中断言标记的退化 assistant 文本（空回复兜底 / LLM 调用失败等）——
         这类文案不属于对话内容，留在历史里只会继续污染上下文；
      2. 保守清理：仅当同一正常文本在全历史重复 >=3 次（刷屏特征）时，从尾部移除重复，
         避免误删恰好重复的正常回答。
    只删纯文本 assistant 消息，不动 user / tool 消息与 assistant tool_calls，
    不会破坏消息配对与顺序。"""
    if not messages:
        return messages
    # 1) 全局移除退化兜底/错误文案
    filtered = []
    for m in messages:
        if _is_ai_message(m) and not getattr(m, "tool_calls", None):
            t = _message_text(m).strip()
            if t and is_degenerate_text(t):
                continue
        filtered.append(m)
    # 2) 保守：正常文本重复 >=3 时清理尾部（防"无意义重复回答"刷屏）
    counts = {}
    for m in filtered:
        if _is_ai_message(m) and not getattr(m, "tool_calls", None):
            t = _message_text(m).strip()
            if t:
                counts[t] = counts.get(t, 0) + 1
    out = list(filtered)
    while out:
        m = out[-1]
        if _is_tool_message(m):
            out.pop()
            continue
        if _is_ai_message(m) and not getattr(m, "tool_calls", None):
            t = _message_text(m).strip()
            if t and counts.get(t, 0) >= 3:
                counts[t] = max(counts.get(t, 0) - 1, 0)
                out.pop()
                continue
        break
    return out


def apply_reply_guard(history, content):
    """连续异常/错误回复的可见性治理（业界：错误必须对用户可见，但不能无限刷屏）。
    规则：
      - 正常回复、以及"新的（与上一条不同）"错误/兜底文案 → 原样返回，让用户看到真实原因；
      - 与上一条 assistant 逐字相同 且 命中兜底/错误标记（同一句反复出现）→ 替换为一次性
        STUCK_REPLY_HINT（提示连续异常）；
      - 上一条已是 STUCK_REPLY_HINT 且本条仍为异常文案 → 返回 (None, True)，跳过写入，历史停止膨胀。
    返回 (final_content, skip)。skip=True 时调用方不应把回复写入历史/记忆。"""
    txt = str(content or "").strip()
    if not txt:
        return content, False
    prev = None
    for m in reversed(history):
        if _is_ai_message(m) and not getattr(m, "tool_calls", None):
            prev = _message_text(m).strip()
            break
    if prev == STUCK_REPLY_HINT and is_degenerate_text(txt):
        # 已提示过熔断：后续异常文案（兜底/报错）不再重复写入
        return None, True
    if prev and prev == txt and is_degenerate_text(txt):
        # 与上一条完全相同的异常文案 → 用一次性提示替换，避免刷屏（错误信息本身不吞）
        return STUCK_REPLY_HINT, False
    return content, False


def prepare_context_messages(messages, system_text, keep_recent=KEEP_RECENT):
    """组装发给 LLM 的上下文（业界组合拳）：
    退化文案清理 → 结构清洗 → 按完整回合裁剪到 token 预算 → 前置 system。"""
    cleaned = sanitize_messages(strip_degenerate_replies(messages))
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
    """把待发消息收敛到 TOKEN_LIMIT 预算内。

    旧实现超预算时调用 compress_messages 需要模块级 llm 实例——utils 里并未定义，
    一旦超长会话触发该分支就会 NameError（表现为 "LLM 调用失败: name 'llm' is not defined"）。
    现改为：预算不足时先结构清洗、再按完整回合从最早历史裁剪（摘要压缩不再参与每轮
    同步路径；如需摘要由 MemoryManager.update_summary 单独负责）。
    """
    system_msg = SystemMessage(content=system_text)
    system_tokens = count_tokens([system_msg])
    max_other_tokens = max(TOKEN_LIMIT - system_tokens, 1)
    other_messages = [msg for msg in messages if not (isinstance(msg, SystemMessage) and msg.content == system_text)]
    if count_tokens(other_messages) <= max_other_tokens:
        return sanitize_messages(messages)
    return sanitize_messages([system_msg] + trim_history_by_turns(other_messages, max_other_tokens, keep_recent=KEEP_RECENT))


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
