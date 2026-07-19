"""sheets 包共享函数与通知 helper。"""
import logging
import uuid

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sheet import Sheet, SheetRow
from app.models.user import Player
from app.repositories import sheet_repo
from app.repositories.sheet_repo import SheetArchived
from app.schemas.sheet import RowDetail
from app.services import notification_service
from app.services.translation import resolve_item_name

logger = logging.getLogger(__name__)


def _resolve_item_name(item_name: str | None, registry_id: str | None) -> str:
    """item_name 缺失时用 registry_id 翻译补默认中文名。

    供新建路径（from-items / 无 row_id upsert）落库前补默认名。
    schema 的 model_validator 已保证二者至少有一个非空；
    此处仍防御性返回 422（不裸 assert 致 500）。
    """
    try:
        return resolve_item_name(item_name, registry_id)
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "item_name 与 registry_id 至少提供一个",
        ) from exc


def _is_owner(sheet: Sheet, player: Player) -> bool:
    """表的真拥有者（owner_uuid 匹配）。用于 tier A 高危操作的前置判定。"""
    return sheet.owner_uuid == player.uuid


def _is_superuser(player: Player) -> bool:
    """全局 admin/owner 角色（全服超管），对任意项目拥有全部权限（含 tier A）。"""
    return player.role in ("admin", "owner")


def _can_manage(sheet: Sheet, player: Player) -> bool:
    """tier A 高危写权限：owner 或全局超管。

    覆盖：删除项目、改项目名、授予/撤销协管员、归档（advance→archived）。
    manager 无此权限。
    """
    return _is_owner(sheet, player) or _is_superuser(player)


def _can_operate(sheet: Sheet, player: Player) -> bool:
    """tier B 常规写权限：owner、全局超管，或本表 manager（迁移 0014）。

    覆盖：增删改行、子物品、setreg、progress 覆写、release、reject、
    collecting→constructing 流转。manager 可触发。
    依赖 Sheet.managers 关系预加载（lazy="selectin"）——调用前须经
    _load_sheet_or_404 / sheet_repo.get_sheet 路径加载。
    """
    if _can_manage(sheet, player):
        return True
    managers = getattr(sheet, "managers", None) or []
    return any(m.player_uuid == player.uuid for m in managers)


def _can_edit(sheet: Sheet, player: Player) -> bool:
    """[deprecated] tier A 别名，等价于 _can_manage。保留一轮渐进迁移，新代码请用
    _can_manage（tier A）/ _can_operate（tier B）。"""
    return _can_manage(sheet, player)


async def _load_sheet_or_404(session: AsyncSession, sheet_id: int) -> Sheet:
    result = await sheet_repo.get_sheet(session, sheet_id)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sheet not found")
    return result[0]


def _row_payload(sheet: Sheet, row_id: int, item_name: str, **extra) -> dict:
    """通知 payload 基座（sheet_id, sheet_title, row_id, item_name + 额外字段）。

    调用方传快照值，避免读已被 repo 改写的 row。
    """
    return {
        "sheet_id": sheet.id,
        "sheet_title": sheet.title,
        "row_id": row_id,
        "item_name": item_name,
        **extra,
    }


async def notify_owner_row_event(
    session: AsyncSession,
    *,
    sheet: Sheet,
    actor: Player,
    category: str,
    title: str,
    body: str,
    row_id: int,
    item_name: str,
    **payload_extra,
) -> None:
    """通知 owner 行事件（收件人=owner）。actor==recipient 时跳过。"""
    if sheet.owner_uuid == actor.uuid:
        return
    full_payload = {
        "actor_uuid": str(actor.uuid),
        "actor_name": actor.current_name,
        **_row_payload(sheet, row_id, item_name, **payload_extra),
    }
    await notification_service.notify(
        session,
        recipient_uuid=sheet.owner_uuid,
        category=category,
        title=title,
        body=body,
        payload=full_payload,
    )


async def notify_uuids(
    session: AsyncSession,
    uuids: list[uuid.UUID],
    *,
    actor: Player,
    category: str,
    title: str,
    body: str,
    **payload,
) -> None:
    """批量通知（贡献者群发）。actor==recipient 自动跳过。"""
    for recipient_uuid in uuids:
        if recipient_uuid == actor.uuid:
            continue
        full_payload = {
            "actor_uuid": str(actor.uuid),
            "actor_name": actor.current_name,
            **payload,
        }
        await notification_service.notify(
            session,
            recipient_uuid=recipient_uuid,
            category=category,
            title=title,
            body=body,
            payload=full_payload,
        )


async def notify_rows_deleted(
    session: AsyncSession,
    *,
    sheet: Sheet,
    actor: Player,
    rows_with_names: list[tuple[SheetRow, str | None]],
    contributors_map: dict[int, list[tuple[uuid.UUID, str]]] | None = None,
) -> None:
    """删行/删表后通知各行的认领人与 progress 贡献者（删行 + 删表共用，DRY）。

    - 认领人（claimant_uuid 非空）：``sheet_row_deleted``，标题「认领的行已被删除」。
    - progress 行贡献者（contributors_map 命中）：同 category，标题「贡献的行已被删除」。
    actor==recipient 自动跳过（notify_uuids 内置）。
    """
    cmap = contributors_map or {}
    for row, _name in rows_with_names:
        item_name = row.item_name
        if row.claimant_uuid is not None:
            await notify_uuids(
                session,
                [row.claimant_uuid],
                actor=actor,
                category="sheet_row_deleted",
                title="认领的行已被删除",
                body=f"[{item_name}] 已被拥有者删除，认领取消",
                sheet_id=sheet.id,
                sheet_title=sheet.title,
                row_id=row.id,
                item_name=item_name,
            )
        for contrib_uuid, _contrib_name in cmap.get(row.id, []):
            await notify_uuids(
                session,
                [contrib_uuid],
                actor=actor,
                category="sheet_row_deleted",
                title="贡献的行已被删除",
                body=f"[{item_name}] 已被拥有者删除，贡献取消",
                sheet_id=sheet.id,
                sheet_title=sheet.title,
                row_id=row.id,
                item_name=item_name,
            )


def _row_dict(
    row: SheetRow,
    claimant_name: str | None = None,
    contributors: list[dict] | None = None,
) -> dict:
    return {
        "id": row.id,
        "item_name": row.item_name,
        "registry_id": row.registry_id,
        "need_qty": row.need_qty,
        "mode": row.mode,
        "status": row.status,
        "claimant_uuid": row.claimant_uuid,
        "claimant_name": claimant_name,
        "delivered_qty": row.delivered_qty,
        "contributors": contributors or [],
        "sort_order": row.sort_order,
        "updated_at": row.updated_at,
        "parent_row_id": row.parent_row_id,
        "qty_per_unit": row.qty_per_unit,
    }


def _to_summary(sheet: Sheet, owner_name: str):
    from app.schemas.sheet import SheetSummary

    return SheetSummary(
        id=sheet.id,
        owner_uuid=sheet.owner_uuid,
        owner_name=owner_name,
        title=sheet.title,
        status=sheet.status,
        archived_path=sheet.archived_path,
        archived_at=sheet.archived_at,
        created_at=sheet.created_at,
        updated_at=sheet.updated_at,
    )


def _to_detail(
    sheet: Sheet,
    rows_with_names: list[tuple[SheetRow, str | None]],
    owner_name: str,
    contributors_map: dict[int, list[tuple[uuid.UUID, str]]] | None = None,
    managers: list[tuple[uuid.UUID, str, object]] | None = None,
):
    from app.schemas.sheet import RowDetail, SheetDetail, SheetManagerEntry

    cmap = contributors_map or {}
    mgrs = [
        SheetManagerEntry(player_uuid=pu, player_name=pn, granted_at=gat)
        for pu, pn, gat in (managers or [])
    ]
    return SheetDetail(
        id=sheet.id,
        owner_uuid=sheet.owner_uuid,
        owner_name=owner_name,
        title=sheet.title,
        status=sheet.status,
        archived_path=sheet.archived_path,
        archived_at=sheet.archived_at,
        created_at=sheet.created_at,
        updated_at=sheet.updated_at,
        rows=[
            RowDetail(
                **_row_dict(
                    r,
                    name,
                    [
                        {"player_uuid": pu, "player_name": pn}
                        for pu, pn in cmap.get(r.id, [])
                    ],
                )
            )
            for r, name in rows_with_names
        ],
        managers=mgrs,
    )


async def _row_response(
    session: AsyncSession,
    sheet_id: int,
    row: SheetRow,
    *,
    with_contributors: bool = False,
) -> RowDetail:
    """统一收口 get_row→name→refresh(row)→RowDetail。

    contribute/progress 走 with_contributors=True。
    """
    result = await sheet_repo.get_row(session, sheet_id, row.id)
    name = result[1] if result is not None else None
    contributors = []
    if with_contributors:
        contrib_map = await sheet_repo.list_contributors(session, [row.id])
        contributors = [
            {"player_uuid": pu, "player_name": pn}
            for pu, pn in contrib_map.get(row.id, [])
        ]
    return RowDetail(**_row_dict(row, name, contributors))
