"""wiki git publisher 测试（archive/publisher.py）。

连真实 PG（conftest autouse truncate 每测清库）；archive_root 用 pytest ``tmp_path``；
remote 用本地 bare 仓（``git init --bare`` + ``file://`` 协议）。

覆盖：
1. push 成功：bare 仓收到两条文件（index.md + contributions.png）+ commit message 含 sheet_id；不抛。
2. push 失败 best-effort：remote 指向不存在路径 → publish 不抛；owner 收到
   ``category="wiki_publish_failed"`` 通知；本地产物仍在；DB sheet 仍 archived。
3. 未配 remote 早返：``wiki_git_remote_url=""`` → 立即 return，archive_root 下无 ``.git``。

AAA 结构；sheet 直接经 ``sheet_repo.create_sheet`` + ``advance_sheet`` 置 archived 态。
"""
from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.core.db import async_session_factory
from app.models.notification import Notification
from app.models.user import Player
from app.repositories import sheet_repo
from app.services.archive import publisher


# ---------- fixtures & helpers ----------


async def _seed_player(name: str = "alice") -> uuid.UUID:
    """seed 一个 users.players 行，返回其 UUID（owner）。"""
    u = uuid.uuid4()
    async with async_session_factory() as s:
        s.add(Player(uuid=u, current_name=name))
        await s.commit()
    return u


async def _seed_archived_sheet(title: str = "项目A") -> tuple[int, uuid.UUID]:
    """建一张表并 advance 到 archived 态；返回 (sheet_id, owner_uuid)。

    archived_path 用归档服务约定的相对 POSIX 路径 ``projects/{id}/index.md``。
    """
    owner_uuid = await _seed_player()
    async with async_session_factory() as s:
        sheet = await sheet_repo.create_sheet(s, owner_uuid, title)
        await sheet_repo.advance_sheet(
            s,
            sheet.id,
            sheet_repo.SHEET_PHASE_ARCHIVED,
            archived_path=f"projects/{sheet.id}/index.md",
        )
        await s.commit()
        sid = sheet.id
    return sid, owner_uuid


def _seed_archive_artifacts(archive_root: Path, sheet_id: int) -> None:
    """往 archive_root/projects/<sheet_id>/ 写 index.md + contributions.png（随便几字节）。"""
    proj = archive_root / "projects" / str(sheet_id)
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "index.md").write_text(f"# 📦 项目归档：#{sheet_id}\n", encoding="utf-8")
    (proj / "contributions.png").write_bytes(b"\x89PNG\r\n\x1a\nfake-png-bytes")


def _make_cfg(**overrides) -> Settings:
    """构造一个 Settings 实例并按 overrides 覆盖 wiki_git_* 字段（字段无 frozen，可改）。"""
    cfg = Settings()
    cfg.wiki_git_remote_url = overrides.get("wiki_git_remote_url", "")
    cfg.wiki_git_branch = overrides.get("wiki_git_branch", "main")
    cfg.wiki_git_token = overrides.get("wiki_git_token", "")
    cfg.wiki_git_author_name = overrides.get("wiki_git_author_name", "PCHSystem")
    cfg.wiki_git_author_email = overrides.get(
        "wiki_git_author_email", "pchsystem@local"
    )
    return cfg


def _init_bare_repo(path: Path, branch: str = "main") -> str:
    """在 path 下 ``git init --bare`` 一个 bare 仓（HEAD 默认指向 branch），返回 file:// URL。

    bare 仓默认 HEAD → refs/heads/<init 默认分支>；push HEAD:<branch> 后要让 ``git log``
    （默认查 HEAD）能查到，须把 bare HEAD 指向同一 branch。``git init --bare -b <branch>``
    在 2.28+ 可一步设 HEAD；旧版回退用 symbolic-ref。
    """
    path.mkdir(parents=True, exist_ok=True)
    init = subprocess.run(
        ["git", "init", "--bare", "-b", branch, str(path)],
        capture_output=True,
        text=True,
    )
    if init.returncode != 0:  # 旧 git 无 -b：先默认 init 再 symbolic-ref 改 HEAD
        subprocess.run(
            ["git", "init", "--bare", str(path)],
            capture_output=True,
            text=True,
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(path), "symbolic-ref", "HEAD", f"refs/heads/{branch}"],
            capture_output=True,
            text=True,
            check=True,
        )
    return f"file://{path}"


# ---------- 测试 1：push 成功 ----------


@pytest.mark.asyncio
async def test_publish_pushes_artifacts_to_remote_and_commits_message(tmp_path):
    # Arrange：seed archived sheet + 产物 + bare 仓 remote
    sid, owner_uuid = await _seed_archived_sheet("大教堂主材")
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    _seed_archive_artifacts(archive_root, sid)
    remote_dir = tmp_path / "remote.git"
    remote_url = _init_bare_repo(remote_dir)
    cfg = _make_cfg(wiki_git_remote_url=remote_url, wiki_git_branch="main")
    # Act
    await publisher.publish(
        sid, "大教堂主材", owner_uuid, archive_root=str(archive_root), cfg=cfg
    )
    # Assert：bare 仓 log 含两条文件 + commit message 含 sheet_id
    log = subprocess.run(
        ["git", "-C", str(remote_dir), "log", "--name-only", "--pretty=format:%s"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert f"#{sid}" in log  # commit message 含 sheet_id
    assert "index.md" in log
    assert "contributions.png" in log


# ---------- 测试 2：push 失败 best-effort ----------


@pytest.mark.asyncio
async def test_publish_failure_is_best_effort_and_notifies_owner(tmp_path):
    # Arrange：seed archived sheet + 产物；remote 指向不存在路径
    sid, owner_uuid = await _seed_archived_sheet("失败项目")
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    _seed_archive_artifacts(archive_root, sid)
    cfg = _make_cfg(
        wiki_git_remote_url="file:///nonexistent/path/x.git", wiki_git_branch="main"
    )
    # Act：publish 不抛（best-effort）
    await publisher.publish(
        sid, "失败项目", owner_uuid, archive_root=str(archive_root), cfg=cfg
    )
    # Assert 1：owner 收到一条 wiki_publish_failed 通知
    async with async_session_factory() as s:
        notifs = (
            await s.execute(
                select(Notification).where(Notification.recipient_uuid == owner_uuid)
            )
        ).scalars().all()
    failed = [n for n in notifs if n.category == "wiki_publish_failed"]
    assert len(failed) == 1
    n = failed[0]
    assert n.title == "Wiki 同步失败"
    assert n.payload["sheet_id"] == sid
    assert n.payload["sheet_title"] == "失败项目"
    assert "error" in n.payload
    # Assert 2：本地产物仍在（best-effort 不删归档产物）
    assert (archive_root / "projects" / str(sid) / "index.md").is_file()
    # Assert 3：DB sheet 仍 archived（绝无回滚）
    async with async_session_factory() as s:
        got = await sheet_repo.get_sheet(s, sid)
        assert got is not None
        assert got[0].status == "archived"
        assert got[0].archived_path == f"projects/{sid}/index.md"


# ---------- 测试 3b：失败通知 payload 不泄露 token（R-11）----------


def test_scrub_token_strips_embedded_credentials():
    """``_scrub_token`` 抹掉异常文本中的内嵌 token URL 与 token 字面量（R-11）。

    ``CalledProcessError`` 的 ``str(exc)`` 含完整命令行（含 tokenized push URL），
    publisher 在写日志/通知前必须脱敏。直接单测脱敏函数（不触发真实 push，避免网络超时）。
    """
    secret = "super-secret-token-12345"
    cmd = (
        f"Command '['git', 'push', "
        f"'https://x-access-token:{secret}@github.com/owner/repo.git', 'HEAD:main']' "
        f"returned non-zero exit status 128."
    )
    cleaned = publisher._scrub_token(cmd, secret)
    # token 字面量 + 内嵌凭证段都被抹掉
    assert secret not in cleaned
    assert f"x-access-token:{secret}" not in cleaned
    # 脱敏后仍保留可读结构（host/path、退出码）
    assert "github.com/owner/repo.git" in cleaned
    assert "128" in cleaned


# ---------- 测试 4：未配 remote 早返 ----------


@pytest.mark.asyncio
async def test_publish_without_remote_returns_early_no_git_init(tmp_path):
    # Arrange：seed archived sheet + 产物；wiki_git_remote_url 留空
    sid, owner_uuid = await _seed_archived_sheet("未配远端")
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    _seed_archive_artifacts(archive_root, sid)
    cfg = _make_cfg(wiki_git_remote_url="")  # 未配置
    # Act
    await publisher.publish(
        sid, "未配远端", owner_uuid, archive_root=str(archive_root), cfg=cfg
    )
    # Assert：archive_root 下无 .git（默认 off，未 init）
    assert not (archive_root / ".git").exists()
    # owner 无 wiki_publish_failed 通知（早返路径不发通知）
    async with async_session_factory() as s:
        notifs = (
            await s.execute(
                select(Notification).where(Notification.recipient_uuid == owner_uuid)
            )
        ).scalars().all()
    assert all(n.category != "wiki_publish_failed" for n in notifs)
