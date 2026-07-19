"""项目级协管员（manager）端点（迁移 0014）。

owner 可授予/撤销协管员；manager 协助 owner 日常协作（tier B 写权限）。
- GET  /sheets/{id}/managers          任意登录玩家可读（透明）
- POST /sheets/{id}/managers          仅 owner/超管（tier A），archived 拒，owner 自身拒
- DELETE /sheets/{id}/managers/{uuid} 仅 owner/超管；例外：self-revoke 放行（主动卸任）
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_player
from app.api.sheets._shared import _can_manage, _load_sheet_or_404
from app.core.db import get_session
from app.models.sheet import SHEET_PHASE_ARCHIVED
from app.models.user import Player
from app.repositories import sheet_manager_repo
from app.repositories import player_repo
from app.schemas.sheet import ManagerGrantRequest, SheetManagerEntry
from app.services import notification_service

router = APIRouter()


def _to_entries(
    rows: list[tuple[UUID, str, object]],
) -> list[SheetManagerEntry]:
    return [
        SheetManagerEntry(player_uuid=pu, player_name=pn, granted_at=gat)
        for pu, pn, gat in rows
    ]


async def _list_or_404(
    session: AsyncSession, sheet_id: int
) -> list[SheetManagerEntry]:
    sheet = await _load_sheet_or_404(session, sheet_id)
    # 任何登录玩家可读 manager 列表（透明，便于协作时识别谁有管理权）
    managers = await sheet_manager_repo.list_managers(session, sheet.id)
    return _to_entries(managers)


def _assert_not_archived(sheet) -> None:
    """归档终态只读守卫（RS-10）：owner 授权/撤销协管员也禁止在 archived 态进行。"""
    if sheet.status == SHEET_PHASE_ARCHIVED:
        raise HTTPException(status.HTTP_409_CONFLICT, "项目已归档，只读")


@router.get("/{sheet_id}/managers", response_model=list[SheetManagerEntry])
async def list_managers(
    sheet_id: int,
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
) -> list[SheetManagerEntry]:
    return await _list_or_404(session, sheet_id)


@router.post(
    "/{sheet_id}/managers",
    response_model=list[SheetManagerEntry],
    status_code=status.HTTP_201_CREATED,
)
async def grant_manager(
    sheet_id: int,
    body: ManagerGrantRequest,
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
) -> list[SheetManagerEntry]:
    sheet = await _load_sheet_or_404(session, sheet_id)
    if not _can_manage(sheet, player):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    _assert_not_archived(sheet)
    # 目标玩家须已存在（至少登录过一次），否则 FK 失败
    target = await player_repo.get_by_uuid(session, body.player_uuid)
    if target is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "目标玩家不存在，需至少登录过一次",
        )
    try:
        added = await sheet_manager_repo.add_manager(
            session,
            sheet_id,
            body.player_uuid,
            owner_uuid=sheet.owner_uuid,
            granted_by_uuid=player.uuid,
        )
        # 新授予（非幂等重复）→ 通知被授予者「你现在是 X 项目的协管员」。
        # 同事务落库（R-10：commit 后才投递，rollback 则通知不落库）；幂等重授不重复打扰。
        if added:
            await notification_service.notify(
                session,
                recipient_uuid=body.player_uuid,
                category="sheet_manager_granted",
                title="你被设为项目协管员",
                body=f"[{sheet.title}] 的拥有者 {player.current_name} 将你设为协管员",
                payload={
                    "sheet_id": sheet.id,
                    "sheet_title": sheet.title,
                    "granted_by_uuid": str(player.uuid),
                    "granted_by_name": player.current_name,
                },
            )
        await session.commit()
    except sheet_manager_repo.SheetOwnerCannotBeManager:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "不能把项目拥有者设为协管员",
        )
    return await _list_or_404(session, sheet_id)


@router.delete(
    "/{sheet_id}/managers/{player_uuid}",
    response_model=list[SheetManagerEntry],
)
async def revoke_manager(
    sheet_id: int,
    player_uuid: UUID,
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
) -> list[SheetManagerEntry]:
    sheet = await _load_sheet_or_404(session, sheet_id)
    is_self_revoke = player_uuid == player.uuid
    # owner/超管可撤销任意 manager；例外：manager 可 self-revoke（主动卸任）
    if not is_self_revoke and not _can_manage(sheet, player):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    _assert_not_archived(sheet)
    try:
        await sheet_manager_repo.remove_manager(session, sheet_id, player_uuid)
        await session.commit()
    except sheet_manager_repo.SheetManagerNotFound:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "该玩家不是此项目的协管员"
        )
    return await _list_or_404(session, sheet_id)

