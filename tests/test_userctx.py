"""tests/test_userctx.py — 多用户上下文（每用户路径 / 密钥 / 模型脱敏）。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.config as config
from app import userctx
from app.userctx import (set_current_user, reset_current_user, current_user,
                         user_paths, resolve_user_keys)


class UserPathTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.mkdtemp()
        self._orig_users_dir = userctx.USERS_DIR
        userctx.USERS_DIR = os.path.join(self._tmp, "users")

    def tearDown(self):
        userctx.USERS_DIR = self._orig_users_dir
        userctx._ensured_users.clear()

    def test_default_user_is_mirror(self):
        tok = set_current_user("")
        try:
            self.assertEqual(current_user(), "Mirror")
            self.assertIn(os.path.join("users", "Mirror"), str(config.DB_PATH))
        finally:
            reset_current_user(tok)

    def test_path_changes_per_user(self):
        tok = set_current_user("alice")
        try:
            p1 = str(config.DB_PATH)
            self.assertTrue(p1.endswith(os.path.join("users", "alice", "data", "memory.db")))
            self.assertTrue(str(config.UPLOADS_DIR).endswith(os.path.join("users", "alice", "uploads")))
        finally:
            reset_current_user(tok)
        tok = set_current_user("bob")
        try:
            self.assertNotEqual(str(config.DB_PATH), p1)
            self.assertIn(os.path.join("users", "bob"), str(config.DB_PATH))
        finally:
            reset_current_user(tok)

    def test_checkpoint_and_rag_paths(self):
        tok = set_current_user("carol")
        try:
            self.assertIn("checkpoints.db", str(config.CHECKPOINT_DB_PATH))
            self.assertIn(os.path.join("users", "carol", "lightrag_storage"), str(config.RAG_ROOT))
        finally:
            reset_current_user(tok)

    def test_ensure_user_dirs(self):
        tok = set_current_user("dave")
        try:
            p = userctx.ensure_user_dirs("dave")
            for d in (p.data_dir, p.uploads_dir, p.deliverables_dir, p.rag_root, p.skills_dir):
                self.assertTrue(os.path.isdir(d), d)
        finally:
            reset_current_user(tok)


class UserKeysTest(unittest.TestCase):
    def setUp(self):
        from app.server import accounts
        import tempfile
        self._tmp = tempfile.mkdtemp()
        self._orig_acc = accounts.ACCOUNTS_DB_PATH
        self._orig_legacy = accounts.LEGACY_AUTH_FILE
        self._orig_users_dir = userctx.USERS_DIR
        accounts.ACCOUNTS_DB_PATH = os.path.join(self._tmp, "accounts.db")
        accounts.LEGACY_AUTH_FILE = os.path.join(self._tmp, "nonexistent_auth.json")
        userctx.USERS_DIR = os.path.join(self._tmp, "users")  # 避免在真实 data/users 建目录
        self.accounts = accounts
        accounts.init_db()

    def tearDown(self):
        self.accounts.ACCOUNTS_DB_PATH = self._orig_acc
        self.accounts.LEGACY_AUTH_FILE = self._orig_legacy
        userctx.USERS_DIR = self._orig_users_dir

    def test_normal_user_uses_own_keys(self):
        self.accounts.create_user("u1", "pw123456", "agnes-1", "sf-1",
                                  role="user", status="active")
        keys = resolve_user_keys("u1")
        self.assertEqual(keys["agnes"], ["agnes-1"])
        self.assertEqual(keys["siliconflow"], ["sf-1"])

    def test_admin_pools_all_active_users(self):
        self.accounts.create_user("Mirror", "pw123456", "agnes-admin", "sf-admin",
                                  role="admin", status="active")
        self.accounts.create_user("u2", "pw123456", "agnes-2", "sf-2",
                                  role="user", status="active")
        self.accounts.create_user("u3", "pw123456", "agnes-3", "sf-3",
                                  role="user", status="pending")
        keys = resolve_user_keys("Mirror")
        self.assertIn("agnes-2", keys["agnes"])
        self.assertNotIn("agnes-3", keys["agnes"])  # pending 不纳入

    def test_multi_key_split(self):
        self.accounts.create_user("u4", "pw123456", "k1,k2", "s1, s2",
                                  role="user", status="active")
        keys = resolve_user_keys("u4")
        self.assertEqual(keys["agnes"], ["k1", "k2"])
        self.assertEqual(keys["siliconflow"], ["s1", "s2"])


class MigrationTest(unittest.TestCase):
    def test_migrate_legacy_to_mirror(self):
        import tempfile
        base = tempfile.mkdtemp()
        data = os.path.join(base, "data")
        os.makedirs(data)
        with open(os.path.join(data, "memory.db"), "w", encoding="utf-8") as f:
            f.write("legacy-memory")
        with open(os.path.join(data, "checkpoints.db"), "w", encoding="utf-8") as f:
            f.write("legacy-ck")
        os.makedirs(os.path.join(base, "uploads"))
        with open(os.path.join(base, "uploads", "a.txt"), "w", encoding="utf-8") as f:
            f.write("up")

        old = (userctx.BASE_DIR, userctx.DATA_DIR, userctx.USERS_DIR, userctx._MIGRATION_FLAG)
        userctx.BASE_DIR = base
        userctx.DATA_DIR = data
        userctx.USERS_DIR = os.path.join(data, "users")
        userctx._MIGRATION_FLAG = os.path.join(userctx.USERS_DIR, ".migrated_to_mirror")
        try:
            result = userctx.migrate_legacy_to_mirror()
            self.assertEqual(result.get("memory.db"), "moved")
            self.assertEqual(result.get("checkpoints.db"), "moved")
            self.assertEqual(result.get("uploads"), "moved")
            my = userctx.user_paths("Mirror")
            self.assertTrue(os.path.isfile(os.path.join(my.data_dir, "memory.db")))
            self.assertTrue(os.path.isfile(os.path.join(my.uploads_dir, "a.txt")))
            # 幂等：再次调用不再移动
            self.assertEqual(userctx.migrate_legacy_to_mirror(), {})
        finally:
            userctx.BASE_DIR, userctx.DATA_DIR, userctx.USERS_DIR, userctx._MIGRATION_FLAG = old


class MigrationMergeTest(unittest.TestCase):
    def test_merge_into_preexisting_empty_dir(self):
        """目标目录被 ensure_user_dirs 预建为空时，仍应合并内容而非漏迁。"""
        import tempfile
        base = tempfile.mkdtemp()
        data = os.path.join(base, "data")
        os.makedirs(data)
        os.makedirs(os.path.join(base, "uploads"))
        with open(os.path.join(base, "uploads", "a.txt"), "w", encoding="utf-8") as f:
            f.write("up")
        # 预建空的 Mirror/uploads，模拟 ensure_user_dirs 的副作用
        os.makedirs(os.path.join(data, "users", "Mirror", "uploads"))

        old = (userctx.BASE_DIR, userctx.DATA_DIR, userctx.USERS_DIR, userctx._MIGRATION_FLAG)
        userctx.BASE_DIR = base
        userctx.DATA_DIR = data
        userctx.USERS_DIR = os.path.join(data, "users")
        userctx._MIGRATION_FLAG = os.path.join(userctx.USERS_DIR, ".migrated_to_mirror")
        try:
            result = userctx.migrate_legacy_to_mirror()
            self.assertEqual(result.get("uploads"), "moved")
            self.assertTrue(os.path.isfile(os.path.join(userctx.user_paths("Mirror").uploads_dir, "a.txt")))
        finally:
            userctx.BASE_DIR, userctx.DATA_DIR, userctx.USERS_DIR, userctx._MIGRATION_FLAG = old


class StoreEventIsolationTest(unittest.TestCase):
    def setUp(self):
        import asyncio
        import tempfile
        from app.server import store
        self.store = store
        self.asyncio = asyncio
        self._tmp = tempfile.mkdtemp()
        self._orig_users_dir = userctx.USERS_DIR
        userctx.USERS_DIR = os.path.join(self._tmp, "users")
        self._orig_listeners = store._event_listeners
        store._event_listeners = []

    def tearDown(self):
        self.store._event_listeners = self._orig_listeners
        userctx.USERS_DIR = self._orig_users_dir
        userctx._ensured_users.clear()

    def test_event_not_delivered_to_other_user(self):
        q_alice = self.asyncio.Queue()
        q_bob = self.asyncio.Queue()
        q_admin = self.asyncio.Queue()
        self.store._register_listener(q_alice, "alice")
        self.store._register_listener(q_bob, "bob")
        self.store._register_listener(q_admin, "Mirror")

        tok = set_current_user("alice")
        try:
            self.store.add_event("tool_call", {"name": "x"}, "t1")
        finally:
            reset_current_user(tok)

        self.assertFalse(q_alice.empty(), "本人应收到自己的事件")
        self.assertTrue(q_bob.empty(), "其他普通用户不应收到")
        self.assertFalse(q_admin.empty(), "管理员应能看到全部事件")


class ModelsMaskTest(unittest.TestCase):
    def test_catalog_masked_for_non_admin(self):
        from app.server import config as srv_cfg
        catalog = {"p1": {"label": "x", "api_key": "supersecret9999"}}
        masked = srv_cfg._mask_catalog(catalog)
        self.assertNotEqual(masked["p1"]["api_key"], "supersecret9999")
        self.assertTrue(masked["p1"]["api_key"].endswith("9999"))

    def test_catalog_untouched_for_admin(self):
        from app.server import config as srv_cfg
        # 无请求上下文（None）按管理员处理，不脱敏
        self.assertTrue(srv_cfg._is_request_admin(None))


class ToolsFilterTest(unittest.TestCase):
    """非管理员工具的收窄：不提供 execute_command / tavily_search。"""

    def setUp(self):
        from app.server import accounts
        import tempfile
        self._tmp = tempfile.mkdtemp()
        self._orig_acc = accounts.ACCOUNTS_DB_PATH
        self._orig_legacy = accounts.LEGACY_AUTH_FILE
        self._orig_users_dir = userctx.USERS_DIR
        accounts.ACCOUNTS_DB_PATH = os.path.join(self._tmp, "accounts.db")
        accounts.LEGACY_AUTH_FILE = os.path.join(self._tmp, "nonexistent_auth.json")
        userctx.USERS_DIR = os.path.join(self._tmp, "users")
        accounts.init_db()

    def tearDown(self):
        from app.server import accounts
        accounts.ACCOUNTS_DB_PATH = self._orig_acc
        accounts.LEGACY_AUTH_FILE = self._orig_legacy
        userctx.USERS_DIR = self._orig_users_dir
        userctx._ensured_users.clear()

    def test_admin_gets_full_tools(self):
        from app.server import accounts
        from app.tools import get_tools_for_user, tools
        accounts.create_user("Mirror", "pw123456", "agnes-admin", "sf-admin",
                             role="admin", status="active")
        mine = get_tools_for_user("Mirror")
        names = {t.name for t in mine}
        self.assertEqual(names, {t.name for t in tools})
        self.assertIn("execute_command", names)
        self.assertIn("tavily_search", names)

    def test_non_admin_filtered(self):
        from app.server import accounts
        from app.tools import get_tools_for_user, tools
        accounts.create_user("u1", "pw123456", "agnes-1", "sf-1",
                             role="user", status="active")
        mine = get_tools_for_user("u1")
        names = {t.name for t in mine}
        self.assertIn("read_file", names)
        self.assertIn("ls", names)
        self.assertNotIn("execute_command", names)
        self.assertNotIn("tavily_search", names)
        self.assertEqual(len(mine), len([t for t in tools if t.name not in ("execute_command", "tavily_search")]))


class SkillInstallPerUserTest(unittest.TestCase):
    """技能安装/读取按用户隔离：install_skill_md 写入当前用户 skills 目录。"""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.mkdtemp()
        self._orig_users_dir = userctx.USERS_DIR
        userctx.USERS_DIR = os.path.join(self._tmp, "users")
        self._tok = userctx.set_current_user("alice")

    def tearDown(self):
        userctx.reset_current_user(self._tok)
        userctx.USERS_DIR = self._orig_users_dir
        userctx._ensured_users.clear()

    def test_install_goes_to_user_dir_and_isolated(self):
        from app.skills import loader
        raw = "---\nname: demo-skill\ndescription: 测试技能\n---\n步骤：无"
        rel = loader.install_skill_md("demo-skill", raw)
        # 必须写到 alice 自己的目录
        expected = os.path.join(userctx.USERS_DIR, "alice", "skills", "demo-skill", "SKILL.md")
        self.assertTrue(os.path.isfile(expected), rel)
        names = {s["name"] for s in loader.load_all_skills()}
        self.assertIn("demo-skill", names)

        # 切到 bob：看不到 alice 安装的 demo-skill
        tok = userctx.set_current_user("bob")
        try:
            bob_names = {s["name"] for s in loader.load_all_skills()}
        finally:
            userctx.reset_current_user(tok)
        self.assertNotIn("demo-skill", bob_names)

    def test_builtin_skills_available_to_everyone(self):
        from app.skills import loader
        names = {s["name"] for s in loader.load_all_skills()}
        # 人人可见的内置技能
        self.assertIn("agent-browser", names)
        self.assertIn("find-skills", names)
        self.assertIn("create-skill", names)

    def test_mirror_legacy_skills_only_in_mirror_dir(self):
        """预置给 Mirror 的历史技能只存在于 Mirror 自己的技能目录，其他用户不可见。"""
        from app.skills import loader
        import tempfile
        tmp = tempfile.mkdtemp()
        mirrorskills = os.path.join(tmp, "Mirror", "skills")
        os.makedirs(mirrorskills, exist_ok=True)
        for s in ("agnes-media", "ai-trends-reporter", "amap-commute", "amap-weather", "Image-Understanding"):
            os.makedirs(os.path.join(mirrorskills, s), exist_ok=True)
            with open(os.path.join(mirrorskills, s, "SKILL.md"), "w", encoding="utf-8") as f:
                f.write(f"---\nname: {s}\ndescription: legacy skill {s}\n---\n")
        # Mirror 看到内置 + 自己的历史技能
        old_udir = userctx.USERS_DIR
        userctx.USERS_DIR = tmp
        self._tok = userctx.set_current_user("Mirror")
        try:
            mirror_names = {x["name"] for x in loader.load_all_skills()}
        finally:
            userctx.reset_current_user(self._tok)
            userctx.USERS_DIR = old_udir
            userctx._ensured_users.clear()
        self.assertIn("agnes-media", mirror_names)
        self.assertIn("amap-weather", mirror_names)
        # 普通用户看不到历史技能
        userctx.USERS_DIR = tmp
        self._tok = userctx.set_current_user("Test")
        try:
            test_names = {x["name"] for x in loader.load_all_skills()}
        finally:
            userctx.reset_current_user(self._tok)
            userctx.USERS_DIR = old_udir
            userctx._ensured_users.clear()
        self.assertNotIn("agnes-media", test_names)
        self.assertNotIn("amap-commute", test_names)
        self.assertIn("agent-browser", test_names)


class PerUserStateTest(unittest.TestCase):
    """每用户会话状态隔离：thread_id / 审批模式都落在各自用户目录，互不串扰。"""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.mkdtemp()
        self._orig_users_dir = userctx.USERS_DIR
        userctx.USERS_DIR = os.path.join(self._tmp, "users")
        self._tok = userctx.set_current_user("alice")

    def tearDown(self):
        userctx.reset_current_user(self._tok)
        userctx.USERS_DIR = self._orig_users_dir
        userctx._ensured_users.clear()

    def test_thread_id_per_user(self):
        self.assertEqual(userctx.current_tid("alice"), "default")
        userctx.set_current_tid("t-alice", "alice")
        # bob 不受影响
        self.assertEqual(userctx.current_tid("bob"), "default")
        # 当前上下文（alice）读取一致
        self.assertEqual(userctx.current_tid(), "t-alice")

    def test_approval_mode_per_user(self):
        self.assertEqual(userctx.get_approval_mode(), "session_allow")
        userctx.set_approval_mode("per_ask")
        self.assertEqual(userctx.get_approval_mode(), "per_ask")
        self.assertEqual(userctx.get_approval_mode("bob"), "session_allow")

    def test_threads_current_tid_scoped_to_user(self):
        import asyncio
        from app.server.api import memory as mem_api
        userctx.ensure_user_dirs("alice")
        userctx.ensure_user_dirs("bob")
        userctx.set_current_tid("alice-tid", "alice")
        # 预置一条 alice 消息，让该 thread 出现在列表
        from app.memory.memory_manager import MemoryManager
        mm = MemoryManager(db_path=userctx.user_paths("alice").db_path, thread_id="alice-tid")
        mm.add_message("alice-tid", "user", "hello")
        data = asyncio.run(mem_api.get_threads())
        self.assertEqual(data["current_thread_id"], "alice-tid")
        self.assertTrue(any(t["thread_id"] == "alice-tid" and t.get("current") for t in data["threads"]))
        # bob 的 current 是 bob 自己的（不暴露 alice 的）
        tok = userctx.set_current_user("bob")
        try:
            data_bob = asyncio.run(mem_api.get_threads())
        finally:
            userctx.reset_current_user(tok)
        self.assertNotEqual(data_bob["current_thread_id"], "alice-tid")


class ToolsApiFilterTest(unittest.TestCase):
    """/api/tools 按当前用户返回可见工具：非管理员不含 execute_command / tavily_search。"""

    def setUp(self):
        from app.server import accounts
        import tempfile
        self._tmp = tempfile.mkdtemp()
        self._orig_acc = accounts.ACCOUNTS_DB_PATH
        self._orig_legacy = accounts.LEGACY_AUTH_FILE
        accounts.ACCOUNTS_DB_PATH = os.path.join(self._tmp, "accounts.db")
        accounts.LEGACY_AUTH_FILE = os.path.join(self._tmp, "nonexistent_auth.json")
        accounts.init_db()

    def tearDown(self):
        from app.server import accounts
        accounts.ACCOUNTS_DB_PATH = self._orig_acc
        accounts.LEGACY_AUTH_FILE = self._orig_legacy

    def test_non_admin_tools_api_excludes_admin_tools(self):
        import asyncio
        from app.server import accounts
        from app.server.api import tools as tools_api
        accounts.create_user("t1", "pw123456", "agnes-1", "sf-1",
                             role="user", status="active")
        tok = userctx.set_current_user("t1")
        try:
            res = asyncio.run(tools_api.get_tools())
        finally:
            userctx.reset_current_user(tok)
        names = {t["name"] for t in res["tools"]}
        self.assertIn("read_file", names)
        self.assertNotIn("execute_command", names)
        self.assertNotIn("tavily_search", names)

    def test_admin_tools_api_includes_all(self):
        import asyncio
        from app.server import accounts
        from app.server.api import tools as tools_api
        accounts.create_user("Mirror", "pw123456", "agnes-admin", "sf-admin",
                             role="admin", status="active")
        tok = userctx.set_current_user("Mirror")
        try:
            res = asyncio.run(tools_api.get_tools())
        finally:
            userctx.reset_current_user(tok)
        names = {t["name"] for t in res["tools"]}
        self.assertIn("execute_command", names)
        self.assertIn("tavily_search", names)


class Neo4jWorkspaceTest(unittest.TestCase):
    """Neo4j 图谱按用户隔离：workspace 标签并入用户名，且净化后无非法字符。"""

    def tearDown(self):
        userctx._ensured_users.clear()

    def test_workspace_includes_current_user(self):
        from app.memory import ligraphrag_adapter as L
        tok = userctx.set_current_user("Alice")
        try:
            self.assertEqual(L._neo4j_workspace("__global__"), "Alice___global__")
        finally:
            userctx.reset_current_user(tok)

    def test_workspace_isolated_between_users(self):
        from app.memory import ligraphrag_adapter as L
        tok = userctx.set_current_user("Alice")
        ws_alice = L._neo4j_workspace("__global__")
        userctx.reset_current_user(tok)
        tok = userctx.set_current_user("Bob")
        try:
            ws_bob = L._neo4j_workspace("__global__")
        finally:
            userctx.reset_current_user(tok)
        self.assertNotEqual(ws_alice, ws_bob)

    def test_workspace_sanitized_and_unique_for_cjk_user(self):
        from app.memory import ligraphrag_adapter as L
        tok = userctx.set_current_user("张三")
        try:
            ws = L._neo4j_workspace("kb-一二三")
        finally:
            userctx.reset_current_user(tok)
        self.assertNotIn("/", ws)
        self.assertNotIn("\\", ws)
        self.assertNotIn("`", ws)


class FileToolsPathScopeTest(unittest.TestCase):
    """文件工具目录边界：普通用户锁自己的工作区；管理员不限制目录。"""

    def setUp(self):
        from app.server import accounts
        import tempfile
        self._tmp = tempfile.mkdtemp()
        self._orig_acc = accounts.ACCOUNTS_DB_PATH
        self._orig_legacy = accounts.LEGACY_AUTH_FILE
        self._orig_users_dir = userctx.USERS_DIR
        accounts.ACCOUNTS_DB_PATH = os.path.join(self._tmp, "accounts.db")
        accounts.LEGACY_AUTH_FILE = os.path.join(self._tmp, "nonexistent_auth.json")
        userctx.USERS_DIR = os.path.join(self._tmp, "users")
        accounts.init_db()

    def tearDown(self):
        from app.server import accounts
        accounts.ACCOUNTS_DB_PATH = self._orig_acc
        accounts.LEGACY_AUTH_FILE = self._orig_legacy
        userctx.USERS_DIR = self._orig_users_dir
        userctx._ensured_users.clear()

    def test_non_admin_confined_to_own_workspace(self):
        from app.server import accounts
        from app.tools import file_ops
        accounts.create_user("u1", "pw123456", "a", "s", role="user", status="active")
        tok = userctx.set_current_user("u1")
        try:
            ok = file_ops.write_file.func("deliverables/note.txt", "hi")
            self.assertTrue(json_loads_ok(ok))
            # 越界写被拒绝（ValueError 由工具调用层捕获为错误消息）
            with self.assertRaises(ValueError):
                file_ops.write_file.func("../escape.txt", "x")
            self.assertFalse(os.path.isfile(os.path.join(self._tmp, "escape.txt")))
        finally:
            userctx.reset_current_user(tok)

    def test_admin_unrestricted_paths(self):
        from app.server import accounts
        from app.tools import file_ops
        import json as _json
        accounts.create_user("Mirror", "pw123456", "a", "s", role="admin", status="active")
        tok = userctx.set_current_user("Mirror")
        outside = os.path.join(self._tmp, "outside_dir")
        os.makedirs(outside, exist_ok=True)
        try:
            # 管理员基准 = 项目根；绝对路径不受限
            self.assertEqual(file_ops._root(), os.path.abspath(file_ops.BASE_DIR))
            target = os.path.join(outside, "x.txt")
            res = file_ops.write_file.func(target, "admin")
            self.assertEqual(_json.loads(res)["path"], target)
            self.assertTrue(os.path.isfile(target))
            # 越界路径不再抛错
            p = file_ops._resolve_path(os.path.join(self._tmp, "anywhere"))
            self.assertEqual(p, os.path.join(self._tmp, "anywhere"))
            # 核心保护路径仍然拒绝（与 execute_command 一致）
            with self.assertRaises(ValueError) as ctx:
                file_ops._assert_writable(".env")
            self.assertIn("受保护", str(ctx.exception))
        finally:
            userctx.reset_current_user(tok)


def json_loads_ok(text: str) -> bool:
    import json
    try:
        d = json.loads(text)
    except Exception:
        return False
    return bool(d.get("ok"))


if __name__ == "__main__":
    unittest.main()
