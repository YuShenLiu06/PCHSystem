"""sheets 包（原 sheets.py 1215 行拆分）。

路由组装方式：parent ``router`` 不直接注册端点，对每个子模块 ``include_router``；
子模块装饰器（path / response_model / response_class / status_code）为权威定义。
前缀 ``/sheets`` 与 ``tags`` 在 include 处统一注入（DRY，子模块无需各自声明）。

``main.py`` 经 ``app.include_router(sheets_router)`` 挂载（不带 prefix）——前缀在本
文件 include 时已加到每条子路由，故 parent 自身无需 prefix。
"""
from fastapi import APIRouter

from app.api.sheets import collab, lifecycle, rows, sheets_crud

router = APIRouter()

router.include_router(sheets_crud.router, prefix="/sheets", tags=["sheets"])
router.include_router(rows.router, prefix="/sheets", tags=["sheets"])
router.include_router(collab.router, prefix="/sheets", tags=["sheets"])
router.include_router(lifecycle.router, prefix="/sheets", tags=["sheets"])
