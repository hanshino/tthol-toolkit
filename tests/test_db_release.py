"""Tests for scripts/db_release.py: pinning tthol.sqlite to a release asset."""

import hashlib
import io
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

    def opener(url, timeout):
        urls.append(url)
        return io.BytesIO(content)

    return opener, urls


def test_pull_downloads_when_missing(tmp_path):
    db = tmp_path / "tthol.sqlite"
    opener, urls = _serving(NEW)
    assert pull(db_path=db, lock_path=_lock(tmp_path), opener=opener) == "downloaded"
    assert db.read_bytes() == NEW
    assert urls == [
        "https://github.com/hanshino/tthol-toolkit/releases/download/db-2026-10-01/tthol.sqlite"
    ]
    assert not (tmp_path / "tthol.sqlite.download").exists()


def test_pull_skips_when_current(tmp_path):
    db = tmp_path / "tthol.sqlite"
    db.write_bytes(NEW)
    opener, urls = _serving(NEW)
    assert pull(db_path=db, lock_path=_lock(tmp_path), opener=opener) == "current"
    assert urls == []


def test_pull_refuses_to_overwrite_an_unpublished_db(tmp_path):
    db = tmp_path / "tthol.sqlite"
    db.write_bytes(OLD)
    opener, urls = _serving(NEW)
    with pytest.raises(DbReleaseError, match="differs"):
        pull(db_path=db, lock_path=_lock(tmp_path), opener=opener)
    assert db.read_bytes() == OLD
    assert urls == []
    pull(force=True, db_path=db, lock_path=_lock(tmp_path), opener=opener)
    assert db.read_bytes() == NEW


def test_corrupt_download_keeps_the_existing_file(tmp_path):
    db = tmp_path / "tthol.sqlite"
    db.write_bytes(OLD)
    opener, _ = _serving(b"truncated")
    with pytest.raises(DbReleaseError, match="sha256 mismatch"):
        pull(force=True, db_path=db, lock_path=_lock(tmp_path), opener=opener)
    assert db.read_bytes() == OLD
    assert not (tmp_path / "tthol.sqlite.download").exists()


def test_network_failure_is_reported(tmp_path):
    def opener(_url, timeout):
        raise OSError("HTTP Error 404: Not Found")

    with pytest.raises(DbReleaseError, match="release published"):
        pull(db_path=tmp_path / "tthol.sqlite", lock_path=_lock(tmp_path), opener=opener)


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
