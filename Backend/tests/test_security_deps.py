from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.deps import require_service_token
from app.core.config import get_settings


def _app(token: str) -> FastAPI:
    app = FastAPI()

    @app.get("/probe")
    async def probe(_=Depends(require_service_token)) -> dict:
        return {"ok": True}

    # 注入测试 token
    import app.api.deps as deps
    deps._settings = get_settings()
    deps._settings.mcdr_service_token = token
    return app


def test_service_token_missing_returns_401():
    client = TestClient(_app("svc"))
    assert client.get("/probe").status_code == 401


def test_service_token_wrong_returns_401():
    client = TestClient(_app("svc"))
    assert client.get("/probe", headers={"X-Service-Token": "bad"}).status_code == 401


def test_service_token_ok():
    client = TestClient(_app("svc"))
    resp = client.get("/probe", headers={"X-Service-Token": "svc"})
    assert resp.status_code == 200
