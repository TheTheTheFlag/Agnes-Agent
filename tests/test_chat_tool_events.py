"""回归测试：工具调用事件链路（liveStatus 空白问题）。

背景：网关把 tool_call 增量以 dict 形态投递（首个 chunk 含 name/id，后续 chunk 只有 args
JSON 片段）；且 chatbot/executor 的工具在自定义 ReActLoop 里执行，ToolMessage 不进节点
state update，updates 分支收不到 tool 结束事件。chat.py 的修复：
  1. tool_chunk 事件按 dict/对象两种形态解析 name/args/index；
  2. 订阅 store 事件监听队列，把 on_tool_after 发的 add_event("tool_call") 转发为
     SSE 的 tool end 事件（带 output_preview）。

本测试用 FakeGraph 喂预定义流事件，走真实 chat_endpoint 端点，断言 SSE 输出。

运行：uv run python -m unittest tests.test_chat_tool_events -v
"""
import asyncio
import json
import unittest

from langchain_core.messages import AIMessage, AIMessageChunk

from app.server import config as _srv_cfg
from app.server import set_graph
from app.server.store import add_log_entry, add_event
from app.server.api.chat import chat_endpoint


class FakeGraph:
    """模拟 _srv_cfg._GRAPH：按预定义序列 yield 流事件，中途触发 add_event/add_log_entry
    （等价于 ReActLoop 的 on_tool_after 在流内部被调用）。"""

    def stream(self, inputs, config, **kwargs):
        tid = config["configurable"]["thread_id"]
        # 1) 节点开始
        yield ("updates", {"chatbot": {"messages": []}})
        # 2) 空 content 的 thinking chunk（真实网关先推 reasoning）
        yield ("messages", (AIMessageChunk(content=""), {"langgraph_node": "chatbot", "run_id": "r1"}))
        # 3) 工具调用首个 chunk：dict 形态，带 name/id、args 为空
        yield ("messages", (
            AIMessageChunk(content="", tool_call_chunks=[{"name": "list_my_recent_tasks", "id": "call_1", "args": "", "index": 0}]),
            {"langgraph_node": "chatbot", "run_id": "r1"},
        ))
        # 4) 参数 JSON 片段增量（dict 形态，无 name）
        for frag in ('{', '"limit"', ': 5', '}'):
            yield ("messages", (
                AIMessageChunk(content="", tool_call_chunks=[{"name": None, "id": None, "args": frag, "index": 0}]),
                {"langgraph_node": "chatbot", "run_id": "r1"},
            ))
        # 5) 工具执行完成：on_tool_after → add_log_entry + add_event（真实顺序：log 先、event 后）
        add_log_entry("info", "工具: list_my_recent_tasks",
                      {"params": {"limit": 5}, "result_preview": "已查到 2 条任务"})
        add_event("tool_call", {"name": "list_my_recent_tasks", "params": {"limit": 5}}, tid)
        # 6) 下一轮 LLM 流式文本（触发 _drain_listener 转发 tool end）
        yield ("messages", (AIMessageChunk(content="查到"), {"langgraph_node": "chatbot", "run_id": "r2"}))
        # 7) 节点结束：final 兜底文本
        yield ("updates", {"chatbot": {"messages": [AIMessage(content="已为你查到任务。")]}})
        return

    def get_state(self, config):
        """模拟 graph.get_state：返回带 values 的快照（chat.py 流结束后保存用）。"""
        tid = config["configurable"]["thread_id"]
        return type("StateSnapshot", (), {
            "values": {"thread_id": tid, "messages": [{"type": "human", "content": "hi"}]},
        })


async def _collect(payload: dict) -> list:
    """调用真实 chat_endpoint，消费流式响应，返回事件 dict 列表。"""
    resp = await chat_endpoint(payload)
    raw = "".join([chunk async for chunk in resp.body_iterator])
    events = []
    for line in raw.splitlines():
        if line.startswith("data: "):
            try:
                events.append(json.loads(line[6:]))
            except Exception:
                pass
    return events


class ChatToolEventsTest(unittest.TestCase):
    def setUp(self):
        set_graph(FakeGraph(), {"configurable": {"thread_id": "test-tid"}})

    def test_tool_chunk_carries_name_and_args_delta(self):
        """dict 形态的 tool_call_chunks 应解析出 name（首个 chunk）+ args 增量。"""
        events = asyncio.run(_collect({"message": "查任务", "thread_id": "test-tid"}))
        chunks = [e for e in events if e["step"] == "tool_chunk"]
        self.assertTrue(chunks, "应产生 tool_chunk 事件")
        first = chunks[0]
        self.assertEqual(first["name"], "list_my_recent_tasks")
        self.assertEqual(first["index"], 0)
        # 后续增量 chunk：name 为空但 args 片段透传
        deltas = "".join(e["args"] for e in chunks[1:] if not e["name"])
        self.assertEqual(deltas.replace(" ", ""), '{"limit":5}')

    def test_tool_end_emitted_via_listener_bridge(self):
        """监听桥应把 add_event("tool_call") 转发为 tool end 事件（含结果预览）。"""
        events = asyncio.run(_collect({"message": "查任务", "thread_id": "test-tid"}))
        ends = [e for e in events if e["step"] == "tool" and e.get("phase") == "end"]
        self.assertTrue(ends, "应产生 tool end 事件")
        self.assertEqual(ends[0]["name"], "list_my_recent_tasks")
        self.assertIn("limit", ends[0]["args"])
        self.assertEqual(ends[0]["output_preview"], "已查到 2 条任务")

    def test_listener_unsubscribed_after_stream(self):
        """请求结束后监听队列应从全局 _event_listeners 退订，避免泄漏。"""
        from app.server import store as _store
        before = len(_store._event_listeners)
        asyncio.run(_collect({"message": "查任务", "thread_id": "test-tid"}))
        after = len(_store._event_listeners)
        self.assertEqual(after, before)

    def test_state_snapshot_saved_after_stream(self):
        """流结束后应保存完整 graph state 快照（State 面板 messages 不再恒空）。"""
        from app.server import store as _store
        _store._state_snapshots.clear()
        asyncio.run(_collect({"message": "查任务", "thread_id": "test-tid"}))
        snap = _store._state_snapshots.get("test-tid")
        self.assertTrue(snap, "应写入 test-tid 的 state 快照")
        self.assertTrue(snap.get("messages"), "快照中 messages 应非空")


if __name__ == "__main__":
    unittest.main()
