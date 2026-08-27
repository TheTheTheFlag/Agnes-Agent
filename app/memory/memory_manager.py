import sqlite3
import json
from datetime import datetime
from typing import Optional, Dict, List, Any
from langchain_core.messages import SystemMessage, HumanMessage

class MemoryManager:
    """精简版记忆管理器：存储用户画像、偏好、任务计划、智能摘要，以及消息记录（用于分析）"""

    def __init__(self, db_path: str = "memory.db", thread_id: str = None):
        self.db_path = db_path
        self.thread_id = thread_id
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS user_profile (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS user_preferences (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                -- 任务计划主表
                CREATE TABLE IF NOT EXISTS task_plans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    thread_id TEXT NOT NULL,
                    goal TEXT NOT NULL,
                    status TEXT NOT NULL,   -- planning, executing, completed, deleted
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    deleted_at TIMESTAMP NULL
                );
                CREATE INDEX IF NOT EXISTS idx_task_plans_thread ON task_plans(thread_id);

                -- 子任务表（增加 artifacts 字段）
                CREATE TABLE IF NOT EXISTS subtasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    thread_id TEXT NOT NULL,
                    task_plan_id INTEGER NOT NULL,
                    subtask_id TEXT NOT NULL,
                    description TEXT NOT NULL,
                    dependencies TEXT,
                    status TEXT DEFAULT 'pending',
                    result TEXT,
                    artifacts TEXT,  -- 存储产出文件清单（JSON 数组）
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (task_plan_id) REFERENCES task_plans(id) ON DELETE CASCADE,
                    UNIQUE(thread_id, task_plan_id, subtask_id)
                );
                CREATE INDEX IF NOT EXISTS idx_subtasks_task_plan ON subtasks(task_plan_id);

                CREATE TABLE IF NOT EXISTS task_summaries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    thread_id TEXT NOT NULL,
                    summary_text TEXT NOT NULL,
                    start_time TIMESTAMP,
                    end_time TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_task_summaries_thread_id ON task_summaries(thread_id);
                CREATE INDEX IF NOT EXISTS idx_task_summaries_created_at ON task_summaries(created_at);

                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    thread_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT,
                    tool_calls TEXT,
                    tool_call_id TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_messages_thread_id ON messages(thread_id);
                CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp);

                -- ====== L4 程序化记忆：系统命令历史 ======
                CREATE TABLE IF NOT EXISTS command_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    thread_id TEXT NOT NULL,
                    command TEXT NOT NULL,
                    exit_code INTEGER,
                    stdout_preview TEXT,
                    stderr_preview TEXT,
                    duration_ms INTEGER,
                    success INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_command_history_thread ON command_history(thread_id);
                CREATE INDEX IF NOT EXISTS idx_command_history_created ON command_history(created_at);

                -- ====== L5 语义记忆：外部知识缓存（tavily / 文档读取） ======
                CREATE TABLE IF NOT EXISTS semantic_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,           -- 'tavily' | 'file_read' | 'doc' | ...
                    query TEXT NOT NULL,            -- 检索关键词
                    content TEXT NOT NULL,          -- 缓存的结果（截断到 N 字符）
                    hit_count INTEGER DEFAULT 0,
                    last_accessed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP NULL       -- 过期时间
                );
                CREATE INDEX IF NOT EXISTS idx_semantic_cache_query ON semantic_cache(query);
                CREATE INDEX IF NOT EXISTS idx_semantic_cache_expires ON semantic_cache(expires_at);
            """)

    # -------------------- 用户信息 --------------------
    def get_profile(self) -> Dict[str, Any]:
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute("SELECT key, value FROM user_profile")
            return {row[0]: json.loads(row[1]) for row in cur.fetchall()}

    def set_profile(self, key: str, value: Any):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO user_profile (key, value, updated_at) VALUES (?, ?, ?)",
                (key, json.dumps(value, ensure_ascii=False), datetime.now())
            )

    # -------------------- 用户偏好 --------------------
    def get_preferences(self) -> Dict[str, Any]:
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute("SELECT key, value FROM user_preferences")
            return {row[0]: json.loads(row[1]) for row in cur.fetchall()}

    def set_preference(self, key: str, value: Any):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO user_preferences (key, value, updated_at) VALUES (?, ?, ?)",
                (key, json.dumps(value, ensure_ascii=False), datetime.now())
            )

    # -------------------- 消息记录 --------------------
    def add_message(self, thread_id: str, role: str, content: str = None,
                    tool_calls: List[Dict] = None, tool_call_id: str = None):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO messages (thread_id, role, content, tool_calls, tool_call_id, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (thread_id, role, content,
                 json.dumps(tool_calls, ensure_ascii=False) if tool_calls else None,
                 tool_call_id, datetime.now())
            )

    def get_thread_messages(self, thread_id: str, limit: int = 100) -> List[Dict]:
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                """SELECT role, content, tool_calls, tool_call_id, timestamp
                   FROM messages WHERE thread_id = ?
                   ORDER BY timestamp ASC LIMIT ?""",
                (thread_id, limit)
            )
            return [{
                "role": row[0],
                "content": row[1],
                "tool_calls": json.loads(row[2]) if row[2] else None,
                "tool_call_id": row[3],
                "timestamp": row[4]
            } for row in cur.fetchall()]

    # -------------------- 任务计划管理（软删除） --------------------
    def create_task_plan(self, thread_id: str, goal: str) -> int:
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "INSERT INTO task_plans (thread_id, goal, status) VALUES (?, ?, ?)",
                (thread_id, goal, "planning")
            )
            return cur.lastrowid

    def update_task_plan_status(self, task_plan_id: int, status: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE task_plans SET status = ?, updated_at = ? WHERE id = ?",
                (status, datetime.now(), task_plan_id)
            )

    def complete_task_plan(self, task_plan_id: int):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE task_plans SET status = 'completed', updated_at = ? WHERE id = ?",
                (datetime.now(), task_plan_id)
            )

    def delete_task_plan(self, thread_id: str, task_plan_id: int = None):
        with sqlite3.connect(self.db_path) as conn:
            if task_plan_id:
                conn.execute(
                    "UPDATE task_plans SET status = 'deleted', deleted_at = ?, updated_at = ? WHERE id = ?",
                    (datetime.now(), datetime.now(), task_plan_id)
                )
            else:
                conn.execute(
                    "UPDATE task_plans SET status = 'deleted', deleted_at = ?, updated_at = ? WHERE thread_id = ? AND status != 'deleted'",
                    (datetime.now(), datetime.now(), thread_id)
                )

    # -------------------- 子任务管理（含 artifacts） --------------------
    def add_subtask(self, task_plan_id: int, thread_id: str, subtask_id: str,
                    description: str, dependencies: List[str], artifacts: str = "[]"):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO subtasks (thread_id, task_plan_id, subtask_id, description, dependencies, status, artifacts)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (thread_id, task_plan_id, subtask_id, description,
                 json.dumps(dependencies), "pending", artifacts)
            )

    def get_subtasks(self, task_plan_id: int) -> List[Dict]:
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "SELECT subtask_id, description, dependencies, status, result, artifacts FROM subtasks WHERE task_plan_id = ? ORDER BY id",
                (task_plan_id,)
            )
            rows = cur.fetchall()
            subtasks = []
            for row in rows:
                deps = row[2]
                if deps:
                    try:
                        deps = json.loads(deps)
                    except Exception:
                        deps = []
                else:
                    deps = []
                if not isinstance(deps, list):
                    deps = []
                artifacts_raw = row[5]
                if artifacts_raw:
                    try:
                        artifacts = json.loads(artifacts_raw)
                    except Exception:
                        artifacts = []
                else:
                    artifacts = []
                subtasks.append({
                    "id": row[0],
                    "description": row[1],
                    "dependencies": deps,
                    "status": row[3],
                    "result": row[4],
                    "artifacts": artifacts
                })
            return subtasks

    def update_subtask_status(self, task_plan_id: int, subtask_id: str, status: str,
                              result: str = None, artifacts: List[str] = None):
        with sqlite3.connect(self.db_path) as conn:
            if result is not None and artifacts is not None:
                conn.execute(
                    "UPDATE subtasks SET status = ?, result = ?, artifacts = ?, updated_at = ? WHERE task_plan_id = ? AND subtask_id = ?",
                    (status, result, json.dumps(artifacts, ensure_ascii=False),
                     datetime.now(), task_plan_id, subtask_id)
                )
            elif result is not None:
                conn.execute(
                    "UPDATE subtasks SET status = ?, result = ?, updated_at = ? WHERE task_plan_id = ? AND subtask_id = ?",
                    (status, result, datetime.now(), task_plan_id, subtask_id)
                )
            elif artifacts is not None:
                conn.execute(
                    "UPDATE subtasks SET status = ?, artifacts = ?, updated_at = ? WHERE task_plan_id = ? AND subtask_id = ?",
                    (status, json.dumps(artifacts, ensure_ascii=False),
                     datetime.now(), task_plan_id, subtask_id)
                )
            else:
                conn.execute(
                    "UPDATE subtasks SET status = ?, updated_at = ? WHERE task_plan_id = ? AND subtask_id = ?",
                    (status, datetime.now(), task_plan_id, subtask_id)
                )

    def get_task_plan_by_thread(self, thread_id: str, status_filter: str = None) -> Optional[Dict]:
        with sqlite3.connect(self.db_path) as conn:
            if status_filter:
                cur = conn.execute(
                    "SELECT id, goal, status FROM task_plans WHERE thread_id = ? AND status = ? ORDER BY created_at DESC LIMIT 1",
                    (thread_id, status_filter)
                )
            else:
                cur = conn.execute(
                    "SELECT id, goal, status FROM task_plans WHERE thread_id = ? AND status IN ('planning', 'executing') ORDER BY created_at DESC LIMIT 1",
                    (thread_id,)
                )
            row = cur.fetchone()
            if row:
                return {"id": row[0], "goal": row[1], "status": row[2]}
            return None

    def get_latest_task_plan(self, thread_id: str) -> Optional[Dict]:
        plan = self.get_task_plan_by_thread(thread_id)
        if not plan:
            return None
        return {
            "goal": plan["goal"],
            "plan_json": "{}",
            "status": plan["status"],
            "updated_at": datetime.now()
        }

    # -------------------- 智能摘要相关 --------------------
    def generate_summary_from_messages(self, messages: List, llm) -> str:
        msg_texts = []
        for msg in messages:
            if hasattr(msg, 'content') and msg.content:
                role = getattr(msg, 'type', 'unknown')
                content = str(msg.content)[:500]
                msg_texts.append(f"[{role}]: {content}")
        conversation_text = "\n".join(msg_texts[-20:])

        prompt = SystemMessage(
            content=(
                "请根据以下对话内容，生成一份结构化的摘要，必须包含以下三个部分：\n\n"
                "1. **用户目标**：用户想要达成什么目的？\n"
                "2. **已完成步骤**：截至目前已经完成了哪些操作或讨论？\n"
                "3. **待办事项**：还有什么需要继续完成的任务？\n\n"
                "如果某部分没有内容，请写\"无\"。\n"
                "请用中文简洁回答，每条不超过 100 字。\n\n"
                "对话内容：\n"
            )
        )
        human_msg = HumanMessage(content=conversation_text)
        response = llm.invoke([prompt, human_msg])
        return response.content

    def save_summary(self, thread_id: str, summary_text: str, start_time: datetime = None, end_time: datetime = None):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO task_summaries (thread_id, summary_text, start_time, end_time, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (thread_id, summary_text, start_time, end_time, datetime.now())
            )

    def get_recent_summary(self, thread_id: str) -> Optional[str]:
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                """SELECT summary_text FROM task_summaries
                   WHERE thread_id = ?
                   ORDER BY created_at DESC LIMIT 1""",
                (thread_id,)
            )
            row = cur.fetchone()
            return row[0] if row else None

    def update_summary(self, thread_id: str, old_summary: str, new_messages: List, llm) -> str:
        recent_text = []
        for msg in new_messages[-5:]:
            if hasattr(msg, 'content') and msg.content:
                role = getattr(msg, 'type', 'unknown')
                content = str(msg.content)[:300]
                recent_text.append(f"[{role}]: {content}")

        recent_conversation = "\n".join(recent_text)

        user_content = (
            f"之前的摘要：{old_summary}\n\n"
            f"最新对话：\n{recent_conversation}\n\n"
            "请根据以上信息，生成一份更新的摘要。格式要求：\n"
            "1. **用户目标**：用户当前想要达成什么目的？\n"
            "2. **已完成步骤**：截至目前已经完成了哪些操作或讨论？\n"
            "3. **待办事项**：还有什么需要继续完成的任务？\n"
            "请输出更新后的完整摘要（三条都保留）。"
        )
        messages = [
            SystemMessage(content="你是一个摘要生成助手，根据对话内容生成结构化摘要。"),
            HumanMessage(content=user_content)
        ]
        response = llm.invoke(messages)
        return response.content

    # -------------------- LLM 重要性评估 --------------------
    def importance_filter(self, messages: List, llm) -> Dict[str, List]:
        if not messages:
            return {'keep': [], 'summarize': [], 'discard': []}

        msg_summaries = []
        for idx, msg in enumerate(messages):
            if hasattr(msg, 'content') and msg.content:
                role = getattr(msg, 'type', 'unknown')
                content = str(msg.content)[:300]
                msg_summaries.append(f"{idx}. [{role}]: {content}")
            else:
                msg_summaries.append(f"{idx}. [unknown]: (无内容)")

        text = "\n".join(msg_summaries)
        prompt = SystemMessage(
            content=(
                "你是一个对话重要性评估专家。给定以下对话消息列表（每条有编号和角色、内容），"
                "请对每条消息分类：\n"
                "- 如果消息包含用户明确指令、关键信息、工具调用结果或重要结论，标记为 'keep'。\n"
                "- 如果消息有一定信息但非关键，可以简要总结，标记为 'summarize'。\n"
                "- 如果消息是简单问候、确认、无关闲聊，标记为 'discard'。\n"
                "请以 JSON 数组格式输出，每个元素为 {\"index\": 编号, \"label\": \"keep/summarize/discard\"}，"
                "只输出 JSON 数组，不要其他内容。\n\n"
                f"消息列表：\n{text}"
            )
        )
        response = llm.invoke([prompt])
        try:
            data = json.loads(response.content)
            keep_indices = [item['index'] for item in data if item.get('label') == 'keep']
            summarize_indices = [item['index'] for item in data if item.get('label') == 'summarize']
            discard_indices = [item['index'] for item in data if item.get('label') == 'discard']
            keep = [messages[i] for i in keep_indices if i < len(messages)]
            summarize = [messages[i] for i in summarize_indices if i < len(messages)]
            discard = [messages[i] for i in discard_indices if i < len(messages)]
            print(f"[重要性分类] 保留 {len(keep)} 条，摘要 {len(summarize)} 条，丢弃 {len(discard)} 条")
            return {'keep': keep, 'summarize': summarize, 'discard': discard}
        except Exception as e:
            print(f"[重要性分类] 解析失败: {e}，保留所有消息")
            return {'keep': messages, 'summarize': [], 'discard': []}

    # -------------------- 构建上下文 --------------------
    def build_context(self) -> Dict:
        return {
            "profile": self.get_profile(),
            "preferences": self.get_preferences(),
            "recent_summary": self.get_recent_summary(self.thread_id),
        }    # ============================================================
    # 分层记忆 API（L1-L5）
    # 业界共识（参考 MemGPT / LangMem / Letta）的 5 层：
    #   L0 Working   → LangGraph state（messages 列表本身）
    #   L1 Thread    → task_summaries（已有，本节只新增读取聚合）
    #   L2 Profile   → user_profile + user_preferences（已有）
    #   L3 Episodic  → task_plans + subtasks + messages（已有）
    #   L4 Procedural→ command_history（新增）
    #   L5 Semantic  → semantic_cache（新增）
    # ============================================================

    # ----- L4 命令历史 -----
    def add_command_history(self, thread_id: str, command: str, success: bool,
                            exit_code: int = None, stdout_preview: str = None,
                            stderr_preview: str = None, duration_ms: int = None):
        """记录一次系统命令执行；on_tool_after 钩子调用。"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO command_history
                   (thread_id, command, exit_code, stdout_preview, stderr_preview, duration_ms, success)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (thread_id, command, exit_code,
                 (stdout_preview or "")[:2000],
                 (stderr_preview or "")[:2000],
                 duration_ms, 1 if success else 0)
            )

    def get_command_history(self, thread_id: str = None, limit: int = 20,
                            pattern: str = None) -> List[Dict]:
        """读取最近 N 条命令。thread_id=None 时跨 thread 查（按所有用户）。"""
        with sqlite3.connect(self.db_path) as conn:
            if thread_id:
                sql = """SELECT command, exit_code, success, duration_ms, created_at
                         FROM command_history WHERE thread_id = ?
                         ORDER BY created_at DESC LIMIT ?"""
                params = (thread_id, limit)
            else:
                sql = """SELECT command, exit_code, success, duration_ms, created_at, thread_id
                         FROM command_history ORDER BY created_at DESC LIMIT ?"""
                params = (limit,)
            if pattern:
                sql = sql.replace("WHERE thread_id = ?",
                                  "WHERE thread_id = ? AND command LIKE ?")
                sql = sql.replace("ORDER BY created_at DESC LIMIT ?",
                                  "AND command LIKE ? ORDER BY created_at DESC LIMIT ?")
                if thread_id:
                    params = (thread_id, f"%{pattern}%", limit)
                else:
                    params = (f"%{pattern}%", limit)
            cur = conn.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    # ----- L5 语义缓存 -----
    def get_cached_knowledge(self, query: str, ttl_seconds: int = 86400) -> Optional[Dict]:
        """按 query 查找未过期的缓存命中。命中时增加 hit_count 并刷新 last_accessed_at。"""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                """SELECT id, source, content, hit_count FROM semantic_cache
                   WHERE query = ? AND (expires_at IS NULL OR expires_at > ?)
                   ORDER BY last_accessed_at DESC LIMIT 1""",
                (query, datetime.now())
            )
            row = cur.fetchone()
            if not row:
                return None
            conn.execute(
                """UPDATE semantic_cache SET hit_count = hit_count + 1,
                                            last_accessed_at = ? WHERE id = ?""",
                (datetime.now(), row[0])
            )
            return {"source": row[1], "content": row[2], "hit_count": row[3] + 1}

    def cache_knowledge(self, source: str, query: str, content: str,
                        ttl_seconds: int = 86400, max_len: int = 8000):
        """写入语义缓存。相同 query 会覆盖。截断到 max_len 字符避免撑爆。"""
        with sqlite3.connect(self.db_path) as conn:
            expires = None
            if ttl_seconds:
                from datetime import timedelta
                expires = datetime.now() + timedelta(seconds=ttl_seconds)
            conn.execute(
                """INSERT OR REPLACE INTO semantic_cache
                   (source, query, content, expires_at)
                   VALUES (?, ?, ?, ?)""",
                (source, query, (content or "")[:max_len], expires)
            )

    def search_knowledge(self, keyword: str, limit: int = 5) -> List[Dict]:
        """在 L5 缓存中按关键词模糊搜索（用于 read 工具）。"""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                """SELECT source, query, content, hit_count, last_accessed_at
                   FROM semantic_cache
                   WHERE (query LIKE ? OR content LIKE ?) AND (expires_at IS NULL OR expires_at > ?)
                   ORDER BY hit_count DESC, last_accessed_at DESC LIMIT ?""",
                (f"%{keyword}%", f"%{keyword}%", datetime.now(), limit)
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def cleanup_expired_cache(self) -> int:
        """清理过期缓存；返回删除行数。"""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "DELETE FROM semantic_cache WHERE expires_at IS NOT NULL AND expires_at <= ?",
                (datetime.now(),)
            )
            return cur.rowcount

    # ----- L3 任务记忆读取（聚合） -----
    def get_recent_tasks(self, limit: int = 5, status_filter: str = None,
                         keyword: str = None) -> List[Dict]:
        """读取最近的任务计划（按完成时间倒序）。"""
        with sqlite3.connect(self.db_path) as conn:
            sql = """SELECT id, thread_id, goal, status, created_at, updated_at
                     FROM task_plans WHERE status != 'deleted' """
            params = []
            if status_filter:
                sql += "AND status = ? "
                params.append(status_filter)
            if keyword:
                sql += "AND goal LIKE ? "
                params.append(f"%{keyword}%")
            sql += "ORDER BY updated_at DESC LIMIT ?"
            params.append(limit)
            cur = conn.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def search_episodes(self, keyword: str, limit: int = 10) -> List[Dict]:
        """在历史任务 + 消息里搜关键词。"""
        with sqlite3.connect(self.db_path) as conn:
            results = []
            # 搜任务目标
            cur = conn.execute(
                """SELECT 'task' as kind, id, goal as text, created_at
                   FROM task_plans WHERE goal LIKE ? AND status != 'deleted'
                   ORDER BY created_at DESC LIMIT ?""",
                (f"%{keyword}%", limit)
            )
            results.extend([{"kind": "task", "id": r[1], "text": r[2], "at": r[3]} for r in cur.fetchall()])
            # 搜消息内容
            cur = conn.execute(
                """SELECT 'message' as kind, id, content, timestamp
                   FROM messages WHERE content LIKE ?
                   ORDER BY timestamp DESC LIMIT ?""",
                (f"%{keyword}%", limit)
            )
            results.extend([{"kind": "msg", "id": r[1], "text": (r[2] or "")[:300], "at": r[3]} for r in cur.fetchall()])
            return results[:limit]

    # ----- 分层注入 prompt 的聚合方法 -----
    def build_memory_injection(self, thread_id: str, layers: List[str] = None) -> Dict[str, str]:
        """
        为 chatbot 准备分层的"记忆注入"片段。
        默认只注入 L2 (画像/偏好) + L3 (近期任务极简摘要) —— context 稀缺，只常驻高价值信息；
        L4 (命令历史) / L5 (知识缓存) 完全靠工具查询（get_command_history / search_my_memory）。
        返回 dict，每条 key 是层名，value 是要追加到 system prompt 的 markdown 段落。
        """
        if layers is None:
            layers = ["L2", "L3"]
        out = {}
        if "L2" in layers:
            profile = self.get_profile()
            prefs = self.get_preferences()
            parts = []
            if profile:
                parts.append("【用户画像】\n" + "\n".join(f"- {k}: {v}" for k, v in profile.items()))
            if prefs:
                parts.append("【用户偏好】\n" + "\n".join(f"- {k}: {v}" for k, v in prefs.items()))
            if parts:
                out["L2"] = "\n".join(parts)
        if "L3" in layers:
            recent_tasks = self.get_recent_tasks(limit=2, status_filter="completed")
            if recent_tasks:
                lines = ["【近期任务摘要（最近 2 条）】"]
                for t in recent_tasks:
                    when = (t.get("updated_at") or "")[:10]
                    goal = str(t.get("goal") or "")[:40]
                    lines.append(f"- {when}: {goal} [id={t['id']}]")
                out["L3"] = "\n".join(lines)
        return out
