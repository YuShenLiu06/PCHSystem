""".litematic 解析器（基于 litemapy）。

litemapy（仓库 https://github.com/SmylerMC/litemapy ，自带 nbtlib，无需 amulet-nbt）
负责 gzip+NBT 与 bit-packed BlockStates 解码；本类只做「计数 + 分组」。

- 方块：逐体素 ``region[x, y, z].id``（纯 ``namespace:path``，properties 不含其中，满足 R-6）。
- 容器：仅读 vanilla ``Items`` 键（箱子/木桶/漏斗/潜影盒 ... 的 ``{id, Count, Slot}``）。
  Create 自有存储（``create:item_vault`` 等）不走 ``Items``，其内容暂不可提取
  （见 ``Docs/architecture/api/parsing.md`` §限制）。
"""
from __future__ import annotations

import os
import tempfile
from collections import Counter

from litemapy import Schematic

from app.services.parsing.models import MaterialEntry, ParseMeta, ParsedMaterialList
from app.services.parsing.normalize import SKIP_BLOCKS

from .base import MaterialParser


class LitematicParseError(Exception):
    """.litematic 解析失败（文件损坏 / 非 litematic NBT / litemapy 抛错）。api 层翻译为 422。"""


class LitematicParser(MaterialParser):
    """解析 ``.litematic`` 字节流 → ``ParsedMaterialList``。"""

    def parse(self, data: bytes, filename: str) -> ParsedMaterialList:
        # litemapy.Schematic.load 需要文件路径：写临时文件，用完即删（不持久化、不落库）。
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".litematic")
        try:
            with os.fdopen(tmp_fd, "wb") as tmp:
                tmp.write(data)
            try:
                schem = Schematic.load(tmp_path)
            except Exception as exc:  # litemapy 对损坏/非 litematic 抛多种异常
                raise LitematicParseError(str(exc)) from exc
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

        blocks_counter: Counter[str] = Counter()
        container_counter: Counter[str] = Counter()
        total_volume = 0

        for _name, region in schem.regions.items():
            # 方块（区域尺寸可负，取绝对值）
            total_volume += abs(region.width) * abs(region.height) * abs(region.length)
            for x, y, z in region.block_positions():
                bid = region[x, y, z].id
                if bid in SKIP_BLOCKS:
                    continue
                blocks_counter[bid] += 1
            # 容器（vanilla Items 键）
            for te in region.tile_entities:
                items = te.data.get("Items")
                if not items:
                    continue
                for it in items:
                    item_id = str(it.get("id", ""))
                    if not item_id:
                        continue
                    # 兼容经典 Count（Byte/Int）与新版小写 count
                    raw_count = it.get("Count", it.get("count", 1))
                    try:
                        count = int(raw_count)
                    except (TypeError, ValueError):
                        continue
                    if count > 0:
                        container_counter[item_id] += count

        blocks = tuple(
            MaterialEntry(item_id=i, count=c) for i, c in blocks_counter.most_common()
        )
        container_items = tuple(
            MaterialEntry(item_id=i, count=c) for i, c in container_counter.most_common()
        )
        meta = ParseMeta(
            filename=filename,
            schematic_name=schem.name or "",
            author=schem.author or "",
            region_count=len(schem.regions),
            total_blocks=sum(blocks_counter.values()),
            total_volume=total_volume,
        )
        return ParsedMaterialList(blocks=blocks, container_items=container_items, meta=meta)
