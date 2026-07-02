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
    mode=0 lock（二元备齐）/ mode=1 progress（跟踪交付量），拥有者每行手选（D-2/D-3）；
    status 三态 open/claimed/done（D-6，认领协作语义，spec §5.2）；
    claimant_uuid 当前认领人（null 当 open，FK→users.players.uuid）；
    delivered_qty 已交付数量（lock 模式认领人标备齐=置 need_qty；progress 模式累加上报）。
    UNIQUE(sheet_id, item_name) 兼作 upsert 锁点。
    """

    __tablename__ = "sheet_rows"
    __table_args__ = (
        UniqueConstraint("sheet_id", "item_name", name="uq_sheet_rows_sheet_item"),
        {"schema": "sheets"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    sheet_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("sheets.sheets.id", ondelete="CASCADE"),
        nullable=False,
    )
    item_name: Mapped[str] = mapped_column(Text, nullable=False)
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
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
