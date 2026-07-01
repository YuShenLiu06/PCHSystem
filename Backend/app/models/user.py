from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, String, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Player(Base):
    """玩家实体（users schema）。

    MVP 第一阶段身份锚 = 游戏内 UUID（离线模式由玩家名确定性推导，
    详见根规范 R-5）。后续阶段升级为 Web 绑定账号主锚时再调整。
    """

    __tablename__ = "players"
    __table_args__ = {"schema": "users"}

    uuid: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    current_name: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="user")
    whitelist_state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active"
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
