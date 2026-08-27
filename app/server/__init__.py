import os
import json
import asyncio
import os as _os
from langgraph.types import Command
from datetime import datetime
from typing import Dict, Any, List, Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import uvicorn
import threading
import time as _time

from app.config import DB_PATH, CHECKPOINT_DB_PATH, STATIC_DIR as _STATIC_DIR
from app.server.store import (update_state, update_prompt, add_log_entry, add_event, _state_snapshot, _state_snapshots, _prompt_snapshot, _log_entries, _events, _event_listeners)

# FastAPI 应用实例
app = FastAPI(title="Agnes Agent Debug Panel", version="1.0.0")

# 静态文件（marked.min.js 等本地资源，避免 CDN 依赖）
if _os.path.isdir(_STATIC_DIR):
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/", response_class=HTMLResponse)
async def root():
    """返回前端面板（静态文件）。"""
    idx = _os.path.join(_STATIC_DIR, "index.html")
    if _os.path.exists(idx):
        with open(idx, 'r', encoding='utf-8') as fh:
            return HTMLResponse(fh.read())
    return HTMLResponse('<h1>index.html 缺失</h1>', status_code=500)

def start_debug_server(host: str = "0.0.0.0", port: int = 8000, max_tries: int = 10):
    """启动调试服务器；若 port 已被占用则自动顺延到 port+1, port+2, ... 直到 max_tries。
    避免 8000 端口冲突导致整个 Agent 启动失败。
    注意：Windows 上 bind 0.0.0.0 不一定报错，所以改用 connect 探测占用。"""
    import socket as _socket

    def _is_port_busy(p: int) -> bool:
        """尝试向 127.0.0.1:p 发起 connect；连得上代表已被占用"""
        try:
            with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as s:
                s.settimeout(0.3)
                s.connect_ex(("127.0.0.1", p))
                # connect_ex 返回 0 = 连上 = 端口被占；非 0 = 没人监听
                # 但 uvicorn 刚启动时可能 1-2s 才 listen，所以加 0.3s timeout 已经够短
                # 真正判断要看返回码
        except OSError:
            return True
        # 重新做一次更可靠的探测
        try:
            with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as s:
                s.settimeout(0.3)
                rc = s.connect_ex(("127.0.0.1", p))
                return rc == 0
        except OSError:
            return True

    def run(p):
        uvicorn.run(app, host=host, port=p, log_level="warning")

    chosen = None
    for offset in range(max_tries):
        candidate = port + offset
        if not _is_port_busy(candidate):
            chosen = candidate
            break

    if chosen is None:
        print(f"⚠️ [debug_server] 端口 {port}~{port+max_tries-1} 全部被占用，控制面板未启动（不影响 Agent 主流程）")
        thread = threading.Thread(target=lambda: None, daemon=True)
        thread.start()
        return thread

    thread = threading.Thread(target=run, args=(chosen,), daemon=True)
    thread.start()
    if chosen != port:
        print(f"🔍 控制面板: http://localhost:{chosen} (原 {port} 被占用，已自动顺延)")
    else:
        print(f"🔍 控制面板: http://localhost:{chosen}")
    return thread


# ==================== 与主进程共享 graph 实例 ====================

# 持有 main.py 注入的 graph 对象和当前 config
_GRAPH = None
_CONFIG = None


def set_graph(graph, config: dict):
    """由 main.py 在启动后注入；debug_server 后续 /api/chat 会调用它。
    注意：_GRAPH/_CONFIG 定义在 app.server.config 模块（api 子路由都从那里 import），
    这里必须直接写 config 模块的全局，而不是本模块的局部副本。"""
    import app.server.config as _cfg
    _cfg._GRAPH = graph
    _cfg._CONFIG = config
    # 同时更新本模块的引用（兼容 from app.server import _GRAPH 的用法）
    global _GRAPH, _CONFIG
    _GRAPH = graph
    _CONFIG = config


# ==================== 模型配置管理 ====================

# 内置厂商 + 模型目录（页面下拉用）
from app.server.config import (router as _config_router, _GRAPH, _CONFIG, load_model_config,
    save_model_config, get_custom_models, save_custom_models, get_full_catalog,
    get_current_model, resolve_provider_env, rebuild_graph_with_model)
app.include_router(_config_router)
# 功能 API 子路由
from app.server.api import system as _api_system
from app.server.api import memory as _api_memory
from app.server.api import tools as _api_tools
from app.server.api import chat as _api_chat
from app.server.api import git as _api_git
app.include_router(_api_system.router)
app.include_router(_api_memory.router)
app.include_router(_api_tools.router)
app.include_router(_api_chat.router)
app.include_router(_api_git.router)

_SCHED_DB = DB_PATH  # 定时任务表复用 memory.db

_SCHED_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    schedule_type TEXT NOT NULL,      -- 'interval' | 'daily'
    interval_seconds INTEGER,          -- schedule_type=interval 时
    daily_time TEXT,                   -- schedule_type=daily 时 "HH:MM"
    prompt TEXT NOT NULL,
    thread_id TEXT,
    enabled INTEGER DEFAULT 1,
    last_run_at TEXT,
    last_result TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

def _sched_init():
    import sqlite3 as _sqlite
    with _sqlite.connect(_SCHED_DB) as conn:
        conn.execute(_SCHED_TABLE_SQL)

_sched_init()


def _sched_all():
    import sqlite3 as _sqlite
    with _sqlite.connect(_SCHED_DB) as conn:
        conn.row_factory = _sqlite.Row
        rows = conn.execute("SELECT * FROM scheduled_tasks ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]


def _sched_get(task_id):
    import sqlite3 as _sqlite
    with _sqlite.connect(_SCHED_DB) as conn:
        conn.row_factory = _sqlite.Row
        r = conn.execute("SELECT * FROM scheduled_tasks WHERE id = ?", (task_id,)).fetchone()
        return dict(r) if r else None


def _sched_insert(task: dict):
    import sqlite3 as _sqlite
    with _sqlite.connect(_SCHED_DB) as conn:
        conn.execute(
            """INSERT INTO scheduled_tasks (id, name, schedule_type, interval_seconds, daily_time, prompt, thread_id, enabled)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (task["id"], task["name"], task["schedule_type"], task.get("interval_seconds"),
             task.get("daily_time"), task["prompt"], task.get("thread_id"), 1 if task.get("enabled", True) else 0)
        )


def _sched_update(task_id, **fields):
    import sqlite3 as _sqlite
    sets = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [task_id]
    with _sqlite.connect(_SCHED_DB) as conn:
        conn.execute(f"UPDATE scheduled_tasks SET {sets} WHERE id = ?", vals)


def _sched_delete(task_id):
    import sqlite3 as _sqlite
    with _sqlite.connect(_SCHED_DB) as conn:
        conn.execute("DELETE FROM scheduled_tasks WHERE id = ?", (task_id,))


def _sched_due(task: dict, now: float, now_str: str) -> bool:
    """判断任务是否到点执行。"""
    if not task.get("enabled"):
        return False
    if task["schedule_type"] == "interval":
        secs = task.get("interval_seconds") or 0
        if secs <= 0:
            return False
        last = task.get("last_run_at")
        if not last:
            return True  # 首次立即执行
        try:
            from datetime import datetime as _dt
            last_ts = _dt.fromisoformat(last).timestamp()
            return (now - last_ts) >= secs
        except Exception:
            return False
    else:  # daily
        hm = (task.get("daily_time") or "00:00").strip()
        try:
            h, m = hm.split(":")
            cur_hm = now_str[11:16]
            target = f"{int(h):02d}:{int(m):02d}"
            last = task.get("last_run_at") or ""
            today = now_str[:10]
            return cur_hm >= target and not last.startswith(today)
        except Exception:
            return False


_SCHED_GRAPH = None
_SCHED_LOCK = threading.Lock()


def _get_sched_graph():
    """调度专用 graph：在调用线程（调度线程）内首次构建，connection 与主线程隔离。"""
    global _SCHED_GRAPH
    if _SCHED_GRAPH is None:
        from app.graph.builder import build_graph
        _SCHED_GRAPH = build_graph()
    return _SCHED_GRAPH


def _sched_run_task(task: dict):
    """执行定时任务：把 prompt 提交给调度专用 graph。"""
    result = "graph 不可用，任务未执行"
    with _SCHED_LOCK:  # 防止重入
        try:
            import uuid as _uid
            from app.graph.builder import build_graph
            g = _get_sched_graph()
            tid = task.get("thread_id") or _uid.uuid4().hex
            config = {"configurable": {"thread_id": tid}}
            inputs = {"messages": [("user", task["prompt"])]}
            outputs = []
            for ev in g.stream(inputs, config, stream_mode="updates", recursion_limit=30):
                for node, upd in (ev or {}).items():
                    if isinstance(upd, dict) and isinstance(upd.get("messages"), list):
                        for m in reversed(upd["messages"]):
                            if hasattr(m, "type") and getattr(m, "type") == "ai":
                                c = getattr(m, "content", "") or ""
                                if c:
                                    outputs.append(str(c)[:800])
                                break
            result = outputs[-1] if outputs else "已执行（无文本输出）"
        except Exception as e:
            result = f"执行失败: {e}"
    from datetime import datetime as _dt
    _sched_update(task["id"], last_run_at=_dt.now().isoformat(), last_result=result[:2000])


def _scheduler_loop():
    """后台调度线程：每 30 秒检查一次到期任务。"""
    while True:
        try:
            from datetime import datetime as _dt
            now = _time.time()
            now_str = _dt.now().isoformat()
            for task in _sched_all():
                if _sched_due(task, now, now_str):
                    try:
                        _sched_run_task(task)
                    except Exception as e:
                        try:
                            _sched_update(task["id"], last_result=f"执行失败: {e}"[:2000])
                        except Exception:
                            pass
        except Exception:
            pass
        _time.sleep(10)


_sched_thread = threading.Thread(target=_scheduler_loop, daemon=True)
_sched_thread.start()


# ==================== Memory DB 浏览器（前端 UI） ====================

# 白名单：只允许这些表通过 API 访问。绝不让前端传任意表名/任意 SQL。
_MEMORY_TABLES = {
    "user_profile": {
        "label": "用户画像 (L2)",
        "pk": "key",
        "columns": ["key", "value", "updated_at"],
    },
    "user_preferences": {
        "label": "用户偏好 (L2)",
        "pk": "key",
        "columns": ["key", "value", "updated_at"],
    },
    "messages": {
        "label": "对话消息",
        "pk": "id",
        "columns": ["id", "thread_id", "role", "content", "tool_calls", "tool_call_id", "timestamp"],
    },
    "task_plans": {
        "label": "任务计划",
        "pk": "id",
        "columns": ["id", "thread_id", "goal", "status", "created_at", "updated_at", "deleted_at"],
    },
    "subtasks": {
        "label": "子任务",
        "pk": "id",
        "columns": ["id", "thread_id", "task_plan_id", "subtask_id", "description", "dependencies", "status", "result", "artifacts", "created_at", "updated_at"],
    },
    "task_summaries": {
        "label": "任务摘要",
        "pk": "id",
        "columns": ["id", "thread_id", "summary_text", "start_time", "end_time", "created_at"],
    },
    "command_history": {
        "label": "命令历史 (L4)",
        "pk": "id",
        "columns": ["id", "thread_id", "command", "exit_code", "stdout_preview", "stderr_preview", "duration_ms", "success", "created_at"],
    },
    "semantic_cache": {
        "label": "知识缓存 (L5)",
        "pk": "id",
        "columns": ["id", "source", "query", "content", "hit_count", "last_accessed_at", "created_at", "expires_at"],
    },
}

import re as _re
_SAFE_IDENT = _re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _mdb_check_table(name: str):
    if not name or not _SAFE_IDENT.match(name) or name not in _MEMORY_TABLES:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=f"表名不合法或不在白名单: {name}")


def _mdb_check_col(table: str, col: str):
    if not col or not _SAFE_IDENT.match(col):
        raise ValueError(f"列名不合法: {col}")
    if col not in _MEMORY_TABLES[table]["columns"]:
        raise ValueError(f"列 {col} 不在表 {table} 的白名单列中")


@app.get("/api/memorydb/tables")
async def mdb_tables():
    return {
        "tables": [
            {"name": t, "label": meta["label"], "pk": meta["pk"], "columns": meta["columns"]}
            for t, meta in _MEMORY_TABLES.items()
        ]
    }


@app.get("/api/memorydb/schema")
async def mdb_schema(table: str):
    """返回表的真实结构（PRAGMA table_info）：字段名/类型/是否必填/默认值。"""
    import sqlite3 as _sqlite
    try:
        _mdb_check_table(table)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    with _sqlite.connect(DB_PATH) as conn:
        conn.row_factory = _sqlite.Row
        cols = [dict(r) for r in conn.execute(f"PRAGMA table_info({table})")]
    # 标记自动字段：自增主键（pk=1 且 INTEGER）
    for c in cols:
        c["auto"] = bool(c["pk"]) and c["type"].upper() == "INTEGER"
    return {"table": table, "columns": cols}


@app.get("/api/memorydb/query")
async def mdb_query(table: str, limit: int = 50, where: str = ""):
    """查询某表。where 形如 'key=\"foo\"' 或 'thread_id=\"xxx\" AND role=\"user\"'（已加白名单过滤）。"""
    import sqlite3 as _sqlite
    _mdb_check_table(table)
    if limit < 1 or limit > 500:
        limit = 50
    # 严格 where 校验：只允许 col op value（value 必带引号或纯数字）
    where_clause = ""
    if where and where.strip():
        # 拒绝任何 ';' 注释、union、drop 等
        wl = where.lower()
        for bad in (';', '--', '/*', '*/', 'union', 'drop', 'delete', 'update', 'insert', 'alter', 'create', 'pragma', 'attach'):
            if bad in wl:
                return JSONResponse({"error": f"WHERE 包含禁用关键字: {bad}"}, status_code=400)
        # 必须包含 = 或 IS 或 IN/LIKE
        if not any(op in where for op in ('=', 'IS', 'IN', 'LIKE', '<', '>', '!=', '<>')):
            return JSONResponse({"error": "WHERE 必须包含比较运算符"}, status_code=400)
        where_clause = " WHERE " + where
    sql = f"SELECT * FROM {table}{where_clause} LIMIT ?"
    with _sqlite.connect(DB_PATH) as conn:
        conn.row_factory = _sqlite.Row
        try:
            cur = conn.execute(sql, (limit,))
            rows = [dict(r) for r in cur.fetchall()]
        except _sqlite.OperationalError as e:
            return JSONResponse({"error": f"SQL 错误: {e}"}, status_code=400)
    return {"rows": rows, "total": len(rows), "table": table, "sql": sql}


@app.post("/api/memorydb/insert")
async def mdb_insert(payload: dict):
    import sqlite3 as _sqlite
    p = payload or {}
    table = p.get("table", "")
    _mdb_check_table(table)
    cols = p.get("columns") or []
    vals = p.get("values") or []
    if not cols or not isinstance(cols, list) or not isinstance(vals, list) or len(cols) != len(vals):
        return JSONResponse({"error": "columns/values 必填且长度相同"}, status_code=400)
    for c in cols:
        _mdb_check_col(table, c)
    placeholders = ", ".join(["?"] * len(cols))
    col_sql = ", ".join(cols)
    with _sqlite.connect(DB_PATH) as conn:
        try:
            cur = conn.execute(f"INSERT INTO {table} ({col_sql}) VALUES ({placeholders})", vals)
            conn.commit()
            new_id = cur.lastrowid
        except _sqlite.IntegrityError as e:
            return JSONResponse({"error": f"约束冲突: {e}"}, status_code=400)
        except _sqlite.OperationalError as e:
            return JSONResponse({"error": f"SQL 错误: {e}"}, status_code=400)
    return {"ok": True, "id": new_id, "table": table}


@app.post("/api/memorydb/update")
async def mdb_update(payload: dict):
    """更新某行。payload: {table, pk_value, set: {col: value, ...}}"""
    import sqlite3 as _sqlite
    p = payload or {}
    table = p.get("table", "")
    _mdb_check_table(table)
    pk = p.get("pk_value")
    sets = p.get("set") or {}
    if pk is None or not isinstance(sets, dict) or not sets:
        return JSONResponse({"error": "pk_value 和 set 必填"}, status_code=400)
    pk_col = _MEMORY_TABLES[table]["pk"]
    set_parts, set_vals = [], []
    for col, val in sets.items():
        _mdb_check_col(table, col)
        set_parts.append(f"{col} = ?")
        set_vals.append(val)
    sql = f"UPDATE {table} SET {', '.join(set_parts)} WHERE {pk_col} = ?"
    set_vals.append(pk)
    with _sqlite.connect(DB_PATH) as conn:
        try:
            cur = conn.execute(sql, set_vals)
            conn.commit()
            affected = cur.rowcount
        except _sqlite.OperationalError as e:
            return JSONResponse({"error": f"SQL 错误: {e}"}, status_code=400)
    return {"ok": True, "affected": affected, "table": table}


@app.post("/api/memorydb/delete")
async def mdb_delete(payload: dict):
    """删除某行。payload: {table, pk_value}"""
    import sqlite3 as _sqlite
    p = payload or {}
    table = p.get("table", "")
    pk = p.get("pk_value")
    _mdb_check_table(table)
    if pk is None:
        return JSONResponse({"error": "pk_value 必填"}, status_code=400)
    pk_col = _MEMORY_TABLES[table]["pk"]
    with _sqlite.connect(DB_PATH) as conn:
        try:
            cur = conn.execute(f"DELETE FROM {table} WHERE {pk_col} = ?", (pk,))
            conn.commit()
            affected = cur.rowcount
        except _sqlite.OperationalError as e:
            return JSONResponse({"error": f"SQL 错误: {e}"}, status_code=400)
    return {"ok": True, "affected": affected, "table": table}


@app.get("/api/scheduler")
async def sched_list():
    return {"tasks": _sched_all()}


@app.post("/api/scheduler")
async def sched_create(payload: dict):
    """新增定时任务。
    payload: { name, schedule_type: 'interval'|'daily', interval_seconds?, daily_time?("HH:MM"), prompt, thread_id? }
    """
    import uuid as _uid
    p = payload or {}
    name = (p.get("name") or "").strip()
    prompt = (p.get("prompt") or "").strip()
    stype = p.get("schedule_type", "interval")
    if not name or not prompt:
        return JSONResponse({"error": "name 和 prompt 必填"}, status_code=400)
    if stype not in ("interval", "daily"):
        return JSONResponse({"error": "schedule_type 必须是 interval 或 daily"}, status_code=400)
    task = {
        "id": _uid.uuid4().hex,
        "name": name,
        "schedule_type": stype,
        "interval_seconds": int(p.get("interval_seconds") or 0),
        "daily_time": p.get("daily_time"),
        "prompt": prompt,
        "thread_id": p.get("thread_id"),
        "enabled": p.get("enabled", True),
    }
    _sched_insert(task)
    return {"task": _sched_get(task["id"])}


@app.post("/api/scheduler/{task_id}/toggle")
async def sched_toggle(task_id: str):
    task = _sched_get(task_id)
    if not task:
        return JSONResponse({"error": "任务不存在"}, status_code=404)
    _sched_update(task_id, enabled=0 if task["enabled"] else 1)
    return {"task": _sched_get(task_id)}


@app.delete("/api/scheduler/{task_id}")
async def sched_delete(task_id: str):
    task = _sched_get(task_id)
    if not task:
        return JSONResponse({"error": "任务不存在"}, status_code=404)
    _sched_delete(task_id)
    return {"ok": True, "deleted": task_id}


__all__ = ['app', 'update_state', 'update_prompt', 'add_log_entry', 'add_event', 'start_debug_server', 'set_graph', 'load_model_config', 'save_model_config', 'get_current_model', 'rebuild_graph_with_model']