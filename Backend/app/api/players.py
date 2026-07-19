"""玩家查询端点（协管员授予联想等）。

``GET /players?q=<prefix>`` —— 按 current_name 前缀联想，任意登录玩家可调（需 JWT
避免爬库）。返回 ``[{player_uuid, player_name}]``，前端选中后内部传 uuid 调
``POST /sheets/{id}/managers``（grant body 不变）。
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_player
from app.core.db import get_session
from app.models.user import Player
from app.repositories import player_repo
from app.schemas.player import PlayerBrief

router = APIRouter()


@router.get("/players", response_model=list[PlayerBrief])
async def search_players(
    q: str = Query(default="", description="玩家名前缀（大小写不敏感，至少 1 字符）"),
    limit: int = Query(default=10, ge=1, le=20),
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
) -> list[PlayerBrief]:
    return [
        PlayerBrief(player_uuid=p.uuid, player_name=p.current_name)
        for p in await player_repo.search_by_name_prefix(session, q, limit)
    ]
