"""System backup / restore endpoints (/api/backup).

Pure-web download/upload: GET returns the versioned JSON as an attachment,
POST accepts a multipart file and merges it. No app.py / pywebview changes.
"""

import json
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, UploadFile
from fastapi.responses import Response

from services import backup as backup_service
from services.api_types import BackupImportResult

router = APIRouter(prefix="/api/backup", tags=["backup"])


@router.get("/export")
async def export_backup(request: Request) -> Response:
    db = request.app.state.services.get("snapshot_db")
    if db is None:
        raise HTTPException(status_code=503, detail="snapshot database unavailable")
    payload = backup_service.build_backup(db)
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"tthol-backup-{ts}.json"
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/import", response_model=BackupImportResult)
async def import_backup(request: Request, file: UploadFile) -> BackupImportResult:
    db = request.app.state.services.get("snapshot_db")
    if db is None:
        raise HTTPException(status_code=503, detail="snapshot database unavailable")
    raw = await file.read()
    try:
        data = backup_service.parse_backup(raw)
    except backup_service.BackupFormatError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    summary = db.import_merge(data)
    return BackupImportResult(**summary)
