from fastapi import APIRouter, Request

from services._mock import mock_snapshots
from services.api_types import (
    OkResponse,
    SaveSnapshotRequest,
    SaveSnapshotResult,
    SnapshotRow,
)

router = APIRouter(prefix="/api", tags=["snapshots"])


@router.get("/snapshots", response_model=list[SnapshotRow])
async def list_snapshots(
    request: Request,
    account_id: int | None = None,
    character_name: str | None = None,
    source: str | None = None,
    days: int | None = None,
) -> list[SnapshotRow]:
    db = request.app.state.services.get("snapshot_db")
    if db is None:
        return mock_snapshots()
    return db.list_snapshots(
        account_id=account_id,
        character_name=character_name,
        source=source,
        days=days,
    )


@router.post("/snapshots", response_model=SaveSnapshotResult)
async def save_snapshot(body: SaveSnapshotRequest, request: Request) -> SaveSnapshotResult:
    wm = request.app.state.services.get("worker_manager")
    db = request.app.state.services.get("snapshot_db")
    if wm is None or db is None:
        return SaveSnapshotResult(saved=False)
    return wm.save_snapshot(body.pid, body.source)


@router.delete("/snapshots/{snapshot_id}", response_model=OkResponse)
async def delete_snapshot(snapshot_id: int, request: Request) -> OkResponse:
    db = request.app.state.services.get("snapshot_db")
    if db is None:
        return OkResponse(ok=True)
    db.delete_snapshot(snapshot_id)
    return OkResponse(ok=True)


@router.delete("/characters/by-name/{name}", response_model=OkResponse)
async def delete_character(name: str, request: Request) -> OkResponse:
    db = request.app.state.services.get("snapshot_db")
    if db is None:
        return OkResponse(ok=True)
    db.delete_character(name)
    return OkResponse(ok=True)
