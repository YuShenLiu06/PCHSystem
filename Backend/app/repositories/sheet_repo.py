"""SheetRepository 函数式实现（sheets schema，B2）。

镜像 ``auth_token_repo.py`` 风格：函数签名收 ``AsyncSession``，只 ``flush()``，
由 api 层负责 ``commit()``。upsert 用「先 select...with_for_update() 在则改 / 不在则
insert」，并发同名 insert 触发 IntegrityError 上抛（api 层翻译为 409）。

身份锚 = owner_uuid（FK→users.players.uuid，红线 R-5）。
"""
import csv
import io
import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sheet import Sheet, SheetRow

_CSV_HEADER = ["sheet_id", "item_name", "need_qty", "done_flag", "sort_order"]


async def create_sheet(
    session: AsyncSession, owner_uuid: uuid.UUID, title: str
) -> Sheet:
    sheet = Sheet(owner_uuid=owner_uuid, title=title)
    session.add(sheet)
    await session.flush()
    return sheet


async def get_sheet(session: AsyncSession, sheet_id: int) -> Sheet | None:
    stmt = select(Sheet).where(Sheet.id == sheet_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_sheets(
    session: AsyncSession, owner_uuid: uuid.UUID | None = None
) -> list[Sheet]:
    stmt = select(Sheet).order_by(Sheet.id)
    if owner_uuid is not None:
        stmt = stmt.where(Sheet.owner_uuid == owner_uuid)
    return list((await session.execute(stmt)).scalars().all())


async def list_rows(session: AsyncSession, sheet_id: int) -> list[SheetRow]:
    stmt = (
        select(SheetRow)
        .where(SheetRow.sheet_id == sheet_id)
        .order_by(SheetRow.sort_order, SheetRow.id)
    )
    return list((await session.execute(stmt)).scalars().all())


async def upsert_row(
    session: AsyncSession,
    sheet_id: int,
    item_name: str,
    need_qty: int,
    done_flag: int,
    sort_order: int,
) -> SheetRow:
    """按 UNIQUE(sheet_id, item_name) upsert。在则改，不在则 insert。

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
        row.done_flag = done_flag
        row.sort_order = sort_order
        await session.flush()
        return row
    row = SheetRow(
        sheet_id=sheet_id,
        item_name=item_name,
        need_qty=need_qty,
        done_flag=done_flag,
        sort_order=sort_order,
    )
    session.add(row)
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
    sheets = await list_sheets(session)
    result: list[tuple[Sheet, list[SheetRow]]] = []
    for sheet in sheets:
        rows = await list_rows(session, sheet.id)
        result.append((sheet, rows))
    return result


def _row_to_csv_record(sheet_id: int, row: SheetRow) -> list[str | int]:
    return [sheet_id, row.item_name, row.need_qty, row.done_flag, row.sort_order]


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
