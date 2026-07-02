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
    ]:
        assert p in paths, f"missing {p}"
