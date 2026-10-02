"""
Market survey database: player stall listings recorded while browsing stalls.

Schema:
    visits(id, seller, sign, stage_id, map, opened_at, recorded_at, fingerprint, items)
        One row per settled read of a stall. Local only; the raw log.
    listings(id, seller, item_id, price, price_kind, coins, attrs, count,
             stage_id, map, first_seen, last_seen, ended_at)
        One row per listing: (seller, item_id, price, attrs) while it stays up.
        count is the total qty across identical lots. Re-opening a stall only
        moves last_seen; a listing that disappears gets ended_at.

    excluded_sellers(seller), excluded_listings(listing_id)
        The player's own "不採計" marks.

A listing is flagged (kept, but left out of price stats and, later, uploads)
when it is a suspected bait price or the player excluded it.

Times are unix seconds. See docs/plans/2026-10-02-market-survey.md.
"""

import hashlib
import json
import os
import sqlite3
import statistics
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from services._paths import app_root, bundled

# Price conventions (user rules, 2026-10-02). The raw price is always stored;
# the kind is derived from it.
# - exactly 99,999,999: not a real ask, haggle by 飛鴿傳書
# - 8 digits, at least 4 leading 9s, then a non-zero tail: the tail is the ask
#   in 百萬官幣 (items.id 24086, worth 1,000,000 silver), e.g. 99,999,200 = 200
# - anything else, repdigits like 88,888,888 included, is a silver price
NEGOTIATE_PRICE = 99_999_999
COIN_MIN_LEADING_NINES = 4
COIN_SILVER = 1_000_000
# Bait prices: a price far under the item's value that cannot actually be paid
# (the seller keeps their own silver full) and is meant to start a haggle.
# Verified on 664 listings: under 1/10 of items.value caught exactly the 7
# listings of the two stalls whose signs said 鴿 / 飛鴿談, and no real cheap sale.
BAIT_RATIO = 0.1

ITEM_DB = bundled("tthol.sqlite")

SCHEMA = """
CREATE TABLE IF NOT EXISTS visits (
    id INTEGER PRIMARY KEY,
    seller TEXT NOT NULL,
    sign TEXT NOT NULL DEFAULT '',
    stage_id INTEGER,
    map TEXT NOT NULL DEFAULT '',
    opened_at REAL NOT NULL,
    recorded_at REAL NOT NULL,
    fingerprint TEXT NOT NULL,
    items TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS visits_seller ON visits (seller, recorded_at);
CREATE TABLE IF NOT EXISTS listings (
    id INTEGER PRIMARY KEY,
    seller TEXT NOT NULL,
    item_id INTEGER NOT NULL,
    price INTEGER NOT NULL,
    price_kind TEXT NOT NULL,
    coins INTEGER NOT NULL DEFAULT 0,
    attrs TEXT NOT NULL,
    count INTEGER NOT NULL,
    stage_id INTEGER,
    map TEXT NOT NULL DEFAULT '',
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL,
    ended_at REAL
);
CREATE INDEX IF NOT EXISTS listings_seller ON listings (seller, ended_at);
CREATE INDEX IF NOT EXISTS listings_item ON listings (item_id);
CREATE TABLE IF NOT EXISTS excluded_sellers (seller TEXT PRIMARY KEY, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS excluded_listings (listing_id INTEGER PRIMARY KEY, created_at REAL NOT NULL);
"""


def classify_price(price: int) -> tuple[str, int]:
    """(kind, coins): kind is 'negotiate', 'coin' or 'silver'; coins counts 百萬官幣 for 'coin'."""
    if price == NEGOTIATE_PRICE:
        return "negotiate", 0
    digits = str(price)
    tail = digits.lstrip("9")
    if (
        len(digits) == 8
        and len(digits) - len(tail) >= COIN_MIN_LEADING_NINES
        and int(tail or 0) > 0
    ):
        return "coin", int(tail)
    return "silver", 0


def silver_value(price: int) -> int | None:
    """The ask in silver for price statistics; None for a haggle-only listing."""
    kind, coins = classify_price(price)
    if kind == "negotiate":
        return None
    return coins * COIN_SILVER if kind == "coin" else price


_ITEM_VALUES: dict[int, int] | None = None


def item_values() -> dict[int, int]:
    """items.value by id from the bundled game DB, loaded once."""
    global _ITEM_VALUES
    if _ITEM_VALUES is None:
        try:
            con = sqlite3.connect(f"file:{ITEM_DB}?mode=ro", uri=True)
            try:
                _ITEM_VALUES = {r[0]: r[1] or 0 for r in con.execute("SELECT id, value FROM items")}
            finally:
                con.close()
        except sqlite3.Error:
            _ITEM_VALUES = {}
    return _ITEM_VALUES


def is_bait(item_id: int, price: int, values: dict[int, int] | None = None) -> bool:
    """A priced ask under BAIT_RATIO of the item's value."""
    value = (item_values() if values is None else values).get(item_id, 0)
    silver = silver_value(price)
    return bool(value) and silver is not None and silver < value * BAIT_RATIO


def attrs_key(item) -> str:
    """Canonical JSON of the attributes that tell two copies of an item apart."""
    return json.dumps(
        {"plus": item.plus, "stats": dict(item.stats), "inlays": list(item.inlays)},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def fingerprint(items) -> str:
    rows = sorted((i.item_id, i.price, i.qty, attrs_key(i)) for i in items)
    return hashlib.sha1(json.dumps(rows, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


@dataclass
class RecordResult:
    fingerprint: str
    new: int = 0
    unchanged: int = 0
    # (item_id, price, old count, new count)
    changed: list[tuple[int, int, int, int]] = field(default_factory=list)
    # (item_id, price)
    gone: list[tuple[int, int]] = field(default_factory=list)
    # One per listing in this read: item_id, price, attrs (JSON), count,
    # status ('new' | 'unchanged' | 'changed'), old_count (None unless changed)
    rows: list[dict] = field(default_factory=list)


def _default_db_path() -> Path:
    """Next to the snapshot DB under %APPDATA%\\御心鑒 (install root without APPDATA)."""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return app_root() / "market.db"
    target = Path(appdata) / "御心鑒" / "market.db"
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


class MarketDB:
    def __init__(self, path: str | None = None, values: dict[int, int] | None = None):
        db_path = path or str(_default_db_path())
        # Written by the market survey thread, read by API handler threads.
        self._con = sqlite3.connect(db_path, check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._values = values  # items.value override for tests; None = the game DB
        with self._lock:
            self._con.executescript(SCHEMA)
            self._con.commit()

    def close(self):
        with self._lock:
            self._con.close()

    def record(self, seller, sign, stage_id, map_name, opened_at, items, now=None) -> RecordResult:
        """Store a settled read of a stall and diff it into its listings."""
        now = time.time() if now is None else now
        fp = fingerprint(items)
        seen: dict[tuple[int, int, str], int] = {}
        for item in items:
            key = (item.item_id, item.price, attrs_key(item))
            seen[key] = seen.get(key, 0) + item.qty
        result = RecordResult(fingerprint=fp)
        with self._lock:
            con = self._con
            con.execute(
                "INSERT INTO visits (seller, sign, stage_id, map, opened_at, recorded_at, fingerprint, items)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (
                    seller,
                    sign or "",
                    stage_id,
                    map_name or "",
                    opened_at,
                    now,
                    fp,
                    json.dumps(
                        [[i.item_id, i.price, i.qty, attrs_key(i)] for i in items],
                        ensure_ascii=False,
                    ),
                ),
            )
            active = {
                (r["item_id"], r["price"], r["attrs"]): (r["id"], r["count"])
                for r in con.execute(
                    "SELECT id, item_id, price, attrs, count FROM listings WHERE seller=? AND ended_at IS NULL",
                    (seller,),
                )
            }
            for key, count in seen.items():
                if key in active:
                    lid, old = active.pop(key)
                    con.execute(
                        "UPDATE listings SET last_seen=?, count=?, stage_id=?, map=? WHERE id=?",
                        (now, count, stage_id, map_name or "", lid),
                    )
                    if count != old:
                        result.changed.append((key[0], key[1], old, count))
                        status = "changed"
                    else:
                        result.unchanged += 1
                        status, old = "unchanged", None
                else:
                    kind, coins = classify_price(key[1])
                    con.execute(
                        "INSERT INTO listings (seller, item_id, price, price_kind, coins, attrs, count,"
                        " stage_id, map, first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            seller,
                            key[0],
                            key[1],
                            kind,
                            coins,
                            key[2],
                            count,
                            stage_id,
                            map_name or "",
                            now,
                            now,
                        ),
                    )
                    result.new += 1
                    status, old = "new", None
                result.rows.append(
                    {
                        "item_id": key[0],
                        "price": key[1],
                        "attrs": key[2],
                        "count": count,
                        "status": status,
                        "old_count": old,
                    }
                )
            for key, (lid, _old) in active.items():
                con.execute("UPDATE listings SET ended_at=? WHERE id=?", (now, lid))
                result.gone.append((key[0], key[1]))
            con.commit()
        return result

    def last_recorded(self) -> dict[str, float]:
        """{seller: last time a stall read was recorded}."""
        with self._lock:
            rows = self._con.execute(
                "SELECT seller, MAX(recorded_at) AS t FROM visits GROUP BY seller"
            ).fetchall()
        return {r["seller"]: r["t"] for r in rows}

    def totals(self) -> dict[str, int | float | None]:
        with self._lock:
            row = self._con.execute(
                "SELECT COUNT(*) AS listings, COUNT(DISTINCT seller) AS stalls,"
                " SUM(price_kind='negotiate') AS negotiate, MAX(last_seen) AS last_seen FROM listings"
            ).fetchone()
            visits = self._con.execute("SELECT COUNT(*) FROM visits").fetchone()[0]
        return {
            "listings": row["listings"],
            "stalls": row["stalls"],
            "negotiate": row["negotiate"] or 0,
            "visits": visits,
            "last_seen": row["last_seen"],
        }

    def is_bait(self, item_id: int, price: int) -> bool:
        return is_bait(item_id, price, self._values)

    def _listing_rows(
        self, where: str, args: tuple
    ) -> tuple[list[sqlite3.Row], set[str], set[int]]:
        with self._lock:
            rows = self._con.execute(f"SELECT * FROM listings {where}", args).fetchall()
            sellers = {r[0] for r in self._con.execute("SELECT seller FROM excluded_sellers")}
            ids = {r[0] for r in self._con.execute("SELECT listing_id FROM excluded_listings")}
        return rows, sellers, ids

    def _flags(self, r: sqlite3.Row, sellers: set[str], ids: set[int]) -> tuple[bool, str | None]:
        """(suspect, excluded) where excluded is 'seller', 'listing' or None."""
        excluded = "seller" if r["seller"] in sellers else "listing" if r["id"] in ids else None
        return self.is_bait(r["item_id"], r["price"]), excluded

    def set_seller_excluded(self, seller: str, excluded: bool):
        with self._lock:
            if excluded:
                self._con.execute(
                    "INSERT OR IGNORE INTO excluded_sellers VALUES (?, ?)", (seller, time.time())
                )
            else:
                self._con.execute("DELETE FROM excluded_sellers WHERE seller=?", (seller,))
            self._con.commit()

    def set_listing_excluded(self, listing_id: int, excluded: bool) -> bool:
        """False when there is no such listing."""
        with self._lock:
            if (
                self._con.execute("SELECT 1 FROM listings WHERE id=?", (listing_id,)).fetchone()
                is None
            ):
                return False
            if excluded:
                self._con.execute(
                    "INSERT OR IGNORE INTO excluded_listings VALUES (?, ?)",
                    (listing_id, time.time()),
                )
            else:
                self._con.execute("DELETE FROM excluded_listings WHERE listing_id=?", (listing_id,))
            self._con.commit()
        return True

    def item_summaries(self, include_ended=False, include_negotiate=True) -> list[dict]:
        """Per item_id: listing / seller counts and silver-value stats, newest first."""
        rows, sellers, ids = self._listing_rows(
            "" if include_ended else "WHERE ended_at IS NULL", ()
        )
        by_item: dict[int, list[sqlite3.Row]] = {}
        for r in rows:
            if r["price_kind"] == "negotiate" and not include_negotiate:
                continue
            by_item.setdefault(r["item_id"], []).append(r)
        out = []
        for item_id, rs in by_item.items():
            flagged = {r["id"] for r in rs if any(self._flags(r, sellers, ids))}
            values = [
                v
                for v in (silver_value(r["price"]) for r in rs if r["id"] not in flagged)
                if v is not None
            ]
            out.append(
                {
                    "item_id": item_id,
                    "listings": len(rs),
                    "sellers": len({r["seller"] for r in rs}),
                    "negotiate": sum(r["price_kind"] == "negotiate" for r in rs),
                    "flagged": len(flagged),
                    "min": min(values) if values else None,
                    "median": int(statistics.median(values)) if values else None,
                    "max": max(values) if values else None,
                    "last_seen": max(r["last_seen"] for r in rs),
                }
            )
        out.sort(key=lambda s: s["last_seen"], reverse=True)
        return out

    def listings_for_item(self, item_id: int, include_ended=True) -> list[dict]:
        where = "WHERE item_id=?" + ("" if include_ended else " AND ended_at IS NULL")
        order = " ORDER BY ended_at IS NOT NULL, last_seen DESC"
        rows, sellers, ids = self._listing_rows(where + order, (item_id,))
        return [self._listing_dict(r, sellers, ids) for r in rows]

    def all_listings(self) -> list[dict]:
        rows, sellers, ids = self._listing_rows("ORDER BY last_seen DESC", ())
        return [self._listing_dict(r, sellers, ids) for r in rows]

    def _listing_dict(self, r: sqlite3.Row, sellers: set[str], ids: set[int]) -> dict:
        attrs = json.loads(r["attrs"])
        suspect, excluded = self._flags(r, sellers, ids)
        return {
            "id": r["id"],
            "suspect": suspect,
            "excluded": excluded,
            "seller": r["seller"],
            "item_id": r["item_id"],
            "price": r["price"],
            "price_kind": r["price_kind"],
            "coins": r["coins"],
            "silver": silver_value(r["price"]),
            "count": r["count"],
            "plus": attrs["plus"],
            "stats": attrs["stats"],
            "inlays": attrs["inlays"],
            "stage_id": r["stage_id"],
            "map": r["map"],
            "first_seen": r["first_seen"],
            "last_seen": r["last_seen"],
            "ended_at": r["ended_at"],
        }
