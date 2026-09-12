"""app.planning.dag_storage — Plan-and-Execute 的 DAG 持久化层（SQLite）

按"业界四层"重构规划执行子系统：
  ③ 状态机 + Checkpoint 的存储实现。节点/边各自成表，状态持久化到磁盘，
     重启可按 checkpoint 从最近已提交状态继续（不重跑已完成节点）。

设计要点（对齐文章）：
  - 节点节点状态机：pending / ready / running / success / failed / skipped
  - 边带 soft 标记（软依赖：父失败不阻塞子，但子上下文标注"缺数据"）
  - 按节点提交 = 轻量 checkpoint；recover_stale_running 处理重启后"running 悬空"
"""

import json
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.config import DB_PATH

# ===================== 节点状态机 =====================
STATUS_PENDING = "pending"   # 前置未完成
STATUS_READY = "ready"       # 可执行（前置满足）
STATUS_RUNNING = "running"   # 执行中
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"   # 前置（强依赖）失败被跳过

TERMINAL = (STATUS_SUCCESS, STATUS_FAILED, STATUS_SKIPPED)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class DAGStorage:
    """SQLite 存储：一张计划（plan）由 nodes + edges 表达。"""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_db()

    # ---------------- schema ----------------
    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            # 旧规划器时代已废弃：task_plans / subtasks 表不再创建，
            # 相关接口（/api/threads、list_my_recent_tasks 等）已统一改读 dag_* 表。
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS dag_plans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    thread_id TEXT NOT NULL,
                    goal TEXT NOT NULL,
                    replan_count INTEGER DEFAULT 0,     -- 已重规划次数（上限 3）
                    status TEXT NOT NULL,               -- planning / executing / completed / failed / deleted
                    checkpoint TEXT,                    -- json: {"nodes":{...}}
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_dag_plans_thread ON dag_plans(thread_id);

                CREATE TABLE IF NOT EXISTS dag_nodes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    plan_id INTEGER NOT NULL,
                    node_id TEXT NOT NULL,               -- 逻辑 id（planner 给，如 "1" "2a"）
                    description TEXT NOT NULL,           -- 节点动作描述
                    acceptance_criteria TEXT,            -- 验收标准（planner 给：怎样算完成 / 要产出哪些文件）
                    tool TEXT,                           -- 建议执行的工具（可选，planner 填）
                    params TEXT,                         -- 工具参数（JSON）
                    status TEXT NOT NULL DEFAULT 'pending',
                    result TEXT,                         -- 节点结果
                    artifacts TEXT,                      -- 产出文件/引用（JSON 数组）
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (plan_id) REFERENCES dag_plans(id) ON DELETE CASCADE,
                    UNIQUE(plan_id, node_id)
                );
                CREATE INDEX IF NOT EXISTS idx_dag_nodes_plan ON dag_nodes(plan_id);

                -- 边表：from -> to，soft 表示软依赖
                CREATE TABLE IF NOT EXISTS dag_edges (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    plan_id INTEGER NOT NULL,
                    from_id TEXT NOT NULL,
                    to_id TEXT NOT NULL,
                    soft INTEGER DEFAULT 0,
                    FOREIGN KEY (plan_id) REFERENCES dag_plans(id) ON DELETE CASCADE,
                    UNIQUE(plan_id, from_id, to_id)
                );
                CREATE INDEX IF NOT EXISTS idx_dag_edges_plan ON dag_edges(plan_id);
            """)

            # ---- dag_nodes 结构迁移：旧库补 acceptance_criteria 列 ----
            # 新库由上方 CREATE TABLE 直接带列；旧库（CREATE TABLE IF NOT EXISTS 不生效）
            # 必须 PRAGMA 检测后 ALTER 补列，否则 planner 写入的验收标准会被静默丢弃。
            try:
                _dn_cols = {r[1] for r in conn.execute("PRAGMA table_info(dag_nodes)").fetchall()}
                if "acceptance_criteria" not in _dn_cols:
                    conn.execute("ALTER TABLE dag_nodes ADD COLUMN acceptance_criteria TEXT")
            except Exception:
                pass

    # ---------------- plan ----------------
    def create_plan(self, thread_id: str, goal: str) -> int:
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "INSERT INTO dag_plans (thread_id, goal, status, checkpoint) VALUES (?, ?, 'planning', NULL)",
                (thread_id, goal),
            )
            return cur.lastrowid

    def set_plan_status(self, plan_id: int, status: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE dag_plans SET status = ?, updated_at = ? WHERE id = ?",
                (status, _now(), plan_id),
            )

    def bump_replan(self, plan_id: int) -> int:
        """重规划计数 +1，返回当前计数（>=3 表示已达上限）。"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE dag_plans SET replan_count = replan_count + 1, updated_at = ? WHERE id = ?",
                (_now(), plan_id),
            )
            row = conn.execute("SELECT replan_count FROM dag_plans WHERE id = ?", (plan_id,)).fetchone()
            return row[0] if row else 0

    def get_plan(self, plan_id: int) -> Optional[Dict]:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT id, thread_id, goal, replan_count, status FROM dag_plans WHERE id = ?", (plan_id,)
            ).fetchone()
            if not row:
                return None
            return {
                "id": row[0], "thread_id": row[1], "goal": row[2],
                "replan_count": row[3], "status": row[4],
            }

    def get_plan_by_thread(self, thread_id: str) -> Optional[Dict]:
        """取该线程最近一个未删除的计划主记录。"""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT id, thread_id, goal, replan_count, status FROM dag_plans "
                "WHERE thread_id = ? AND status != 'deleted' ORDER BY id DESC LIMIT 1",
                (thread_id,),
            ).fetchone()
            if not row:
                return None
            return {
                "id": row[0], "thread_id": row[1], "goal": row[2],
                "replan_count": row[3], "status": row[4],
            }

    def delete_plan(self, plan_id: int):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE dag_plans SET status = 'deleted', updated_at = ? WHERE id = ?",
                         (_now(), plan_id))

    # ---------------- nodes ----------------
    def add_node(self, plan_id: int, node_id: str, description: str, tool: str = None,
                 params: Dict = None, status: str = STATUS_PENDING,
                 acceptance_criteria: str = None):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO dag_nodes
                   (plan_id, node_id, description, acceptance_criteria, tool, params, status, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (plan_id, node_id, description, acceptance_criteria or None, tool,
                 json.dumps(params, ensure_ascii=False) if params else None, status, _now()),
            )

    def get_nodes(self, plan_id: int) -> List[Dict]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT node_id, description, acceptance_criteria, tool, params, status, result, artifacts "
                "FROM dag_nodes WHERE plan_id = ? ORDER BY id",
                (plan_id,),
            ).fetchall()
        return [{
            "id": r[0], "description": r[1],
            "acceptance_criteria": r[2] or "",
            "tool": r[3],
            "params": json.loads(r[4]) if r[4] else {},
            "status": r[5], "result": r[6],
            "artifacts": json.loads(r[7]) if r[7] else [],
        } for r in rows]

    def get_node(self, plan_id: int, node_id: str) -> Optional[Dict]:
        with sqlite3.connect(self.db_path) as conn:
            r = conn.execute(
                "SELECT node_id, description, acceptance_criteria, tool, params, status, result, artifacts "
                "FROM dag_nodes WHERE plan_id = ? AND node_id = ?",
                (plan_id, node_id),
            ).fetchone()
            if not r:
                return None
            return {
                "id": r[0], "description": r[1],
                "acceptance_criteria": r[2] or "",
                "tool": r[3],
                "params": json.loads(r[4]) if r[4] else {},
                "status": r[5], "result": r[6],
                "artifacts": json.loads(r[7]) if r[7] else [],
            }

    def set_node_status(self, plan_id: int, node_id: str, status: str,
                        result: str = None, artifacts: List[str] = None):
        sets, vals = ["status = ?", "updated_at = ?"], [status, _now()]
        if result is not None:
            sets.append("result = ?"); vals.append(result)
        if artifacts is not None:
            sets.append("artifacts = ?")
            vals.append(json.dumps(artifacts, ensure_ascii=False))
        vals += [plan_id, node_id]
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                f"UPDATE dag_nodes SET {', '.join(sets)} WHERE plan_id = ? AND node_id = ?",
                vals,
            )

    # ---------------- edges ----------------
    def add_edge(self, plan_id: int, from_id: str, to_id: str, soft: bool = False):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO dag_edges (plan_id, from_id, to_id, soft) VALUES (?, ?, ?, ?)",
                (plan_id, from_id, to_id, 1 if soft else 0),
            )

    def get_edges(self, plan_id: int) -> List[Dict]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT from_id, to_id, soft FROM dag_edges WHERE plan_id = ?", (plan_id,)
            ).fetchall()
        return [{"from": r[0], "to": r[1], "soft": bool(r[2])} for r in rows]

    def remove_edge(self, plan_id: int, from_id: str, to_id: str) -> int:
        """删除一条边（局部重规划把未执行后继的入边"重挂"到替代节点时用）。"""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "DELETE FROM dag_edges WHERE plan_id = ? AND from_id = ? AND to_id = ?",
                (plan_id, from_id, to_id),
            )
            return cur.rowcount

    # ---------------- checkpoint / snapshot ----------------
    def save_checkpoint(self, plan_id: int):
        """把当前节点状态快照存入 plan.checkpoint（轻量 checkpoint，按节点提交时调用）。"""
        nodes = self.get_nodes(plan_id)
        snap = {"nodes": {n["id"]: n["status"] for n in nodes}}
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE dag_plans SET checkpoint = ?, updated_at = ? WHERE id = ?",
                (json.dumps(snap, ensure_ascii=False), _now(), plan_id),
            )

    def recover_stale_running(self, plan_id: int, timeout_seconds: int = 300) -> int:
        """重启/中断后，清理"running 悬空"节点为 failed。返回清理数。"""
        from datetime import datetime as _dt
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT node_id, updated_at FROM dag_nodes WHERE plan_id = ? AND status = 'running'",
                (plan_id,),
            ).fetchall()
            stale = []
            for nid, ts in rows:
                try:
                    last = _dt.fromisoformat(ts.replace(" ", "T"))
                    if (_dt.now() - last).total_seconds() > timeout_seconds:
                        stale.append(nid)
                except Exception:
                    stale.append(nid)
            for nid in stale:
                conn.execute(
                    "UPDATE dag_nodes SET status = 'failed', result = 'stale running 超时清理', updated_at = ? "
                    "WHERE plan_id = ? AND node_id = ?",
                    (_now(), plan_id, nid),
                )
            return len(stale)

    def _clear_all(self):
        """（测试用）清空 DAG 表。"""
        with sqlite3.connect(self.db_path) as conn:
            for t in ("dag_edges", "dag_nodes", "dag_plans"):
                conn.execute(f"DELETE FROM {t}")
