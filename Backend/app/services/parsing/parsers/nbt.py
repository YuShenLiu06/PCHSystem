""".nbt(Create 蓝图 / structure) 解析器（基于 nbtlib）。

nbtlib（litemapy 已自带）负责 NBT 二进制/gzip/tag 解码；本类只做「计数 + 分组」。

- 方块：逐 block 读取 palette Name（纯 namespace:path，不含 properties，满足 R-6）。
- 容器：仅读 vanilla Items 键（箱子/木桶/漏斗/潜影盒 ... 的 {id, Count, Slot}）。
  Create 自有存储（create:item_vault 等）不走 Items，其内容暂不可提取
  （见 Docs/architecture/api/parsing.md §限制）。
"""
from __future__ import annotations

import gzip
import io
from collections import Counter
from pathlib import Path

import nbtlib

from app.services.parsing.models import MaterialEntry, ParseMeta, ParsedMaterialList
from app.services.parsing.normalize import SKIP_BLOCKS

from .base import MaterialParser


class NbtParseError(Exception):
    """.nbt 解析失败（文件损坏 / 非 structure NBT / nbtlib 抛错）。api 层翻译为 422。"""


class NbtParser(MaterialParser):
    """解析 ``.nbt`` 字节流 → ``ParsedMaterialList``（Create 蓝图 / standard structure）。"""

    def parse(self, data: bytes, filename: str) -> ParsedMaterialList:
        # nbtlib.File.parse 可解析裸 NBT 或 gzip 压缩；先尝试裸 NBT，失败再 gunzip 兜底
        root = None
        try:
            root = nbtlib.File.parse(io.BytesIO(data))
        except Exception:
            try:
                root = nbtlib.File.parse(io.BytesIO(gzip.decompress(data)))
            except Exception as exc:
                raise NbtParseError(str(exc)) from exc

        # 结构校验：structure NBT 必含 palette 与 blocks
        if "palette" not in root or "blocks" not in root:
            raise NbtParseError("not a structure NBT (missing palette/blocks)")

        palette = root["palette"]
        blocks = root["blocks"]

        blocks_counter: Counter[str] = Counter()
        container_counter: Counter[str] = Counter()

        # 方块计数
        for b in blocks:
            state_idx = int(b["state"])
            name = str(palette[state_idx]["Name"])
            if name in SKIP_BLOCKS:
                continue
            blocks_counter[name] += 1

            # 容器物品（vanilla Items 键）
            nbt_data = b.get("nbt")
            if nbt_data and "Items" in nbt_data:
                for it in nbt_data["Items"]:
                    item_id = str(it.get("id", ""))
                    if not item_id:
                        continue
                    # 兼容经典 Count（Byte/Int）与小写 count
                    raw_count = it.get("Count", it.get("count", 1))
                    try:
                        count = int(raw_count)
                    except (TypeError, ValueError):
                        continue
                    if count > 0:
                        container_counter[item_id] += count

        # meta 组装
        size = root.get("size", [0, 0, 0])
        sx, sy, sz = (int(x) for x in size)
        total_volume = abs(sx * sy * sz)

        blocks = tuple(
            MaterialEntry(item_id=i, count=c) for i, c in blocks_counter.most_common()
        )
        container_items = tuple(
            MaterialEntry(item_id=i, count=c) for i, c in container_counter.most_common()
        )
        meta = ParseMeta(
            filename=filename,
            schematic_name=Path(filename).stem,
            author="",  # structure 格式无 author 字段
            region_count=1,  # 单 structure
            total_blocks=sum(blocks_counter.values()),
            total_volume=total_volume,
        )
        return ParsedMaterialList(blocks=blocks, container_items=container_items, meta=meta)
