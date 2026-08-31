"""app.trace — 对话全量追踪（TraceStore）。

记录当前对话的所有输入输出：图节点流转 / LLM 调用（含输入输出）/ 工具调用 / 错误。
数据存内存（按 thread_id 分桶，滚动保留最近 500 条），供 /api/trace 查询。
"""
import time
import threading
from datetime import datetime
from typing import Dict, List, Any, Optional

_TRACE_LIMIT = 500
_lock = threading.Lock()
_traces: Dict[str, List[Dict[str, Any]]] = {}  # thread_id -> [event, ...]


def _now() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def add(thread_id: str, event_type: str, data: Dict[str, Any]) -> None:
    """追加一条 trace 事件。thread_id 为空时不记录。"""
    if not thread_id:
        return
    entry = {
        "seq": None,  # 由 _traces 长度决定
        "type": event_type,
        "ts": _now(),
        "ts_ms": time.time() * 1000,
        "data": data,
    }
    with _lock:
        bucket = _traces.setdefault(thread_id, [])
        entry["seq"] = len(bucket) + 1
        bucket.append(entry)
        if len(bucket) > _TRACE_LIMIT:
            del bucket[: len(bucket) - _TRACE_LIMIT]


def get(thread_id: str, limit: int = 300) -> List[Dict[str, Any]]:
    with _lock:
        bucket = list(_traces.get(thread_id, []))
    return bucket[-limit:]


def clear(thread_id: Optional[str] = None) -> int:
    with _lock:
        if thread_id:
            n = len(_traces.pop(thread_id, []))
        else:
            n = sum(len(v) for v in _traces.values())
            _traces.clear()
    return n


def truncate_text(text: Any, limit: int = 600) -> str:
    """截断任意内容为可读文本（避免 trace 过大）。"""
    if text is None:
        return ""
    if not isinstance(text, str):
        try:
            import json
            text = json.dumps(text, ensure_ascii=False, default=str)
        except Exception:
            text = str(text)
    return text if len(text) <= limit else text[:limit] + f"…(+{len(text) - limit})"


# ---- 便捷记录函数 ----

def record_node_start(thread_id: str, node: str) -> None:
    add(thread_id, "node_start", {"node": node})


def record_node_end(thread_id: str, node: str, result_summary: str = "") -> None:
    add(thread_id, "node_end", {"node": node, "result": truncate_text(result_summary, 300)})


def record_llm(thread_id: str, node: str, messages: Any, response: Any,
               duration_ms: float = 0.0, model: str = "") -> None:
    """记录一次 LLM 调用：输入消息摘要 + 输出。messages 为消息列表或原始文本。"""
    try:
        msgs = list(messages) if isinstance(messages, (list, tuple)) else [messages]
        input_snippet = []
        for m in msgs[-4:]:  # 最近 4 条
            role = getattr(m, "type", "?")
            content = getattr(m, "content", "")
            if isinstance(content, list):
                content = "".join(str(x.get("text", "") if isinstance(x, dict) else x) for x in content)
            input_snippet.append(f"[{role}] {truncate_text(str(content), 300)}")
        output = getattr(response, "content", response)
        if isinstance(output, list):
            output = "".join(str(x.get("text", "") if isinstance(x, dict) else x) for x in output)
        add(thread_id, "llm_call", {
            "node": node,
            "model": model or "",
            "duration_ms": round(duration_ms, 1),
            "input": "\n".join(input_snippet),
            "output": truncate_text(str(output), 600),
        })
    except Exception:
        pass


def record_tool(thread_id: str, node: str, name: str, params: Dict[str, Any],
                result: Any = None, duration_ms: float = 0.0) -> None:
    add(thread_id, "tool_call", {
        "node": node,
        "name": name,
        "params": truncate_text(params, 400),
        "result": truncate_text(result, 300),
        "duration_ms": round(duration_ms, 1),
    })


def record_error(thread_id: str, node: str, error: Any) -> None:
    add(thread_id, "error", {"node": node, "message": truncate_text(str(error), 500)})
