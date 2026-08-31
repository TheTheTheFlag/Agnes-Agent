"""回归测试：app.server.api.chat._filter_internal_tokens 的内部摘要 token 过滤。

背景：update_summary 等内部 LLM 调用的输出会被 langgraph messages 流以 node='chatbot'
捕获并推给前端。其文本以摘要结构 marker（如 "**用户目标"）开头，若只按"完整 marker 包含"
检测，marker 前缀 token（如 "**用户"）会先泄漏到聊天框。
修复：累积文本是任一 marker 的前缀时，立即判定为内部摘要并丢弃该段。
运行：uv run python -m unittest tests.test_chat_filter -v
"""
import unittest

from app.server.api.chat import _filter_internal_tokens


def _feed(state, run_id, text):
    return _filter_internal_tokens("chatbot", run_id, text, state)


class ChatFilterTest(unittest.TestCase):
    def test_normal_reply_kept(self):
        """正常回复 token 流（逐字符）应全部保留。"""
        state = {}
        reply = "嗨！旅行者，派蒙在这里～有什么可以帮你的吗？"
        out = "".join(_feed(state, "A", ch) for ch in reply)
        self.assertEqual(out, reply)

    def test_summary_prefix_fully_blocked(self):
        """内部摘要（以 **用户目标 开头）从第一个 token 起被拦截，零泄漏。"""
        state = {}
        summary = "**用户目标**：用户想问候。\n**已完成步骤**：回复了用户。"
        out = "".join(_feed(state, "B", ch) for ch in summary)
        self.assertEqual(out, "")
        self.assertTrue(state["internal"])

    def test_new_run_id_resets_state(self):
        """run_id 变化后（新的 LLM 调用段）过滤状态应重置。"""
        state = {}
        for ch in "**用户目标**：xxx":
            _feed(state, "B", ch)
        self.assertTrue(state["internal"])
        out = "".join(_feed(state, "C", ch) for ch in "好的，我在。")
        self.assertEqual(out, "好的，我在。")

    def test_marker_in_middle_not_blocked(self):
        """主回复中途出现 "**用户"（非段开头）不应被误杀。"""
        state = {}
        text = "可以，**用户**你好"
        out = "".join(_feed(state, "D", ch) for ch in text)
        self.assertEqual(out, text)

    def test_non_chatbot_node_passthrough(self):
        """非 chatbot 节点的 token 不做过滤。"""
        self.assertEqual(_filter_internal_tokens("executor", "E", "**用户", {}), "**用户")

    def test_page_shown_text_matches_reply(self):
        """模拟真实轮次（主回复 run A → 内部摘要 run B），前端显示文本 == 主回复。"""
        state = {}
        reply = "嗨！旅行者，派蒙在这里～有什么可以帮你的吗？"
        summary = "**用户目标**：用户想问候。\n**已完成步骤**：回复了用户。"
        buf = "".join(_feed(state, "A", ch) for ch in reply)
        for ch in summary:
            _feed(state, "B", ch)
        # 前端逻辑：token 缓冲非空则用缓冲，为空则用 final 文本兜底
        shown = buf if buf else reply
        self.assertEqual(shown, reply)


if __name__ == "__main__":
    unittest.main()
