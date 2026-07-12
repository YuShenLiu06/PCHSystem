"""format_qty 数量换算纯函数测试（issue #18，三端对齐）。

边界覆盖：盒/组/个三档切换 + :g 去尾零；与 Backend/tests/test_qty.py +
Frontend/src/utils/__tests__/qty.spec.ts 的用例镜像，外加大数/边界与 format_qty_safe 守护。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import tests  # noqa: F401,E402  触发 stubs 安装与 sys.path 配置

from pch_system.qty import STACK, SHULKER, format_qty, format_qty_safe  # noqa: E402


class FormatQtyTest(unittest.TestCase):
    """三档切换 + :g 去尾零（镜像后端/前端边界用例）。"""

    def test_盒档整除去尾零(self):
        # 3456 / 1728 = 2.0 → :g → "2"
        self.assertEqual(format_qty(3456), "2盒")

    def test_盒档非整除两位小数(self):
        # 2000 / 1728 = 1.1574... → round 2 位 = 1.16
        self.assertEqual(format_qty(2000), "1.16盒")

    def test_恰好一盒阈值(self):
        # >= 命中盒档（不是 >）
        self.assertEqual(format_qty(1728), "1盒")

    def test_盒档大整数倍去尾零(self):
        # 前端用例：8640 = 5 * 1728 → "5盒"
        self.assertEqual(format_qty(8640), "5盒")

    def test_组档整除去尾零(self):
        # 192 / 64 = 3.0 → "3"；192 < 1728 走组档
        self.assertEqual(format_qty(192), "3组")

    def test_恰好一组阈值(self):
        self.assertEqual(format_qty(64), "1组")

    def test_组档非整除两位小数不分解余数(self):
        # 100 / 64 = 1.5625 → 1.56（余数不显示，纯浮点）
        self.assertEqual(format_qty(100), "1.56组")

    def test_组档大整数倍去尾零(self):
        # 前端用例：256 = 4 * 64 → "4组"
        self.assertEqual(format_qty(256), "4组")

    def test_组档刚过阈值(self):
        # 65 / 64 = 1.0156 → 1.02
        self.assertEqual(format_qty(65), "1.02组")

    def test_个档原样整数(self):
        # < 64 不做除法
        self.assertEqual(format_qty(63), "63个")

    def test_零落个档(self):
        self.assertEqual(format_qty(0), "0个")

    def test_负数落个档原样(self):
        # 负数 < 64 走个档，原样带负号（DB schema ge=0 保证不出现，锁定行为）
        self.assertEqual(format_qty(-1), "-1个")

    def test_盒档刚过阈值仍为一盒(self):
        # 1729 / 1728 = 1.000578 → round 2 位 = 1.0 → "1盒"（与 1728 同输出）
        self.assertEqual(format_qty(1729), "1盒")


class FormatQtyLargeNumberTest(unittest.TestCase):
    """issue #18 核心诉求：大数换算后更易读。"""

    def test_万级数量换算为盒(self):
        # 12345 / 1728 = 7.145... → 7.14盒（issue 示例的大数场景）
        self.assertEqual(format_qty(12345), "7.14盒")

    def test_超大数量(self):
        # 100000 / 1728 = 57.87...
        self.assertEqual(format_qty(100000), f"{round(100000 / 1728, 2):g}盒")


class ConstantsTest(unittest.TestCase):
    def test_stack_and_shulker(self):
        self.assertEqual(STACK, 64)
        self.assertEqual(SHULKER, 27 * 64)
        self.assertEqual(SHULKER, 1728)


class FormatQtySafeTest(unittest.TestCase):
    """显示层包装：int 走换算，非 int（缺失/\"?\" 兜底）原样 str()。"""

    def test_int正常换算(self):
        self.assertEqual(format_qty_safe(64), "1组")
        self.assertEqual(format_qty_safe(0), "0个")
        self.assertEqual(format_qty_safe(12345), "7.14盒")

    def test_缺失兜底原样返回(self):
        # format_notification / deliver 回执用 payload.get(..., "?") 兜底
        self.assertEqual(format_qty_safe("?"), "?")

    def test_none原样转字符串(self):
        self.assertEqual(format_qty_safe(None), "None")

    def test_bool不算数量走str兜底(self):
        # bool 是 int 子类但语义非数量；走 str() 兜底而非换算（避免 True→"True个"）
        self.assertEqual(format_qty_safe(True), "True")
        self.assertEqual(format_qty_safe(False), "False")


if __name__ == "__main__":
    unittest.main()
