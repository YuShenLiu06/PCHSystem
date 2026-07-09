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
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import PlainTextResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_player, require_service_token
from app.core.config import get_settings
from app.core.db import get_session
from app.models.sheet import Sheet, SheetRow
from app.models.sheet import (
    SHEET_PHASE_ARCHIVED,
    SHEET_PHASE_COLLECTING,
    SHEET_PHASE_CONSTRUCTING,
)
from app.models.user import Player
from app.repositories import sheet_repo
from app.repositories.player_repo import set_last_sheet
from app.repositories.sheet_repo import SheetArchived, SheetRowConflict
from app.schemas.sheet import (
    RowContributeRequest,
    RowDeliveryRequest,
    RowDetail,
    RowProgressRequest,
    RowUpsertRequest,
    SheetCreateRequest,
    SheetDetail,
    SheetFromItemsRequest,
    SheetPatchRequest,
    SheetSummary,
)
from app.services import notification_service
from app.services.archive import (
    ArchiveNotConfigured,
    SheetNotFoundError,
    SheetStatusError,
    archive_sheet,
    read_archive_bytes,
    read_archive_file,
)
from app.services.parsing import preview as preview_service

router = APIRouter(prefix="/sheets", tags=["sheets"])

# 阶段过滤合法值（GET /sheets?status= 与 advance ?to= 共用枚举校验）。
_VALID_STATUS_FILTERS = frozenset(
    {SHEET_PHASE_COLLECTING, SHEET_PHASE_CONSTRUCTING, SHEET_PHASE_ARCHIVED, "active"}
)
# advance ?to= 合法目标（不含 active，active 仅用于过滤）。
_VALID_ADVANCE_TARGETS = frozenset({SHEET_PHASE_CONSTRUCTING, SHEET_PHASE_ARCHIVED})


# 翻译器单例（复用 translators/lang/*.zh_cn.json，进程级 lru_cache）：registry_id → 中文 item_name。
# 后续新增 mod 翻译表只需往 lang/ 目录加 JSON，本单例自动合并，零改动。
_translator = preview_service.get_default_translator()

logger = logging.getLogger(__name__)


def _resolve_item_name(item_name: str | None, registry_id: str | None) -> str:
    """item_name 缺失时用 registry_id 翻译补默认中文名；未命中回退 registry_id 本身。

    供新建路径（from-items / 无 row_id upsert）落库前补默认名（MCDR addhand 只传
    registry_id 时走此路径）。schema 的 model_validator 已保证二者至少有一个非空；
    此处仍防御性返回 422（不裸 assert 致 500，issue #20 回归）。
    """
    if item_name:
        return item_name
    if registry_id is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "item_name 与 registry_id 至少提供一个",
        )
    return _translator.translate(registry_id)


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
    }


def _to_summary(sheet: Sheet, owner_name: str) -> SheetSummary:
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
) -> SheetDetail:
    cmap = contributors_map or {}
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


@router.post("/from-items", response_model=SheetDetail, status_code=status.HTTP_201_CREATED)
async def create_sheet_from_items(
    body: SheetFromItemsRequest,
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
) -> SheetDetail:
    """按材料清单一次性建表 + 批量行（mode 默认 lock）；调用方=拥有者。单事务 commit。

    用于「投影解析→生成表格」：方块组 / 容器组各调一次。``items`` 条数由 schema 限 ≤2000。
    """
    sheet = await sheet_repo.create_sheet(session, player.uuid, body.title)
    for item in body.items:
        item_name = _resolve_item_name(item.item_name, item.registry_id)
        try:
            await sheet_repo.create_row(
                session,
                sheet_id=sheet.id,
                item_name=item_name,
                need_qty=item.need_qty if item.need_qty is not None else 0,
                mode=item.mode if item.mode is not None else sheet_repo.MODE_LOCK,
                sort_order=item.sort_order if item.sort_order is not None else 0,
                registry_id=item.registry_id,
            )
        except IntegrityError as exc:  # 批量内同名 / 撞已存在名 → UNIQUE
            await session.rollback()
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"物品名重复：{item_name} 已存在",
            ) from exc
    await session.commit()
    result = await sheet_repo.get_sheet(session, sheet.id)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sheet not found")
    sheet_obj, owner_name = result
    rows_with_names = await sheet_repo.list_rows(session, sheet.id)
    return _to_detail(sheet_obj, rows_with_names, owner_name)


@router.get("", response_model=list[SheetSummary])
async def list_sheets(
    owner: str | None = Query(default=None, description="过滤：传 me 只看自己"),
    status_filter: str | None = Query(
        default=None,
        alias="status",
        description="阶段过滤：collecting / constructing / archived / active（=collecting+constructing）",
    ),
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
) -> list[SheetSummary]:
    owner_uuid = player.uuid if owner == "me" else None
    sheets_with_names = await sheet_repo.list_sheets(
        session, owner_uuid=owner_uuid, status_filter=status_filter, player_uuid=player.uuid
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
    # 有意为之：GET 详情时自动记录 last_sheet_id（best-effort，失败不影响返回）
    try:
        await set_last_sheet(session, player.uuid, sheet_id)
        await session.commit()
    except Exception:
        logger.exception("record last_sheet_id failed player=%s sheet=%s", player.uuid, sheet_id)
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


async def _collect_progress_contributors(
    session: AsyncSession, row: SheetRow
) -> list[tuple[uuid.UUID, str]]:
    """mode 从 progress 变走时，repo 会 clear_contributors —— 预取名单用于事后通知。"""
    _contrib_map = await sheet_repo.list_contributors(session, [row.id])
    return _contrib_map.get(row.id, [])


async def _dispatch_row_edit_notifications(
    session: AsyncSession,
    *,
    sheet: Sheet,
    player: Player,
    row: SheetRow,
    item_name: str,
    mode_changed: bool,
    prev_mode: int | None,
    old_need: int | None,
    new_need: int,
    claimant_uuid: uuid.UUID | None,
    progress_contributors: list[tuple[uuid.UUID, str]],
) -> None:
    """行编辑后的通知派发（新建/更新两路径共用，DRY）。

    - mode 变化：通知原认领人「认领已重置」；若从 progress 变走，再通知每位贡献者。
    - mode 不变但 need 变化：通知认领人「所需数量已调整」。
    """
    sheet_id = sheet.id
    if mode_changed and claimant_uuid is not None:
        # 换模式重置了行 → 通知原认领人协作已取消
        await _notify(
            session,
            recipient_uuid=claimant_uuid,
            actor=player,
            category="sheet_released",
            title="模式变更，认领已重置",
            body=f"[{item_name}] 拥有者调整了模式，认领/进度已重置",
            payload={
                "sheet_id": sheet_id,
                "sheet_title": sheet.title,
                "row_id": row.id,
                "item_name": item_name,
            },
        )
    if mode_changed and prev_mode == sheet_repo.MODE_PROGRESS:
        # progress→lock：repo 已清空贡献者，通知每位贡献者贡献已被重置
        for contrib_uuid, _cname in progress_contributors:
            await _notify(
                session,
                recipient_uuid=contrib_uuid,
                actor=player,
                category="sheet_progress_reset",
                title="贡献已被拥有者清空",
                body=f"拥有者调整了 [{item_name}] 的模式，进度与贡献已重置",
                payload={
                    "sheet_id": sheet_id,
                    "sheet_title": sheet.title,
                    "row_id": row.id,
                    "item_name": item_name,
                },
            )
    elif (
        not mode_changed
        and claimant_uuid is not None
        and old_need is not None
        and old_need != new_need
    ):
        await _notify(
            session,
            recipient_uuid=claimant_uuid,
            actor=player,
            category="sheet_qty_changed",
            title="所需数量已调整",
            body=f"[{item_name}] 所需数量变为 {new_need}（原 {old_need}）",
            payload={
                "sheet_id": sheet_id,
                "sheet_title": sheet.title,
                "row_id": row.id,
                "item_name": item_name,
                "old": old_need,
                "new": new_need,
            },
        )


async def _create_row_by_item(
    session: AsyncSession, sheet: Sheet, body: RowUpsertRequest
) -> SheetRow:
    """新建路径（无 row_id）：严格 INSERT，不再 upsert 覆盖同名（issue #20 后 item_name 是数据字段）。

    同名由 ``UNIQUE(sheet_id, item_name)`` 兜底 → IntegrityError → api 翻译 409（中文）。
    need_qty/mode/sort_order 缺省 coerce 到 0 / lock(0) / 0。新建行无旧协作状态，不发通知。
    """
    item_name = _resolve_item_name(body.item_name, body.registry_id)
    row = await sheet_repo.create_row(
        session,
        sheet_id=sheet.id,
        item_name=item_name,
        need_qty=body.need_qty if body.need_qty is not None else 0,
        mode=body.mode if body.mode is not None else sheet_repo.MODE_LOCK,
        sort_order=body.sort_order if body.sort_order is not None else 0,
        registry_id=body.registry_id,
    )
    return row


async def _update_row_by_id(
    session: AsyncSession, sheet: Sheet, player: Player, body: RowUpsertRequest
) -> SheetRow:
    """更新路径（带 row_id）：按主键部分更新（可改名；item_name 是数据字段非定位键）。

    item_name 未传 → 保留原名；need_qty/mode/sort_order 未传 → 不改（None 透传给
    repo ``update_row`` 跳过）。mode 仅在传入且变化时重置协作。issue #20：改名不再新建行。
    """
    prev = await sheet_repo.get_row(session, sheet.id, body.row_id)
    if prev is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "row not found")
    prev_row, _ = prev
    old_need = prev_row.need_qty
    claimant_uuid = prev_row.claimant_uuid
    prev_mode = prev_row.mode
    mode_changed = body.mode is not None and prev_mode != body.mode
    progress_contributors: list[tuple[uuid.UUID, str]] = (
        await _collect_progress_contributors(session, prev_row)
        if mode_changed and prev_mode == sheet_repo.MODE_PROGRESS
        else []
    )
    # item_name 未传 → 保留原名用于通知文案（更新路径不做 registry→name 翻译）；
    # need 未传 → new_need 取原值，使 old_need != new_need 为 False（不发数量通知）
    item_name = body.item_name if body.item_name is not None else prev_row.item_name
    new_need = body.need_qty if body.need_qty is not None else old_need
    row = await sheet_repo.update_row(
        session,
        sheet_id=sheet.id,
        row_id=body.row_id,
        item_name=body.item_name,
        registry_id=body.registry_id,
        need_qty=body.need_qty,
        mode=body.mode,
        sort_order=body.sort_order,
    )
    if row is None:
        # 并发删行竞态：上面 get_row 之后、update_row FOR UPDATE 锁行之前，行被另一事务
        # 删除并提交 → update_row 返 None。干净 404，避免下游 _dispatch 解引用 / refresh(None) 致 500。
        raise HTTPException(status.HTTP_404_NOT_FOUND, "row not found")
    await _dispatch_row_edit_notifications(
        session,
        sheet=sheet,
        player=player,
        row=row,
        item_name=item_name,
        mode_changed=mode_changed,
        prev_mode=prev_mode,
        old_need=old_need,
        new_need=new_need,
        claimant_uuid=claimant_uuid,
        progress_contributors=progress_contributors,
    )
    return row


@router.put("/{sheet_id}/rows", response_model=RowDetail)
async def upsert_row(
    sheet_id: int,
    body: RowUpsertRequest,
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
) -> RowDetail:
    """行新建 / 更新（单端点按 ``row_id`` 分流；issue #20 改名重复修复）。

    - **带 ``row_id``**：按主键**更新**（``_update_row_by_id``），item_name 可改名，
      其余字段部分更新（未传=不改）。改名不再新建重复行。
    - **不带 ``row_id``**：按 ``item_name`` **严格新建**（``_create_row_by_item``），
      同名不再覆盖 → 409。

    archived 终态 → 409（只读）；新建/改名撞 ``UNIQUE(sheet_id, item_name)`` → 409（中文）。
    """
    sheet = await _load_sheet_or_404(session, sheet_id)
    if not _can_edit(sheet, player):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    try:
        if body.row_id is not None:
            row = await _update_row_by_id(session, sheet, player, body)
        else:
            row = await _create_row_by_item(session, sheet, body)
        await session.commit()
    except SheetArchived:
        await session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "项目已归档，只读"
        )
    except IntegrityError as exc:  # 新建/改名撞 UNIQUE(sheet_id, item_name)
        await session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "物品名重复：该项目已存在同名行，请编辑该行而非新建",
        ) from exc
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
    prev_mode = old_row.mode
    is_claimant_self = prev_claimant == player.uuid
    if not is_claimant_self and not _can_edit(sheet, player):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    # progress 行：repo release 会 clear_contributors，必须在 repo 调用前预取名单
    progress_contributors: list[tuple[uuid.UUID, str]] = []
    if prev_mode == sheet_repo.MODE_PROGRESS:
        _contrib_map = await sheet_repo.list_contributors(session, [old_row.id])
        progress_contributors = _contrib_map.get(old_row.id, [])
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
        # progress 行：owner 解除会清空贡献者，通知每位贡献者（actor==recipient 自动跳过）
        if row is not None and prev_mode == sheet_repo.MODE_PROGRESS:
            for contrib_uuid, _cname in progress_contributors:
                await _notify(
                    session,
                    recipient_uuid=contrib_uuid,
                    actor=player,
                    category="sheet_progress_reset",
                    title="贡献已被拥有者清空",
                    body=f"拥有者解除了 [{prev_item}] 的进度行，你的贡献已清空",
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
    current = await sheet_repo.get_row(session, sheet_id, row_id)
    if current is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "row not found")
    old_row = current[0]
    if old_row.mode != sheet_repo.MODE_PROGRESS:
        # lock 行用 /delivery；提前 409（repo mode 守卫同样会抛，这里更早失败）
        raise HTTPException(status.HTTP_409_CONFLICT, "row conflict")
    old_delivered = old_row.delivered_qty
    prev_item = old_row.item_name
    # repo set_row_progress 不动 contributors，提前快照用于通知（与 release 一致）
    contrib_snapshot: list[tuple[uuid.UUID, str]] = (
        (await sheet_repo.list_contributors(session, [old_row.id])).get(old_row.id, [])
    )
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
            # 通知每位贡献者进度被调整（actor==recipient 自动跳过；
            # 同值不通知，避免 owner 误操作产生噪音）
            if body.delivered_qty != old_delivered:
                for contrib_uuid, _cname in contrib_snapshot:
                    await _notify(
                        session,
                        recipient_uuid=contrib_uuid,
                        actor=player,
                        category="sheet_progress_changed",
                        title="进度已被拥有者调整",
                        body=(
                            f"拥有者将 [{prev_item}] 的进度调整为 "
                            f"{body.delivered_qty}/{row.need_qty}（原 {old_delivered}）"
                        ),
                        payload={
                            "sheet_id": sheet_id,
                            "sheet_title": sheet.title,
                            "row_id": row_id,
                            "item_name": prev_item,
                            "old": old_delivered,
                            "new": body.delivered_qty,
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



# ---------- 项目阶段生命周期（collecting → constructing → archived） ----------


def _infer_advance_target(current_status: str) -> str:
    """缺省 ``to`` 时按状态机推进下一态：collecting→constructing，constructing→archived。

    archived 态本就不该再 advance（调用前 _can_edit 通过后，archived 态会进入
    advance_sheet raise SheetArchived→409）；这里只处理活跃态的默认推进。
    """
    if current_status == SHEET_PHASE_COLLECTING:
        return SHEET_PHASE_CONSTRUCTING
    return SHEET_PHASE_ARCHIVED  # constructing（或防御性其它）默认 → archived


async def _sheet_detail_or_404(session: AsyncSession, sheet_id: int) -> SheetDetail:
    """重新构造 SheetDetail（advance 后取最新 sheet + rows + contributors）。"""
    result = await sheet_repo.get_sheet(session, sheet_id)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sheet not found")
    sheet, owner_name = result
    rows_with_names = await sheet_repo.list_rows(session, sheet_id)
    contributors_map = await sheet_repo.list_contributors(
        session, [r.id for r, _ in rows_with_names]
    )
    return _to_detail(sheet, rows_with_names, owner_name, contributors_map)


@router.post("/{sheet_id}/advance", response_model=SheetDetail)
async def advance_sheet_phase(
    sheet_id: int,
    to: str | None = Query(
        default=None, description="目标阶段：constructing / archived；缺省按状态机推进"
    ),
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
) -> SheetDetail:
    """项目阶段流转（owner/admin）。

    - ``to=constructing``：collecting → constructing（repo advance_sheet，api 层 commit）。
    - ``to=archived``：collecting/constructing → archived（走 archive_service：写盘 +
      通知 + 内部 commit）。允许 collecting 直跳跳过施工。
    - ``to`` 缺省：按当前状态推进下一态（collecting→constructing，constructing→archived）。
    - archived 终态：任何 advance → 409（只读）；非 owner/admin → 403。

    事务边界：constructing 路径在 api 层 commit；archived 路径由 archive_sheet 内部 commit。
    """
    if to is not None and to not in _VALID_ADVANCE_TARGETS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"invalid 'to' target: {to} (expected constructing|archived)",
        )
    sheet = await _load_sheet_or_404(session, sheet_id)
    if not _can_edit(sheet, player):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")

    target = to if to is not None else _infer_advance_target(sheet.status)

    if target == SHEET_PHASE_ARCHIVED:
        # 走归档服务：渲染 md → 写盘 → DB 置 archived + 通知 → 内部 commit
        try:
            await archive_sheet(
                session,
                sheet_id,
                archive_root=get_settings().archive_root,
                player=player,
            )
        except SheetNotFoundError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "sheet not found")
        except SheetArchived:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "sheet is archived, read-only"
            )
        except SheetStatusError:
            raise HTTPException(status.HTTP_409_CONFLICT, "illegal phase transition")
        except ArchiveNotConfigured:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "archive root not configured"
            )
    else:
        # → constructing：repo advance_sheet，api 层 commit
        try:
            advanced = await sheet_repo.advance_sheet(
                session, sheet_id, SHEET_PHASE_CONSTRUCTING
            )
        except SheetArchived:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "sheet is archived, read-only"
            )
        except SheetRowConflict as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        if advanced is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "sheet not found")
        await session.commit()
        await session.refresh(advanced)
    return await _sheet_detail_or_404(session, sheet_id)


@router.get("/{sheet_id}/archive")
async def get_sheet_archive(
    sheet_id: int,
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
) -> Response:
    """读归档 markdown（text/markdown）。未归档 / 文件缺失 → 404。"""
    result = await sheet_repo.get_sheet(session, sheet_id)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sheet not found")
    sheet, _owner_name = result
    if sheet.status != SHEET_PHASE_ARCHIVED or not sheet.archived_path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sheet is not archived")
    md = read_archive_file(get_settings().archive_root, sheet.archived_path)
    if md is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "archive file missing")
    return Response(content=md, media_type="text/markdown")


# 资产白名单：归档目录下允许读取的产物文件名（basename only，纵深防御）。
_ARCHIVE_ASSET_WHITELIST = frozenset({"contributions.png"})


@router.get("/{sheet_id}/archive/assets/{filename}")
async def get_sheet_archive_asset(
    sheet_id: int,
    filename: str,
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
) -> Response:
    """读归档产物（如 contributions.png，image/png）。非法名 / 未归档 / 缺失 → 404。

    filename 必须命中 basename 白名单；rel_path = archived_path 父目录 + filename，
    再经 read_archive_bytes 的路径穿越守卫（_assert_within）双保险。
    """
    if filename not in _ARCHIVE_ASSET_WHITELIST:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "invalid asset filename")
    result = await sheet_repo.get_sheet(session, sheet_id)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sheet not found")
    sheet, _owner_name = result
    if sheet.status != SHEET_PHASE_ARCHIVED or not sheet.archived_path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sheet is not archived")
    # archived_path = projects/{id}/index.md → 资产同目录 projects/{id}/{filename}
    parent = sheet.archived_path.rsplit("/", 1)[0]
    data = read_archive_bytes(get_settings().archive_root, f"{parent}/{filename}")
    if data is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "asset not found")
    return Response(content=data, media_type="image/png")
