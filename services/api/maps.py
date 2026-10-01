from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from services import map_db, map_image_cache, map_regions
from services.api_types import (
    MapInfo,
    MapMonster,
    MapWarp,
    Minimap,
    MinimapNpc,
    MinimapRegion,
    MinimapExit,
    MinimapExitOption,
    MinimapSpawn,
    SpawnPoint,
    StageInfo,
)

router = APIRouter(prefix="/api/maps", tags=["maps"])

NEARBY_LIMIT = 20  # how many spawn points to return, sorted by distance


def _chebyshev(ax: int, ay: int, bx: int, by: int) -> int:
    return max(abs(ax - bx), abs(ay - by))


@router.get("/by-name/{name}", response_model=MapInfo)
async def map_by_name(name: str, x: int | None = None, y: int | None = None) -> MapInfo:
    stage = map_db.stage_by_name(name)
    if stage is None:
        raise HTTPException(status_code=404, detail=f"No stage named {name!r}")
    sid = stage["id"]
    monsters_raw = map_db.monsters_on_stage(sid)
    warps_raw = map_db.warps_from_stage(sid)
    spawns_raw = map_db.spawn_points(sid)

    nearby_models: list[SpawnPoint] = []
    if x is not None and y is not None and spawns_raw:
        with_dist = [(sp, _chebyshev(x, y, sp["x"], sp["y"])) for sp in spawns_raw]
        with_dist.sort(key=lambda t: t[1])
        nearby_models = [
            SpawnPoint(npc_id=sp["npc_id"], name=sp["name"], x=sp["x"], y=sp["y"], distance=d)
            for sp, d in with_dist[:NEARBY_LIMIT]
        ]

    return MapInfo(
        stage=StageInfo(stage_id=sid, name=stage["name"]),
        player_x=x,
        player_y=y,
        monsters=[MapMonster(**m) for m in monsters_raw],
        warps=[MapWarp(**w) for w in warps_raw],
        nearby=nearby_models,
    )


DEFAULT_TILE_PX = 40  # every map in the DB uses 40 px tiles


# Sync on purpose: several sqlite queries, so FastAPI runs it in the threadpool.
@router.get("/{stage_id}/minimap", response_model=Minimap)
def minimap(stage_id: int) -> Minimap:
    base = map_db.minimap_base(stage_id)
    if base is None:
        raise HTTPException(status_code=404, detail=f"No stage {stage_id}")
    tile_px = base["tile_px"] or DEFAULT_TILE_PX
    height_px = (base["h_tiles"] or 0) * tile_px
    # map_placements raw_y is an image row (top-down); flip into game coordinates.
    return Minimap(
        stage=StageInfo(stage_id=base["stage_id"], name=base["name"]),
        width_px=(base["w_tiles"] or 0) * tile_px,
        height_px=height_px,
        tile_px=tile_px,
        image_url=f"/api/maps/{stage_id}/image" if base["url"] else None,
        image_origin=base["origin"],
        exits=[
            MinimapExit(
                **{
                    **e,
                    "y": height_px - e["y"],
                    "options": [MinimapExitOption(**o) for o in e["options"]],
                }
            )
            for e in map_db.portal_exits(stage_id)
        ],
        npcs=[MinimapNpc(**{**n, "y": height_px - n["y"]}) for n in map_db.minimap_npcs(stage_id)],
        spawns=[
            MinimapSpawn(**{**sp, "y": height_px - sp["y"]})
            for sp in map_db.minimap_spawns(stage_id)
        ],
    )


@router.get("/{stage_id}/region", response_model=MinimapRegion | None)
def minimap_region(stage_id: int, x: int, y: int) -> MinimapRegion | None:
    """The walkable space holding game tile (x, y); null when none (e.g. no mask)."""
    base = map_db.minimap_base(stage_id)
    if base is None:
        raise HTTPException(status_code=404, detail=f"No stage {stage_id}")
    box = map_regions.region_box(stage_id, x, y, base["tile_px"] or DEFAULT_TILE_PX)
    return MinimapRegion(x0=box[0], y0=box[1], x1=box[2], y1=box[3]) if box else None


@router.get(
    "/{stage_id}/image",
    responses={200: {"content": {"image/webp": {}}}, 404: {"description": "No image available"}},
)
def minimap_image(
    stage_id: int,
    size: int = Query(
        map_image_cache.DEFAULT_MINIMAP_SIZE,
        description=f"Longest side in px, one of {map_image_cache.MINIMAP_SIZES}",
    ),
) -> Response:
    if size not in map_image_cache.MINIMAP_SIZES:
        raise HTTPException(
            status_code=422, detail=f"size must be one of {map_image_cache.MINIMAP_SIZES}"
        )
    base = map_db.minimap_base(stage_id)
    data = map_image_cache.get(base["url"], size) if base and base["url"] else None
    if data is None:
        # No image in the DB, or not cached yet and the image host is unreachable.
        raise HTTPException(status_code=404, detail="no map image")
    return Response(data, media_type="image/webp", headers={"Cache-Control": "max-age=86400"})
