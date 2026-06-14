"""FastAPI router tests for /api/backup (export + import).

Uses AsyncClient + ASGITransport with a real (temp-file) SnapshotDB injected
into app.state.services — no pymem, no running game.
"""

import json

import pytest
from httpx import ASGITransport, AsyncClient

from services.api import build_app
from services.backup import BACKUP_FORMAT, BACKUP_VERSION, build_backup
from services.snapshot_db import SnapshotDB


@pytest.fixture
async def seeded(tmp_path):
    db = SnapshotDB(str(tmp_path / "router.db"))
    acct = db.create_account("帳號甲")
    db.save_snapshot("角色A", "inventory", [{"item_id": 1234, "qty": 5}])
    db.set_character_account("角色A", acct)
    app = build_app(services={"snapshot_db": db})
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, db
    db.close()


@pytest.fixture
async def empty_client():
    app = build_app(services=None)  # no snapshot_db wired
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---- export ------------------------------------------------------------------


async def test_export_returns_attachment_json(seeded):
    client, _ = seeded
    resp = await client.get("/api/backup/export")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    cd = resp.headers["content-disposition"]
    assert "attachment" in cd
    assert "tthol-backup-" in cd and cd.endswith('.json"')
    body = resp.json()
    assert body["format"] == BACKUP_FORMAT
    assert body["version"] == BACKUP_VERSION
    assert body["accounts"] == ["帳號甲"]
    assert len(body["snapshots"]) == 1


async def test_export_503_when_db_absent(empty_client):
    resp = await empty_client.get("/api/backup/export")
    assert resp.status_code == 503


# ---- import ------------------------------------------------------------------


async def test_import_happy_path(seeded, tmp_path):
    client, _ = seeded
    # Build a backup from a separate source db, then upload it.
    src = SnapshotDB(str(tmp_path / "src.db"))
    src.create_account("帳號乙")
    src.save_snapshot("角色B", "warehouse", [{"item_id": 9, "qty": 2}])
    src.set_character_account("角色B", src.create_account("帳號乙"))
    payload = build_backup(src)
    src.close()
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    resp = await client.post(
        "/api/backup/import",
        files={"file": ("backup.json", raw, "application/json")},
    )
    assert resp.status_code == 200
    summary = resp.json()
    assert set(summary) == {
        "snapshots_added",
        "snapshots_skipped",
        "accounts_added",
        "characters_assigned",
        "account_conflicts",
    }
    assert summary["snapshots_added"] == 1
    assert summary["characters_assigned"] == 1


async def test_import_rejects_malformed(seeded):
    client, _ = seeded
    resp = await client.post(
        "/api/backup/import",
        files={"file": ("bad.json", b"\x00 not json", "application/json")},
    )
    assert resp.status_code == 400


async def test_import_rejects_bad_format(seeded):
    client, _ = seeded
    raw = json.dumps({"format": "nope", "version": 1}).encode("utf-8")
    resp = await client.post(
        "/api/backup/import",
        files={"file": ("bad.json", raw, "application/json")},
    )
    assert resp.status_code == 400


async def test_import_503_when_db_absent(empty_client):
    raw = json.dumps({"format": BACKUP_FORMAT, "version": BACKUP_VERSION}).encode("utf-8")
    resp = await empty_client.post(
        "/api/backup/import",
        files={"file": ("backup.json", raw, "application/json")},
    )
    assert resp.status_code == 503
