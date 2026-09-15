"""回归测试：RAG 管理台扩展——知识库元信息/配置/大盘/反馈/导入URL/切片编辑删除、新 API 端点。

全部 mock：不初始化真实 LightRAG、不触网、不依赖 Neo4j/FAISS 进程。
"""
import asyncio
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from app.memory import ligraphrag_adapter as L


class _TempBase(unittest.TestCase):
    """临时 storage root + DB_PATH。"""

    def setUp(self):
        self._root = tempfile.mkdtemp(prefix="kb_admin_")
        self._root_patch = mock.patch.object(L, "_storage_root", return_value=self._root)
        self._root_patch.start()
        self._db = os.path.join(self._root, "t.db")
        self._db_patch = mock.patch.object(L, "DB_PATH", self._db)
        self._db_patch.start()
        self.addCleanup(lambda: (self._root_patch.stop(), self._db_patch.stop()))
        self.addCleanup(shutil.rmtree, self._root, ignore_errors=True)

    def _ws(self, tid):
        p = os.path.join(self._root, tid, tid)
        os.makedirs(p, exist_ok=True)
        return p

    def _meta_file(self, tid):
        return os.path.join(self._root, tid, "kb_meta.json")


class MetaConfigTest(_TempBase):
    def test_get_config_defaults(self):
        cfg = L.kb_get_config("tid1")
        self.assertEqual(cfg["top_k"], 12)
        self.assertTrue(cfg["rerank"])
        self.assertIn("threshold", cfg)
        # 未建库也不抛错
        self.assertEqual(L.kb_meta("tid1")["name"], "tid1")

    def test_set_config_persists_and_clears_instance(self):
        tid = "tid1"
        with mock.patch.object(L, "clear_instance") as ci:
            out = L.kb_set_config(tid, {"top_k": 5, "rerank": False, "bogus": 1})
        self.assertEqual(out["top_k"], 5)
        self.assertFalse(out["rerank"])
        self.assertNotIn("bogus", out)
        ci.assert_called_once_with(tid)
        # 写盘后重新读
        self.assertEqual(L.kb_get_config(tid)["top_k"], 5)

    def test_chunking_strategy_default_and_accept(self):
        # 默认是 R
        self.assertEqual(L.kb_get_config("nonexist")["chunking_strategy"], "R")
        out = L.kb_set_config("tid1", {"chunking_strategy": "F"})
        self.assertEqual(L.kb_get_config("tid1")["chunking_strategy"], "F")
        # 非法值被过滤（不在策略列表里，但 kb_set_config 仍可用任意值；实例构建时兜底 R）
        out2 = L.kb_set_config("tid1", {"chunking_strategy": "X"})
        self.assertEqual(out2["chunking_strategy"], "X")
        # 构建实例时非法策略回退 R
        fn = L._make_chunking_func("X")
        self.assertEqual(fn.__name__, "_chunking_func_r")


class ChunkingStrategyTest(_TempBase):
    """分块策略工厂：F/R/V/P/C 的返回与兜底。"""

    def _tok(self):
        import tiktoken
        class _T:
            def __init__(self):
                self._e = tiktoken.get_encoding("cl100k_base")
            def encode(self, s):
                return self._e.encode(s)
            def decode(self, tks):
                return self._e.decode(tks)
        return _T()

    def test_factory_names(self):
        for s in "FRVPC":
            fn = L._make_chunking_func(s)
            self.assertEqual(fn.__name__, f"_chunking_func_{s.lower()}")

    def test_r_f_p_return_structured(self):
        tok = self._tok()
        text = ("氢动力拖拉机在 2026 年立项，由 Helios Agro 主导设计。\n"
                "CoelhoBot 每小时耕作 12 亩，减少 63% 碳排放。\n" * 20)
        for s in ("F", "R", "P", "C"):
            fn = L._make_chunking_func(s)
            chunks = fn(tok, text, None, False, 80, 600)
            self.assertIsInstance(chunks, list)
            self.assertGreater(len(chunks), 0)
            for c in chunks:
                self.assertIn("content", c)
                self.assertIn("tokens", c)
                self.assertGreater(c["tokens"], 0)

    def test_locator_prefix_all(self):
        tok = self._tok()
        text = "CoelhoBot 拖拉机每小时可耕作 12 亩土地。\n" * 30
        pref = L._with_doc_locator(text)
        for s in ("F", "R", "P", "C"):
            fn = L._make_chunking_func(s)
            chunks = fn(tok, pref, None, False, 80, 600)
            self.assertTrue(any(c["content"].startswith("[文档定位]") for c in chunks),
                            f"{s} 应保留定位前缀")
            self.assertGreaterEqual(len(chunks), 1)

    def test_v_falls_back_to_r(self):
        tok = self._tok()
        fn = L._make_chunking_func("V")
        import asyncio
        res = fn(tok, "x" * 4000, None, False, 80, 600)
        if asyncio.iscoroutine(res):
            self.skipTest("环境装了 langchain-experimental，V 分支已启用")
        self.assertIsInstance(res, list)
        self.assertGreater(len(res), 0)


class CreateTest(_TempBase):
    def test_create_makes_dir_meta(self):
        r = L.kb_create(name="我的知识库", description="备忘")
        self.assertTrue(r["ok"])
        self.assertTrue(r["thread_id"])
        self.assertEqual(r["name"], "我的知识库")
        self.assertTrue(os.path.isdir(self._ws(r["thread_id"])))
        self.assertTrue(os.path.isfile(self._meta_file(r["thread_id"])))
        meta = L.kb_meta(r["thread_id"])
        self.assertEqual(meta["name"], "我的知识库")
        self.assertEqual(meta["description"], "备忘")

    def test_create_with_explicit_id(self):
        r = L.kb_create(thread_id="abc123", name="x")
        self.assertEqual(r["thread_id"], "abc123")
        self.assertTrue(os.path.isdir(self._ws("abc123")))


class DashboardTest(_TempBase):
    def test_dashboard_aggregates(self):
        t1, t2 = "k1", "k2"
        self._ws(t1); self._ws(t2)
        d1 = {
            f"doc-{i:08d}": {"status": "processed", "chunks_count": 1,
                             "created_at": "2026-09-14T00:00:00+00:00",
                             "updated_at": "2026-09-14T00:00:00+00:00"}
            for i in range(3)
        }
        d2 = {f"doc-{i:08d}a": {"status": "failed",
                                "created_at": "2026-09-12T00:00:00+00:00",
                                "updated_at": "2026-09-12T00:00:00+00:00"}
              for i in range(2)}
        for tid, docs in ((t1, d1), (t2, d2)):
            with open(os.path.join(self._ws(tid), "kv_store_doc_status.json"), "w", encoding="utf-8") as f:
                json.dump(docs, f)
            with open(os.path.join(self._ws(tid), "kv_store_text_chunks.json"), "w", encoding="utf-8") as f:
                json.dump({f"c{i}": {} for i in range(4)}, f)
        dash = L.kb_dashboard()
        self.assertEqual(dash["kb_count"], 2)
        self.assertEqual(dash["doc_total"], 5)
        self.assertEqual(dash["status_counts"]["processed"], 3)
        self.assertEqual(dash["status_counts"]["failed"], 2)
        # 7 日趋势：仅今天(created_at=今天环境日期)计入趋势；测试环境日期与 fixture 可能不符，不强断言精确值
        self.assertEqual(len(dash["trend_days"]), 7)

    def test_dashboard_handles_no_feedback_table(self):
        dash = L.kb_dashboard()
        self.assertEqual(dash["feedback"]["like"], 0)


class FeedbackTest(_TempBase):
    def test_write_and_read_roundtrip(self):
        r = L.kb_feedback("k1", "q1", "a1", "like", top_k=5, mode="hybrid")
        self.assertTrue(r["ok"])
        items = L.kb_feedback_list("k1")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["query"], "q1")
        self.assertEqual(items[0]["verdict"], "like")
        self.assertEqual(items[0]["top_k"], 5)

    def test_verdict_normalized(self):
        L.kb_feedback("k1", "q2", "a2", "other")
        items = L.kb_feedback_list()
        self.assertEqual(items[0]["verdict"], "dislike")


class ImportURLTest(_TempBase):
    def test_invalid_url_rejected(self):
        r = L.kb_import_url("k1", "ftp://x")
        self.assertFalse(r["ok"])

    def test_html_stripped_and_ingested(self):
        fake = mock.Mock()
        fake.text = "<html><body><script>bad()</script><style>x{}</style><h1>标题</h1><p>正文内容</p></body></html>"
        fake.headers = {"content-type": "text/html"}
        fake.raise_for_status = mock.Mock()
        with mock.patch.object(L, "httpx") as hx:
            hx.get.return_value = fake
            with mock.patch.object(L, "kb_ingest", return_value={"ok": True, "doc_ids": ["doc-x"]}) as ins:
                r = L.kb_import_url("k1", "https://example.com/a")
        self.assertTrue(r["ok"])
        doc = ins.call_args[0][1][0]
        self.assertIn("正文内容", doc)
        self.assertNotIn("<script>", doc)
        self.assertIn("https://example.com/a", doc)


class ChunkOpsTest(unittest.TestCase):
    def _rag_with(self):
        rag = mock.Mock()
        rag.doc_status = mock.Mock()
        rag.text_chunks = mock.Mock()
        rag.chunks_vdb = mock.Mock()
        rag.doc_status.get_by_id.return_value = {
            "chunks_list": ["doc-1-chunk-000", "doc-1-chunk-001"], "chunks_count": 2}
        rag.text_chunks.get_by_id.return_value = {
            "chunk_order_index": 0, "tokens": 10, "content": "原文",
            "doc_id": "doc-1", "full_doc_id": "doc-1"}
        return rag

    def test_delete_chunk(self):
        rag = self._rag_with()
        with mock.patch.object(L, "get_lightrag", return_value=rag), \
                mock.patch.object(L, "_ensure_initialized"), \
                mock.patch.object(L, "_run_on_worker", side_effect=lambda coro, **kw: coro):
            r = L.kb_delete_chunk("k1", "doc-1-chunk-000")
        self.assertTrue(r["ok"])
        self.assertEqual(r["chunk_key"], "doc-1-chunk-000")
        # doc_status 计数与列表更新，然后 KV 与向量删除
        upserted = rag.doc_status.upsert.call_args[0][0]
        self.assertNotIn("doc-1-chunk-000", upserted["doc-1"]["chunks_list"])
        self.assertEqual(upserted["doc-1"]["chunks_count"], 1)
        self.assertEqual(rag.text_chunks.delete.call_args[0][0], ["doc-1-chunk-000"])
        self.assertEqual(rag.chunks_vdb.delete.call_args[0][0], ["doc-1-chunk-000"])

    def test_edit_chunk(self):
        rag = self._rag_with()
        with mock.patch.object(L, "get_lightrag", return_value=rag), \
                mock.patch.object(L, "_ensure_initialized"), \
                mock.patch.object(L, "_run_on_worker", side_effect=lambda coro, **kw: coro):
            r = L.kb_edit_chunk("k1", "doc-1-chunk-000", "新内容", tags="重要, 精选")
        self.assertTrue(r["ok"])
        up = rag.text_chunks.upsert.call_args[0][0]["doc-1-chunk-000"]
        self.assertEqual(up["content"], "新内容")
        self.assertEqual(up["tags"], ["重要", "精选"])
        vrec = rag.chunks_vdb.upsert.call_args[0][0]["doc-1-chunk-000"]
        self.assertEqual(vrec["content"], "新内容")


class KBAdminAPITest(unittest.TestCase):
    def setUp(self):
        self.addCleanup(mock.patch.stopall)

    def test_dashboard_endpoint(self):
        from app.server.api.kb import dashboard
        with mock.patch.object(L, "kb_dashboard", return_value={"doc_total": 1}) as d:
            resp = asyncio.run(dashboard())
        self.assertEqual(resp["doc_total"], 1)

    def test_list_endpoint(self):
        from app.server.api.kb import list_kbs
        with mock.patch.object(L, "kb_list", return_value=[{"thread_id": "k1"}]) as k:
            resp = asyncio.run(list_kbs())
        self.assertEqual(resp["knowledge_bases"][0]["thread_id"], "k1")

    def test_create_endpoint(self):
        from app.server.api.kb import create, CreateReq
        with mock.patch.object(L, "kb_create", return_value={"ok": True, "thread_id": "k1"}) as c:
            resp = asyncio.run(create(CreateReq(name="n", description="d")))
        self.assertTrue(resp["ok"])
        c.assert_called_once_with(None, "n", "d")

    def test_config_get_set(self):
        from app.server.api.kb import get_config, set_config, ConfigSetReq
        with mock.patch.object(L, "kb_get_config", return_value={"top_k": 12}):
            resp = asyncio.run(get_config(thread_id="k1"))
        self.assertEqual(resp["config"]["top_k"], 12)
        with mock.patch.object(L, "kb_set_config", return_value={"top_k": 5}) as s:
            resp = asyncio.run(set_config(ConfigSetReq(thread_id="k1", config={"top_k": 5})))
        self.assertEqual(resp["config"]["top_k"], 5)
        self.assertEqual(s.call_args[0][1], {"top_k": 5})

    def test_feedback_endpoint(self):
        from app.server.api.kb import feedback as fb_endpoint, FeedbackReq
        with mock.patch.object(L, "kb_feedback", return_value={"ok": True, "id": 1}) as f:
            resp = asyncio.run(fb_endpoint(FeedbackReq(thread_id="k1", query="q", verdict="like")))
        self.assertTrue(resp["ok"])
        f.assert_called_once_with("k1", "q", "", "like", top_k=0, mode="", note="")

    def test_chunk_ops_endpoints(self):
        from app.server.api.kb import chunk_delete, chunk_edit, ChunkDeleteReq, ChunkEditReq
        with mock.patch.object(L, "kb_delete_chunk", return_value={"ok": True}) as d:
            resp = asyncio.run(chunk_delete(ChunkDeleteReq(thread_id="k1", chunk_key="doc-1-chunk-000")))
        self.assertTrue(resp["ok"])
        d.assert_called_once_with("k1", "doc-1-chunk-000")
        with mock.patch.object(L, "kb_edit_chunk", return_value={"ok": True}):
            resp = asyncio.run(chunk_edit(ChunkEditReq(thread_id="k1", chunk_key="doc-1-chunk-000", content="x")))
        self.assertTrue(resp["ok"])

    def test_answer_endpoint(self):
        from app.server.api.kb import answer, AnswerReq
        with mock.patch.object(L, "lightrag_query", return_value="回答内容") as q:
            resp = asyncio.run(answer(AnswerReq(thread_id="k1", q="问题", top_k=6, mode="hybrid")))
        self.assertEqual(resp["answer"], "回答内容")
        self.assertEqual(resp["mode"], "hybrid")
        q.assert_called_once_with("k1", "问题", top_k=6, mode="hybrid")

    def test_import_url_endpoint(self):
        from app.server.api.kb import import_url, ImportURLReq
        with mock.patch.object(L, "kb_import_url", return_value={"ok": True}) as i:
            resp = asyncio.run(import_url(ImportURLReq(thread_id="k1", url="https://x.y")))
        self.assertTrue(resp["ok"])
        i.assert_called_once_with("k1", "https://x.y")


if __name__ == "__main__":
    unittest.main()