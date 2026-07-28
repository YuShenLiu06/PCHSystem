"""施工进度上报层 ORM（construction schema）。

对应迁移 ``0017_construction``（基表）+ ``0018_placement_snapshots``（时序快照）。
表语义见迁移 docstring 与 [`Docs/architecture/flows/construction-progress.md`]。

- ``PlacementRecord``：方块净放置聚合（按 sheet×account×registry）。
- ``PlacementSnapshot``：时序快照（每次 report 后按 account 写一条累计净放置，
  前端折线图消费；迭代 2）。
- ``PlayerSource`` / ``PlayerSourceHistory``：每玩家单活跃上报源 + 切换审计。
- ``ServerModSource``：服务端 mod 白名单。

R-5：``PlacementRecord``/``PlacementSnapshot`` 的 ``account_id`` 锚 Web 账号。
"""
from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, Text, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class PlacementRecord(Base):
    """方块净放置聚合（construction.placement_records）。

    ``net_qty = placed_qty - broken_qty``（允许负）。归档时按 account 聚合喂给
    ``BuildAScoreCalculator``（scoring-settlement.md §4）。
    """

    __tablename__ = "placement_records"
    __table_args__ = {"schema": "construction"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    sheet_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("sheets.sheets.id", ondelete="CASCADE"),
        nullable=False,
    )
    account_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.web_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    registry_id: Mapped[str] = mapped_column(Text, nullable=False)
    placed_qty: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    broken_qty: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    net_qty: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class PlacementSnapshot(Base):
    """施工净放置时序快照（construction.placement_snapshots，迁移 0018）。

    每次 report 成功落 placement 后，为涉及的 account 写一条快照
    （``total_net`` = 该 account 此刻在该 sheet 的 ``sum(net_qty)``）。
    前端折线图「时序累计趋势」按 account 拆线消费。写入 best-effort，
    失败不阻断 report（展示用，非权威源）。
    """

    __tablename__ = "placement_snapshots"
    __table_args__ = {"schema": "construction"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    sheet_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("sheets.sheets.id", ondelete="CASCADE"),
        nullable=False,
    )
    account_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.web_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    total_net: Mapped[int] = mapped_column(Integer, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class PlayerSource(Base):
    """玩家上报源记录（construction.player_sources）。

    每玩家同时仅一条 ``disabled_at IS NULL`` 活跃源（部分唯一索引保证）。
    旧源 disabled 后留存（用于审计回查 + 休眠源列表），故代理主键 id 而非 player_uuid。
    """

    __tablename__ = "player_sources"
    __table_args__ = {"schema": "construction"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    player_uuid: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.players.uuid", ondelete="CASCADE"),
        nullable=False,
    )
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    activated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    disabled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PlayerSourceHistory(Base):
    """上报源切换审计（construction.player_source_history，append-only）。"""

    __tablename__ = "player_source_history"
    __table_args__ = {"schema": "construction"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    player_uuid: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.players.uuid", ondelete="CASCADE"),
        nullable=False,
    )
    from_type: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    from_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    to_type: Mapped[str] = mapped_column(Text, nullable=False)
    to_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    switched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class ServerModSource(Base):
    """服务端 mod 白名单（construction.server_mod_sources，PK=name 幂等）。"""

    __tablename__ = "server_mod_sources"
    __table_args__ = {"schema": "construction"}

    name: Mapped[str] = mapped_column(Text, primary_key=True)
    enabled: Mapped[bool] = mapped_column(
        Boolean, server_default=text("true"), nullable=False, default=True
    )
    approved_by_uuid: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.players.uuid", ondelete="SET NULL"),
        nullable=True,
    )
    approved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
