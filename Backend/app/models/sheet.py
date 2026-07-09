from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

# 项目级三阶段生命周期（与行级 STATUS_* 区分前缀；行级常量在 sheet_repo.py）
SHEET_PHASE_COLLECTING, SHEET_PHASE_CONSTRUCTING, SHEET_PHASE_ARCHIVED = (
    "collecting",
    "constructing",
    "archived",
)
# 非终态集合（list_sheets status_filter="active" 用）
SHEET_PHASE_ACTIVE_SET = frozenset(
    {SHEET_PHASE_COLLECTING, SHEET_PHASE_CONSTRUCTING}
)


class Sheet(Base):
    """在线表格主表（sheets schema）。

    MVP 第一阶段核心交付物：固定列清单表，支持 Web + 游戏内双向编辑。
    身份锚 = owner_uuid（FK→users.players.uuid，红线 R-5）。
    详见 Docs/Plans/MVP-第一阶段计划.md §3.2。
    """

    __tablename__ = "sheets"
    __table_args__ = {"schema": "sheets"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    owner_uuid: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.players.uuid"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    # 项目阶段生命周期（迁移 0009）：collecting（默认）→ constructing → archived（只读终态）。
    # archived 必有 archived_path/archived_at（DB CHECK ck_sheets_status_archive_consistency）。
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=SHEET_PHASE_COLLECTING,
        server_default=text("'collecting'"),
    )
    archived_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class SheetRow(Base):
    """表格行（sheets schema）。每行 = 一个物品条目。

    item_name 自由文本（D-2，红线 R-6 不覆盖 sheets）；
    need_qty 原始整数（D-4，永不存换算结果）；
    mode=0 lock（二元备齐，单人锁定）/ mode=1 progress（进度，多人贡献者列表）；
    status 三态 open/claimed/done；lock=认领协作状态机，progress=由 delivered_qty 推导；
    claimant_uuid lock 模式当前认领人（open 态 null）；progress 模式恒 null（贡献者走 SheetRowContributor 子表）；
    delivered_qty 已交付数量（lock 认领人维护；progress 任何人 contribute 累加）。

    子物品嵌套行（0012 迁移）：parent_row_id（FK 自引用 CASCADE）+ qty_per_unit。
    顶层行 UNIQUE(sheet_id, item_name)，子行 UNIQUE(parent_row_id, registry_id)。
    """

    __tablename__ = "sheet_rows"
    __table_args__ = {"schema": "sheets"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    sheet_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("sheets.sheets.id", ondelete="CASCADE"),
        nullable=False,
    )
    item_name: Mapped[str] = mapped_column(Text, nullable=False)
    # MC 注册名（namespace:path，如 minecraft:stone）；隐式可空。
    # 投影解析 from-items 透传 / MCDR 手持新建行 / Web 行编辑器手填。
    # 一键提交按此字段精确匹配；旧行与纯文本行为 null（不参与匹配）。
    registry_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    need_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    mode: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default=text("0")
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="open", server_default=text("'open'")
    )
    claimant_uuid: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.players.uuid"),
        nullable=True,
    )
    delivered_qty: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 子物品嵌套行（0012 迁移）
    parent_row_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("sheets.sheet_rows.id", ondelete="CASCADE"),
        nullable=True,
    )
    qty_per_unit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class SheetRowContributor(Base):
    """表格行贡献者（progress 模式多人协作，sheets schema）。

    progress（mode=1）行的「上交过材料」玩家名单；lock 行不写入。
    任何人 contribute（增量上交）时幂等加入（UNIQUE(row_id, player_uuid)）。
    ON DELETE CASCADE：行删除时贡献者随行自动清。
    身份锚 = player_uuid（FK→users.players.uuid，红线 R-5）。
    """

    __tablename__ = "sheet_row_contributors"
    __table_args__ = (
        UniqueConstraint(
            "row_id", "player_uuid", name="uq_sheet_row_contributors_row_player"
        ),
        {"schema": "sheets"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    row_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("sheets.sheet_rows.id", ondelete="CASCADE"),
        nullable=False,
    )
    player_uuid: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.players.uuid"),
        nullable=False,
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    contributed_qty: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
