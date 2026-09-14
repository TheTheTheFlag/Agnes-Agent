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


class LightgraphQueryTest(unittest.TestCase):
    def setUp(self):
        self._patchers = [
            mock.patch.object(grt, "get_lightrag", return_value=object()),
            mock.patch.object(grt, "lightrag_query"),
        ]
        for p in self._patchers:
            p.start()
        self.addCleanup(self._stop)

    def _stop(self):
        for p in self._patchers:
            p.stop()

    def test_hit_returns_content(self):
        grt.lightrag_query.return_value = "Entity: HarmonyOS 5.0 supports DeepSeek V4"
        self.assertEqual(grt.lightgraph_query("tid", "HarmonyOS"),
                         "Entity: HarmonyOS 5.0 supports DeepSeek V4")

    def test_miss_returns_empty(self):
        grt.lightrag_query.return_value = ""
        self.assertEqual(grt.lightgraph_query("tid", "x"), "")

    def test_failure_degrades_to_hint(self):
        grt.lightrag_query.side_effect = ValueError("no endpoint")
        self.assertEqual(grt.lightgraph_query("tid", "x"),
                         "[GraphRAG 检索失败：no endpoint]")

    def test_l6_context_formatting_truncates(self):
        grt.lightrag_query.return_value = "\n\n".join([f"内容第{i}行，实测长度不算太长" for i in range(1, 30)])
        ctx = grt.get_l6_context("tid", "q", limit_chars=100)
        self.assertTrue(ctx.startswith("【GraphRAG（知识图谱检索）】"))
        # 每行 ≤ 100//3+1 字符、最多 10 行 + 标题行
        self.assertLessEqual(len(ctx), 34 * 10 + 20)
        self.assertLessEqual(ctx.count("\n"), 10)
        grt.lightrag_query.return_value = ""
        self.assertEqual(grt.get_l6_context("tid", "q"), "")


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