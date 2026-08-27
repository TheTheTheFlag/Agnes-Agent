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
