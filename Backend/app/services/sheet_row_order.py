"""sheet 行的展示优先级排序（纯函数，免 DB）。

供 ``app/api/sheets.py`` 的 ``GET /sheets/{id}`` JSON 路径调用：list_rows 返回自然序（sort_order, id），
本模块按「五档优先级 + 还需数量降序」重排，**玩家相关**（progress 档 1/2、lock 档 0/3 取决于当前查看玩家）。

刻意放 router/service 层、用 Python 排序而非 SQL CASE：
- 隔离 archive 渲染 / CSV 导出等其它 list_rows 调用方（它们用自然序）；
- 单测免起 DB；
- 排序逻辑单一来源。

不可变：``sort_sheet_rows`` 返回新列表，不改入参。
"""
from __future__ import annotations

from uuid import UUID

from app.models.sheet import SheetRow
from app.repositories.sheet_repo import MODE_LOCK, STATUS_DONE

# 五档优先级（升序，档 0 最前）：
#   0 = 我认领的 lock（未完成）       1 = 我参与的 progress（未完成）
#   2 = 我未参与的 progress            3 = 非我认领的 lock（open 或他人认领，未完成）
#   4 = done
PRIORITY_MY_LOCK = 0
PRIORITY_MY_PROGRESS = 1
PRIORITY_OTHER_PROGRESS = 2
PRIORITY_OTHER_LOCK = 3
PRIORITY_DONE = 4


def row_remaining(row: SheetRow) -> int:
    """还需数量 = max(0, need_qty - delivered_qty)（行级总量，非玩家相关）。"""
    return max(0, (row.need_qty or 0) - (row.delivered_qty or 0))


def row_priority(row: SheetRow, player_uuid: UUID, my_row_ids: set[int]) -> int:
    """计算行的五档优先级（玩家相关）。

    - done → 4
    - lock（非 done）→ claimant_uuid == player_uuid ? 0 : 3（open 或他人认领都归 3）
    - progress（非 done）→ row.id in my_row_ids ? 1 : 2

    ``my_row_ids`` = 当前玩家贡献过的 progress 行 id 集（由调用方据 list_contributors 预算）。
    """
    if row.status == STATUS_DONE:
        return PRIORITY_DONE
    if row.mode == MODE_LOCK:
        # lock 单认领人：claimant_uuid 即权威锚（progress 行恒 None，但此处已按 mode 分流）
        return PRIORITY_MY_LOCK if row.claimant_uuid == player_uuid else PRIORITY_OTHER_LOCK
    # progress（非 done）：是否当前玩家贡献过
    return PRIORITY_MY_PROGRESS if row.id in my_row_ids else PRIORITY_OTHER_PROGRESS


def sort_sheet_rows(
    rows: list[tuple[SheetRow, str | None]],
    player_uuid: UUID,
    my_row_ids: set[int],
) -> list[tuple[SheetRow, str | None]]:
    """按 (priority, -remaining, sort_order, id) 稳定升序排序，返回新列表。

    - priority：五档（``row_priority``），升序；
    - -remaining：还需数量**降序**（剩余多优先）；
    - sort_order、id：末位稳定 tiebreaker（保留 owner 显式排序意图）。

    入参 ``rows`` = list_rows 返回的 ``[(SheetRow, claimant_name|None)]``；不改入参。
    """
    return sorted(
        rows,
        key=lambda item: (
            row_priority(item[0], player_uuid, my_row_ids),
            -row_remaining(item[0]),
            item[0].sort_order,
            item[0].id,
        ),
    )
