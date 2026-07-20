from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, Text, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class WebAccount(Base):
    """Web 账号主锚（users schema）。

    一个账号可绑定多个 MC UUID（Player）。临时账号 username/password_hash 均为 NULL。
    """
    __tablename__ = "web_accounts"
    __table_args__ = {"schema": "users"}

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement="auto")
    username: Mapped[Optional[str]] = mapped_column(Text, nullable=True, unique=True)
    password_hash: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="user")
    wiki_user_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 玩家自定义昵称（sheets 三端统一显示名主源）；为空时应用层回退到
    # 该账号下 last_seen_at 最新 UUID 的 current_name。不用 username（那是 Web 登录账号名）。
    display_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    last_active_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )

    @property
    def is_temporary(self) -> bool:
        """临时账号判定：username IS NULL"""
        return self.username is None


class BindToken(Base):
    """绑定短码（双向）。

    game_init: 游戏内发起（!!PCH bind）→ Web 确认
    web_init: Web 发起 → 游戏内确认（!!PCH bind <code>）
    """
    __tablename__ = "bind_tokens"
    __table_args__ = {"schema": "users"}

    token: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    short_code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    player_uuid: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    target_account_id: Mapped[Optional[int]] = mapped_column(nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class Player(Base):
    """玩家实体（users schema）。

    MC UUID 子身份，归属 Web 账号主锚（web_account_id）。
    未绑定时 web_account_id 为 NULL（!!PCH login 自动挂临时账号）。
    """

    __tablename__ = "players"
    __table_args__ = {"schema": "users"}

    uuid: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    current_name: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="user")
    whitelist_state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active"
    )
    web_account_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.web_accounts.id", ondelete="SET NULL"), nullable=True
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    last_sheet_id: Mapped[int | None] = mapped_column(nullable=True)

    # 关系：Player → WebAccount（可选）
    web_account: Mapped[Optional[WebAccount]] = relationship(
        lazy="selectin", foreign_keys=[web_account_id]
    )


class AuthToken(Base):
    __tablename__ = "auth_tokens"
    __table_args__ = {"schema": "users"}

    token: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    player_uuid: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.players.uuid"),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    issued_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    exchanged_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class JwtRevocation(Base):
    __tablename__ = "jwt_revocations"
    __table_args__ = {"schema": "users"}

    jti: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    player_uuid: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.players.uuid"),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
