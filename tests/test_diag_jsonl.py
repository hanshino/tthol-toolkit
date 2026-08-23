import json
import logging

from services.diag_jsonl import JsonlHandler, read_jsonl


def _logger(handler: logging.Handler, name: str) -> logging.Logger:
    log = logging.getLogger(name)
    log.setLevel(logging.DEBUG)
    log.handlers.clear()
    log.propagate = False
    log.addHandler(handler)
    return log


def test_each_line_is_valid_json_with_schema_version(tmp_path):
    path = tmp_path / "events.jsonl"
    handler = JsonlHandler(path)
    log = _logger(handler, "tthol.test.jsonl1")
    log.info("hello", extra={"char_pid": 5, "cat": "startup"})
    log.error("bad", extra={"code": "E_PROC_GONE"})
    handler.close()

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    for line in lines:
        assert json.loads(line)["v"] == 1
    assert json.loads(lines[1])["code"] == "E_PROC_GONE"


def test_non_serialisable_detail_does_not_drop_the_event(tmp_path):
    path = tmp_path / "events.jsonl"
    handler = JsonlHandler(path)
    log = _logger(handler, "tthol.test.jsonl2")
    log.error("weird", extra={"detail": {"pm": object()}})
    handler.close()

    parsed = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert isinstance(parsed["detail"]["pm"], str)


def test_rotation_keeps_every_backup_valid_jsonl(tmp_path):
    path = tmp_path / "events.jsonl"
    handler = JsonlHandler(path, max_bytes=2_000, backup_count=3)
    log = _logger(handler, "tthol.test.jsonl3")
    for i in range(200):
        log.info("padding message number %d", i, extra={"cat": "startup"})
    handler.close()

    assert (tmp_path / "events.jsonl.1").exists()
    for candidate in tmp_path.iterdir():
        for line in candidate.read_text(encoding="utf-8").splitlines():
            json.loads(line)  # raises if a record was split mid-line


def test_read_jsonl_merges_backups_in_chronological_order(tmp_path):
    path = tmp_path / "events.jsonl"
    handler = JsonlHandler(path, max_bytes=2_000, backup_count=3)
    log = _logger(handler, "tthol.test.jsonl4")
    for i in range(200):
        log.info("m%d", i, extra={"cat": "startup"})
    handler.close()

    events = read_jsonl(path)
    # backup_count=3 deliberately discards the oldest rolls, so the total is
    # far below 200. What must hold is that the surviving files are merged --
    # more events than one 2KB file can hold -- oldest first, ending on the
    # newest record in the live file.
    one_file_worth = len((tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines())
    assert len(events) > one_file_worth
    assert events == sorted(events, key=lambda e: e.ts)
    assert events[-1].message == "m199"


def test_read_jsonl_skips_corrupt_lines(tmp_path):
    path = tmp_path / "events.jsonl"
    handler = JsonlHandler(path)
    log = _logger(handler, "tthol.test.jsonl5")
    log.info("good", extra={"cat": "startup"})
    handler.close()
    with path.open("a", encoding="utf-8") as fh:
        fh.write("{not json\n")

    assert len(read_jsonl(path)) == 1


def test_handler_never_raises(tmp_path):
    handler = JsonlHandler(tmp_path / "events.jsonl")
    broken = logging.LogRecord("x", logging.INFO, __file__, 1, "%d", ("nope",), None)
    handler.emit(broken)  # must not raise
    handler.close()
