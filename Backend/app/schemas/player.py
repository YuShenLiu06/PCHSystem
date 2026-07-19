"""玩家相关 Pydantic 模型。"""
from uuid import UUID

from pydantic import BaseModel


class PlayerBrief(BaseModel):
    """玩家简要信息（联想 / 列表用）。身份锚 = player_uuid。"""

    player_uuid: UUID
    player_name: str
