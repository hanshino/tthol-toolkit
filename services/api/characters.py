import time

from fastapi import APIRouter, Request

from services._mock import mock_chars, mock_world
from services.api_types import (
    Character,
    CharacterDetail,
    ConnectRequest,
    ConnectResult,
    OkResponse,
    WorldSnapshot,
)

router = APIRouter(prefix="/api", tags=["characters"])


@router.get("/characters", response_model=list[Character])
async def list_characters(request: Request) -> list[Character]:
    wm = request.app.state.services.get("worker_manager")
    if wm is None:
        return mock_chars()
    return wm.list_characters()


@router.get("/world", response_model=WorldSnapshot)
async def world_snapshot(request: Request) -> WorldSnapshot:
    wm = request.app.state.services.get("worker_manager")
    if wm is None:
        snap = mock_world()
        snap.server_ts = time.time()
        return snap
    return wm.world_snapshot()


@router.get("/characters/{pid}", response_model=CharacterDetail)
async def character_detail(pid: int, request: Request) -> CharacterDetail:
    wm = request.app.state.services.get("worker_manager")
    if wm is None:
        from services.api_types import AutoClickStatus, CharacterStats, Position, Vitals

        return CharacterDetail(
            pid=pid,
            name="無塵",
            sect="少林",
            link="ok",
            stats=CharacterStats(
                level=20,
                waigong=100,
                neili=80,
                genggu=70,
                shenfa=60,
                jiqiao=50,
                xuanxue=40,
                wugong=200,
                wugong_base=180,
                neijing=150,
                fangyu=120,
                huji=90,
                mingzhong=85,
                shanduo=75,
            ),
            vitals=Vitals(hp=120, hp_max=150, mp=90, mp_max=100, weight=40, weight_max=200),
            position=Position(map_name="少林寺", x=100, y=200),
            autoclick=AutoClickStatus(running=False),
        )
    return wm.character_detail(pid)


@router.post("/characters/{pid}/connect", response_model=ConnectResult)
async def connect_char(pid: int, body: ConnectRequest, request: Request) -> ConnectResult:
    wm = request.app.state.services.get("worker_manager")
    if wm is None:
        return ConnectResult(ok=True, hp_addr=0xDEADBEEF)
    return wm.connect(pid, body)


@router.post("/characters/{pid}/disconnect", response_model=OkResponse)
async def disconnect_char(pid: int, request: Request) -> OkResponse:
    wm = request.app.state.services.get("worker_manager")
    if wm is None:
        return OkResponse(ok=True)
    wm.disconnect(pid)
    return OkResponse(ok=True)


@router.post("/characters/{pid}/relocate", response_model=ConnectResult)
async def relocate_char(pid: int, body: dict, request: Request) -> ConnectResult:
    wm = request.app.state.services.get("worker_manager")
    if wm is None:
        return ConnectResult(ok=True)
    return wm.relocate(pid, body.get("hp"))


@router.post("/characters/{pid}/focus", response_model=OkResponse)
async def focus_window(pid: int, request: Request) -> OkResponse:
    wm = request.app.state.services.get("worker_manager")
    if wm is None:
        return OkResponse(ok=True)
    wm.focus(pid)
    return OkResponse(ok=True)
