"""回归测试：app.graph.utils 消息结构安全工具（会话"复读兜底文案"修复）。

背景：某个会话出现"[模型未返回内容] 请检查模型配置或 API 额度。"无限刷屏。
根因（三层）：
  1. 跨轮历史组装硬切 state["messages"][-30:] / compress 逐条删头，会把
     assistant(tool_calls) 与其 ToolMessage 拆开 → 孤儿 tool 消息 → 网关 400
     或静默空返回（表现为空回复）；
  2. 空回复被 react_loop 替换成固定兜底文案后，经 builder 当普通 assistant 回答
     写回历史 → 历史被同一句刷满 → 每轮都空返回，自我恶化；
  3. 旧 ensure_token_limit 超预算分支引用模块级 llm（从未定义）→ NameError。
本测试覆盖修复函数：
  split_turn_blocks / sanitize_messages / trim_history_by_turns /
  strip_degenerate_tail / apply_reply_guard / prepare_context_messages

运行：uv run python -m unittest tests.test_message_safety -v
"""
import unittest

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, SystemMessage

from app.graph.utils import (
    split_turn_blocks, sanitize_messages, trim_history_by_turns,
    strip_degenerate_replies, apply_reply_guard, prepare_context_messages,
    STUCK_REPLY_HINT, is_degenerate_text,
)

DEG = "请检查模型配置或 API 额度。"


def H(t):
    return HumanMessage(content=t)


def A(t):
    return AIMessage(content=t)


def AT(cid, text=""):
    return AIMessage(content=text, tool_calls=[{"name": "tool_x", "args": {}, "id": cid}])


def T(cid):
    return ToolMessage(content="tool ok", tool_call_id=cid)


class SplitTurnsTest(unittest.TestCase):
    def test_user_boundary_blocks(self):
        """以用户消息为界分块：一个用户回合（含工具轮次与最终答复）不得被拆散。"""
        msgs = [H("q1"), AT("c1"), T("c1"), A("done1"), H("q2")]
        blocks = split_turn_blocks(msgs)
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0][-1].content, "done1")
        self.assertEqual(blocks[1][0].content, "q2")


class SanitizeTest(unittest.TestCase):
    def test_keeps_normal_history(self):
        msgs = [H("q1"), AT("c1"), T("c1"), A("done"), H("q2")]
        out = sanitize_messages(msgs)
        self.assertEqual(len(out), len(msgs))

    def test_drops_orphan_tool_message(self):
        """孤儿 ToolMessage（前方无对应 assistant tool_calls）应被丢弃。"""
        out = sanitize_messages([H("q1"), A("ok"), T("c1")])
        self.assertEqual(len(out), 2)
        self.assertEqual(out[-1].content, "ok")

    def test_drops_half_open_round_at_end(self):
        """结尾"assistant(tool_calls) 无 ToolMessage 收尾"的半截回合应被移除。"""
        out = sanitize_messages([H("q1"), AT("c1"), T("c1"), H("q2"), AT("c2")])
        self.assertEqual(len(out), 4)
        self.assertEqual(out[-1].content, "q2")

    def test_cleans_interior_broken_round(self):
        """中部"带 tool_calls 却无 ToolMessage"的残块（压缩产物）应被回删。"""
        out = sanitize_messages([H("q1"), AT("c1"), H("q2")])
        self.assertEqual(len(out), 2)
        self.assertEqual(out[-1].content, "q2")

    def test_drops_orphan_tool_after_user(self):
        """user 消息后夹着无主 tool 消息也应被丢弃。"""
        out = sanitize_messages([H("q1"), T("c0"), A("ok"), H("q2")])
        self.assertEqual(len(out), 3)


class TrimTurnsTest(unittest.TestCase):
    def test_keeps_recent_full_turns_with_tool_pair(self):
        """裁剪保留最近 N 个完整用户回合，工具调用配对不被拆散。"""
        msgs = [H("q1"), A("a1"), H("q2"), AT("c2"), T("c2"), A("a2"), H("q3"), A("a3")]
        out = trim_history_by_turns(msgs, max_tokens=10 ** 9, keep_recent=2)
        self.assertEqual(len(out), 6)
        self.assertEqual(out[0].content, "q2")
        # q2 的工具轮次成对保留
        self.assertEqual(out[1].tool_calls[0]["id"], "c2")
        self.assertEqual(out[2].tool_call_id, "c2")


class StripDegenerateRepliesTest(unittest.TestCase):
    def test_removes_repeated_degenerate_replies(self):
        msgs = [H("q1"), A("正常1"), H("q2"), A(DEG), H("q3"), A(DEG)]
        out = strip_degenerate_replies(msgs)
        # 退化兜底文案（含历史中部的）全部移除；正常对话内容保留，最近用户消息结尾
        self.assertEqual(len(out), 4)
        self.assertEqual(out[-1].content, "q3")
        self.assertEqual([m.content for m in out if hasattr(m, "content")],
                         ["q1", "正常1", "q2", "q3"])

    def test_keeps_single_normal_answer(self):
        msgs = [H("q1"), A("正常回答")]
        out = strip_degenerate_replies(msgs)
        self.assertEqual(len(out), 2)

    def test_removes_triple_repeat_normal_text(self):
        """同一正常文本重复 >=3 次视为刷屏，也清理（保守阈值）。"""
        msgs = [H("q1"), A("嗯嗯"), H("q2"), A("嗯嗯"), H("q3"), A("嗯嗯")]
        out = strip_degenerate_replies(msgs)
        self.assertEqual(len(out), 5)
        self.assertEqual(out[-1].content, "q3")


class ReplyGuardTest(unittest.TestCase):
    def test_normal_reply_never_suppressed(self):
        content, skip = apply_reply_guard([H("q"), A("正常回答")], "正常回答")
        self.assertEqual(content, "正常回答")
        self.assertFalse(skip)

    def test_first_degenerate_still_recorded(self):
        content, skip = apply_reply_guard([H("q"), A("正常回答")], DEG)
        self.assertEqual(content, DEG)
        self.assertFalse(skip)

    def test_second_degenerate_becomes_hint(self):
        content, skip = apply_reply_guard([H("q"), A(DEG)], DEG)
        self.assertEqual(content, STUCK_REPLY_HINT)
        self.assertFalse(skip)

    def test_third_degenerate_skipped(self):
        content, skip = apply_reply_guard([H("q"), A(STUCK_REPLY_HINT)], DEG)
        self.assertIsNone(content)
        self.assertTrue(skip)

    def test_different_error_message_still_shown(self):
        """与上一条不同的错误文案（如 LLM 调用失败: TypeError…）必须原样展示，不能吞。"""
        err = "LLM 调用失败: TypeError: APIStatusError.__init__() missing 2 required keyword-only arguments: 'response' and 'body' | 当前消息约 3300 tokens"
        content, skip = apply_reply_guard([H("q"), A(DEG)], err)
        self.assertEqual(content, err)
        self.assertFalse(skip)

    def test_different_fallback_variant_still_shown(self):
        """长/短两种兜底变体交替出现（内容不同）时也各自展示一次。"""
        long_deg = "[模型未返回内容] 请检查模型配置或 API 额度（网关可能已耗尽配额或返回空响应）。"
        content, skip = apply_reply_guard([H("q"), A(DEG)], long_deg)
        self.assertEqual(content, long_deg)
        self.assertFalse(skip)

    def test_marker_detection(self):
        self.assertTrue(is_degenerate_text("[模型未返回内容] " + DEG + "（网关可能已耗尽配额）。"))
        self.assertFalse(is_degenerate_text("一切正常"))


class PrepareContextTest(unittest.TestCase):
    def test_bad_history_recovers(self):
        """坏会话历史（尾部全是兜底句）经 prepare 后：system 开头、无退化尾巴、
        最近用户提问保留、序列结构合法。"""
        msgs = [H("q1"), AT("c1"), T("c1"), A("回答1"), H("q2"), A(DEG), H("q3"), A(DEG)]
        out = prepare_context_messages(msgs, "系统提示", keep_recent=30)
        self.assertIsInstance(out[0], SystemMessage)
        self.assertEqual(out[-1].content, "q3")
        # 无退化尾巴
        last_ai = [m for m in out if isinstance(m, AIMessage)]
        self.assertFalse(any(is_degenerate_text(m.content or "") for m in last_ai))


if __name__ == "__main__":
    unittest.main()
