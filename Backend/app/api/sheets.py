"""sheets 路由（在线表格 CRUD + CSV 导出 + 行认领协作）。

权限（spec §5.3）：
- 读（GET /sheets, GET /sheets/{id}）：JWT 已登录玩家
- 写表/行 upsert/删（POST/PATCH/DELETE 表、PUT 行、DELETE 行）：表的 owner_uuid 或 admin/owner 角色
- 认领 claim（POST .../claim）：任意登录玩家
- 上报交付（PATCH .../delivery）：当前认领人 only（lock 模式绝对值）
- 增量上交（POST .../contribute）：任意登录玩家（progress 模式增量，自动加贡献者）
- 调整进度（PATCH .../progress）：拥有者/admin（progress 行直接设 delivered_qty 绝对值，可增可减，不动贡献者）
- 解除锁定（POST .../release）：认领人自放 或 拥有者
- 打回（POST .../reject）：认领人 或 拥有者（done→claimed，delivered 归零，认领人保留重做）
- CSV 全量导出（GET /sheets/export）：service token（外部系统读取，MVP §4 硬约束）

分层（红线）：api 调 repo，**commit 在 api 层**，repo 只 flush。
状态机转移 + with_for_update 在 repo；非法转移 raise SheetRowConflict → api 翻译为 409。
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import PlainTextResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_player, require_service_token
from app.core.db import get_session
from app.models.sheet import Sheet, SheetRow
from app.models.user import Player
from app.repositories import sheet_repo
from app.repositories.sheet_repo import SheetRowConflict
from app.schemas.sheet import (
    RowContributeRequest,
    RowDeliveryRequest,
    RowDetail,
    RowProgressRequest,
    RowUpsertRequest,
    SheetCreateRequest,
    SheetDetail,
    SheetPatchRequest,
    SheetSummary,
)
from app.services import notification_service

router = APIRouter(prefix="/sheets", tags=["sheets"])


def _can_edit(sheet: Sheet, player: Player) -> bool:
    """表的 owner 或 admin/owner 角色可编辑（D-3，复用 deps.require_role 的 owner 隐式超级语义）。"""
    return sheet.owner_uuid == player.uuid or player.role in ("admin", "owner")


async def _load_sheet_or_404(session: AsyncSession, sheet_id: int) -> Sheet:
    result = await sheet_repo.get_sheet(session, sheet_id)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sheet not found")
    return result[0]


async def _notify(
    session: AsyncSession,
    *,
    recipient_uuid,
    actor: Player,
    category: str,
    title: str,
    body: str,
    payload: dict,
) -> None:
    """落库通知（同调用方事务）。actor==recipient 时跳过（不发给自己）。"""
    if recipient_uuid is None or recipient_uuid == actor.uuid:
        return
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


def _row_dict(
    row: SheetRow,
    claimant_name: str | None = None,
    contributors: list[dict] | None = None,
) -> dict:
    return {
        "id": row.id,
        "item_name": row.item_name,
        "need_qty": row.need_qty,
        "mode": row.mode,
        "status": row.status,
        "claimant_uuid": row.claimant_uuid,
        "claimant_name": claimant_name,
        "delivered_qty": row.delivered_qty,
        "contributors": contributors or [],
        "sort_order": row.sort_order,
        "updated_at": row.updated_at,
    }


def _to_summary(sheet: Sheet, owner_name: str) -> SheetSummary:
    return SheetSummary(
        id=sheet.id,
        owner_uuid=sheet.owner_uuid,
        owner_name=owner_name,
        title=sheet.title,
        created_at=sheet.created_at,
        updated_at=sheet.updated_at,
    )


def _to_detail(
    sheet: Sheet,
    rows_with_names: list[tuple[SheetRow, str | None]],
    owner_name: str,
    contributors_map: dict[int, list[tuple[uuid.UUID, str]]] | None = None,
) -> SheetDetail:
    cmap = contributors_map or {}
    return SheetDetail(
        id=sheet.id,
        owner_uuid=sheet.owner_uuid,
        owner_name=owner_name,
        title=sheet.title,
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
    )


@router.post("", response_model=SheetDetail, status_code=status.HTTP_201_CREATED)
async def create_sheet(
    body: SheetCreateRequest,
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
) -> SheetDetail:
    sheet = await sheet_repo.create_sheet(session, player.uuid, body.title)
    await session.commit()
    await session.refresh(sheet)
    return _to_detail(sheet, [], player.current_name)


@router.get("", response_model=list[SheetSummary])
async def list_sheets(
    owner: str | None = Query(default=None, description="过滤：传 me 只看自己"),
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
) -> list[SheetSummary]:
    owner_uuid = player.uuid if owner == "me" else None
    sheets_with_names = await sheet_repo.list_sheets(
        session, owner_uuid=owner_uuid
    )
    return [_to_summary(s, name) for s, name in sheets_with_names]


# 注意：/export 必须注册在 /{sheet_id} 之前，否则被动态路径吞掉
@router.get("/export", response_class=PlainTextResponse)
async def export_all(
    session: AsyncSession = Depends(get_session),
    _svc: None = Depends(require_service_token),
) -> PlainTextResponse:
    bundled = await sheet_repo.list_all_sheets_with_rows(session)
    csv_str = sheet_repo.export_all_csv(bundled)
    return PlainTextResponse(content=csv_str, media_type="text/csv")


@router.get("/{sheet_id}", response_model=SheetDetail)
async def get_sheet(
    sheet_id: int,
    format: str | None = Query(default=None, description="传 csv 返回 text/csv"),
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
):
    result = await sheet_repo.get_sheet(session, sheet_id)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sheet not found")
    sheet, owner_name = result
    rows_with_names = await sheet_repo.list_rows(session, sheet_id)
    if format == "csv":
        csv_str = sheet_repo.export_csv(sheet_id, [r for r, _ in rows_with_names])
        return PlainTextResponse(content=csv_str, media_type="text/csv")
    contributors_map = await sheet_repo.list_contributors(
        session, [r.id for r, _ in rows_with_names]
    )
    return _to_detail(sheet, rows_with_names, owner_name, contributors_map)


@router.patch("/{sheet_id}", response_model=SheetDetail)
async def patch_sheet(
    sheet_id: int,
    body: SheetPatchRequest,
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
) -> SheetDetail:
    sheet = await _load_sheet_or_404(session, sheet_id)
    if not _can_edit(sheet, player):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    sheet.title = body.title
    await session.flush()
    await session.commit()
    await session.refresh(sheet)
    result = await sheet_repo.get_sheet(session, sheet_id)
    owner_name = result[1] if result is not None else ""
    rows_with_names = await sheet_repo.list_rows(session, sheet_id)
    contributors_map = await sheet_repo.list_contributors(
        session, [r.id for r, _ in rows_with_names]
    )
    return _to_detail(sheet, rows_with_names, owner_name, contributors_map)


@router.delete("/{sheet_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_sheet(
    sheet_id: int,
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
) -> Response:
    sheet = await _load_sheet_or_404(session, sheet_id)
    if not _can_edit(sheet, player):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    # 删表前：对所有被认领的行通知各认领人「行已随表删除」
    # M-5：循环顶部把 item_name/claimant_uuid/row_id 解到局部变量快照，
    # 避免 notify 落库 flush / commit 路径上 expire_on_commit 触发额外 SQL。
    rows_with_names = await sheet_repo.list_rows(session, sheet_id)
    # 预取 progress 行的贡献者名单（删表前通知）
    progress_row_ids = [
        r.id for r, _ in rows_with_names if r.mode == sheet_repo.MODE_PROGRESS
    ]
    contributors_map = (
        await sheet_repo.list_contributors(session, progress_row_ids)
        if progress_row_ids
        else {}
    )
    # M-5：循环顶部把 item_name/claimant_uuid/row_id 解到局部变量快照，
    # 避免 notify 落库 flush / commit 路径上 expire_on_commit 触发额外 SQL。
    for old_row, _name in rows_with_names:
        item_name = old_row.item_name
        old_row_id = old_row.id
        # lock 行：通知认领人
        claimant = old_row.claimant_uuid
        if claimant is not None:
            await _notify(
                session,
                recipient_uuid=claimant,
                actor=player,
                category="sheet_row_deleted",
                title="认领的行已被删除",
                body=f"[{item_name}] 已被拥有者删除，认领取消",
                payload={
                    "sheet_id": sheet_id,
                    "sheet_title": sheet.title,
                    "row_id": old_row_id,
                    "item_name": item_name,
                },
            )
        # progress 行：通知每位贡献者
        for contrib_uuid, _contrib_name in contributors_map.get(old_row_id, []):
            await _notify(
                session,
                recipient_uuid=contrib_uuid,
                actor=player,
                category="sheet_row_deleted",
                title="贡献的行已被删除",
                body=f"[{item_name}] 已被拥有者删除，贡献取消",
                payload={
                    "sheet_id": sheet_id,
                    "sheet_title": sheet.title,
                    "row_id": old_row_id,
                    "item_name": item_name,
                },
            )
    await sheet_repo.delete_sheet(session, sheet_id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/{sheet_id}/rows", response_model=RowDetail)
async def upsert_row(
    sheet_id: int,
    body: RowUpsertRequest,
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
) -> RowDetail:
    sheet = await _load_sheet_or_404(session, sheet_id)
    if not _can_edit(sheet, player):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    # 捕获旧状态：判定 mode 是否变化（重置）、need 是否变化（通知认领人）
    prev_row = await sheet_repo.get_row_by_item(session, sheet_id, body.item_name)
    old_need = prev_row.need_qty if prev_row is not None else None
    claimant_uuid = prev_row.claimant_uuid if prev_row is not None else None
    mode_changed = prev_row is not None and prev_row.mode != body.mode
    try:
        row = await sheet_repo.upsert_row(
            session,
            sheet_id=sheet_id,
            item_name=body.item_name,
            need_qty=body.need_qty,
            mode=body.mode,
            sort_order=body.sort_order,
        )
        if mode_changed and claimant_uuid is not None:
            # 换模式重置了行 → 通知原认领人协作已取消
            await _notify(
                session,
                recipient_uuid=claimant_uuid,
                actor=player,
                category="sheet_released",
                title="模式变更，认领已重置",
                body=f"[{body.item_name}] 拥有者调整了模式，认领/进度已重置",
                payload={
                    "sheet_id": sheet_id,
                    "sheet_title": sheet.title,
                    "row_id": row.id,
                    "item_name": body.item_name,
                },
            )
        elif (
            not mode_changed
            and claimant_uuid is not None
            and old_need is not None
            and old_need != body.need_qty
        ):
            await _notify(
                session,
                recipient_uuid=claimant_uuid,
                actor=player,
                category="sheet_qty_changed",
                title="所需数量已调整",
                body=f"[{body.item_name}] 所需数量变为 {body.need_qty}（原 {old_need}）",
                payload={
                    "sheet_id": sheet_id,
                    "sheet_title": sheet.title,
                    "row_id": row.id,
                    "item_name": body.item_name,
                    "old": old_need,
                    "new": body.need_qty,
                },
            )
        await session.commit()
    except IntegrityError as exc:  # 并发同名 insert 命中 UNIQUE
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "row conflict") from exc
    await session.refresh(row)
    return RowDetail(**_row_dict(row, None))


@router.delete("/{sheet_id}/rows/{row_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_row(
    sheet_id: int,
    row_id: int,
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
) -> Response:
    sheet = await _load_sheet_or_404(session, sheet_id)
    if not _can_edit(sheet, player):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    current = await sheet_repo.get_row(session, sheet_id, row_id)
    if current is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "row not found")
    old_row = current[0]
    if old_row.claimant_uuid is not None:
        await _notify(
            session,
            recipient_uuid=old_row.claimant_uuid,
            actor=player,
            category="sheet_row_deleted",
            title="认领的行已被删除",
            body=f"[{old_row.item_name}] 已被拥有者删除，认领取消",
            payload={
                "sheet_id": sheet_id,
                "sheet_title": sheet.title,
                "row_id": row_id,
                "item_name": old_row.item_name,
            },
        )
    # progress 行：通知每位贡献者
    if old_row.mode == sheet_repo.MODE_PROGRESS:
        contrib_map = await sheet_repo.list_contributors(session, [old_row.id])
        for contrib_uuid, _name in contrib_map.get(old_row.id, []):
            await _notify(
                session,
                recipient_uuid=contrib_uuid,
                actor=player,
                category="sheet_row_deleted",
                title="贡献的行已被删除",
                body=f"[{old_row.item_name}] 已被拥有者删除，贡献取消",
                payload={
                    "sheet_id": sheet_id,
                    "sheet_title": sheet.title,
                    "row_id": row_id,
                    "item_name": old_row.item_name,
                },
            )
    count = await sheet_repo.delete_row(session, sheet_id, row_id)
    if count == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "row not found")
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------- 行认领协作（spec §5.4） ----------
@router.post(
    "/{sheet_id}/rows/{row_id}/claim",
    response_model=RowDetail,
)
async def claim_row(
    sheet_id: int,
    row_id: int,
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
) -> RowDetail:
    sheet = await _load_sheet_or_404(session, sheet_id)
    try:
        row = await sheet_repo.claim_row(session, sheet_id, row_id, player.uuid)
        if row is not None:
            await _notify(
                session,
                recipient_uuid=sheet.owner_uuid,
                actor=player,
                category="sheet_claimed",
                title="物品被认领",
                body=f"{player.current_name} 认领了 [{row.item_name}]",
                payload={
                    "sheet_id": sheet_id,
                    "sheet_title": sheet.title,
                    "row_id": row_id,
                    "item_name": row.item_name,
                },
            )
        await session.commit()
    except SheetRowConflict as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "row conflict") from exc
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "row not found")
    result = await sheet_repo.get_row(session, sheet_id, row_id)
    name = result[1] if result is not None else None
    await session.refresh(row)
    return RowDetail(**_row_dict(row, name))


@router.patch(
    "/{sheet_id}/rows/{row_id}/delivery",
    response_model=RowDetail,
)
async def set_row_delivery(
    sheet_id: int,
    row_id: int,
    body: RowDeliveryRequest,
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
) -> RowDetail:
    sheet = await _load_sheet_or_404(session, sheet_id)
    current = await sheet_repo.get_row(session, sheet_id, row_id)
    if current is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "row not found")
    if current[0].mode == sheet_repo.MODE_PROGRESS:
        # progress 行用 contribute（不走 delivery）；交 repo 抛 mode 守卫 → 409
        pass
    elif current[0].claimant_uuid != player.uuid:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not claimant")
    try:
        row = await sheet_repo.set_row_delivery(
            session, sheet_id, row_id, body.delivered_qty
        )
        if row is not None:
            is_done = body.delivered_qty >= row.need_qty
            await _notify(
                session,
                recipient_uuid=sheet.owner_uuid,
                actor=player,
                category="sheet_done" if is_done else "sheet_delivered",
                title="物品已备齐" if is_done else "物品上报交付",
                body=(
                    f"{player.current_name} 已备齐 [{row.item_name}]"
                    if is_done
                    else f"{player.current_name} 上报交付 {body.delivered_qty}/{row.need_qty} [{row.item_name}]"
                ),
                payload={
                    "sheet_id": sheet_id,
                    "sheet_title": sheet.title,
                    "row_id": row_id,
                    "item_name": row.item_name,
                    "delivered": body.delivered_qty,
                    "need": row.need_qty,
                },
            )
        await session.commit()
    except SheetRowConflict as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "row conflict") from exc
    result = await sheet_repo.get_row(session, sheet_id, row_id)
    name = result[1] if result is not None else None
    await session.refresh(row)
    return RowDetail(**_row_dict(row, name))


@router.post(
    "/{sheet_id}/rows/{row_id}/release",
    response_model=RowDetail,
)
async def release_row(
    sheet_id: int,
    row_id: int,
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
) -> RowDetail:
    sheet = await _load_sheet_or_404(session, sheet_id)
    current = await sheet_repo.get_row(session, sheet_id, row_id)
    if current is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "row not found")
    old_row = current[0]
    # repo 的 release_row 会清空 claimant_uuid/delivered（in-place 改同一 ORM 对象），
    # 必须先存不可变快照，通知时引用快照值。
    prev_claimant = old_row.claimant_uuid
    prev_item = old_row.item_name
    is_claimant_self = prev_claimant == player.uuid
    if not is_claimant_self and not _can_edit(sheet, player):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    try:
        row = await sheet_repo.release_row(session, sheet_id, row_id)
        if row is not None and prev_claimant is not None:
            if is_claimant_self:
                # 认领人自放 → 通知 owner「取消认领」
                await _notify(
                    session,
                    recipient_uuid=sheet.owner_uuid,
                    actor=player,
                    category="sheet_released",
                    title="认领已取消",
                    body=f"{player.current_name} 取消了对 [{prev_item}] 的认领",
                    payload={
                        "sheet_id": sheet_id,
                        "sheet_title": sheet.title,
                        "row_id": row_id,
                        "item_name": prev_item,
                    },
                )
            else:
                # owner 解锁 → 通知认领人「拥有者解除了锁定」
                await _notify(
                    session,
                    recipient_uuid=prev_claimant,
                    actor=player,
                    category="sheet_released",
                    title="锁定已被拥有者解除",
                    body=f"拥有者解除了你对 [{prev_item}] 的锁定",
                    payload={
                        "sheet_id": sheet_id,
                        "sheet_title": sheet.title,
                        "row_id": row_id,
                        "item_name": prev_item,
                    },
                )
        await session.commit()
    except SheetRowConflict as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "row conflict") from exc
    result = await sheet_repo.get_row(session, sheet_id, row_id)
    name = result[1] if result is not None else None
    await session.refresh(row)
    return RowDetail(**_row_dict(row, name))


@router.post(
    "/{sheet_id}/rows/{row_id}/reject",
    response_model=RowDetail,
)
async def reject_row(
    sheet_id: int,
    row_id: int,
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
) -> RowDetail:
    sheet = await _load_sheet_or_404(session, sheet_id)
    current = await sheet_repo.get_row(session, sheet_id, row_id)
    if current is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "row not found")
    old_row = current[0]
    # 认领人（自取消备齐）或拥有者（打回）均可；其余 403
    if old_row.claimant_uuid != player.uuid and not _can_edit(sheet, player):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    try:
        row = await sheet_repo.reject_row(session, sheet_id, row_id)
        if row is not None and old_row.claimant_uuid is not None:
            await _notify(
                session,
                recipient_uuid=old_row.claimant_uuid,
                actor=player,
                category="sheet_rejected",
                title="物品已打回",
                body=f"[{old_row.item_name}] 已打回，delivered 归零，可重做",
                payload={
                    "sheet_id": sheet_id,
                    "sheet_title": sheet.title,
                    "row_id": row_id,
                    "item_name": old_row.item_name,
                },
            )
        await session.commit()
    except SheetRowConflict as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "row conflict") from exc
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "row not found")
    result = await sheet_repo.get_row(session, sheet_id, row_id)
    name = result[1] if result is not None else None
    await session.refresh(row)
    return RowDetail(**_row_dict(row, name))


@router.post(
    "/{sheet_id}/rows/{row_id}/contribute",
    response_model=RowDetail,
)
async def contribute_to_row(
    sheet_id: int,
    row_id: int,
    body: RowContributeRequest,
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
) -> RowDetail:
    """progress 行增量上交（任意登录玩家）。lock 行 → 409（应用 claim）。

    delivered_qty += qty（不封顶）；幂等加入贡献者；累计≥need 自动 done。
    """
    sheet = await _load_sheet_or_404(session, sheet_id)
    try:
        row = await sheet_repo.contribute_row(
            session, sheet_id, row_id, player.uuid, body.qty
        )
        if row is not None:
            is_done = row.delivered_qty >= row.need_qty
            await _notify(
                session,
                recipient_uuid=sheet.owner_uuid,
                actor=player,
                category="sheet_done" if is_done else "sheet_delivered",
                title="物品已备齐" if is_done else "物品收到上交",
                body=(
                    f"{player.current_name} 上交 {body.qty}，已备齐 [{row.item_name}]"
                    f"（累计 {row.delivered_qty}/{row.need_qty}）"
                    if is_done
                    else f"{player.current_name} 上交 {body.qty}"
                    f"（累计 {row.delivered_qty}/{row.need_qty}）[{row.item_name}]"
                ),
                payload={
                    "sheet_id": sheet_id,
                    "sheet_title": sheet.title,
                    "row_id": row_id,
                    "item_name": row.item_name,
                    "delta": body.qty,
                    "delivered": row.delivered_qty,
                    "need": row.need_qty,
                },
            )
        await session.commit()
    except SheetRowConflict as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "row conflict") from exc
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "row not found")
    result = await sheet_repo.get_row(session, sheet_id, row_id)
    name = result[1] if result is not None else None
    contrib_map = await sheet_repo.list_contributors(session, [row.id])
    await session.refresh(row)
    return RowDetail(
        **_row_dict(
            row,
            name,
            [
                {"player_uuid": pu, "player_name": pn}
                for pu, pn in contrib_map.get(row.id, [])
            ],
        )
    )


@router.patch(
    "/{sheet_id}/rows/{row_id}/progress",
    response_model=RowDetail,
)
async def set_row_progress(
    sheet_id: int,
    row_id: int,
    body: RowProgressRequest,
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
) -> RowDetail:
    """progress 行 owner 直接修正进度（绝对值，可增可减）。仅表拥有者/admin。

    与 contribute（增量、任意玩家、加贡献者）互补：本端点供拥有者修正/回退进度，
    直接覆写 delivered_qty，按新值重算 status，**不动 contributors**（保留下交历史）。
    lock 行 → 409（请用 /delivery）。
    """
    sheet = await _load_sheet_or_404(session, sheet_id)
    if not _can_edit(sheet, player):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    try:
        row = await sheet_repo.set_row_progress(
            session, sheet_id, row_id, body.delivered_qty
        )
        if row is not None:
            # owner 自改 → actor==recipient，_notify 自动跳过；
            # admin 改 → 通知 owner 进度被调整
            await _notify(
                session,
                recipient_uuid=sheet.owner_uuid,
                actor=player,
                category="sheet_qty_changed",
                title="进度已调整",
                body=(
                    f"{player.current_name} 将 [{row.item_name}] 的进度"
                    f"调整为 {body.delivered_qty}/{row.need_qty}"
                ),
                payload={
                    "sheet_id": sheet_id,
                    "sheet_title": sheet.title,
                    "row_id": row_id,
                    "item_name": row.item_name,
                    "delivered": body.delivered_qty,
                    "need": row.need_qty,
                },
            )
        await session.commit()
    except SheetRowConflict as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "row conflict") from exc
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "row not found")
    result = await sheet_repo.get_row(session, sheet_id, row_id)
    name = result[1] if result is not None else None
    contrib_map = await sheet_repo.list_contributors(session, [row.id])
    await session.refresh(row)
    return RowDetail(
        **_row_dict(
            row,
            name,
            [
                {"player_uuid": pu, "player_name": pn}
                for pu, pn in contrib_map.get(row.id, [])
            ],
        )
    )
