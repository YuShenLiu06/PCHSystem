import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.deps import require_service_token


@pytest.fixture(autouse=True)
def _restore_service_token():
    """_app 会改 deps._settings 原对象的 token；逐测试还原初值，
    避免残留值污染后续测试文件（同 _svc_token 裸赋值泄漏根因）。"""
    import app.api.deps as deps

    original = deps._settings.mcdr_service_token
    yield
    deps._settings.mcdr_service_token = original


def _app(token: str) -> FastAPI:
    app = FastAPI()

    @app.get("/probe")
    async def probe(_=Depends(require_service_token)) -> dict:
        return {"ok": True}

    # 注入测试 token（改原对象属性，不替换 deps._settings 指针）
    import app.api.deps as deps
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
