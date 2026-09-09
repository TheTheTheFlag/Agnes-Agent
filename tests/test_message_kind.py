"""回归测试：messages 表 kind/meta 事件持久化 + /api/messages type 筛选。

覆盖：
  1. MemoryManager.add_event 以 role='event' + kind/meta 落库；
  2. add_event 的 2s 轻量去重（同 thread+kind+content）；
  3. set_last_approval_decision 回填审批 allow/mode；
  4. /api/messages?type=… 只返回对应类型（含旧行无 kind 时按 chat 归类）。
"""
import asyncio
import json
import os as _os
import tempfile
import unittest
from unittest import mock

from app.memory.memory_manager import MemoryManager


class MessageKindTest(unittest.TestCase):
    def setUp(self):
        fd, self._tmp_db = tempfile.mkstemp(suffix=".db")
        _os.close(fd)
        self._patchers = [
            mock.patch("app.server.api.memory.DB_PATH", self._tmp_db),
        ]
        for p in self._patchers:
            p.start()
        self.mm = MemoryManager(db_path=self._tmp_db, thread_id="kind-tid")

    def tearDown(self):
        for p in self._patchers:
            p.stop()
        try:
            _os.remove(self._tmp_db)
        except OSError:
            pass

    def _seed(self):
        self.mm.add_message("kind-tid", "user", "你好")
        self.mm.add_message("kind-tid", "assistant", "收到")
        self.mm.add_event("kind-tid", "node_start", "chatbot", {"node": "chatbot"})
        self.mm.add_event("kind-tid", "llm_call", "chatbot",
                          {"node": "chatbot", "input": "hi", "output": "hello", "duration_ms": 5})
        self.mm.add_event("kind-tid", "tool_call", "list_tasks",
                          {"name": "list_tasks", "params": {"limit": 5}, "result": "ok"})
        self.mm.add_event("kind-tid", "thought", "规划", {"role": "planner", "title": "规划", "text": "x"})
        self.mm.add_event("kind-tid", "approval", "允许执行命令?",
                          {"question": "允许执行命令?", "command": "ls", "mode": "per_ask", "allow": None})

    def test_add_event_stores_kind_and_meta(self):
        self._seed()
        rows = self.mm.get_thread_messages("kind-tid", limit=100)
        events = [r for r in rows if r["role"] == "event"]
        self.assertEqual(len(events), 5)
        llm = next(r for r in events if r["kind"] == "llm_call")
        self.assertEqual(llm["meta"]["output"], "hello")
        node = next(r for r in events if r["kind"] == "node_start")
        self.assertEqual(node["content"], "chatbot")

    def test_add_event_dedup_within_2s(self):
        self.mm.add_event("kind-tid", "llm_call", "chatbot", {"node": "chatbot", "output": "a"})
        self.mm.add_event("kind-tid", "llm_call", "chatbot", {"node": "chatbot", "output": "a"})
        rows = [r for r in self.mm.get_thread_messages("kind-tid", limit=10)
                if r["kind"] == "llm_call"]
        self.assertEqual(len(rows), 1, "2 秒窗口内同 kind+content 应去重")

    def test_approval_decision_backfill(self):
        self.mm.add_event("kind-tid", "approval", "允许?",
                          {"question": "允许?", "command": "rm", "mode": "per_ask", "allow": None})
        ok = self.mm.set_last_approval_decision("kind-tid", True, "always_allow")
        self.assertTrue(ok)
        rows = [r for r in self.mm.get_thread_messages("kind-tid", limit=10)
                if r["kind"] == "approval"]
        self.assertEqual(rows[0]["meta"]["allow"], True)
        self.assertEqual(rows[0]["meta"]["mode"], "always_allow")

    def test_api_messages_type_filter(self):
        from app.server.api.memory import get_messages
        self._seed()
        # all：5 事件 + 2 对话
        all_resp = asyncio.run(get_messages(thread_id="kind-tid", limit=100))
        kinds = [m["kind"] for m in all_resp["messages"]]
        self.assertEqual(kinds.count("chat"), 2)
        self.assertEqual(len([k for k in kinds if k != "chat"]), 5)

        # type=chat：只有对话（含旧行无 kind 归 chat）
        chat_resp = asyncio.run(get_messages(thread_id="kind-tid", limit=100, type="chat"))
        self.assertTrue(all(m["kind"] == "chat" for m in chat_resp["messages"]))
        self.assertEqual(len(chat_resp["messages"]), 2)

        # type=llm_call,tool_call：多类型逗号分隔
        ev_resp = asyncio.run(get_messages(thread_id="kind-tid", limit=100, type="llm_call,tool_call"))
        got = {m["kind"] for m in ev_resp["messages"]}
        self.assertEqual(got, {"llm_call", "tool_call"})

        # type=approval：审批卡完整（含 meta）
        ap_resp = asyncio.run(get_messages(thread_id="kind-tid", limit=100, type="approval"))
        self.assertEqual(len(ap_resp["messages"]), 1)
        self.assertIn("allow", ap_resp["messages"][0]["meta"])


if __name__ == "__main__":
    unittest.main()
