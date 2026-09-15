import sqlite3
import json
from datetime import datetime
from typing import Optional, Dict, List, Any
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_core.callbacks import CallbackManager


def _silent_invoke(llm, messages):
    """内部 LLM 调用（摘要生成/重要性评估等，非对话主回复）。

    用空的 CallbackManager 隔离回调：这些调用发生在 chatbot 节点内部，若不加隔离，
    其 token 会经 langchain 回调冒泡到 langgraph 的 messages 流（stream_mode="messages"），
    被 chat.py 的 SSE 推给前端，污染聊天框（表现为回复末尾多出 "**用户" 等摘要前缀）。
    隔离后 token 只发给空管理器，前端只收到主回复的 token。
    """
    return llm.invoke(messages, config={"callbacks": CallbackManager([])})

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

                CREATE TABLE IF NOT EXISTS history_summaries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    thread_id TEXT NOT NULL,
                    summary_text TEXT NOT NULL,
                    importance_score REAL DEFAULT 5.0,  -- 1.0 ~ 10.0, LLM 评分
                    access_count INTEGER DEFAULT 0,   -- 检索命中次数
                    start_time TIMESTAMP,
                    end_time TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_history_summaries_thread_id ON history_summaries(thread_id);
                CREATE INDEX IF NOT EXISTS idx_history_summaries_created_at ON history_summaries(created_at);
                CREATE INDEX IF NOT EXISTS idx_history_summaries_importance ON history_summaries(importance_score);

                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    thread_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT,
                    tool_calls TEXT,
                    tool_call_id TEXT,
                    kind TEXT NOT NULL DEFAULT 'chat',
                    meta TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_messages_thread_id ON messages(thread_id);
                CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp);

                -- ====== 长期记忆：固化的事实/偏好（成熟记忆） ======
                -- 向量直接存本表（embedding/embedding_dim），不再复制到 memory_chunks 冗余表：
                -- 检索、删除、衰减同表一致，避免"幽灵记忆"（已被遗忘的事实仍被语义检索命中）。
                CREATE TABLE IF NOT EXISTS memory_facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content TEXT NOT NULL UNIQUE,
                    category TEXT DEFAULT 'fact',   -- fact | preference | identity | relation | project
                    importance REAL DEFAULT 5.0,    -- 1.0 ~ 10.0
                    source TEXT DEFAULT 'extraction', -- extraction | explicit
                    thread_id TEXT DEFAULT '',
                    access_count INTEGER DEFAULT 0,
                    last_accessed_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    embedding BLOB,                 -- np.float32 bytes（embedding_dim * 4）
                    embedding_dim INTEGER DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_memory_facts_importance ON memory_facts(importance);
            """)

            # ---- messages 表结构迁移：旧库补 kind/meta 列（事件气泡持久化用）----
            # 新库由上方 CREATE TABLE 直接带列；旧库（CREATE TABLE IF NOT EXISTS 不生效）
            # 用 PRAGMA 检测缺列后 ALTER 补上，避免历史 DB 升级后列不存在。
            try:
                _cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)").fetchall()}
                if "kind" not in _cols:
                    conn.execute("ALTER TABLE messages ADD COLUMN kind TEXT NOT NULL DEFAULT 'chat'")
                if "meta" not in _cols:
                    conn.execute("ALTER TABLE messages ADD COLUMN meta TEXT")
                try:
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_kind ON messages(thread_id, kind)")
                except Exception:
                    pass
            except Exception:
                pass

            # ---- history_summaries 表结构迁移：旧库补 importance_score/access_count 列（history_summary 评分用）----
            try:
                _ts_cols = {r[1] for r in conn.execute("PRAGMA table_info(history_summaries)").fetchall()}
                if "importance_score" not in _ts_cols:
                    conn.execute("ALTER TABLE history_summaries ADD COLUMN importance_score REAL DEFAULT 5.0")
                if "access_count" not in _ts_cols:
                    conn.execute("ALTER TABLE history_summaries ADD COLUMN access_count INTEGER DEFAULT 0")
                try:
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_history_summaries_importance ON history_summaries(importance_score)")
                except Exception:
                    pass
            except Exception:
                pass

            # ---- memory_facts 结构迁移：旧库补 embedding/embedding_dim 列 ----
            try:
                _mf_cols = {r[1] for r in conn.execute("PRAGMA table_info(memory_facts)").fetchall()}
                if "embedding" not in _mf_cols:
                    conn.execute("ALTER TABLE memory_facts ADD COLUMN embedding BLOB")
                if "embedding_dim" not in _mf_cols:
                    conn.execute("ALTER TABLE memory_facts ADD COLUMN embedding_dim INTEGER DEFAULT 0")
            except Exception:
                pass

            # ---- memory_chunks 冗余索引表合并回权威表后删除 ----
            # 旧架构把 fact/task 的文本+向量复制进 memory_chunks 做语义检索，导致
            # 删除/衰减 memory_facts 时不同步清块 → 幽灵记忆。现在向量直接存权威表，
            # 这里把历史 memory_chunks 数据回填后 DROP（幂等：表不存在则跳过）。
            try:
                _has_chunks = any(
                    r[0].lower() == "memory_chunks"
                    for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                )
                if _has_chunks:
                    # fact 块 → memory_facts.embedding（按 ref_id 对应）
                    conn.execute(
                        """UPDATE memory_facts
                           SET embedding = (
                               SELECT mc.embedding FROM memory_chunks mc
                               WHERE mc.kind = 'fact' AND mc.ref_id = memory_facts.id
                               ORDER BY mc.id DESC LIMIT 1
                           ),
                           embedding_dim = (
                               SELECT mc.embedding_dim FROM memory_chunks mc
                               WHERE mc.kind = 'fact' AND mc.ref_id = memory_facts.id
                               ORDER BY mc.id DESC LIMIT 1
                           )
                           WHERE EXISTS (
                               SELECT 1 FROM memory_chunks mc
                               WHERE mc.kind = 'fact' AND mc.ref_id = memory_facts.id
                               AND mc.embedding IS NOT NULL
                           )"""
                    )
                    # task 块 → dag_plans.embedding（按 ref_id 对应）
                    try:
                        _dp_cols = {r[1] for r in conn.execute("PRAGMA table_info(dag_plans)").fetchall()}
                        if "embedding" not in _dp_cols:
                            conn.execute("ALTER TABLE dag_plans ADD COLUMN embedding BLOB")
                        if "embedding_dim" not in _dp_cols:
                            conn.execute("ALTER TABLE dag_plans ADD COLUMN embedding_dim INTEGER DEFAULT 0")
                        conn.execute(
                            """UPDATE dag_plans
                               SET embedding = (
                                   SELECT mc.embedding FROM memory_chunks mc
                                   WHERE mc.kind = 'task' AND mc.ref_id = dag_plans.id
                                   ORDER BY mc.id DESC LIMIT 1
                               ),
                               embedding_dim = (
                                   SELECT mc.embedding_dim FROM memory_chunks mc
                                   WHERE mc.kind = 'task' AND mc.ref_id = dag_plans.id
                                   ORDER BY mc.id DESC LIMIT 1
                               )
                               WHERE EXISTS (
                                   SELECT 1 FROM memory_chunks mc
                                   WHERE mc.kind = 'task' AND mc.ref_id = dag_plans.id
                                   AND mc.embedding IS NOT NULL
                               )"""
                        )
                    except Exception:
                        pass
                    conn.execute("DROP TABLE memory_chunks")
            except Exception:
                pass

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
        # 幂等去重：LangGraph 的 interrupt/resume 会重放节点（审批场景），
        # 导致 chatbot 节点的 add_message 重复执行。同会话最近一条同 role+content
        # 的消息在短时间窗口内视为同一次，跳过重复插入。
        try:
            with sqlite3.connect(self.db_path) as conn:
                last = conn.execute(
                    "SELECT content, timestamp FROM messages WHERE thread_id=? AND role=? ORDER BY id DESC LIMIT 1",
                    (thread_id, role)
                ).fetchone()
                if last and last[0] == content:
                    try:
                        from datetime import datetime as _dt
                        last_ts = _dt.fromisoformat(last[1])
                        if (datetime.now() - last_ts).total_seconds() < 60:
                            return  # 节点重放，跳过
                    except Exception:
                        pass
                conn.execute(
                    """INSERT INTO messages (thread_id, role, content, tool_calls, tool_call_id, timestamp)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (thread_id, role, content,
                     json.dumps(tool_calls, ensure_ascii=False) if tool_calls else None,
                     tool_call_id, datetime.now())
                )
        except Exception:
            pass

    def add_event(self, thread_id: str, kind: str, content: str = None,
                  meta: Dict[str, Any] = None):
        """持久化一条页面"事件气泡"（节点开始/结束、模型调用、工具、思考、审批卡）。

        与 add_message 的对话记录区分：role='event'，kind 表示事件类型
        （node_start/node_end/llm_call/tool_call/thought/approval），meta 存结构化详情，
        按真实发生逐条落库（不做对话的 60s 幂等去重——事件天然逐次不同）。
        仅对同 thread+kind+content 在 2 秒内重复（listener 桥与 updates 兜底双写、
        interrupt/resume 重放）做轻量去重，避免同一事件落两条。
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                if content:
                    _dup = conn.execute(
                        """SELECT timestamp FROM messages
                           WHERE thread_id=? AND role='event' AND kind=? AND content=?
                           ORDER BY id DESC LIMIT 1""",
                        (thread_id, kind, content)
                    ).fetchone()
                    if _dup:
                        try:
                            from datetime import datetime as _dt
                            _ts = _dt.fromisoformat(_dup[0])
                            if (datetime.now() - _ts).total_seconds() < 2:
                                return
                        except Exception:
                            pass
                conn.execute(
                    """INSERT INTO messages (thread_id, role, content, kind, meta, timestamp)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (thread_id, "event", content, kind,
                     json.dumps(meta, ensure_ascii=False, default=str) if meta is not None else None,
                     datetime.now())
                )
        except Exception:
            pass

    def set_last_approval_decision(self, thread_id: str, allow: bool, mode: str = "") -> bool:
        """把最近一条未决策的审批提问（kind='approval'）回填用户选择（allow/mode）。

        审批卡提问在 interrupt 时落库（meta.allow=None）；用户在 resume 请求里给出
        allow/mode 后调用本方法，把决定写回同一条记录，重启回放时审批卡显示完整
        （问题 + 最终决定）。返回是否命中。
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                row = conn.execute(
                    """SELECT id, meta FROM messages
                       WHERE thread_id=? AND role='event' AND kind='approval'
                       ORDER BY id DESC LIMIT 1""",
                    (thread_id,)
                ).fetchone()
                if not row:
                    return False
                meta = {}
                if row[1]:
                    try:
                        meta = json.loads(row[1])
                    except Exception:
                        meta = {}
                meta["allow"] = bool(allow)
                meta["mode"] = mode or meta.get("mode", "")
                conn.execute(
                    "UPDATE messages SET meta=? WHERE id=?",
                    (json.dumps(meta, ensure_ascii=False, default=str), row[0])
                )
                return True
        except Exception:
            return False

    def get_thread_messages(self, thread_id: str, limit: int = 100) -> List[Dict]:
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                """SELECT role, content, tool_calls, tool_call_id, kind, meta, timestamp
                   FROM messages WHERE thread_id = ?
                   ORDER BY timestamp ASC LIMIT ?""",
                (thread_id, limit)
            )
            return [{
                "role": row[0],
                "content": row[1],
                "tool_calls": json.loads(row[2]) if row[2] else None,
                "tool_call_id": row[3],
                "kind": row[4] or "chat",
                "meta": json.loads(row[5]) if row[5] else None,
                "timestamp": row[6]
            } for row in cur.fetchall()]

    # -------------------- 消息摘要生成（供 update_summary / history_summaries 使用） --------------------
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
        response = _silent_invoke(llm, [prompt, human_msg])
        return response.content

    # ===== history_summary 历史对话摘要 =====
    # 触发条件：对话消息达到阈值（如 10 条）或距上次 120 秒
    # 数据源：history_summaries 表（字段语义 = history_summary）
    # 用途：build_memory_injection 注入到 system prompt，压缩历史消息避免 context 爆炸

    def save_history_summary_scored(self, thread_id: str, summary_text: str,
                                   importance_score: float, llm=None,
                                   start_time: datetime = None, end_time: datetime = None):
        """保存带重要性评分的历史摘要。
        importance_score: 1.0 ~ 10.0
        如果传了 llm，会自动调用 LLM 评估重要性。
        """
        if llm is not None:
            importance_score = self._evaluate_importance(summary_text, llm)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO history_summaries
                   (thread_id, summary_text, importance_score, start_time, end_time, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (thread_id, summary_text, importance_score, start_time, end_time, datetime.now())
            )

    def evaluate_messages_importance(self, messages: List, llm,
                                    min_score: float = 7.0) -> List[Dict]:
        """对一批消息逐条打分（1-10），返回达到 min_score 阈值的高价值消息。

        设计目的：在压缩历史前先过滤低分消息（闲聊/确认/无关内容），
        只保留 ≥ min_score 分的重要消息进入 history_summary 压缩流程。
        这是 Q7 的 Importance Score 过滤策略。
        """
        from langchain_core.messages import SystemMessage, HumanMessage
        if not messages:
            return []

        # 准备消息列表文本（带索引方便 LLM 引用）
        msg_lines = []
        for idx, msg in enumerate(messages):
            role = getattr(msg, 'type', 'unknown')
            content = str(getattr(msg, 'content', '') or '')[:300]
            msg_lines.append(f"{idx}. [{role}]: {content}")
        text = "\n".join(msg_lines)

        system = SystemMessage(content=(
            "你是对话重要性评估专家。给定对话消息列表，对每条打 1-10 分：\n"
            "- 1-3: 闲聊/确认/无关（'好的'、'谢谢'、'嗯'、'再见'）\n"
            "- 4-6: 普通信息（一般对话、工具调用结果）\n"
            "- 7-10: 重要内容（用户偏好/核心背景/关键决策/重要发现）\n"
            "请以 JSON 数组输出：[{\"index\": 编号, \"score\": 数字, \"reason\": \"原因\"}]"
        ))
        human = HumanMessage(content=f"消息列表：\n{text}")

        try:
            response = _silent_invoke(llm, [system, human])
            data = json.loads(response.content)
        except Exception:
            # 解析失败时全部保留（不丢失数据）
            return [{"msg": m, "score": 5.0, "reason": "parse_failed"}
                    for m in messages]

        # 过滤高分消息
        kept = []
        for item in data:
            idx = item.get("index")
            score = float(item.get("score", 5.0))
            if idx is not None and 0 <= idx < len(messages) and score >= min_score:
                kept.append({
                    "msg": messages[idx],
                    "score": score,
                    "reason": item.get("reason", ""),
                })
        return kept

    def _evaluate_importance(self, summary_text: str, llm) -> float:
        """让 LLM 评估摘要的重要性（1-10 分）。用于保存摘要时打元数据标签。"""
        from langchain_core.messages import SystemMessage, HumanMessage
        prompt = SystemMessage(content=(
            "你是记忆重要性评估专家。给定一段对话摘要，评估其对 Agent 未来行为的重要性。"
            "评分标准：1=闲聊无关，5=普通信息，10=核心偏好/关键背景/重要决策。"
            "请只返回 1-10 之间的整数。"
        ))
        try:
            response = _silent_invoke(llm, [prompt, HumanMessage(content=summary_text)])
            score = float(response.content.strip())
            return max(1.0, min(10.0, score))
        except Exception:
            return 5.0  # 默认中等重要性

    def get_history_summaries_scored(self, limit: int = 5, thread_id: str = None,
                                     query: str = None,
                                     alpha: float = 0.5, beta: float = 0.3, gamma: float = 0.2,
                                     decay_factor: float = 0.995) -> List[Dict]:
        """按 Stanford 三因子评分排序的历史摘要：
        Score = α*Recency + β*Importance + γ*Relevance
        - Recency: 0.995 ^ hours_since_access（指数衰减）
        - Importance: importance_score / 10
        - Relevance: 简单的关键词重叠（如果没传 query 则跳过）
        """
        from datetime import datetime

        candidates = self.get_history_summaries(limit=max(limit * 3, 10), thread_id=thread_id)
        if not candidates:
            return []

        now = datetime.now()
        scored = []
        for c in candidates:
            # 1. 时效性（指数衰减）
            try:
                created = datetime.fromisoformat(c["created_at"]) if isinstance(c["created_at"], str) else c["created_at"]
                hours_elapsed = (now - created).total_seconds() / 3600
            except Exception:
                hours_elapsed = 0
            recency = decay_factor ** max(0, hours_elapsed)

            # 2. 重要性（1-10 → 0-1）
            importance = (c.get("importance_score") or 5.0) / 10.0

            # 3. 相关性（如果传了 query，做简单的关键词重叠评分）
            relevance = 0.5  # 默认中性
            if query:
                query_words = set(query.lower().split())
                summary_words = set((c.get("summary_text") or "").lower().split())
                if query_words and summary_words:
                    overlap = len(query_words & summary_words) / len(query_words | summary_words)
                    relevance = min(1.0, overlap * 2)  # 放大一点

            # 综合评分
            score = alpha * recency + beta * importance + gamma * relevance
            scored.append((score, c))

        # 按评分降序
        scored.sort(key=lambda x: x[0], reverse=True)
        return [item[1] for item in scored[:limit]]

    def get_recent_summary(self, thread_id: str) -> Optional[str]:
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                """SELECT summary_text FROM history_summaries
                   WHERE thread_id = ?
                   ORDER BY created_at DESC LIMIT 1""",
                (thread_id,)
            )
            row = cur.fetchone()
            return row[0] if row else None

    def get_history_summaries(self, limit: int = 5, thread_id: str = None) -> List[Dict]:
        """读取最近的历史对话摘要（按创建时间倒序）。
        用于 build_memory_injection 的 history_summary 注入。
        thread_id 为 None 时返回所有线程的全局摘要。
        """
        return self.get_recent_summaries(limit, thread_id)

    def get_recent_summaries(self, limit: int = 5, thread_id: str = None) -> List[Dict]:
        """读取最近的对话摘要（按创建时间倒序）。
        用于 history_summary 注入：把压缩过的历史消息摘要放进 system prompt。
        thread_id 为 None 时返回所有线程的全局摘要。
        """
        with sqlite3.connect(self.db_path) as conn:
            if thread_id:
                cur = conn.execute(
                    """SELECT id, thread_id, summary_text, importance_score, start_time, end_time, created_at
                       FROM history_summaries
                       WHERE thread_id = ?
                       ORDER BY created_at DESC LIMIT ?""",
                    (thread_id, limit)
                )
            else:
                cur = conn.execute(
                    """SELECT id, thread_id, summary_text, importance_score, start_time, end_time, created_at
                       FROM history_summaries
                       ORDER BY created_at DESC LIMIT ?""",
                    (limit,)
                )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

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
        response = _silent_invoke(llm, messages)
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
        response = _silent_invoke(llm, [prompt])
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
    # 分层记忆 API
    #   L1 Thread    → messages 表（对话消息；role='event'/kind='tool_call' 承载工具调用审计）
    #   L2 Profile   → user_profile + user_preferences
    #   L3 Episodic  → dag_plans（规划器）+ messages（任务目标+消息）
    #   L6 Graph     → LightRAG 知识图谱（取代原 L4 命令历史 / L5 语义缓存，见 docs/l6-graphrag.md）
    # 附：history_summaries 表是历史对话的压缩摘要（不归入标准层），用于 history_summary 注入中
    # ============================================================

    # ----- L1 工具调用历史（原 L4 命令历史已并入 messages 的 tool_call 事件） -----
    def get_tool_call_history(self, thread_id: str = None, limit: int = 20,
                              pattern: str = None) -> List[Dict]:
        """读取最近 N 条工具调用记录（来自 messages 表 role='event'/kind='tool_call'）。

        thread_id=None 时跨 thread 查。pattern 匹配工具名/参数摘要或结果预览。
        """

        def _brief(params) -> str:
            try:
                s = json.dumps(params, ensure_ascii=False, default=str)
            except Exception:
                s = str(params)
            return s[:200]

        with sqlite3.connect(self.db_path) as conn:
            if thread_id:
                cur = conn.execute(
                    """SELECT meta, timestamp FROM messages
                       WHERE thread_id = ? AND role = 'event' AND kind = 'tool_call'
                       ORDER BY id DESC LIMIT ?""",
                    (thread_id, limit)
                )
                rows = [(meta, ts) for (meta, ts) in cur.fetchall()]
            else:
                cur = conn.execute(
                    """SELECT thread_id, meta, timestamp FROM messages
                       WHERE role = 'event' AND kind = 'tool_call'
                       ORDER BY id DESC LIMIT ?""",
                    (limit,)
                )
                rows = [(meta, ts, tid) for (tid, meta, ts) in cur.fetchall()]
        out = []
        for row in rows:
            ts = row[1]
            tid = row[2] if len(row) > 2 else None
            try:
                meta = json.loads(row[0] or "{}")
            except Exception:
                meta = {}
            name = str(meta.get("name") or "?")
            params = meta.get("params") or {}
            result = str(meta.get("result") or "")[:500]
            text = f"{name}: {_brief(params)}" if params else name
            if pattern and pattern not in text and pattern not in result:
                continue
            item = {"command": text, "stdout_preview": result, "created_at": ts}
            if tid:
                item["thread_id"] = tid
            out.append(item)
        return out

    # ----- L3 任务记忆读取（聚合） -----
    def get_recent_tasks(self, limit: int = 5, status_filter: str = None,
                         keyword: str = None) -> List[Dict]:
        """读取最近的任务计划（按完成时间倒序，基于 dag_plans 表）。"""
        import sqlite3 as _s
        try:
            with _s.connect(self.db_path) as conn:
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
        except Exception:
            return []

    def search_episodes(self, keyword: str, limit: int = 10) -> List[Dict]:
        """在历史任务（dag_plans）+ 消息里搜关键词。"""
        with sqlite3.connect(self.db_path) as conn:
            results = []
            # 搜任务目标（新规划器：dag_plans）
            try:
                cur = conn.execute(
                    """SELECT 'task' as kind, id, goal as text, created_at
                       FROM dag_plans WHERE goal LIKE ? AND status != 'deleted'
                       ORDER BY created_at DESC LIMIT ?""",
                    (f"%{keyword}%", limit)
                )
                results.extend([{"kind": "task", "id": r[1], "text": r[2], "at": r[3]} for r in cur.fetchall()])
            except Exception:
                pass
            # 搜消息内容
            cur = conn.execute(
                """SELECT 'message' as kind, id, content, timestamp
                   FROM messages WHERE content LIKE ?
                   ORDER BY timestamp DESC LIMIT ?""",
                (f"%{keyword}%", limit)
            )
            results.extend([{"kind": "msg", "id": r[1], "text": (r[2] or "")[:300], "at": r[3]} for r in cur.fetchall()])
            return results[:limit]

    # ==================== 长期记忆（memory_facts / memory_chunks） ====================
    def add_memory_fact(self, content: str, category: str = "fact", importance: float = 5.0,
                        source: str = "extraction", thread_id: str = "") -> tuple:
        """写入/更新一条长期记忆（按 content 唯一）。返回 (id, action)，action ∈ insert|update。"""
        now = datetime.now()
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT id FROM memory_facts WHERE content = ?", (content,)
            ).fetchone()
            if row:
                conn.execute(
                    """UPDATE memory_facts SET category=?, importance=?, source=?, thread_id=?, updated_at=?
                       WHERE id=?""",
                    (category, importance, source, thread_id or "", now, row[0])
                )
                return row[0], "update"
            cur = conn.execute(
                """INSERT INTO memory_facts (content, category, importance, source, thread_id, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (content, category, importance, source, thread_id or "", now, now)
            )
            return cur.lastrowid, "insert"

    def delete_memory_fact(self, fact_id: int = None, content: str = None) -> int:
        """删除一条长期记忆（按 id 或 content）。返回删除行数。"""
        with sqlite3.connect(self.db_path) as conn:
            if fact_id:
                cur = conn.execute("DELETE FROM memory_facts WHERE id = ?", (fact_id,))
            elif content:
                cur = conn.execute("DELETE FROM memory_facts WHERE content = ?", (content,))
            else:
                return 0
            return cur.rowcount

    def get_memory_facts(self, limit: int = 50, min_importance: float = 0.0) -> List[Dict]:
        """读取长期记忆；顺带惰性衰减 importance（见 decay_memory_facts 说明）。"""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT id, content, category, importance, source, thread_id, access_count,
                          last_accessed_at, created_at, updated_at
                   FROM memory_facts ORDER BY importance DESC LIMIT ?""",
                (limit,)
            ).fetchall()
        cols = ["id", "content", "category", "importance", "source", "thread_id", "access_count",
                "last_accessed_at", "created_at", "updated_at"]
        return [dict(zip(cols, r)) for r in rows]

    def decay_memory_facts(self, min_importance: float = 2.0, rate: float = 0.995) -> dict:
        """主动遗忘：importance 随时间衰减（每过 1 天乘 rate），并补记访问升温。

        - 访问过的记忆（access_count>0 且最近 3 天内有访问）不衰减；
        - importance 跌破 min_importance 的记忆被删除（主动遗忘）。
        返回 {"decayed": n, "deleted": n}。由 memory_engine 定期调度调用。
        """
        from datetime import timedelta
        now = datetime.now()
        decayed = deleted = 0
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("SELECT id, importance, access_count, last_accessed_at, updated_at FROM memory_facts").fetchall()
            cutoff_access = (now - timedelta(days=3)).isoformat()
            for (fid, importance, access_count, last_accessed, updated_at) in rows:
                recent_access = bool(last_accessed) and str(last_accessed) >= cutoff_access
                if recent_access:
                    # 被持续访问的记忆升温（越用越重要），不衰减
                    conn.execute(
                        "UPDATE memory_facts SET importance = ?, updated_at = ? WHERE id = ?",
                        (min(10.0, importance + 0.2), now, fid)
                    )
                    continue
                days = 0.0
                if updated_at:
                    try:
                        days = (now - datetime.fromisoformat(str(updated_at))).days
                    except Exception:
                        days = 0.0
                if days <= 0:
                    continue
                new_imp = importance * (rate ** days)
                if new_imp < min_importance:
                    conn.execute("DELETE FROM memory_facts WHERE id = ?", (fid,))
                    deleted += 1
                else:
                    conn.execute(
                        "UPDATE memory_facts SET importance = ?, updated_at = ? WHERE id = ?",
                        (round(new_imp, 2), now, fid)
                    )
                    decayed += 1
            conn.commit()
        return {"decayed": decayed, "deleted": deleted}

    def set_fact_embedding(self, fact_id: int, embedding: bytes, embedding_dim: int) -> None:
        """把向量写入 memory_facts（向量直接存权威表，不再复制到 memory_chunks）。"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE memory_facts SET embedding = ?, embedding_dim = ? WHERE id = ?",
                (embedding, embedding_dim, fact_id)
            )

    def set_plan_embedding(self, plan_id: int, embedding: bytes, embedding_dim: int) -> None:
        """把向量写入 dag_plans（任务目标向量直接存权威表）。"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE dag_plans SET embedding = ?, embedding_dim = ? WHERE id = ?",
                (embedding, embedding_dim, plan_id)
            )

    def get_embedding_chunks(self, kinds: tuple = None, thread_id: str = None,
                             limit: int = 2000) -> List[Dict]:
        """读取带向量的检索记录（来自权威表，kind 过滤如 ('fact','task')）。

        向量的权威来源已在 memory_facts.embedding / dag_plans.embedding，
        memory_chunks 冗余表已废弃删除。返回维度与旧索引块一致，调用方无需改动。
        """
        albums: List[Dict] = []
        if kinds is None:
            kinds = ("fact", "task")
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            if "fact" in kinds:
                sql = """SELECT id AS ref_id, thread_id, content AS text, embedding, embedding_dim
                         FROM memory_facts WHERE embedding IS NOT NULL"""
                params: list = []
                if thread_id:
                    sql += " AND thread_id = ?"
                    params.append(thread_id)
                sql += " ORDER BY id DESC LIMIT ?"
                params.append(limit)
                for r in conn.execute(sql, params).fetchall():
                    albums.append({
                        "id": r["ref_id"], "kind": "fact", "thread_id": r["thread_id"],
                        "ref_id": r["ref_id"], "text": r["text"],
                        "vector": r["embedding"], "dim": r["embedding_dim"],
                    })
            if "task" in kinds:
                try:
                    sql = """SELECT id AS ref_id, thread_id, goal AS text, embedding, embedding_dim
                             FROM dag_plans WHERE embedding IS NOT NULL"""
                    params = []
                    if thread_id:
                        sql += " AND thread_id = ?"
                        params.append(thread_id)
                    sql += " ORDER BY id DESC LIMIT ?"
                    params.append(limit)
                    for r in conn.execute(sql, params).fetchall():
                        albums.append({
                            "id": r["ref_id"], "kind": "task", "thread_id": r["thread_id"],
                            "ref_id": r["ref_id"], "text": r["text"],
                            "vector": r["embedding"], "dim": r["embedding_dim"],
                        })
                except Exception:
                    pass
        return albums

    # ----- 分层注入 prompt 的聚合方法 -----
    def build_memory_injection(self, thread_id: str, layers: List[str] = None) -> Dict[str, str]:
        """为 chatbot 准备分层的"记忆注入"片段。

        - history_summary：当前会话历史对话的压缩摘要（history_summaries 表），
          在上下文被压缩后仍保留"更早发生过什么"。
        - L2：用户画像 / 偏好 —— **唯一注入来源**（以【用户画像】/【用户偏好】形式），
          由 chatbot 拼进"=== 分层记忆注入 ==="块。模板中不再有 {{profile_section}} 占位符。
        - L3：近期任务极简摘要（新规划器 dag_plans）。
        - facts：长期记忆（memory_facts，固化的事实/偏好），高 importance（≥6）的注入，
          让跨会话的"用户事实/偏好"常驻 prompt。
        返回 dict：key 是层名，value 是要追加到 system prompt 的 markdown 段落。
        """
        if layers is None:
            layers = ["history_summary", "L2", "L3", "facts"]
        out = {}

        if "history_summary" in layers:
            # Stanford 三因子排序（α*Recency + β*Importance + γ*Relevance）挑出最该保留的摘要，
            # 而不是单纯按时间取最近 N 条——避免久远但高价值的记忆被新摘要挤出注入窗口。
            try:
                summaries = self.get_history_summaries_scored(limit=3, thread_id=thread_id)
            except Exception:
                summaries = []
            texts = [s.get("summary_text") for s in summaries if s.get("summary_text")]
            if texts:
                lines = ["【历史对话摘要（按 Recency+Importance 排序）】"]
                lines.extend(f"- {t}" for t in texts)
                out["history_summary"] = "\n".join(lines)

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

        if "facts" in layers:
            # 长期记忆注入：高重要性（≥6）的固化事实/偏好，跨会话常驻。
            try:
                facts = [f for f in self.get_memory_facts(limit=40)
                         if (f.get("importance") or 0) >= 6.0]
                facts.sort(key=lambda x: x.get("last_accessed_at") or "", reverse=True)
            except Exception:
                facts = []
            if facts:
                lines = ["【长期记忆（固化的事实/偏好）】"]
                for f in facts:
                    cat = {"fact": "事实", "preference": "偏好", "identity": "身份",
                           "relation": "关系", "project": "项目"}.get(f.get("category"), "记忆")
                    lines.append(f"- [{cat}] {f['content']}")
                out["facts"] = "\n".join(lines)

        return out
