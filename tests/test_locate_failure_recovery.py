"""Regression tests for the 2026-08-23 stale-pointer-chain incident.

The game updated, `PLAYER_HP_CHAIN_BASE` stopped resolving, and the app became
unrecoverable from the UI while the character sat plainly in memory (a scan
found it in 0.5s). Three separate defects stacked up; each gets a test here.
"""

import logging
import threading

import pytest

from services import diagnostics, logsetup
from services.char_session import CharSession
from services.diag_events import ErrorCode
from services.worker import ReaderWorker


@pytest.fixture(autouse=True)
def _bus(tmp_path, monkeypatch):
    root = logging.getLogger()
    saved = list(root.handlers)
    root.handlers.clear()
    logsetup._reset_for_tests()
    monkeypatch.setattr(logsetup, "candidate_paths", lambda: [tmp_path / "events.jsonl"])
    diagnostics.init(console=False)
    diagnostics.get_buffer().clear()
    yield
    root.handlers.clear()
    root.handlers.extend(saved)
    logsetup._reset_for_tests()


def _worker(errors: list) -> ReaderWorker:
    return ReaderWorker(
        pid=27160,
        on_state=lambda _s: None,
        on_stats=lambda _s: None,
        on_inventory=lambda _i: None,
        on_warehouse=lambda _w: None,
        on_error=lambda msg, **kw: errors.append((msg, kw)),
    )


# --------------------------------------------------------------------------
# Defect 1: exhausting the retries reported nothing a triager could use.
# --------------------------------------------------------------------------


def test_locate_exhaustion_reports_its_code_from_the_retry_loop(monkeypatch):
    """The report belongs to the loop that exhausts, not to each caller.

    The initial-locate caller in run() logged a bare warning with no code, no
    snapshot and no _cb_error -- so last_error stayed empty and the dashboard
    showed "lost" with no reason. That is the exact failure this whole
    diagnostics effort exists to make impossible, and it was the most common
    locate failure of the three.
    """
    import services.worker as W

    monkeypatch.setattr(W, "LOCATE_RETRY_INTERVAL", 0.0)
    monkeypatch.setattr(W, "LOCATE_MAX_RETRIES", 1)
    errors: list = []
    w = _worker(errors)
    monkeypatch.setattr(w, "_locate", lambda _pm, silent=False: None)

    assert w._locate_with_retries(pm=object(), waiting_state="WAITING") is None

    assert errors, "exhausting the retries must report through on_error"
    msg, kw = errors[0]
    assert kw["code"] == ErrorCode.E_LOCATE_EXHAUSTED
    assert kw["cat"] == "locate"
    assert kw["detail"]["chain_hp"] is not None or "chain_walk" in kw["detail"]


def test_stopping_is_not_reported_as_a_locate_failure(monkeypatch):
    """A deliberate shutdown must not manufacture an error event."""
    import services.worker as W

    monkeypatch.setattr(W, "LOCATE_RETRY_INTERVAL", 0.0)
    errors: list = []
    w = _worker(errors)
    monkeypatch.setattr(w, "_locate", lambda _pm, silent=False: None)
    w._stop_event.set()

    assert w._locate_with_retries(pm=object(), waiting_state="WAITING") is None
    assert errors == []


def test_snapshot_records_the_raw_chain_walk(monkeypatch):
    """`chain_hp: null` cannot tell "not logged in" from "constant is stale".

    Both look identical in the event, and the triage doc guessed wrong at the
    incident: it read null as "expected before login" while the real cause was
    a stale base. The raw deref sequence settles it -- a first hop reading 0x1
    is not a heap pointer, so the constant moved.
    """
    import reader

    class FakePm:
        def read_bytes(self, addr, size):
            if addr == reader.PLAYER_HP_CHAIN_BASE:
                return (1).to_bytes(4, "little")  # not a pointer
            raise OSError("unmapped")

    snap = diagnostics.snapshot_locate_failure(FakePm(), knowledge={})

    assert snap["chain_walk"], "the deref sequence must be recorded"
    assert snap["chain_walk"][0].startswith("0x1")


# --------------------------------------------------------------------------
# Defect 2: a session whose worker had exited 500'd on every reconnect.
# --------------------------------------------------------------------------


def test_start_rebuilds_a_worker_that_already_ran(monkeypatch):
    """`is_alive()` is False both before the first start and after the thread
    exits, so the guard let Thread.start() be called twice:

        RuntimeError: threads can only be started once

    Once the initial locate exhausted, every POST /connect on that pid returned
    500 forever -- there was no way back without restarting the app.
    """
    monkeypatch.setattr(ReaderWorker, "run", lambda _self: None)
    sess = CharSession(pid=1234)
    first = sess._worker

    sess.start(hp=111)
    first.join(timeout=5)
    assert not first.is_alive()

    sess.start(hp=222)  # must not raise

    assert sess._worker is not first, "a dead worker must be replaced"
    assert sess._worker._hp_value == 222
    assert sess._worker._cb_error == sess._on_error, "callbacks must survive the rebuild"


def test_start_is_idempotent_while_the_worker_is_alive(monkeypatch):
    """Rebuilding a *live* worker would orphan a running thread."""
    gate = threading.Event()
    monkeypatch.setattr(ReaderWorker, "run", lambda _self: gate.wait(5))
    sess = CharSession(pid=1234)
    first = sess._worker
    try:
        sess.start(hp=111)
        sess.start(hp=222)
        assert sess._worker is first
    finally:
        gate.set()
        first.join(timeout=5)


# --------------------------------------------------------------------------
# Defect 3: the one working recovery path was unreachable.
# --------------------------------------------------------------------------


def test_session_remembers_the_hp_it_was_started_with(monkeypatch):
    monkeypatch.setattr(ReaderWorker, "run", lambda _self: None)
    sess = CharSession(pid=1234)
    assert sess.last_hp is None
    sess.start(hp=48377)
    assert sess.last_hp == 48377


def test_rescan_reuses_the_last_manual_hp(monkeypatch):
    """With the chain stale, a manual HP is the only thing that can locate.

    rescan() built the replacement session with no hp, so the 重偵 button
    re-ran the same dead chain ten times and failed again -- for ever. Anyone
    who had already supplied a working HP silently lost it on the first retry.
    """
    import services.worker_manager as WM

    monkeypatch.setattr(ReaderWorker, "run", lambda _self: None)
    monkeypatch.setattr(WM, "find_tthol_processes", lambda: [{"pid": 4242}])

    wm = WM.WorkerManager()
    first = CharSession(pid=4242)
    first.start(hp=48377)
    wm._sessions[4242] = first

    assert wm.rescan(4242).ok is True

    replacement = wm._sessions[4242]
    assert replacement is not first
    assert replacement.last_hp == 48377, "the manual HP must survive a rescan"


# --------------------------------------------------------------------------
# Defect 4: the error never reached the UI for the only characters that had it.
# --------------------------------------------------------------------------


def test_placeholder_row_still_carries_the_last_error(monkeypatch):
    """row() returns None until a character has located at least once, and the
    placeholder that stands in for it dropped last_error on the floor.

    That is the whole affected population: a character which never located is
    exactly the one with an error to show. The dashboard could only ever
    display errors for characters that were already working.
    """
    import services.worker_manager as WM

    monkeypatch.setattr(ReaderWorker, "run", lambda _self: None)
    monkeypatch.setattr(WM, "find_tthol_processes", lambda: [{"pid": 4242}])

    wm = WM.WorkerManager()
    sess = CharSession(pid=4242)
    wm._sessions[4242] = sess
    sess._on_error("nope", cat="locate", code=ErrorCode.E_LOCATE_EXHAUSTED)

    assert sess.row() is None, "precondition: never located, so no real row"
    row = wm.world_snapshot().chars[0]
    assert row.last_error is not None, "the placeholder must carry the error"
    assert row.last_error.code == ErrorCode.E_LOCATE_EXHAUSTED
