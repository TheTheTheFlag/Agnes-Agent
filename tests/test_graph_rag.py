"""回归测试：GraphRAG（L6）层——三元组解析 / record_graph / lightgraph_query / API 端点。

覆盖：
  1. _triage_triplets_json 对各种输入（JSON 数组、文本行、单行对象、垃圾）的归一；
  2. record_graph 对非法输入/空列表/写入失败的降级与成功计数；
  3. lightgraph_query 在命中/未命中/异常时的返回值约定；
  4. get_l6_context 的摘要格式化与截断；
  5. /api/graph/nodes、/api/graph/edges 的数据结构与空图降级。

全部走 mock，不触网、不初始化真实 LightRAG 实例。
"""
import asyncio
import time
import unittest
from unittest import mock

from app.memory import graph_rag_tool as grt


class TripletTriageTest(unittest.TestCase):
    def test_valid_json_array(self):
        raw = '[{"head":"HarmonyOS 5.0","rel":"supports","tail":"DeepSeek V4"}]'
        out = grt._triage_triplets_json(raw)
        self.assertEqual(out, [{"head": "HarmonyOS 5.0", "rel": "supports", "tail": "DeepSeek V4"}])

    def test_text_lines(self):
        raw = "HarmonyOS 5.0 - supports - DeepSeek V4\nNata Company,发布,2026-08-28"
        out = grt._triage_triplets_json(raw)
        self.assertEqual(out[0], {"head": "HarmonyOS 5.0", "rel": "supports", "tail": "DeepSeek V4"})
        self.assertEqual(out[1], {"head": "Nata Company", "rel": "发布", "tail": "2026-08-28"})
        self.assertEqual(len(out), 2)

    def test_single_json_object_line(self):
        out = grt._triage_triplets_json('{"head":"A","rel":"B","tail":"C"}')
        self.assertEqual(out, [{"head": "A", "rel": "B", "tail": "C"}])

    def test_garbage_returns_empty(self):
        self.assertEqual(grt._triage_triplets_json("这是没法解析的内容"), [])
        self.assertEqual(grt._triage_triplets_json(""), [])
        self.assertEqual(grt._triage_triplets_json(None), [])


class RecordGraphTest(unittest.TestCase):
    def setUp(self):
        self._patchers = [
            mock.patch.object(grt, "get_lightrag", return_value=object()),
            mock.patch.object(grt, "lightrag_insert"),
        ]
        for p in self._patchers:
            p.start()
        self.addCleanup(self._stop)

    def _stop(self):
        for p in self._patchers:
            p.stop()

    def test_invalid_json_degrades(self):
        # 以 [ 开头但解析失败时按"无有效三元组"降级（不会抛错阻塞主流程）
        self.assertEqual(grt.record_graph("tid", "[{bad json"), "无有效三元组")

    def test_empty_triplets(self):
        self.assertEqual(grt.record_graph("tid", "[]"), "无有效三元组")
        self.assertIsInstance(grt.record_graph("tid", None), str)

    def test_skips_incomplete_and_inserts(self):
        raw = ('[{"head":"A","rel":"r","tail":"B"},'
               '{"head":"","rel":"r","tail":"B"},'
               '{"head":"C","rel":"","tail":"D"}]')
        self.assertEqual(grt.record_graph("tid", raw), "已记录 1 条实体关系")
        grt.lightrag_insert.assert_called_once()
        docs = grt.lightrag_insert.call_args[0][1]
        self.assertEqual(docs, ["A r B (来源: user)"])

    def test_insert_failure_degrades(self):
        grt.lightrag_insert.side_effect = RuntimeError("boom")
        self.assertEqual(grt.record_graph("tid", '[{"head":"A","rel":"r","tail":"B"}]'),
                         "写入失败：boom")


class NamespaceScopeTest(unittest.TestCase):
    """检索范围以勾选列表为准：勾了什么查什么，什么都没勾就什么都不查。"""

    def setUp(self):
        from app.memory import ligraphrag_adapter as LA
        self.LA = LA
        self.addCleanup(lambda: LA.set_current_kbs(None))

    def test_explicit_scope_does_not_prepend_global(self):
        self.assertEqual(grt._namespaces("tid", ["kb-1"]), ["kb-1"])

    def test_global_token_resolves_to_session_ns(self):
        self.assertEqual(grt._namespaces("tid", ["__global__"]), ["__global__"])

    def test_nothing_selected_returns_empty(self):
        self.LA.set_current_kbs([])
        self.assertEqual(grt._namespaces("tid", None), [])
        self.assertEqual(grt._namespaces("tid", []), [])

    def test_falls_back_to_current_kbs_when_none(self):
        self.LA.set_current_kbs(["kb-7"])
        self.assertEqual(grt._namespaces("tid", None), ["kb-7"])

    def test_dedup_preserves_order(self):
        self.assertEqual(grt._namespaces("tid", ["kb-2", "kb-1", "kb-2"]), ["kb-2", "kb-1"])


class GraphRelevanceThresholdTest(unittest.TestCase):
    """图谱实体/关系向量库使用更高相似度阈值，过滤跨领域无关召回。"""

    def setUp(self):
        from app.memory import ligraphrag_adapter as LA
        self.LA = LA

    def _fake_rag(self):
        class _V:
            def __init__(self, t):
                self.cosine_better_than_threshold = t

        class _R:
            def __init__(self):
                self.entities_vdb = _V(0.2)
                self.relationships_vdb = _V(0.2)
                self.chunks_vdb = _V(0.2)

        return _R()

    def test_raises_entity_and_relation_thresholds(self):
        rag = self._fake_rag()
        self.LA._apply_graph_relevance_threshold(rag)
        self.assertEqual(rag.entities_vdb.cosine_better_than_threshold, 0.5)
        self.assertEqual(rag.relationships_vdb.cosine_better_than_threshold, 0.5)

    def test_chunks_threshold_untouched(self):
        rag = self._fake_rag()
        self.LA._apply_graph_relevance_threshold(rag)
        self.assertEqual(rag.chunks_vdb.cosine_better_than_threshold, 0.2)

    def test_env_override(self):
        rag = self._fake_rag()
        with mock.patch.dict("os.environ", {"GRAPH_COSINE_THRESHOLD": "0.62"}):
            self.LA._apply_graph_relevance_threshold(rag)
        self.assertEqual(rag.entities_vdb.cosine_better_than_threshold, 0.62)

    def test_tolerates_missing_vdbs(self):
        class _R:
            pass

        self.LA._apply_graph_relevance_threshold(_R())  # 不应抛错


class LightgraphQueryTest(unittest.TestCase):
    def setUp(self):
        from app.memory import ligraphrag_adapter as LA
        self.LA = LA
        self._patchers = [
            mock.patch.object(grt, "get_lightrag", return_value=object()),
            mock.patch.object(grt, "lightrag_query"),
        ]
        for p in self._patchers:
            p.start()
        self.addCleanup(self._stop)
        self.addCleanup(lambda: LA.set_current_kbs(None))

    def _stop(self):
        for p in self._patchers:
            p.stop()

    def test_hit_returns_content(self):
        self.LA.set_current_kbs(["__global__"])
        grt.lightrag_query.return_value = "Entity: HarmonyOS 5.0 supports DeepSeek V4"
        self.assertEqual(grt.lightgraph_query("tid", "HarmonyOS"),
                         "【全局图谱】\nEntity: HarmonyOS 5.0 supports DeepSeek V4")

    def test_miss_returns_empty(self):
        self.LA.set_current_kbs(["__global__"])
        grt.lightrag_query.return_value = ""
        self.assertEqual(grt.lightgraph_query("tid", "x"), "")

    def test_failure_degrades_to_hint(self):
        self.LA.set_current_kbs(["__global__"])
        grt.lightrag_query.side_effect = ValueError("no endpoint")
        self.assertEqual(grt.lightgraph_query("tid", "x"),
                         "【__global__】检索失败：no endpoint")

    def test_nothing_selected_skips_all_namespaces(self):
        # 什么都没勾选 → 一个命名空间都不查
        self.LA.set_current_kbs([])
        self.assertEqual(grt.lightgraph_query("tid", "x"), "")
        self.assertEqual(grt.get_l6_context("tid", "x"), "")
        grt.lightrag_query.assert_not_called()

    def test_only_checked_kb_excludes_global(self):
        # 只勾选一个知识库 → 只查它，不再自动追加全局对话图谱
        self.LA.set_current_kbs(["kb-1"])
        grt.lightrag_query.return_value = "库命中"
        out = grt.lightgraph_query("tid", "q")
        self.assertEqual(out, "【kb-1】\n库命中")
        grt.lightrag_query.assert_called_once_with("tid", "q", top_k=12, ns="kb-1")

    def test_multi_namespace_merges_results(self):
        # 勾选后各命名空间独立查询并带前缀合并；单个失败不阻塞
        grt.lightrag_query.side_effect = ["全局命中", "库命中", "", ValueError("boom")]
        self.LA.set_current_kbs(["__global__", "kb-1", "kb-2", "kb-3"])
        out = grt.lightgraph_query("tid", "q", top_k=4)
        self.assertIn("【全局图谱】\n全局命中", out)
        self.assertIn("【kb-1】\n库命中", out)
        # kb-2 空命中跳过，kb-3 失败降级前缀标注
        self.assertIn("【kb-3】检索失败：boom", out)

    def test_l6_context_formatting_truncates(self):
        self.LA.set_current_kbs(["__global__"])
        grt.lightrag_query.return_value = "\n\n".join([f"内容第{i}行，实测长度不算太长" for i in range(1, 30)])
        ctx = grt.get_l6_context("tid", "q", limit_chars=100)
        self.assertTrue(ctx.startswith("【GraphRAG（知识图谱检索）】"))
        # 每行 ≤ 100//3+1 字符、最多 10 行 + 标题行
        self.assertLessEqual(len(ctx), 34 * 10 + 20)
        self.assertLessEqual(ctx.count("\n"), 10)
        grt.lightrag_query.return_value = ""
        self.assertEqual(grt.get_l6_context("tid", "q"), "")


class GraphToolsTest(unittest.TestCase):
    """LLM 侧工具：record_graph / lightgraph_query 的线程定位与转发。"""

    def setUp(self):
        self._thread_patch = mock.patch("app.tools.graph_rag_tools._resolve_thread")
        self._resolve = self._thread_patch.start()
        self._patchers = [
            mock.patch.object(grt, "get_lightrag", return_value=object()),
            mock.patch.object(grt, "lightrag_insert"),
            mock.patch.object(grt, "lightrag_query"),
        ]
        for p in self._patchers:
            p.start()
        self.addCleanup(mock.patch.stopall)

    def test_tools_registered(self):
        from app.tools import tools
        names = {t.name for t in tools}
        self.assertIn("record_graph", names)
        self.assertIn("lightgraph_query", names)

    def test_lightgraph_query_without_thread(self):
        from app.tools.graph_rag_tools import lightgraph_query
        self._resolve.return_value = ""
        out = lightgraph_query.invoke({"query": "HarmonyOS 支持谁"})
        self.assertIn("未定位到当前会话", out)
        grt.lightrag_query.assert_not_called()

    def test_record_graph_without_thread(self):
        from app.tools.graph_rag_tools import record_graph
        self._resolve.return_value = ""
        out = record_graph.invoke({"triplets_json": "[]"})
        self.assertIn("未定位到当前会话", out)
        grt.lightrag_insert.assert_not_called()

    def test_lightgraph_query_forwards_thread(self):
        from app.tools.graph_rag_tools import lightgraph_query
        from app.memory import ligraphrag_adapter as LA
        self._resolve.return_value = "t1"
        LA.set_current_kbs(["__global__"])
        try:
            grt.lightrag_query.return_value = "Entity: A supports B"
            out = lightgraph_query.invoke({"query": "q1", "top_k": 6})
        finally:
            LA.set_current_kbs(None)
        self.assertEqual(out, "【全局图谱】\nEntity: A supports B")
        grt.lightrag_query.assert_called_once_with("t1", "q1", top_k=6, ns="__global__")

    def test_lightgraph_query_without_selection_skips(self):
        from app.tools.graph_rag_tools import lightgraph_query
        from app.memory import ligraphrag_adapter as LA
        self._resolve.return_value = "t1"
        LA.set_current_kbs([])
        try:
            out = lightgraph_query.invoke({"query": "q1", "top_k": 6})
        finally:
            LA.set_current_kbs(None)
        self.assertEqual(out, "")
        grt.lightrag_query.assert_not_called()

    def test_lightgraph_query_forwards_checked_kbs(self):
        from app.tools.graph_rag_tools import lightgraph_query
        from app.memory import ligraphrag_adapter as LA
        self._resolve.return_value = "t1"
        LA.set_current_kbs(["__global__", "kb-9"])
        try:
            grt.lightrag_query.side_effect = ["G", "K"]
            out = lightgraph_query.invoke({"query": "q1", "top_k": 6})
        finally:
            LA.set_current_kbs(None)
        self.assertIn("【全局图谱】\nG", out)
        self.assertIn("【kb-9】\nK", out)

    def test_record_graph_forwards_thread(self):
        from app.tools.graph_rag_tools import record_graph
        self._resolve.return_value = "t1"
        out = record_graph.invoke({"triplets_json": '[{"head":"A","rel":"r","tail":"B"}]', "source": "llm_extracted"})
        self.assertEqual(out, "已记录 1 条实体关系")
        docs = grt.lightrag_insert.call_args[0][1]
        self.assertEqual(docs, ["A r B (来源: llm_extracted)"])
        self.assertEqual(grt.lightrag_insert.call_args[0][0], "t1")

    def test_feed_turn_async_writes_in_background(self):
        grt._feed_turn_async("t2", "用户问 HarmonyOS 5.0 是否支持 DeepSeek", "是的，支持。")
        deadline = time.time() + 3
        while not grt.lightrag_insert.called and time.time() < deadline:
            time.sleep(0.05)
        self.assertTrue(grt.lightrag_insert.called, "后台喂图线程应完成一次 ainsert 调用")
        args = grt.lightrag_insert.call_args[0]
        self.assertEqual(args[0], "t2")
        self.assertIn("HarmonyOS 5.0", args[1][0])

    def test_feed_turn_async_skips_short_text(self):
        grt._feed_turn_async("t2", "hi", "")
        time.sleep(0.1)
        grt.lightrag_insert.assert_not_called()


class GraphAPITest(unittest.TestCase):
    """直接调用 handler 函数（绕过 HTTP/auth 中间件），mock 掉 graph_snapshot。"""

    def setUp(self):
        self._snap = mock.patch("app.memory.ligraphrag_adapter.graph_snapshot").start()
        self._snap.return_value = {"nodes": [], "edges": []}
        self.addCleanup(mock.patch.stopall)

    def test_nodes_endpoint_shape(self):
        from app.server.api.graph import graph_nodes
        self._snap.return_value = {
            "nodes": [{"id": "A", "name": "A", "kind": "Entity", "attrs": "{}"}],
            "edges": [],
        }
        resp = asyncio.run(graph_nodes(thread_id="tid"))
        self.assertEqual(resp["thread_id"], "tid")
        self.assertEqual(resp["node_count"], 1)
        self.assertEqual(resp["nodes"][0]["name"], "A")

    def test_edges_endpoint_shape(self):
        from app.server.api.graph import graph_edges
        self._snap.return_value = {
            "nodes": [],
            "edges": [{"id": "A|B", "from_id": "A", "to_id": "B", "label": "r", "attrs": "{}"}],
        }
        resp = asyncio.run(graph_edges(thread_id="tid"))
        self.assertEqual(resp["edge_count"], 1)
        self.assertEqual(resp["edges"][0]["from_id"], "A")

    def test_empty_graph_degrades(self):
        from app.server.api.graph import graph_nodes
        self._snap.return_value = {"nodes": [], "edges": []}
        resp = asyncio.run(graph_nodes(thread_id="tid"))
        self.assertEqual(resp["node_count"], 0)
        self.assertEqual(resp["nodes"], [])


if __name__ == "__main__":
    unittest.main()