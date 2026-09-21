"""tests/test_scheduler.py — 多用户定时任务：每用户表隔离 + 按归属用户自动执行 + 工具包装。"""
import os
import sys
import tempfile
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import scheduler
from app import userctx
from app.scheduler import (list_tasks, get_task, insert_task, update_task,
                           delete_task, clean_create, clean_update, is_due, run_task_now)
from app.userctx import set_current_user, reset_current_user, current_user


def _mk(username, name="t", prompt="hello", cron="*/5 * * * *", **kw):
    d = {"name": name, "prompt": prompt, "schedule_type": "cron", "cron_expr": cron}
    d.update(kw)
    task, err = clean_create(d)
    assert not err, err
    return task


class SchedulerIsolationTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig = userctx.USERS_DIR
        userctx.USERS_DIR = os.path.join(self._tmp, "users")
        userctx._ensured_users.clear()
        scheduler._started = False  # 避免后台线程在测试中做真实图执行

    def tearDown(self):
        userctx.USERS_DIR = self._orig
        userctx._ensured_users.clear()

    def _ctx(self, u):
        return set_current_user(u)

    def test_crud_only_own_table(self):
        tok = self._ctx("alice")
        try:
            insert_task(_mk("alice", name="alice任务"))
            self.assertEqual(len(list_tasks("alice")), 1)
            self.assertEqual(list_tasks("bob"), [], "bob 不应看到 alice 的任务")
            tid = list_tasks("alice")[0]["id"]
            self.assertTrue(get_task(tid, "alice"))
            self.assertIsNone(get_task(tid, "bob"), "bob 越权读应无果")
        finally:
            reset_current_user(tok)

    def test_update_and_delete_scoped(self):
        tok = self._ctx("alice")
        try:
            insert_task(_mk("alice", name="todo"))
            tid = list_tasks("alice")[0]["id"]
            update_task(tid, username="alice", prompt="改后")
            self.assertEqual(get_task(tid, "alice")["prompt"], "改后")
            # bob 试图更新 alice 的任务：bob 自己的表没有该 id → 0 行生效
            update_task(tid, username="bob", prompt="hack")
            self.assertEqual(get_task(tid, "alice")["prompt"], "改后", "bob 更新无效")
            delete_task(tid, username="alice")
            self.assertIsNone(get_task(tid, "alice"))
        finally:
            reset_current_user(tok)

    def test_clean_create_validation(self):
        t, err = clean_create({})
        self.assertIsNone(t)
        self.assertTrue(err)
        t, err = clean_create({"name": "x", "prompt": "y", "schedule_type": "cron", "cron_expr": "bad"})
        self.assertIsNone(t)
        self.assertIn("5 段", err)
        t, err = clean_create({"name": "x", "prompt": "y", "schedule_type": "interval", "interval_seconds": 0})
        self.assertIsNone(t)
        t, err = clean_create({"name": "x", "prompt": "y", "schedule_type": "cron", "cron_expr": "0 9 * * *"})
        self.assertEqual(t["schedule_type"], "cron")
        self.assertEqual(t["cron_expr"], "0 9 * * *")

    def test_clean_update_validation(self):
        existing = _mk("x", name="old", prompt="p", cron="0 9 * * *")
        fields, err = clean_update(existing, {"name": "new"})
        self.assertIsNone(err)
        self.assertEqual(fields["name"], "new")
        fields, err = clean_update(existing, {"enabled": False})
        self.assertEqual(fields["enabled"], False)
        fields, err = clean_update(existing, {})
        self.assertFalse(fields)
        self.assertTrue(err)
        fields, err = clean_update(existing, {"schedule_type": "cron", "cron_expr": "bogus"})
        self.assertTrue(err)

    def test_is_due_cron_and_interval(self):
        now = datetime(2026, 9, 21, 9, 0, 5)
        now_str = now.isoformat()
        task = _mk("x", name="m", cron="0 9 * * *")
        self.assertTrue(is_due(task, now.timestamp(), now_str))
        task["last_run_at"] = "2026-09-21T09:00:00"
        self.assertFalse(is_due(task, now.timestamp(), now_str), "本分钟已执行")
        task["enabled"] = 0
        self.assertFalse(is_due(task, now.timestamp(), now_str))
        iv = {"id": "i1", "name": "iv", "schedule_type": "interval",
              "interval_seconds": 60, "enabled": 1, "last_run_at": None,
              "prompt": "p"}
        self.assertTrue(is_due(iv, now.timestamp(), now_str), "interval 首次立即执行")
        iv["last_run_at"] = "2026-09-21T08:58:00"
        self.assertTrue(is_due(iv, now.timestamp(), now_str))
        iv["last_run_at"] = "2026-09-21T08:59:30"
        self.assertFalse(is_due(iv, now.timestamp(), now_str))

    def test_execute_task_runs_under_owner_ctx(self):
        seen = {}

        class FakeAI:
            type = "ai"
            content = "输出B"

        def fake_stream(inputs, config, **kw):
            seen["ctx_user"] = current_user()
            seen["prompt"] = inputs["messages"][0][1]
            return [{"node": {"messages": [FakeAI()]}}]

        class FakeGraph:
            def stream(self, *a, **kw):
                return fake_stream(*a, **kw)

        orig = scheduler._get_graph
        scheduler._GRAPH = FakeGraph()
        tok = self._ctx("alice")
        try:
            insert_task(_mk("alice", name="run", prompt="跑我"))
            tid = list_tasks("alice")[0]["id"]
            res = run_task_now(tid, "alice")
            self.assertTrue(res["ok"], res)
            self.assertEqual(seen["ctx_user"], "alice", "执行时应按归属用户设置上下文")
            self.assertEqual(seen["prompt"], "跑我")
            self.assertIn("输出B", res["result"])
            got = get_task(tid, "alice")
            self.assertTrue(got["last_run_at"])
            self.assertEqual(got["last_result"], "输出B")
        finally:
            reset_current_user(tok)
            scheduler._GRAPH = None
            scheduler._get_graph = orig


class SchedulerToolsTest(unittest.TestCase):
    def setUp(self):
        import json
        self._json = json
        self._tmp = tempfile.mkdtemp()
        self._orig = userctx.USERS_DIR
        userctx.USERS_DIR = os.path.join(self._tmp, "users")
        userctx._ensured_users.clear()

    def tearDown(self):
        userctx.USERS_DIR = self._orig
        userctx._ensured_users.clear()

    def _call(self, fn, **kw):
        import json
        out = fn.invoke(kw) if kw else fn.invoke({})
        try:
            return json.loads(out)
        except Exception:
            return {"raw": out}

    def test_tools_full_crud_on_own_tasks(self):
        from app.tools import (list_scheduled_tasks, create_scheduled_task,
                               update_scheduled_task, delete_scheduled_task)
        tok = set_current_user("alice")
        try:
            r = self._call(create_scheduled_task, name="早报", prompt="总结昨天的新闻",
                           schedule_type="cron", cron_expr="0 9 * * *")
            self.assertTrue(r.get("ok"), r)
            tid = r["task"]["id"]
            r2 = self._call(list_scheduled_tasks)
            self.assertEqual(len(r2["tasks"]), 1)
            r3 = self._call(update_scheduled_task, task_id=tid, enabled=False, cron_expr="0 10 * * *")
            self.assertEqual(r3["task"]["enabled"], 0)
            self.assertEqual(r3["task"]["cron_expr"], "0 10 * * *")
            r4 = self._call(delete_scheduled_task, task_id=tid)
            self.assertTrue(r4.get("ok"))
            r5 = self._call(list_scheduled_tasks)
            self.assertEqual(len(r5["tasks"]), 0)
        finally:
            reset_current_user(tok)

    def test_tools_in_common_toolset(self):
        from app.tools import tools, _ADMIN_ONLY_TOOLS
        names = {t.name for t in tools}
        for n in ("list_scheduled_tasks", "create_scheduled_task",
                  "update_scheduled_task", "delete_scheduled_task",
                  "run_scheduled_task_now"):
            self.assertIn(n, names)
        self.assertNotIn("list_scheduled_tasks", _ADMIN_ONLY_TOOLS)


if __name__ == "__main__":
    unittest.main()