"""sheet 归档编排服务（markdown_render 首个消费者 + 文件落盘 + 事务一致性）。

``archive_sheet(session, sheet_id, *, archive_root, player)`` 编排：
1. 取 sheet + owner_name（get_sheet）；None → raise（api 层 404）。
2. 预检 status：archived → SheetArchived（api 层 409）；非法源态 → 状态错误。
3. 取贡献量排行（aggregate_contributor_totals：lock 交付 + progress 上交合并按人）。
4. 构建 context + 渲染 markdown（纯函数，不查库）。
5. ``writer.write_atomic`` 写盘（**事务外**，文件系统不参与 DB 事务）。
6. ``try:`` advance_sheet（SELECT FOR UPDATE 锁 + 校验 + 置 archived 三字段，flush 不 commit）
   + notification_service.notify（同 session 同事务，RS-9）
   + ``session.commit()``
   ``except Exception:`` ``writer.cleanup`` 删孤儿文件 + ``session.rollback()`` + raise。
7. 返回 advance_sheet 后的 sheet（已置 archived 三字段）。

事务一致性要点（写进 docstring）：
- **顺序理由**：文件是可清理副产物；先写盘后 commit——commit 失败可清文件（孤儿无害），
  反之 DB 显 archived 但文件缺失更糟（GET /archive 404 且无补救）。
- advance_sheet 的 SELECT FOR UPDATE 在 commit 前已锁行+校验+flush；并发归档第二个 →
  advance_sheet 内 SheetArchived 上抛 → 进入 except 分支 cleanup+rollback。
- notify 同事务：commit 成功通知落库；commit 失败（任一异常）通知随 rollback 消失（R-10）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sheet import (
    SHEET_PHASE_ACTIVE_SET,
    SHEET_PHASE_ARCHIVED,
    Sheet,
)
from app.models.user import Player
from app.repositories import sheet_repo
from app.repositories.sheet_repo import SheetArchived
from app.services import notification_service
from app.services.archive import writer
from app.services.archive.renderer import build_sheet_archive_document

# 归档态展示用的中文标签（status_line section + 通知 title/body）。
_ARCHIVED_LABEL = "已归档"


class SheetNotFoundError(Exception):
    """sheet 不存在。api 层翻译为 404。"""


class SheetStatusError(Exception):
    """sheet 当前状态不允许归档（非 collecting/constructing 源态，且非 archived）。

    与 SheetArchived 区分：SheetArchived 是「已归档终态」（409 只读）；
    SheetStatusError 理论上当前状态机下不会触发（只有 collecting/constructing/archived
    三态，前两态都允许归档），保留为防御性异常（api 层翻译 409）。
    """


def _build_context(
    sheet: Sheet,
    owner_name: str,
    contributor_totals: list,
) -> dict[str, Any]:
    """构建渲染 context（不可变输入：不改 sheet/contributor_totals）。"""
    return {
        "sheet_id": sheet.id,
        "title": sheet.title,
        "owner_name": owner_name,
        "status_label": _ARCHIVED_LABEL,
        "created_at": sheet.created_at,
        "archived_at": datetime.now(timezone.utc),
        # 进入施工时间当前模型未单独记录（status 列无时间戳），保持 None。
        "constructing_at": None,
        # 精确贡献量排行（render_contributor_stats 用）：repo 已排序+汇总。
        "contributor_totals": contributor_totals,
    }


async def archive_sheet(
    session: AsyncSession,
    sheet_id: int,
    *,
    archive_root: str,
    player: Player,
) -> Sheet:
    """把一张 sheet 归档：渲染 md → 写盘 → DB 置 archived + 通知 → commit。

    参数：
    - session：调用方（写端点）事务的同一 session（R-10）。
    - sheet_id：归档目标 sheet。
    - archive_root：归档根目录绝对路径；空串 → writer 写盘前 raise ArchiveNotConfigured。
    - player：调用者（deps 注入的 Player）；权限已由 api 层判，此处只用其身份。

    返回：advance_sheet 后的 sheet（已置 status=archived / archived_path / archived_at）。

    异常：
    - SheetNotFoundError → api 404（sheet 不存在）。
    - SheetArchived → api 409（已归档，由 advance_sheet 在并发竞态时也上抛）。
    - SheetStatusError → api 409（防御性，非法源态）。
    - ArchiveNotConfigured → api 503（archive_root 未配置）。
    - 其他异常：cleanup 已删孤儿文件 + rollback 后原样上抛。
    """
    # 1. 取 sheet + owner_name
    got = await sheet_repo.get_sheet(session, sheet_id)
    if got is None:
        raise SheetNotFoundError(f"sheet {sheet_id} not found")
    sheet, owner_name = got

    # 2. 预检 status（advance_sheet 的 FOR UPDATE 是权威校验；这里早期友好失败）
    if sheet.status == SHEET_PHASE_ARCHIVED:
        raise SheetArchived(f"sheet {sheet_id} is archived (terminal)")
    if sheet.status not in SHEET_PHASE_ACTIVE_SET:
        raise SheetStatusError(
            f"sheet {sheet_id} status {sheet.status} is not archivable"
        )

    # 3. 精确贡献量排行（lock 交付 + progress 上交合并按人）：repo 已排序+汇总。
    contributor_totals = await sheet_repo.aggregate_contributor_totals(
        session, sheet_id
    )

    # 4. 构建 context + 渲染（纯函数）
    context = _build_context(sheet, owner_name, contributor_totals)
    archived_at = context["archived_at"]  # 取出供通知 payload 用
    md = build_sheet_archive_document().render(context)

    # 5. 写盘（事务外；ArchiveNotConfigured 在此上抛，DB 尚未改动）
    rel_path = writer.write_atomic(archive_root, sheet_id, md)

    # 6. try: DB 置 archived + 通知 + commit；except: cleanup 删孤儿 + rollback + raise
    try:
        archived_sheet = await sheet_repo.advance_sheet(
            session,
            sheet_id,
            SHEET_PHASE_ARCHIVED,
            archived_path=rel_path,
        )
        if archived_sheet is None:
            # 极端：预检通过但 advance 时 sheet 被删 → cleanup + 404
            writer.cleanup(archive_root, rel_path)
            raise SheetNotFoundError(f"sheet {sheet_id} disappeared before archive")

        await notification_service.notify(
            session,
            recipient_uuid=sheet.owner_uuid,
            category="sheet_archived",
            title="项目已归档",
            body=f"项目「{sheet.title}」已归档",
            payload={
                "sheet_id": sheet_id,
                "sheet_title": sheet.title,
                "archived_path": rel_path,
                "archived_at": archived_at.isoformat(),
            },
        )
        await session.commit()
        return archived_sheet
    except Exception:
        # commit 失败（含 advance_sheet 的 SheetArchived/其他 DB 错）：删孤儿文件 + 回滚 + 上抛
        writer.cleanup(archive_root, rel_path)
        await session.rollback()
        raise
