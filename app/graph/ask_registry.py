"""app.graph.ask_registry — 进程内人工问答（ask）注册表。

为什么不用 langgraph 的 interrupt()：executor 节点跑在 ThreadPoolExecutor
worker 里（见 dag_executor._run_node / executor_node 的 pool.submit），
而 langgraph 的 interrupt() 依赖主执行线程通过 contextvar 注入的 runnable
config，在 worker 线程里调用会抛
`RuntimeError: Called get_config outside of a runnable context`
（已实测）。所以 ask_user 走本模块：进程内字典 + threading.Event 阻塞等待，
不依赖 langgraph 挂起。

生命周期：
- dag_executor 的 _interrupt_handler 检测到 ask_user → register_ask() 拿到 ask_id，
  add_event("approval", {kind:"ask", ...}) 推 SSE 给前端弹选项卡，
  然后阻塞在 wait_answer(ask_id) 上。
- 前端 respondAsk() 回 POST /api/chat {resume:true, mode:"ask", answer}，
  chat.py 调 submit_answer(ask_id, answer) 唤醒 worker，worker 返回继续执行。
- 超时（_ASK_TIMEOUT）或进程重启 → worker 视为「未答复」，按默认继续，
  不会永久挂死。
"""
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

# ask_id -> {"question","options","allow_custom","event","answer","created_at"}
_pending: Dict[str, Dict[str, Any]] = {}
_lock = threading.RLock()

# 兜底超时：超过则视为用户未答复，唤醒 worker 继续（避免永久挂起）
ASK_TIMEOUT = 30 * 60  # 30 分钟
# 同一 thread 同时只允许一个待答问题（与前端单卡片一致）
_thread_ask: Dict[str, str] = {}  # thread_id -> ask_id


def register_ask(thread_id: str, question: str, options: Optional[List[str]] = None,
                 allow_custom: bool = True, timeout: int = ASK_TIMEOUT) -> str:
    """登记一个待答问题，返回 ask_id（同时把同一 thread 的旧问题作废）。"""
    ask_id = uuid.uuid4().hex[:12]
    opts = [str(o) for o in (options or []) if str(o or "").strip()]
    with _lock:
        # 同 thread 已有未答问题 → 作废旧的（前端只会弹一张卡）
        old = _thread_ask.get(thread_id)
        if old and old in _pending:
            _pending[old]["event"].set()
            _pending.pop(old, None)
        _pending[ask_id] = {
            "question": str(question or "请用户决定"),
            "options": opts,
            "allow_custom": bool(allow_custom),
            "event": threading.Event(),
            "answer": None,
            "created_at": time.time(),
            "timeout": timeout,
            "thread_id": thread_id,
        }
        _thread_ask[thread_id] = ask_id
    return ask_id


def get_pending(thread_id: str) -> Optional[Dict[str, Any]]:
    """取该 thread 当前待答问题的可序列化快照（无则 None）。"""
    with _lock:
        ask_id = _thread_ask.get(thread_id)
        if not ask_id:
            return None
        rec = _pending.get(ask_id)
        if not rec or rec["answer"] is not None:
            return None
        return {
            "ask_id": ask_id,
            "question": rec["question"],
            "options": list(rec["options"]),
            "allow_custom": rec["allow_custom"],
        }


def submit_answer(thread_id: str, answer: str, ask_id: str = "") -> bool:
    """提交答案并唤醒等待中的 worker。返回是否命中一个待答问题。

    注意这里**不**把记录从 _pending 里删掉——答案要留给 wait_answer 去读，
    清理（pop）统一由 wait_answer 负责，否则 worker 醒来拿到的是空串。
    """
    with _lock:
        target = ask_id or _thread_ask.get(thread_id)
        if not target:
            return False
        rec = _pending.get(target)
        if not rec:
            return False
        rec["answer"] = str(answer or "")[:4000]
        rec["event"].set()
        if _thread_ask.get(thread_id) == target:
            _thread_ask.pop(thread_id, None)
        return True


def wait_answer(ask_id: str, timeout: Optional[int] = None) -> str:
    """阻塞等待答复，返回答案字符串；超时/未答复返回空串（调用方按「未答复」处理）。"""
    with _lock:
        rec = _pending.get(ask_id)
        if not rec:
            return ""
        ev = rec["event"]
        tmo = timeout if timeout is not None else rec.get("timeout", ASK_TIMEOUT)
    got = ev.wait(timeout=tmo)
    with _lock:
        rec = _pending.pop(ask_id, None)
        if rec is not None:
            for tid, aid in list(_thread_ask.items()):
                if aid == ask_id:
                    _thread_ask.pop(tid, None)
    if not got or not rec:
        return ""
    return rec.get("answer") or ""