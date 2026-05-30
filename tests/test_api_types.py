from services.api_types import WorldSnapshot, CharacterRow, Vitals, Position, AutoClickStatus


def test_world_snapshot_round_trip():
    snap = WorldSnapshot(
        chars=[
            CharacterRow(
                pid=1234,
                name="無名",
                sect="少林",
                link="ok",
                level=20,
                vitals=Vitals(hp=100, hp_max=120, mp=80, mp_max=80, weight=50, weight_max=200),
                position=Position(map_name="洛陽", x=100, y=200),
                autoclick=AutoClickStatus(
                    running=False, started_at=None, runtime_seconds=None, last_click_at=None
                ),
            )
        ],
        server_ts=1714723200.0,
    )
    dumped = snap.model_dump()
    assert dumped["chars"][0]["link"] == "ok"
    assert dumped["chars"][0]["vitals"]["hp"] == 100
    WorldSnapshot.model_validate(dumped)  # round-trip
