from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from services import doll_catalog, icon_cache

router = APIRouter(prefix="/api/doll", tags=["doll"])


# Sync on purpose: the first call loads the frame table and a miss downloads
# the PNG, so FastAPI runs it in the threadpool.
@router.get(
    "/{gender}/{slot}/{sequence}.png",
    responses={200: {"content": {"image/png": {}}}, 404: {"description": "No frame available"}},
)
def doll_frame(
    gender: Literal["m", "f"],
    slot: Literal["head", "cap"],
    sequence: int,
    color: int = Query(0, ge=0, le=10),
) -> Response:
    frame = doll_catalog.frame(gender, slot, sequence, color)
    data = icon_cache.get(frame.url) if frame else None
    if data is None:
        # No art in the DB, or not cached yet and the image host is unreachable.
        raise HTTPException(status_code=404, detail="no frame")
    # Frames are immutable within a DB build, so let WebView2 keep them.
    return Response(data, media_type="image/png", headers={"Cache-Control": "max-age=86400"})
