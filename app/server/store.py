"""app.server.store — 内存快照与日志/事件流（日志/事件持久化到 memory.db，重启不丢失）。"""
import json
import sqlite3 as _sqlite
from datetime import datetime
from typing import Dict, Any, List, Optional

_state_snapshot: Dict[str, Any] = {}
_state_snapshots: Dict[str, Dict[str, Any]] = {}  # per-thread 快照: {thread_id: state}
_prompt_snapshot: str = ""
_log_entries: List[Dict[str, Any]] = []
_max_log_entries = 1000
_events: List[Dict[str, Any]] = []
_event_listeners = []

# 数据库路径（与 graph.py 保持一致）
import os as _os
from app.config import DB_PATH, CHECKPOINT_DB_PATH, STATIC_DIR as _STATIC_DIR
_BASE_DIR = _os.path.dirname(_os.path.abspath(__file__))

# 日志/事件持久化表（重启后仍可查历史会话的日志/事件）
_LOG_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS app_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    level TEXT,
    message TEXT,
    data TEXT,
    timestamp TEXT
);
CREATE TABLE IF NOT EXISTS app_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT,
    data TEXT,
    timestamp TEXT
);
"""


def _init_persist():
    try:
        with _sqlite.connect(DB_PATH) as conn:
            conn.executescript(_LOG_TABLE_SQL)
    except Exception:
        pass


_init_persist()


def _persist_log(level: str, message: str, data, timestamp: str):
    try:
        with _sqlite.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO app_logs (level, message, data, timestamp) VALUES (?,?,?,?)",
                (level, message, json.dumps(data, ensure_ascii=False, default=str) if data is not None else None, timestamp),
            )
    except Exception:
        pass


def _persist_event(event_type: str, data, timestamp: str):
    try:
        with _sqlite.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO app_events (type, data, timestamp) VALUES (?,?,?)",
                (event_type, json.dumps(data, ensure_ascii=False, default=str) if data is not None else None, timestamp),
            )
    except Exception:
        pass


def get_persisted_logs(limit: int = 200) -> List[Dict[str, Any]]:
    """从 DB 读取最近的日志（含历史会话，重启后仍可查）。"""
    try:
        with _sqlite.connect(DB_PATH) as conn:
            conn.row_factory = _sqlite.Row
            rows = conn.execute(
                "SELECT level, message, data, timestamp FROM app_logs ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        out = []
        for r in reversed(rows):
            item = {"level": r["level"], "message": r["message"], "timestamp": r["timestamp"], "data": None}
            if r["data"]:
                try:
                    item["data"] = json.loads(r["data"])
                except Exception:
                    pass
            out.append(item)
        return out
    except Exception:
        return []


def get_persisted_events(limit: int = 100) -> List[Dict[str, Any]]:
    """从 DB 读取最近的事件（含历史会话，重启后仍可查）。"""
    try:
        with _sqlite.connect(DB_PATH) as conn:
            conn.row_factory = _sqlite.Row
            rows = conn.execute(
                "SELECT type, data, timestamp FROM app_events ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        out = []
        for r in reversed(rows):
            item = {"type": r["type"], "timestamp": r["timestamp"], "data": None}
            if r["data"]:
                try:
                    item["data"] = json.loads(r["data"])
                except Exception:
                    pass
            out.append(item)
        return out
    except Exception:
        return []


def clear_persisted():
    """清空 DB 中的日志/事件。"""
    try:
        with _sqlite.connect(DB_PATH) as conn:
            conn.execute("DELETE FROM app_logs")
            conn.execute("DELETE FROM app_events")
    except Exception:
        pass


def update_state(state: Dict[str, Any]):
    global _state_snapshot, _state_snapshots
    try:
        snap = json.loads(json.dumps(state, default=str, ensure_ascii=False))
    except:
        snap = {"error": "无法序列化 State"}
    _state_snapshot = snap  # 兼容旧引用
    tid = state.get("thread_id") if isinstance(state, dict) else None
    if tid:
        _state_snapshots[tid] = snap


def update_prompt(prompt: str):
    global _prompt_snapshot
    _prompt_snapshot = prompt


def add_log_entry(level: str, message: str, data: Optional[Dict] = None):
    global _log_entries
    entry = {
        "timestamp": datetime.now().isoformat(),
        "level": level,
        "message": message,
        "data": data
    }
    _log_entries.append(entry)
    if len(_log_entries) > _max_log_entries:
        _log_entries = _log_entries[-_max_log_entries:]

    # 持久化到 DB（重启后历史会话仍可查）
    _persist_log(level, message, data, entry["timestamp"])

    global _event_listeners
    for listener in _event_listeners:
        try:
            listener.put_nowait(entry)
        except:
            pass


def add_event(event_type: str, data: Any):
    global _events
    entry = {
        "type": event_type,
        "timestamp": datetime.now().isoformat(),
        "data": data
    }
    _events.append(entry)
    if len(_events) > 100:
        _events = _events[-100:]

    # 持久化到 DB
    _persist_event(event_type, data, entry["timestamp"])

    global _event_listeners
    for listener in _event_listeners:
        try:
            listener.put_nowait(entry)
        except:
            pass


