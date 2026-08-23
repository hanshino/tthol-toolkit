import json
import os

import reader
from services.backup import APP_VERSION
import services.runtime_info as ri


def test_environment_header_has_the_fields_a_triage_needs():
    hdr = ri.environment_header()
    for key in (
        "app_version",
        "python",
        "os",
        "frozen",
        "exe",
        "knowledge_sha8",
        "knowledge_mtime",
        "items_rows",
        "static_base",
        "static_offsets",
        "player_hp_chain_base",
        "player_hp_chain_offsets",
    ):
        assert key in hdr, f"missing {key}"
    # Compared against the source constant, not a literal: a pinned version
    # string turns every release into a test failure, which is how four of
    # these broke at once on the 1.3.0 bump.
    assert hdr["app_version"] == APP_VERSION
    # The pointer-chain constants are what a game update invalidates, so assert
    # the header *reports reader.py's current values* rather than pinning a
    # literal. A pinned literal turns every legitimate chain re-derivation into
    # a test failure on the one day nobody has spare attention for it -- the
    # 2026-08-07 patch moved the base and broke exactly that assertion.
    assert hdr["player_hp_chain_base"] == hex(reader.PLAYER_HP_CHAIN_BASE)
    assert hdr["player_hp_chain_offsets"] == [hex(o) for o in reader.PLAYER_HP_CHAIN_OFFSETS]
    # Only the player HP chain exists in reader.py; the session static chain
    # (STATIC_BASE / STATIC_OFFSETS) was removed, so the header must report it
    # as absent rather than raise.
    assert hdr["static_base"] is None  # absent today; present again after a re-scan


def test_write_and_read_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(ri, "RUNTIME_DIR", tmp_path)
    ri.write_runtime_json(port=51234, events_path=tmp_path / "events.jsonl")

    info = ri.read_runtime_json()
    assert info is not None
    assert info["schema"] == 1
    assert info["port"] == 51234
    assert info["pid"] == os.getpid()
    assert info["events_path"].endswith("events.jsonl")
    assert info["app_version"] == APP_VERSION
    assert json.loads((tmp_path / "runtime.json").read_text(encoding="utf-8"))["port"] == 51234


def test_read_returns_none_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(ri, "RUNTIME_DIR", tmp_path)
    assert ri.read_runtime_json() is None


def test_stale_detected_by_dead_pid(tmp_path, monkeypatch):
    monkeypatch.setattr(ri, "RUNTIME_DIR", tmp_path)
    ri.write_runtime_json(port=1, events_path=tmp_path / "e.jsonl")
    live = ri.read_runtime_json()
    assert live is not None
    assert ri.is_stale(live) is False

    dead = dict(live, pid=2_147_483_600)  # a pid that cannot be running
    assert ri.is_stale(dead) is True


def test_clear_stamps_the_exit_instead_of_deleting(tmp_path, monkeypatch):
    # Deleting the pointer on exit made post-mortem triage impossible in the
    # most common case: the user closes the app and *then* reports the problem,
    # leaving events.jsonl on disk with nothing pointing at it.
    monkeypatch.setattr(ri, "RUNTIME_DIR", tmp_path)
    ri.write_runtime_json(port=1, events_path=tmp_path / "e.jsonl")
    assert ri.was_clean_exit(ri.read_runtime_json()) is False

    ri.clear_runtime_json()
    info = ri.read_runtime_json()
    assert info is not None, "the pointer must survive a clean exit"
    assert ri.was_clean_exit(info) is True
    assert info["events_path"].endswith("e.jsonl")

    ri.clear_runtime_json()  # idempotent, must not raise


def test_crash_is_distinguishable_from_a_clean_exit(tmp_path, monkeypatch):
    monkeypatch.setattr(ri, "RUNTIME_DIR", tmp_path)
    ri.write_runtime_json(port=1, events_path=tmp_path / "e.jsonl")
    crashed = dict(ri.read_runtime_json(), pid=2_147_483_600)
    assert ri.is_stale(crashed) is True
    assert ri.was_clean_exit(crashed) is False  # dead pid, no stamp -> crashed


def test_corrupt_runtime_json_reads_as_none(tmp_path, monkeypatch):
    monkeypatch.setattr(ri, "RUNTIME_DIR", tmp_path)
    (tmp_path / "runtime.json").write_text("{not json", encoding="utf-8")
    assert ri.read_runtime_json() is None
