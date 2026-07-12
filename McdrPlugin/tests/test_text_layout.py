"""text_layout 纯函数单测：像素宽度估算 + 对齐填充。

不依赖 MCDR（text_layout 无 RText 依赖），但沿用 tests 包约定（import tests 触发 sys.path 配置）。
期望值依据见 text_layout.py docstring 的 ASCII / CJK 宽度表。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import tests  # noqa: F401,E402  触发 sys.path 配置（pch_system 顶层入路径）

from pch_system.text_layout import (  # noqa: E402
    CHAT_LINE_PX,
    CJK_ADVANCE_PX,
    SPACE_ADVANCE_PX,
    center_leading,
    make_padding,
    right_align_suffix,
    text_width_px,
)


class TextWidthPxTest(unittest.TestCase):
    def test_ascii_lowercase(self):
        # 多数小写 = 6；窄字符例外
        self.assertEqual(text_width_px("a"), 6)
        self.assertEqual(text_width_px("b"), 6)
        self.assertEqual(text_width_px("n"), 6)
        self.assertEqual(text_width_px("i"), 2)
        self.assertEqual(text_width_px("l"), 3)
        self.assertEqual(text_width_px("t"), 4)
        self.assertEqual(text_width_px("c"), 5)
        self.assertEqual(text_width_px("f"), 5)
        self.assertEqual(text_width_px("k"), 5)
        self.assertEqual(text_width_px("r"), 5)

    def test_ascii_uppercase(self):
        self.assertEqual(text_width_px("A"), 6)
        self.assertEqual(text_width_px("M"), 6)
        self.assertEqual(text_width_px("W"), 6)
        self.assertEqual(text_width_px("I"), 4)  # 大写唯一例外

    def test_digits(self):
        for d in "0123456789":
            self.assertEqual(text_width_px(d), 6, f"digit {d}")

    def test_punctuation(self):
        self.assertEqual(text_width_px("!"), 2)
        self.assertEqual(text_width_px('"'), 5)
        self.assertEqual(text_width_px("["), 4)
        self.assertEqual(text_width_px("]"), 4)
        self.assertEqual(text_width_px("("), 4)
        self.assertEqual(text_width_px(")"), 4)
        self.assertEqual(text_width_px(":"), 2)
        self.assertEqual(text_width_px(";"), 2)
        self.assertEqual(text_width_px(","), 2)
        self.assertEqual(text_width_px("."), 2)
        self.assertEqual(text_width_px("@"), 7)  # 最宽 ASCII

    def test_space(self):
        self.assertEqual(text_width_px(" "), SPACE_ADVANCE_PX)
        self.assertEqual(text_width_px("    "), SPACE_ADVANCE_PX * 4)

    def test_section_sign_zero_width(self):
        # §码整体 0 宽
        self.assertEqual(text_width_px("§a"), 0)
        self.assertEqual(text_width_px("§l"), 0)
        self.assertEqual(text_width_px("§r"), 0)
        self.assertEqual(text_width_px("§7"), 0)
        # §码不影响后续字符宽度
        self.assertEqual(text_width_px("§7ab"), 12)  # §7(0) + a(6) + b(6)

    def test_bold_adds_one_per_char(self):
        # §labc：a(6+1) + b(6+1) + c(5+1) = 20
        self.assertEqual(text_width_px("§labc"), 20)

    def test_color_resets_bold(self):
        # §l甲§r乙：甲 bold(9+1) + §r 关 bold + 乙(9) = 19
        self.assertEqual(text_width_px("§l甲§r乙"), 19)

    def test_color_code_also_resets_bold(self):
        # §l 甲 §a 乙：§a 是颜色码也重置 bold → 甲(10) + 乙(9) = 19
        self.assertEqual(text_width_px("§l甲§a乙"), 19)

    def test_cjk_single(self):
        self.assertEqual(text_width_px("泥"), CJK_ADVANCE_PX)
        self.assertEqual(text_width_px("方"), CJK_ADVANCE_PX)

    def test_cjk_word(self):
        self.assertEqual(text_width_px("泥土"), CJK_ADVANCE_PX * 2)  # 18

    def test_cjk_with_brackets(self):
        # [交付] = [(4) + 交(9) + 付(9) + ](4) = 26
        self.assertEqual(text_width_px("[交付]"), 26)

    def test_mixed_row(self):
        # 真实行：§a#120 §f泥土 §7[lock]
        # §a(0) + #(6)+1(6)+2(6)+0(6)+空格(4) + §f(0)+泥(9)+土(9)+空格(4)
        # + §7(0)+[(4)+l(3)+o(6)+c(5)+k(5)+](4) = 77
        self.assertEqual(text_width_px("§a#120 §f泥土 §7[lock]"), 77)

    def test_non_ascii_fallback(self):
        # 非 CJK 的非 ASCII（如拉丁 é）走 else 分支按 CJK_ADVANCE_PX 估算
        self.assertEqual(text_width_px("é"), CJK_ADVANCE_PX)

    def test_empty_string(self):
        self.assertEqual(text_width_px(""), 0)


class MakePaddingTest(unittest.TestCase):
    def test_exact_division(self):
        self.assertEqual(make_padding(8), "  ")   # 8/4 = 2

    def test_with_remainder_floors(self):
        # 向下取整：10/4 = 2（少 2px，确保不超宽）
        self.assertEqual(make_padding(10), "  ")

    def test_zero(self):
        self.assertEqual(make_padding(0), "")

    def test_negative_fallback(self):
        self.assertEqual(make_padding(-5), "")
        self.assertEqual(make_padding(-100), "")

    def test_small_positive_below_one_space(self):
        # 3px < 一个空格(4px) → 0 空格
        self.assertEqual(make_padding(3), "")


class RightAlignSuffixTest(unittest.TestCase):
    def test_normal_returns_padding(self):
        # a(6)+b(6)=12, c(6)+d(6)=12, gap=50-24=26 → 26//4=6 空格
        self.assertEqual(right_align_suffix("ab", "cd", target_px=50), "      ")

    def test_too_long_falls_back_to_double_space(self):
        # prefix 远超 target → gap < 0 < min_gap → 兜底双空格
        long_prefix = "a" * 100
        self.assertEqual(right_align_suffix(long_prefix, "x", target_px=50), "  ")

    def test_min_gap_boundary(self):
        # ab=12, cd=12, gap=24；target_px=36 → gap=12 ≥ 8 → make_padding(12)='   '
        self.assertEqual(right_align_suffix("ab", "cd", target_px=36), "   ")
        # target_px=31 → gap=7 < 8 → 兜底双空格
        self.assertEqual(right_align_suffix("ab", "cd", target_px=31), "  ")

    def test_custom_min_gap(self):
        # min_gap_px=20 → 即使有 12px gap 也兜底
        self.assertEqual(
            right_align_suffix("ab", "cd", target_px=36, min_gap_px=20), "  "
        )

    def test_default_target_is_chat_line(self):
        # 默认 target_px=CHAT_LINE_PX(320)：ab=12, x=6, gap=302 → 75 空格
        self.assertEqual(len(right_align_suffix("ab", "x")), 75)


class CenterLeadingTest(unittest.TestCase):
    def test_normal(self):
        # ab=12, target=20, gap=8, gap//2=4 → make_padding(4)=' '
        self.assertEqual(center_leading("ab", target_px=20), " ")

    def test_too_wide_returns_empty(self):
        # abcdefghij=60 > 20 → gap<=0 → ""
        self.assertEqual(center_leading("abcdefghij", target_px=20), "")

    def test_odd_gap(self):
        # a=6, target=15, gap=9, gap//2=4 → ' '
        self.assertEqual(center_leading("a", target_px=15), " ")

    def test_default_target(self):
        # [一键提交] = 4+9*4+4 = 44, target=320, gap=276, gap//2=138, 138//4=34 空格
        self.assertEqual(len(center_leading("[一键提交]")), 34)


if __name__ == "__main__":
    unittest.main()
