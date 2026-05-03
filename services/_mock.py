"""Mock data for routers when no live services are wired (dev fallback)."""

from services.api_types import (
    Account,
    AutoClickStatus,
    Character,
    CharacterRow,
    Position,
    SnapshotRow,
    Vitals,
    WorldSnapshot,
)


def mock_chars() -> list[Character]:
    return [
        Character(pid=1001, name="無塵", sect="少林", level=20, link="ok"),
        Character(pid=1002, name="風清揚", sect="華山", level=25, link="ok"),
        Character(pid=1003, name="令狐沖", sect="華山", level=22, link="weak"),
    ]


def mock_world() -> WorldSnapshot:
    base = AutoClickStatus(running=False)
    return WorldSnapshot(
        server_ts=0.0,
        chars=[
            CharacterRow(
                pid=1001,
                name="無塵",
                sect="少林",
                link="ok",
                level=20,
                vitals=Vitals(hp=120, hp_max=150, mp=90, mp_max=100, weight=40, weight_max=200),
                position=Position(map_name="少林寺", x=100, y=200),
                autoclick=base,
            ),
            CharacterRow(
                pid=1002,
                name="風清揚",
                sect="華山",
                link="ok",
                level=25,
                vitals=Vitals(hp=180, hp_max=200, mp=150, mp_max=160, weight=80, weight_max=250),
                position=Position(map_name="華山絕頂", x=50, y=80),
                autoclick=base,
            ),
        ],
    )


def mock_snapshots() -> list[SnapshotRow]:
    return [
        SnapshotRow(
            snapshot_id=1,
            character_name="無塵",
            account_id=1,
            source="inventory",
            saved_at="2026-04-01T10:00:00",
            item_count=24,
        ),
        SnapshotRow(
            snapshot_id=2,
            character_name="風清揚",
            account_id=2,
            source="warehouse",
            saved_at="2026-04-02T11:00:00",
            item_count=42,
        ),
    ]


def mock_accounts() -> list[Account]:
    return [
        Account(account_id=1, name="主帳", character_count=3),
        Account(account_id=2, name="練功號", character_count=4),
    ]
