from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from services import icon_cache, item_catalog
from services.api_types import ItemMeta

router = APIRouter(prefix="/api/items", tags=["items"])

MAX_IDS = 1000  # a bag + pet bag + warehouse is ~200 distinct ids at most


# Sync on purpose: the first call loads the catalog (~0.3 s), so FastAPI runs it
# in the threadpool instead of blocking the event loop.
@router.get("", response_model=list[ItemMeta])
def items_by_id(ids: str = Query(..., description="Comma-separated item ids")) -> list[ItemMeta]:
    try:
        parsed = [int(part) for part in ids.split(",") if part.strip()]
    except ValueError:
        raise HTTPException(
            status_code=422, detail="ids must be comma-separated integers"
        ) from None
    if len(parsed) > MAX_IDS:
        raise HTTPException(status_code=422, detail=f"at most {MAX_IDS} ids per request")
    return item_catalog.lookup(parsed)


@router.get(
    "/{item_id}/icon",
    responses={200: {"content": {"image/png": {}}}, 404: {"description": "No icon available"}},
)
def item_icon(item_id: int) -> Response:
    url = item_catalog.icon_url(item_id)
    data = icon_cache.get(url) if url else None
    if data is None:
        # No icon in the DB, or not cached yet and the image host is unreachable.
        raise HTTPException(status_code=404, detail="no icon")
    # An item's icon never changes within a DB build, so let WebView2 keep it.
    return Response(data, media_type="image/png", headers={"Cache-Control": "max-age=86400"})
