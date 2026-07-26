"""玩家相关 Pydantic 模型。"""
from uuid import UUID

from pydantic import BaseModel


class PlayerBrief(BaseModel):
    """玩家简要信息（联想 / 列表用）。身份锚 = player_uuid。"""

    player_uuid: UUID
    player_name: str
    # 三端统一显示名（#41）：由 web_account_repo.resolve_display_names 解析，
    # 含回退链（display_name → 同 account 最新 member current_name → 自身名），必非空。
    display_name: str
