from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, ForeignKey, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Notification(Base):
    """统一通知记录（notifications schema）。

    通知抽象层的落库实体：业务模块经 ``notification_service.notify`` 在写端点
    同一事务内写入，外部（MCDR）经 ``GET /notifications/pending`` 轮询拉取后
    ``POST /notifications/ack`` 标投递。``category`` 用 String（非枚举类型），
    便于新模块按 ``<domain>_<event>`` 扩展（详见 notification-service 契约）。
    """

    __tablename__ = "notifications"
    __table_args__ = {"schema": "notifications"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    recipient_uuid: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.players.uuid", ondelete="CASCADE"),
        nullable=False,
    )
    category: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
