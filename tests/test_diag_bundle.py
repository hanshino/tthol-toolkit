import io
import json
import zipfile

from services.diag_bundle import (
    build_bundle,
    bundle_filename,
    render_human_line,
    render_report_md,
)
from services.diag_events import SCHEMA_VERSION, DiagEvent


def _ev(ts, level="ERROR", code="E_INV_NOT_FOUND", msg="Inventory not found in memory"):
    return DiagEvent(
        v=SCHEMA_VERSION,
        ts=ts,
        level=level,
        logger="tthol.worker",
        pid=27160,
        char="無塵",
        cat="inventory",
        code=code,
        message=msg,
        detail={"hp_addr": "0x1BE7A430"},
    )


def test_human_line_includes_identity_and_code():
    line = render_human_line(_ev(1700000000.0))
    assert "ERROR" in line
    assert "27160" in line
    assert "無塵" in line
    assert "E_INV_NOT_FOUND" in line
    assert "Inventory not found in memory" in line
    assert "\n" not in line


def test_report_md_leads_with_errors_and_environment():
    md = render_report_md(
        events=[_ev(1.0), _ev(2.0, level="INFO", code=None, msg="located")],
        header={"app_version": "1.2.1", "static_base": "0x778afc", "frozen": True},
        sessions=[{"pid": 27160, "name": "無塵", "link": "ok"}],
    )
    assert "# tthol-reader diagnostic report" in md
    assert "1.2.1" in md
    assert "0x778afc" in md
    assert "E_INV_NOT_FOUND" in md
    assert "27160" in md


def test_report_md_caps_the_error_list_at_20_and_says_so():
    md = render_report_md(
        events=[_ev(float(i)) for i in range(50)],
        header={"app_version": "1.2.1"},
        sessions=[],
    )
    assert "50 total" in md  # the total is stated, so the cap is not silent truncation
    assert "showing last 20" in md


def test_bundle_contains_report_runtime_and_events(tmp_path):
    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        json.dumps(
            {
                "v": 1,
                "ts": 1.0,
                "level": "ERROR",
                "logger": "tthol.worker",
                "pid": 1,
                "char": None,
                "cat": "inventory",
                "code": "E_INV_NOT_FOUND",
                "message": "m",
                "detail": None,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "events.jsonl.1").write_text(
        json.dumps(
            {
                "v": 1,
                "ts": 0.5,
                "level": "INFO",
                "logger": "tthol.startup",
                "pid": None,
                "char": None,
                "cat": "startup",
                "code": None,
                "message": "older",
                "detail": None,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    blob = build_bundle(events_path, header={"app_version": "1.2.1"}, sessions=[])
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        names = set(zf.namelist())
        assert "report.md" in names
        assert "runtime.json" in names
        assert "events/events.jsonl" in names
        assert "events/events.jsonl.1" in names
        assert json.loads(zf.read("runtime.json"))["app_version"] == "1.2.1"


def test_bundle_survives_a_missing_events_file(tmp_path):
    blob = build_bundle(tmp_path / "gone.jsonl", header={"app_version": "1.2.1"}, sessions=[])
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        assert "report.md" in zf.namelist()


def test_filename_is_sortable():
    import datetime

    name = bundle_filename(datetime.datetime(2026, 8, 22, 14, 5, 9))
    assert name == "tthol-diag-20260822-140509.zip"
