"""SheetRepository 函数式实现（sheets schema）。

镜像 ``auth_token_repo.py`` 风格：函数签名收 ``AsyncSession``，只 ``flush()``，
由 api 层负责 ``commit()``。upsert / 状态机转移用 ``select(...).with_for_update()``
锁行后判定，并发同名 insert 触发 IntegrityError 上抛（api 层翻译为 409），
非法状态转移 raise ``SheetRowConflict``（api 层翻译为 409）。

身份锚 = owner_uuid / claimant_uuid（FK→users.players.uuid，红线 R-5）。
"""
import csv
import io
import math
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import case, delete, func, or_, select, union_all
from sqlalchemy.exc import IntegrityError
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
    "parent_row_id",
    "qty_per_unit",
]


class SheetRowConflict(Exception):
    """行状态非法转移/不变量违反，api 层翻译为 409。"""


class SheetArchived(Exception):
    """sheet 已归档只读，api 层翻译为 409。任何写操作入口先经 _assert_writable 守卫。

    契约：api 层统一译为 HTTP 409，detail 用「项目已归档，只读」（advance 路径为英文
    「sheet is archived, read-only」）。MCDR `_resolve` 按 detail 含「归档」/「archiv」
    子串识别归档态并显示「项目已归档，只读」——改文案须保留其中一个标记，否则游戏端
    会回退为通用「状态非法」。
    """


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
    player_uuid: uuid.UUID | None = None,
) -> list[tuple[Sheet, str]]:
    """列所有表：inner join players 取 owner 游戏名。返回 [(Sheet, owner_name)]。

    status_filter（与 owner_uuid 可组合）：
    - None → 不过滤；
    - "active" → status ∈ (collecting, constructing)；
    - 单值（collecting/constructing/archived）→ status == 该值。

    player_uuid（参与优先排序）：
    - 非空时，该玩家参与过的表（owner/claimant/contributor）排在前面，组内按 id 升序；
    - None 时，按 sheet id 升序（与历史行为一致）。
    """
    stmt = (
        select(Sheet, Player.current_name)
        .join(Player, Player.uuid == Sheet.owner_uuid)
    )
    if owner_uuid is not None:
        stmt = stmt.where(Sheet.owner_uuid == owner_uuid)
    if status_filter == "active":
        stmt = stmt.where(Sheet.status.in_(SHEET_PHASE_ACTIVE_SET))
    elif status_filter is not None:
        stmt = stmt.where(Sheet.status == status_filter)

    if player_uuid is not None:
        # 构造「该玩家参与过的 sheet_id 集」复合 SELECT（三源 UNION）。
        # 直接以 CompoundSelect 传入 in_()——勿加 .subquery()，否则 SQLAlchemy 会
        # 触发「Coercing Subquery into select() for IN()」告警（2.x 行为）。
        involved_ids = (
            select(SheetRow.sheet_id).where(SheetRow.claimant_uuid == player_uuid)
        ).union(
            select(SheetRow.sheet_id)
            .join(SheetRowContributor, SheetRowContributor.row_id == SheetRow.id)
            .where(SheetRowContributor.player_uuid == player_uuid),
            select(Sheet.id).where(Sheet.owner_uuid == player_uuid),
        )
        stmt = stmt.order_by(Sheet.id.in_(involved_ids).desc(), Sheet.id.asc())
    else:
        stmt = stmt.order_by(Sheet.id.asc())
    return [(r[0], r[1]) for r in (await session.execute(stmt)).all()]


async def list_rows(
    session: AsyncSession, sheet_id: int, *, search: str | None = None
) -> list[tuple[SheetRow, str | None]]:
    """列单表所有行：left join players 取认领人游戏名。返回 [(SheetRow, claimant_name|None)]。

    ``search`` 非空时按 ``item_name`` / ``registry_id`` 大小写不敏感子串过滤。
    分组排序：所有父行（parent_row_id IS NULL）排在前面（组内按 sort_order, id），
    所有子行排在后面（按 parent_row_id 分组——同父的子行相邻，组内再按 sort_order, id）。
    注意：子行并非紧跟各自父行，而是父行段与子行段分两段（archive 不渲染行清单、CSV
    依赖此稳定顺序，勿改 SQL）。
    """
    stmt = (
        select(SheetRow, Player.current_name)
        .outerjoin(Player, Player.uuid == SheetRow.claimant_uuid)
        .where(SheetRow.sheet_id == sheet_id)
        .order_by(
            # 父行（parent_row_id IS NULL）排在前面
            SheetRow.parent_row_id.is_(None).desc(),
            # 父行按 sort_order 排序，子行按 parent_row_id（父的 id）分组
            case(
                (SheetRow.parent_row_id.is_(None), SheetRow.sort_order),
                else_=SheetRow.parent_row_id,
            ),
            # 同组内按 sort_order, id
            SheetRow.sort_order,
            SheetRow.id,
        )
    )
    if search:
        # 转义 LIKE 通配符（% _ \）：registry_id 普遍含 _（如 minecraft:oak_log），
        # 不转义则 _ 被当单字符通配，搜 oak_log 会误匹配 oakXlog 之类
        escaped = search.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pat = f"%{escaped}%"
        stmt = stmt.where(
            or_(
                func.lower(SheetRow.item_name).like(pat, escape="\\"),
                func.lower(SheetRow.registry_id).like(pat, escape="\\"),
            )
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

    ⚠️ **仅供测试 seeding 用**——生产路径已弃用此 by-``item_name`` upsert（issue #20：
    改名查不到旧行 → 新建 → 重复）。新代码请用 ``create_row``（严格新建）/ ``update_row``
    （按 row_id 主键更新）。**勿在 app/ 内复用本函数**，否则会悄悄把 #20 引回来。

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
        await _recompute_after_edit(
            session, row, mode_changed=mode_changed, new_need_qty=need_qty
        )
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


async def _validate_parent_for_sub(
    session: AsyncSession,
    sheet_id: int,
    parent_row_id: int,
    *,
    self_row_id: int | None = None,
) -> SheetRow:
    """子物品父行校验（create_row / update_row 共用，DRY）。

    校验：
    - 自引用：``parent_row_id == self_row_id`` → ValueError（仅 update 需要，create 无 id）。
    - parent 存在且同表：parent is None 或 parent.sheet_id != sheet_id → ValueError（跨表拦截）。
    - 单层：parent.parent_row_id is not None → ValueError（不允许子行再挂子行）。

    返回合法的 parent SheetRow；不合法 raise ValueError。
    """
    if self_row_id is not None and parent_row_id == self_row_id:
        raise ValueError("parent_row_id 不能指向自身（自引用）")
    parent = await session.get(SheetRow, parent_row_id)
    if parent is None or parent.sheet_id != sheet_id:
        raise ValueError("parent_row_id not found or cross-sheet")
    if parent.parent_row_id is not None:
        raise ValueError("子行只能嵌套一层（parent.parent_row_id IS NULL）")
    return parent


async def create_row(
    session: AsyncSession,
    sheet_id: int,
    item_name: str,
    *,
    need_qty: int,
    mode: int,
    sort_order: int,
    registry_id: str | None = None,
    parent_row_id: int | None = None,
    qty_per_unit: float | None = None,
) -> SheetRow:
    """严格新建行（不做 upsert）。

    子物品嵌套行（0012，0013 倍数放宽为小数）：
    - 父行锁校验单层（parent.parent_row_id IS NULL）。
    - 模式继承：父 lock 强制子 lock；缺省继承父 mode。
    - need_qty = ceil(qty_per_unit × parent.need_qty)（子行派生，向上取整成整数）。
    - item_name 自动加父名前缀 ``{父.item_name}-{item_name}``（仅创建路径；flat 视图消歧）。
    - 子行必传 registry_id + qty_per_unit > 0。
    """
    await _assert_writable(session, sheet_id)

    # 子物品逻辑
    if parent_row_id is not None:
        # 父行校验复用共享 helper（同表/单层/自引用）。create 无 self id，不传 self_row_id。
        parent = await _validate_parent_for_sub(session, sheet_id, parent_row_id)
        if registry_id is None:
            raise ValueError("子行必传 registry_id")
        if qty_per_unit is None or qty_per_unit <= 0:
            raise ValueError("子行 qty_per_unit 必须 > 0")
        # 模式继承：父 lock 强制子 lock
        if parent.mode == MODE_LOCK:
            mode = MODE_LOCK
        else:
            mode = mode if mode is not None else parent.mode
        # D2：Decimal 精确计算（Python float 直接相乘会让 ceil(0.07*100)=8，
        # 应为 7）。parent.need_qty 是 int，Decimal×int 精确。
        need_qty = math.ceil(Decimal(str(qty_per_unit)) * parent.need_qty)
        # 子行 item_name 自动加父名前缀，避免 flat 视图（CSV/MCDR 列表）重名歧义。
        # 仅创建路径加前缀；更新路径（update_row）尊重调用方传入值，不重拼。
        item_name = f"{parent.item_name}-{item_name}"

    row = SheetRow(
        sheet_id=sheet_id,
        item_name=item_name,
        need_qty=need_qty,
        mode=mode,
        sort_order=sort_order,
        registry_id=registry_id,
        parent_row_id=parent_row_id,
        qty_per_unit=qty_per_unit,
    )
    session.add(row)
    await session.flush()
    return row


async def _recompute_after_edit(
    session: AsyncSession,
    row: SheetRow,
    *,
    mode_changed: bool,
    new_need_qty: int,
) -> None:
    """行编辑后的协作状态重算（upsert_row / update_row 共用，DRY）。

    调用方应先把 need_qty/mode 写入 row，再传 mode_changed 与 new_need_qty：

    - mode 变化：重置协作（status=open / claimant=None / delivered=0 / 清贡献者），
      避免违反 progress 不变量（claimant 恒 null）。
    - mode 不变：保留进度，按 new_need_qty 封顶 delivered（need=0 = 无目标，不封顶）
      并重算 status（满足→done；done 但不足→claimed）。
    """
    if mode_changed:
        row.status = STATUS_OPEN
        row.claimant_uuid = None
        row.delivered_qty = 0
        await clear_contributors(session, row.id)
        return
    # mode 不变：保留进度，按新 need 封顶（need=0 = 无目标，不封顶）
    if new_need_qty > 0 and row.delivered_qty > new_need_qty:
        row.delivered_qty = new_need_qty
    if (
        row.status in (STATUS_CLAIMED, STATUS_DONE)
        and new_need_qty > 0
        and row.delivered_qty >= new_need_qty
    ):
        row.status = STATUS_DONE
    elif row.status == STATUS_DONE and row.delivered_qty < new_need_qty:
        row.status = STATUS_CLAIMED


async def update_row(
    session: AsyncSession,
    sheet_id: int,
    row_id: int,
    *,
    item_name: str | None = None,
    registry_id: str | None = None,
    need_qty: int | None = None,
    mode: int | None = None,
    sort_order: int | None = None,
    parent_row_id: int | None = None,
    qty_per_unit: float | None = None,
) -> SheetRow | None:
    """按主键 row_id 部分更新行（子物品嵌套行 0012）。

    子行 qty_per_unit/mode 变 → 重算 need_qty = ceil(qty_per_unit × 父.need_qty)。
    顶层行 need/mode 变 → 级联子行重算（同样 ceil）。
    """
    await _assert_writable(session, sheet_id)
    stmt = (
        select(SheetRow)
        .where(SheetRow.sheet_id == sheet_id, SheetRow.id == row_id)
        .with_for_update()
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None

    # 记录是否为顶层行（用于级联判断）
    is_top_level = row.parent_row_id is None
    old_parent_id = row.parent_row_id
    old_mode = row.mode
    old_need = row.need_qty

    # D1：reparent 校验（parent_row_id 传入时）。复用 create_row 的父行校验 +
    # 自引用拦截（update 特有，create 新建无 id）+ 顶层→子行转换必须有
    # registry_id/qty_per_unit（子行不变量）。
    reparent = parent_row_id is not None and parent_row_id != old_parent_id
    new_parent: SheetRow | None = None
    if parent_row_id is not None:
        new_parent = await _validate_parent_for_sub(
            session, sheet_id, parent_row_id, self_row_id=row_id
        )
        if reparent:
            # D1 收尾：reparent 时 row 自身不能已有子行，否则形成多层嵌套
            # （顶层行 A 有子 B，把 A 挂到 C 下 → A→C 且 A→B = 两层）。
            # 单层不变量要求：子行的 parent 必须顶层；故挂为子行前须确认 row 无子行。
            existing_child = (
                await session.execute(
                    select(SheetRow.id)
                    .where(SheetRow.parent_row_id == row_id)
                    .limit(1)
                )
            ).first()
            if existing_child is not None:
                raise ValueError("不能把已有子行的行挂为子行（会形成多层嵌套）")
        if old_parent_id is None:
            # 顶层行转子行：子行不变量要求 registry_id + qty_per_unit（本次传入或已有）。
            effective_registry = (
                registry_id if registry_id is not None else row.registry_id
            )
            if effective_registry is None:
                raise ValueError("顶层行转子行必须有 registry_id")
            effective_qty = (
                qty_per_unit if qty_per_unit is not None else row.qty_per_unit
            )
            if effective_qty is None or effective_qty <= 0:
                raise ValueError("顶层行转子行必须有 qty_per_unit > 0")

    # D2/D3：子行 need_qty 重算。触发：
    #   - qty_per_unit 变（非 reparent）→ 用当前 parent
    #   - reparent（parent_row_id 变）→ 用新 parent（顶层→子行 / 子→子换父）
    #   - 两者同时 → 用新 parent + 新 qty_per_unit
    # Decimal 精确计算（float 直接相乘会让 ceil(0.07*100)=8）。
    need_recompute = (
        qty_per_unit is not None and row.parent_row_id is not None
    ) or reparent
    if need_recompute:
        if reparent:
            calc_parent = new_parent
        else:
            calc_parent = await session.get(SheetRow, row.parent_row_id)
        if calc_parent is not None:
            calc_qty = qty_per_unit if qty_per_unit is not None else row.qty_per_unit
            if calc_qty is not None:
                row.need_qty = math.ceil(Decimal(str(calc_qty)) * calc_parent.need_qty)

    if item_name is not None:
        row.item_name = item_name
    if registry_id is not None:
        row.registry_id = registry_id
    if need_qty is not None:
        row.need_qty = need_qty
    if mode is not None:
        row.mode = mode
    if sort_order is not None:
        row.sort_order = sort_order
    if parent_row_id is not None:
        row.parent_row_id = parent_row_id
    if qty_per_unit is not None:
        row.qty_per_unit = qty_per_unit

    mode_changed = mode is not None and row.mode != old_mode
    # need 变化既含显式传 need_qty，也含子行 qty_per_unit/reparent 重算派生的新 need：
    # 必须基于 row.need_qty 实际值判定，否则派生变化漏触发状态重算（done→claimed）。
    need_changed = row.need_qty != old_need

    # mode/need 任一变化才重算
    if mode_changed or need_changed:
        await _recompute_after_edit(
            session, row, mode_changed=mode_changed, new_need_qty=row.need_qty
        )

    await session.flush()

    # 顶层行 need/mode 变 → 级联子行重算
    if is_top_level and (need_changed or mode_changed):
        child_stmt = (
            select(SheetRow)
            .where(SheetRow.parent_row_id == row_id)
            .order_by(SheetRow.id)
        )
        child_rows = (await session.execute(child_stmt)).scalars().all()
        for child in child_rows:
            child_need_changed = False
            if need_changed and child.qty_per_unit is not None:
                child.need_qty = math.ceil(child.qty_per_unit * row.need_qty)
                child_need_changed = True
            # D7（by-design）：级联只紧不松——父切 LOCK 强制子 LOCK；父切 PROGRESS
            # 不反向放松子行（允许 progress 父 + lock 子混合，放松留待 owner 手动改子行 mode）。
            if mode_changed and row.mode == MODE_LOCK:
                child.mode = MODE_LOCK
                await _recompute_after_edit(
                    session, child, mode_changed=True, new_need_qty=child.need_qty
                )
            elif child_need_changed:
                await _recompute_after_edit(
                    session, child, mode_changed=False, new_need_qty=child.need_qty
                )

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
    """lock 行 open → claimed：置 claimant、delivered=0。progress 行 / 非 open 行视为非法转移。

    子物品级联（0012）：
    - 子行不得单独认领/解除当其父行=lock（随父行）。
    - 认领顶层 lock 父行 = 同事务认领其所有 open lock 子行（同 claimant）。
    - 父行=progress 时，被改成 lock 的子行可单独认领/解除（progress 父行本身不可认领，无法级联）。
    """
    await _assert_writable(session, sheet_id)
    row = await _lock_row(session, sheet_id, row_id)
    if row is None:
        return None
    if row.mode == MODE_PROGRESS:
        raise SheetRowConflict("progress rows use contribute, not claim")
    if row.status != STATUS_OPEN:
        raise SheetRowConflict(f"cannot claim row in status {row.status}")

    # 子行守卫：子行随父 lock 行，不得单独认领
    if row.parent_row_id is not None:
        parent = await _lock_row(session, sheet_id, row.parent_row_id)
        if parent is not None and parent.mode == MODE_LOCK:
            raise SheetRowConflict("子物品随父行认领，不得单独认领")

    # 认领本行
    row.status = STATUS_CLAIMED
    row.claimant_uuid = claimant_uuid
    row.delivered_qty = 0

    # 顶层级联：认领所有 open lock 子行
    if row.parent_row_id is None:
        child_stmt = (
            select(SheetRow)
            .where(
                SheetRow.sheet_id == sheet_id,
                SheetRow.parent_row_id == row_id,
                SheetRow.mode == MODE_LOCK,
                SheetRow.status == STATUS_OPEN,
            )
            .order_by(SheetRow.id)
        )
        child_rows = (await session.execute(child_stmt)).scalars().all()
        for child in child_rows:
            child.status = STATUS_CLAIMED
            child.claimant_uuid = claimant_uuid
            child.delivered_qty = 0

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
    """claimed/done → open：清 claimant、delivered=0、清贡献者（progress 行）。

    子物品级联（0012）：
    - 子行不得单独认领/解除当其父行=lock（随父行）。
    - 解除顶层 lock 父行 = 同事务解除其所有 claimed/done lock 子行（清 claimant、delivered、贡献者）。
    - 父行=progress 时，被改成 lock 的子行可单独认领/解除。
    """
    await _assert_writable(session, sheet_id)
    row = await _lock_row(session, sheet_id, row_id)
    if row is None:
        return None
    if row.status not in (STATUS_CLAIMED, STATUS_DONE):
        raise SheetRowConflict(f"cannot release row in status {row.status}")

    # 子行守卫：子行随父 lock 行，不得单独解除
    if row.parent_row_id is not None:
        parent = await _lock_row(session, sheet_id, row.parent_row_id)
        if parent is not None and parent.mode == MODE_LOCK:
            raise SheetRowConflict("子物品随父行解除，请解除父行")

    # 解除本行
    row.status = STATUS_OPEN
    row.claimant_uuid = None
    row.delivered_qty = 0
    await clear_contributors(session, row.id)

    # 顶层级联：解除所有 claimed/done 子行
    if row.parent_row_id is None:
        child_stmt = (
            select(SheetRow)
            .where(
                SheetRow.sheet_id == sheet_id,
                SheetRow.parent_row_id == row_id,
                SheetRow.mode == MODE_LOCK,
                SheetRow.status.in_((STATUS_CLAIMED, STATUS_DONE)),
            )
            .order_by(SheetRow.id)
        )
        child_rows = (await session.execute(child_stmt)).scalars().all()
        for child in child_rows:
            child.status = STATUS_OPEN
            child.claimant_uuid = None
            child.delivered_qty = 0
            await clear_contributors(session, child.id)

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
        row.parent_row_id or "",
        "" if row.qty_per_unit is None else f"{float(row.qty_per_unit):g}",
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
