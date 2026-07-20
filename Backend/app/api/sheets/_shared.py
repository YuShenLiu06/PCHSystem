"""sheets 包共享函数与通知 helper。

权限契约（R-5 主锚落地 + main 协管员升 account 锚，2026-07-19）：

- owner / lock 行 claimant 判定按 **UUID 集合**（``account_uuids``）——同 Web 账号
  任一 UUID 都视为同一身份，权限/可见性跨 UUID 共享。
- manager 判定按 **``web_account_id``**——``SheetManager`` 锚 account 级（迁移 0014），
  同账号任一 UUID 自动继承 manager 权限（``_is_account_manager`` 直读
  ``sheet.managers`` 关系，无 getattr 兜底）。
- 统一收口：tier A = ``_can_manage``（owner/超管）；tier B = ``_can_operate``
  （tier A ‖ account manager）。外部调用方按端点语义选 tier，**勿混用** ``_can_edit``
  （已删 deprecated 别名，B4）。
- ``_is_superuser`` 经 ``_resolve_role`` 取 role 权威（``WebAccount.role``），避免
  「绑 account 但 ``player.role`` 仍是旧值」与全局 RBAC 不一致（B1 修 main bug）。
"""
import logging
import uuid

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import _resolve_role
from app.models.sheet import Sheet, SheetRow
from app.models.user import Player
from app.repositories import sheet_repo, web_account_repo
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


def _is_owner(sheet: Sheet, account_uuids: set[uuid.UUID]) -> bool:
    """表的真拥有者（按 account UUID 集合判定，R-5 主锚）。

    owner 建表时是其账号下某 UUID，同账号其他 UUID 视为同身份 → 均为 owner。
    不收 ``player`` 参数：防误读 ``player.uuid`` 绕开 account 聚合。
    """
    return sheet.owner_uuid in account_uuids


def _is_superuser(player: Player) -> bool:
    """全局 admin/owner 角色（全服超管，任意项目 tier A）。

    role 权威源 = ``WebAccount.role``（经 ``_resolve_role``）；未绑 account 时回退
    ``player.role``（默认 "user"）。避免 main 旧实现直读 ``player.role`` 在
    「绑 account 但 player.role 旧值未同步」场景下的权限漂移（B1）。
    """
    return _resolve_role(player) in ("admin", "owner")


def _can_manage(
    sheet: Sheet, player: Player, account_uuids: set[uuid.UUID]
) -> bool:
    """tier A 高危写权限：owner（按 account UUID 集合）或全局超管。

    覆盖：删除项目 / 改项目名 / 授予撤销协管员 / 归档（advance→archived）。
    manager 无此权限。
    """
    return _is_owner(sheet, account_uuids) or _is_superuser(player)


def _is_account_manager(sheet: Sheet, player: Player) -> bool:
    """该玩家所属 Web 账号是否为本表 manager（tier B 的 manager 分支）。

    - ``player.web_account_id is None`` → False（B3：未绑账号无 manager 身份）。
    - 直接读 ``sheet.managers`` 关系（lazy=selectin）——漏加载显式 AttributeError
      比静默 False 安全（B5 修 main 的 getattr 兜底）。
    """
    if player.web_account_id is None:
        return False
    return any(
        m.web_account_id == player.web_account_id for m in sheet.managers
    )


def _can_operate(
    sheet: Sheet, player: Player, account_uuids: set[uuid.UUID]
) -> bool:
    """tier B 常规写权限：tier A ‖ 本表 account manager。

    覆盖：增删改行 / 子物品 / setreg / progress 覆写 / release / reject /
    collecting→constructing 流转。manager 可触发。

    **前置条件（N4）**：``sheet.managers`` 关系必须已加载——调用方须先经
    ``_load_sheet_or_404`` 或 ``sheet_repo.get_sheet`` 加载本对象，不可用裸
    ``session.get(Sheet, id)`` 绕过 selectin 关系加载，否则 ``_is_account_manager``
    会触发隐式 lazy load 或 AttributeError。
    """
    if _can_manage(sheet, player, account_uuids):
        return True
    return _is_account_manager(sheet, player)


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
    actor_name: str,
    account_uuids: set[uuid.UUID],
    category: str,
    title: str,
    body: str,
    row_id: int,
    item_name: str,
    **payload_extra,
) -> None:
    """通知 owner 行事件（收件人=owner）。actor 与 owner 同 account 时跳过。"""
    if sheet.owner_uuid in account_uuids:
        return
    full_payload = {
        "actor_uuid": str(actor.uuid),
        "actor_name": actor_name,
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
    actor_name: str,
    account_uuids: set[uuid.UUID],
    category: str,
    title: str,
    body: str,
    **payload,
) -> None:
    """批量通知（贡献者群发 / manager 授予撤销）。recipient 与 actor 同 account 跳过。"""
    for recipient_uuid in uuids:
        if recipient_uuid in account_uuids:
            continue
        full_payload = {
            "actor_uuid": str(actor.uuid),
            "actor_name": actor_name,
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
    actor_name: str,
    account_uuids: set[uuid.UUID],
    rows_with_names: list[tuple[SheetRow, str | None]],
    contributors_map: (
        dict[int, list[tuple[int | None, str, list[uuid.UUID], int]]] | None
    ) = None,
) -> None:
    """删行/删表后通知各行的认领人与 progress 贡献者（删行 + 删表共用，DRY）。

    - 认领人（claimant_uuid 非空）：``sheet_row_deleted``，标题「认领的行已被删除」。
    - progress 行贡献者（contributors_map 命中，按 account 聚合后展开 member_uuids）：
      同 category，标题「贡献的行已被删除」。
    recipient 与 actor 同 account 自动跳过（notify_uuids 内置）。
    """
    cmap = contributors_map or {}
    for row, _name in rows_with_names:
        item_name = row.item_name
        if row.claimant_uuid is not None:
            await notify_uuids(
                session,
                [row.claimant_uuid],
                actor=actor,
                actor_name=actor_name,
                account_uuids=account_uuids,
                category="sheet_row_deleted",
                title="认领的行已被删除",
                body=f"[{item_name}] 已被拥有者删除，认领取消",
                sheet_id=sheet.id,
                sheet_title=sheet.title,
                row_id=row.id,
                item_name=item_name,
            )
        for _aid, _dn, member_uuids, _qty in cmap.get(row.id, []):
            await notify_uuids(
                session,
                member_uuids,
                actor=actor,
                actor_name=actor_name,
                account_uuids=account_uuids,
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


async def _to_detail(
    session: AsyncSession,
    sheet: Sheet,
    rows_with_names: list[tuple[SheetRow, str | None]],
    owner_name: str,
    contributors_map: (
        dict[int, list[tuple[int | None, str, list[uuid.UUID], int]]] | None
    ) = None,
    *,
    viewer_uuids: set[uuid.UUID] | list[uuid.UUID] | None = None,
    managers: list | None = None,
):
    """组装 SheetDetail（含 viewer_uuids + account 级 managers）。

    - ``viewer_uuids``（HEAD）：查看者所属 account 的全部 UUID（可见性）。
    - ``managers``（main-account）：``SheetManager`` ORM 列表；``None`` 时内部
      拉取（``sheet_manager_repo.list_managers``）。组装为 ``SheetManagerEntry``
      时经 ``web_account_repo.resolve_account_briefs`` 解析 display_name +
      member_uuids（供客户端按 viewer_uuids 交集判定 is_manager）。
    """
    from app.repositories import sheet_manager_repo
    from app.schemas.sheet import RowDetail, SheetDetail, SheetManagerEntry

    cmap = contributors_map or {}
    if managers is None:
        managers = await sheet_manager_repo.list_managers(session, sheet.id)
    briefs = await web_account_repo.resolve_account_briefs(
        session, [m.web_account_id for m in managers]
    )
    mgr_entries = [
        SheetManagerEntry(
            web_account_id=m.web_account_id,
            display_name=briefs.get(
                m.web_account_id, (f"账号#{m.web_account_id}", [])
            )[0],
            member_uuids=briefs.get(
                m.web_account_id, (f"账号#{m.web_account_id}", [])
            )[1],
            granted_at=m.granted_at,
        )
        for m in managers
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
        viewer_uuids=list(viewer_uuids or []),
        rows=[
            RowDetail(
                **_row_dict(
                    r,
                    name,
                    [
                        {
                            "account_id": aid,
                            "display_name": dn,
                            "member_uuids": mids,
                            "contributed_qty": qty,
                        }
                        for aid, dn, mids, qty in cmap.get(r.id, [])
                    ],
                )
            )
            for r, name in rows_with_names
        ],
        managers=mgr_entries,
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
            {
                "account_id": aid,
                "display_name": dn,
                "member_uuids": mids,
                "contributed_qty": qty,
            }
            for aid, dn, mids, qty in contrib_map.get(row.id, [])
        ]
    return RowDetail(**_row_dict(row, name, contributors))
