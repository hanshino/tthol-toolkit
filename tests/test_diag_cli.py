import json
import zipfile

import pytest

import diag


def _write_events(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def _row(ts, level="ERROR", code="E_INV_NOT_FOUND", pid=27160, msg="m"):
    return {
        "v": 1,
        "ts": ts,
        "level": level,
        "logger": "tthol.worker",
        "pid": pid,
        "char": "無塵",
        "cat": "inventory",
        "code": code,
        "message": msg,
        "detail": None,
    }


def test_parse_since_accepts_durations():
    now = 1_000_000.0
    assert diag.parse_since("10m", now=now) == now - 600
    assert diag.parse_since("2h", now=now) == now - 7200
    assert diag.parse_since("30s", now=now) == now - 30
    assert diag.parse_since(None, now=now) is None


def test_parse_since_rejects_garbage():
    with pytest.raises(ValueError):
        diag.parse_since("soon", now=0.0)


def test_events_json_emits_one_object_per_line(tmp_path, capsys, monkeypatch):
    events = tmp_path / "events.jsonl"
    _write_events(events, [_row(1.0), _row(2.0, level="INFO", code=None)])
    monkeypatch.setattr(diag, "read_runtime_json", lambda: {"events_path": str(events), "pid": 1})
    monkeypatch.setattr(diag, "_app_is_live", lambda _info: False)

    assert diag.main(["events", "--json"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 2
    for line in lines:
        json.loads(line)


def test_events_filters_by_code_and_level(tmp_path, capsys, monkeypatch):
    events = tmp_path / "events.jsonl"
    _write_events(events, [_row(1.0), _row(2.0, level="INFO", code=None, msg="fine")])
    monkeypatch.setattr(diag, "read_runtime_json", lambda: {"events_path": str(events), "pid": 1})
    monkeypatch.setattr(diag, "_app_is_live", lambda _info: False)

    diag.main(["events", "--code", "E_INV_NOT_FOUND", "--json"])
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1
    assert json.loads(out[0])["code"] == "E_INV_NOT_FOUND"

    diag.main(["events", "--level", "INFO", "--json"])
    out = capsys.readouterr().out.strip().splitlines()
    assert json.loads(out[0])["message"] == "fine"


def test_events_without_json_renders_human_lines(tmp_path, capsys, monkeypatch):
    events = tmp_path / "events.jsonl"
    _write_events(events, [_row(1.0)])
    monkeypatch.setattr(diag, "read_runtime_json", lambda: {"events_path": str(events), "pid": 1})
    monkeypatch.setattr(diag, "_app_is_live", lambda _info: False)

    diag.main(["events"])
    out = capsys.readouterr().out
    assert "E_INV_NOT_FOUND" in out
    assert "27160" in out
    assert not out.strip().startswith("{")


def test_inspect_reads_a_bundle(tmp_path, capsys):
    bundle_path = tmp_path / "b.zip"
    with zipfile.ZipFile(bundle_path, "w") as zf:
        zf.writestr("report.md", "# report")
        zf.writestr("events/events.jsonl", json.dumps(_row(1.0)) + "\n")

    assert diag.main(["inspect", str(bundle_path), "--json"]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert json.loads(out[0])["code"] == "E_INV_NOT_FOUND"


def test_missing_runtime_json_is_a_clear_error(capsys, monkeypatch):
    monkeypatch.setattr(diag, "read_runtime_json", lambda: None)
    assert diag.main(["events"]) == 2
    assert "runtime.json" in capsys.readouterr().err


def test_summary_reports_counts_and_staleness(tmp_path, capsys, monkeypatch):
    events = tmp_path / "events.jsonl"
    _write_events(events, [_row(1.0), _row(2.0, level="WARNING")])
    monkeypatch.setattr(
        diag,
        "read_runtime_json",
        lambda: {
            "events_path": str(events),
            "pid": 2_147_483_600,
            "app_version": "1.2.1",
            "port": 51234,
        },
    )
    monkeypatch.setattr(diag, "_app_is_live", lambda _info: False)

    assert diag.main(["summary"]) == 0
    out = capsys.readouterr().out
    assert "1.2.1" in out
    assert "ERROR" in out
    assert "not running" in out.lower()
