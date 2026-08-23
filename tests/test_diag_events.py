import json
import logging

from services.diag_events import (
    SCHEMA_VERSION,
    ErrorCode,
    event_from_json_line,
    event_from_record,
    event_to_json_line,
)


def _record(**extra) -> logging.LogRecord:
    rec = logging.LogRecord(
        name="tthol.worker",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="inventory not found",
        args=(),
        exc_info=None,
    )
    for k, v in extra.items():
        setattr(rec, k, v)
    return rec


def test_event_from_record_reads_extras():
    ev = event_from_record(
        _record(
            char_pid=27160,
            char_name="無塵",
            cat="inventory",
            code=ErrorCode.E_INV_NOT_FOUND,
            detail={"hp_addr": 123},
        )
    )
    assert ev.v == SCHEMA_VERSION
    assert ev.level == "ERROR"
    assert ev.logger == "tthol.worker"
    assert ev.pid == 27160
    assert ev.char == "無塵"
    assert ev.cat == "inventory"
    assert ev.code == "E_INV_NOT_FOUND"
    assert ev.detail == {"hp_addr": 123}
    assert ev.message == "inventory not found"


def test_event_from_record_defaults_missing_extras_to_none():
    ev = event_from_record(_record())
    assert ev.pid is None
    assert ev.char is None
    assert ev.code is None
    assert ev.detail is None
    assert ev.cat == "general"


def test_json_line_roundtrip():
    ev = event_from_record(_record(char_pid=1, cat="locate"))
    line = event_to_json_line(ev)
    assert "\n" not in line
    assert json.loads(line)["v"] == SCHEMA_VERSION
    assert event_from_json_line(line) == ev


def test_non_serialisable_detail_degrades_to_repr():
    ev = event_from_record(_record(detail={"pm": object()}))
    parsed = json.loads(event_to_json_line(ev))
    assert isinstance(parsed["detail"]["pm"], str)
    assert "object object" in parsed["detail"]["pm"]


def test_message_formats_args():
    rec = logging.LogRecord(
        name="tthol.worker",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="located at 0x%08X",
        args=(0x1BE7A430,),
        exc_info=None,
    )
    assert event_from_record(rec).message == "located at 0x1BE7A430"


def test_error_codes_are_their_own_names():
    for name in dir(ErrorCode):
        if name.startswith("E_"):
            assert getattr(ErrorCode, name) == name


def test_diag_event_is_frozen():
    ev = event_from_record(_record())
    try:
        ev.level = "INFO"  # type: ignore[misc]
    except Exception as exc:
        assert "frozen" in str(exc).lower() or exc.__class__.__name__ == "FrozenInstanceError"
    else:
        raise AssertionError("DiagEvent must be frozen")
