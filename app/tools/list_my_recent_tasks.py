"""
list_my_recent_tasks：列出最近的任务计划，并可查看任务详情（含节点）。
模型想"我最近做过什么任务"或"某个任务的进度/详情"时主动调用。
"""
import json
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
    with_nodes: bool = False,
) -> str:
    """
    列出最近的任务计划（基于 DAG 规划器）；也可按主任务 id 查看某个任务的详情（含节点列表与状态）。

    参数:
        limit: 最多返回条数，默认 5
        status: 任务状态过滤，可选 'completed' / 'executing' / 'planning' / 'failed' / 'deleted'。默认 'completed'。
        keyword: 在目标 goal 里做关键词过滤，可选。
        task_id: 若指定（>0），只返回该主任务的详情（含其节点），并忽略 limit/status/keyword。
        with_nodes: 为 true 时，每个任务附带其节点列表（从 dag_nodes 表按 plan_id 查询）。
                 当用户问"任务做到哪了/节点有哪些/进度如何"时，应传 task_id 或 with_nodes=true。

    返回:
        JSON。task_id>0 时为单个任务对象（含 nodes 数组）；
        否则为任务数组，每个元素含 id, thread_id, goal, status, created_at, updated_at；
        with_nodes=true 时每个元素额外含 nodes 数组。
    """
    if task_id and task_id > 0:
        # 查单个任务详情 + 节点
        return _task_detail(task_id)

    tasks = _list_tasks(limit=limit, status_filter=status, keyword=keyword or None)
    if with_nodes:
        for t in tasks:
            t["nodes"] = _nodes_of(t["id"])
    return json.dumps(tasks, ensure_ascii=False, indent=2, default=str)


def _list_tasks(limit: int = 5, status_filter: str = None, keyword: str = None) -> list:
    """从 dag_plans 表读取最近的任务计划。"""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            sql = "SELECT id, thread_id, goal, status, created_at, updated_at FROM dag_plans WHERE 1=1"
            params = []
            if status_filter:
                sql += " AND status = ?"
                params.append(status_filter)
            if keyword:
                sql += " AND goal LIKE ?"
                params.append(f"%{keyword}%")
            sql += " ORDER BY updated_at DESC LIMIT ?"
            params.append(limit)
            cur = conn.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception as e:
        return [{"error": str(e)}]


def _nodes_of(plan_id):
    """按主任务 id 查 dag_nodes 表，返回节点列表。"""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT id, node_id, description, tool, params, status, result, artifacts, updated_at
                   FROM dag_nodes WHERE plan_id = ?
                   ORDER BY id ASC""",
                (plan_id,),
            ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                # params / artifacts 是 JSON 字符串，解析成数组/对象便于模型阅读
                for k in ("params", "artifacts"):
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


def _task_detail(task_plan_id):
    """查单个主任务 + 节点。"""
    import sqlite3 as _s
    try:
        with _s.connect(DB_PATH) as conn:
            conn.row_factory = _s.Row
            r = conn.execute(
                """SELECT id, thread_id, goal, status, created_at, updated_at
                   FROM dag_plans WHERE id = ? AND status != 'deleted'""",
                (task_plan_id,),
            ).fetchone()
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    if not r:
        return json.dumps({"error": f"任务 {task_plan_id} 不存在"}, ensure_ascii=False)
    task = dict(r)
    task["nodes"] = _nodes_of(task_plan_id)
    return json.dumps(task, ensure_ascii=False, indent=2, default=str)
