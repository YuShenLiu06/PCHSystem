"""!!PCH sheet view 的分页 + 参数解析纯函数（仅依赖标准库）。

刻意独立成模块（与 ``scanner.py`` 同范式）：不含 mcdreforged 依赖，便于单测按文件路径加载
（绕过 ``__init__.py`` 的 mcdreforged import）。由 ``sheet_commands.py`` 导入使用。
"""
from __future__ import annotations

# view 单页最多展示材料行数（远低于 MC 客户端聊天框 ~126 行截断阈值，issue #17）
VIEW_PAGE_SIZE = 30


def paginate_rows(rows: list, page: int, size: int = VIEW_PAGE_SIZE):
    """把 rows 切成第 page 页。返回 (page_rows, total_pages, total)。

    page 钳到 ``[1, total_pages]``；空表 ``total_pages=1``（避免 0 页导致按钮/计算异常）。
    返回新切片，不改入参。
    """
    total = len(rows)
    total_pages = max(1, (total + size - 1) // size)
    page = max(1, min(page, total_pages))
    start = (page - 1) * size
    return rows[start:start + size], total_pages, total


def parse_view_args(tokens):
    """解析 view 参数 token 列表 → (page, search, unknown)。

    支持：``-p N`` / ``--page N`` / 裸整数（页码）；``-s <kw>`` / ``--search <kw>``
    （关键词贪婪取到下一旗标，空格连接）。
    返回 ``(page: int, search: str|None, unknown: str|None)``；``unknown`` 非 None = 非法 token。
    默认 ``page=1, search=None``。
    """
    page = 1
    search = None
    i = 0
    n = len(tokens)
    while i < n:
        t = tokens[i]
        if t in ("-p", "--page"):
            i += 1
            if i < n and tokens[i].lstrip("-").isdigit():
                page = max(1, int(tokens[i]))
            else:
                return None, None, t  # -p 后缺页码
        elif t in ("-s", "--search"):
            i += 1
            terms = []
            while i < n and not tokens[i].startswith("-"):
                terms.append(tokens[i])
                i += 1
            if not terms:
                return None, None, t  # -s 后缺关键词
            search = " ".join(terms)
            continue  # 内层 while 已前进到下一旗标 / 结尾，跳过底部 i += 1
        elif t.lstrip("-").isdigit():
            page = max(1, int(t))  # 裸整数 = 页码（便捷写法 !!PCH sheet view <id> 2）
        else:
            return None, None, t  # 非法 token
        i += 1
    return page, search, None
