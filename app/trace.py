"""app.trace — 对话全量追踪（TraceStore），按会话持久化到目录。

每个会话（thread_id）一个 JSONL 文件：data/traces/<thread_id>.jsonl。
记录图节点流转 / LLM 调用（含输入输出）/ 工具调用 / 错误。
"""
import os
import json
import time
import threading
from datetime import datetime
from typing import Dict, List, Any, Optional

_TRACE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "traces")
_lock = threading.Lock()


def _ensure_dir() -> None:
    os.makedirs(_TRACE_DIR, exist_ok=True)


def _safe_thread_id(thread_id: str) -> str:
    """thread_id 可能含路径特殊字符，做安全化处理。"""
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in str(thread_id))


def _file_path(thread_id: str) -> str:
    return os.path.join(_TRACE_DIR, _safe_thread_id(thread_id) + ".jsonl")


def _now() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def add(thread_id: str, event_type: str, data: Dict[str, Any]) -> None:
    """追加一条 trace 事件到会话文件。thread_id 为空时不记录。"""
    if not thread_id:
        return
    entry = {
        "type": event_type,
        "ts": _now(),
        "ts_ms": time.time() * 1000,
        "data": data,
    }
    try:
        _ensure_dir()
        with _lock, open(_file_path(thread_id), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


def get(thread_id: str, limit: int = 300) -> List[Dict[str, Any]]:
    """读取会话 trace（按写入顺序）。"""
    path = _file_path(thread_id)
    if not os.path.exists(path):
        return []
    events = []
    try:
        with _lock, open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                    evt["seq"] = len(events) + 1
                    events.append(evt)
                except Exception:
                    continue
    except Exception:
        return []
    return events[-limit:]


def clear(thread_id: Optional[str] = None) -> int:
    """清空指定会话或全部会话的 trace。返回清理的文件数。"""
    _ensure_dir()
    n = 0
    with _lock:
        if thread_id:
            path = _file_path(thread_id)
            if os.path.exists(path):
                try:
                    os.remove(path)
                    n = 1
                except OSError:
                    pass
        else:
            for f in os.listdir(_TRACE_DIR):
                if f.endswith(".jsonl"):
                    try:
                        os.remove(os.path.join(_TRACE_DIR, f))
                        n += 1
                    except OSError:
                        pass
    return n


def truncate_text(text: Any, limit: int = 600) -> str:
    """截断任意内容为可读文本（避免 trace 过大）。"""
    if text is None:
        return ""
    if not isinstance(text, str):
        try:
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
