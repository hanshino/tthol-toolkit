"""Tests for system backup / restore (services/backup.py + SnapshotDB merge).

Pure Python: no pymem, no Qt. Exercises export_all / import_merge round-trip,
idempotency, non-destructive merge, account remap-by-name, format validation,
and transactional rollback.
"""

import json

import pytest

from services.backup import (
    BACKUP_FORMAT,
    BACKUP_VERSION,
    BackupFormatError,
    build_backup,
    parse_backup,
)
from services.snapshot_db import SnapshotDB


@pytest.fixture
def db(tmp_path):
    instance = SnapshotDB(str(tmp_path / "src.db"))
    yield instance
    instance.close()


@pytest.fixture
def fresh(tmp_path):
    instance = SnapshotDB(str(tmp_path / "dst.db"))
    yield instance
    instance.close()


def _seed(d):
    """Seed a db with two accounts, two characters, and a few snapshots."""
    a = d.create_account("帳號甲")
    b = d.create_account("帳號乙")
    d.save_snapshot("角色A", "inventory", [{"item_id": 1234, "qty": 5}])
    d.save_snapshot("角色A", "warehouse", [{"item_id": 1, "qty": 2}, {"item_id": 9, "qty": 1}])
    d.save_snapshot("角色B", "inventory", [{"item_id": 50, "qty": 9}])
    d.set_character_account("角色A", a)
    d.set_character_account("角色B", b)
    return d


# ---- build_backup / envelope -------------------------------------------------


def test_build_backup_envelope(db):
    _seed(db)
    payload = build_backup(db)
    assert payload["format"] == BACKUP_FORMAT
    assert payload["version"] == BACKUP_VERSION
    assert "exported_at" in payload
    assert "app_version" in payload
    assert sorted(payload["accounts"]) == ["帳號乙", "帳號甲"]
    assert len(payload["snapshots"]) == 3
    # character_accounts reference account by NAME, not id
    ca = {e["character"]: e["account"] for e in payload["character_accounts"]}
    assert ca == {"角色A": "帳號甲", "角色B": "帳號乙"}


# ---- round-trip --------------------------------------------------------------


def test_round_trip_equal(db, fresh):
    _seed(db)
    payload = build_backup(db)
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    data = parse_backup(raw)
    fresh.import_merge(data)
    # export_all is the canonical, id-independent state representation
    assert fresh.export_all() == db.export_all()


def test_round_trip_preserves_scanned_at(db, fresh):
    _seed(db)
    payload = build_backup(db)
    src_times = sorted(s["scanned_at"] for s in payload["snapshots"])
    fresh.import_merge(parse_backup(json.dumps(payload).encode("utf-8")))
    dst_times = sorted(s["scanned_at"] for s in fresh.export_all()["snapshots"])
    assert dst_times == src_times


# ---- idempotency -------------------------------------------------------------


def test_import_is_idempotent(db, fresh):
    _seed(db)
    data = parse_backup(json.dumps(build_backup(db)).encode("utf-8"))
    first = fresh.import_merge(data)
    assert first["snapshots_added"] == 3
    assert first["accounts_added"] == 2
    assert first["characters_assigned"] == 2

    second = fresh.import_merge(parse_backup(json.dumps(build_backup(db)).encode("utf-8")))
    assert second["snapshots_added"] == 0
    assert second["snapshots_skipped"] == 3
    assert second["accounts_added"] == 0
    assert second["characters_assigned"] == 0
    assert second["account_conflicts"] == 0


# ---- non-destructive merge + account conflict --------------------------------


def test_merge_keeps_existing_and_counts_conflict(db, fresh):
    _seed(db)
    # Pre-seed dst: 角色A already on a DIFFERENT account than the backup says.
    other = fresh.create_account("別的帳號")
    fresh.set_character_account("角色A", other)
    # And one identical snapshot already present (should be skipped).
    fresh.save_snapshot("角色B", "inventory", [{"item_id": 50, "qty": 9}])

    data = parse_backup(json.dumps(build_backup(db)).encode("utf-8"))
    summary = fresh.import_merge(data)

    # 角色A conflict (kept on 別的帳號), 角色B assigned to 帳號乙
    assert summary["account_conflicts"] == 1
    assert summary["characters_assigned"] == 1
    assert fresh.get_character_account("角色A")["name"] == "別的帳號"
    assert fresh.get_character_account("角色B")["name"] == "帳號乙"
    # 角色B inventory snapshot identical -> skipped; the other 2 added
    assert summary["snapshots_added"] == 2
    assert summary["snapshots_skipped"] == 1


def test_merge_same_account_is_noop_not_conflict(db, fresh):
    _seed(db)
    # Pre-assign 角色A to the SAME account name the backup uses.
    same = fresh.create_account("帳號甲")
    fresh.set_character_account("角色A", same)
    summary = fresh.import_merge(parse_backup(json.dumps(build_backup(db)).encode("utf-8")))
    assert summary["account_conflicts"] == 0
    # 角色A already assigned (same) -> not re-counted; 角色B newly assigned
    assert summary["characters_assigned"] == 1


# ---- account remap by name (ids differ between source and target) ------------


def test_account_resolves_by_name_not_id(db, fresh):
    _seed(db)
    # Force dst account ids to differ from src: create accounts in reverse order.
    fresh.create_account("帳號乙")  # id 1 in dst (was id 2 in src)
    fresh.create_account("帳號甲")  # id 2 in dst (was id 1 in src)
    fresh.import_merge(parse_backup(json.dumps(build_backup(db)).encode("utf-8")))
    # Assignment must resolve by name regardless of id mismatch.
    assert fresh.get_character_account("角色A")["name"] == "帳號甲"
    assert fresh.get_character_account("角色B")["name"] == "帳號乙"


# ---- format validation -------------------------------------------------------


def test_parse_rejects_non_json():
    with pytest.raises(BackupFormatError):
        parse_backup(b"\x00\x01 not json at all")


def test_parse_rejects_bad_format():
    raw = json.dumps({"format": "something-else", "version": 1}).encode("utf-8")
    with pytest.raises(BackupFormatError):
        parse_backup(raw)


def test_parse_rejects_bad_version():
    raw = json.dumps({"format": BACKUP_FORMAT, "version": 999}).encode("utf-8")
    with pytest.raises(BackupFormatError):
        parse_backup(raw)


def test_parse_rejects_non_object():
    with pytest.raises(BackupFormatError):
        parse_backup(json.dumps([1, 2, 3]).encode("utf-8"))


def test_parse_accepts_valid_envelope():
    raw = json.dumps({"format": BACKUP_FORMAT, "version": BACKUP_VERSION, "accounts": []}).encode(
        "utf-8"
    )
    data = parse_backup(raw)
    assert data["format"] == BACKUP_FORMAT


# ---- missing optional sections tolerated -------------------------------------


def test_import_tolerates_missing_sections(fresh):
    # Only a format/version envelope, no accounts/snapshots/character_accounts.
    data = {"format": BACKUP_FORMAT, "version": BACKUP_VERSION}
    summary = fresh.import_merge(data)
    assert summary["snapshots_added"] == 0
    assert summary["accounts_added"] == 0


# ---- transactional rollback --------------------------------------------------


def test_failed_import_rolls_back(fresh):
    # Pre-seed dst with known good state.
    fresh.create_account("既有帳號")
    fresh.save_snapshot("既有角色", "inventory", [{"item_id": 7, "qty": 1}])
    before = fresh.export_all()

    # Craft data whose snapshot is malformed (item missing 'item_id') so the
    # checksum step raises mid-transaction, AFTER a new account would insert.
    bad = {
        "format": BACKUP_FORMAT,
        "version": BACKUP_VERSION,
        "accounts": ["新帳號"],
        "character_accounts": [],
        "snapshots": [
            {
                "character": "壞角色",
                "source": "inventory",
                "scanned_at": "2026-06-10T09:00:00",
                "items": [{"qty": 5}],  # missing item_id -> KeyError in _canonical
            }
        ],
    }
    with pytest.raises(Exception):
        fresh.import_merge(bad)

    # Nothing partially applied: "新帳號" must not exist, state unchanged.
    after = fresh.export_all()
    assert after == before
    assert "新帳號" not in after["accounts"]


# ---- snapshot identity is the full (character, source, scanned_at, checksum) --


def test_same_checksum_different_scanned_at_both_kept(fresh):
    # Same character/source/items but different scanned_at -> two distinct rows
    # (history preserved). A char+source-only dedup would wrongly skip one.
    data = {
        "snapshots": [
            {
                "character": "C",
                "source": "inventory",
                "scanned_at": "2026-06-01T00:00:00",
                "items": [{"item_id": 1, "qty": 1}],
            },
            {
                "character": "C",
                "source": "inventory",
                "scanned_at": "2026-06-02T00:00:00",
                "items": [{"item_id": 1, "qty": 1}],
            },
        ],
    }
    summary = fresh.import_merge(data)
    assert summary["snapshots_added"] == 2
    assert summary["snapshots_skipped"] == 0


def test_same_scanned_at_different_checksum_both_kept(fresh):
    # Same character/source/scanned_at but different items (checksum) -> both kept.
    data = {
        "snapshots": [
            {
                "character": "C",
                "source": "inventory",
                "scanned_at": "2026-06-01T00:00:00",
                "items": [{"item_id": 1, "qty": 1}],
            },
            {
                "character": "C",
                "source": "inventory",
                "scanned_at": "2026-06-01T00:00:00",
                "items": [{"item_id": 2, "qty": 5}],
            },
        ],
    }
    summary = fresh.import_merge(data)
    assert summary["snapshots_added"] == 2
    assert summary["snapshots_skipped"] == 0


# ---- lazy account creation from a character_accounts entry -------------------


def test_lazy_account_creation_from_assignment(fresh):
    # A character_accounts entry references an account NOT in the top-level
    # accounts list (e.g. a hand-edited backup). The account is created lazily.
    data = {
        "accounts": [],
        "character_accounts": [{"character": "孤角", "account": "未列帳號"}],
        "snapshots": [],
    }
    summary = fresh.import_merge(data)
    assert summary["accounts_added"] == 1
    assert summary["characters_assigned"] == 1
    assert fresh.get_character_account("孤角")["name"] == "未列帳號"
