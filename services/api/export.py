import time
from pathlib import Path

from fastapi import APIRouter, Request

from services.api_types import ExportCsvRequest, ExportCsvResult

router = APIRouter(prefix="/api/export", tags=["export"])


@router.post("/csv", response_model=ExportCsvResult)
async def export_csv(body: ExportCsvRequest, request: Request) -> ExportCsvResult:
    exporter = request.app.state.services.get("exporter")
    out_path = Path("exports") / f"tthol_{body.mode}_{int(time.time())}.csv"
    out_path.parent.mkdir(exist_ok=True)
    if exporter is None:
        out_path.write_text("character,item_id,name,quantity\n", encoding="utf-8")
        return ExportCsvResult(rows=0, path=str(out_path.resolve()))
    rows = exporter.export(mode=body.mode, out_path=out_path)
    return ExportCsvResult(rows=rows, path=str(out_path.resolve()))
