import logging

import pytest

from services import logsetup
from services.diag_buffer import DiagnosticsBuffer
from services.backup import APP_VERSION


@pytest.fixture(autouse=True)
def _clean_root():
    root = logging.getLogger()
    saved = list(root.handlers)
    root.handlers.clear()
    logsetup._reset_for_tests()
    yield
    root.handlers.clear()
    root.handlers.extend(saved)
    logsetup._reset_for_tests()


def test_uses_the_first_writable_candidate(tmp_path, monkeypatch):
    good = tmp_path / "second" / "events.jsonl"
    monkeypatch.setattr(
        logsetup,
        "candidate_paths",
        lambda: [tmp_path / "nope" / "\0bad" / "events.jsonl", good],
    )
    used = logsetup.setup_logging(DiagnosticsBuffer(), console=False)
    assert used == good
    assert good.exists()


def test_all_tiers_failing_still_feeds_the_ring_buffer(monkeypatch):
    monkeypatch.setattr(logsetup, "candidate_paths", lambda: [])
    buf = DiagnosticsBuffer()
    used = logsetup.setup_logging(buf, console=False)
    assert used is None

    logging.getLogger("tthol.test").error("still recorded", extra={"cat": "startup"})
    assert "still recorded" in [e.message for e in buf.query()]


def test_console_formatter_survives_a_record_with_no_extra(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(logsetup, "candidate_paths", lambda: [tmp_path / "events.jsonl"])
    logsetup.setup_logging(DiagnosticsBuffer(), console=True)

    # A third-party record carries none of our fields; without ContextFilter
    # supplying defaults the formatter raises KeyError on char_pid.
    logging.getLogger("pymem").warning("Process 7924 is being debugged")
    err = capsys.readouterr().err
    assert "pid=-" in err and "char=-" in err
    assert "--- Logging error" not in err


def test_startup_header_is_the_first_event(tmp_path, monkeypatch):
    monkeypatch.setattr(logsetup, "candidate_paths", lambda: [tmp_path / "events.jsonl"])
    buf = DiagnosticsBuffer()
    logsetup.setup_logging(buf, console=False)

    events = list(reversed(buf.query()))
    assert events[0].cat == "startup"
    assert events[0].detail is not None
    assert events[0].detail["app_version"] == APP_VERSION
    assert "static_base" in events[0].detail
    assert events[0].detail["events_path"] == str(tmp_path / "events.jsonl")


def test_setup_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(logsetup, "candidate_paths", lambda: [tmp_path / "events.jsonl"])
    logsetup.setup_logging(DiagnosticsBuffer(), console=False)
    before = len(logging.getLogger().handlers)
    logsetup.setup_logging(DiagnosticsBuffer(), console=False)
    assert len(logging.getLogger().handlers) == before
