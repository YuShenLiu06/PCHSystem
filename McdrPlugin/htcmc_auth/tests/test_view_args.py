"""view_args.py 单测（纯 Python，按文件路径加载，绕过 mcdreforged 依赖）。

镜像 test_scanner.py 的加载方式。覆盖 paginate_rows（分页/钳位/空表）与
parse_view_args（-p/--page、-s/--search 多词、裸页码、非法 token）。
"""
import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "_view_args_under_test",
    Path(__file__).resolve().parent.parent / "htcmc_auth" / "view_args.py",
)
view_args = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(view_args)

paginate_rows = view_args.paginate_rows
parse_view_args = view_args.parse_view_args
VIEW_PAGE_SIZE = view_args.VIEW_PAGE_SIZE


# === paginate_rows ===


def test_paginate_single_page():
    rows = list(range(5))
    page_rows, total_pages, total = paginate_rows(rows, 1)
    assert page_rows == [0, 1, 2, 3, 4]
    assert total_pages == 1
    assert total == 5


def test_paginate_multi_page_basic():
    rows = list(range(75))  # VIEW_PAGE_SIZE=30 → 3 页
    p1, tp, total = paginate_rows(rows, 1)
    p2, _, _ = paginate_rows(rows, 2)
    p3, _, _ = paginate_rows(rows, 3)
    assert tp == 3 and total == 75
    assert len(p1) == 30 and p1[0] == 0 and p1[-1] == 29
    assert len(p2) == 30 and p2[0] == 30
    assert len(p3) == 15 and p3[0] == 60 and p3[-1] == 74


def test_paginate_page_clamped_high():
    rows = list(range(50))  # 2 页
    page_rows, tp, _ = paginate_rows(rows, 99)  # 越界 → 钳到末页
    assert tp == 2
    assert len(page_rows) == 20  # 末页 20 行
    assert page_rows[0] == 30


def test_paginate_page_clamped_low():
    rows = list(range(50))
    page_rows, _, _ = paginate_rows(rows, 0)  # 钳到第 1 页
    assert page_rows[0] == 0 and len(page_rows) == 30


def test_paginate_empty_rows_one_page():
    page_rows, tp, total = paginate_rows([], 1)
    assert page_rows == [] and tp == 1 and total == 0


def test_paginate_custom_size():
    rows = list(range(10))
    p1, tp, _ = paginate_rows(rows, 1, size=4)
    assert tp == 3  # ceil(10/4)=3
    assert p1 == [0, 1, 2, 3]


# === parse_view_args ===


def test_parse_empty():
    assert parse_view_args([]) == (1, None, None)


def test_parse_page_short_and_long():
    assert parse_view_args(["-p", "3"]) == (3, None, None)
    assert parse_view_args(["--page", "7"]) == (7, None, None)


def test_parse_bare_integer_is_page():
    assert parse_view_args(["2"]) == (2, None, None)


def test_parse_page_clamped_to_min_one():
    # 负数 / 0 钳到 1（max(1, ...)）
    assert parse_view_args(["-p", "0"])[0] == 1
    assert parse_view_args(["-p", "-3"])[0] == 1


def test_parse_search_single():
    assert parse_view_args(["-s", "stone"]) == (1, "stone", None)
    assert parse_view_args(["--search", "stone"]) == (1, "stone", None)


def test_parse_search_multi_word_greedy():
    # 关键词贪婪取到下一旗标，空格连接
    assert parse_view_args(["-s", "圆石", "石头"]) == (1, "圆石 石头", None)


def test_parse_search_then_page():
    page, search, unknown = parse_view_args(["-s", "stone", "-p", "2"])
    assert (page, search, unknown) == (2, "stone", None)


def test_parse_page_then_search():
    page, search, unknown = parse_view_args(["--page", "3", "--search", "oak", "log"])
    assert (page, search, unknown) == (3, "oak log", None)


def test_parse_bare_page_then_search():
    page, search, unknown = parse_view_args(["2", "-s", "iron"])
    assert (page, search, unknown) == (2, "iron", None)


def test_parse_unknown_token():
    page, search, unknown = parse_view_args(["--bogus"])
    assert unknown == "--bogus"
    assert page is None and search is None


def test_parse_page_missing_value():
    _, _, unknown = parse_view_args(["-p"])  # -p 后缺页码
    assert unknown == "-p"


def test_parse_search_missing_value():
    _, _, unknown = parse_view_args(["-s"])  # -s 后缺关键词
    assert unknown == "-s"
