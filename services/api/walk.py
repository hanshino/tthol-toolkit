from fastapi import APIRouter, Request

from services.api_types import WalkRequest, WalkStatus

router = APIRouter(prefix="/api/characters/{pid}/walk", tags=["walk"])


@router.post("", response_model=WalkStatus)
def start(pid: int, body: WalkRequest, request: Request) -> WalkStatus:
    """Walk the character to tile (x, y) with background clicks."""
    mgr = request.app.state.services.get("walk_manager")
    if mgr is None:
        return WalkStatus(state="idle")
    return mgr.start(pid, (body.x, body.y))


@router.post("/stop", response_model=WalkStatus)
def stop(pid: int, request: Request) -> WalkStatus:
    mgr = request.app.state.services.get("walk_manager")
    if mgr is None:
        return WalkStatus(state="idle")
    mgr.stop(pid)
    return mgr.status(pid)


@router.get("", response_model=WalkStatus)
def status(pid: int, request: Request) -> WalkStatus:
    mgr = request.app.state.services.get("walk_manager")
    if mgr is None:
        return WalkStatus(state="idle")
    return mgr.status(pid)
