import json
import sys

import pytest

from services.api_types import LoginEntryIn
from services.backup import build_backup
from services.login_store import LoginStore
from services.snapshot_db import SnapshotDB


def fake_crypto():
    return (lambda s: b"enc:" + s.encode()), (lambda b: b[4:].decode())


def make():
    db = SnapshotDB(":memory:")
    enc, dec = fake_crypto()
    return db, LoginStore(db, protect=enc, unprotect=dec)


def entry(character="阿克婭", username="acct1", **kw):
    return LoginEntryIn(character=character, username=username, server="飛雁山莊(花)", **kw)


def test_saved_entry_reports_only_whether_secrets_are_set():
    _db, store = make()
    out = store.save(entry(password="pw", protect="pp"))
    assert out.has_password and out.has_protect
    assert "pw" not in out.model_dump_json() and "pp" not in out.model_dump_json()
    s = store.secrets("阿克婭")
    assert (s.username, s.password, s.protect) == ("acct1", "pw", "pp")


def test_none_keeps_and_empty_clears():
    _db, store = make()
    store.save(entry(password="pw", protect="pp"))
    store.save(entry(enabled=False))  # password / protect None: kept
    assert store.secrets("阿克婭").protect == "pp"
    out = store.save(entry(protect=""))
    assert not out.has_protect and store.secrets("阿克婭").protect is None
    assert store.get("阿克婭").enabled  # this save has the default enabled=True


def test_secrets_belong_to_the_account():
    _db, store = make()
    store.save(entry(password="pw", protect="pp"))
    second = store.save(entry(character="債務居士"))  # same account, no secrets typed
    assert second.has_password and store.secrets("債務居士").password == "pw"
    store.save(entry(character="債務居士", password="new"))
    assert store.secrets("阿克婭").password == "new"  # both rows of the account


def test_moving_a_row_to_another_account_does_not_keep_the_old_secrets():
    _db, store = make()
    store.save(entry(password="pw"))
    out = store.save(entry(username="acct2"))
    assert not out.has_password and store.secrets("阿克婭") is None


def test_delete():
    _db, store = make()
    store.save(entry(password="pw"))
    assert store.delete("阿克婭") and store.list() == []


def test_backup_never_carries_login_rows():
    db, store = make()
    store.save(entry(password="secret-pw", protect="secret-pp"))
    text = json.dumps(build_backup(db), ensure_ascii=False)
    assert "acct1" not in text and "secret" not in text and "enc:" not in text


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows only")
def test_dpapi_round_trip():
    from services.secret_box import protect, unprotect

    blob = protect("測試pw123")
    assert b"pw123" not in blob and unprotect(blob) == "測試pw123"


# ---- export / import (another computer) ---------------------------------------


def test_export_import_round_trip_to_another_store():
    from services.login_transfer import open_sealed, seal

    _db, here = make()
    here.save(entry(password="pw", protect="pp"))
    here.save(entry(character="債務居士", username="acct9", password="pw9"))
    raw = seal(here.export_rows(), "a long passphrase")
    assert b"pw9" not in raw and "債務居士".encode() not in raw  # the payload is sealed

    _db2, there = make()
    counts = there.import_rows(open_sealed(raw, "a long passphrase"), overwrite=False)
    assert counts == {"added": 2, "updated": 0, "skipped": 0, "settings": 0}
    assert there.secrets("阿克婭").protect == "pp" and there.secrets("債務居士").password == "pw9"


def test_import_keeps_local_rows_unless_overwrite():
    from services.login_transfer import open_sealed, seal

    _db, here = make()
    here.save(entry(password="new"))
    raw = seal(here.export_rows(), "a long passphrase")
    _db2, there = make()
    there.save(entry(password="old"))
    assert there.import_rows(open_sealed(raw, "a long passphrase"), overwrite=False)["skipped"] == 1
    assert there.secrets("阿克婭").password == "old"
    assert there.import_rows(open_sealed(raw, "a long passphrase"), overwrite=True)["updated"] == 1
    assert there.secrets("阿克婭").password == "new"


def test_wrong_passphrase_tampering_and_short_passphrase():
    from services.login_transfer import TransferError, open_sealed, seal

    raw = seal(
        [{"character": "甲", "username": "u", "server": "s", "password": "p"}], "a long passphrase"
    )
    with pytest.raises(TransferError, match="passphrase"):
        open_sealed(raw, "another passphrase")
    body = json.loads(raw)
    body["n"] = 2**10  # a weakened header must not open with the right passphrase
    with pytest.raises(TransferError):
        open_sealed(json.dumps(body), "a long passphrase")
    with pytest.raises(TransferError, match="not a login export"):
        open_sealed(b'{"format": "something else"}', "a long passphrase")
    with pytest.raises(TransferError, match="short"):
        seal([], "short")


def test_export_carries_character_settings_but_not_tower_progress():
    from services.login_transfer import open_sealed, seal

    db, here = make()
    here.save(entry(password="pw"))
    db.set_setting("阿克婭", "daily.queue", {"tasks": ["tower"]})
    db.set_setting("阿克婭", "tower", {"stop_floor": 40})
    db.set_setting("阿克婭", "tower.record", {"date": "2026-10-07"})
    raw = seal(here.export_rows(), "a long passphrase")

    db2, there = make()
    counts = there.import_rows(open_sealed(raw, "a long passphrase"), overwrite=False)
    assert counts["added"] == 1 and counts["settings"] == 1
    assert db2.get_setting("阿克婭", "daily.queue") == {"tasks": ["tower"]}
    assert db2.get_setting("阿克婭", "tower") == {"stop_floor": 40}
    assert db2.get_setting("阿克婭", "tower.record") is None


def test_import_fills_missing_settings_and_replaces_only_with_overwrite():
    from services.login_transfer import open_sealed, seal

    db, here = make()
    here.save(entry(password="pw"))
    db.set_setting("阿克婭", "tower", {"stop_floor": 40})
    db.set_setting("阿克婭", "combat", {"skill": 1})
    raw = seal(here.export_rows(), "a long passphrase")

    # Already listed here (an older import without settings) and has its own tower.
    db2, there = make()
    there.save(entry(password="pw"))
    db2.set_setting("阿克婭", "tower", {"stop_floor": 20})
    counts = there.import_rows(open_sealed(raw, "a long passphrase"), overwrite=False)
    assert counts["skipped"] == 1 and counts["settings"] == 1
    assert db2.get_setting("阿克婭", "tower") == {"stop_floor": 20}  # kept
    assert db2.get_setting("阿克婭", "combat") == {"skill": 1}  # filled in
    there.import_rows(open_sealed(raw, "a long passphrase"), overwrite=True)
    assert db2.get_setting("阿克婭", "tower") == {"stop_floor": 40}


def test_transfer_sections_match_the_stores():
    from services import daily, family, guard, item_rules, tower_run
    from services.login_store import TRANSFER_SECTIONS

    assert set(TRANSFER_SECTIONS) == {
        daily.QUEUE_SECTION,
        tower_run.TOWER_SECTION,
        tower_run.COMBAT_SECTION,
        guard.POTION_SECTION,
        guard.BUFF_SECTION,
        item_rules.ITEMS_SECTION,
        family.SECTION,
    }
    assert tower_run.RECORD_SECTION not in TRANSFER_SECTIONS
