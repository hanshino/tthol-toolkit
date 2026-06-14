"""Tests for the treasury CSV report (GET /api/treasury/export.csv).

Verifies detail/summary headers + row counts, UTF-8 BOM (so Excel on a cp950
locale reads Chinese names), Chinese round-trip, and header-only empty output.
"""

import csv
import io
import sqlite3

import pytest
from httpx import ASGITransport, AsyncClient

from services.api import build_app
from services.snapshot_db import ITEM_NAME_DB, SnapshotDB

BOM = b"\xef\xbb\xbf"


def _real_items(n: int) -> list[tuple[int, str]]:
    """Pull a few real (id, name) rows from the bundled item DB so name
    resolution and Chinese round-trip are exercised against real data."""
    con = sqlite3.connect(str(ITEM_NAME_DB))
    con.text_factory = lambda b: b.decode("utf-8", errors="replace")
    rows = con.execute(
        "SELECT id, name FROM items WHERE name IS NOT NULL AND name != '' LIMIT ?",
        (n,),
    ).fetchall()
    con.close()
    return [(r[0], r[1]) for r in rows]


def _parse_csv(content: bytes) -> tuple[bytes, list[list[str]]]:
    """Return (raw 3-byte prefix, parsed rows) from response bytes."""
    text = content.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    return content[:3], rows


@pytest.fixture
async def client_with_data(tmp_path):
    db = SnapshotDB(str(tmp_path / "treasury.db"))
    items = _real_items(2)
    (x_id, x_name), (y_id, y_name) = items[0], items[1]
    db.save_snapshot(
        "角色A", "inventory", [{"item_id": x_id, "qty": 5}, {"item_id": y_id, "qty": 3}]
    )
    db.save_snapshot("角色B", "inventory", [{"item_id": x_id, "qty": 1}])
    db.save_snapshot("角色A", "warehouse", [{"item_id": x_id, "qty": 2}])
    app = build_app(services={"snapshot_db": db})
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, (x_id, x_name), (y_id, y_name)
    db.close()


@pytest.fixture
async def empty_client(tmp_path):
    db = SnapshotDB(str(tmp_path / "empty.db"))
    app = build_app(services={"snapshot_db": db})
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    db.close()


# ---- detail ------------------------------------------------------------------


async def test_detail_headers_and_rows(client_with_data):
    client, (x_id, _), (y_id, _) = client_with_data
    resp = await client.get("/api/treasury/export.csv?mode=detail")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]
    prefix, rows = _parse_csv(resp.content)
    assert prefix == BOM
    assert rows[0] == ["角色", "帳號", "來源", "道具ID", "道具名", "類型", "數量", "掃描時間"]
    # 角色A: x + y inventory, A warehouse x; 角色B: x inventory  => 4 data rows
    assert len(rows) - 1 == 4
    # 來源 localized
    sources = {r[2] for r in rows[1:]}
    assert sources == {"隨身", "庫房"}


async def test_detail_localizes_source(client_with_data):
    client, _, _ = client_with_data
    _, rows = _parse_csv((await client.get("/api/treasury/export.csv?mode=detail")).content)
    assert "inventory" not in {r[2] for r in rows[1:]}
    assert "warehouse" not in {r[2] for r in rows[1:]}


# ---- summary -----------------------------------------------------------------


async def test_summary_headers_and_rows(client_with_data):
    client, (x_id, x_name), (y_id, y_name) = client_with_data
    resp = await client.get("/api/treasury/export.csv?mode=summary")
    assert resp.status_code == 200
    prefix, rows = _parse_csv(resp.content)
    assert prefix == BOM
    assert rows[0] == ["道具ID", "道具名", "類型", "身上", "庫房", "合計", "持有角色數"]
    # 2 distinct items
    assert len(rows) - 1 == 2
    by_id = {int(r[0]): r for r in rows[1:]}
    # item X: 身上 5+1=6, 庫房 2, 合計 8, 持有角色數 = {A,B} = 2
    assert by_id[x_id][3] == "6"
    assert by_id[x_id][4] == "2"
    assert by_id[x_id][5] == "8"
    assert by_id[x_id][6] == "2"
    # item Y: only 角色A inventory
    assert by_id[y_id][6] == "1"


async def test_summary_is_default_for_unknown_mode(client_with_data):
    client, _, _ = client_with_data
    _, rows = _parse_csv((await client.get("/api/treasury/export.csv?mode=bogus")).content)
    assert rows[0] == ["道具ID", "道具名", "類型", "身上", "庫房", "合計", "持有角色數"]


async def test_chinese_names_round_trip(client_with_data):
    client, (x_id, x_name), _ = client_with_data
    text = (await client.get("/api/treasury/export.csv?mode=summary")).content.decode("utf-8-sig")
    # The real item name from the bundled DB must appear verbatim.
    assert x_name in text


# ---- empty -------------------------------------------------------------------


async def test_empty_detail_header_only(empty_client):
    prefix, rows = _parse_csv(
        (await empty_client.get("/api/treasury/export.csv?mode=detail")).content
    )
    assert prefix == BOM
    assert len(rows) == 1  # header only


async def test_empty_summary_header_only(empty_client):
    prefix, rows = _parse_csv(
        (await empty_client.get("/api/treasury/export.csv?mode=summary")).content
    )
    assert prefix == BOM
    assert len(rows) == 1  # header only


# ---- service absent (spec §7) ------------------------------------------------


@pytest.fixture
async def no_db_client():
    app = build_app(services=None)  # snapshot_db service not wired
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_export_503_when_db_absent(no_db_client):
    for mode in ("detail", "summary"):
        resp = await no_db_client.get(f"/api/treasury/export.csv?mode={mode}")
        assert resp.status_code == 503


# ---- CSV formula injection neutralized (CWE-1236) ----------------------------


async def test_detail_escapes_formula_in_names(tmp_path):
    db = SnapshotDB(str(tmp_path / "evil.db"))
    x_id = _real_items(1)[0][0]
    db.save_snapshot("=cmd|' /C calc'!A1", "inventory", [{"item_id": x_id, "qty": 1}])
    app = build_app(services={"snapshot_db": db})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        _, rows = _parse_csv((await ac.get("/api/treasury/export.csv?mode=detail")).content)
    db.close()
    # 角色 column (index 0) is neutralized with a leading quote.
    assert rows[1][0].startswith("'=")


# ---- same-account warehouse dedup feeds 庫房 + 持有角色數 ----------------------


async def test_summary_same_account_warehouse_dedup(tmp_path):
    db = SnapshotDB(str(tmp_path / "dedup.db"))
    x_id = _real_items(1)[0][0]
    # Two characters on the SAME account both hold the item in warehouse;
    # load_latest_snapshots keeps only the latest-scanned character per account.
    db.import_merge(
        {
            "accounts": ["甲帳"],
            "character_accounts": [
                {"character": "阿一", "account": "甲帳"},
                {"character": "阿二", "account": "甲帳"},
            ],
            "snapshots": [
                {
                    "character": "阿一",
                    "source": "warehouse",
                    "scanned_at": "2026-06-01T00:00:00",
                    "items": [{"item_id": x_id, "qty": 10}],
                },
                {
                    "character": "阿二",
                    "source": "warehouse",
                    "scanned_at": "2026-06-02T00:00:00",
                    "items": [{"item_id": x_id, "qty": 99}],
                },
            ],
        }
    )
    app = build_app(services={"snapshot_db": db})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        _, rows = _parse_csv((await ac.get("/api/treasury/export.csv?mode=summary")).content)
    db.close()
    by_id = {int(r[0]): r for r in rows[1:]}
    # Only the latest-scanned character (阿二) survives the per-account dedup:
    assert by_id[x_id][4] == "99"  # 庫房 = winner only, not 10+99
    assert by_id[x_id][6] == "1"  # 持有角色數 = 1 (deduped)
