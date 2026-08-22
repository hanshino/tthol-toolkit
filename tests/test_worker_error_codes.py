import logging

import pytest

from services import diagnostics, logsetup
from services.diag_events import ErrorCode
from services.worker import ReaderWorker, RelocateWindow


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


def test_inventory_not_found_carries_its_code_and_still_unblocks_the_request(monkeypatch):
    import services.worker as W

    errors: list = []
    inv: list = []
    w = _worker(errors)
    w._cb_inventory = inv.append
    monkeypatch.setattr(W, "locate_inventory", lambda _pm: None)

    w._do_inventory_scan(pm=object())

    assert errors and errors[0][1]["code"] == ErrorCode.E_INV_NOT_FOUND
    assert errors[0][1]["cat"] == "inventory"
    # The empty callback must still fire or the API request hangs to its timeout.
    assert inv == [[]]


def test_warehouse_not_found_carries_its_code(monkeypatch):
    import services.worker as W

    errors: list = []
    wh: list = []
    w = _worker(errors)
    w._cb_warehouse = wh.append
    monkeypatch.setattr(W, "locate_inventory", lambda _pm: None)
    monkeypatch.setattr(W, "locate_all_slot_arrays", lambda _pm: [])

    w._do_warehouse_scan(pm=object())

    assert errors and errors[0][1]["code"] == ErrorCode.E_WH_NOT_FOUND
    assert wh == [[]]


def test_scan_exception_reports_scan_failed(monkeypatch):
    import services.worker as W

    errors: list = []
    inv: list = []
    w = _worker(errors)
    w._cb_inventory = inv.append

    def boom(_pm):
        raise RuntimeError("read failed")

    monkeypatch.setattr(W, "locate_inventory", boom)
    w._do_inventory_scan(pm=object())

    assert errors[0][1]["code"] == ErrorCode.E_SCAN_FAILED
    assert inv == [[]]


def test_connect_failure_reports_proc_gone(monkeypatch):
    import services.worker as W

    errors: list = []
    w = _worker(errors)

    class FakePymem:
        def __init__(self, _pid):
            raise RuntimeError("no such process")

    monkeypatch.setattr(W.pymem, "Pymem", FakePymem)
    assert w._connect_process() is None
    assert errors[0][1]["code"] == ErrorCode.E_PROC_GONE


def test_relocate_window_demotes_repeats_then_summarises():
    win = RelocateWindow(window_seconds=60.0, threshold=2)

    assert win.should_log_at_info(now=0.0) is True
    assert win.should_log_at_info(now=5.0) is True
    assert win.should_log_at_info(now=10.0) is False  # third within the window
    assert win.should_log_at_info(now=20.0) is False

    summary = win.roll(now=61.0)
    assert summary == 4
    # A fresh window logs at INFO again.
    assert win.should_log_at_info(now=62.0) is True
    assert win.roll(now=62.0) is None  # nothing to summarise yet


def test_worker_binds_its_logger_to_the_pid():
    w = _worker([])
    w._log.info("hello", extra={"cat": "locate"})
    ev = diagnostics.get_buffer().query(cat="locate")[0]
    assert ev.pid == 27160
