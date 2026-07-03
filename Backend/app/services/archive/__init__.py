"""sheet 归档服务（markdown_render 首个消费者 + 文件系统落盘 + 事务一致性）。

公共导出（Phase 4/api 对齐）::

    from app.services.archive import (
        ArchiveNotConfigured,
        SheetNotFoundError,
        SheetStatusError,
        archive_sheet,
        build_sheet_archive_document,
        read_archive_file,
    )

子模块：
- ``renderer``：纯函数渲染（build_sheet_archive_document / render_*）；context key 见该模块 docstring。
- ``writer``：文件落盘（write_atomic / read_archive_file / cleanup）+ ArchiveNotConfigured。
- ``service``：编排（archive_sheet 事务一致性 + 通知联动 RS-9）。
"""
from app.services.archive.renderer import (
    build_sheet_archive_document,
    render_contributor_stats,
    render_material_table,
    render_timeline,
)
from app.services.archive.service import (
    SheetNotFoundError,
    SheetStatusError,
    archive_sheet,
)
from app.services.archive.writer import (
    ArchiveNotConfigured,
    cleanup,
    read_archive_file,
    write_atomic,
)

__all__ = [
    "ArchiveNotConfigured",
    "SheetNotFoundError",
    "SheetStatusError",
    "archive_sheet",
    "build_sheet_archive_document",
    "cleanup",
    "read_archive_file",
    "render_contributor_stats",
    "render_material_table",
    "render_timeline",
    "write_atomic",
]
