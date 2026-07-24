"""投影文件解析路由（预览，不落库）。

鉴权：``get_current_player``（Web 走 JWT）。解析为 CPU 密集，``asyncio.to_thread`` 卸到
线程池，不阻塞事件循环（RS-7）。上传字节仅用于解析，**不持久化文件、不落库**。

端点：
- ``POST /parsing/batch``：**唯一**解析端点（批量 / 单文件统一入口，混型 ``.litematic`` /
  ``.nbt``）。每文件独立成功/失败隔离；后端只解析，不收 multiplier——倍数与跨文件聚合
  在前端做，便于随时调倍数无需重新上传。单文件等价于批量 1 个文件。
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from app.api.deps import get_current_player
from app.core.config import get_settings
from app.models.user import Player
from app.schemas.parsing import (
    BatchFilePreview,
    BatchParsedPreview,
    ParsedMaterialPreview,
    PreviewItem,
    PreviewMeta,
)
from app.services import translation
from app.services.parsing import preview as preview_service
from app.services.parsing.parsers.litematic import LitematicParseError, LitematicParser
from app.services.parsing.parsers.nbt import NbtParseError, NbtParser

router = APIRouter(prefix="/parsing", tags=["parsing"])
_settings = get_settings()
logger = logging.getLogger(__name__)

_LITEMATIC_EXT = ".litematic"
_NBT_EXT = ".nbt"

# 玩家可读的固定文案；原始异常仅服务端日志（不外泄内部细节如 NBT 键名）。
_LITEMATIC_FRIENDLY = "无法解析该投影文件，请确认上传的是由投影 mod 导出的有效 .litematic 文件"
_NBT_FRIENDLY = "无法解析该蓝图文件，请确认上传的是有效的 .nbt 结构文件（Create 蓝图 / 原版结构）"


def _detect_kind(filename: str) -> str | None:
    """按扩展名判定解析类型（大小写不敏感）。"""
    name = filename.lower()
    if name.endswith(_NBT_EXT):
        return "nbt"
    if name.endswith(_LITEMATIC_EXT):
        return "litematic"
    return None


def _friendly_parse_error(kind: str) -> str:
    """解析失败的玩家可读中文文案（按文件类型）。"""
    return _NBT_FRIENDLY if kind == "nbt" else _LITEMATIC_FRIENDLY


def _parse_bytes_to_preview(filename: str, kind: str, data: bytes) -> ParsedMaterialPreview:
    """同步：bytes → ``ParsedMaterialPreview``（parser.parse + build_preview + 组装 Pydantic）。

    设计为纯函数（无 await），可安全在 ``asyncio.to_thread`` 内调用 / 循环。解析异常
    （``LitematicParseError`` / ``NbtParseError``）向上抛，由调用方决定 HTTP / 批量项映射。
    """
    parser = NbtParser() if kind == "nbt" else LitematicParser()
    translator = translation.get_translator()
    try:
        parsed = parser.parse(data, filename)  # 解析失败时抛 LitematicParseError / NbtParseError
    except LitematicParseError as exc:
        # KeyError __cause__ 多为 litemapy 对某 NBT 键直接下标失败——提示 dev 评估是否要把
        # 该键加入 _OPTIONAL_REGION_LIST_KEYS（见 parsers/litematic.py，issue #8 类排障线索）。
        # 放在共享 helper 内，使唯一端点 /parsing/batch 的 per-file 失败也能产出此日志。
        if isinstance(exc.__cause__, KeyError):
            logger.warning(
                "litematic parse failed for %r: litemapy KeyError on NBT key %s — "
                "若属规范可选键，把它加入 _OPTIONAL_REGION_LIST_KEYS 即可恢复解析",
                filename,
                exc.__cause__,
            )
        else:
            logger.warning("litematic parse failed for %r: %s", filename, exc)
        raise
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


@router.post("/batch", response_model=BatchParsedPreview)
async def parse_batch(
    files: list[UploadFile] = File(...),
    player: Player = Depends(get_current_player),
) -> BatchParsedPreview:
    """批量上传多个 ``.litematic`` / ``.nbt`` → 每文件独立预览（不落库、不收 multiplier）。

    单文件失败不影响其他文件（per-file error isolation）；倍数与跨文件聚合由前端在
    解析结果上计算（便于随时调倍数无需重新上传）。执行分两阶段：

    1. async 阶段：逐文件 ``await read()`` + 单文件级校验（扩展名 / 非空 / 单文件大小），
       累计总字节做整请求总大小护栏。``await file.read()`` 不能进线程，故在读阶段完成。
    2. 单个 ``asyncio.to_thread``：一个线程内顺序解析所有合法文件（不并行——大文件解析
       已是 CPU 密集，并行反而抢线程池），``try/except`` per-file 隔离解析失败。
    """
    del player  # 仅鉴权，业务无身份依赖
    # files: list[UploadFile] = File(...) 为必填——未提供文件时 FastAPI 在进入 handler 前
    # 即以 422（field required）拒绝，故此处无需再判空。
    if len(files) > _settings.parsing_batch_max_files:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"批量解析最多 {_settings.parsing_batch_max_files} 个文件",
        )

    # 阶段 1：读字节 + 单文件校验。不合法文件直接记 error 不进线程。
    jobs: list[dict] = []
    total = 0
    for f in files:
        filename = f.filename or ""
        kind = _detect_kind(filename)
        if kind is None:
            jobs.append({"filename": filename, "kind": None, "error": "仅支持 .litematic / .nbt 文件"})
            continue
        data = await f.read()
        if not data:
            jobs.append({"filename": filename, "kind": kind, "error": "空文件"})
            continue
        cap = _settings.nbt_max_upload_bytes if kind == "nbt" else _settings.litematic_max_upload_bytes
        if len(data) > cap:
            jobs.append({"filename": filename, "kind": kind, "error": "文件过大"})
            continue
        total += len(data)
        jobs.append({"filename": filename, "kind": kind, "data": data})

    if total > _settings.parsing_batch_total_max_bytes:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"批量总大小超限（上限 {_settings.parsing_batch_total_max_bytes // (1024 * 1024)}MB）",
        )

    # 阶段 2：单线程内顺序解析，per-file 隔离。
    def _run() -> list[BatchFilePreview]:
        out: list[BatchFilePreview] = []
        for job in jobs:
            if "error" in job:
                out.append(BatchFilePreview(
                    filename=job["filename"],
                    kind=job["kind"] or "litematic",
                    status="error",
                    preview=None,
                    error=job["error"],
                ))
                continue
            try:
                preview = _parse_bytes_to_preview(job["filename"], job["kind"], job["data"])
                out.append(BatchFilePreview(
                    filename=job["filename"], kind=job["kind"], status="ok",
                    preview=preview, error=None,
                ))
            except (LitematicParseError, NbtParseError) as exc:
                logger.warning("batch parse failed for %r: %s", job["filename"], exc)
                out.append(BatchFilePreview(
                    filename=job["filename"], kind=job["kind"], status="error",
                    preview=None, error=_friendly_parse_error(job["kind"]),
                ))
        return out

    file_results = await asyncio.to_thread(_run)
    return BatchParsedPreview(files=file_results)
