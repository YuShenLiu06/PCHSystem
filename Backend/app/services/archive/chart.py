"""归档贡献占比饼图渲染（matplotlib → PNG bytes）。

设计要点：
- **惰性 import**：matplotlib 仅在归档时需要，不在模块加载期拖应用启动。
  ``matplotlib.use("Agg")`` 必须在导入 pyplot 前设置 → 模块顶即切，保证任何后续
  pyplot import 都用无窗口 Agg 后端（免系统 GUI 包）。
- **CJK 字体**：用 ``rc_context`` 临时指定 ``Noto Sans CJK SC``（容器 Dockerfile 装该
  字体），用完即恢复，**不污染全局 rcParams**。字体缺失时 matplotlib 回退默认字体
  （中文可能豆腐，但 PNG 仍生成、不抛——宿主测试只校验 PNG 头，渲染由容器保证）。
- **不可变输入**：不改 totals。
"""
from __future__ import annotations

import io
from typing import Sequence
from uuid import UUID

# Agg 必须在 pyplot import 前设置；模块顶即切。
import matplotlib

matplotlib.use("Agg")

# 贡献占比图产物文件名（与 index.md 同目录；renderer markdown 引用 / asset 端点读取）。
CHART_FILENAME = "contributions.png"
# 贡献者超过此数 → top N +「其他」聚合，避免饼图切片过多不可读。
_TOP_N = 5
_OTHER_LABEL = "其他"
_CHART_TITLE = "贡献占比"
# CJK 字体（容器装；缺失时 matplotlib 回退，中文可能豆腐但 PNG 仍生成）。
_CJK_FONT = "Noto Sans CJK SC"
_PNG_DPI = 160


def render_contribution_pie(
    totals: Sequence[tuple[UUID, str, int]],
) -> bytes:
    """渲染贡献占比饼图 → PNG bytes（``\\x89PNG`` 开头）。

    - 空 totals 或全零 → ``b""``（调用方据此跳过写盘 + section）。
    - ≤ ``_TOP_N`` 人全显；> ``_TOP_N`` 取 top N +「其他」聚合（totals 已按总量降序）。
    - autopct ``%1.1f%%``、title ``贡献占比``、``bbox_inches='tight'``、DPI 160。
    """
    # 过滤零和（repo HAVING>0 已保证 >0，这里纵深防御）。
    # qty 强制 int：PostgreSQL SUM 返回 Decimal，matplotlib np.isfinite 不接受 Decimal。
    items: list[tuple[str, int]] = [
        (name, int(qty)) for _uuid, name, qty in totals if qty > 0
    ]
    if not items:
        return b""

    if len(items) > _TOP_N:
        top = items[:_TOP_N]
        rest_sum = sum(qty for _name, qty in items[_TOP_N:])
        items = [*top, (_OTHER_LABEL, rest_sum)]

    labels = [name for name, _qty in items]
    sizes = [qty for _name, qty in items]

    # 惰性 import（仅归档路径走到这里）。
    import matplotlib.pyplot as plt

    # rc_context：临时字体配置，退出恢复，不污染全局 rcParams。
    with plt.rc_context(
        {
            "font.sans-serif": [_CJK_FONT, "DejaVu Sans"],
            "axes.unicode_minus": False,
        }
    ):
        fig, ax = plt.subplots(figsize=(6, 6))
        ax.pie(sizes, labels=labels, autopct="%1.1f%%", startangle=90)
        ax.set_title(_CHART_TITLE)
        ax.axis("equal")  # 正圆（否则椭圆压扁百分比视觉）

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=_PNG_DPI, bbox_inches="tight")
        plt.close(fig)

    return buf.getvalue()
