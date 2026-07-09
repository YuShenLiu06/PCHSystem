"""format_qty 数量换算纯函数（issue #18，与前后端三端对齐）。

权威源是后端 ``Backend/app/core/qty.py``，前端 ``Frontend/src/utils/qty.ts`` 已对齐；
本模块逐字照抄后端 ``STACK`` / ``SHULKER`` / ``format_qty``，保证三端字节级一致。
另提供显示层便利包装 ``format_qty_safe``（处理缺失/``"?"`` 兜底）。

Minecraft 物品单位换算（离线模式 MVP，无 NBT 容量差异）：
- SHULKER = 27 * 64 = 1728（一盒 = 一整个潜影盒 = 27 组）
- STACK   = 64（一组）

设计约束：
  * **纯函数**——仅依赖内置 ``int`` / ``round`` / f-string，不 import mcdreforged，
    可在无 MCDR 运行时的环境单测（与 ``text_layout.py`` / ``scanner.py`` 同范式）。
  * **显示层专用**——只换算展示串，不入库、不进 API 响应（DB ``need_qty`` 永存原始 int）。
"""

STACK: int = 64
SHULKER: int = 27 * STACK  # 1728


def format_qty(n: int) -> str:
    """将原始整数数量换算为「X盒 / X组 / X个」展示串。

    - n >= SHULKER → "X盒"（n/1728，保留两位去尾零，如 1.16盒）
    - n >= STACK   → "X组"（n/64）
    - 否则          → "X个"

    用 ``round(x, 2):g`` 去尾零：``2.0`` → ``"2"``，``1.50`` → ``"1.5"``。
    """
    if n >= SHULKER:
        return f"{round(n / SHULKER, 2):g}盒"
    if n >= STACK:
        return f"{round(n / STACK, 2):g}组"
    return f"{n}个"


def format_qty_safe(value) -> str:
    """显示层包装：``int`` 走 ``format_qty`` 换算；非 ``int``（如 ``"?"`` 缺失兜底）原样 ``str()``。

    用于 ``format_notification`` / 回执等用 ``payload.get(..., "?")`` 兜底的地方——
    ``format_qty("?")`` 会因 str 与 int 比较抛 ``TypeError``，故在此守护。
    ``bool`` 虽是 ``int`` 子类，但语义上不是数量，一并走 ``str()`` 兜底（避免 ``True``→``"True个"``）。
    """
    return format_qty(value) if isinstance(value, int) and not isinstance(value, bool) else str(value)
