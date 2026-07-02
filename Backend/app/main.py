from fastapi import FastAPI

from app.api.auth import router as auth_router, top_router
from app.api.notifications import router as notifications_router
from app.api.sheets import router as sheets_router


def create_app() -> FastAPI:
    app = FastAPI(title="HTCMC PCHSystem", version="0.1.0")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(auth_router)
    app.include_router(sheets_router)
    app.include_router(notifications_router)
    app.include_router(top_router)
    return app


app = create_app()
