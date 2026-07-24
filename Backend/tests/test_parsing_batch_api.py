"""POST /parsing/batch 端点测试（multipart 多文件上传 → 每文件独立预览）。

复用 test_parsing_api.py 的 _svc_token / _make_player / _auth 模式与真实 fixture。
重点：per-file error isolation（单文件失败不中断整批）、文件数/大小护栏。
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest

import app.api.deps as deps
import app.api.parsing as parsing_api
from app.core.config import get_settings
from tests.conftest import seed_player_with_account

_FIXTURE = Path(__file__).parent / "fixtures" / "机械动力仓库_1.litematic"
_NBT_FIXTURE = Path(__file__).parent / "fixtures" / "create_blueprint_sample.nbt"
# issue #8 复现样本（Create 蓝图 .nbt 经投影转出的 .litematic，缺可选 NBT 键）。
_BLUEPRINT_FIXTURE = Path(__file__).parent / "fixtures" / "1103.litematic"


@pytest.fixture(autouse=True)
def _svc_token(monkeypatch):
    deps._settings = get_settings()
    deps._settings.mcdr_service_token = "svc"


async def _make_player(name: str = "alice", role: str = "user") -> tuple[uuid.UUID, str]:
    return await seed_player_with_account(name=name, role=role)


def _auth(bearer: str) -> dict[str, str]:
    return {"Authorization": bearer}


def _part(name: str, data: bytes) -> tuple[str, tuple[str, bytes, str]]:
    """构造一个 multipart 'files' 重复键元组（key 恒为 'files'，与后端 list[UploadFile] 对齐）。"""
    return ("files", (name, data, "application/octet-stream"))


@pytest.mark.asyncio
async def test_batch_mixed_litematic_and_nbt_all_ok(client):
    """混合 .litematic + .nbt → 200，两文件均 status=ok，preview 含正确方块数。"""
    _, bearer = await _make_player("batch_ok")
    resp = await client.post(
        "/parsing/batch",
        files=[
            _part("机械动力仓库_1.litematic", _FIXTURE.read_bytes()),
            _part("create_blueprint_sample.nbt", _NBT_FIXTURE.read_bytes()),
        ],
        headers=_auth(bearer),
    )
    assert resp.status_code == 200, resp.text
    files = resp.json()["files"]
    assert len(files) == 2
    assert all(f["status"] == "ok" for f in files)
    by_name = {f["filename"]: f for f in files}
    assert by_name["机械动力仓库_1.litematic"]["preview"]["meta"]["total_blocks"] == 592
    assert by_name["create_blueprint_sample.nbt"]["preview"]["meta"]["total_blocks"] == 132

    # 译名 + 数量回归守卫（迁移自原单文件用例 test_parse_*_returns_translated_preview，
    # 防 LangJsonTranslator / lang JSON 改动回归中文词条而测试全绿）
    lit_blocks = by_name["机械动力仓库_1.litematic"]["preview"]["blocks"]
    assert len(lit_blocks) == 13
    vault = next(b for b in lit_blocks if b["item_id"] == "create:item_vault")
    assert vault["item_name"] == "物品保险库"  # Create 官方中文
    assert vault["count"] == 486
    assert by_name["机械动力仓库_1.litematic"]["preview"]["container_items"] == []
    assert by_name["机械动力仓库_1.litematic"]["preview"]["untranslated"] == []

    nbt_blocks = by_name["create_blueprint_sample.nbt"]["preview"]["blocks"]
    assert len(nbt_blocks) == 15
    casing = next(b for b in nbt_blocks if b["item_id"] == "create:andesite_casing")
    assert casing["item_name"] == "安山机壳"  # Create 6.0.8 官方中文
    assert casing["count"] == 38
    assert by_name["create_blueprint_sample.nbt"]["preview"]["container_items"] == []
    assert by_name["create_blueprint_sample.nbt"]["preview"]["untranslated"] == []


@pytest.mark.asyncio
async def test_batch_isolates_single_corrupt_file(client):
    """一个垃圾文件仅标 error，另一正常文件仍 ok——整批 200（非 422）。"""
    _, bearer = await _make_player("batch_iso")
    resp = await client.post(
        "/parsing/batch",
        files=[
            _part("机械动力仓库_1.litematic", _FIXTURE.read_bytes()),
            _part("bad.litematic", b"not nbt at all"),
        ],
        headers=_auth(bearer),
    )
    assert resp.status_code == 200, resp.text
    files = resp.json()["files"]
    by_name = {f["filename"]: f for f in files}
    assert by_name["机械动力仓库_1.litematic"]["status"] == "ok"
    assert by_name["bad.litematic"]["status"] == "error"
    assert "无法解析该投影文件" in by_name["bad.litematic"]["error"]
    # 不泄漏内部 NBT 键名（迁移自原单文件用例 test_parse_litematic_garbage_returns_friendly_message）
    assert "PendingBlockTicks" not in by_name["bad.litematic"]["error"]


@pytest.mark.asyncio
async def test_batch_isolates_corrupt_nbt_file(client):
    """垃圾 .nbt → 该项 status=error 且文案为蓝图友好串（覆盖 _friendly_parse_error 的 nbt 分支）。"""
    _, bearer = await _make_player("batch_iso_nbt")
    resp = await client.post(
        "/parsing/batch",
        files=[
            _part("create_blueprint_sample.nbt", _NBT_FIXTURE.read_bytes()),
            _part("bad.nbt", b"not nbt at all"),
        ],
        headers=_auth(bearer),
    )
    assert resp.status_code == 200, resp.text
    by_name = {f["filename"]: f for f in resp.json()["files"]}
    assert by_name["create_blueprint_sample.nbt"]["status"] == "ok"
    assert by_name["bad.nbt"]["status"] == "error"
    assert "无法解析该蓝图文件" in by_name["bad.nbt"]["error"]
    assert "PendingBlockTicks" not in by_name["bad.nbt"]["error"]


@pytest.mark.asyncio
async def test_batch_single_create_blueprint_litematic(client):
    """issue #8：Create 蓝图转出的 .litematic（缺可选 NBT 键）批量单文件可解析、Create 方块存活。

    单文件等价于批量 1 个文件——原 /parsing/litematic 单文件端点删除后，该能力由 /parsing/batch 承载。
    """
    _, bearer = await _make_player("batch_bp")
    resp = await client.post(
        "/parsing/batch",
        files=[_part("1103.litematic", _BLUEPRINT_FIXTURE.read_bytes())],
        headers=_auth(bearer),
    )
    assert resp.status_code == 200, resp.text
    f = resp.json()["files"][0]
    assert f["status"] == "ok"
    assert f["preview"]["meta"]["total_blocks"] == 121
    ids = {b["item_id"] for b in f["preview"]["blocks"]}
    assert "create:belt" in ids


@pytest.mark.asyncio
async def test_batch_requires_jwt(client):
    """无 JWT → 401。"""
    resp = await client.post(
        "/parsing/batch",
        files=[_part("x.litematic", b"junk")],
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_batch_rejects_empty(client):
    """未提供文件 → 422（FastAPI：files 为必填 form 字段，缺省即 field required）。"""
    _, bearer = await _make_player("batch_empty")
    resp = await client.post("/parsing/batch", files=[], headers=_auth(bearer))
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_batch_wrong_extension_per_file(client):
    """.zip 文件该项标 error「仅支持…」，整批仍 200。"""
    _, bearer = await _make_player("batch_ext")
    resp = await client.post(
        "/parsing/batch",
        files=[_part("archive.zip", b"pk\x03\x04")],
        headers=_auth(bearer),
    )
    assert resp.status_code == 200, resp.text
    f = resp.json()["files"][0]
    assert f["status"] == "error"
    assert "仅支持" in f["error"]


@pytest.mark.asyncio
async def test_batch_over_file_count_cap(client, monkeypatch):
    """超过 parsing_batch_max_files → 400（整请求）。"""
    monkeypatch.setattr(parsing_api._settings, "parsing_batch_max_files", 1)
    _, bearer = await _make_player("batch_cap_n")
    resp = await client.post(
        "/parsing/batch",
        files=[
            _part("a.litematic", _FIXTURE.read_bytes()),
            _part("b.litematic", _FIXTURE.read_bytes()),
        ],
        headers=_auth(bearer),
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_batch_oversize_single_file(client, monkeypatch):
    """单文件超大小上限 → 该项 error「文件过大」，整批 200（per-file，非 413）。"""
    monkeypatch.setattr(parsing_api._settings, "litematic_max_upload_bytes", 8)
    _, bearer = await _make_player("batch_cap_size")
    resp = await client.post(
        "/parsing/batch",
        files=[
            _part("机械动力仓库_1.litematic", _FIXTURE.read_bytes()),  # 远大于 8 字节
            _part("bad.litematic", b"not nbt at all"),  # 14 字节 > 8 也触发过大
        ],
        headers=_auth(bearer),
    )
    assert resp.status_code == 200, resp.text
    files = resp.json()["files"]
    assert all(f["status"] == "error" and f["error"] == "文件过大" for f in files)


@pytest.mark.asyncio
async def test_batch_total_size_cap(client, monkeypatch):
    """总字节超 parsing_batch_total_max_bytes → 413（整请求）。"""
    monkeypatch.setattr(parsing_api._settings, "parsing_batch_total_max_bytes", 8)
    _, bearer = await _make_player("batch_cap_total")
    resp = await client.post(
        "/parsing/batch",
        files=[
            _part("机械动力仓库_1.litematic", _FIXTURE.read_bytes()),
            _part("create_blueprint_sample.nbt", _NBT_FIXTURE.read_bytes()),
        ],
        headers=_auth(bearer),
    )
    assert resp.status_code == 413
