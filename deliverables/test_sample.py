"""示例测试文件（派蒙创建）。"""

import unittest


def add(a: int, b: int) -> int:
    return a + b


class TestSample(unittest.TestCase):
    def test_add(self):
        self.assertEqual(add(1, 2), 3)
        self.assertEqual(add(-1, 1), 0)


if __name__ == "__main__":
    unittest.main()
