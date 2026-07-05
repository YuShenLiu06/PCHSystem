"""SheetRepository 函数式实现（sheets schema）。

镜像 ``auth_token_repo.py`` 风格：函数签名收 ``AsyncSession``，只 ``flush()``，
由 api 层负责 ``commit()``。upsert / 状态机转移用 ``select(...).with_for_update()``
锁行后判定，并发同名 insert 触发 IntegrityError 上抛（api 层翻译为 409），
非法状态转移 raise ``SheetRowConflict``（api 层翻译为 409）。

身份锚 = owner_uuid / claimant_uuid（FK→users.players.uuid，红线 R-5）。
"""
import csv
import io
import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, func, select, union_all
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sheet import (
    SHEET_PHASE_ACTIVE_SET,
    SHEET_PHASE_ARCHIVED,
    SHEET_PHASE_COLLECTING,
    SHEET_PHASE_CONSTRUCTING,
    Sheet,
    SheetRow,
    SheetRowContributor,
)
from app.models.user import Player

# mode（D-3）
MODE_LOCK, MODE_PROGRESS = 0, 1
# status（D-6，spec §5.2）
STATUS_OPEN, STATUS_CLAIMED, STATUS_DONE = "open", "claimed", "done"

_CSV_HEADER = [
    "sheet_id",
    "item_name",
    "registry_id",
    "need_qty",
    "mode",
    "status",
    "claimant_uuid",
    "delivered_qty",
    "sort_order",
]


class SheetRowConflict(Exception):
    """行状态非法转移/不变量违反，api 层翻译为 409。"""


class SheetArchived(Exception):
    """sheet 已归档只读，api 层翻译为 409。任何写操作入口先经 _assert_writable 守卫。"""


async def _assert_writable(session: AsyncSession, sheet_id: int) -> None:
    """写操作入口守卫：sheet 不存在 → 不做（让后续逻辑返 None→404）；archived → raise SheetArchived。

    只查 status 列（轻量）；非 archived 状态（collecting/constructing）放行。
    """
    stmt = select(Sheet.status).where(Sheet.id == sheet_id)
    status = (await session.execute(stmt)).scalar_one_or_none()
    if status is None:
        # sheet 不存在，交给调用方走 None → 404 分支
        return
    if status == SHEET_PHASE_ARCHIVED:
        raise SheetArchived(f"sheet {sheet_id} is archived (read-only)")


async def advance_sheet(
    session: AsyncSession,
    sheet_id: int,
    to_status: str,
    *,
    archived_path: str | None = None,
) -> Sheet | None:
    """项目阶段状态机（owner/admin 触发，api 层做权限校验）。

    合法转移：
    - collecting → constructing
    - collecting → archived（跳过施工，要求 archived_path 非空）
    - constructing → archived（要求 archived_path 非空）
    archived = 终态只读。

    异常：
    - sheet 不存在 → return None（api 层 404）。
    - 当前已 archived → SheetArchived（终态，不可再流转）。
    - to_status == 当前状态 → SheetRowConflict（幂等拒绝，避免重复通知/覆盖 archived_at）。
    - to_status=archived 但 archived_path 为空 → ValueError（调用方契约违反）。
    - 其他非法转移（如 constructing→collecting、to_status 非法值）→ SheetRowConflict。

    SELECT FOR UPDATE 锁行防并发归档；flush 不 commit（api 层负责 commit + 通知联动）。
    """
    stmt = (
        select(Sheet).where(Sheet.id == sheet_id).with_for_update()
    )
    sheet = (await session.execute(stmt)).scalar_one_or_none()
    if sheet is None:
        return None
    if sheet.status == SHEET_PHASE_ARCHIVED:
        raise SheetArchived(f"sheet {sheet_id} is archived (terminal)")
    if to_status == sheet.status:
        raise SheetRowConflict(
            f"sheet {sheet_id} already in status {to_status}"
        )
    if to_status == SHEET_PHASE_CONSTRUCTING and sheet.status == SHEET_PHASE_COLLECTING:
        sheet.status = SHEET_PHASE_CONSTRUCTING
    elif to_status == SHEET_PHASE_ARCHIVED and sheet.status in (
        SHEET_PHASE_COLLECTING,
        SHEET_PHASE_CONSTRUCTING,
    ):
        if not archived_path:
            raise ValueError("archived_path required for archived transition")
        sheet.status = SHEET_PHASE_ARCHIVED
        sheet.archived_path = archived_path
        sheet.archived_at = datetime.now(timezone.utc)
    else:
        # 非法转移（含 to_status 不在枚举内、constructing→collecting 回退等）
        raise SheetRowConflict(
            f"illegal transition {sheet.status} -> {to_status}"
        )
    await session.flush()
    return sheet


async def create_sheet(
    session: AsyncSession, owner_uuid: uuid.UUID, title: str
) -> Sheet:
    sheet = Sheet(owner_uuid=owner_uuid, title=title)
    session.add(sheet)
    await session.flush()
    return sheet


async def get_sheet(
    session: AsyncSession, sheet_id: int
) -> tuple[Sheet, str] | None:
    """单表详情：inner join players 取 owner 游戏名。返回 (Sheet, owner_name) 或 None。"""
    stmt = (
        select(Sheet, Player.current_name)
        .join(Player, Player.uuid == Sheet.owner_uuid)
        .where(Sheet.id == sheet_id)
    )
    row = (await session.execute(stmt)).first()
    if row is None:
        return None
    return row[0], row[1]


async def list_sheets(
    session: AsyncSession,
    owner_uuid: uuid.UUID | None = None,
    status_filter: str | None = None,
) -> list[tuple[Sheet, str]]:
    """列所有表：inner join players 取 owner 游戏名。返回 [(Sheet, owner_name)]。

    status_filter（与 owner_uuid 可组合）：
    - None → 不过滤；
    - "active" → status ∈ (collecting, constructing)；
    - 单值（collecting/constructing/archived）→ status == 该值。
    按 sheet id 升序（与历史行为一致）。
    """
    stmt = (
        select(Sheet, Player.current_name)
        .join(Player, Player.uuid == Sheet.owner_uuid)
        .order_by(Sheet.id)
    )
    if owner_uuid is not None:
        stmt = stmt.where(Sheet.owner_uuid == owner_uuid)
    if status_filter == "active":
        stmt = stmt.where(Sheet.status.in_(SHEET_PHASE_ACTIVE_SET))
    elif status_filter is not None:
        stmt = stmt.where(Sheet.status == status_filter)
    return [(r[0], r[1]) for r in (await session.execute(stmt)).all()]


async def list_rows(
    session: AsyncSession, sheet_id: int
) -> list[tuple[SheetRow, str | None]]:
    """列单表所有行：left join players 取认领人游戏名。返回 [(SheetRow, claimant_name|None)]。"""
    stmt = (
        select(SheetRow, Player.current_name)
        .outerjoin(Player, Player.uuid == SheetRow.claimant_uuid)
        .where(SheetRow.sheet_id == sheet_id)
        .order_by(SheetRow.sort_order, SheetRow.id)
    )
    return [(r[0], r[1]) for r in (await session.execute(stmt)).all()]


async def get_row(
    session: AsyncSession, sheet_id: int, row_id: int
) -> tuple[SheetRow, str | None] | None:
    """单行：left join players 取认领人名。给端点构造单行响应/权限判断用。"""
    stmt = (
        select(SheetRow, Player.current_name)
        .outerjoin(Player, Player.uuid == SheetRow.claimant_uuid)
        .where(SheetRow.sheet_id == sheet_id, SheetRow.id == row_id)
    )
    row = (await session.execute(stmt)).first()
    if row is None:
        return None
    return row[0], row[1]


async def get_row_by_item(
    session: AsyncSession, sheet_id: int, item_name: str
) -> SheetRow | None:
    """按 (sheet_id, item_name) UNIQUE 锁点查行（upsert 前捕获旧状态用）。"""
    stmt = select(SheetRow).where(
        SheetRow.sheet_id == sheet_id, SheetRow.item_name == item_name
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def upsert_row(
    session: AsyncSession,
    sheet_id: int,
    item_name: str,
    need_qty: int,
    mode: int,
    sort_order: int,
    registry_id: str | None = None,
) -> SheetRow:
    """按 UNIQUE(sheet_id, item_name) upsert（拥有者改需求/mode/sort/registry_id）。在则改，不在则 insert。

    新建行：status=open / claimant=None / delivered=0。
    更新行 mode 不变：仅改 need_qty/sort_order（+ registry_id 仅当传入非 None），保留 status/claimant/delivered；
    按 spec §5.3 封顶 delivered 并按新 need 重算 status。
    更新行 mode 变化：重置协作（status=open/claimant=None/delivered=0/清贡献者），
    避免违反 progress 不变量（claimant 恒 null）。
    registry_id=None 时不覆盖已有值（避免 upsert 其它字段时误擦匹配键）。
    并发同名 insert 会触发 IntegrityError，上抛交 api 层翻译为 409。
    """
    await _assert_writable(session, sheet_id)
    stmt = (
        select(SheetRow)
        .where(SheetRow.sheet_id == sheet_id, SheetRow.item_name == item_name)
        .with_for_update()
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is not None:
        mode_changed = row.mode != mode
        row.need_qty = need_qty
        row.mode = mode
        row.sort_order = sort_order
        if registry_id is not None:
            # None 不覆盖：避免 upsert 其它字段时误擦已有 registry_id（匹配键）
            row.registry_id = registry_id
        if mode_changed:
            # 换模式 = 重新开始：清协作状态，避免违反 progress 不变量（claimant 恒 null）
            row.status = STATUS_OPEN
            row.claimant_uuid = None
            row.delivered_qty = 0
            await clear_contributors(session, row.id)
        else:
            # mode 不变：保留进度，按新 need 封顶（need=0 = 无目标，不封顶）
            if need_qty > 0 and row.delivered_qty > need_qty:
                row.delivered_qty = need_qty
            if (
                row.status in (STATUS_CLAIMED, STATUS_DONE)
                and need_qty > 0
                and row.delivered_qty >= need_qty
            ):
                row.status = STATUS_DONE
            elif row.status == STATUS_DONE and row.delivered_qty < need_qty:
                row.status = STATUS_CLAIMED
        await session.flush()
        return row
    row = SheetRow(
        sheet_id=sheet_id,
        item_name=item_name,
        need_qty=need_qty,
        mode=mode,
        sort_order=sort_order,
        registry_id=registry_id,
    )
    session.add(row)
    await session.flush()
    return row


async def _lock_row(
    session: AsyncSession, sheet_id: int, row_id: int
) -> SheetRow | None:
    stmt = (
        select(SheetRow)
        .where(SheetRow.sheet_id == sheet_id, SheetRow.id == row_id)
        .with_for_update()
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def claim_row(
    session: AsyncSession, sheet_id: int, row_id: int, claimant_uuid: uuid.UUID
) -> SheetRow | None:
    """lock 行 open → claimed：置 claimant、delivered=0。progress 行 / 非 open 行视为非法转移。"""
    await _assert_writable(session, sheet_id)
    row = await _lock_row(session, sheet_id, row_id)
    if row is None:
        return None
    if row.mode == MODE_PROGRESS:
        raise SheetRowConflict("progress rows use contribute, not claim")
    if row.status != STATUS_OPEN:
        raise SheetRowConflict(f"cannot claim row in status {row.status}")
    row.status = STATUS_CLAIMED
    row.claimant_uuid = claimant_uuid
    row.delivered_qty = 0
    await session.flush()
    return row


async def set_row_delivery(
    session: AsyncSession, sheet_id: int, row_id: int, delivered_qty: int
) -> SheetRow | None:
    """lock 行 claimed/done → 设 delivered；delivered>=need 自动 done，否则 claimed。progress 行用 contribute。"""
    await _assert_writable(session, sheet_id)
    row = await _lock_row(session, sheet_id, row_id)
    if row is None:
        return None
    if row.mode == MODE_PROGRESS:
        raise SheetRowConflict("progress rows use contribute, not delivery")
    if row.status not in (STATUS_CLAIMED, STATUS_DONE):
        raise SheetRowConflict(f"cannot set delivery on row in status {row.status}")
    row.delivered_qty = delivered_qty
    row.status = STATUS_DONE if delivered_qty >= row.need_qty else STATUS_CLAIMED
    await session.flush()
    return row


async def set_row_progress(
    session: AsyncSession, sheet_id: int, row_id: int, delivered_qty: int
) -> SheetRow | None:
    """progress 行 owner 直接修正进度（绝对值，可增可减）：按新值重算 status，**不动 contributors**。

    仅 progress 行可用（lock 行 raise SheetRowConflict，请用 set_row_delivery）。
    delivered_qty=0 → open；0<x<need → claimed；>=need → done。
    保留 contributors（上交历史），即使 owner 把进度调回 0 也不清贡献者名单。
    """
    await _assert_writable(session, sheet_id)
    row = await _lock_row(session, sheet_id, row_id)
    if row is None:
        return None
    if row.mode != MODE_PROGRESS:
        raise SheetRowConflict("lock rows use set_row_delivery, not progress")
    row.delivered_qty = delivered_qty
    if delivered_qty == 0:
        row.status = STATUS_OPEN
    elif row.need_qty > 0 and delivered_qty >= row.need_qty:
        row.status = STATUS_DONE
    else:
        row.status = STATUS_CLAIMED  # need=0 + delivered>0 → 无目标，永不 done
    await session.flush()
    return row


async def release_row(
    session: AsyncSession, sheet_id: int, row_id: int
) -> SheetRow | None:
    """claimed/done → open：清 claimant、delivered=0、清贡献者（progress 行）。"""
    await _assert_writable(session, sheet_id)
    row = await _lock_row(session, sheet_id, row_id)
    if row is None:
        return None
    if row.status not in (STATUS_CLAIMED, STATUS_DONE):
        raise SheetRowConflict(f"cannot release row in status {row.status}")
    row.status = STATUS_OPEN
    row.claimant_uuid = None
    row.delivered_qty = 0
    await clear_contributors(session, row.id)
    await session.flush()
    return row


async def reject_row(
    session: AsyncSession, sheet_id: int, row_id: int
) -> SheetRow | None:
    """lock 行 done → claimed：delivered 归零，claimant 保留重做。progress 行无 reject（用 release）。"""
    await _assert_writable(session, sheet_id)
    row = await _lock_row(session, sheet_id, row_id)
    if row is None:
        return None
    if row.mode == MODE_PROGRESS:
        raise SheetRowConflict("progress rows have no reject; use release")
    if row.status != STATUS_DONE:
        raise SheetRowConflict(f"cannot reject row in status {row.status}")
    row.status = STATUS_CLAIMED
    row.delivered_qty = 0
    await session.flush()
    return row


async def contribute_row(
    session: AsyncSession,
    sheet_id: int,
    row_id: int,
    player_uuid: uuid.UUID,
    qty: int,
) -> SheetRow | None:
    """progress 行增量上交（任意玩家）：delivered += qty，幂等加入贡献者，重算 status。

    仅 progress 行可用（lock 行 raise SheetRowConflict）；done 行不再收上交。
    delivered 不封顶 need（允许超额，status=done）。
    幂等加贡献者：ON CONFLICT (row_id, player_uuid) DO NOTHING。
    """
    await _assert_writable(session, sheet_id)
    row = await _lock_row(session, sheet_id, row_id)
    if row is None:
        return None
    if row.mode != MODE_PROGRESS:
        raise SheetRowConflict(f"cannot contribute on row in mode {row.mode}")
    # need=0 = 无目标（无限收集），永不 done，可一直上交；仅 need>0 的 done 行拒绝重复上交
    if row.status == STATUS_DONE and row.need_qty > 0:
        raise SheetRowConflict("cannot contribute on done row")
    row.delivered_qty += qty
    row.status = (
        STATUS_DONE
        if (row.need_qty > 0 and row.delivered_qty >= row.need_qty)
        else STATUS_CLAIMED
    )
    # 幂等加贡献者并累加每人累计上交量（contributed_qty 供按贡献量排序显示）
    await session.execute(
        pg_insert(SheetRowContributor)
        .values(row_id=row.id, player_uuid=player_uuid, contributed_qty=qty)
        .on_conflict_do_update(
            index_elements=["row_id", "player_uuid"],
            set_={"contributed_qty": SheetRowContributor.contributed_qty + qty},
        )
    )
    await session.flush()
    return row


async def list_contributors(
    session: AsyncSession, row_ids: list[int]
) -> dict[int, list[tuple[uuid.UUID, str]]]:
    """批量取多行贡献者：返回 {row_id: [(player_uuid, player_name), ...]}，按 joined_at 升序。"""
    if not row_ids:
        return {}
    stmt = (
        select(SheetRowContributor.row_id, Player.uuid, Player.current_name)
        .join(Player, Player.uuid == SheetRowContributor.player_uuid)
        .where(SheetRowContributor.row_id.in_(row_ids))
        .order_by(
            SheetRowContributor.row_id,
            SheetRowContributor.contributed_qty.desc(),
            SheetRowContributor.joined_at,
            SheetRowContributor.id,
        )
    )
    result: dict[int, list[tuple[uuid.UUID, str]]] = {}
    for row_id, player_uuid, player_name in (await session.execute(stmt)).all():
        result.setdefault(row_id, []).append((player_uuid, player_name))
    return result


async def clear_contributors(session: AsyncSession, row_id: int) -> None:
    """清空某行贡献者（progress release / upsert 改 mode 重置时调用）。"""
    await session.execute(
        delete(SheetRowContributor).where(SheetRowContributor.row_id == row_id)
    )


async def aggregate_contributor_totals(
    session: AsyncSession, sheet_id: int
) -> list[tuple[uuid.UUID, str, int]]:
    """聚合该 sheet 每个玩家的贡献总量（lock 交付 + progress 上交合并按人）。

    返回 ``[(player_uuid, player_name, total_qty)]``，按 total_qty 降序、
    player_name 升序兜底（同贡献量下名字字母序）。

    两支 union_all 后外层再 GROUP BY player：
    - lock 支：``SUM(delivered_qty)`` GROUP BY ``claimant_uuid``（mode=LOCK 且 claimant 非空）。
    - progress 支：``SUM(contributed_qty)`` GROUP BY ``player_uuid``（mode=PROGRESS，
      join sheet_row_contributors）。
    - 外层 ``HAVING SUM(qty) > 0``：剔除 lock 认领但 delivered=0、以及任何零和玩家。
      同一玩家既是 lock claimant 又是 progress 贡献者 → 两支合并求和。
    空 → []。
    """
    # lock 支：claimant_uuid 是 Player.uuid 子集（FK），复用为统一 player_uuid 列。
    lock_part = (
        select(
            SheetRow.claimant_uuid.label("player_uuid"),
            func.sum(SheetRow.delivered_qty).label("qty"),
        )
        .where(
            SheetRow.sheet_id == sheet_id,
            SheetRow.mode == MODE_LOCK,
            SheetRow.claimant_uuid.is_not(None),
        )
        .group_by(SheetRow.claimant_uuid)
    )
    progress_part = (
        select(
            SheetRowContributor.player_uuid.label("player_uuid"),
            func.sum(SheetRowContributor.contributed_qty).label("qty"),
        )
        .join(SheetRow, SheetRow.id == SheetRowContributor.row_id)
        .where(
            SheetRow.sheet_id == sheet_id,
            SheetRow.mode == MODE_PROGRESS,
        )
        .group_by(SheetRowContributor.player_uuid)
    )
    combined = union_all(lock_part, progress_part).subquery()

    total = func.sum(combined.c.qty).label("total_qty")
    stmt = (
        select(Player.uuid, Player.current_name, total)
        .select_from(combined)
        .join(Player, Player.uuid == combined.c.player_uuid)
        .group_by(Player.uuid, Player.current_name)
        .having(total > 0)
        .order_by(total.desc(), Player.current_name.asc())
    )
    return [
        (pu, pn, qty)
        for pu, pn, qty in (await session.execute(stmt)).all()
    ]


async def delete_row(session: AsyncSession, sheet_id: int, row_id: int) -> int:
    await _assert_writable(session, sheet_id)
    stmt = delete(SheetRow).where(
        SheetRow.sheet_id == sheet_id, SheetRow.id == row_id
    )
    result = await session.execute(stmt)
    return result.rowcount or 0


async def delete_sheet(session: AsyncSession, sheet_id: int) -> int:
    """删表级联 rows（DDL ON DELETE CASCADE 保证 rows 随之消失）。"""
    await _assert_writable(session, sheet_id)
    stmt = delete(Sheet).where(Sheet.id == sheet_id)
    result = await session.execute(stmt)
    return result.rowcount or 0


async def list_all_sheets_with_rows(
    session: AsyncSession,
) -> list[tuple[Sheet, list[SheetRow]]]:
    """全量导出辅助：返回所有表及其 rows，按表 id / 行排序聚合。"""
    sheets_with_names = await list_sheets(session)
    result: list[tuple[Sheet, list[SheetRow]]] = []
    for sheet, _name in sheets_with_names:
        rows_with_names = await list_rows(session, sheet.id)
        result.append((sheet, [r for r, _ in rows_with_names]))
    return result


def _row_to_csv_record(sheet_id: int, row: SheetRow) -> list[str | int]:
    return [
        sheet_id,
        row.item_name,
        row.registry_id or "",
        row.need_qty,
        row.mode,
        row.status,
        str(row.claimant_uuid) if row.claimant_uuid is not None else "",
        row.delivered_qty,
        row.sort_order,
    ]


def export_csv(sheet_id: int, rows: list[SheetRow]) -> str:
    """单表 CSV：表头 + 该表全部 rows。"""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_CSV_HEADER)
    for row in rows:
        writer.writerow(_row_to_csv_record(sheet_id, row))
    return buf.getvalue()


def export_all_csv(sheets_with_rows: list[tuple[Sheet, list[SheetRow]]]) -> str:
    """全量 CSV：所有表的所有 rows 拼成单字符串（service token 调用）。"""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_CSV_HEADER)
    for sheet, rows in sheets_with_rows:
        for row in rows:
            writer.writerow(_row_to_csv_record(sheet.id, row))
    return buf.getvalue()
