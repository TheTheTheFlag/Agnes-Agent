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
    # 分层记忆 API（L1-L5）
    # 业界共识（参考 MemGPT / LangMem / Letta）的 5 层：
    #   L0 Working   → LangGraph state（messages 列表本身）
    #   L1 Thread    → messages 表（对话消息历史，通过 messages 字段直接访问）
    #   L2 Profile   → user_profile + user_preferences（已有）
    #   L3 Episodic  → dag_plans（新规划器）+ messages（任务目标+消息）
    #   L4 Procedural→ command_history（新增）
    #   L5 Semantic  → semantic_cache（新增）
    # 附：history_summaries 表是历史对话的压缩摘要（不归入标准 5 层），用于 history_summary 注入中
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

    # ----- 分层注入 prompt 的聚合方法 -----
    def build_memory_injection(self, thread_id: str, layers: List[str] = None) -> Dict[str, str]:
        """为 chatbot 准备分层的"记忆注入"片段。

        - history_summary：当前会话历史对话的压缩摘要（history_summaries 表），
          在上下文被压缩后仍保留"更早发生过什么"。
        - L2：用户画像 / 偏好 —— **由 chatbot 节点通过 {{profile_section}} 注入**（见 builder.py:114），
          本函数不重复注入，避免 system prompt 中出现两次"用户个人信息"段。
        - L3：近期任务极简摘要（新规划器 dag_plans）。
        L4（命令历史）/ L5（知识缓存）不常驻，完全靠工具查询
        （get_command_history / search_my_memory）。
        返回 dict：key 是层名，value 是要追加到 system prompt 的 markdown 段落。
        """
        if layers is None:
            layers = ["history_summary", "L3"]   # L2 由 {{profile_section}} 单独注入
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

        return out
