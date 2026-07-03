"""归档 markdown 文件落盘（原子写 + 路径穿越防护）。

职责（计划 §归档服务 writer.py）：
- ``write_atomic``：在 ``<archive_root>/projects/{sheet_id}/index.md`` 原子写 markdown，
  返回相对 root 的 POSIX 路径（``projects/{sheet_id}/index.md``），供 DB ``archived_path`` 列存。
- ``read_archive_file``：按相对路径读回内容（Phase 4 GET /archive 用）；不存在 → None。
- ``cleanup``：删目标文件（commit 失败回滚时清孤儿）；吞 FileNotFoundError。

不变量：
- 路径穿越防护：所有解析后的绝对路径必须落在 ``archive_root.resolve()`` 之内，否则 ValueError。
- 原子写：先写 ``<root>/.tmp/{sheet_id}.md.<pid>``（UTF-8），再 ``os.replace`` 到目标（同文件系统原子）。
- 单文件覆盖（YAGNI，archived 终态不重归档）；``{sheet_id}`` 稳定可预测。

注意：文件系统操作**不参与 DB 事务**——commit 失败时由 service 层调 ``cleanup`` 清孤儿文件。
"""
from __future__ import annotations

import os
from pathlib import Path

# 归档文件统一存放子目录（相对 archive_root）。
_PROJECTS_SUBDIR = "projects"
# 每个项目目录下的 markdown 主文件名（与 contributions.png 等产物同目录）。
_INDEX_FILENAME = "index.md"
# 临时写入目录（相对 archive_root），原子写中转。
_TMP_SUBDIR = ".tmp"


class ArchiveNotConfigured(Exception):
    """archive_root 未配置（空串）。api 层翻译为 503。"""


def _resolve_root(archive_root: str) -> Path:
    """校验 archive_root 非空并返回 resolve 后的绝对路径。

    空串 → raise ArchiveNotConfigured（api 层 503）。
    """
    if not archive_root or not archive_root.strip():
        raise ArchiveNotConfigured("archive_root is not configured")
    return Path(archive_root).resolve()


def _assert_within(root: Path, target: Path) -> None:
    """路径穿越防护：target.resolve() 必须落在 root 之内（含 root 自身），否则 ValueError。

    ``is_relative_to`` 在 Python 3.9+ 可用；非 root 后代 → ValueError（带诊断信息）。
    """
    resolved = target.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(
            f"path traversal rejected: {target} resolves outside archive root {root}"
        )


def write_atomic(archive_root: str, sheet_id: int, md: str) -> str:
    """原子写归档 markdown，返回相对 root 的 POSIX 路径 ``projects/{sheet_id}/index.md``。

    步骤：
    1. resolve root + 路径穿越防护（空 root → ArchiveNotConfigured）。
    2. mkdir ``<root>/projects/{sheet_id}`` 和 ``<root>/.tmp``（parents=True, exist_ok=True）。
    3. UTF-8 写临时文件 ``<root>/.tmp/{sheet_id}.index.md.<pid>``。
    4. ``os.replace(tmp, final)``（同文件系统原子替换）。

    每项目独立文件夹（``projects/{id}/``）：``index.md`` + 未来 ``contributions.png`` 等产物同目录。
    sheet_id 是 int，路径恒在 root/projects 下；穿越防护是纵深防御（保护 symlink / 异常 root）。
    """
    root = _resolve_root(archive_root)
    projects_dir = root / _PROJECTS_SUBDIR
    project_dir = projects_dir / str(sheet_id)
    tmp_dir = root / _TMP_SUBDIR
    final = project_dir / _INDEX_FILENAME

    _assert_within(root, final)

    project_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    tmp_path = tmp_dir / f"{sheet_id}.{_INDEX_FILENAME}.{os.getpid()}"
    tmp_path.write_text(md, encoding="utf-8")
    # os.replace：同文件系统原子替换；目标存在则覆盖（单文件覆盖语义）。
    os.replace(tmp_path, final)

    return f"{_PROJECTS_SUBDIR}/{sheet_id}/{_INDEX_FILENAME}"


def write_bytes_atomic(
    archive_root: str, sheet_id: int, filename: str, data: bytes
) -> str:
    """原子写二进制产物（如 contributions.png）到 ``projects/{sheet_id}/{filename}``。

    返回相对 root 的 POSIX 路径。``filename`` 必须**仅 basename**（禁 ``/`` / ``..``，
    调用方传字面量如 ``contributions.png``）；纵深防御校验。
    复用 write_atomic 的 ``projects/{sheet_id}/`` 目录（已 mkdir）。
    """
    if filename != Path(filename).name:
        raise ValueError(f"filename must be a basename, got {filename!r}")

    root = _resolve_root(archive_root)
    project_dir = root / _PROJECTS_SUBDIR / str(sheet_id)
    tmp_dir = root / _TMP_SUBDIR
    final = project_dir / filename

    _assert_within(root, final)

    project_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    tmp_path = tmp_dir / f"{sheet_id}.{filename}.{os.getpid()}"
    tmp_path.write_bytes(data)
    os.replace(tmp_path, final)

    return f"{_PROJECTS_SUBDIR}/{sheet_id}/{filename}"


def read_archive_file(archive_root: str, rel_path: str) -> str | None:
    """按相对路径读归档文件内容（UTF-8）；不存在 → None。

    rel_path 期望是 ``write_atomic`` 返回的相对 POSIX 路径（如 ``projects/42.md``）。
    路径穿越防护：``../`` 注入解析到 root 之外 → ValueError（api 层 400/404）。
    """
    root = _resolve_root(archive_root)
    target = (root / rel_path)
    _assert_within(root, target)
    if not target.is_file():
        return None
    return target.read_text(encoding="utf-8")


def read_archive_bytes(archive_root: str, rel_path: str) -> bytes | None:
    """按相对路径读归档二进制产物（如 contributions.png）；不存在 → None。

    与 ``read_archive_file`` 同样的路径穿越防护；rel_path 期望 ``write_bytes_atomic``
    返回的相对 POSIX 路径（如 ``projects/42/contributions.png``）。
    """
    root = _resolve_root(archive_root)
    target = root / rel_path
    _assert_within(root, target)
    if not target.is_file():
        return None
    return target.read_bytes()


def cleanup(archive_root: str, rel_path: str) -> None:
    """删目标归档文件（commit 失败回滚时清孤儿）。

    文件不存在 → 静默（吞 FileNotFoundError）；路径穿越防护仍生效。
    其他 OSError（权限等）正常上抛，由调用方处理。
    """
    root = _resolve_root(archive_root)
    target = (root / rel_path)
    _assert_within(root, target)
    try:
        target.unlink()
    except FileNotFoundError:
        # 已被删 / 从未真正落盘，幂等吞掉。
        return
