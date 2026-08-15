from fastapi.testclient import TestClient
from app.main import create_app


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
    ]:
        assert p in paths, f"missing {p}"
