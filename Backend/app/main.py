from importlib.metadata import PackageNotFoundError, version as pkg_version

from fastapi import FastAPI

from app.api.auth import router as auth_router, top_router
from app.api.identity import (
    auth_router as identity_auth_router,
    bind_router,
    router as identity_router,
)
from app.api.notifications import router as notifications_router
from app.api.parsing import router as parsing_router
from app.api.sheets import router as sheets_router
from app.core.config import get_settings
from app.core.web_probe import probe_web


def _backend_version() -> str:
    """从已安装包元数据取版本（权威源 pyproject.toml 的 [project] version）。

    容器内 / `pip install -e .` 后均注入元数据；改 pyproject version 重装即同步，
    无需手动改本文件。拿不到时返回 `0.0.0+unknown` 而非抛错（/info / /docs 仍可用）。
    """
    try:
        return pkg_version("pchsystem-backend")
    except PackageNotFoundError:
        return "0.0.0+unknown"


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="HTCMC PCHSystem", version=_backend_version())

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        # 契约不变：compose healthcheck + install.sh/update.sh 的 wait_http_ok /healthz 依赖
        return {"status": "ok"}

    @app.get("/info")
    async def info() -> dict:
        # 公开无鉴权（同 /healthz）。供 pch_system 插件 on_load 自检嗅探后端可达性 +
        # 前端地址（web_base_url）+ 前端可达性（web_online）+ 前端版本（web_version）。
        # web_online/web_version 由后端探 web_probe_url/version.json（同 compose 网络探服务名）。
        web = await probe_web(settings.web_probe_url)
        return {
            "name": "HTCMC PCHSystem",
            "version": _backend_version(),
            "status": "ok",
            "web_base_url": settings.web_base_url,
            "web_online": web.online,
            "web_version": web.version,
        }

    # 挂载顺序：identity_auth_router (/auth/login) 先挂，auth_router (/auth/*) 后挂
    app.include_router(identity_auth_router)  # /auth/login
    app.include_router(auth_router)  # /auth/token, /auth/exchange, /auth/refresh
    app.include_router(identity_router)  # /web-accounts/*
    app.include_router(bind_router)  # /bind/*
    app.include_router(sheets_router)
    app.include_router(parsing_router)
    app.include_router(notifications_router)
    app.include_router(top_router)
    return app


app = create_app()
