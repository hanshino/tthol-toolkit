"""
Snapshot database for tthol_inventory.db.

Schema:
    snapshots(id, character, source, scanned_at, items TEXT, checksum TEXT)
    accounts(id, name TEXT UNIQUE)
    character_accounts(character TEXT PK, account_id INTEGER NOT NULL → accounts.id)

items is a JSON array sorted by item_id: [{"item_id": N, "qty": N}, ...]
checksum is SHA256 of the canonical items JSON string.
"""

import hashlib
import json
import os
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from services._paths import app_root, bundled

ITEM_NAME_DB = bundled("tthol.sqlite")


def _default_db_path() -> Path:
    """User snapshots live under %APPDATA%\\御心鑒 so they survive folder moves
    and self-updates. On first run after upgrading, migrate the legacy
    tthol_inventory.db from the install root if present.
    """
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return app_root() / "tthol_inventory.db"
    target = Path(appdata) / "御心鑒" / "snapshots.db"
    target.parent.mkdir(parents=True, exist_ok=True)
    legacy = app_root() / "tthol_inventory.db"
    if legacy.exists() and not target.exists():
        try:
            shutil.move(str(legacy), str(target))
        except (OSError, shutil.Error):
            pass
    return target


_ITEM_MAPS_CACHE: tuple[dict[int, str], dict[int, str]] | None = None


def _load_item_maps() -> tuple[dict[int, str], dict[int, str]]:
    """Load (id->name, id->type) from tthol.sqlite once per process."""
    global _ITEM_MAPS_CACHE
    if _ITEM_MAPS_CACHE is not None:
        return _ITEM_MAPS_CACHE
    name_map: dict[int, str] = {}
    type_map: dict[int, str] = {}
    try:
        with sqlite3.connect(str(ITEM_NAME_DB)) as name_con:
            name_con.text_factory = lambda b: b.decode("utf-8", errors="replace")
            for r in name_con.execute("SELECT id, name, type FROM items"):
                name_map[r[0]] = r[1]
                if r[2]:
                    type_map[r[0]] = r[2]
    except sqlite3.OperationalError:
        pass
    _ITEM_MAPS_CACHE = (name_map, type_map)
    return _ITEM_MAPS_CACHE


SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    character   TEXT NOT NULL,
    source      TEXT NOT NULL,
    scanned_at  TEXT NOT NULL,
    items       TEXT NOT NULL,
    checksum    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS accounts (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name  TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS character_accounts (
    character  TEXT PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT
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
        db_path = path or str(_default_db_path())
        # check_same_thread=False: uvicorn dispatches handlers on worker threads.
        self._con = sqlite3.connect(db_path, check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._con.executescript(SCHEMA)
        self._con.commit()

    def close(self):
        self._con.close()

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
        Each row: {character, source, item_id, qty, name, scanned_at, account}
        Item names are resolved from tthol.sqlite.
        Warehouse rows from same-account characters are deduplicated: only the
        character with the latest scanned_at is kept.
        """
        snapshot_rows = self._con.execute(
            "SELECT id, character, source, scanned_at, items "
            "FROM snapshots "
            "WHERE id IN ("
            "  SELECT MAX(id) FROM snapshots GROUP BY character, source"
            ")"
        ).fetchall()

        name_map, type_map = _load_item_maps()

        # Load account assignments
        acct_rows = self._con.execute(
            "SELECT ca.character, a.name FROM character_accounts ca "
            "JOIN accounts a ON a.id=ca.account_id"
        ).fetchall()
        char_to_account: dict[str, str] = {r[0]: r[1] for r in acct_rows}

        result = []
        for snap in snapshot_rows:
            items = json.loads(snap["items"])
            acct = char_to_account.get(snap["character"])
            for item in items:
                result.append(
                    {
                        "character": snap["character"],
                        "source": snap["source"],
                        "scanned_at": snap["scanned_at"],
                        "item_id": item["item_id"],
                        "qty": item["qty"],
                        "name": name_map.get(item["item_id"], "???"),
                        "item_type": type_map.get(item["item_id"], ""),
                        "account": acct,
                    }
                )

        # Dedup warehouse rows: same account -> keep only newest scanned_at
        # snapshot_rows is already MAX(id) per (character, source), so the
        # "newest" character within an account is the one with the largest scanned_at
        # Build: account -> character with the latest warehouse snapshot
        acct_warehouse_latest: dict[str, tuple[str, str]] = {}  # account -> (character, scanned_at)
        for snap in snapshot_rows:
            if snap["source"] != "warehouse":
                continue
            acct = char_to_account.get(snap["character"])
            if acct is None:
                continue
            cur = acct_warehouse_latest.get(acct)
            if cur is None or snap["scanned_at"] > cur[1]:
                acct_warehouse_latest[acct] = (snap["character"], snap["scanned_at"])

        warehouse_winners: set[str] = {char for char, _ in acct_warehouse_latest.values()}

        filtered = []
        for r in result:
            if r["source"] == "warehouse" and r["account"] is not None:
                if r["character"] not in warehouse_winners:
                    continue
            filtered.append(r)

        return filtered

    def delete_snapshot(self, snapshot_id: int) -> None:
        """Delete a single snapshot row by id."""
        self._con.execute("DELETE FROM snapshots WHERE id=?", (snapshot_id,))
        self._con.commit()

    def delete_character(self, character: str) -> None:
        """Delete all snapshots and account assignment for a character."""
        self._con.execute("DELETE FROM snapshots WHERE character=?", (character,))
        self._con.execute("DELETE FROM character_accounts WHERE character=?", (character,))
        self._con.commit()

    def list_snapshots(
        self,
        account_id: int | None = None,
        character_name: str | None = None,
        source: str | None = None,
        days: int | None = None,
    ) -> list[dict]:
        """Return snapshot rows shaped for the API SnapshotRow model.

        Each dict: {snapshot_id, character_name, account_id, source, saved_at, item_count}
        """
        sql = (
            "SELECT s.id AS snapshot_id, s.character AS character_name, "
            "ca.account_id AS account_id, s.source AS source, "
            "s.scanned_at AS saved_at, s.items AS items "
            "FROM snapshots s "
            "LEFT JOIN character_accounts ca ON ca.character = s.character "
            "WHERE 1=1"
        )
        params: list = []
        if account_id is not None:
            sql += " AND ca.account_id = ?"
            params.append(account_id)
        if character_name is not None:
            sql += " AND s.character = ?"
            params.append(character_name)
        if source is not None:
            sql += " AND s.source = ?"
            params.append(source)
        if days is not None:
            sql += " AND s.scanned_at >= datetime('now', ?)"
            params.append(f"-{int(days)} days")
        sql += " ORDER BY s.id DESC"

        rows = self._con.execute(sql, params).fetchall()
        out = []
        for r in rows:
            try:
                item_count = len(json.loads(r["items"]))
            except (json.JSONDecodeError, TypeError):
                item_count = 0
            out.append(
                {
                    "snapshot_id": r["snapshot_id"],
                    "character_name": r["character_name"],
                    "account_id": r["account_id"],
                    "source": r["source"],
                    "saved_at": r["saved_at"],
                    "item_count": item_count,
                }
            )
        return out

    def list_all_snapshots(self, character: str) -> list[dict]:
        """
        Return all snapshots for a character, newest first.
        Each dict: {id, source, scanned_at, item_count}
        """
        rows = self._con.execute(
            "SELECT id, source, scanned_at, items FROM snapshots "
            "WHERE character=? ORDER BY id DESC",
            (character,),
        ).fetchall()
        result = []
        for r in rows:
            items = json.loads(r["items"])
            result.append(
                {
                    "id": r["id"],
                    "source": r["source"],
                    "scanned_at": r["scanned_at"],
                    "item_count": len(items),
                }
            )
        return result

    def list_accounts(self) -> list[dict]:
        """Return all accounts as list of {id, name}."""
        rows = self._con.execute("SELECT id, name FROM accounts ORDER BY name").fetchall()
        return [{"id": r["id"], "name": r["name"]} for r in rows]

    def account_character_counts(self) -> list[dict]:
        """Return [{account_id, count}] of characters per account."""
        rows = self._con.execute(
            "SELECT account_id, COUNT(*) AS c FROM character_accounts GROUP BY account_id"
        ).fetchall()
        return [{"account_id": r["account_id"], "count": r["c"]} for r in rows]

    def create_account(self, name: str) -> int:
        """Create a new account, return its id. Returns existing id if name already exists."""
        try:
            cur = self._con.execute("INSERT INTO accounts (name) VALUES (?)", (name,))
            self._con.commit()
            return cur.lastrowid
        except sqlite3.IntegrityError:
            row = self._con.execute("SELECT id FROM accounts WHERE name=?", (name,)).fetchone()
            return row["id"]

    def set_character_account(self, character: str, account_id: int) -> None:
        """Assign a character to an account (upsert)."""
        self._con.execute(
            "INSERT INTO character_accounts (character, account_id) VALUES (?, ?) "
            "ON CONFLICT(character) DO UPDATE SET account_id=excluded.account_id",
            (character, account_id),
        )
        self._con.commit()

    def get_character_account(self, character: str) -> dict | None:
        """Return {id, name} for the character's account, or None."""
        row = self._con.execute(
            "SELECT a.id, a.name FROM accounts a "
            "JOIN character_accounts ca ON ca.account_id=a.id "
            "WHERE ca.character=?",
            (character,),
        ).fetchone()
        return {"id": row["id"], "name": row["name"]} if row else None

    def remove_character_account(self, character: str) -> None:
        """Remove a character's account assignment."""
        self._con.execute("DELETE FROM character_accounts WHERE character=?", (character,))
        self._con.commit()

    def list_characters(self) -> list[dict]:
        """Return all characters that have at least one snapshot, with optional account info.
        Each dict: {character, account_id, account_name}
        account_id/account_name are None if not assigned.
        """
        rows = self._con.execute(
            "SELECT DISTINCT s.character, a.id AS account_id, a.name AS account_name "
            "FROM snapshots s "
            "LEFT JOIN character_accounts ca ON ca.character=s.character "
            "LEFT JOIN accounts a ON a.id=ca.account_id "
            "ORDER BY s.character"
        ).fetchall()
        return [
            {
                "character": r["character"],
                "account_id": r["account_id"],
                "account_name": r["account_name"],
            }
            for r in rows
        ]
