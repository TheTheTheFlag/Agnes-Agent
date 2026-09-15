"""app.server.console_log — 服务控制台日志打印。

把 store 的 add_log_entry / add_event 广播实时格式化成易读的彩色一行日志，
替代旧的"终端对话循环"。事件类型全覆盖：
  - 节点：node_start / node_end
  - 模型调用：llm_call
  - 工具调用：tool_call / tool_call_rejected
  - 规划与执行：planner / executor / replan / summarizer / node_thought
  - 常规 log：info / success / warning / error

设计：不串行化、不落盘（落盘走 store 的 DB），只做终端展示；
install_console_logger() 被 main / service 各调一次，幂等（同一 Queue 只注册一次），
后台 daemon 线程消费，绝不阻塞写日志的一方。
"""
import os
import sys
import threading
from datetime import datetime
from queue import Queue
from typing import Any, Dict, Optional

from app.server import store as _store

_lock = threading.Lock()
_installed = False
_colors = sys.stdout.isatty() if hasattr(sys.stdout, "isatty") else False

# ANSI 颜色（仅 TTY 终端开启）
_C = {
    "reset": "\033[0m",
    "lvl_info": "\033[36m",      # cyan
    "lvl_success": "\033[32m",   # green
    "lvl_warning": "\033[33m",   # yellow
    "lvl_error": "\033[31m",     # red
    "node": "\033[35m",          # magenta
    "llm": "\033[34m",           # blue
    "tool": "\033[36m",          # cyan
    "plan": "\033[33m",          # yellow
    "dim": "\033[2m",
}


def _col(key: str, text: str) -> str:
    if not _colors:
        return text
    return f"{_C[key]}{text}{_C['reset']}"


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _trunc(text: Any, n: int = 160) -> str:
    s = str(text)
    s = " ".join(s.split()) if s else s
    if len(s) <= n:
        return s
    return s[:n] + "…"


def _format_part(text: Any, n: int = 160) -> str:
    return _col("dim", _trunc(text, n))


def _on_entry(entry: Dict[str, Any]) -> Optional[str]:
    """把一条广播 entry 格式化为控制台一行；不需要展示的返回 None。"""
    try:
        etype = entry.get("type", "")
        ts = _col("dim", "[" + _now() + "]")
        level = entry.get("level", "")

        if etype == "log":
            msg = entry.get("message", "")
            # “工具: xxx” 与 favicon/心跳等噪音已被 add_log_entry 之外的工具事件覆盖，
            # log 层的工具行只显示名称，详细参数由 tool_call 事件负责。
            if str(msg).startswith("工具: "):
                return None
            if level == "error" or level == "critical":
                return f"{ts} {_col('lvl_error', 'ERROR')} {_col('lvl_error', _trunc(msg, 300))}"
            if level == "warning" or level == "warn":
                return f"{ts} {_col('lvl_warning', 'WARN ')} {_col('lvl_warning', _trunc(msg, 300))}"
            if level == "success":
                return f"{ts} {_col('lvl_success', 'OK   ')} {_trunc(msg, 300)}"
            return f"{ts} {_col('lvl_info', 'INFO ')} {_trunc(msg, 300)}"

        data = entry.get("data") or {}
        if etype == "node_start":
            node = data.get("node", "")
            return f"{ts} {_col('node', '▶ 节点开始')} {_trunc(node, 60)}"
        if etype == "node_end":
            node = data.get("node", "")
            res = data.get("result", "")
            return f"{ts} {_col('node', '■ 节点结束')} {_trunc(node, 60)}{_format_part(' · ' + res) if res else ''}"
        if etype == "llm_call":
            node = data.get("node") or data.get("node_type") or "llm"
            dur = data.get("duration_ms")
            dur_s = f"{int(dur) / 1000:.1f}s" if dur is not None else "?"
            model = data.get("model") or ""
            model_s = f" ({model})" if model else ""
            out = _trunc(data.get("output") or data.get("content") or "", 120)
            return f"{ts} {_col('llm', 'LLM   ')} {_trunc(node, 40)}{_col('dim', f'  {dur_s}{model_s}')} {_format_part(out)}"
        if etype == "tool_call":
            name = data.get("name", "")
            node = data.get("node", "")
            params = _trunc(data.get("params") or data.get("args") or "", 100)
            node_s = f"[{node}] " if node else ""
            return f"{ts} {_col('tool', 'TOOL  ')} {_trunc(node_s + name, 50)} {_col('dim', params)}"
        if etype == "tool_call_rejected":
            name = data.get("name", "") or entry.get("message", "")
            reason = data.get("reason", "")
            return f"{ts} {_col('lvl_warning', '拒绝  ')} 工具审批被拒 {_trunc(name, 60)}{_format_part(' · ' + reason) if reason else ''}"
        if etype == "planner":
            goal = data.get("goal", "")
            dag = data.get("dag") or {}
            nc = len(dag.get("nodes") or [])
            ec = len(dag.get("edges") or [])
            return f"{ts} {_col('plan', 'PLAN  ')} 目标: {_trunc(goal, 80)}  ·  {nc} 节点 / {ec} 边"
        if etype == "node_thought":
            role = data.get("role", "assistant")
            title = data.get("title", "")
            text = data.get("text", "")
            return f"{ts} {_col('plan', 'THOUGHT')} [{_trunc(role, 12)}] {_trunc(title or text, 120)}"
        if etype == "executor":
            subtask = data.get("subtask", "")
            status = data.get("status", "")
            goal = data.get("goal", "")
            replan = str(subtask).startswith("replan")
            if replan:
                return f"{ts} {_col('plan', 'REPLAN')} 局部重规划 · {_trunc(goal, 80)}"
            if subtask and status:
                return f"{ts} {_col('plan', 'EXEC  ')} {_trunc(subtask, 40)} → {status}"
            if data.get("nodes"):
                return f"{ts} {_col('plan', 'EXEC  ')} 批状态已更新 · {len(data['nodes'])} 节点"
            return None
        if etype == "summarizer":
            artifacts = data.get("artifacts") or []
            done = data.get("done")
            if done is True:
                return f"{ts} {_col('lvl_success', 'OK   ')} 任务交付汇总完成 · {len(artifacts)} 个产物"
            return None
        if etype in ("replan_done",):
            return f"{ts} {_col('lvl_success', 'OK   ')} 局部重规划完成 · {_trunc(data.get('node', ''), 40)}"
        # 其余未知事件类型：不打扰控制台，仅保留 node_* 兜底
        return None
    except Exception:
        return None


def install_console_logger() -> bool:
    """注册一个后台消费队列，把日志/事件广播格式化为彩色控制台行。

    幂等：重复调用只注册一次；通过 daemon 线程消费，写日志方永不阻塞。
    返回 True 表示本次新安装了日志打印（重复调用返回 False）。
    """
    global _installed
    with _lock:
        if _installed:
            return False
        _installed = True

    q: Queue = Queue()
    _store._event_listeners.append(q)

    def _drain() -> None:
        while True:
            entry = q.get()
            line = _on_entry(entry)
            if line is not None:
                try:
                    print(line, flush=True)
                except Exception:
                    pass

    threading.Thread(target=_drain, daemon=True, name="console-log").start()
    return True