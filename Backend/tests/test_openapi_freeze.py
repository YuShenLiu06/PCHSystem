import json
from pathlib import Path

from fastapi.testclient import TestClient
from app.main import create_app

_FROZEN_SPEC = Path(__file__).resolve().parent.parent / "openapi.json"


def _top_level_diff(live: dict, frozen: dict) -> str:
    """汇总顶层差异 keys（仅 live / 仅 frozen / 值不同），供断言失败信息。"""
    only_live = sorted(set(live) - set(frozen))
    only_frozen = sorted(set(frozen) - set(live))
    changed = sorted(k for k in set(live) & set(frozen) if live[k] != frozen[k])
    return (
        f"仅 live 有 {only_live}；仅 frozen 有 {only_frozen}；"
        f"值不同 {changed}——请用 create_app().openapi() 再生成 openapi.json"
    )


def test_openapi_matches_frozen_artifact():
    # Arrange — 运行时 spec vs 仓库冻结工件（注意 info.version 取自已安装
    # 包元数据，改 pyproject version 后需 pip install -e . 并再生成工件）
    live = create_app().openapi()
    frozen = json.loads(_FROZEN_SPEC.read_text(encoding="utf-8"))

    # Assert — 全量相等（docstring/summary/版本任一漂移都会被抓住）
    assert live == frozen, _top_level_diff(live, frozen)


def test_security_schemes_present():
    spec = TestClient(create_app()).get("/openapi.json").json()
    schemes = spec["components"]["securitySchemes"]
    for name in ["X-Service-Token", "X-Player-UUID", "X-Source-Id", "Authorization"]:
        assert name in schemes, f"missing security scheme {name}"
        assert schemes[name]["type"] == "apiKey"


def test_docs_ui_available():
    response = TestClient(create_app()).get("/docs")
    assert response.status_code == 200


def test_authed_endpoint_declares_security():
    spec = TestClient(create_app()).get("/openapi.json").json()
    operation = spec["paths"]["/v1/scoring/credit"]["post"]
    assert operation["security"], "credit 应声明 security（service-token）"
    assert any("X-Service-Token" in req for req in operation["security"])
    assert operation.get("summary"), "credit 应有中文 summary"


def test_paths_present():
    paths = TestClient(create_app()).get("/openapi.json").json()["paths"]
    for p in [
        "/auth/token",
        "/auth/exchange",
        "/auth/refresh",
        "/me",
        "/me/last_sheet",
        "/healthz",
        "/sheets",
        "/sheets/export",
        "/sheets/{sheet_id}",
        "/sheets/{sheet_id}/rows",
        "/sheets/{sheet_id}/rows/{row_id}",
        "/sheets/{sheet_id}/rows/{row_id}/claim",
        "/sheets/{sheet_id}/rows/{row_id}/contribute",
        "/sheets/{sheet_id}/rows/{row_id}/delivery",
        "/sheets/{sheet_id}/rows/{row_id}/release",
        "/sheets/{sheet_id}/rows/{row_id}/reject",
        "/sheets/{sheet_id}/submit-batch",
        "/notifications/pending",
        "/notifications/ack",
        "/notifications/{notification_id}/read",
        "/parsing/batch",
        "/sheets/from-items",
        "/sheets/{sheet_id}/advance",
        "/sheets/{sheet_id}/archive",
        "/sheets/{sheet_id}/archive/assets/{filename}",
        # 施工进度上报层（迁移 0017）
        "/v1/construction/report",
        "/v1/construction/active-sheets",
        "/v1/construction/settings",
        "/v1/construction/mod-sources",
        "/v1/construction/mod-sources/{name}",
        "/v1/construction/source/switch-server",
        "/v1/construction/source/switch-self",
        "/v1/construction/source/me",
        "/v1/construction/{sheet_id}/progress",
        # 加入施工（迁移 0021）
        "/v1/construction/me/construction",
        "/v1/construction/me/join",
        "/v1/construction/me/switch",
        "/v1/construction/me/leave",
        "/v1/construction/active-by-uuids",
        # 积分层低层 API（迁移 0024）+ 管理员调控
        "/v1/scoring/credit",
        "/v1/scoring/debit",
        "/v1/scoring/ledger",
        "/v1/scoring/admin/adjust",
        "/v1/scoring/admin/players",
        "/v1/scoring/admin/balances",
    ]:
        assert p in paths, f"missing {p}"
