from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class NotificationOut(BaseModel):
    id: int
    recipient_uuid: UUID
    category: str
    title: str
    body: str
    payload: dict[str, Any]
    created_at: datetime
    delivered_at: datetime | None = None
    read_at: datetime | None = None


class NotificationAckRequest(BaseModel):
    """MCDR ack：限定只标该 player_uuid 名下的通知（防越权 ack 他人）。"""
    player_uuid: UUID
    ids: list[int] = Field(default_factory=list)


class NotificationAckResponse(BaseModel):
    acked: int
