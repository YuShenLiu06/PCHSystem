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

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sheet import Sheet, SheetRow
from app.models.user import Player

# mode（D-3）
MODE_LOCK, MODE_PROGRESS = 0, 1
# status（D-6，spec §5.2）
STATUS_OPEN, STATUS_CLAIMED, STATUS_DONE = "open", "claimed", "done"

_CSV_HEADER = [
    "sheet_id",
    "item_name",
    "need_qty",
    "mode",
    "status",
    "claimant_uuid",
    "delivered_qty",
    "sort_order",
]


class SheetRowConflict(Exception):
    """行状态非法转移/不变量违反，api 层翻译为 409。"""


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
    session: AsyncSession, owner_uuid: uuid.UUID | None = None
) -> list[tuple[Sheet, str]]:
    """列所有表：inner join players 取 owner 游戏名。返回 [(Sheet, owner_name)]。"""
    stmt = (
        select(Sheet, Player.current_name)
        .join(Player, Player.uuid == Sheet.owner_uuid)
        .order_by(Sheet.id)
    )
    if owner_uuid is not None:
        stmt = stmt.where(Sheet.owner_uuid == owner_uuid)
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
) -> SheetRow:
    """按 UNIQUE(sheet_id, item_name) upsert（拥有者改需求/mode/sort）。在则改，不在则 insert。

    新建行：status=open / claimant=None / delivered=0。
    更新行：仅改 need_qty/mode/sort_order，保留 status/claimant/delivered；
    并按 spec §5.3 封顶：delivered>新need→delivered=新need；
    若 status∈{claimed,done} 且 delivered>=need→done；status==done 且 delivered<need→claimed。
    并发同名 insert 会触发 IntegrityError，上抛交 api 层翻译为 409。
    """
    stmt = (
        select(SheetRow)
        .where(SheetRow.sheet_id == sheet_id, SheetRow.item_name == item_name)
        .with_for_update()
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is not None:
        row.need_qty = need_qty
        row.mode = mode
        row.sort_order = sort_order
        if row.delivered_qty > need_qty:
            row.delivered_qty = need_qty
        if row.status in (STATUS_CLAIMED, STATUS_DONE) and row.delivered_qty >= need_qty:
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
    """open → claimed：置 claimant、delivered=0。非 open 行视为非法转移。"""
    row = await _lock_row(session, sheet_id, row_id)
    if row is None:
        return None
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
    """claimed/done → 设 delivered；delivered>=need 自动 done，否则 claimed。"""
    row = await _lock_row(session, sheet_id, row_id)
    if row is None:
        return None
    if row.status not in (STATUS_CLAIMED, STATUS_DONE):
        raise SheetRowConflict(f"cannot set delivery on row in status {row.status}")
    row.delivered_qty = delivered_qty
    row.status = STATUS_DONE if delivered_qty >= row.need_qty else STATUS_CLAIMED
    await session.flush()
    return row


async def release_row(
    session: AsyncSession, sheet_id: int, row_id: int
) -> SheetRow | None:
    """claimed/done → open：清 claimant、delivered=0。"""
    row = await _lock_row(session, sheet_id, row_id)
    if row is None:
        return None
    if row.status not in (STATUS_CLAIMED, STATUS_DONE):
        raise SheetRowConflict(f"cannot release row in status {row.status}")
    row.status = STATUS_OPEN
    row.claimant_uuid = None
    row.delivered_qty = 0
    await session.flush()
    return row


async def reject_row(
    session: AsyncSession, sheet_id: int, row_id: int
) -> SheetRow | None:
    """done → claimed：delivered 归零，claimant 保留重做。非 done 行视为非法转移。"""
    row = await _lock_row(session, sheet_id, row_id)
    if row is None:
        return None
    if row.status != STATUS_DONE:
        raise SheetRowConflict(f"cannot reject row in status {row.status}")
    row.status = STATUS_CLAIMED
    row.delivered_qty = 0
    await session.flush()
    return row


async def delete_row(session: AsyncSession, sheet_id: int, row_id: int) -> int:
    stmt = delete(SheetRow).where(
        SheetRow.sheet_id == sheet_id, SheetRow.id == row_id
    )
    result = await session.execute(stmt)
    return result.rowcount or 0


async def delete_sheet(session: AsyncSession, sheet_id: int) -> int:
    """删表级联 rows（DDL ON DELETE CASCADE 保证 rows 随之消失）。"""
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
