from fastapi import APIRouter, HTTPException

from services import map_db
from services.api_types import MapInfo, MapMonster, MapWarp, SpawnPoint, StageInfo

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
