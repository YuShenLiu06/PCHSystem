"""施工进度上报层 ORM（construction schema）。

对应迁移 ``0017_construction``（基表）+ ``0018_placement_snapshots``（时序快照）+
``0021_construction_participants``（加入施工）+ ``0023_report_events``（玩家可见
事件流水）。
表语义见迁移 docstring 与 [`Docs/architecture/flows/construction-progress.md`]。

- ``PlacementRecord``：方块净放置聚合（按 sheet×account×registry）。
- ``PlacementSnapshot``：时序快照（每次 report 后按 account 写一条累计净放置，
  前端折线图消费；迭代 2）。
- ``ReportEvent``：玩家可见事件流水（accepted + 所有 skip 原因逐条落库；
  ``/me/report-events`` 数据源，迭代 5）。
- ``PlayerSource`` / ``PlayerSourceHistory``：每玩家单活跃上报源 + 切换审计。
- ``ServerModSource``：服务端 mod 白名单。
- ``Participant``：玩家加入施工项目记录（plan BLOCK 1，2026-07-28）。

R-5：``PlacementRecord``/``PlacementSnapshot``/``ReportEvent``/``Participant`` 均锚 Web 账号。
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


class ReportEvent(Base):
    """玩家可见上报事件流水（construction.report_events，迁移 0023）。

    每次 ``submit_report`` 产出的 ``PlacementOutcome`` 逐条落库（仅 bound 玩家：
    ``account_id`` NOT NULL）：``accepted`` + **所有 skip 原因**都写一行。
    ``/me/report-events`` 消费——让玩家看到「为什么我的上报被拒」。

    - ``sheet_id`` nullable：归因失败 / 客户端 mod 全局关闭等无 sheet 场景照落。
    - ``registry_id`` nullable：防御性（outcome 都带，但允许 null）。
    - ``net_delta`` nullable：accepted=本次计入；skipped=被拒/尝试量（部分接受 over）。
    - append-only（无 UPDATE/DELETE 端点），与 ``PlacementSnapshot`` 同为展示用辅助表。

    R-5：锚 ``account_id``（``/me/report-events`` 按 account 过滤）。
    """

    __tablename__ = "report_events"
    __table_args__ = {"schema": "construction"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    sheet_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("sheets.sheets.id", ondelete="CASCADE"),
        nullable=True,
    )
    account_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.web_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    player_uuid: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    registry_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    net_delta: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
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


class Participant(Base):
    """玩家加入施工项目记录（construction.participants，迁移 0021）。

    每个 Web 账号同时最多活跃加入 1 个施工项目（``uq_participants_active``
    partial unique index 兜底）。主表 append-only：仅 UPDATE ``left_at``，从不 DELETE
    （保留历史行供「我的施工历程」，仿 ``player_sources``）。

    - ``join_source='auto'``：备货/施工阶段认领/上交时由 ``sheet_repo._maybe_auto_join``
      自动加入（已在他项目时 silent skip；冲突仅 manual 抛 ``ParticipantConflict``）。
    - ``join_source='manual'``：玩家经 Web/MCDR 显式 join/switch 加入。
    - ``left_reason``：``'manual_leave'`` / ``'switched'`` / ``'archived'`` / ``'auto_displaced'``。

    R-5：锚 ``web_account_id``（同账号任一 UUID 都视为同一加入者）。
    """

    __tablename__ = "participants"
    __table_args__ = {"schema": "construction"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    web_account_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.web_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    sheet_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("sheets.sheets.id", ondelete="CASCADE"),
        nullable=False,
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    left_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    join_source: Mapped[str] = mapped_column(Text, nullable=False)
    left_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
