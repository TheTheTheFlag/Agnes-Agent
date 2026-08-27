"""
list_my_recent_tasks：列出最近的任务计划，并可查看任务详情（含子任务）。
模型想"我最近做过什么任务"或"某个任务的进度/详情"时主动调用。
"""
import json
import os
import sqlite3
from typing import Annotated
from langchain_core.tools import tool
from app.config import DB_PATH



@tool
def list_my_recent_tasks(
    limit: int = 5,
    status: str = "completed",
    keyword: str = "",
    task_id: int = 0,
    with_subtasks: bool = False,
) -> str:
    """
    列出最近的任务计划；也可按主任务 id 查看某个任务的详情（含子任务列表与状态）。

    参数:
        limit: 最多返回条数，默认 5
        status: 任务状态过滤，可选 'completed' / 'executing' / 'planning' / 'deleted'。默认 'completed'。
        keyword: 在目标 goal 里做关键词过滤，可选。
        task_id: 若指定（>0），只返回该主任务的详情（含其子任务），并忽略 limit/status/keyword。
        with_subtasks: 为 true 时，每个任务附带其子任务列表（从 subtasks 表按 task_plan_id 查询）。
                 当用户问"任务做到哪了/子任务有哪些/进度如何"时，应传 task_id 或 with_subtasks=true。

    返回:
        JSON。task_id>0 时为单个任务对象（含 subtasks 数组）；
        否则为任务数组，每个元素含 id, thread_id, goal, status, created_at, updated_at；
        with_subtasks=true 时每个元素额外含 subtasks 数组。
    """
    from app.memory import MemoryManager
    mm = MemoryManager(db_path=DB_PATH)

    if task_id and task_id > 0:
        # 查单个任务详情 + 子任务
        return _task_detail(mm, task_id)

    tasks = mm.get_recent_tasks(limit=limit, status_filter=status, keyword=keyword or None)
    if with_subtasks:
        for t in tasks:
            t["subtasks"] = _subtasks_of(mm, t["id"])
    return json.dumps(tasks, ensure_ascii=False, indent=2, default=str)


def _subtasks_of(mm, task_plan_id):
    """按主任务 id 查 subtasks 表，返回子任务列表。"""
    try:
        with sqlite3.connect(mm.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT id, subtask_id, description, dependencies, status, result, artifacts, created_at, updated_at
                   FROM subtasks WHERE task_plan_id = ?
                   ORDER BY id ASC""",
                (task_plan_id,),
            ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                # dependencies / artifacts 是 JSON 字符串，解析成数组便于模型阅读
                for k in ("dependencies", "artifacts"):
                    v = d.get(k)
                    if isinstance(v, str):
                        try:
                            d[k] = json.loads(v)
                        except Exception:
                            d[k] = v
                # result 可能很长，截断到 500 字
                if isinstance(d.get("result"), str) and len(d["result"]) > 500:
                    d["result"] = d["result"][:500] + "..."
                result.append(d)
            return result
    except Exception as e:
        return [{"error": str(e)}]


def _task_detail(mm, task_plan_id):
    """查单个主任务 + 子任务。"""
    import sqlite3 as _s
    try:
        with _s.connect(mm.db_path) as conn:
            conn.row_factory = _s.Row
            r = conn.execute(
                """SELECT id, thread_id, goal, status, created_at, updated_at
                   FROM task_plans WHERE id = ? AND status != 'deleted'""",
                (task_plan_id,),
            ).fetchone()
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    if not r:
        return json.dumps({"error": f"任务 {task_plan_id} 不存在"}, ensure_ascii=False)
    task = dict(r)
    task["subtasks"] = _subtasks_of(mm, task_plan_id)
    return json.dumps(task, ensure_ascii=False, indent=2, default=str)
