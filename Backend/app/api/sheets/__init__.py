"""sheets 包（原 sheets.py 1215 行拆分）。"""
from fastapi import APIRouter, status

router = APIRouter(prefix="/sheets", tags=["sheets"])

# 从 sheets_crud.py 导入端点
from app.api.sheets import sheets_crud
from app.schemas.sheet import SheetDetail, SheetSummary

router.post("", response_model=SheetDetail, status_code=status.HTTP_201_CREATED)(sheets_crud.create_sheet)
router.post("/from-items", response_model=SheetDetail, status_code=status.HTTP_201_CREATED)(sheets_crud.create_sheet_from_items)
router.get("", response_model=list[SheetSummary])(sheets_crud.list_sheets)
router.get("/export", response_class=sheets_crud.PlainTextResponse)(sheets_crud.export_all)
router.get("/{sheet_id}", response_model=SheetDetail)(sheets_crud.get_sheet)
router.patch("/{sheet_id}", response_model=SheetDetail)(sheets_crud.patch_sheet)
router.delete("/{sheet_id}", status_code=status.HTTP_204_NO_CONTENT)(sheets_crud.delete_sheet)

# 从 rows.py 导入端点
from app.api.sheets import rows
from app.schemas.sheet import RowDetail

router.put("/{sheet_id}/rows", response_model=RowDetail)(rows.upsert_row)
router.delete("/{sheet_id}/rows/{row_id}", status_code=status.HTTP_204_NO_CONTENT)(rows.delete_row)

# 从 collab.py 导入端点
from app.api.sheets import collab

router.post("/{sheet_id}/rows/{row_id}/claim", response_model=RowDetail)(collab.claim_row)
router.patch("/{sheet_id}/rows/{row_id}/delivery", response_model=RowDetail)(collab.set_row_delivery)
router.post("/{sheet_id}/rows/{row_id}/release", response_model=RowDetail)(collab.release_row)
router.post("/{sheet_id}/rows/{row_id}/reject", response_model=RowDetail)(collab.reject_row)
router.post("/{sheet_id}/rows/{row_id}/contribute", response_model=RowDetail)(collab.contribute_to_row)
router.patch("/{sheet_id}/rows/{row_id}/progress", response_model=RowDetail)(collab.set_row_progress)

# 从 lifecycle.py 导入端点
from app.api.sheets import lifecycle

router.post("/{sheet_id}/advance")(lifecycle.advance_sheet_phase)
router.get("/{sheet_id}/archive")(lifecycle.get_sheet_archive)
router.get("/{sheet_id}/archive/assets/{filename}")(lifecycle.get_sheet_archive_asset)
