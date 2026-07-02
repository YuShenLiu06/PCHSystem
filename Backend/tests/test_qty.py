"""format_qty 纯函数测试（B1，D-4）。

边界覆盖：盒/组/个三档切换 + :g 去尾零。
"""
import pytest

from app.core.qty import STACK, SHULKER, format_qty


@pytest.mark.parametrize(
    "n,expected",
    [
        (3456, "2盒"),
        (2000, "1.16盒"),
        (1728, "1盒"),
        (192, "3组"),
        (64, "1组"),
        (100, "1.56组"),
        (63, "63个"),
        (0, "0个"),
    ],
)
def test_format_qty_boundaries(n: int, expected: str) -> None:
    assert format_qty(n) == expected


def test_stack_and_shulker_constants() -> None:
    assert STACK == 64
    assert SHULKER == 27 * 64
    assert SHULKER == 1728
