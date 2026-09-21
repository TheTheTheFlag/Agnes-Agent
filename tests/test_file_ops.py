"""回归测试：read_file 二进制/图片防护 + 单次读取字符上限。

背景：WeCom 发图后 Agent 曾用 read_file"读"JPG——无换行导致整个二进制文件被当作
一行文本整读进 LLM 上下文（约 45 万 token），超出模型 524288 上下文上限报错。

运行：uv run python -m unittest tests.test_file_ops -v
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.tools import file_ops
from app.tools.file_ops import _READ_FILE_MAX_CHARS, _READ_TRUNC_NOTE


class ReadFileGuardTest(unittest.TestCase):
    def setUp(self):
        self._ws = tempfile.mkdtemp()
        patcher_ws = mock.patch.object(file_ops, "WORKSPACE_DIR", new=self._ws)
        patcher_admin = mock.patch.object(file_ops, "_is_admin_user", return_value=False)
        patcher_ws.start()
        patcher_admin.start()
        self.addCleanup(patcher_ws.stop)
        self.addCleanup(patcher_admin.stop)

    def _write(self, name: str, data: bytes) -> str:
        p = os.path.join(self._ws, name)
        with open(p, "wb") as f:
            f.write(data)
        return name

    def _read(self, name: str, **kw) -> dict:
        raw = file_ops.read_file.func(name, **kw)
        return json.loads(raw)

    def test_binary_image_rejected(self):
        """图片（含 NUL 的字节）拒绝读入上下文，仅返回元数据并指向图片理解技能。"""
        name = self._write("pic.jpg", b"\xff\xd8\xff\xe0\x00\x10JFIF data...")
        out = self._read(name)
        self.assertTrue(out.get("binary"))
        self.assertIn("图片", out.get("error", ""))
        self.assertIn("Image-Understanding", out.get("error", ""))
        self.assertNotIn("content", out)

    def test_binary_nonimage_rejected(self):
        """非图片二进制同样拒绝，内容不进上下文。"""
        name = self._write("blob.bin", bytes(range(256)) * 40)
        out = self._read(name)
        self.assertTrue(out.get("binary"))
        self.assertIn("二进制", out.get("error", ""))
        self.assertNotIn("content", out)

    def test_utf8_chinese_text_not_binary(self):
        """中文 UTF-8 文本（高位字节）不应被误判为二进制。"""
        name = self._write("note.txt", ("你好世界，这是一段中文文本。\n" * 20).encode("utf-8"))
        out = self._read(name)
        self.assertFalse(out.get("binary"))
        self.assertTrue(out.get("total_lines", 0) >= 20)
        self.assertIn("你好世界", out.get("content", ""))

    def test_text_read_ok(self):
        name = self._write("a.txt", "hello\nworld\n".encode("utf-8"))
        out = self._read(name)
        self.assertEqual(out["total_lines"], 2)
        self.assertIn("hello", out["content"])
        self.assertIn("world", out["content"])

    def test_single_giant_line_truncated_with_note(self):
        """无换行的大文件整行不被读爆：截断到上限并带标记（当年 357KB JPG 场景），
        但仍返回真实 size 供模型判断。"""
        name = self._write("big.txt", b"a" * 200000)
        out = self._read(name)
        self.assertTrue(out.get("truncated"))
        self.assertLessEqual(len(out["content"]),
                             _READ_FILE_MAX_CHARS + len(_READ_TRUNC_NOTE) + 64)
        self.assertIn("已截断", out["content"])
        self.assertEqual(out["size"], 200000)

    def test_multiline_truncation_resumes_by_offset(self):
        """多行文本在字符上限处截断后，可用 offset 继续读取后续行。"""
        lines = "".join(f"line-{i:04d}-" + "x" * 120 + "\n" for i in range(500))
        name = self._write("many.txt", lines.encode("utf-8"))
        out1 = self._read(name)
        self.assertTrue(out1.get("truncated"))
        self.assertLessEqual(len(out1["content"]),
                             _READ_FILE_MAX_CHARS + len(_READ_TRUNC_NOTE) + 64)
        # 从靠后的 offset 继续读取，应能看到文件尾行（验证 offset 分页仍然生效）
        last = json.loads(file_ops.read_file.func(name, offset=400))
        self.assertIn("line-0499", last["content"])
        # 小片读取（行内分页）不触发截断
        small = self._read(name, offset=0, limit=3)
        self.assertFalse(small.get("truncated"))
        self.assertEqual(small["total_lines"], 500)


if __name__ == "__main__":
    unittest.main()