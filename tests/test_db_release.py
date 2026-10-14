"""Tests for scripts/db_release.py: pinning tthol.sqlite to a release asset."""

import hashlib
import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import db_release  # noqa: E402
from db_release import DbReleaseError, next_tag, pull  # noqa: E402

NEW = b"SQLite format 3\x00 new db"
OLD = b"SQLite format 3\x00 old db"


def _lock(tmp_path, content=NEW, tag="db-2026-10-01"):
    lock = {
        "tag": tag,
        "asset": "tthol.sqlite",
        "sha256": hashlib.sha256(content).hexdigest(),
        "size": len(content),
    }
    path = tmp_path / "db.lock.json"
    path.write_text(json.dumps(lock), encoding="utf-8")
    return path


def _serving(content):
    urls = []

    def fetch(lock, dest):
        urls.append((lock["tag"], lock["asset"]))
        dest.write_bytes(content)

    return fetch, urls


def test_pull_downloads_when_missing(tmp_path):
    db = tmp_path / "tthol.sqlite"
    fetch, urls = _serving(NEW)
    assert pull(db_path=db, lock_path=_lock(tmp_path), fetch=fetch) == "downloaded"
    assert db.read_bytes() == NEW
    assert urls == [("db-2026-10-01", "tthol.sqlite")]
    assert not (tmp_path / "tthol.sqlite.download").exists()


def test_pull_skips_when_current(tmp_path):
    db = tmp_path / "tthol.sqlite"
    db.write_bytes(NEW)
    fetch, urls = _serving(NEW)
    assert pull(db_path=db, lock_path=_lock(tmp_path), fetch=fetch) == "current"
    assert urls == []


def test_pull_refuses_to_overwrite_an_unpublished_db(tmp_path):
    db = tmp_path / "tthol.sqlite"
    db.write_bytes(OLD)
    fetch, urls = _serving(NEW)
    with pytest.raises(DbReleaseError, match="differs"):
        pull(db_path=db, lock_path=_lock(tmp_path), fetch=fetch)
    assert db.read_bytes() == OLD
    assert urls == []
    pull(force=True, db_path=db, lock_path=_lock(tmp_path), fetch=fetch)
    assert db.read_bytes() == NEW


def test_corrupt_download_keeps_the_existing_file(tmp_path):
    db = tmp_path / "tthol.sqlite"
    db.write_bytes(OLD)
    fetch, _ = _serving(b"truncated")
    with pytest.raises(DbReleaseError, match="sha256 mismatch"):
        pull(force=True, db_path=db, lock_path=_lock(tmp_path), fetch=fetch)
    assert db.read_bytes() == OLD
    assert not (tmp_path / "tthol.sqlite.download").exists()


def test_download_failure_is_reported(tmp_path, monkeypatch):
    calls = []

    def gh(*args):
        calls.append(args)
        return db_release.subprocess.CompletedProcess(args, 1, "", "release not found")

    monkeypatch.setattr(db_release, "_gh", gh)
    with pytest.raises(DbReleaseError, match="release not found"):
        pull(db_path=tmp_path / "tthol.sqlite", lock_path=_lock(tmp_path))
    assert calls[0][:5] == ("release", "download", "db-2026-10-01", "--repo", db_release.REPO)
    assert not (tmp_path / "tthol.sqlite.download").exists()


def test_next_tag_appends_a_counter_for_same_day_releases():
    taken = {"db-2026-10-01", "db-2026-10-01-2"}
    assert next_tag(exists=taken.__contains__, today=date(2026, 10, 1)) == "db-2026-10-01-3"
    assert next_tag(exists=taken.__contains__, today=date(2026, 10, 2)) == "db-2026-10-02"


def test_committed_lock_is_well_formed():
    lock = db_release.read_lock()
    assert lock is not None, "db.lock.json must be committed"
    assert lock["asset"] == "tthol.sqlite"
    assert lock["tag"].startswith("db-")
    assert len(lock["sha256"]) == 64
