"""投影文件解析路由（预览，不落库）。

鉴权：``get_current_player``（Web 走 JWT）。解析为 CPU 密集，``asyncio.to_thread`` 卸到
线程池，不阻塞事件循环（RS-7）。上传字节仅用于解析，**不持久化文件、不落库**。
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status

from app.api.deps import get_current_player
from app.core.config import get_settings
from app.models.user import Player
from app.schemas.parsing import ParsedMaterialPreview, PreviewItem, PreviewMeta
from app.services.parsing import preview as preview_service
from app.services.parsing.parsers.litematic import LitematicParseError, LitematicParser

router = APIRouter(prefix="/parsing", tags=["parsing"])
_settings = get_settings()
logger = logging.getLogger(__name__)

_LITEMATIC_EXT = ".litematic"


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
        # 玩家可读的固定文案；原始异常仅服务端日志（不外泄内部细节）。
        # 若 __cause__ 是 KeyError，多为 litemapy 对某个 NBT 键直接下标失败——
        # 提示 dev 评估是否要把该键加入 _OPTIONAL_REGION_LIST_KEYS（见 parsers/litematic.py）。
        if isinstance(exc.__cause__, KeyError):
            logger.warning(
                "litematic parse failed for %r: litemapy KeyError on NBT key %s — "
                "若属规范可选键，把它加入 _OPTIONAL_REGION_LIST_KEYS 即可恢复解析",
                filename,
                exc.__cause__,
            )
        else:
            logger.warning("litematic parse failed for %r: %s", filename, exc)
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "无法解析该投影文件，请确认上传的是由投影 mod 导出的有效 .litematic 文件",
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
