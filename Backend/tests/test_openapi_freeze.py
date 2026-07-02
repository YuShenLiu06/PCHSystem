from fastapi.testclient import TestClient
from app.main import create_app


def test_paths_present():
    paths = TestClient(create_app()).get("/openapi.json").json()["paths"]
    for p in [
        "/auth/token",
        "/auth/exchange",
        "/auth/refresh",
        "/me",
        "/healthz",
        "/sheets",
        "/sheets/export",
        "/sheets/{sheet_id}",
        "/sheets/{sheet_id}/rows",
        "/sheets/{sheet_id}/rows/{row_id}",
        "/sheets/{sheet_id}/rows/{row_id}/claim",
        "/sheets/{sheet_id}/rows/{row_id}/delivery",
        "/sheets/{sheet_id}/rows/{row_id}/release",
        "/sheets/{sheet_id}/rows/{row_id}/reject",
        "/notifications/pending",
        "/notifications/ack",
        "/notifications/{notification_id}/read",
        "/parsing/litematic",
        "/sheets/from-items",
    ]:
        assert p in paths, f"missing {p}"
