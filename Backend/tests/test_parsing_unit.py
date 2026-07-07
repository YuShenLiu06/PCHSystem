"""投影解析单元测试：LitematicParser（真样例文件）+ LangJsonTranslator（注入表）+ lang key 候选。

纯单元，不触 DB；但 conftest 仍 autouse truncate（无副作用）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services.parsing.parsers.litematic import LitematicParseError, LitematicParser
from app.services.parsing.parsers.nbt import NbtParseError, NbtParser
from app.services.parsing.translators.lang_json import (
    LangJsonTranslator,
    lang_key_candidates,
)

_FIXTURE = Path(__file__).parent / "fixtures" / "机械动力仓库_1.litematic"
_NBT_FIXTURE = Path(__file__).parent / "fixtures" / "create_blueprint_sample.nbt"


# ---------- lang_key_candidates ----------
def test_lang_key_candidates_block_then_item():
    # 命名空间:path → 先 block. 后 item.
    assert lang_key_candidates("minecraft:stone") == [
        "block.minecraft.stone",
        "item.minecraft.stone",
    ]
    assert lang_key_candidates("create:cogwheel") == [
        "block.create.cogwheel",
        "item.create.cogwheel",
    ]


def test_lang_key_candidates_no_namespace_returns_empty():
    assert lang_key_candidates("nonsense") == []


# ---------- LangJsonTranslator ----------
def test_translator_prefers_block_key():
    t = LangJsonTranslator(
        {"block.minecraft.stone": "石头", "item.minecraft.stone": "圆石"}
    )
    assert t.translate("minecraft:stone") == "石头"  # block 优先


def test_translator_falls_back_to_item_key():
    t = LangJsonTranslator({"item.minecraft.stick": "木棍"})
    assert t.translate("minecraft:stick") == "木棍"


def test_translator_miss_returns_original_id():
    t = LangJsonTranslator({})
    assert t.translate("create:totally_unknown") == "create:totally_unknown"


# ---------- LitematicParser ----------
def test_litematic_parser_parses_sample_structure():
    data = _FIXTURE.read_bytes()
    parsed = LitematicParser().parse(data, "机械动力仓库_1.litematic")

    assert parsed.meta.filename == "机械动力仓库_1.litematic"
    assert parsed.meta.schematic_name == "机械动力仓库_1"
    assert parsed.meta.author == "LiuYuShen_06"
    assert parsed.meta.region_count == 1
    assert parsed.meta.total_blocks == 592

    ids = {e.item_id for e in parsed.blocks}
    assert len(parsed.blocks) == 13
    assert "create:item_vault" in ids
    assert "minecraft:chest" in ids

    vault = next(e for e in parsed.blocks if e.item_id == "create:item_vault")
    assert vault.count == 486
    # 降序：最多的排第一
    assert parsed.blocks[0].item_id == "create:item_vault"

    # 该样本：箱子为空、Create vault 不走 vanilla Items → 容器组为空
    assert parsed.container_items == ()


def test_litematic_parser_skips_air_and_fluids():
    parsed = LitematicParser().parse(_FIXTURE.read_bytes(), "x.litematic")
    ids = {e.item_id for e in parsed.blocks}
    assert "minecraft:air" not in ids
    assert "minecraft:water" not in ids
    assert "minecraft:lava" not in ids


def test_litematic_parser_rejects_garbage_bytes():
    with pytest.raises(LitematicParseError):
        LitematicParser().parse(b"definitely not an NBT file", "bad.litematic")


# ---------- NbtParser ----------
def test_nbt_parser_parses_sample_structure():
    """NbtParser 解析 Create 蓝图：断言总数、首位方块、特定方块存在、容器为空、meta 字段。"""
    data = _NBT_FIXTURE.read_bytes()
    parsed = NbtParser().parse(data, "create_blueprint_sample.nbt")

    # meta 断言
    assert parsed.meta.filename == "create_blueprint_sample.nbt"
    assert parsed.meta.schematic_name == "create_blueprint_sample"
    assert parsed.meta.author == ""
    assert parsed.meta.total_blocks == 132
    assert parsed.meta.region_count == 1
    assert parsed.meta.total_volume == 490  # 14×7×5

    # blocks 断言（降序）
    assert len(parsed.blocks) == 15
    assert parsed.blocks[0].item_id == "create:andesite_casing"
    assert parsed.blocks[0].count == 38

    ids = {e.item_id for e in parsed.blocks}
    assert "create:belt" in ids
    assert "create:smart_chute" in ids

    belt = next(e for e in parsed.blocks if e.item_id == "create:belt")
    assert belt.count == 13
    chute = next(e for e in parsed.blocks if e.item_id == "create:smart_chute")
    assert chute.count == 12

    # Create 走自家存储，vanilla Items 为空
    assert parsed.container_items == ()


def test_nbt_parser_skips_air_and_fluids():
    """NbtParser 跳过空气与流体方块（minecraft:air / water / lava）。"""
    parsed = NbtParser().parse(_NBT_FIXTURE.read_bytes(), "x.nbt")
    ids = {e.item_id for e in parsed.blocks}
    assert "minecraft:air" not in ids
    assert "minecraft:water" not in ids
    assert "minecraft:lava" not in ids


def test_nbt_parser_rejects_garbage_bytes():
    """NbtParser 拒绝非 NBT 字节（抛 NbtParseError）。"""
    with pytest.raises(NbtParseError):
        NbtParser().parse(b"definitely not an NBT file", "bad.nbt")
