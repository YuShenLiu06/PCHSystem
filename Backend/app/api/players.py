"""玩家查询端点（协管员授予联想等）。

``GET /players?q=<prefix>`` —— 按 current_name / 昵称前缀联想，任意登录玩家可调（需 JWT
避免爬库）。返回 ``[{player_uuid, player_name, display_name}]``，display_name 经
``web_account_repo.resolve_display_names`` 解析（含回退链）；前端选中后内部传 uuid 调
``POST /sheets/{id}/managers``（grant body 不变）。仅返回已绑 WebAccount 的玩家。
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_player
from app.core.db import get_session
from app.models.user import Player
from app.repositories import player_repo, web_account_repo
from app.schemas.player import PlayerBrief

router = APIRouter()


@router.get("/players", response_model=list[PlayerBrief])
async def search_players(
    q: str = Query(default="", description="玩家名 / 昵称前缀（大小写不敏感，至少 1 字符）"),
    limit: int = Query(default=10, ge=1, le=20),
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
) -> list[PlayerBrief]:
    players = await player_repo.search_for_manager(session, q, limit)
    display_names = await web_account_repo.resolve_display_names(
        session, [p.uuid for p in players]
    )
    return [
        PlayerBrief(
            player_uuid=p.uuid,
            player_name=p.current_name,
            display_name=display_names.get(p.uuid, p.current_name),  # CR #7：理论 race 缺失回退玩家名
        )
        for p in players
    ]
