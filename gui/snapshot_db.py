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
        db_path = path or str(DEFAULT_DB)
        self._con = sqlite3.connect(db_path)
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

        # Build item_id -> name map from tthol.sqlite
        name_map: dict[int, str] = {}
        if ITEM_NAME_DB.exists():
            try:
                with sqlite3.connect(str(ITEM_NAME_DB)) as name_con:
                    for r in name_con.execute("SELECT id, name FROM items"):
                        name_map[r[0]] = r[1]
            except sqlite3.OperationalError:
                pass

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
                        "account": acct,
                    }
                )

        # Dedup warehouse rows: same account -> keep only newest scanned_at
        # snapshot_rows is already MAX(id) per (character, source), so the
        # "newest" character within an account is the one with the largest scanned_at
        # Build: account -> character with the latest warehouse snapshot
        acct_warehouse_latest: dict[
            str, tuple[str, str]
        ] = {}  # account -> (character, scanned_at)
        for snap in snapshot_rows:
            if snap["source"] != "warehouse":
                continue
            acct = char_to_account.get(snap["character"])
            if acct is None:
                continue
            cur = acct_warehouse_latest.get(acct)
            if cur is None or snap["scanned_at"] > cur[1]:
                acct_warehouse_latest[acct] = (snap["character"], snap["scanned_at"])

        warehouse_winners: set[str] = {
            char for char, _ in acct_warehouse_latest.values()
        }

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
        self._con.execute(
            "DELETE FROM character_accounts WHERE character=?", (character,)
        )
        self._con.commit()

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
        rows = self._con.execute(
            "SELECT id, name FROM accounts ORDER BY name"
        ).fetchall()
        return [{"id": r["id"], "name": r["name"]} for r in rows]

    def create_account(self, name: str) -> int:
        """Create a new account, return its id. Returns existing id if name already exists."""
        try:
            cur = self._con.execute("INSERT INTO accounts (name) VALUES (?)", (name,))
            self._con.commit()
            return cur.lastrowid
        except sqlite3.IntegrityError:
            row = self._con.execute(
                "SELECT id FROM accounts WHERE name=?", (name,)
            ).fetchone()
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
        self._con.execute(
            "DELETE FROM character_accounts WHERE character=?", (character,)
        )
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
