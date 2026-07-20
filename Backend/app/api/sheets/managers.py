"""项目级协管员（manager）端点（迁移 0014，account 锚，R-5 落地）。

manager 权限锚 ``web_account_id``（非 player_uuid）：同账号任一 UUID 都继承
manager；授予目标必须已绑 Web 账号（B7：未绑 → 422；owner account → 409）。

- GET    /sheets/{id}/managers  任意登录玩家可读（透明，便于协作识别管理权）
- POST   /sheets/{id}/managers  仅 owner/超管（tier A）；archived 拒；
                                  target 未绑 account → 422；target == owner account → 409
- DELETE /sheets/{id}/managers   self-revoke 放行（B6 守卫：player.web_account_id
                                  非 None 且 == body.web_account_id）；否则 tier A
"""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_account_uuids, get_current_player
from app.api.sheets._shared import (
    _can_manage,
    _load_sheet_or_404,
    notify_uuids,
)
from app.core.db import get_session
from app.models.sheet import SHEET_PHASE_ARCHIVED
from app.models.user import Player
from app.repositories import (
    player_repo,
    sheet_manager_repo,
    web_account_repo,
)
from app.repositories.sheet_manager_repo import (
    SheetManagerNotFound,
    SheetOwnerCannotBeManager,
)
from app.schemas.sheet import (
    ManagerGrantRequest,
    ManagerRevokeRequest,
    SheetManagerEntry,
)

router = APIRouter()
logger = logging.getLogger(__name__)


def _assert_not_archived(sheet) -> None:
    """归档终态只读守卫（RS-10）：授权/撤销协管员禁止在 archived 态。"""
    if sheet.status == SHEET_PHASE_ARCHIVED:
        raise HTTPException(status.HTTP_409_CONFLICT, "项目已归档，只读")


async def _assemble_entries(
    session: AsyncSession, sheet_id: int
) -> list[SheetManagerEntry]:
    """组装 managers 响应：ORM list → account briefs → SheetManagerEntry[]。"""
    managers = await sheet_manager_repo.list_managers(session, sheet_id)
    if not managers:
        return []
    briefs = await web_account_repo.resolve_account_briefs(
        session, [m.web_account_id for m in managers]
    )
    entries: list[SheetManagerEntry] = []
    for m in managers:
        display_name, member_uuids = briefs.get(
            m.web_account_id, (f"账号#{m.web_account_id}", [])
        )
        entries.append(
            SheetManagerEntry(
                web_account_id=m.web_account_id,
                display_name=display_name,
                member_uuids=member_uuids,
                granted_at=m.granted_at,
            )
        )
    return entries


@router.get("/{sheet_id}/managers", response_model=list[SheetManagerEntry])
async def list_managers(
    sheet_id: int,
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
) -> list[SheetManagerEntry]:
    await _load_sheet_or_404(session, sheet_id)
    return await _assemble_entries(session, sheet_id)


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
    account_uuids: set[uuid.UUID] = Depends(get_current_account_uuids),
) -> list[SheetManagerEntry]:
    """授予协管员（account 锚）。

    - 非 tier A → 403；archived → 409。
    - target Player 不存在或 ``web_account_id is None`` → 422（B7）。
    - target account == owner account → 409 ``SheetOwnerCannotBeManager``（B7）。
    - 幂等重授（is_new=False）不重发通知（PK 防重复）。
    """
    sheet = await _load_sheet_or_404(session, sheet_id)
    if not _can_manage(sheet, player, account_uuids):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    _assert_not_archived(sheet)

    target = await player_repo.get_by_uuid(session, body.player_uuid)
    if target is None or target.web_account_id is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "目标玩家未绑定 Web 账号，无法授予 manager",
        )
    owner = await player_repo.get_by_uuid(session, sheet.owner_uuid)
    if owner is None or owner.web_account_id is None:
        # 拥有者未绑 account 属异常（新建表自动挂临时账号）；保守 500 让运维排查
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "项目拥有者未绑定 Web 账号（数据异常）",
        )

    try:
        _, is_new = await sheet_manager_repo.add_manager(
            session,
            sheet_id,
            target.web_account_id,
            owner_web_account_id=owner.web_account_id,
            granted_by_uuid=player.uuid,
        )
    except SheetOwnerCannotBeManager:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "不能把项目拥有者账号设为协管员",
        )

    # 仅新授予（非幂等重复）→ 通知 target 账号全部 UUID（R-5 account 级）
    if is_new:
        target_uuids = await web_account_repo.list_uuids(
            session, target.web_account_id
        )
        actor_name = await web_account_repo.resolve_display_name(
            session, player.uuid
        )
        await notify_uuids(
            session,
            target_uuids,
            actor=player,
            actor_name=actor_name,
            account_uuids=account_uuids,
            category="sheet_manager_granted",
            title="你被设为项目协管员",
            body=f"[{sheet.title}] 的拥有者 {actor_name} 将你设为协管员",
            sheet_id=sheet.id,
            sheet_title=sheet.title,
            granted_by_uuid=str(player.uuid),
        )
    await session.commit()
    return await _assemble_entries(session, sheet_id)


@router.delete(
    "/{sheet_id}/managers",
    response_model=list[SheetManagerEntry],
)
async def revoke_manager(
    sheet_id: int,
    body: ManagerRevokeRequest,
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
    account_uuids: set[uuid.UUID] = Depends(get_current_account_uuids),
) -> list[SheetManagerEntry]:
    """撤销协管员（account 锚）。

    - self-revoke 放行当 ``player.web_account_id is not None`` 且等于
      ``body.web_account_id``（B6：显式拒 ``None==None`` 误匹配）。
    - 否则需 tier A；archived → 409；manager 不存在 → 404。
    - self-revoke 不通知（发起方即受影响方）；其他撤销通知 target 账号全部 UUID。
    """
    sheet = await _load_sheet_or_404(session, sheet_id)
    is_self_revoke = (
        player.web_account_id is not None
        and body.web_account_id == player.web_account_id
    )
    if not is_self_revoke and not _can_manage(sheet, player, account_uuids):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    _assert_not_archived(sheet)

    # 先取 target UUIDs（撤后 list_uuids 仍返剩余账号下 UUID，不影响）
    target_uuids = await web_account_repo.list_uuids(
        session, body.web_account_id
    )
    actor_name = await web_account_repo.resolve_display_name(session, player.uuid)
    try:
        await sheet_manager_repo.remove_manager(
            session, sheet_id, body.web_account_id
        )
    except SheetManagerNotFound:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "该账号不是此项目的协管员"
        )
    if not is_self_revoke:
        await notify_uuids(
            session,
            target_uuids,
            actor=player,
            actor_name=actor_name,
            account_uuids=account_uuids,
            category="sheet_manager_revoked",
            title="你不再是项目协管员",
            body=f"[{sheet.title}] 的 {actor_name} 移除了你的协管员身份",
            sheet_id=sheet.id,
            sheet_title=sheet.title,
            revoked_by_uuid=str(player.uuid),
        )
    await session.commit()
    return await _assemble_entries(session, sheet_id)
