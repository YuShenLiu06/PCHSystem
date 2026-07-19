"""sheet 归档 markdown 渲染器（markdown_render 首个消费者）。

把 sheet / contributors 数据按内置 section 渲染成完整 markdown 文档。
所有渲染函数都是**纯函数**：从 context 取数据，不查 DB、不改入参。

context dict 约定（service.archive_sheet 注入）::

    {
        "sheet_id": int,
        "title": str,
        "owner_name": str,
        "status_label": str,            # 中文状态标签（如 "已归档"）
        "created_at": datetime | None,  # 创建时间
        "archived_at": datetime | None, # 归档时间（archive 时填 now(utc)）
        "constructing_at": datetime | None,  # 可选：进入施工时间（有则显）
        # contributor_totals: aggregate_contributor_totals 返回的原形态
        # [(represent_uuid, display_name, total_qty)]，已按总量降序、名字升序兜底
        #（lock 交付 + progress 上交合并按账号，剔除零和玩家）。
        # display_name = 自定义昵称优先，否则账号下最近活跃 UUID 游戏名。
        # render_contributor_stats / render_contribution_chart 用它。
        "contributor_totals": list[tuple[UUID, str, int]],
    }
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.services.markdown_render import (
    FunctionSection,
    MarkdownDocument,
    TemplateSection,
)


def build_sheet_archive_document() -> MarkdownDocument:
    """链式 register 各内置 section，返回不可变 MarkdownDocument。

    section 编排（order 升序渲染）：
    - header(100)            TemplateSection  标题
    - status_line(200)       TemplateSection  状态标签
    - meta(300)              TemplateSection  拥有者 / 创建 / 归档 时间
    - contributor_stats(500) FunctionSection  贡献者统计（lock+progress 合并）
    - contribution_chart(550) FunctionSection 贡献占比图（引用同目录 contributions.png）
    - timeline(600)          FunctionSection  时间线
    - footer(900)            TemplateSection  脚注

    「支持多种内容新增」= 注册一个新 section（Route C 扩展点）。
    """
    return (
        MarkdownDocument()
        .register(
            TemplateSection(
                "header",
                100,
                "# 📦 项目归档：{title}",
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
            FunctionSection("contributor_stats", 500, render_contributor_stats)
        )
        .register(
            FunctionSection("contribution_chart", 550, render_contribution_chart)
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


def _fmt_dt(dt: datetime | None) -> str:
    """datetime → 'YYYY-MM-DD HH:MM' 字符串；None → 空串。"""
    if dt is None:
        return ""
    return dt.strftime("%Y-%m-%d %H:%M")


def render_contributor_stats(context: Any) -> str:
    """聚合所有贡献者（lock 交付 + progress 上交合并按人），生成精确数量排行。

    无贡献者 → 返空串（让 section 被文档层过滤，避免空标题）。
    数据来源：``contributor_totals`` = ``sheet_repo.aggregate_contributor_totals`` 的返回，
    形如 ``[(player_uuid, player_name, total_qty)]``（已按总量降序、名字升序兜底）。
    repo 已完成排序与汇总，renderer 只做渲染（纯函数不查库）。
    """
    totals: list = context.get("contributor_totals") or []
    if not totals:
        return ""

    lines = ["## 🏆 贡献者统计"]
    for pos, (_uuid, name, qty) in enumerate(totals, start=1):
        lines.append(f"{pos}. {name} — {qty}")
    return "\n".join(lines)


def render_contribution_chart(context: Any) -> str:
    """贡献占比图 section：引用与 index.md 同目录的 contributions.png。

    无贡献者（contributor_totals 空）→ 返空串（section 被过滤；service 不生图）。
    相对文件名 ``contributions.png``：与 index.md 同目录——wiki.js 渲染 + GET /archive
    ``<pre>`` 都兼容（前端单独用 asset 端点显图）。
    """
    totals: list = context.get("contributor_totals") or []
    if not totals:
        return ""
    return "## 📊 贡献占比\n\n![贡献占比](contributions.png)"


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
    return "## 📅 时间线\n" + "\n".join(entries)
