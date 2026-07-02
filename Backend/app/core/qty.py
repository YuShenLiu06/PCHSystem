"""format_qty 数量换算纯函数（D-4）。

后端用于展示与 CSV 之外的换算需要（如日志/告警）；API 永不附带换算字符串，
前端自行调用对齐的 qty.ts。三档：盒（潜影盒 27*64）/ 组（一组 64）/ 个。

Minecraft 物品单位换算（离线模式 MVP，无 NBT 容量差异）：
- SHULKER = 27 * 64 = 1728（一盒 = 一整个潜影盒 = 27 组）
- STACK   = 64（一组）
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
