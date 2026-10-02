from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from services import icon_cache, skill_catalog

router = APIRouter(prefix="/api/skills", tags=["skills"])


@router.get(
    "/{magic_id}/icon",
    responses={200: {"content": {"image/png": {}}}, 404: {"description": "No icon available"}},
)
def skill_icon(magic_id: int, level: int = Query(1, ge=1)) -> Response:
    url = skill_catalog.icon_url(magic_id, level)
    data = icon_cache.get(url) if url else None
    if data is None:
        # No icon in the DB, or not cached yet and the image host is unreachable.
        raise HTTPException(status_code=404, detail="no icon")
    # A skill's icon never changes within a DB build, so let WebView2 keep it.
    return Response(data, media_type="image/png", headers={"Cache-Control": "max-age=86400"})
