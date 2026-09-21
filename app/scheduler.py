"""
app/scheduler — 多用户定时任务：每用户自己的表 + 各自自动执行。

表结构（scheduled_tasks）存放在**每个用户自己的** memory.db
（data/users/<username>/data/memory.db），读写一律用 username 显式定位，
不依赖调用线程的用户上下文（HTTP 时即"只能操作自己任务"）。

后台调度线程遍历所有账号（含默认用户 Mirror），逐用户扫描各自任务，
到点时以**任务归属用户**的身份执行（set_current_user 包住图执行），
因此定时任务会用该用户自己的 Agnes/硅基流动/Tavily/记忆上下文。

注意：图实例全局单例 + RLock 串行执行，某任务耗时会阻塞其他用户任务到点
（沿用旧调度器设计；多进程/重载下不保证跨进程互斥）。
"""
import os
import sqlite3
import threading
import time as _time
import uuid as _uid

from datetime import datetime as _dt

from app.userctx import current_user, reset_current_user, set_current_user, user_paths, DEFAULT_USER

# 定时任务表（复用该用户的 memory.db）
_SCHED_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    schedule_type TEXT NOT NULL,      -- 'cron' | 'interval'(旧) | 'daily'(旧)
    cron_expr TEXT,                   -- schedule_type=cron 时，标准 5 段 cron（分 时 日 月 周）
    interval_seconds INTEGER,          -- 旧：schedule_type=interval 时
    daily_time TEXT,                   -- 旧：schedule_type=daily 时 "HH:MM"
    prompt TEXT NOT NULL,
    thread_id TEXT,
    enabled INTEGER DEFAULT 1,
    last_run_at TEXT,
    last_result TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

SCHED_TYPES = ("cron", "interval", "daily")
_UPDATABLE = ("name", "prompt", "schedule_type", "cron_expr",
              "interval_seconds", "daily_time", "thread_id", "enabled")


# ==================== 连接 / 建表 ====================
def _ensure(conn: sqlite3.Connection) -> None:
    conn.execute(_SCHED_TABLE_SQL)
    try:
        conn.execute("ALTER TABLE scheduled_tasks ADD COLUMN cron_expr TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE scheduled_tasks ADD COLUMN thread_id TEXT")
    except sqlite3.OperationalError:
        pass
    conn.commit()


def _db(username: str) -> sqlite3.Connection:
    p = user_paths(username).db_path
    os.makedirs(os.path.dirname(p), exist_ok=True)
    conn = sqlite3.connect(p)
    try:
        _ensure(conn)
        conn.row_factory = sqlite3.Row
    except Exception:
        conn.close()
        raise
    return conn


# ==================== CRUD（按用户名隔离） ====================
def list_tasks(username: str = None):
    """username 缺省 = 当前上下文用户。返回该用户的全部定时任务。"""
    conn = _db(username or current_user())
    try:
        rows = conn.execute("SELECT * FROM scheduled_tasks ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_task(task_id: str, username: str = None):
    conn = _db(username or current_user())
    try:
        r = conn.execute("SELECT * FROM scheduled_tasks WHERE id = ?", (task_id,)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def insert_task(task: dict, username: str = None) -> None:
    conn = _db(username or current_user())
    try:
        conn.execute(
            """INSERT INTO scheduled_tasks
               (id, name, schedule_type, cron_expr, interval_seconds, daily_time, prompt, thread_id, enabled)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (task["id"], task["name"], task["schedule_type"], task.get("cron_expr"),
             task.get("interval_seconds"), task.get("daily_time"), task["prompt"],
             task.get("thread_id"), 1 if task.get("enabled", True) else 0))
        conn.commit()
    finally:
        conn.close()


def update_task(task_id: str, username: str = None, **fields) -> None:
    """只更新白名单字段，防止 SQL 注入。"""
    allowed = {k: v for k, v in fields.items() if k in _UPDATABLE or k in ("last_run_at", "last_result")}
    if not allowed:
        return
    sets = ", ".join(f"{k} = ?" for k in allowed)
    vals = list(allowed.values()) + [task_id]
    conn = _db(username or current_user())
    try:
        conn.execute(f"UPDATE scheduled_tasks SET {sets} WHERE id = ?", vals)
        conn.commit()
    finally:
        conn.close()


def delete_task(task_id: str, username: str = None) -> None:
    conn = _db(username or current_user())
    try:
        conn.execute("DELETE FROM scheduled_tasks WHERE id = ?", (task_id,))
        conn.commit()
    finally:
        conn.close()


# ==================== 校验 ====================
def _validate_merged(d: dict):
    """对合并后的任务字段做整体校验，返回 (dict, err)。"""
    if not str(d.get("name") or "").strip():
        return None, "任务名不能为空"
    if not str(d.get("prompt") or "").strip():
        return None, "任务内容（prompt）不能为空"
    stype = str(d.get("schedule_type") or "cron").strip()
    if stype not in SCHED_TYPES:
        return None, "schedule_type 必须是 cron / interval / daily"
    if stype == "cron":
        expr = str(d.get("cron_expr") or "").strip()
        if not expr:
            return None, "cron 表达式必填（如 */5 * * * *）"
        if len(expr.split()) != 5:
            return None, "cron 表达式需为 5 段：分 时 日 月 周（如 0 9 * * *）"
    elif stype == "interval":
        try:
            if int(d.get("interval_seconds") or 0) <= 0:
                return None, "interval_seconds 必须大于 0"
        except (TypeError, ValueError):
            return None, "interval_seconds 必须为整数"
    elif stype == "daily":
        if not str(d.get("daily_time") or "").strip():
            return None, "daily_time 必填（HH:MM）"
    return d, None


def clean_create(payload: dict):
    """把创建入参规整为任务字典。返回 (task, err)。"""
    p = payload or {}
    task = {
        "id": _uid.uuid4().hex,
        "name": str(p.get("name") or "").strip(),
        "schedule_type": str(p.get("schedule_type") or "cron").strip(),
        "cron_expr": str(p.get("cron_expr") or "").strip() or None,
        "interval_seconds": 0,
        "daily_time": str(p.get("daily_time") or "").strip() or None,
        "prompt": str(p.get("prompt") or "").strip(),
        "thread_id": str(p.get("thread_id") or "").strip() or None,
        "enabled": bool(p.get("enabled", True)),
    }
    try:
        task["interval_seconds"] = int(p.get("interval_seconds") or 0)
    except (TypeError, ValueError):
        task["interval_seconds"] = 0
    if task["schedule_type"] != "daily":
        task["daily_time"] = None
    if task["schedule_type"] != "interval":
        task["interval_seconds"] = 0
    return _validate_merged(task)


def clean_update(existing: dict, payload: dict):
    """按白名单字段合并更新项，返回 (changed_fields, err)。"""
    p = payload or {}
    merged = dict(existing)
    fields = {}
    for k in _UPDATABLE:
        if k in p and p[k] is not None:
            v = p[k]
            if k == "enabled":
                merged[k] = bool(v)
            elif k == "interval_seconds":
                try:
                    merged[k] = int(v)
                except (TypeError, ValueError):
                    return {}, "interval_seconds 必须为整数"
            elif isinstance(v, str):
                merged[k] = v.strip()
            else:
                merged[k] = v
            if isinstance(v, str):
                fields[k] = v.strip()
            else:
                fields[k] = merged[k]
    if not fields:
        return {}, "没有可更新的字段"
    _, err = _validate_merged(merged)
    if err:
        return {}, err
    return fields, None


# ==================== cron 匹配 ====================
def _cron_field(field, val, lo, hi):
    """判断单个 cron 字段是否匹配值 val。支持 * 、*/n 、a-b 、a-b/n 、a,b,c 。"""
    field = str(field).strip()
    if field == "*":
        return True
    if "," in field:
        return any(_cron_field(x, val, lo, hi) for x in field.split(","))
    if "-" in field:
        rng, _, step_part = field.partition("/")
        a, b = rng.split("-")
        a, b = int(a), int(b)
        step = int(step_part) if step_part else 1
        return a <= val <= b and (val - a) % step == 0
    if "/" in field:
        base, step = field.split("/")
        return _cron_field(base, val, lo, hi) and int(step) > 0 and val % int(step) == 0
    return int(field) == val


def _cron_match(expr, dt):
    """标准 5 段 cron 匹配：分 时 日 月 周。
    dt 为 datetime；周字段 cron 约定 0/7=周日、1=周一…6=周六，与 Python weekday() 对齐。"""
    try:
        parts = str(expr).split()
        if len(parts) != 5:
            return False
        minute, hour, dom, month, dow = parts
        cron_dow = (dt.weekday() + 1) % 7  # 0=周日 … 6=周六
        fields = [
            (minute, dt.minute, 0, 59),
            (hour, dt.hour, 0, 23),
            (dom, dt.day, 1, 31),
            (month, dt.month, 1, 12),
            (dow, cron_dow, 0, 6),
        ]
        return all(_cron_field(f, v, lo, hi) for (f, v, lo, hi) in fields)
    except Exception:
        return False


def is_due(task: dict, now: float, now_str: str) -> bool:
    """判断任务是否到点执行（含已停用/本分钟已执行/首次 interval 等）。"""
    if not task.get("enabled"):
        return False
    if task["schedule_type"] == "cron":
        try:
            now_dt = _dt.fromisoformat(now_str)
        except Exception:
            return False
        if not _cron_match(task.get("cron_expr") or "", now_dt):
            return False
        last = task.get("last_run_at") or ""
        return not last.startswith(now_str[:16])
    if task["schedule_type"] == "interval":
        secs = task.get("interval_seconds") or 0
        if secs <= 0:
            return False
        last = task.get("last_run_at")
        if not last:
            return True  # 首次立即执行
        try:
            last_ts = _dt.fromisoformat(last).timestamp()
            return (now - last_ts) >= secs
        except Exception:
            return False
    # daily（旧格式）
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


# ==================== 执行 ====================
_GRAPH = None
_GRAPH_LOCK = threading.RLock()
_INFLIGHT: set = set()


def _get_graph():
    global _GRAPH
    if _GRAPH is None:
        from app.graph.builder import build_graph
        _GRAPH = build_graph()
    return _GRAPH


def execute_task(task: dict, username: str):
    """以 username 身份执行任务：set_current_user 包住图流式执行，随后落执行结果。

    调用线程内完成（调度循环/手动立即执行共用）；串行化（_GRAPH_LOCK）。"""
    result = "graph 不可用，任务未执行"
    token = set_current_user(username)
    try:
        with _GRAPH_LOCK:
            try:
                g = _get_graph()
                tid = task.get("thread_id") or _uid.uuid4().hex
                config = {"configurable": {"thread_id": tid}}
                inputs = {"messages": [("user", task["prompt"])]}
                outputs = []
                for ev in g.stream(inputs, config, stream_mode="updates", recursion_limit=30):
                    for _node, upd in (ev or {}).items():
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
        update_task(task["id"], username=username,
                    last_run_at=_dt.now().isoformat(), last_result=result[:2000])
    finally:
        reset_current_user(token)
    return result


def run_task_now(task_id: str, username: str = None):
    """立即执行某任务（同步，返回执行结果或错误）。"""
    u = username or current_user()
    task = get_task(task_id, u)
    if not task:
        return {"ok": False, "error": "任务不存在"}
    if not task.get("enabled"):
        return {"ok": False, "error": "任务已停用，请先启用"}
    key = (u, task_id)
    if key in _INFLIGHT:
        return {"ok": False, "error": "任务正在执行中，请稍后再试"}
    _INFLIGHT.add(key)
    try:
        result = execute_task(task, u)
        return {"ok": True, "result": result}
    finally:
        _INFLIGHT.discard(key)


# ==================== 后台调度循环 ====================
def _active_users() -> list:
    """可执行任务的用户名：默认用户 Mirror + accounts 中状态为 active 的账号。"""
    users = [DEFAULT_USER]
    try:
        from app.server import accounts
        users += [u["username"] for u in accounts.list_users()
                  if str(u.get("username")) != DEFAULT_USER
                  and u.get("status") == accounts.STATUS_ACTIVE]
    except Exception:
        pass
    seen = set()
    out = []
    for u in users:
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


def scheduler_loop():
    """后台调度线程：每 10 秒检查一次所有用户到期任务，到期以归属用户身份串行执行。"""
    while True:
        try:
            now = _time.time()
            now_str = _dt.now().isoformat()
            for u in _active_users():
                try:
                    tasks = list_tasks(u)
                except Exception:
                    continue
                for task in tasks:
                    if not is_due(task, now, now_str):
                        continue
                    key = (u, task["id"])
                    if key in _INFLIGHT:
                        continue
                    _INFLIGHT.add(key)
                    try:
                        execute_task(task, u)
                    except Exception as e:
                        try:
                            update_task(task["id"], username=u, last_result=f"执行失败: {e}"[:2000])
                        except Exception:
                            pass
                    finally:
                        _INFLIGHT.discard(key)
        except Exception:
            pass
        _time.sleep(10)


_started = False
_start_lock = threading.Lock()


def start_scheduler():
    """幂等启动后台调度线程（重载/多导入安全）。"""
    global _started
    with _start_lock:
        if _started:
            return
        _started = True
        threading.Thread(target=scheduler_loop, daemon=True).start()