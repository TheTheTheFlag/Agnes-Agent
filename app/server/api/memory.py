"""app.server.api.memory — 消息/记忆/thread/checkpoint API。"""
import json
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from app.config import DB_PATH, CHECKPOINT_DB_PATH
from app.server import config as _srv_cfg
from app.memory import MemoryManager

router = APIRouter()

@router.get("/api/messages")
async def get_messages(thread_id: str = Query("default"), limit: int = Query(200, ge=1, le=1000)):
    """读取某 thread 的完整消息流（user / assistant / tool），按时间正序。
    每条附加 token 估算 + tool_call→tool_result 耗时（ms）。"""
    import sqlite3 as _sqlite
    try:
        import tiktoken
        _enc = tiktoken.get_encoding("cl100k_base")
    except Exception:
        _enc = None
    db = _sqlite.connect(DB_PATH)
    db.row_factory = _sqlite.Row
    cur = db.execute(
        """SELECT role, content, tool_calls, tool_call_id, timestamp
           FROM messages WHERE thread_id = ?
           ORDER BY timestamp ASC LIMIT ?""",
        (thread_id, limit)
    )
    rows = []
    for r in cur.fetchall():
        content = r["content"] or ""
        tokens = len(_enc.encode(content)) if _enc else None
        row = {
            "role": r["role"],
            "content": content,
            "tool_calls": json.loads(r["tool_calls"]) if r["tool_calls"] else None,
            "tool_call_id": r["tool_call_id"],
            "timestamp": r["timestamp"],
            "tokens": tokens,
        }
        rows.append(row)
    db.close()
    # 计算 tool_call→tool_result 耗时（ms）：assistant 发出的 tool_call 与对应 tool 返回配对
    pending_calls = {}  # tool_call_id -> timestamp
    for row in rows:
        if row["tool_calls"]:
            for tc in row["tool_calls"]:
                if tc.get("id"):
                    pending_calls[tc["id"]] = row["timestamp"]
        if row["role"] == "tool" and row["tool_call_id"] in pending_calls:
            try:
                from datetime import datetime as _dt
                t1 = _dt.fromisoformat(pending_calls[row["tool_call_id"]])
                t2 = _dt.fromisoformat(row["timestamp"])
                row["duration_ms"] = int((t2 - t1).total_seconds() * 1000)
            except Exception:
                pass
            del pending_calls[row["tool_call_id"]]
    # 总 token 统计
    total_tokens = sum((r["tokens"] or 0) for r in rows)
    return {"messages": rows, "total": len(rows), "total_tokens": total_tokens}


@router.get("/api/memory")
async def get_memory_api(thread_id: str = Query("default")):
    """5 层记忆快照。"""
    from app.memory import MemoryManager
    mm = MemoryManager(db_path=DB_PATH, thread_id=thread_id)
    return {
        "L2_profile": mm.get_profile(),
        "L2_preferences": mm.get_preferences(),
        "L3_recent_tasks": mm.get_recent_tasks(limit=10),
        "L4_command_history": mm.get_command_history(thread_id=thread_id, limit=20),
        "L5_knowledge_cache": mm.search_knowledge("", limit=20),
        "prompt_injection_preview": mm.build_memory_injection(thread_id, layers=["L2", "L3"]),
    }



@router.get("/api/threads")
async def get_threads():
    """列出所有有过活动的 thread（来自 messages / task_plans），
    并把当前会话（main.py 注入的 thread_id）置顶、标记 current=true。"""
    import sqlite3 as _sqlite
    db = _sqlite.connect(DB_PATH)
    out = []
    rows = db.execute(
        """SELECT thread_id, COUNT(*) as n, MAX(timestamp) as last
           FROM messages GROUP BY thread_id"""
    ).fetchall()
    for r in rows:
        out.append({"thread_id": r[0], "source": "messages", "count": r[1], "last": r[2]})
    rows = db.execute(
        """SELECT thread_id, COUNT(*) as n, MAX(updated_at) as last
           FROM task_plans WHERE status != 'deleted' GROUP BY thread_id"""
    ).fetchall()
    for r in rows:
        existing = next((t for t in out if t["thread_id"] == r[0]), None)
        if existing:
            existing["task_count"] = r[1]
        else:
            out.append({"thread_id": r[0], "source": "task_plans", "count": r[1], "last": r[2]})
    db.close()
    # 当前会话置顶（即使无消息也要显示）
    current_tid = (_srv_cfg._CONFIG or {}).get("configurable", {}).get("thread_id")
    if current_tid:
        cur_entry = next((t for t in out if t["thread_id"] == current_tid), None)
        if cur_entry:
            cur_entry["current"] = True
            out.remove(cur_entry)
            out.insert(0, cur_entry)
        else:
            out.insert(0, {"thread_id": current_tid, "source": "current", "count": 0, "last": None, "current": True})
    return {"threads": out, "current_thread_id": current_tid}


@router.get("/api/inspect")
async def inspect_checkpoint(thread_id: str = Query("default")):
    """深度解包最新 checkpoint 的 channel_values（msgpack → dict）。
    返回的 messages / task_plan 等是 LangGraph 的内部表示，UTF-8 字段已可读。"""
    import sqlite3 as _sqlite
    try:
        import ormsgpack as msgpack  # LangGraph 用 ormsgpack
    except ImportError:
        try:
            import msgpack
        except ImportError:
            return {"found": True, "error": "msgpack / ormsgpack 未安装", "blob_size": len(blob)}
    db = _sqlite.connect(CHECKPOINT_DB_PATH)
    cur = db.execute(
        """SELECT checkpoint, metadata FROM checkpoints
           WHERE thread_id = ? ORDER BY checkpoint_id DESC LIMIT 1""",
        (thread_id,)
    )
    row = cur.fetchone()
    db.close()
    if not row:
        return {"found": False, "thread_id": thread_id}
    blob, meta_json = row[0], row[1]
    try:
        if msgpack.__name__ == 'ormsgpack':
            # ormsgpack 必须提供 ext_hook 才能解 LangGraph 的 ext 类型；用兜底函数返回原值
            def _ext_hook(code, data):
                try:
                    return msgpack.unpackb(data)
                except Exception:
                    return f"<ext code={code} len={len(data)}>"
            unpacked = msgpack.unpackb(blob, ext_hook=_ext_hook)
        else:
            unpacked = msgpack.unpackb(blob, raw=False)
    except Exception as e:
        return {"found": True, "error": f"msgpack 解码失败: {e}", "blob_size": len(blob)}
    # 提取 v1 通道数据（LangGraph 0.2+ 的格式）
    channel_values = (unpacked.get("channel_values") or {}) if isinstance(unpacked, dict) else {}
    messages = channel_values.get("messages", [])
    # 把消息简化：role + content 摘要（不返回全部以免大）
    msg_summary = []
    for m in messages:
        if isinstance(m, dict):
            t = m.get("type", m.get("role", "?"))
            c = m.get("content", "")
            if isinstance(c, list):
                # 多模态 content 数组
                c = " | ".join(str(x)[:200] for x in c if isinstance(x, (str, dict)))
            else:
                c = str(c)[:500]
            msg_summary.append({"type": t, "content_preview": c, "id": m.get("id", "")})
        else:
            msg_summary.append({"type": "?", "content_preview": str(m)[:200]})
    return {
        "found": True,
        "blob_size": len(blob),
        "metadata": json.loads(meta_json) if meta_json else {},
        "channel_keys": list(channel_values.keys()),
        "messages_count": len(messages),
        "messages_preview": msg_summary[-20:],  # 最近 20 条
        "task_plan": _plan_to_dict(channel_values.get("task_plan")),
    }


def _plan_to_dict(plan):
    if plan is None:
        return None
    if isinstance(plan, dict):
        return plan
    if hasattr(plan, "model_dump"):
        return plan.model_dump()
    return str(plan)


@router.get("/api/checkpoint")
async def get_checkpoint_api(thread_id: str = Query("default")):
    """从 LangGraph SqliteSaver 读最新 checkpoint 的元信息。"""
    import sqlite3 as _sqlite
    db = _sqlite.connect(CHECKPOINT_DB_PATH)
    cur = db.execute(
        """SELECT thread_id, checkpoint_id, parent_checkpoint_id, type, checkpoint, metadata
           FROM checkpoints WHERE thread_id = ?
           ORDER BY checkpoint_id DESC LIMIT 1""",
        (thread_id,)
    )
    row = cur.fetchone()
    db.close()
    if not row:
        return {"found": False, "thread_id": thread_id}
    return {
        "found": True,
        "thread_id": row[0],
        "checkpoint_id": row[1],
        "parent": row[2],
        "type": row[3],
        "checkpoint_blob_size": len(row[4]) if row[4] else 0,
        "metadata": json.loads(row[5]) if row[5] else {},
    }


@router.get("/api/diff")
async def diff_checkpoints_api(thread_id: str = Query("default"), from_id: str = Query(""), to_id: str = Query("")):
    """对比两个 checkpoint 的元信息。"""
    import sqlite3 as _sqlite
    db = _sqlite.connect(CHECKPOINT_DB_PATH)
    def fetch(cid):
        cur = db.execute(
            "SELECT checkpoint, metadata FROM checkpoints WHERE thread_id=? AND checkpoint_id=?",
            (thread_id, cid)
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "checkpoint_blob": row[0],
            "metadata": json.loads(row[1]) if row[1] else {},
        }
    a = fetch(from_id)
    b = fetch(to_id)
    db.close()
    return {
        "from": {"id": from_id, "found": a is not None, "blob_size": len(a["checkpoint_blob"]) if a else 0},
        "to":   {"id": to_id,   "found": b is not None, "blob_size": len(b["checkpoint_blob"]) if b else 0},
        "note": "checkpoint 是 msgpack blob，完整 diff 需 LangGraph 配套；此处仅展示元信息与大小",
    }


