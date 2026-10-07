import json
import logging
import os
import time

import pytest

from services import logsetup, run_log
from services.run_log import DayFileHandler, sweep


@pytest.fixture(autouse=True)
def _clean():
    run_log._reset_for_tests()
    yield
    run_log._reset_for_tests()


def _day(path, day, size=10):
    p = path / f"{day}.jsonl"
    p.write_text("x" * size, encoding="utf-8")
    return p


# ---- cleanup --------------------------------------------------------------


def test_sweep_drops_days_past_the_keep(tmp_path):
    old = _day(tmp_path, "2026-09-30")
    edge = _day(tmp_path, "2026-10-01")
    today = _day(tmp_path, "2026-10-07")
    other = tmp_path / "notes.jsonl"
    other.write_text("keep", encoding="utf-8")
    gone = sweep(tmp_path, today.name, "2026-10-07", keep_days=7, max_bytes=10**9)
    assert gone == [old]
    assert edge.exists() and today.exists() and other.exists()


def test_sweep_trims_the_oldest_to_the_size_cap(tmp_path):
    a = _day(tmp_path, "2026-10-04", 100)
    b = _day(tmp_path, "2026-10-05", 100)
    today = _day(tmp_path, "2026-10-07", 100)
    gone = sweep(tmp_path, today.name, "2026-10-07", keep_days=7, max_bytes=150)
    assert gone == [a, b]  # the oldest first, until under the cap
    assert today.exists()


def test_sweep_never_deletes_the_file_being_written(tmp_path):
    today = _day(tmp_path, "2026-10-07", 500)
    assert sweep(tmp_path, today.name, "2026-10-07", keep_days=7, max_bytes=10) == []
    assert today.exists()


def _record(msg, created):
    rec = logging.LogRecord("tthol.run", logging.INFO, __file__, 1, msg, None, None)
    rec.created = created
    rec.cat, rec.char_pid, rec.char_name, rec.detail = "tower", 11444, "醉不可射", {"floor": 50}
    return rec


def test_day_handler_writes_one_file_per_local_day_and_sweeps(tmp_path):
    stale = _day(tmp_path, "2020-01-01")
    h = DayFileHandler(tmp_path)
    t1 = time.mktime((2026, 10, 7, 23, 59, 0, 0, 0, -1))
    h.emit(_record("第 50 層（第 10 房）", t1))
    h.emit(_record("next day", t1 + 120))
    h.close()
    first = json.loads((tmp_path / "2026-10-07.jsonl").read_text(encoding="utf-8"))
    assert first["message"] == "第 50 層（第 10 房）"
    assert first["pid"] == 11444 and first["char"] == "醉不可射" and first["cat"] == "tower"
    assert first["detail"] == {"floor": 50}
    assert (tmp_path / "2026-10-08.jsonl").exists()
    assert not stale.exists()  # opening the day's file swept the folder


def test_setup_logging_puts_the_run_record_beside_events_and_out_of_it(tmp_path, monkeypatch):
    root = logging.getLogger()
    saved = list(root.handlers)
    root.handlers.clear()
    logsetup._reset_for_tests()
    try:
        from services import diagnostics

        monkeypatch.setattr(logsetup, "candidate_paths", lambda: [tmp_path / "events.jsonl"])
        diagnostics.init(console=False)
        run_log.note("guard", 1, "a", "喝 金創藥 ×1", hp=10)
        for h in run_log.log.handlers:
            h.flush()
        days = list((tmp_path / "runs").glob("*.jsonl"))
        assert len(days) == 1
        assert "金創藥" in days[0].read_text(encoding="utf-8")
        for h in root.handlers:
            h.flush()
        assert "金創藥" not in (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    finally:
        root.handlers.clear()
        root.handlers.extend(saved)
        logsetup._reset_for_tests()


# ---- HP trace ---------------------------------------------------------------


def _messages(caplog):
    return [
        (r.getMessage(), getattr(r, "detail", None))
        for r in caplog.records
        if r.name == "tthol.run"
    ]


def test_floor_stats_keep_the_low_and_the_largest_single_drop():
    run_log.vitals(7, 1000, 50, hp_max=1000, mp_max=100, now=0.0)
    run_log.vitals(7, 700, 50, now=0.1)  # -300
    run_log.vitals(7, 760, 50, now=0.2)
    run_log.vitals(7, 760, 50, now=0.3)  # unchanged: not a sample
    run_log.vitals(7, 400, 50, now=0.4)  # -360, low 40%
    s = run_log.floor_stats(7)
    assert (s.low_pct, s.max_drop, s.max_drop_pct) == (40, 360, 36)
    run_log.floor_reset(7)
    assert run_log.floor_stats(7) is None  # nothing seen on the new floor yet
    run_log.vitals(7, 300, 50, now=1.0)
    assert run_log.floor_stats(7).max_drop == 100  # the drop into the floor counts


def test_a_death_writes_the_trace_once(caplog):
    caplog.set_level(logging.INFO, logger="tthol.run")
    run_log.vitals(7, 21000, 50, hp_max=33085, mp_max=4781, now=100.0, src="packet")
    run_log.vitals(7, 0, 50, now=100.1, src="packet")
    run_log.check(7, "零錢包", now=101.0)
    assert _messages(caplog) == []  # a map load reads 0 for a moment
    run_log.check(7, "零錢包", now=103.2)
    run_log.check(7, "零錢包", now=104.0)
    deaths = [d for m, d in _messages(caplog) if m == "death"]
    assert len(deaths) == 1
    assert deaths[0]["hp_max"] == 33085
    assert [row[1:] for row in deaths[0]["trace"]] == [[21000, 50, "packet"], [0, 50, "packet"]]


def test_a_close_call_is_written_at_most_every_gap(caplog):
    caplog.set_level(logging.INFO, logger="tthol.run")
    run_log.vitals(7, 2000, 50, hp_max=10000, now=0.0)
    run_log.check(7, "a", now=0.0)
    run_log.vitals(7, 2100, 50, now=1.0)
    run_log.check(7, "a", now=1.0)
    run_log.vitals(7, 2200, 50, now=run_log.LOW_GAP + 1)
    run_log.check(7, "a", now=run_log.LOW_GAP + 1)
    lows = [m for m, _d in _messages(caplog) if m.startswith("low hp")]
    assert lows == ["low hp 20%", "low hp 22%"]


def test_the_trace_keeps_only_its_window(caplog):
    caplog.set_level(logging.INFO, logger="tthol.run")
    run_log.vitals(7, 9000, 0, hp_max=10000, now=0.0)
    run_log.vitals(7, 1000, 0, now=50.0)
    run_log.check(7, "a", now=50.0)
    ((_m, detail),) = _messages(caplog)
    assert [row[1] for row in detail["trace"]] == [1000]


def test_the_log_files_are_utf8(tmp_path):
    h = DayFileHandler(tmp_path)
    h.emit(_record("登塔結束：角色死亡", time.time()))
    h.close()
    (f,) = tmp_path.glob("*.jsonl")
    assert "角色死亡" in f.read_text(encoding="utf-8")
    assert os.path.getsize(f) > 0
