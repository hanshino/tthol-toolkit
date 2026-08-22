import logging
import threading

from services.diag_buffer import BUFFER_MAXLEN, DiagnosticsBuffer, DiagnosticsHandler
from services.diag_events import SCHEMA_VERSION, DiagEvent


def _ev(
    ts: float,
    level: str = "INFO",
    pid: int | None = None,
    cat: str = "general",
    code: str | None = None,
) -> DiagEvent:
    return DiagEvent(
        v=SCHEMA_VERSION,
        ts=ts,
        level=level,
        logger="tthol.test",
        pid=pid,
        char=None,
        cat=cat,
        code=code,
        message=f"m{ts}",
        detail=None,
    )


def test_buffer_caps_at_maxlen():
    buf = DiagnosticsBuffer()
    for i in range(BUFFER_MAXLEN + 50):
        buf.append(_ev(float(i)))
    got = buf.query()
    assert len(got) == BUFFER_MAXLEN
    assert got[0].ts == float(BUFFER_MAXLEN + 49)  # newest first


def test_query_filters_combine():
    buf = DiagnosticsBuffer()
    buf.append(_ev(1.0, level="INFO", pid=1, cat="locate"))
    buf.append(_ev(2.0, level="ERROR", pid=1, cat="inventory", code="E_INV_NOT_FOUND"))
    buf.append(_ev(3.0, level="ERROR", pid=2, cat="inventory", code="E_INV_NOT_FOUND"))

    assert [e.ts for e in buf.query(level="ERROR")] == [3.0, 2.0]
    assert [e.ts for e in buf.query(pid=1)] == [2.0, 1.0]
    assert [e.ts for e in buf.query(since=1.5)] == [3.0, 2.0]
    assert [e.ts for e in buf.query(cat="inventory", pid=2)] == [3.0]
    assert [e.ts for e in buf.query(code="E_INV_NOT_FOUND")] == [3.0, 2.0]
    assert [e.ts for e in buf.query(limit=1)] == [3.0]


def test_counts_by_level():
    buf = DiagnosticsBuffer()
    buf.append(_ev(1.0, level="ERROR"))
    buf.append(_ev(2.0, level="ERROR"))
    buf.append(_ev(3.0, level="WARNING"))
    assert buf.counts() == {"ERROR": 2, "WARNING": 1}


def test_concurrent_appends_lose_nothing():
    buf = DiagnosticsBuffer()

    def worker(base: int) -> None:
        for i in range(100):
            buf.append(_ev(float(base * 100 + i)))

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(buf.query()) == 500


def test_handler_appends_records():
    buf = DiagnosticsBuffer()
    log = logging.getLogger("tthol.test.handler")
    log.setLevel(logging.INFO)
    log.addHandler(DiagnosticsHandler(buf))
    try:
        log.error("boom", extra={"char_pid": 7, "cat": "locate", "code": "E_LOCK_LOST"})
    finally:
        log.handlers.clear()
    got = buf.query()
    assert len(got) == 1
    assert got[0].pid == 7 and got[0].code == "E_LOCK_LOST"


def test_handler_never_raises_on_bad_record():
    buf = DiagnosticsBuffer()
    handler = DiagnosticsHandler(buf)
    broken = logging.LogRecord("x", logging.INFO, __file__, 1, "%d", ("not-an-int",), None)
    handler.emit(broken)  # must not raise
    assert buf.query() == []
