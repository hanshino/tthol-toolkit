"""
Snapshot database for tthol_inventory.db.

Schema:
    snapshots(id, character, source, scanned_at, items TEXT, checksum TEXT)

items is a JSON array sorted by item_id: [{"item_id": N, "qty": N}, ...]
checksum is SHA256 of the canonical items JSON string.
"""
import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path

ITEM_NAME_DB = Path(__file__).parent.parent / "tthol.sqlite"
DEFAULT_DB = Path(__file__).parent.parent / "tthol_inventory.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    character   TEXT NOT NULL,
    source      TEXT NOT NULL,
    scanned_at  TEXT NOT NULL,
    items       TEXT NOT NULL,
    checksum    TEXT NOT NULL
);
"""


def _canonical(items: list[dict]) -> str:
    """Return canonical JSON string for hashing (sorted by item_id)."""
    sorted_items = sorted(items, key=lambda x: x["item_id"])
    return json.dumps(sorted_items, separators=(",", ":"), ensure_ascii=False)


def _checksum(canonical: str) -> str:
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class SnapshotDB:
    def __init__(self, path: str | None = None):
        db_path = path or str(DEFAULT_DB)
        self._con = sqlite3.connect(db_path, check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._con.executescript(SCHEMA)
        self._con.commit()

    def save_snapshot(self, character: str, source: str, items: list[dict]) -> bool:
        """
        Save a snapshot. Returns True if saved, False if identical to last snapshot.
        items: list of {"item_id": int, "qty": int}
        """
        canonical = _canonical(items)
        chk = _checksum(canonical)

        # Dedup: check last snapshot for this character+source
        row = self._con.execute(
            "SELECT checksum FROM snapshots "
            "WHERE character=? AND source=? ORDER BY id DESC LIMIT 1",
            (character, source),
        ).fetchone()
        if row and row["checksum"] == chk:
            return False

        now = datetime.now().isoformat(timespec="seconds")
        self._con.execute(
            "INSERT INTO snapshots (character, source, scanned_at, items, checksum) "
            "VALUES (?, ?, ?, ?, ?)",
            (character, source, now, canonical, chk),
        )
        self._con.commit()
        return True

    def load_latest_snapshots(self) -> list[dict]:
        """
        Return rows for the latest snapshot per (character, source).
        Each row: {character, source, item_id, qty, name, scanned_at}
        Item names are resolved from tthol.sqlite.
        """
        snapshot_rows = self._con.execute(
            "SELECT id, character, source, scanned_at, items "
            "FROM snapshots "
            "WHERE id IN ("
            "  SELECT MAX(id) FROM snapshots GROUP BY character, source"
            ")"
        ).fetchall()

        # Build item_id -> name map from tthol.sqlite
        name_map: dict[int, str] = {}
        if ITEM_NAME_DB.exists():
            with sqlite3.connect(str(ITEM_NAME_DB)) as name_con:
                for r in name_con.execute("SELECT id, name FROM items"):
                    name_map[r[0]] = r[1]

        result = []
        for snap in snapshot_rows:
            items = json.loads(snap["items"])
            for item in items:
                result.append({
                    "character": snap["character"],
                    "source": snap["source"],
                    "scanned_at": snap["scanned_at"],
                    "item_id": item["item_id"],
                    "qty": item["qty"],
                    "name": name_map.get(item["item_id"], "???"),
                })
        return result
