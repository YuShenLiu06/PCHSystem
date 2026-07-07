"""投影文件解析路由（预览，不落库）。

鉴权：``get_current_player``（Web 走 JWT）。解析为 CPU 密集，``asyncio.to_thread`` 卸到
线程池，不阻塞事件循环（RS-7）。上传字节仅用于解析，**不持久化文件、不落库**。
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status

from app.api.deps import get_current_player
from app.core.config import get_settings
from app.models.user import Player
from app.schemas.parsing import ParsedMaterialPreview, PreviewItem, PreviewMeta
from app.services.parsing import preview as preview_service
from app.services.parsing.parsers.litematic import LitematicParseError, LitematicParser
from app.services.parsing.parsers.nbt import NbtParseError, NbtParser

router = APIRouter(prefix="/parsing", tags=["parsing"])
_settings = get_settings()

_LITEMATIC_EXT = ".litematic"
_NBT_EXT = ".nbt"


@router.post("/litematic", response_model=ParsedMaterialPreview)
async def parse_litematic(
    file: UploadFile,
    player: Player = Depends(get_current_player),
) -> ParsedMaterialPreview:
    """上传 ``.litematic`` → 解析材料 + 翻译中文 → 分组预览（方块组 + 容器组）。"""
    del player  # 仅鉴权，业务无身份依赖（任何登录玩家均可解析预览）

    filename = file.filename or ""
    if not filename.lower().endswith(_LITEMATIC_EXT):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "only .litematic files are supported"
        )

    data = await file.read()
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "empty file")
    if len(data) > _settings.litematic_max_upload_bytes:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "file too large"
        )

    parser = LitematicParser()
    translator = preview_service.get_default_translator()
    try:
        parsed = await asyncio.to_thread(parser.parse, data, filename)
    except LitematicParseError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"failed to parse litematic: {exc}",
        ) from exc

    blocks, containers, untranslated = preview_service.build_preview(parsed, translator)
    return ParsedMaterialPreview(
        meta=PreviewMeta(
            filename=parsed.meta.filename,
            schematic_name=parsed.meta.schematic_name,
            author=parsed.meta.author,
            region_count=parsed.meta.region_count,
            total_blocks=parsed.meta.total_blocks,
            total_volume=parsed.meta.total_volume,
        ),
        blocks=[
            PreviewItem(item_id=e.item_id, item_name=e.item_name, count=e.count)
            for e in blocks
        ],
        container_items=[
            PreviewItem(item_id=e.item_id, item_name=e.item_name, count=e.count)
            for e in containers
        ],
        untranslated=untranslated,
    )


@router.post("/nbt", response_model=ParsedMaterialPreview)
async def parse_nbt(
    file: UploadFile,
    player: Player = Depends(get_current_player),
) -> ParsedMaterialPreview:
    """上传 ``.nbt``（Create 蓝图 / structure）→ 解析材料 + 翻译中文 → 分组预览（方块组 + 容器组）。"""
    del player  # 仅鉴权，业务无身份依赖（任何登录玩家均可解析预览）

    filename = file.filename or ""
    if not filename.lower().endswith(_NBT_EXT):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "only .nbt files are supported"
        )

    data = await file.read()
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "empty file")
    if len(data) > _settings.nbt_max_upload_bytes:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "file too large"
        )

    parser = NbtParser()
    translator = preview_service.get_default_translator()
    try:
        parsed = await asyncio.to_thread(parser.parse, data, filename)
    except NbtParseError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"failed to parse nbt: {exc}",
        ) from exc

    blocks, containers, untranslated = preview_service.build_preview(parsed, translator)
    return ParsedMaterialPreview(
        meta=PreviewMeta(
            filename=parsed.meta.filename,
            schematic_name=parsed.meta.schematic_name,
            author=parsed.meta.author,
            region_count=parsed.meta.region_count,
            total_blocks=parsed.meta.total_blocks,
            total_volume=parsed.meta.total_volume,
        ),
        blocks=[
            PreviewItem(item_id=e.item_id, item_name=e.item_name, count=e.count)
            for e in blocks
        ],
        container_items=[
            PreviewItem(item_id=e.item_id, item_name=e.item_name, count=e.count)
            for e in containers
        ],
        untranslated=untranslated,
    )
