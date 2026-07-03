"""sheet 归档 markdown 渲染器（markdown_render 首个消费者）。

把 sheet/rows/contributors 数据按内置 section 渲染成完整 markdown 文档。
所有渲染函数都是**纯函数**：从 context 取数据，不查 DB、不改入参。

context dict 约定（service.archive_sheet 注入，Phase 4/api 对齐）::

    {
        "sheet_id": int,
        "title": str,
        "owner_name": str,
        "status_label": str,            # 中文状态标签（如 "已归档"）
        "created_at": datetime | None,  # 创建时间
        "archived_at": datetime | None, # 归档时间（archive 时填 now(utc)）
        "constructing_at": datetime | None,  # 可选：进入施工时间（有则显）
        # rows: list_rows 返回的原形态 [(SheetRow, claimant_name|None)]
        "rows": list[tuple[SheetRow, str | None]],
        # contributors_map: list_contributors 返回的原形态 {row_id: [(uuid, name), ...]}
        "contributors_map": dict[int, list[tuple[UUID, str]]],
        # contributor_totals: aggregate_contributor_totals 返回的原形态
        # [(player_uuid, player_name, total_qty)]，已按总量降序、名字升序兜底。
        # render_contributor_stats 用它渲染精确数量排行（贡献者统计 section）。
        "contributor_totals": list[tuple[UUID, str, int]],
    }
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from app.services.markdown_render import (
    FunctionSection,
    MarkdownDocument,
    TemplateSection,
)

# 行级 status（open/claimed/done）中文映射（与 sheet_repo.STATUS_* 对齐）。
_ROW_STATUS_LABELS = {
    "open": "未认领",
    "claimed": "进行中",
    "done": "已备齐",
}
# 行 mode（0=lock / 1=progress）中文映射。
_MODE_LABELS = {
    0: "lock",
    1: "progress",
}


def build_sheet_archive_document() -> MarkdownDocument:
    """链式 register 各内置 section，返回不可变 MarkdownDocument。

    section 编排（order 升序渲染）：
    - header(100)        TemplateSection  标题
    - status_line(200)   TemplateSection  状态标签
    - meta(300)          TemplateSection  拥有者 / 创建 / 归档 时间
    - material_table(400)    FunctionSection  材料表
    - contributor_stats(500) FunctionSection  贡献者统计
    - timeline(600)      FunctionSection  时间线
    - footer(900)        TemplateSection  脚注

    「支持多种内容新增」= 注册一个新 section（Route C 扩展点）。
    """
    return (
        MarkdownDocument()
        .register(
            TemplateSection(
                "header",
                100,
                "# 项目归档：{title}",
            )
        )
        .register(
            TemplateSection(
                "status_line",
                200,
                "**状态**：{status_label}",
            )
        )
        .register(
            TemplateSection(
                "meta",
                300,
                "**拥有者**：{owner_name}  ·  **创建**：{created_at}  ·  **归档**：{archived_at}",
            )
        )
        .register(
            FunctionSection("material_table", 400, render_material_table)
        )
        .register(
            FunctionSection("contributor_stats", 500, render_contributor_stats)
        )
        .register(FunctionSection("timeline", 600, render_timeline))
        .register(
            TemplateSection(
                "footer",
                900,
                "---\n由 PCHSystem 自动生成",
            )
        )
    )


def _row_status_label(status: str | None) -> str:
    """行 status → 中文标签；未知值原样回退（不抛，比中断渲染稳健）。"""
    if status is None:
        return ""
    return _ROW_STATUS_LABELS.get(status, status)


def _mode_label(mode: int | None) -> str:
    """行 mode → 标签；未知值原样回退。"""
    if mode is None:
        return ""
    return _MODE_LABELS.get(mode, str(mode))


def _fmt_dt(dt: datetime | None) -> str:
    """datetime → 'YYYY-MM-DD HH:MM' 字符串；None → 空串。"""
    if dt is None:
        return ""
    return dt.strftime("%Y-%m-%d %H:%M")


def _contributor_names(
    row_id: int,
    contributors_map: dict[int, list[tuple[UUID, str]]],
) -> list[str]:
    """取某行贡献者游戏名列表（保留 list_contributors 的贡献量降序）。"""
    return [name for _uuid, name in contributors_map.get(row_id, [])]


def render_material_table(context: Any) -> str:
    """渲染材料表 section。

    表头：``| # | 物品 | 需求 | 已交付 | 状态 | 模式 | 认领/贡献者 |``
    - lock 行（mode=0）：显 claimant_name（来自 list_rows 的 left join）。
    - progress 行（mode=1）：从 contributors_map[row_id] 取贡献者名 join。
    - 空表（无 rows）：返回 ``## 材料清单\\n\\n_暂无物品_``（非空串，让 section 显出来）。
    """
    rows = context.get("rows") or []
    contributors_map = context.get("contributors_map") or {}

    header = "| # | 物品 | 需求 | 已交付 | 状态 | 模式 | 认领/贡献者 |"
    separator = "|---|---|---|---|---|---|---|"

    if not rows:
        return "## 材料清单\n\n_暂无物品_"

    lines = ["## 材料清单", "", header, separator]
    for idx, (row, claimant_name) in enumerate(rows, start=1):
        if row.mode == 1:
            owner_cell = ", ".join(_contributor_names(row.id, contributors_map))
        else:
            owner_cell = claimant_name or ""
        lines.append(
            "| {idx} | {item} | {need} | {delivered} | {status} | {mode} | {owner} |".format(
                idx=idx,
                item=row.item_name,
                need=row.need_qty,
                delivered=row.delivered_qty,
                status=_row_status_label(row.status),
                mode=_mode_label(row.mode),
                owner=owner_cell,
            )
        )
    return "\n".join(lines)


def render_contributor_stats(context: Any) -> str:
    """聚合所有 progress 行的贡献者（按 contributed_qty 总量降序），生成精确数量排行。

    无贡献者 → 返空串（让 section 被文档层过滤，避免空标题）。
    数据来源：``contributor_totals`` = ``sheet_repo.aggregate_contributor_totals`` 的返回，
    形如 ``[(player_uuid, player_name, total_qty)]``（已按总量降序、名字升序兜底）。
    repo 已完成排序与汇总，renderer 只做渲染（纯函数不查库）。
    """
    totals: list = context.get("contributor_totals") or []
    if not totals:
        return ""

    lines = ["## 贡献者统计"]
    for pos, (_uuid, name, qty) in enumerate(totals, start=1):
        lines.append(f"{pos}. {name} — {qty}")
    return "\n".join(lines)


def render_timeline(context: Any) -> str:
    """渲染时间线 section（创建 / [进入施工] / 归档）。

    缺时间字段（None / 空）的行跳过；全缺时返空串（section 被过滤）。
    """
    created_at = context.get("created_at")
    constructing_at = context.get("constructing_at")
    archived_at = context.get("archived_at")

    entries: list[str] = []
    if created_at is not None:
        entries.append(f"- 创建：{_fmt_dt(created_at)}")
    if constructing_at is not None:
        entries.append(f"- 进入施工：{_fmt_dt(constructing_at)}")
    if archived_at is not None:
        entries.append(f"- 归档：{_fmt_dt(archived_at)}")

    if not entries:
        return ""
    return "## 时间线\n" + "\n".join(entries)
