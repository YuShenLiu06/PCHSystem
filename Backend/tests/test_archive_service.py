"""归档服务测试（renderer + writer + service）。

连真实 PG（conftest autouse truncate 每测清库）；archive_root 用 pytest tmp_path。
覆盖：
- renderer 纯函数：material_table lock/progress、空表、贡献者统计聚合排序、
  document 渲染含标题与 section。
- writer：原子写创建文件 + 路径穿越拒绝（read_archive_file rel_path 注入）+
  read 缺失返 None + read 返内容 + cleanup 删/吞 FileNotFoundError。
- service：collecting→archived / constructing→archived / 直跳 / 已 archived 抛 /
  通知 owner / commit 失败回滚孤儿文件 / archive_root 未配置抛 ArchiveNotConfigured。

AAA 结构；service 测试用独立 session 包裹 + 末尾 commit 验持久化（同 test_sheet_repo 风格）。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.core.db import async_session_factory
from app.models.notification import Notification
from app.models.sheet import Sheet, SheetRow
from app.models.user import Player
from app.repositories import sheet_repo
from app.services.archive import (
    ArchiveNotConfigured,
    SheetNotFoundError,
    archive_sheet,
    build_sheet_archive_document,
    read_archive_file,
    writer,
)
from app.services.archive.renderer import (
    render_contributor_stats,
    render_material_table,
    render_timeline,
)
from app.services.archive.service import SheetStatusError


# ---------- fixtures & helpers ----------


async def _seed_player(name: str = "alice", role: str = "user") -> tuple[Player, uuid.UUID]:
    """seed 一个 users.players 行，返回 (Player 实例, uuid)。"""
    u = uuid.uuid4()
    async with async_session_factory() as s:
        s.add(Player(uuid=u, current_name=name, role=role))
        await s.commit()
    return Player(uuid=u, current_name=name, role=role), u


class _FakeRow:
    """renderer 测试用的轻量行替身（避免每次建表；renderer 是纯函数不查库）。

    属性对齐 SheetRow ORM：id / item_name / need_qty / mode / status / delivered_qty。
    """

    def __init__(
        self,
        row_id: int,
        item_name: str,
        need_qty: int,
        mode: int,
        status: str,
        delivered_qty: int = 0,
    ) -> None:
        self.id = row_id
        self.item_name = item_name
        self.need_qty = need_qty
        self.mode = mode
        self.status = status
        self.delivered_qty = delivered_qty


# ---------- renderer：material_table ----------


def test_render_material_table_shows_claimant_for_lock_row():
    # Arrange：lock 行（mode=0），有 claimant_name
    row = _FakeRow(1, "铁锭", 64, 0, "claimed", delivered_qty=5)
    ctx = {"rows": [(row, "bob")], "contributors_map": {}}
    # Act
    md = render_material_table(ctx)
    # Assert
    assert "## 材料清单" in md
    assert "| # | 物品 | 需求 | 已交付 | 状态 | 模式 | 认领/贡献者 |" in md
    # lock 行认领者单元格显 claimant_name
    assert "bob" in md
    # status 中文映射
    assert "进行中" in md
    assert "lock" in md


def test_render_material_table_shows_contributors_for_progress_row():
    # Arrange：progress 行（mode=1），无 claimant，有 contributors_map
    row = _FakeRow(2, "圆石", 128, 1, "claimed", delivered_qty=10)
    contributors = {2: [(uuid.uuid4(), "alice"), (uuid.uuid4(), "carol")]}
    ctx = {"rows": [(row, None)], "contributors_map": contributors}
    # Act
    md = render_material_table(ctx)
    # Assert：progress 行认领者单元格显贡献者名（按 contributors_map 顺序 join）
    assert "alice" in md
    assert "carol" in md
    assert "progress" in md
    # claimant_name（None）不显
    assert "None" not in md


def test_render_material_table_empty_sheet_returns_placeholder():
    # Arrange：空表
    ctx = {"rows": [], "contributors_map": {}}
    # Act
    md = render_material_table(ctx)
    # Assert：非空串（让 section 显出来），含占位文案
    assert md.strip()
    assert "暂无物品" in md


# ---------- renderer：contributor_stats ----------


def test_render_contributor_stats_aggregates_and_sorts():
    # Arrange：两个 progress 行的贡献者（按贡献量已内部降序，这里测跨行汇总排序）
    alice, bob, carol = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    contributors = {
        1: [(alice, "alice"), (bob, "bob")],   # alice + bob 各参与行 1
        2: [(alice, "alice"), (carol, "carol")],  # alice 又参与行 2
    }
    ctx = {"contributors_map": contributors}
    # Act
    md = render_contributor_stats(ctx)
    # Assert：alice 参与 2 项排第一；bob/carol 各 1 项，名字升序
    lines = md.splitlines()
    assert lines[0] == "## 贡献者统计"
    # alice 必出现在 bob 之前（参与行数多）
    assert lines.index(next(l for l in lines if "alice" in l)) < lines.index(
        next(l for l in lines if "bob" in l)
    )
    assert "参与 2 项" in md
    assert "参与 1 项" in md


def test_render_contributor_stats_no_contributors_returns_empty():
    # Arrange：无贡献者
    ctx = {"contributors_map": {}}
    # Act
    md = render_contributor_stats(ctx)
    # Assert：空串（让 section 被文档层过滤）
    assert md == ""


# ---------- renderer：document 渲染 ----------


def test_build_document_render_contains_title_and_sections():
    # Arrange
    doc = build_sheet_archive_document()
    ctx = {
        "title": "大教堂主材",
        "status_label": "已归档",
        "owner_name": "alice",
        "created_at": datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc),
        "archived_at": datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc),
        "constructing_at": None,
        "rows": [],
        "contributors_map": {},
    }
    # Act
    md = doc.render(ctx)
    # Assert：header 含标题、status_line 含状态、meta 含 owner、material_table 占位、footer
    assert "# 项目归档：大教堂主材" in md
    assert "**状态**：已归档" in md
    assert "alice" in md
    assert "## 材料清单" in md
    assert "暂无物品" in md
    assert "## 时间线" in md
    assert "由 PCHSystem 自动生成" in md
    # contributor_stats section 因无贡献者被过滤（不应出现空标题）
    assert "## 贡献者统计" not in md


def test_render_timeline_includes_archived_when_present():
    # Arrange
    ctx = {
        "created_at": datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc),
        "archived_at": datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc),
        "constructing_at": None,
    }
    # Act
    md = render_timeline(ctx)
    # Assert：创建 + 归档显，constructing 缺省不显
    assert "## 时间线" in md
    assert "创建：" in md
    assert "归档：" in md
    assert "进入施工" not in md


def test_render_timeline_all_none_returns_empty():
    # Arrange：全缺时间
    ctx = {"created_at": None, "archived_at": None, "constructing_at": None}
    # Act
    md = render_timeline(ctx)
    # Assert：空串（section 被过滤）
    assert md == ""


def test_render_timeline_includes_constructing_when_present():
    """constructing_at 当前模型未记录（service 恒填 None），但 renderer 支持该字段；
    未来扩展时无需改 renderer。此测试固化字段语义。"""
    # Arrange
    ctx = {
        "created_at": datetime(2026, 7, 1, tzinfo=timezone.utc),
        "constructing_at": datetime(2026, 7, 2, tzinfo=timezone.utc),
        "archived_at": datetime(2026, 7, 3, tzinfo=timezone.utc),
    }
    # Act
    md = render_timeline(ctx)
    # Assert：三行时间线全显
    assert "创建：" in md
    assert "进入施工：" in md
    assert "归档：" in md


def test_render_helpers_handle_none_defensively():
    """_row_status_label / _mode_label / _fmt_dt 收 None → 空串（不抛）。"""
    # 经由 render_material_table / render_timeline 间接覆盖，这里直接验私有 helper 语义。
    from app.services.archive.renderer import _fmt_dt, _mode_label, _row_status_label

    assert _row_status_label(None) == ""
    assert _mode_label(None) == ""
    assert _fmt_dt(None) == ""
    # 未知 status 原样回退
    assert _row_status_label("weird") == "weird"


# ---------- writer：原子写 + 路径穿越 ----------


def test_write_atomic_creates_file_under_projects_dir(tmp_path):
    # Arrange
    root = str(tmp_path)
    md = "# 项目归档：测试\n\n内容"
    # Act
    rel = writer.write_atomic(root, 42, md)
    # Assert：返回相对 POSIX 路径
    assert rel == "projects/42.md"
    final = tmp_path / "projects" / "42.md"
    assert final.is_file()
    assert final.read_text(encoding="utf-8") == md
    # .tmp 目录无残留（os.replace 已搬走）
    tmp_files = list((tmp_path / ".tmp").glob("*"))
    assert tmp_files == []


def test_write_atomic_empty_root_raises_not_configured(tmp_path):
    # Arrange / Act / Assert：空 archive_root → ArchiveNotConfigured（非 ValueError）
    with pytest.raises(ArchiveNotConfigured):
        writer.write_atomic("", 1, "x")


def test_read_archive_file_returns_none_when_missing(tmp_path):
    # Arrange
    root = str(tmp_path)
    # Act / Assert：文件不存在 → None
    assert read_archive_file(root, "projects/999.md") is None


def test_read_archive_file_returns_content(tmp_path):
    # Arrange：先写再读
    root = str(tmp_path)
    writer.write_atomic(root, 7, "# hello")
    # Act
    content = read_archive_file(root, "projects/7.md")
    # Assert
    assert content == "# hello"


def test_read_archive_file_rejects_path_traversal(tmp_path):
    # Arrange：恶意 rel_path 想逃出 root
    root = str(tmp_path)
    # 先在 root 外放一个敏感文件
    sibling = tmp_path.parent / "secret.txt"
    sibling.write_text("sensitive", encoding="utf-8")
    malicious = f"../{sibling.name}"
    # Act / Assert：穿越防护 → ValueError
    with pytest.raises(ValueError):
        read_archive_file(root, malicious)


def test_cleanup_deletes_file(tmp_path):
    # Arrange
    root = str(tmp_path)
    rel = writer.write_atomic(root, 5, "x")
    final = tmp_path / "projects" / "5.md"
    assert final.is_file()
    # Act
    writer.cleanup(root, rel)
    # Assert
    assert not final.exists()


def test_cleanup_swallows_file_not_found(tmp_path):
    # Arrange：从未写盘或已删
    root = str(tmp_path)
    # Act / Assert：删不存在的文件不抛（幂等）
    writer.cleanup(root, "projects/never.md")  # 不应抛


def test_cleanup_rejects_path_traversal(tmp_path):
    # Arrange
    root = str(tmp_path)
    # Act / Assert
    with pytest.raises(ValueError):
        writer.cleanup(root, "../../etc/passwd")


# ---------- service：archive_sheet 事务一致性 ----------


async def _make_collecting_sheet(
    title: str = "S", rows: tuple = ()
) -> tuple[int, Player, uuid.UUID]:
    """seed 一张 collecting 态表 + 可选 rows（(item, need, mode)），返回 (sheet_id, player, owner_uuid)。"""
    player, owner_uuid = await _seed_player("alice")
    async with async_session_factory() as s:
        sheet = await sheet_repo.create_sheet(s, owner_uuid, title)
        for item, need, mode in rows:
            await sheet_repo.upsert_row(s, sheet.id, item, need, mode, 0)
        await s.commit()
        sid = sheet.id
    return sid, player, owner_uuid


@pytest.mark.asyncio
async def test_archive_sheet_collecting_to_archived_writes_file_and_sets_path(tmp_path):
    # Arrange：collecting 态表 + 一行 lock + 一行 progress
    sid, player, owner_uuid = await _make_collecting_sheet(
        "S", rows=[("铁锭", 64, 0), ("圆石", 128, 1)]
    )
    # progress 行加一个贡献者
    async with async_session_factory() as s:
        rows = await sheet_repo.list_rows(s, sid)
        prog_row = next(r for r, _ in rows if r.mode == 1)
        await sheet_repo.contribute_row(s, sid, prog_row.id, owner_uuid, 10)
        await s.commit()
    root = str(tmp_path)
    # Act
    async with async_session_factory() as s:
        sheet = await archive_sheet(s, sid, archive_root=root, player=player)
        # service 内部已 commit
    # Assert：DB 置 archived
    assert sheet.status == "archived"
    assert sheet.archived_path == f"projects/{sid}.md"
    assert sheet.archived_at is not None
    # 文件落盘，内容含标题 + 材料表
    final = tmp_path / "projects" / f"{sid}.md"
    assert final.is_file()
    md = final.read_text(encoding="utf-8")
    assert "# 项目归档：S" in md
    assert "## 材料清单" in md
    assert "铁锭" in md and "圆石" in md
    # 持久化校验
    async with async_session_factory() as s:
        got = await sheet_repo.get_sheet(s, sid)
        assert got is not None
        assert got[0].status == "archived"


@pytest.mark.asyncio
async def test_archive_sheet_directly_from_collecting_skips_constructing(tmp_path):
    # Arrange：collecting 直跳 archived（不先到 constructing）
    sid, player, _ = await _make_collecting_sheet("直跳")
    root = str(tmp_path)
    # Act
    async with async_session_factory() as s:
        sheet = await archive_sheet(s, sid, archive_root=root, player=player)
    # Assert
    assert sheet.status == "archived"
    assert (tmp_path / "projects" / f"{sid}.md").is_file()


@pytest.mark.asyncio
async def test_archive_sheet_from_constructing(tmp_path):
    # Arrange：先 advance 到 constructing
    sid, player, _ = await _make_collecting_sheet("施工中")
    async with async_session_factory() as s:
        await sheet_repo.advance_sheet(s, sid, sheet_repo.SHEET_PHASE_CONSTRUCTING)
        await s.commit()
    root = str(tmp_path)
    # Act
    async with async_session_factory() as s:
        sheet = await archive_sheet(s, sid, archive_root=root, player=player)
    # Assert
    assert sheet.status == "archived"
    assert (tmp_path / "projects" / f"{sid}.md").is_file()


@pytest.mark.asyncio
async def test_archive_sheet_already_archived_raises_sheet_archived(tmp_path):
    # Arrange：先归档一次
    sid, player, _ = await _make_collecting_sheet("已归档")
    root = str(tmp_path)
    async with async_session_factory() as s:
        await archive_sheet(s, sid, archive_root=root, player=player)
    # Act / Assert：第二次归档 → SheetArchived（来自 service 预检）
    from app.repositories.sheet_repo import SheetArchived
    async with async_session_factory() as s:
        with pytest.raises(SheetArchived):
            await archive_sheet(s, sid, archive_root=root, player=player)


@pytest.mark.asyncio
async def test_archive_sheet_notifies_owner(tmp_path):
    # Arrange
    sid, player, owner_uuid = await _make_collecting_sheet("通知")
    root = str(tmp_path)
    # Act
    async with async_session_factory() as s:
        await archive_sheet(s, sid, archive_root=root, player=player)
    # Assert：notifications 表有一条 category=sheet_archived，recipient=owner
    async with async_session_factory() as s:
        notifs = (
            await s.execute(
                select(Notification).where(Notification.recipient_uuid == owner_uuid)
            )
        ).scalars().all()
    assert len(notifs) == 1
    n = notifs[0]
    assert n.category == "sheet_archived"
    assert n.title == "项目已归档"
    assert n.payload["sheet_id"] == sid
    assert n.payload["archived_path"] == f"projects/{sid}.md"


@pytest.mark.asyncio
async def test_archive_sheet_rolls_back_file_on_db_failure(tmp_path):
    # Arrange
    sid, player, _ = await _make_collecting_sheet("回滚")
    root = str(tmp_path)
    final = tmp_path / "projects" / f"{sid}.md"
    # Act：mock session.commit 抛 → service 应 cleanup 删孤儿 + rollback + raise
    async with async_session_factory() as s:
        with patch.object(s, "commit", side_effect=RuntimeError("db down")):
            with pytest.raises(RuntimeError):
                await archive_sheet(s, sid, archive_root=root, player=player)
    # Assert：文件被 cleanup 删除（无孤儿）
    assert not final.exists()
    # DB 未变（仍是 collecting）
    async with async_session_factory() as s:
        got = await sheet_repo.get_sheet(s, sid)
        assert got[0].status == "collecting"
        assert got[0].archived_path is None


@pytest.mark.asyncio
async def test_archive_sheet_archive_root_unconfigured_raises(tmp_path):
    # Arrange
    sid, player, _ = await _make_collecting_sheet("无根")
    # Act / Assert：空 archive_root → ArchiveNotConfigured（写盘前抛，DB 未动）
    async with async_session_factory() as s:
        with pytest.raises(ArchiveNotConfigured):
            await archive_sheet(s, sid, archive_root="", player=player)
    # DB 未变
    async with async_session_factory() as s:
        got = await sheet_repo.get_sheet(s, sid)
        assert got[0].status == "collecting"


@pytest.mark.asyncio
async def test_archive_sheet_missing_raises_not_found(tmp_path):
    # Arrange / Act / Assert
    player, _ = await _seed_player()
    async with async_session_factory() as s:
        with pytest.raises(SheetNotFoundError):
            await archive_sheet(s, 999999, archive_root=str(tmp_path), player=player)
