# Diagnostics & Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every failure in the app leave a traceable, structured record that both a human (via a diagnostics page and an exportable bundle) and an agent (via a fixed discovery pointer, JSONL on disk, stable error codes, and a CLI) can act on without a maintainer relaying context.

**Architecture:** Python `logging` is the single event bus. Two handlers consume the root logger: `DiagnosticsHandler` feeds an in-memory ring buffer for the live UI, `JsonlHandler` writes `events.jsonl` for durability, the bundle, and the CLI. Frontend errors POST to an endpoint that re-emits them onto the same bus, so browser and backend land on one timeline. Discovery is solved by `runtime.json` at a fixed path; matching is solved by stable `code` constants rather than prose messages.

**Tech Stack:** Python 3.11 stdlib `logging` / `dataclasses` / `zipfile` / `argparse` (no new Python dependencies), FastAPI, Pydantic v2, pytest + pytest-asyncio + httpx, React 18 + Vite + TypeScript (no new frontend dependencies).

**Spec:** `docs/superpowers/specs/2026-08-22-diagnostics-observability-design.md`

## Global Constraints

- **All code output in English** — log messages, print statements, comments, variable names. Communication with the user is Chinese; code artifacts are English to avoid Windows cp950 encoding issues.
- **Always `uv run`** for every Python invocation. Never bare `python` or `python -m`.
- **Always `encoding="utf-8"`** on every file read/write.
- **No new Python dependencies.** Stdlib only — keeps the PyInstaller bundle flat.
- **No new frontend dependencies.** No virtual-list library; a "load more" button is sufficient at the 1000-event cap.
- **Read-only with respect to game memory.** Nothing in this plan writes to the game process.
- **Pydantic → TypeScript is a generated chain.** After editing `services/api_types.py`: `uv run python scripts/gen_openapi.py`, then `cd webui && npm run gen-types`, then hand-add the alias in `webui/src/api/types.ts`. `webui/src/api/schema.ts` is auto-generated — never hand-edit it.
- **`_Base` sets `extra="forbid"`** in `services/api_types.py`. A field must exist on the model before any payload may carry it.
- **Logging handlers must never log from inside `emit()`** — a handler that logs recurses into itself. Wrap every `emit` body in `try/except: pass`.
- **Contrast floor 4.5:1.** In new UI, body text uses `var(--tt-text)`, secondary text uses `var(--tt-dim)` (6.3:1 on panel). Never `var(--tt-mute)` (3.3:1) for body text.
- **No emoji as icons.** Text markers (`錯` / `警` / `訊` / `詳`) or SVG only.
- **Line length 100** (`[tool.ruff] line-length = 100`). Run `uv run ruff check .` and `uv run ruff format .` before each commit.
- **Log level names in events are the stdlib ones:** `DEBUG` / `INFO` / `WARNING` / `ERROR`.
- **`asyncio_mode = "auto"`** — async test functions need no `@pytest.mark.asyncio`.

---

## File Structure

**New backend files**

| File | Responsibility |
|---|---|
| `services/diag_events.py` | `DiagEvent` dataclass, `ErrorCode` constants, serialisation helpers. No I/O, no logging imports beyond the record shape. |
| `services/diag_buffer.py` | `DiagnosticsBuffer` (ring buffer) and `DiagnosticsHandler`. In-memory only. |
| `services/diag_jsonl.py` | `JsonlHandler` — the on-disk sink and its rotation. |
| `services/logsetup.py` | Wires the bus: picks the landing path, builds handlers, installs the context filter, writes the startup header. |
| `services/runtime_info.py` | `runtime.json` write / read / staleness check, and the environment header used by both the startup log and the bundle. |
| `services/diagnostics.py` | Public façade: `bind()`, `set_verbose()`, `get_buffer()`, `snapshot_locate_failure()`. This is what the rest of the app imports. |
| `services/api/diagnostics.py` | The `/api/diagnostics/*` router. |
| `services/diag_bundle.py` | Bundle assembly: `report.md` rendering and zip construction. Shared by the API and the CLI. |
| `diag.py` | CLI at repo root, alongside `reader.py` / `auto_detect.py`. |
| `.claude/commands/tthol-diag.md` | Triage skill. |

Split rationale: the buffer, the disk sink, and the wiring change for different reasons and are tested independently. `services/diagnostics.py` stays a thin façade so call sites throughout the app import one name and the internals can move.

**Modified backend files**

| File | Change |
|---|---|
| `services/api_types.py` | Add `ErrorInfo`; add `last_error` to `CharacterRow` / `CharacterDetail`. |
| `services/char_session.py:71` | Replace the no-op `on_error` with a real handler; hold a bound logger. |
| `services/worker.py` | Bound logger, error codes, failure snapshots, relocate-noise suppression. |
| `services/worker_manager.py:173` | `print()` → `log.exception()`. |
| `services/api/__init__.py` | Mount the diagnostics router; register exception handlers and the request middleware. |
| `app.py` | Call `logsetup` / `runtime_info`; remove the inline `_setup_logging`. |
| `tthol-reader.spec` | Ship `.claude/commands/tthol-diag.md`? No — not bundled. Only confirm no change is needed. |

**Modified frontend files**

| File | Change |
|---|---|
| `webui/src/api/client.ts` | `ApiError` carrying status / detail / path. |
| `webui/src/diag/report.ts` | New — `reportClientError`. |
| `webui/src/components/ErrorBoundary.tsx` | New. |
| `webui/src/pages/Diagnostics.tsx` | New — the 脈案 page. |
| `webui/src/main.tsx` | Global error hooks. |
| `webui/src/App.tsx` | Route the new page; wrap `<main>` in the boundary. |
| `webui/src/components/TopNav.tsx` | Add the 脈案 tab; replace the hardcoded `v0.7.2`. |
| `webui/src/pages/Dashboard.tsx` | Surface `last_error`. |
| `webui/src/pages/CharDetail/BodyTab.tsx`, `ItemsTab.tsx`, `AutoClickTab.tsx`, `KeepActiveTab.tsx`, `webui/src/pages/Snapshots.tsx` | Replace swallowed catches. |

---

## Task Ordering

Tasks 1–6 build the bus with no consumers. Tasks 7–10 attach producers. Tasks 11–12 expose it over HTTP. Tasks 13–14 add the agent surface. Tasks 15–19 do the frontend. Each task is independently testable; the app remains runnable after every commit.

---

### Task 1: Event shape and error codes

**Files:**
- Create: `services/diag_events.py`
- Test: `tests/test_diag_events.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `DiagEvent` (frozen dataclass, fields `v, ts, level, logger, pid, char, cat, code, message, detail`), `SCHEMA_VERSION: int = 1`, `ErrorCode` constants (`E_PROC_GONE`, `E_CHAIN_READ`, `E_LOCATE_EXHAUSTED`, `E_LOCK_LOST`, `E_SCAN_FAILED`, `E_INV_NOT_FOUND`, `E_WH_NOT_FOUND`, `E_API_5XX`, `E_CLIENT`), `event_from_record(record) -> DiagEvent`, `event_to_json_line(ev) -> str`, `event_from_json_line(line) -> DiagEvent`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_diag_events.py
import json
import logging

from services.diag_events import (
    SCHEMA_VERSION,
    DiagEvent,
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
        _record(char_pid=27160, char_name="無塵", cat="inventory", code=ErrorCode.E_INV_NOT_FOUND,
                detail={"hp_addr": 123})
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
        name="tthol.worker", level=logging.INFO, pathname=__file__, lineno=1,
        msg="located at 0x%08X", args=(0x1BE7A430,), exc_info=None,
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_diag_events.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.diag_events'`

- [ ] **Step 3: Write minimal implementation**

```python
# services/diag_events.py
"""Diagnostic event shape shared by the ring buffer, the JSONL sink, and the CLI.

Pure data: no I/O and no handler logic, so both the in-process buffer and the
offline CLI can depend on it without pulling in the logging wiring.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from logging import LogRecord
from typing import Any

SCHEMA_VERSION = 1


class ErrorCode:
    """Stable identifiers for failure paths.

    `message` is prose and may be reworded or translated between versions;
    nothing should ever match on it. These codes are the contract instead.
    """

    E_PROC_GONE = "E_PROC_GONE"
    E_CHAIN_READ = "E_CHAIN_READ"
    E_LOCATE_EXHAUSTED = "E_LOCATE_EXHAUSTED"
    E_LOCK_LOST = "E_LOCK_LOST"
    E_SCAN_FAILED = "E_SCAN_FAILED"
    E_INV_NOT_FOUND = "E_INV_NOT_FOUND"
    E_WH_NOT_FOUND = "E_WH_NOT_FOUND"
    E_API_5XX = "E_API_5XX"
    E_CLIENT = "E_CLIENT"


@dataclass(frozen=True)
class DiagEvent:
    v: int
    ts: float
    level: str
    logger: str
    pid: int | None
    char: str | None
    cat: str
    code: str | None
    message: str
    detail: dict[str, Any] | None


def event_from_record(record: LogRecord) -> DiagEvent:
    """Build an event from a log record, tolerating records with no `extra`.

    Third-party loggers (pymem, uvicorn) emit records without our fields, so
    every lookup defaults rather than raising.
    """
    return DiagEvent(
        v=SCHEMA_VERSION,
        ts=record.created,
        level=record.levelname,
        logger=record.name,
        pid=getattr(record, "char_pid", None),
        char=getattr(record, "char_name", None),
        cat=getattr(record, "cat", None) or "general",
        code=getattr(record, "code", None),
        message=record.getMessage(),
        detail=getattr(record, "detail", None),
    )


def event_to_json_line(ev: DiagEvent) -> str:
    """Serialise to a single JSONL line.

    `default=repr` keeps an unexpected non-JSON value in `detail` from losing
    the whole event — a degraded field beats a missing record.
    """
    return json.dumps(asdict(ev), ensure_ascii=False, default=repr)


def event_from_json_line(line: str) -> DiagEvent:
    return DiagEvent(**json.loads(line))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_diag_events.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff format services/diag_events.py tests/test_diag_events.py
uv run ruff check services/diag_events.py tests/test_diag_events.py
git add services/diag_events.py tests/test_diag_events.py
git commit -m "feat(diag): DiagEvent shape and stable error codes"
```

---

### Task 2: Ring buffer and its handler

**Files:**
- Create: `services/diag_buffer.py`
- Test: `tests/test_diag_buffer.py`

**Interfaces:**
- Consumes: `services.diag_events.DiagEvent`, `event_from_record`.
- Produces: `DiagnosticsBuffer` with `append(ev)`, `query(since=None, level=None, pid=None, cat=None, code=None, limit=None) -> list[DiagEvent]` (newest first), `counts() -> dict[str, int]`, `clear()`; and `DiagnosticsHandler(buffer)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_diag_buffer.py
import logging
import threading

from services.diag_buffer import BUFFER_MAXLEN, DiagnosticsBuffer, DiagnosticsHandler
from services.diag_events import DiagEvent, SCHEMA_VERSION


def _ev(ts: float, level: str = "INFO", pid: int | None = None, cat: str = "general",
        code: str | None = None) -> DiagEvent:
    return DiagEvent(v=SCHEMA_VERSION, ts=ts, level=level, logger="tthol.test", pid=pid,
                     char=None, cat=cat, code=code, message=f"m{ts}", detail=None)


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_diag_buffer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.diag_buffer'`

- [ ] **Step 3: Write minimal implementation**

```python
# services/diag_buffer.py
"""In-memory ring buffer of diagnostic events, and the logging handler that fills it.

Feeds the live diagnostics page. Durability is the JSONL sink's job, not this
module's — the buffer is deliberately bounded and process-local.
"""

from __future__ import annotations

import logging
import threading
from collections import Counter, deque

from services.diag_events import DiagEvent, event_from_record

BUFFER_MAXLEN = 1000


class DiagnosticsBuffer:
    def __init__(self, maxlen: int = BUFFER_MAXLEN) -> None:
        self._events: deque[DiagEvent] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def append(self, ev: DiagEvent) -> None:
        with self._lock:
            self._events.append(ev)

    def query(
        self,
        since: float | None = None,
        level: str | None = None,
        pid: int | None = None,
        cat: str | None = None,
        code: str | None = None,
        limit: int | None = None,
    ) -> list[DiagEvent]:
        """Newest first. `since` is exclusive so a poller can pass its last ts."""
        with self._lock:
            items = list(self._events)
        out = [
            e
            for e in reversed(items)
            if (since is None or e.ts > since)
            and (level is None or e.level == level)
            and (pid is None or e.pid == pid)
            and (cat is None or e.cat == cat)
            and (code is None or e.code == code)
        ]
        return out[:limit] if limit is not None else out

    def counts(self) -> dict[str, int]:
        with self._lock:
            return dict(Counter(e.level for e in self._events))

    def clear(self) -> None:
        with self._lock:
            self._events.clear()


class DiagnosticsHandler(logging.Handler):
    def __init__(self, buffer: DiagnosticsBuffer) -> None:
        super().__init__()
        self._buffer = buffer

    def emit(self, record: logging.LogRecord) -> None:
        # Must never log: logging from inside emit() recurses into this handler.
        try:
            self._buffer.append(event_from_record(record))
        except Exception:
            pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_diag_buffer.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff format services/diag_buffer.py tests/test_diag_buffer.py
uv run ruff check services/diag_buffer.py tests/test_diag_buffer.py
git add services/diag_buffer.py tests/test_diag_buffer.py
git commit -m "feat(diag): in-memory ring buffer and handler"
```

---

### Task 3: JSONL disk sink

**Files:**
- Create: `services/diag_jsonl.py`
- Test: `tests/test_diag_jsonl.py`

**Interfaces:**
- Consumes: `services.diag_events.event_from_record`, `event_to_json_line`.
- Produces: `JsonlHandler(path, max_bytes=5_000_000, backup_count=5)` (subclass of `logging.handlers.RotatingFileHandler`), and `read_jsonl(path) -> list[DiagEvent]` which reads the file plus its `.1`…`.N` backups in chronological order and skips unparseable lines.

**Note on rotation:** `RotatingFileHandler` rolls over *before* writing a record that would exceed `maxBytes`, so a record is never split across files. Each backup therefore stays valid JSONL with no extra work — the test below locks that in.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_diag_jsonl.py
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
    assert len(events) > 100
    assert events == sorted(events, key=lambda e: e.ts)


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_diag_jsonl.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.diag_jsonl'`

- [ ] **Step 3: Write minimal implementation**

```python
# services/diag_jsonl.py
"""JSONL sink — the single on-disk format for diagnostic events.

One JSON object per line. Human-readable output is rendered on demand by
diag.py and by the bundle's report.md, so there is exactly one record and no
formatted text to parse back.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from services.diag_events import DiagEvent, event_from_json_line, event_from_record, event_to_json_line

MAX_BYTES = 5_000_000
BACKUP_COUNT = 5


class JsonlHandler(RotatingFileHandler):
    def __init__(
        self,
        path: Path | str,
        max_bytes: int = MAX_BYTES,
        backup_count: int = BACKUP_COUNT,
    ) -> None:
        # RotatingFileHandler rolls over before writing a record that would
        # exceed maxBytes, so a record is never split across files and every
        # backup stays valid JSONL.
        super().__init__(
            str(path), maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
        )

    def format(self, record: logging.LogRecord) -> str:
        return event_to_json_line(event_from_record(record))

    def emit(self, record: logging.LogRecord) -> None:
        # Must never log: logging from inside emit() recurses into this handler.
        try:
            super().emit(record)
        except Exception:
            pass


def read_jsonl(path: Path | str) -> list[DiagEvent]:
    """Read `path` plus its rotation backups, oldest event first.

    Backups are numbered newest-first (.1 is the most recent roll), so they are
    walked in reverse before the live file. Unparseable lines are skipped: a
    truncated tail from a killed process must not lose the rest of the record.
    """
    path = Path(path)
    candidates = [
        path.with_name(f"{path.name}.{i}")
        for i in range(BACKUP_COUNT, 0, -1)
    ] + [path]
    out: list[DiagEvent] = []
    for candidate in candidates:
        if not candidate.exists():
            continue
        for line in candidate.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(event_from_json_line(line))
            except Exception:
                continue
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_diag_jsonl.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff format services/diag_jsonl.py tests/test_diag_jsonl.py
uv run ruff check services/diag_jsonl.py tests/test_diag_jsonl.py
git add services/diag_jsonl.py tests/test_diag_jsonl.py
git commit -m "feat(diag): JSONL disk sink with rotation-safe reads"
```

---

### Task 4: Environment header and runtime.json

**Files:**
- Create: `services/runtime_info.py`
- Test: `tests/test_runtime_info.py`

**Interfaces:**
- Consumes: `services._paths.app_root`, `services.backup.APP_VERSION`.
- Produces: `RUNTIME_DIR: Path`, `runtime_json_path() -> Path`, `environment_header() -> dict`, `write_runtime_json(port, events_path) -> Path`, `read_runtime_json() -> dict | None`, `is_stale(info) -> bool`, `clear_runtime_json() -> None`.

**Note:** `runtime_json_path()` is **fixed and unconditional** — it never participates in the Task 5 landing-path fallback. A discovery pointer that moves is not a discovery pointer.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_runtime_info.py
import json
import os

import services.runtime_info as ri


def test_environment_header_has_the_fields_a_triage_needs():
    hdr = ri.environment_header()
    for key in (
        "app_version", "python", "os", "frozen", "exe",
        "knowledge_sha8", "knowledge_mtime", "items_rows",
        "static_base", "static_offsets", "player_hp_chain_base", "player_hp_chain_offsets",
    ):
        assert key in hdr, f"missing {key}"
    assert hdr["app_version"] == "1.2.1"
    # The pointer-chain constants are what a game update invalidates. Only the
    # player HP chain currently exists in reader.py; the session static chain
    # (STATIC_BASE / STATIC_OFFSETS) was removed, so the header must report it
    # as absent rather than raise.
    assert hdr["player_hp_chain_base"] == "0x7f7810"
    assert hdr["player_hp_chain_offsets"] == ["0x128", "0x68", "0x140"]
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
    assert info["app_version"] == "1.2.1"
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


def test_clear_removes_the_pointer(tmp_path, monkeypatch):
    monkeypatch.setattr(ri, "RUNTIME_DIR", tmp_path)
    ri.write_runtime_json(port=1, events_path=tmp_path / "e.jsonl")
    ri.clear_runtime_json()
    assert ri.read_runtime_json() is None
    ri.clear_runtime_json()  # idempotent, must not raise


def test_corrupt_runtime_json_reads_as_none(tmp_path, monkeypatch):
    monkeypatch.setattr(ri, "RUNTIME_DIR", tmp_path)
    (tmp_path / "runtime.json").write_text("{not json", encoding="utf-8")
    assert ri.read_runtime_json() is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_runtime_info.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.runtime_info'`

- [ ] **Step 3: Write minimal implementation**

```python
# services/runtime_info.py
"""Fixed-location discovery pointer plus the environment header.

app.py picks a random port and (before this module) recorded it only under
--dev, so nothing outside the WebView could reach a release build's API. The
Section 1 landing-path fallback also makes the events path unknowable in
advance. runtime.json answers both, from a path that never moves.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
from pathlib import Path
from typing import Any

RUNTIME_SCHEMA = 1


def _default_runtime_dir() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / ".tthol-reader"
    return base / "tthol-reader"


RUNTIME_DIR = _default_runtime_dir()


def runtime_json_path() -> Path:
    return RUNTIME_DIR / "runtime.json"


def _knowledge_facts() -> tuple[str, float]:
    from services._paths import bundled

    path = bundled("knowledge.json")
    if not path.exists():
        return ("", 0.0)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:8]
    return (digest, path.stat().st_mtime)


def _items_rows() -> int:
    import sqlite3

    from services._paths import bundled

    db = bundled("tthol.sqlite")
    if not db.exists():
        return -1
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            return int(con.execute("SELECT COUNT(*) FROM items").fetchone()[0])
        finally:
            con.close()
    except Exception:
        return -1


def environment_header() -> dict[str, Any]:
    """Everything needed to tell one install apart from another.

    The pointer-chain constants matter most: a game update invalidates them,
    and having them here lets that be confirmed or ruled out at a glance.
    """
    import reader
    from services.backup import APP_VERSION

    sha8, mtime = _knowledge_facts()
    return {
        "app_version": APP_VERSION,
        "python": sys.version.split()[0],
        "os": f"{platform.system()} {platform.version()}",
        "frozen": bool(getattr(sys, "frozen", False)),
        "exe": sys.executable,
        "knowledge_sha8": sha8,
        "knowledge_mtime": mtime,
        "items_rows": _items_rows(),
        # STATIC_BASE / STATIC_OFFSETS are absent from reader.py at present
        # (only the player HP chain survives). getattr keeps the header working
        # either way, and reports None so a reader can tell "no session chain"
        # from "chain is stale".
        "static_base": _maybe_hex(getattr(reader, "STATIC_BASE", None)),
        "static_offsets": _maybe_hex_list(getattr(reader, "STATIC_OFFSETS", None)),
        "player_hp_chain_base": _maybe_hex(getattr(reader, "PLAYER_HP_CHAIN_BASE", None)),
        "player_hp_chain_offsets": _maybe_hex_list(
            getattr(reader, "PLAYER_HP_CHAIN_OFFSETS", None)
        ),
    }


def _maybe_hex(value: int | None) -> str | None:
    return hex(value) if isinstance(value, int) else None


def _maybe_hex_list(values: list[int] | None) -> list[str] | None:
    if not isinstance(values, (list, tuple)):
        return None
    return [hex(v) for v in values]


def write_runtime_json(port: int, events_path: Path | str) -> Path:
    import time

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": RUNTIME_SCHEMA,
        "pid": os.getpid(),
        "port": port,
        "started_at": time.time(),
        "events_path": str(events_path),
        **environment_header(),
    }
    path = runtime_json_path()
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def read_runtime_json() -> dict[str, Any] | None:
    path = runtime_json_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def is_stale(info: dict[str, Any]) -> bool:
    """True when the recorded process is gone.

    A crash leaves the file behind; treating it as stale rather than deleting
    it on read keeps a usable post-mortem pointer to events_path.
    """
    pid = info.get("pid")
    if not isinstance(pid, int):
        return True
    try:
        import psutil

        return not psutil.pid_exists(pid)
    except Exception:
        return False


def clear_runtime_json() -> None:
    try:
        runtime_json_path().unlink(missing_ok=True)
    except Exception:
        pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_runtime_info.py -v`
Expected: PASS (6 passed)

If the chain assertions fail, print the live values before changing the test:
`uv run python -c "import reader; print(getattr(reader,'STATIC_BASE',None), reader.PLAYER_HP_CHAIN_BASE, reader.PLAYER_HP_CHAIN_OFFSETS)"`.
A changed `PLAYER_HP_CHAIN_BASE` means the chain was re-scanned after a game
update — update the expected value. Do not delete the assertion: it is the
canary that tells a triage whether the constants are current.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff format services/runtime_info.py tests/test_runtime_info.py
uv run ruff check services/runtime_info.py tests/test_runtime_info.py
git add services/runtime_info.py tests/test_runtime_info.py
git commit -m "feat(diag): runtime.json discovery pointer and environment header"
```

---

### Task 5: Log wiring with three-tier fallback

**Files:**
- Create: `services/logsetup.py`
- Test: `tests/test_logsetup.py`

**Interfaces:**
- Consumes: `DiagnosticsBuffer`, `DiagnosticsHandler`, `JsonlHandler`, `services.runtime_info.environment_header`.
- Produces: `setup_logging(buffer, console=True) -> Path | None` (returns the events path in use, or `None` if all three tiers failed), `candidate_paths() -> list[Path]`, `ContextFilter`, `CONSOLE_FORMAT`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_logsetup.py
import logging

import pytest

from services.diag_buffer import DiagnosticsBuffer
from services import logsetup


@pytest.fixture(autouse=True)
def _clean_root():
    root = logging.getLogger()
    saved = list(root.handlers)
    root.handlers.clear()
    yield
    root.handlers.clear()
    root.handlers.extend(saved)


def test_uses_the_first_writable_candidate(tmp_path, monkeypatch):
    good = tmp_path / "second" / "events.jsonl"
    monkeypatch.setattr(
        logsetup, "candidate_paths",
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
    assert [e.message for e in buf.query()] == ["still recorded"]


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
    assert events[0].detail["app_version"] == "1.2.1"
    assert "static_base" in events[0].detail
    assert events[0].detail["events_path"] == str(tmp_path / "events.jsonl")


def test_setup_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(logsetup, "candidate_paths", lambda: [tmp_path / "events.jsonl"])
    logsetup.setup_logging(DiagnosticsBuffer(), console=False)
    before = len(logging.getLogger().handlers)
    logsetup.setup_logging(DiagnosticsBuffer(), console=False)
    assert len(logging.getLogger().handlers) == before
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_logsetup.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.logsetup'`

- [ ] **Step 3: Write minimal implementation**

```python
# services/logsetup.py
"""Wires the logging bus: handlers, landing path, context defaults, startup header.

Extracted from app.py::_setup_logging so it can be tested. The old version
wrote next to the exe inside `except Exception: pass`, which meant a
Program Files install logged nothing and said nothing about it.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from services.diag_buffer import DiagnosticsBuffer, DiagnosticsHandler
from services.diag_jsonl import JsonlHandler

CONSOLE_FORMAT = "%(asctime)s %(levelname)-7s %(name)s [pid=%(char_pid)s char=%(char_name)s] %(message)s"

_EVENTS_FILENAME = "events.jsonl"


class ContextFilter(logging.Filter):
    """Supply `-` defaults so the console formatter never raises.

    Third-party records (pymem, uvicorn) carry none of our fields; without
    this the first such record blows up the formatter with KeyError.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "char_pid"):
            record.char_pid = "-"
        if not hasattr(record, "char_name"):
            record.char_name = "-"
        return True


def candidate_paths() -> list[Path]:
    """Landing tiers, most-preferred first."""
    from services._paths import app_root

    out: list[Path] = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        out.append(Path(local) / "tthol-reader" / "logs" / _EVENTS_FILENAME)
    out.append(app_root() / _EVENTS_FILENAME)
    out.append(Path(tempfile.gettempdir()) / _EVENTS_FILENAME)
    return out


def _make_jsonl_handler() -> tuple[JsonlHandler | None, Path | None]:
    for path in candidate_paths():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            return JsonlHandler(path), path
        except Exception:
            continue
    return (None, None)


_configured = False


def setup_logging(buffer: DiagnosticsBuffer, console: bool = True) -> Path | None:
    """Install handlers on the root logger. Returns the events path, or None.

    Returning None is not a failure mode for the app: the ring buffer still
    collects, so the diagnostics page and the bundle keep working. That
    resilience is the payoff of routing everything through one bus.
    """
    global _configured
    if _configured:
        return _current_path

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    ctx = ContextFilter()

    diag_handler = DiagnosticsHandler(buffer)
    diag_handler.addFilter(ctx)
    root.addHandler(diag_handler)

    jsonl_handler, path = _make_jsonl_handler()
    if jsonl_handler is not None:
        jsonl_handler.addFilter(ctx)
        root.addHandler(jsonl_handler)

    if console:
        stream = logging.StreamHandler()
        stream.setFormatter(logging.Formatter(CONSOLE_FORMAT))
        stream.addFilter(ctx)
        root.addHandler(stream)

    _configured = True
    _set_current_path(path)
    _log_startup_header(path)
    return path


_current_path: Path | None = None


def _set_current_path(path: Path | None) -> None:
    global _current_path
    _current_path = path


def _log_startup_header(path: Path | None) -> None:
    from services.runtime_info import environment_header

    header = environment_header()
    header["events_path"] = str(path) if path else None
    logging.getLogger("tthol.startup").info(
        "app starting: version=%s frozen=%s events=%s",
        header.get("app_version"),
        header.get("frozen"),
        header.get("events_path"),
        extra={"cat": "startup", "detail": header},
    )


def _reset_for_tests() -> None:
    global _configured
    _configured = False
    _set_current_path(None)
```

Add `logsetup._reset_for_tests()` to the `_clean_root` fixture so the idempotence guard does
not leak between tests:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_logsetup.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff format services/logsetup.py tests/test_logsetup.py
uv run ruff check services/logsetup.py tests/test_logsetup.py
git add services/logsetup.py tests/test_logsetup.py
git commit -m "feat(diag): log wiring with three-tier landing fallback"
```

---

### Task 6: Public façade — bind, verbose, failure snapshots

**Files:**
- Create: `services/diagnostics.py`
- Test: `tests/test_diagnostics_facade.py`

**Interfaces:**
- Consumes: everything from Tasks 1–5.
- Produces: `get_buffer() -> DiagnosticsBuffer`, `init(console=True) -> Path | None`, `bind(pid, name=None) -> logging.LoggerAdapter`, `set_verbose(on: bool) -> None`, `is_verbose() -> bool`, `snapshot_locate_failure(pm, hp_addr=None, knowledge=None, hp_value=None, score=None, failed_fields=None) -> dict`.

This is the only diagnostics module the rest of the app imports.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_diagnostics_facade.py
import logging

import pytest

from services import diagnostics, logsetup


@pytest.fixture(autouse=True)
def _clean_root():
    root = logging.getLogger()
    saved = list(root.handlers)
    root.handlers.clear()
    logsetup._reset_for_tests()
    diagnostics.get_buffer().clear()
    diagnostics.set_verbose(False)
    yield
    root.handlers.clear()
    root.handlers.extend(saved)
    logsetup._reset_for_tests()


def test_bind_attaches_pid_and_name_to_every_line(tmp_path, monkeypatch):
    monkeypatch.setattr(logsetup, "candidate_paths", lambda: [tmp_path / "events.jsonl"])
    diagnostics.init(console=False)
    buf = diagnostics.get_buffer()
    buf.clear()

    log = diagnostics.bind(27160, "無塵")
    log.error("boom", extra={"cat": "locate", "code": "E_LOCK_LOST"})

    ev = buf.query()[0]
    assert ev.pid == 27160
    assert ev.char == "無塵"
    assert ev.code == "E_LOCK_LOST"


def test_bind_rebind_updates_name(tmp_path, monkeypatch):
    monkeypatch.setattr(logsetup, "candidate_paths", lambda: [tmp_path / "events.jsonl"])
    diagnostics.init(console=False)
    buf = diagnostics.get_buffer()
    buf.clear()

    diagnostics.bind(1, None).info("before", extra={"cat": "locate"})
    diagnostics.bind(1, "無塵").info("after", extra={"cat": "locate"})

    by_msg = {e.message: e for e in buf.query()}
    assert by_msg["before"].char is None
    assert by_msg["after"].char == "無塵"


def test_verbose_only_moves_the_tthol_logger(tmp_path, monkeypatch):
    monkeypatch.setattr(logsetup, "candidate_paths", lambda: [tmp_path / "events.jsonl"])
    diagnostics.init(console=False)

    assert logging.getLogger("tthol").level == logging.INFO
    diagnostics.set_verbose(True)
    assert diagnostics.is_verbose() is True
    assert logging.getLogger("tthol").level == logging.DEBUG
    # Root must stay at INFO or pymem's DEBUG output floods everything.
    assert logging.getLogger().level == logging.INFO

    diagnostics.set_verbose(False)
    assert logging.getLogger("tthol").level == logging.INFO


def test_snapshot_reports_every_declared_key_even_when_probes_raise():
    class ExplodingPm:
        def read_bytes(self, *a, **kw):
            raise RuntimeError("process gone")

    snap = diagnostics.snapshot_locate_failure(ExplodingPm(), hp_addr=0x1BE7A430, score=0.4,
                                               failed_fields=["等級", "防禦"])
    for key in ("chain_hp", "compat_false", "compat_true", "bytes_hex",
                "score", "failed_fields", "process_alive", "hp_addr"):
        assert key in snap, f"missing {key}"
    assert snap["score"] == 0.4
    assert snap["failed_fields"] == ["等級", "防禦"]
    assert snap["hp_addr"] == "0x1BE7A430"
    # A probe that raises must degrade to a string, never propagate.
    assert isinstance(snap["bytes_hex"], str)


def test_snapshot_never_raises_on_a_none_process():
    snap = diagnostics.snapshot_locate_failure(None)
    assert snap["process_alive"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_diagnostics_facade.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.diagnostics'`

- [ ] **Step 3: Write minimal implementation**

```python
# services/diagnostics.py
"""Public diagnostics façade — the only diagnostics module the app imports.

Keeps call sites stable while the buffer / sink internals move behind it.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from services.diag_buffer import DiagnosticsBuffer

_buffer = DiagnosticsBuffer()
_verbose = False


def get_buffer() -> DiagnosticsBuffer:
    return _buffer


def init(console: bool = True) -> Path | None:
    from services.logsetup import setup_logging

    return setup_logging(_buffer, console=console)


def bind(pid: int, name: str | None = None) -> logging.LoggerAdapter:
    """Logger pre-loaded with this session's identity.

    Multi-boxing is a core feature; without this every worker's lines land in
    one undifferentiated stream.
    """
    return logging.LoggerAdapter(
        logging.getLogger("tthol.worker"), {"char_pid": pid, "char_name": name}
    )


def set_verbose(on: bool) -> None:
    """Raise only the `tthol` tree to DEBUG.

    Never the root logger: pymem logs at DEBUG per read and would bury
    everything else.
    """
    global _verbose
    _verbose = on
    logging.getLogger("tthol").setLevel(logging.DEBUG if on else logging.INFO)


def is_verbose() -> bool:
    return _verbose


def _probe(fn) -> Any:
    """Run a probe, returning its value or a string describing the failure.

    A snapshot is taken on an already-broken path; a probe that raises must
    degrade to a field, never replace the snapshot with an exception.
    """
    try:
        return fn()
    except Exception as exc:
        return f"<{type(exc).__name__}: {exc}>"


def snapshot_locate_failure(
    pm: Any,
    hp_addr: int | None = None,
    knowledge: dict | None = None,
    hp_value: int | None = None,
    score: float | None = None,
    failed_fields: list[str] | None = None,
) -> dict[str, Any]:
    """Structured context for a locate/read failure.

    Every declared key is always present so a consumer can read
    detail["bytes_hex"] without defensive probing.
    """
    snap: dict[str, Any] = {
        "hp_addr": hex(hp_addr) if isinstance(hp_addr, int) else None,
        "hp_value": hp_value,
        "score": score,
        "failed_fields": failed_fields,
        "process_alive": pm is not None,
        "chain_hp": None,
        "compat_false": None,
        "compat_true": None,
        "bytes_hex": None,
    }
    if pm is None:
        return snap

    import reader

    snap["chain_hp"] = _probe(lambda: reader.read_hp_from_player_chain(pm))
    if isinstance(hp_addr, int):
        snap["bytes_hex"] = _probe(lambda: pm.read_bytes(hp_addr, 32).hex())

    kb = knowledge if knowledge is not None else _probe(reader.load_knowledge)
    probe_hp = snap["chain_hp"] if isinstance(snap["chain_hp"], int) else hp_value
    if isinstance(kb, dict) and isinstance(probe_hp, int):
        for key, compat in (("compat_false", False), ("compat_true", True)):
            snap[key] = _probe(
                lambda c=compat: reader.locate_character(pm, probe_hp, kb, None, compat_mode=c)
            )
    return snap
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_diagnostics_facade.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff format services/diagnostics.py tests/test_diagnostics_facade.py
uv run ruff check services/diagnostics.py tests/test_diagnostics_facade.py
git add services/diagnostics.py tests/test_diagnostics_facade.py
git commit -m "feat(diag): façade with bind, verbose toggle, failure snapshots"
```

---

### Task 7: ErrorInfo model and last_error on the API types

**Files:**
- Modify: `services/api_types.py`
- Modify: `webui/openapi.json`, `webui/src/api/schema.ts` (both regenerated), `webui/src/api/types.ts`
- Test: `tests/test_api_types_error_info.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `ErrorInfo(ts: float, message: str, cat: str, code: str | None = None)`; `CharacterRow.last_error: ErrorInfo | None = None`; `CharacterDetail.last_error: ErrorInfo | None = None`. TS aliases `ErrorInfo` in `webui/src/api/types.ts`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_api_types_error_info.py
from services.api_types import CharacterRow, ErrorInfo, Position, Vitals, AutoClickStatus


def _row(**kw) -> CharacterRow:
    base = dict(
        pid=1, name="無塵", sect="少林", link="ok", level=20,
        vitals=Vitals(hp=1, hp_max=2, mp=1, mp_max=2, weight=1, weight_max=2),
        position=Position(map_name=None, x=0, y=0),
        autoclick=AutoClickStatus(running=False),
    )
    base.update(kw)
    return CharacterRow(**base)


def test_last_error_defaults_to_none():
    assert _row().last_error is None


def test_last_error_roundtrips():
    row = _row(last_error=ErrorInfo(ts=1.0, message="Inventory not found in memory",
                                    cat="inventory", code="E_INV_NOT_FOUND"))
    dumped = row.model_dump()
    assert dumped["last_error"]["code"] == "E_INV_NOT_FOUND"
    assert CharacterRow(**dumped).last_error.message == "Inventory not found in memory"


def test_error_info_code_is_optional():
    assert ErrorInfo(ts=1.0, message="m", cat="worker").code is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_api_types_error_info.py -v`
Expected: FAIL — `ImportError: cannot import name 'ErrorInfo' from 'services.api_types'`

- [ ] **Step 3: Write minimal implementation**

In `services/api_types.py`, add above `class Character`:

```python
class ErrorInfo(_Base):
    """Last error reported by a session's worker, surfaced on the character row."""

    ts: float
    message: str
    cat: str
    code: str | None = None
```

Then add to both `CharacterRow` and `CharacterDetail` (as the last field of each):

```python
    last_error: ErrorInfo | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_api_types_error_info.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Regenerate the TypeScript chain**

```bash
uv run python scripts/gen_openapi.py
cd webui && npm run gen-types && cd ..
```

Then add to `webui/src/api/types.ts`, keeping the list alphabetical:

```typescript
export type ErrorInfo = S['ErrorInfo'];
```

Verify the generated schema picked it up:

```bash
grep -c "ErrorInfo" webui/src/api/schema.ts
```
Expected: a non-zero count.

- [ ] **Step 6: Run the full suite and commit**

```bash
uv run pytest -q
uv run ruff format services/api_types.py tests/test_api_types_error_info.py
uv run ruff check services/api_types.py tests/test_api_types_error_info.py
git add services/api_types.py tests/test_api_types_error_info.py webui/openapi.json webui/src/api/schema.ts webui/src/api/types.ts
git commit -m "feat(api): ErrorInfo model and last_error on character payloads"
```

---

### Task 8: Connect the session's error channel

**Files:**
- Modify: `services/char_session.py`
- Test: `tests/test_worker_session_errors.py`

**Interfaces:**
- Consumes: `services.diagnostics.bind`, `services.api_types.ErrorInfo`.
- Produces: `CharSession._on_error(msg, *, cat="worker", code=None, detail=None)`, `CharSession.last_error -> ErrorInfo | None`, `CharSession._log` (a bound `LoggerAdapter`, re-bound when the name resolves). `row()` and `detail()` populate `last_error`.

This is the fix for F1 — the single line that made every worker error invisible.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_worker_session_errors.py
import logging

import pytest

from services import diagnostics, logsetup
from services.char_session import CharSession


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


def test_on_error_is_no_longer_a_no_op():
    sess = CharSession(pid=27160)
    # The worker is constructed with a real callback, not `lambda _msg: None`.
    sess._worker._cb_error("Inventory not found in memory", cat="inventory",
                           code="E_INV_NOT_FOUND", detail={"hp_addr": 123})

    assert sess.last_error is not None
    assert sess.last_error.code == "E_INV_NOT_FOUND"
    assert sess.last_error.cat == "inventory"

    events = diagnostics.get_buffer().query(code="E_INV_NOT_FOUND")
    assert len(events) == 1
    assert events[0].pid == 27160
    assert events[0].detail == {"hp_addr": 123}


def test_error_reaches_the_row_payload():
    sess = CharSession(pid=27160)
    sess._on_state("LOCATED")
    sess._on_stats([("角色名稱", "無塵"), ("血量", 100), ("最大血量", 120)])
    sess._on_error("Warehouse not found -- open warehouse UI in game first",
                   cat="warehouse", code="E_WH_NOT_FOUND")

    row = sess.row()
    assert row is not None
    assert row.last_error is not None
    assert row.last_error.code == "E_WH_NOT_FOUND"
    assert sess.detail().last_error.code == "E_WH_NOT_FOUND"


def test_logger_rebinds_to_the_character_name_once_known():
    sess = CharSession(pid=27160)
    sess._on_stats([("角色名稱", "無塵")])
    sess._on_error("boom", cat="locate")

    ev = diagnostics.get_buffer().query(cat="locate")[0]
    assert ev.char == "無塵"


def test_default_cat_and_optional_code():
    sess = CharSession(pid=1)
    sess._on_error("plain message")
    assert sess.last_error.cat == "worker"
    assert sess.last_error.code is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_worker_session_errors.py -v`
Expected: FAIL — `TypeError: <lambda>() got an unexpected keyword argument 'cat'`

- [ ] **Step 3: Write minimal implementation**

In `services/char_session.py`, add the imports:

```python
import time

from services import diagnostics
from services.api_types import ErrorInfo
```

In `__init__`, before constructing the worker:

```python
        self._last_error: ErrorInfo | None = None
        self._log = diagnostics.bind(pid)
```

and change the worker construction's `on_error` line from
`on_error=lambda _msg: None,` to:

```python
            on_error=self._on_error,
```

Add the handler and the property:

```python
    @property
    def last_error(self) -> ErrorInfo | None:
        with self._lock:
            return self._last_error

    def _on_error(
        self,
        msg: str,
        *,
        cat: str = "worker",
        code: str | None = None,
        detail: dict | None = None,
    ) -> None:
        with self._lock:
            self._last_error = ErrorInfo(ts=time.time(), message=msg, cat=cat, code=code)
        self._log.error(msg, extra={"cat": cat, "code": code, "detail": detail})
```

In `_on_stats`, re-bind the logger when the name first resolves — replace the tail of the
method:

```python
            name = translated.get("name")
            if isinstance(name, str) and name:
                if name != self.name:
                    self._log = diagnostics.bind(self.pid, name)
                self.name = name
```

In `row()`, add to the `CharacterRow(...)` call:

```python
                last_error=self._latest_error_locked(),
```

In `detail()`, add to the `CharacterDetail(...)` call:

```python
                last_error=self._latest_error_locked(),
```

And add the helper (both callers already hold `self._lock`, so this must not re-acquire it):

```python
    def _latest_error_locked(self) -> ErrorInfo | None:
        """Read `_last_error` from inside an already-held lock.

        `row()` and `detail()` both run under `self._lock`; using the public
        `last_error` property here would deadlock on the non-reentrant Lock.
        """
        return self._last_error
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_worker_session_errors.py tests/test_worker_session.py -v`
Expected: PASS — the new tests plus the existing session tests still green.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff format services/char_session.py tests/test_worker_session_errors.py
uv run ruff check services/char_session.py tests/test_worker_session_errors.py
git add services/char_session.py tests/test_worker_session_errors.py
git commit -m "fix(diag): stop discarding worker errors at the session boundary"
```

---

### Task 9: Worker error codes, snapshots, and noise suppression

**Files:**
- Modify: `services/worker.py`
- Test: `tests/test_worker_error_codes.py`

**Interfaces:**
- Consumes: `services.diagnostics.bind`, `snapshot_locate_failure`, `services.diag_events.ErrorCode`.
- Produces: every `self._cb_error(...)` call site passes `cat` and `code`; `ReaderWorker._log` replaces the module-level `log`; `RelocateWindow` tracks relocate frequency.

**Call-site map** (`services/worker.py`, current line numbers):

| Line | Message | `cat` | `code` |
|---|---|---|---|
| 176 | `Character not found -- press 重偵 or enter the HP value` (validation path) | `locate` | `E_LOCATE_EXHAUSTED` |
| 223 | `Character not found -- press 重偵 or enter the HP value` (exception path) | `locate` | `E_LOCATE_EXHAUSTED` |
| 245 | `Cannot connect to PID {pid}: {e}` | `locate` | `E_PROC_GONE` |
| 300 | `Cannot locate character -- try entering your current HP value manually` | `locate` | `E_CHAIN_READ` |
| 319 | `Scan failed: {e}` | `locate` | `E_SCAN_FAILED` |
| 330 | `Inventory not found in memory` | `inventory` | `E_INV_NOT_FOUND` |
| 338 | `Inventory scan error: {e}` | `inventory` | `E_SCAN_FAILED` |
| 367 | `Warehouse not found -- open warehouse UI in game first` | `warehouse` | `E_WH_NOT_FOUND` |
| 377 | `Warehouse scan error: {e}` | `warehouse` | `E_SCAN_FAILED` |

- [ ] **Step 1: Write the failing test**

```python
# tests/test_worker_error_codes.py
import logging

import pytest

from services import diagnostics, logsetup
from services.diag_events import ErrorCode
from services.worker import RelocateWindow, ReaderWorker


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_worker_error_codes.py -v`
Expected: FAIL — `ImportError: cannot import name 'RelocateWindow' from 'services.worker'`

- [ ] **Step 3: Write minimal implementation**

In `services/worker.py`, replace the module-level logger line
`log = logging.getLogger("tthol.worker")` with:

```python
class RelocateWindow:
    """Rate-limits repeated relocate reports inside a sliding window.

    The existing log shows `lost lock` / `re-acquired` cycling every ~9s during
    a bad session, which displaces the evidence. Frequency is itself the
    signal, so past the threshold the individual lines drop to DEBUG and the
    window closes with one summary count.
    """

    def __init__(self, window_seconds: float = 60.0, threshold: int = 2) -> None:
        self._window = window_seconds
        self._threshold = threshold
        self._start: float | None = None
        self._count = 0

    def should_log_at_info(self, now: float) -> bool:
        if self._start is None:
            self._start = now
        self._count += 1
        return self._count <= self._threshold

    def roll(self, now: float) -> int | None:
        """Close the window if it has elapsed; returns the count, or None."""
        if self._start is None or now - self._start < self._window:
            return None
        count = self._count
        self._start = None
        self._count = 0
        return count
```

In `ReaderWorker.__init__`, add:

```python
        self._log = diagnostics.bind(pid)
        self._relocate_window = RelocateWindow()
```

and add the imports at the top:

```python
from services import diagnostics
from services.diag_events import ErrorCode
```

Widen the `on_error` type annotation in `__init__` from
`on_error: Callable[[str], None],` to:

```python
        on_error: Callable[..., None],
```

Replace every `log.` reference in the file with `self._log.`, and update the nine
`self._cb_error(...)` call sites per the map above. The two `E_LOCATE_EXHAUSTED` sites
(lines 176 and 223) attach a snapshot:

```python
                        self._cb_error(
                            "Character not found -- press 重偵 or enter the HP value",
                            cat="locate",
                            code=ErrorCode.E_LOCATE_EXHAUSTED,
                            detail=diagnostics.snapshot_locate_failure(
                                pm, hp_addr=hp_addr, knowledge=self._knowledge,
                                hp_value=self._hp_value,
                            ),
                        )
```

The remaining sites take `cat` and `code` only, for example:

```python
                self._cb_error(
                    "Inventory not found in memory",
                    cat="inventory",
                    code=ErrorCode.E_INV_NOT_FOUND,
                )
```

```python
            self._cb_error(
                f"Cannot connect to PID {self._pid}: {e}",
                cat="locate",
                code=ErrorCode.E_PROC_GONE,
            )
```

For the lost-lock path, replace the bare `log.info("lost lock ...")` with the windowed
version, and add the `E_LOCK_LOST` event:

```python
                        now = time.time()
                        if self._relocate_window.should_log_at_info(now):
                            self._log.info(
                                "lost lock (validation score < 0.8 x%d); re-locating",
                                FAILURE_THRESHOLD,
                                extra={"cat": "locate", "code": ErrorCode.E_LOCK_LOST,
                                       "detail": {"score": score, "hp_addr": hex(hp_addr)}},
                            )
                        else:
                            self._log.debug(
                                "lost lock (suppressed); re-locating",
                                extra={"cat": "locate", "code": ErrorCode.E_LOCK_LOST},
                            )
                        rolled = self._relocate_window.roll(now)
                        if rolled is not None:
                            self._log.info(
                                "relocated %d times in the last 60s", rolled,
                                extra={"cat": "locate", "detail": {"relocates": rolled}},
                            )
```

Add `import time` to the imports if it is not already present.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_worker_error_codes.py tests/test_worker_scan_seq.py -v`
Expected: PASS — new tests plus the existing scan-sequence tests still green.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff format services/worker.py tests/test_worker_error_codes.py
uv run ruff check services/worker.py tests/test_worker_error_codes.py
git add services/worker.py tests/test_worker_error_codes.py
git commit -m "feat(diag): worker error codes, failure snapshots, relocate rate-limit"
```

---

### Task 10: API exception handlers and request middleware

**Files:**
- Modify: `services/api/__init__.py`
- Modify: `services/worker_manager.py:173`
- Test: `tests/test_api_error_logging.py`

**Interfaces:**
- Consumes: `services.diag_events.ErrorCode`.
- Produces: `build_app()` registers an `Exception` handler, an `HTTPException` handler, and an HTTP middleware. The 500 body shape is `{"detail": "Internal Server Error"}`, matching `HTTPException`, so the frontend has one parse path.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_api_error_logging.py
import logging

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from services import diagnostics, logsetup
from services.api import build_app
from services.diag_events import ErrorCode


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


@pytest.fixture
async def client():
    app = build_app(services=None)

    @app.get("/api/_boom")
    async def boom():
        raise RuntimeError("kaboom")

    @app.get("/api/_teapot/{pid}")
    async def teapot(pid: int):
        raise HTTPException(status_code=418, detail="short and stout")

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_unhandled_exception_is_logged_and_shaped_like_http_exception(client):
    resp = await client.get("/api/_boom")
    assert resp.status_code == 500
    assert resp.json() == {"detail": "Internal Server Error"}

    events = diagnostics.get_buffer().query(code=ErrorCode.E_API_5XX)
    assert len(events) == 1
    assert events[0].level == "ERROR"
    assert events[0].detail["path"] == "/api/_boom"
    assert events[0].detail["method"] == "GET"
    assert "kaboom" in events[0].detail["traceback"]


async def test_4xx_logs_at_warning_with_the_pid(client):
    resp = await client.get("/api/_teapot/27160")
    assert resp.status_code == 418

    events = [e for e in diagnostics.get_buffer().query(cat="api") if e.level == "WARNING"]
    assert len(events) == 1
    assert events[0].detail["status"] == 418
    assert events[0].detail["pid"] == 27160
    assert events[0].detail["detail"] == "short and stout"


async def test_successful_fast_request_logs_nothing(client):
    await client.get("/api/health")
    assert diagnostics.get_buffer().query(cat="api") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_api_error_logging.py -v`
Expected: FAIL — no `E_API_5XX` events; the buffer query returns `[]`.

- [ ] **Step 3: Write minimal implementation**

In `services/api/__init__.py`, add the imports:

```python
import logging
import re
import time
import traceback

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from services.diag_events import ErrorCode

log = logging.getLogger("tthol.api")
_PID_IN_PATH = re.compile(r"/(\d+)(?:/|$)")
SLOW_REQUEST_SECONDS = 1.0
```

and inside `build_app`, after `app.state.services = services`:

```python
    def _pid_from(path: str) -> int | None:
        m = _PID_IN_PATH.search(path)
        return int(m.group(1)) if m else None

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.error(
            "unhandled error on %s %s", request.method, request.url.path,
            extra={
                "cat": "api",
                "code": ErrorCode.E_API_5XX,
                "detail": {
                    "path": request.url.path,
                    "method": request.method,
                    "status": 500,
                    "pid": _pid_from(request.url.path),
                    "traceback": "".join(
                        traceback.format_exception(type(exc), exc, exc.__traceback__)
                    ),
                },
            },
        )
        # Match HTTPException's body shape so the frontend has one parse path.
        return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})

    @app.exception_handler(HTTPException)
    async def _http_error(request: Request, exc: HTTPException):
        level = log.error if exc.status_code >= 500 else log.warning
        level(
            "%s %s -> %d", request.method, request.url.path, exc.status_code,
            extra={
                "cat": "api",
                "code": ErrorCode.E_API_5XX if exc.status_code >= 500 else None,
                "detail": {
                    "path": request.url.path,
                    "method": request.method,
                    "status": exc.status_code,
                    "pid": _pid_from(request.url.path),
                    "detail": exc.detail,
                },
            },
        )
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    @app.middleware("http")
    async def _slow_requests(request: Request, call_next):
        # uvicorn's access log stays off; only slow requests are worth a line.
        # The 15s/60s scan timeouts surface here on their own.
        started = time.monotonic()
        response = await call_next(request)
        elapsed = time.monotonic() - started
        if elapsed >= SLOW_REQUEST_SECONDS:
            log.warning(
                "slow request %s %s took %.1fs", request.method, request.url.path, elapsed,
                extra={
                    "cat": "api",
                    "detail": {
                        "path": request.url.path,
                        "method": request.method,
                        "status": response.status_code,
                        "pid": _pid_from(request.url.path),
                        "elapsed": elapsed,
                    },
                },
            )
        return response
```

In `services/worker_manager.py`, add near the top:

```python
import logging

log = logging.getLogger("tthol.worker_manager")
```

and replace line 173's `print(f"[tick_loop] publish error: {e}")` with:

```python
                log.exception("tick loop publish error", extra={"cat": "api"})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_api_error_logging.py tests/test_api_app.py -v`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff format services/api/__init__.py services/worker_manager.py tests/test_api_error_logging.py
uv run ruff check services/api/__init__.py services/worker_manager.py tests/test_api_error_logging.py
git add services/api/__init__.py services/worker_manager.py tests/test_api_error_logging.py
git commit -m "feat(diag): log API errors and slow requests; fix lost tick-loop errors"
```

---

### Task 11: Bundle assembly

**Files:**
- Create: `services/diag_bundle.py`
- Test: `tests/test_diag_bundle.py`

**Interfaces:**
- Consumes: `services.diag_events.DiagEvent`, `services.diag_jsonl.read_jsonl`, `services.runtime_info.environment_header`.
- Produces: `render_report_md(events, header, sessions) -> str`, `render_human_line(ev) -> str`, `build_bundle(events_path, header, sessions) -> bytes`, `bundle_filename(now) -> str`.

Shared by the API (Task 12) and the CLI (Task 13) so both produce identical output.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_diag_bundle.py
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
    return DiagEvent(v=SCHEMA_VERSION, ts=ts, level=level, logger="tthol.worker", pid=27160,
                     char="無塵", cat="inventory", code=code, message=msg,
                     detail={"hp_addr": "0x1BE7A430"})


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
    assert md.count("E_INV_NOT_FOUND") <= 25
    assert "50" in md  # the total is stated, so the cap is not silent truncation


def test_bundle_contains_report_runtime_and_events(tmp_path):
    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        json.dumps({"v": 1, "ts": 1.0, "level": "ERROR", "logger": "tthol.worker",
                    "pid": 1, "char": None, "cat": "inventory",
                    "code": "E_INV_NOT_FOUND", "message": "m", "detail": None}) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "events.jsonl.1").write_text(
        json.dumps({"v": 1, "ts": 0.5, "level": "INFO", "logger": "tthol.startup",
                    "pid": None, "char": None, "cat": "startup",
                    "code": None, "message": "older", "detail": None}) + "\n",
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_diag_bundle.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.diag_bundle'`

- [ ] **Step 3: Write minimal implementation**

```python
# services/diag_bundle.py
"""Bundle assembly, shared by the API endpoint and the CLI.

One implementation so a zip built by either route is byte-comparable in
structure — a report the maintainer receives is the report the tool produces.
"""

from __future__ import annotations

import datetime as _dt
import io
import json
import zipfile
from pathlib import Path
from typing import Any

from services.diag_events import DiagEvent
from services.diag_jsonl import read_jsonl

MAX_REPORT_ERRORS = 20


def bundle_filename(now: _dt.datetime) -> str:
    return f"tthol-diag-{now:%Y%m%d-%H%M%S}.zip"


def render_human_line(ev: DiagEvent) -> str:
    ts = _dt.datetime.fromtimestamp(ev.ts).strftime("%Y-%m-%d %H:%M:%S")
    who = f"pid={ev.pid or '-'} char={ev.char or '-'}"
    code = f" [{ev.code}]" if ev.code else ""
    return f"{ts} {ev.level:<7} {ev.logger} [{who}]{code} {ev.message}"


def render_report_md(
    events: list[DiagEvent],
    header: dict[str, Any],
    sessions: list[dict[str, Any]],
) -> str:
    problems = [e for e in events if e.level in ("ERROR", "WARNING")]
    shown = problems[-MAX_REPORT_ERRORS:]

    lines: list[str] = ["# tthol-reader diagnostic report", ""]

    lines.append("## Environment")
    lines.append("")
    for key, value in header.items():
        lines.append(f"- **{key}**: `{value}`")
    lines.append("")

    lines.append("## Sessions")
    lines.append("")
    if sessions:
        for s in sessions:
            lines.append(f"- pid `{s.get('pid')}` — {s.get('name') or '(unnamed)'} — link `{s.get('link')}`")
    else:
        lines.append("- (none)")
    lines.append("")

    lines.append(f"## Problems ({len(problems)} total, showing last {len(shown)})")
    lines.append("")
    if shown:
        for ev in shown:
            lines.append(f"- `{render_human_line(ev)}`")
            if ev.detail:
                lines.append(f"    - detail: `{json.dumps(ev.detail, ensure_ascii=False)}`")
    else:
        lines.append("- (none)")
    lines.append("")

    lines.append(f"## Timeline ({len(events)} events)")
    lines.append("")
    lines.append("```")
    for ev in events[-200:]:
        lines.append(render_human_line(ev))
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def build_bundle(
    events_path: Path | str,
    header: dict[str, Any],
    sessions: list[dict[str, Any]],
) -> bytes:
    events_path = Path(events_path)
    events = read_jsonl(events_path) if events_path.exists() else []

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("report.md", render_report_md(events, header, sessions))
        zf.writestr(
            "runtime.json",
            json.dumps({**header, "sessions": sessions}, indent=2, ensure_ascii=False),
        )
        for candidate in sorted(events_path.parent.glob(f"{events_path.name}*")):
            if candidate.is_file():
                zf.write(candidate, f"events/{candidate.name}")
    return buf.getvalue()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_diag_bundle.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff format services/diag_bundle.py tests/test_diag_bundle.py
uv run ruff check services/diag_bundle.py tests/test_diag_bundle.py
git add services/diag_bundle.py tests/test_diag_bundle.py
git commit -m "feat(diag): bundle assembly with report.md rendering"
```

---

### Task 12: Diagnostics router

**Files:**
- Create: `services/api/diagnostics.py`
- Modify: `services/api/__init__.py` (mount the router)
- Modify: `services/api_types.py` (add `DiagEventModel`, `DiagSummary`, `VerboseState`, `ClientErrorRequest`)
- Test: `tests/test_diagnostics_router.py`

**Interfaces:**
- Consumes: `services.diagnostics`, `services.diag_bundle`, `services.runtime_info`.
- Produces: `GET /api/diagnostics/events`, `GET /api/diagnostics/summary`, `GET|PUT /api/diagnostics/verbose`, `POST /api/diagnostics/client-error`, `GET /api/diagnostics/bundle`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_diagnostics_router.py
import io
import logging
import zipfile

import pytest
from httpx import ASGITransport, AsyncClient

from services import diagnostics, logsetup
from services.api import build_app


@pytest.fixture(autouse=True)
def _bus(tmp_path, monkeypatch):
    root = logging.getLogger()
    saved = list(root.handlers)
    root.handlers.clear()
    logsetup._reset_for_tests()
    monkeypatch.setattr(logsetup, "candidate_paths", lambda: [tmp_path / "events.jsonl"])
    diagnostics.init(console=False)
    diagnostics.get_buffer().clear()
    diagnostics.set_verbose(False)
    yield
    root.handlers.clear()
    root.handlers.extend(saved)
    logsetup._reset_for_tests()


@pytest.fixture
async def client():
    app = build_app(services=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def test_events_returns_newest_first_and_filters(client):
    log = logging.getLogger("tthol.test")
    log.info("one", extra={"cat": "locate", "char_pid": 1})
    log.error("two", extra={"cat": "inventory", "char_pid": 2, "code": "E_INV_NOT_FOUND"})

    body = (await client.get("/api/diagnostics/events")).json()
    assert [e["message"] for e in body][:2] == ["two", "one"]

    filtered = (await client.get("/api/diagnostics/events?level=ERROR")).json()
    assert [e["code"] for e in filtered] == ["E_INV_NOT_FOUND"]

    by_pid = (await client.get("/api/diagnostics/events?pid=1")).json()
    assert [e["message"] for e in by_pid] == ["one"]

    by_code = (await client.get("/api/diagnostics/events?code=E_INV_NOT_FOUND")).json()
    assert len(by_code) == 1


async def test_events_since_returns_only_newer(client):
    log = logging.getLogger("tthol.test")
    log.info("first", extra={"cat": "locate"})
    first_ts = (await client.get("/api/diagnostics/events")).json()[0]["ts"]
    log.info("second", extra={"cat": "locate"})

    body = (await client.get(f"/api/diagnostics/events?since={first_ts}")).json()
    assert [e["message"] for e in body] == ["second"]


async def test_summary_carries_environment_and_counts(client):
    logging.getLogger("tthol.test").error("bad", extra={"cat": "locate"})
    body = (await client.get("/api/diagnostics/summary")).json()
    assert body["environment"]["app_version"] == "1.2.1"
    assert body["counts"]["ERROR"] >= 1
    assert "events_path" in body
    assert body["verbose"] is False


async def test_verbose_toggle_roundtrips(client):
    assert (await client.get("/api/diagnostics/verbose")).json()["verbose"] is False
    resp = await client.put("/api/diagnostics/verbose", json={"verbose": True})
    assert resp.json()["verbose"] is True
    assert diagnostics.is_verbose() is True
    assert (await client.get("/api/diagnostics/verbose")).json()["verbose"] is True


async def test_client_error_lands_on_the_same_bus(client):
    resp = await client.post(
        "/api/diagnostics/client-error",
        json={"message": "TypeError: x is not a function", "url": "/detail",
              "stack": "at Foo\nat Bar", "component": "ItemsTab"},
    )
    assert resp.status_code == 200

    events = diagnostics.get_buffer().query(cat="client")
    assert len(events) == 1
    assert events[0].code == "E_CLIENT"
    assert events[0].detail["component"] == "ItemsTab"


async def test_client_error_dedupes_within_the_window(client):
    payload = {"message": "same boom", "url": "/x"}
    for _ in range(5):
        await client.post("/api/diagnostics/client-error", json=payload)
    assert len(diagnostics.get_buffer().query(cat="client")) == 1

    await client.post("/api/diagnostics/client-error", json={"message": "different", "url": "/x"})
    assert len(diagnostics.get_buffer().query(cat="client")) == 2


async def test_bundle_is_a_zip_with_the_expected_members(client):
    logging.getLogger("tthol.test").error("bad", extra={"cat": "locate"})
    resp = await client.get("/api/diagnostics/bundle")
    assert resp.status_code == 200
    assert "attachment" in resp.headers["content-disposition"]
    assert "tthol-diag-" in resp.headers["content-disposition"]

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        names = set(zf.namelist())
        assert "report.md" in names
        assert "runtime.json" in names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_diagnostics_router.py -v`
Expected: FAIL — 404 on `/api/diagnostics/events`.

- [ ] **Step 3: Write minimal implementation**

Add to `services/api_types.py`:

```python
class DiagEventModel(_Base):
    v: int
    ts: float
    level: str
    logger: str
    pid: int | None = None
    char: str | None = None
    cat: str
    code: str | None = None
    message: str
    detail: dict | None = None


class DiagSummary(_Base):
    environment: dict
    sessions: list[dict]
    counts: dict[str, int]
    events_path: str | None = None
    verbose: bool


class VerboseState(_Base):
    verbose: bool


class ClientErrorRequest(_Base):
    message: str
    url: str | None = None
    stack: str | None = None
    component: str | None = None
    ua: str | None = None
```

Create `services/api/diagnostics.py`:

```python
"""Diagnostics endpoints: live events, summary, verbose toggle, client errors, bundle."""

from __future__ import annotations

import datetime as _dt
import logging
import threading
import time
from dataclasses import asdict

from fastapi import APIRouter, Request, Response

from services import diagnostics
from services.api_types import ClientErrorRequest, DiagEventModel, DiagSummary, VerboseState
from services.diag_bundle import build_bundle, bundle_filename
from services.diag_events import ErrorCode

router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])

client_log = logging.getLogger("tthol.client")

CLIENT_DEDUP_SECONDS = 5.0
_recent_client_errors: dict[str, float] = {}
_dedup_lock = threading.Lock()


def _should_report(message: str, now: float) -> bool:
    """Collapse identical client errors inside a short window.

    A render loop can fire the same error hundreds of times a second; without
    this it would evict the whole ring buffer.
    """
    with _dedup_lock:
        last = _recent_client_errors.get(message)
        if last is not None and now - last < CLIENT_DEDUP_SECONDS:
            return False
        _recent_client_errors[message] = now
        for key, seen in list(_recent_client_errors.items()):
            if now - seen > CLIENT_DEDUP_SECONDS * 10:
                _recent_client_errors.pop(key, None)
        return True


def _sessions(request: Request) -> list[dict]:
    wm = request.app.state.services.get("worker_manager")
    if wm is None:
        return []
    return [
        {"pid": pid, "name": sess.name, "link": sess.link}
        for pid, sess in getattr(wm, "_sessions", {}).items()
    ]


@router.get("/events", response_model=list[DiagEventModel])
async def events(
    since: float | None = None,
    level: str | None = None,
    pid: int | None = None,
    cat: str | None = None,
    code: str | None = None,
    limit: int = 200,
) -> list[DiagEventModel]:
    found = diagnostics.get_buffer().query(
        since=since, level=level, pid=pid, cat=cat, code=code, limit=limit
    )
    return [DiagEventModel(**asdict(e)) for e in found]


@router.get("/summary", response_model=DiagSummary)
async def summary(request: Request) -> DiagSummary:
    from services.logsetup import _current_path
    from services.runtime_info import environment_header

    return DiagSummary(
        environment=environment_header(),
        sessions=_sessions(request),
        counts=diagnostics.get_buffer().counts(),
        events_path=str(_current_path) if _current_path else None,
        verbose=diagnostics.is_verbose(),
    )


@router.get("/verbose", response_model=VerboseState)
async def get_verbose() -> VerboseState:
    return VerboseState(verbose=diagnostics.is_verbose())


@router.put("/verbose", response_model=VerboseState)
async def put_verbose(body: VerboseState) -> VerboseState:
    diagnostics.set_verbose(body.verbose)
    return VerboseState(verbose=diagnostics.is_verbose())


@router.post("/client-error", response_model=VerboseState)
async def client_error(body: ClientErrorRequest) -> VerboseState:
    if _should_report(body.message, time.time()):
        client_log.error(
            body.message,
            extra={
                "cat": "client",
                "code": ErrorCode.E_CLIENT,
                "detail": {
                    "url": body.url,
                    "stack": body.stack,
                    "component": body.component,
                    "ua": body.ua,
                },
            },
        )
    return VerboseState(verbose=diagnostics.is_verbose())


@router.get("/bundle")
async def bundle(request: Request) -> Response:
    from services.logsetup import _current_path
    from services.runtime_info import environment_header

    blob = build_bundle(
        events_path=_current_path or "events.jsonl",
        header=environment_header(),
        sessions=_sessions(request),
    )
    name = bundle_filename(_dt.datetime.now())
    return Response(
        content=blob,
        media_type="application/zip",
        headers={"content-disposition": f'attachment; filename="{name}"'},
    )
```

Mount it in `services/api/__init__.py`:

```python
from services.api import diagnostics as diagnostics_module
```

and alongside the other `include_router` calls:

```python
    app.include_router(diagnostics_module.router)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_diagnostics_router.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Regenerate types, run the suite, commit**

```bash
uv run pytest -q
uv run python scripts/gen_openapi.py
cd webui && npm run gen-types && cd ..
uv run ruff format services/api/diagnostics.py services/api_types.py services/api/__init__.py tests/test_diagnostics_router.py
uv run ruff check services/api/diagnostics.py services/api_types.py services/api/__init__.py tests/test_diagnostics_router.py
git add services/api/diagnostics.py services/api_types.py services/api/__init__.py tests/test_diagnostics_router.py webui/openapi.json webui/src/api/schema.ts
git commit -m "feat(diag): diagnostics router with events, summary, verbose, bundle"
```

---

### Task 13: Wire the bus into app startup

**Files:**
- Modify: `app.py`
- Test: `tests/test_app_wiring.py`

**Interfaces:**
- Consumes: `services.diagnostics.init`, `services.runtime_info.write_runtime_json` / `clear_runtime_json`.
- Produces: `app.py` no longer defines `_setup_logging`; `main()` calls `diagnostics.init()` then writes `runtime.json` once the port is known, and clears it on exit.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_app_wiring.py
import app as app_module


def test_inline_setup_logging_is_gone():
    # Logging wiring lives in services/logsetup.py so it can be tested; a
    # lingering copy here would install a second set of handlers.
    assert not hasattr(app_module, "_setup_logging")


def test_main_writes_and_clears_runtime_json(monkeypatch, tmp_path):
    import services.runtime_info as ri

    monkeypatch.setattr(ri, "RUNTIME_DIR", tmp_path)

    written: list[tuple] = []
    monkeypatch.setattr(app_module, "_write_runtime", lambda port: written.append(("w", port)))
    monkeypatch.setattr(app_module, "_clear_runtime", lambda: written.append(("c",)))

    # Drive only the runtime-pointer lifecycle, not the whole GUI.
    app_module._runtime_lifecycle(51234, lambda: None)
    assert written == [("w", 51234), ("c",)]


def test_runtime_is_cleared_even_when_the_window_raises(monkeypatch, tmp_path):
    import services.runtime_info as ri

    monkeypatch.setattr(ri, "RUNTIME_DIR", tmp_path)
    written: list[tuple] = []
    monkeypatch.setattr(app_module, "_write_runtime", lambda port: written.append(("w", port)))
    monkeypatch.setattr(app_module, "_clear_runtime", lambda: written.append(("c",)))

    def boom():
        raise RuntimeError("window died")

    try:
        app_module._runtime_lifecycle(1, boom)
    except RuntimeError:
        pass
    assert written == [("w", 1), ("c",)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_app_wiring.py -v`
Expected: FAIL — `assert not hasattr(app_module, "_setup_logging")` fails, and `_runtime_lifecycle` does not exist.

- [ ] **Step 3: Write minimal implementation**

In `app.py`, delete the whole `_setup_logging` function and the now-unused
`from logging.handlers import RotatingFileHandler` / `import logging` imports if nothing else
uses them. Add:

```python
from services import diagnostics
from services.runtime_info import clear_runtime_json, write_runtime_json
```

Add the two seams the test drives, above `main()`:

```python
def _write_runtime(port: int) -> None:
    from services.logsetup import _current_path

    write_runtime_json(port=port, events_path=_current_path or "")


def _clear_runtime() -> None:
    clear_runtime_json()


def _runtime_lifecycle(port: int, run) -> None:
    """Publish runtime.json for the life of the window, then remove it.

    The pointer must outlive startup (agents and the CLI read it while the app
    runs) and must not outlive a clean exit, so a stale file only ever means a
    crash.
    """
    _write_runtime(port)
    try:
        run()
    finally:
        _clear_runtime()
```

In `main()`, replace the `_setup_logging()` call with:

```python
    diagnostics.init(console=not getattr(sys, "frozen", False))
```

and wrap the `webview.start(**start_kwargs)` call:

```python
    _runtime_lifecycle(port, lambda: webview.start(**start_kwargs))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_app_wiring.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Smoke-test the real app and commit**

```bash
uv run app.py --dev
```

Confirm in the console that the startup header line appears, then close the window and check
the pointer was written and removed:

```bash
ls "$LOCALAPPDATA/tthol-reader/logs/events.jsonl" && head -1 "$LOCALAPPDATA/tthol-reader/logs/events.jsonl"
ls "$LOCALAPPDATA/tthol-reader/runtime.json" 2>/dev/null || echo "runtime.json cleared on exit — correct"
```

```bash
uv run ruff format app.py tests/test_app_wiring.py
uv run ruff check app.py tests/test_app_wiring.py
git add app.py tests/test_app_wiring.py
git commit -m "feat(diag): wire the logging bus and runtime pointer into startup"
```

---

### Task 14: `diag.py` CLI

**Files:**
- Create: `diag.py`
- Test: `tests/test_diag_cli.py`

**Interfaces:**
- Consumes: `services.runtime_info`, `services.diag_jsonl.read_jsonl`, `services.diag_bundle`.
- Produces: `resolve_source(args) -> tuple[str, object]`, `parse_since(text) -> float | None`, `filter_events(events, ...) -> list[DiagEvent]`, `main(argv) -> int`. Subcommands: `events`, `summary`, `tail`, `inspect`, `bundle`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_diag_cli.py
import json
import zipfile

import pytest

import diag


def _write_events(path, rows):
    path.write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n",
        encoding="utf-8",
    )


def _row(ts, level="ERROR", code="E_INV_NOT_FOUND", pid=27160, msg="m"):
    return {"v": 1, "ts": ts, "level": level, "logger": "tthol.worker", "pid": pid,
            "char": "無塵", "cat": "inventory", "code": code, "message": msg, "detail": None}


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
        diag, "read_runtime_json",
        lambda: {"events_path": str(events), "pid": 2_147_483_600, "app_version": "1.2.1",
                 "port": 51234},
    )
    monkeypatch.setattr(diag, "_app_is_live", lambda _info: False)

    assert diag.main(["summary"]) == 0
    out = capsys.readouterr().out
    assert "1.2.1" in out
    assert "ERROR" in out
    assert "not running" in out.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_diag_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'diag'`

- [ ] **Step 3: Write minimal implementation**

```python
# diag.py
"""Diagnostics CLI — the agent-facing entry point.

Source resolution: runtime.json -> live app over HTTP; if the app is not
running, its events_path on disk; or an explicit bundle. One command set
covers live, post-mortem, and someone-else's-bundle.

    uv run diag.py events --since 10m --level ERROR --json
    uv run diag.py events --code E_INV_NOT_FOUND --json
    uv run diag.py summary
    uv run diag.py tail
    uv run diag.py inspect <bundle.zip>
    uv run diag.py bundle --out <path>
"""

from __future__ import annotations

import argparse
import datetime as _dt
import io
import json
import re
import sys
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import asdict
from pathlib import Path

from services.diag_bundle import build_bundle, bundle_filename, render_human_line
from services.diag_events import DiagEvent, event_from_json_line
from services.diag_jsonl import read_jsonl
from services.runtime_info import read_runtime_json

_DURATION = re.compile(r"^(\d+)([smhd])$")
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_since(text: str | None, now: float | None = None) -> float | None:
    if text is None:
        return None
    m = _DURATION.match(text.strip())
    if not m:
        raise ValueError(f"bad --since {text!r}; expected forms like 30s, 10m, 2h, 1d")
    now = time.time() if now is None else now
    return now - int(m.group(1)) * _UNITS[m.group(2)]


def _app_is_live(info: dict) -> bool:
    port = info.get("port")
    if not port:
        return False
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=0.5) as r:
            return r.status == 200
    except Exception:
        return False


def _events_from_live(info: dict) -> list[DiagEvent]:
    url = f"http://127.0.0.1:{info['port']}/api/diagnostics/events?limit=1000"
    with urllib.request.urlopen(url, timeout=5.0) as r:
        return [DiagEvent(**row) for row in json.loads(r.read().decode("utf-8"))]


def _events_from_zip(path: Path) -> list[DiagEvent]:
    out: list[DiagEvent] = []
    with zipfile.ZipFile(path) as zf:
        for name in sorted(n for n in zf.namelist() if n.startswith("events/")):
            for line in zf.read(name).decode("utf-8", "replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(event_from_json_line(line))
                except Exception:
                    continue
    return sorted(out, key=lambda e: e.ts)


def load_events(args) -> tuple[list[DiagEvent], dict | None]:
    """Returns (events, runtime_info). Raises SystemExit(2) with a clear message."""
    target = getattr(args, "target", None)
    if target:
        return (_events_from_zip(Path(target)), None)

    info = read_runtime_json()
    if info is None:
        print(
            "no runtime.json found — start the app once, or pass a bundle:\n"
            "  uv run diag.py inspect <bundle.zip>",
            file=sys.stderr,
        )
        raise SystemExit(2)

    if _app_is_live(info):
        return (_events_from_live(info), info)
    events_path = info.get("events_path")
    if not events_path or not Path(events_path).exists():
        print(f"app is not running and {events_path!r} is missing", file=sys.stderr)
        raise SystemExit(2)
    return (read_jsonl(events_path), info)


def filter_events(
    events: list[DiagEvent],
    since: float | None = None,
    level: str | None = None,
    pid: int | None = None,
    cat: str | None = None,
    code: str | None = None,
) -> list[DiagEvent]:
    return [
        e
        for e in events
        if (since is None or e.ts > since)
        and (level is None or e.level == level)
        and (pid is None or e.pid == pid)
        and (cat is None or e.cat == cat)
        and (code is None or e.code == code)
    ]


def _emit(events: list[DiagEvent], as_json: bool) -> None:
    for ev in events:
        if as_json:
            print(json.dumps(asdict(ev), ensure_ascii=False))
        else:
            print(render_human_line(ev))


def _cmd_events(args) -> int:
    events, _ = load_events(args)
    _emit(filter_events(events, parse_since(args.since), args.level, args.pid, args.cat,
                        args.code), args.json)
    return 0


def _cmd_inspect(args) -> int:
    return _cmd_events(args)


def _cmd_summary(args) -> int:
    events, info = load_events(args)
    counts: dict[str, int] = {}
    for e in events:
        counts[e.level] = counts.get(e.level, 0) + 1

    if info:
        live = _app_is_live(info)
        print(f"app:        {'running' if live else 'not running'} (pid {info.get('pid')})")
        print(f"version:    {info.get('app_version')}")
        print(f"port:       {info.get('port')}")
        print(f"events:     {info.get('events_path')}")
        print(f"chain:      static={info.get('static_base')} player={info.get('player_hp_chain_base')}")
    print(f"events:     {len(events)}")
    for level in ("ERROR", "WARNING", "INFO", "DEBUG"):
        if level in counts:
            print(f"  {level:<8} {counts[level]}")
    return 0


def _cmd_tail(args) -> int:
    seen = 0.0
    try:
        while True:
            events, _ = load_events(args)
            fresh = [e for e in events if e.ts > seen]
            if fresh:
                seen = max(e.ts for e in fresh)
                _emit(fresh, args.json)
            time.sleep(2.0)
    except KeyboardInterrupt:
        return 0


def _cmd_bundle(args) -> int:
    info = read_runtime_json()
    if info is None:
        print("no runtime.json found — start the app once first", file=sys.stderr)
        return 2
    blob = build_bundle(
        events_path=info.get("events_path") or "events.jsonl",
        header={k: v for k, v in info.items() if k != "sessions"},
        sessions=info.get("sessions", []),
    )
    out = Path(args.out) if args.out else Path(bundle_filename(_dt.datetime.now()))
    out.write_bytes(blob)
    print(str(out))
    return 0


def _add_filters(p: argparse.ArgumentParser) -> None:
    p.add_argument("--since", help="relative window, e.g. 30s / 10m / 2h / 1d")
    p.add_argument("--level", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    p.add_argument("--pid", type=int)
    p.add_argument("--cat")
    p.add_argument("--code")
    p.add_argument("--json", action="store_true", help="NDJSON to stdout")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="diag.py", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_events = sub.add_parser("events", help="print recorded events")
    _add_filters(p_events)
    p_events.set_defaults(func=_cmd_events, target=None)

    p_summary = sub.add_parser("summary", help="environment and event counts")
    p_summary.set_defaults(func=_cmd_summary, target=None, since=None, level=None, pid=None,
                           cat=None, code=None, json=False)

    p_tail = sub.add_parser("tail", help="follow new events")
    _add_filters(p_tail)
    p_tail.set_defaults(func=_cmd_tail, target=None)

    p_inspect = sub.add_parser("inspect", help="read events out of a bundle zip")
    p_inspect.add_argument("target")
    _add_filters(p_inspect)
    p_inspect.set_defaults(func=_cmd_inspect)

    p_bundle = sub.add_parser("bundle", help="write a diagnostic bundle")
    p_bundle.add_argument("--out")
    p_bundle.set_defaults(func=_cmd_bundle)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except SystemExit as exc:
        return int(exc.code or 0)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_diag_cli.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Exercise it against the real app and commit**

With the app running from Task 13:

```bash
uv run diag.py summary
uv run diag.py events --level ERROR --json
```

```bash
uv run ruff format diag.py tests/test_diag_cli.py
uv run ruff check diag.py tests/test_diag_cli.py
git add diag.py tests/test_diag_cli.py
git commit -m "feat(diag): diag.py CLI over live, post-mortem, and bundle sources"
```

---

### Task 15: `/tthol-diag` triage skill

**Files:**
- Create: `.claude/commands/tthol-diag.md`
- Test: `tests/test_diag_skill_doc.py`

**Interfaces:**
- Consumes: the error-code table from Task 1 and the CLI from Task 14.
- Produces: the skill document. The test guards it against drift — a code added to `ErrorCode` with no entry in the skill is a documentation bug that the suite catches.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_diag_skill_doc.py
from pathlib import Path

from services.diag_events import ErrorCode

SKILL = Path(".claude/commands/tthol-diag.md")


def test_skill_exists():
    assert SKILL.exists()


def test_every_error_code_is_documented():
    text = SKILL.read_text(encoding="utf-8")
    codes = [n for n in dir(ErrorCode) if n.startswith("E_")]
    missing = [c for c in codes if c not in text]
    assert missing == [], f"undocumented error codes: {missing}"


def test_skill_uses_uv_run_for_every_command():
    text = SKILL.read_text(encoding="utf-8")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("diag.py") or stripped.startswith("python diag.py"):
            raise AssertionError(f"command must be invoked via `uv run`: {stripped}")
    assert "uv run diag.py" in text


def test_skill_points_at_the_escalation_path():
    text = SKILL.read_text(encoding="utf-8")
    assert "tthol-update-scan" in text
    assert "runtime.json" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_diag_skill_doc.py -v`
Expected: FAIL — `assert SKILL.exists()`

- [ ] **Step 3: Write minimal implementation**

Create `.claude/commands/tthol-diag.md`:

````markdown
---
description: Triage a tthol-reader failure report from its diagnostic events
---

# tthol-diag

Diagnose a reported failure using the app's own event record. Works against a
running app, a closed one, or a bundle zip a user sent.

## 1. Orient

```bash
uv run diag.py summary
```

This prints whether the app is running, its version, the `events.jsonl` path,
the pointer-chain constants, and event counts by level. If it reports
`no runtime.json found`, the app has never been started on this machine — ask
for a bundle instead and use `uv run diag.py inspect <bundle.zip>`.

**Check the pointer chain first.** If `player_hp_chain_base` differs from the
value in `reader.py`, the game has been updated and the constants are stale.
Stop here and run `/tthol-update-scan` — no amount of event reading will fix
that. (`static_base` reads `None` on current builds: the session static chain
was removed from `reader.py` and only the player HP chain remains. That is
expected, not a fault.)

## 2. Find the failure

```bash
uv run diag.py events --level ERROR --since 1h --json
```

Every failure carries a stable `code`. Match on `code`, never on `message` —
message text is prose and changes between versions.

## 3. Read the code

| Code | Meaning | Read in `detail` | Usual cause |
|---|---|---|---|
| `E_PROC_GONE` | Cannot attach to the game process | `pid`, `exc` | Game closed, or the reader lacks rights to open the process |
| `E_CHAIN_READ` | The player HP pointer chain did not resolve | `exc`, `chain_base`, `chain_offsets` | Not logged into a character yet, or the chain constants are stale after a game update |
| `E_LOCATE_EXHAUSTED` | Locate retried to its bound and gave up | the full snapshot: `chain_hp`, `compat_false`, `compat_true`, `bytes_hex`, `score` | See the decision tree below |
| `E_LOCK_LOST` | Validation score fell below 0.8 three times | `score`, `failed_fields`, `hp_addr` | The character struct moved (map change). Normal in ones and twos; a `relocated N times in the last 60s` line means something worse |
| `E_SCAN_FAILED` | A scan raised | `exc`, `hp_value`, `compat_tried` | Usually a read against freed memory |
| `E_INV_NOT_FOUND` | Inventory pattern not found | `hp_addr`, `scan_ms` | The scan returns an empty list on this path, so the UI shows "no items". An empty inventory and an unscannable one look identical without this code — that is the whole reason it exists |
| `E_WH_NOT_FOUND` | No warehouse slot array found | `hp_addr`, `inv_range`, `arrays_seen` | The warehouse UI was not open in game; the structure only exists while it is |
| `E_API_5XX` | An endpoint raised | `path`, `method`, `status`, `traceback` | A real backend bug — read the traceback |
| `E_CLIENT` | A browser-side error | `url`, `stack`, `component`, `ua` | Frontend bug; correlate by timestamp with backend events |

## 4. Decision tree for `E_LOCATE_EXHAUSTED`

Read `detail.bytes_hex` — the first 32 bytes at the last known address:

- Starts `cdcdcdcd` — the block was freed. The struct moved; normal during a
  map change, a problem if it repeats.
- Starts `fdfdfdfd` — the game was restarted. The session is stale; the
  UI's 重偵 button rebuilds it.
- Anything else — read `detail.chain_hp`:
  - An integer, but both `compat_false` and `compat_true` are `null` → the HP
    value is right but no candidate passed structure validation. Suspect
    `knowledge.json` drift; compare `knowledge_sha8` in the summary against
    the repo.
  - A string starting `<` (a captured exception) → the chain read itself
    failed. Treat as `E_CHAIN_READ`.
  - `null` and `hp_value` is also `null` → the user never supplied an HP value
    and the chain was unavailable. Expected before login.

## 5. Correlate frontend and backend

Both land on one timeline, so a client error and its backend cause sit
adjacent:

```bash
uv run diag.py events --since 10m
```

## 6. Get more detail

If the events are too sparse, have the user turn on 詳細記錄 on the 脈案 page
(or `PUT /api/diagnostics/verbose`), reproduce, and export again. Verbose mode
raises only the `tthol` logger to DEBUG and resets to INFO on restart.

## Notes

- Every command runs through `uv run`. Never bare `python`.
- `runtime.json` lives at `%LOCALAPPDATA%\tthol-reader\runtime.json` and always
  at that path, whatever the fallback chose for `events.jsonl`.
- A `runtime.json` whose `pid` is not alive is stale — the app crashed rather
  than exiting cleanly, which is itself a finding.
````

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_diag_skill_doc.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add .claude/commands/tthol-diag.md tests/test_diag_skill_doc.py
git commit -m "docs(diag): /tthol-diag triage skill with drift-guarding test"
```

---

### Task 16: Typed API errors and the client reporting channel

**Files:**
- Modify: `webui/src/api/client.ts`
- Create: `webui/src/diag/report.ts`
- Modify: `webui/src/main.tsx`

**Interfaces:**
- Consumes: `POST /api/diagnostics/client-error` from Task 12.
- Produces: `ApiError` (class, exported from `client.ts`, fields `status: number`, `detail?: string`, `path: string`); `reportClientError(err: unknown, ctx?: {component?: string; silent?: boolean}): void` from `diag/report.ts`; `installGlobalErrorHooks(): void`.

- [ ] **Step 1: Rewrite `client.ts` to carry the server's detail**

The four helpers currently throw `new Error(`${path}: ${r.status}`)`, discarding the response
body — which is why the UI can only show `Error: /api/xxx: 500`.

```typescript
// webui/src/api/client.ts
const base = '';  // same-origin via Vite proxy or pywebview

export class ApiError extends Error {
  readonly status: number;
  readonly detail?: string;
  readonly path: string;

  constructor(path: string, status: number, detail?: string) {
    super(detail ? `${path}: ${status} — ${detail}` : `${path}: ${status}`);
    this.name = 'ApiError';
    this.path = path;
    this.status = status;
    this.detail = detail;
  }
}

// Both HTTPException and the global 500 handler reply as {"detail": ...},
// so one parse path covers every error response.
async function fail(path: string, r: Response): Promise<never> {
  let detail: string | undefined;
  try {
    const body = await r.json();
    if (body && typeof body.detail === 'string') detail = body.detail;
  } catch { /* non-JSON body (e.g. a proxy error page) — status alone will do */ }
  throw new ApiError(path, r.status, detail);
}

export async function get<T>(path: string): Promise<T> {
  const r = await fetch(`${base}${path}`);
  if (!r.ok) return fail(path, r);
  return r.json() as Promise<T>;
}

export async function post<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(`${base}${path}`, {
    method: 'POST',
    headers: body !== undefined ? { 'content-type': 'application/json' } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) return fail(path, r);
  return r.json() as Promise<T>;
}

export async function del<T>(path: string): Promise<T> {
  const r = await fetch(`${base}${path}`, { method: 'DELETE' });
  if (!r.ok) return fail(path, r);
  return r.json() as Promise<T>;
}

export async function upload<T>(path: string, file: File): Promise<T> {
  // Multipart upload; let the browser set the boundary content-type itself.
  const form = new FormData();
  form.append('file', file);
  const r = await fetch(`${base}${path}`, { method: 'POST', body: form });
  if (!r.ok) return fail(path, r);
  return r.json() as Promise<T>;
}

export function openWorldSocket(onFrame: (snap: unknown) => void): WebSocket {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(`${proto}//${location.host}/ws/world`);
  ws.onmessage = (e) => onFrame(JSON.parse(e.data));
  return ws;
}
```

- [ ] **Step 2: Create the reporting channel**

```typescript
// webui/src/diag/report.ts
import { ApiError } from '../api/client';

const DEDUP_MS = 5_000;
const recent = new Map<string, number>();

export function describeError(err: unknown): string {
  if (err instanceof ApiError) return err.detail ? `${err.detail} (${err.status})` : err.message;
  if (err instanceof Error) return err.message;
  return String(err);
}

/**
 * Send an error to the backend so it joins the same timeline as backend events.
 * `silent` marks call sites that intentionally ignore the failure in the UI —
 * they still report, so the record is complete.
 */
export function reportClientError(
  err: unknown,
  ctx: { component?: string; silent?: boolean } = {},
): void {
  const message = describeError(err);
  const now = Date.now();
  const last = recent.get(message);
  // Dedup client-side too: a render loop must not flood the network before
  // the server-side window even sees it.
  if (last !== undefined && now - last < DEDUP_MS) return;
  recent.set(message, now);

  const payload = {
    message,
    url: location.hash || location.pathname,
    stack: err instanceof Error ? err.stack ?? null : null,
    component: ctx.component ?? null,
    ua: navigator.userAgent,
  };
  // Deliberately not awaited and never rethrows: reporting a failure must not
  // become a second failure.
  fetch('/api/diagnostics/client-error', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(payload),
  }).catch(() => { /* backend unreachable; nothing more we can do here */ });
}

export function installGlobalErrorHooks(): void {
  window.addEventListener('error', (e) => {
    reportClientError(e.error ?? e.message, { component: 'window.onerror' });
  });
  window.addEventListener('unhandledrejection', (e) => {
    reportClientError(e.reason, { component: 'unhandledrejection' });
  });
}
```

- [ ] **Step 3: Install the hooks at startup**

In `webui/src/main.tsx`, add the import and call it before rendering:

```typescript
import { installGlobalErrorHooks } from './diag/report';

installGlobalErrorHooks();
```

- [ ] **Step 4: Verify it type-checks and reports end to end**

```bash
cd webui && npx tsc --noEmit && cd ..
```
Expected: no errors.

Then with `uv run app.py --dev` and `npm run dev` running, open the devtools console in the
app window and run `Promise.reject(new Error('probe from console'))`. Confirm the backend saw it:

```bash
uv run diag.py events --code E_CLIENT --json
```
Expected: one event whose `message` contains `probe from console`.

- [ ] **Step 5: Commit**

```bash
git add webui/src/api/client.ts webui/src/diag/report.ts webui/src/main.tsx
git commit -m "feat(webui): typed ApiError and client error reporting channel"
```

---

### Task 17: Error boundary and the swallowed catches

**Files:**
- Create: `webui/src/components/ErrorBoundary.tsx`
- Modify: `webui/src/App.tsx`
- Modify: `webui/src/pages/CharDetail/BodyTab.tsx`, `webui/src/pages/CharDetail/ItemsTab.tsx`, `webui/src/pages/CharDetail/AutoClickTab.tsx`, `webui/src/pages/CharDetail/KeepActiveTab.tsx`, `webui/src/pages/Snapshots.tsx`

**Interfaces:**
- Consumes: `reportClientError`, `describeError` from Task 16.
- Produces: `<ErrorBoundary component="...">` wrapping `<main>`.

**Catch-site map:**

| Site | Today | Becomes |
|---|---|---|
| `BodyTab.tsx:20` | `.catch(() => {})` | report + show the message |
| `ItemsTab.tsx:23` | `.catch(() => {})` | report + show the message |
| `Snapshots.tsx:16` | `.catch(() => {})` | report + show the message |
| `AutoClickTab.tsx:28` | `catch { /* worker may be gone */ }` | `reportClientError(e, {silent: true})` — the ignore is intentional; keep the UX, gain the record |
| `KeepActiveTab.tsx:20` | `catch { /* manager missing off-Windows */ }` | `reportClientError(e, {silent: true})` — same |
| `Dashboard.tsx:15` | `console.warn('rescan failed', e)` | report + toast — a failed 重偵 is exactly the moment a user gives up and files a report |
| `useLiveChars.ts:18` | `console.warn('initial /api/world failed', e)` | `reportClientError(e, {silent: true})` — the WS retry already covers the UX |

A `console.warn` is no better than a swallowed catch here: the WebView2 console
is not visible to the user and is not captured in the bundle.

- [ ] **Step 1: Create the boundary**

```tsx
// webui/src/components/ErrorBoundary.tsx
import { Component, type ErrorInfo, type ReactNode } from 'react';
import { reportClientError } from '../diag/report';

type Props = { component: string; children: ReactNode };
type State = { message: string | null };

export class ErrorBoundary extends Component<Props, State> {
  state: State = { message: null };

  static getDerivedStateFromError(err: unknown): State {
    return { message: err instanceof Error ? err.message : String(err) };
  }

  componentDidCatch(err: Error, info: ErrorInfo): void {
    reportClientError(err, { component: `${this.props.component}${info.componentStack ?? ''}` });
  }

  render(): ReactNode {
    if (this.state.message === null) return this.props.children;
    return (
      <div style={{ padding: 24, fontFamily: 'var(--tt-font)' }}>
        <div style={{
          fontFamily: 'var(--tt-font-serif)', fontSize: 16, letterSpacing: 4,
          color: 'var(--tt-bad)', marginBottom: 10,
        }}>
          此頁出錯
        </div>
        <div style={{ color: 'var(--tt-text)', fontSize: 13, marginBottom: 14 }}>
          {this.state.message}
        </div>
        <div style={{ color: 'var(--tt-dim)', fontSize: 12, marginBottom: 14 }}>
          錯誤已記錄，可到「脈案」分頁匯出診斷包。
        </div>
        <button onClick={() => this.setState({ message: null })}>重試</button>
      </div>
    );
  }
}
```

- [ ] **Step 2: Wrap `<main>` in `App.tsx`**

Add the import and wrap the existing `<main>` contents:

```tsx
import { ErrorBoundary } from './components/ErrorBoundary';
```

```tsx
      <main style={{ flex: 1 }}>
        <ErrorBoundary component={page}>
          {/* existing page switch, unchanged */}
        </ErrorBoundary>
      </main>
```

- [ ] **Step 3: Replace the swallowed catches**

`BodyTab.tsx` — replace the line-20 chain:

```tsx
      get<Detail>(`/api/characters/${pid}`)
        .then(x => { if (alive) setD(x); })
        .catch(e => { if (alive) { setErr(describeError(e)); reportClientError(e, { component: 'BodyTab' }); } });
```

Add `const [err, setErr] = useState<string | null>(null);` and render it where the panel body
goes, using `var(--tt-bad)` for the text:

```tsx
      {err && <div style={{ color: 'var(--tt-bad)', fontSize: 12 }}>讀取失敗：{err}</div>}
```

`ItemsTab.tsx` — replace the line-23 `.catch(() => {})`:

```tsx
      .catch(e => { setToast(`讀取失敗：${describeError(e)}`); reportClientError(e, { component: 'ItemsTab' }); });
```

and change the two existing `String(e)` toasts on lines 35 and 49 to `describeError(e)`, adding
`reportClientError(e, { component: 'ItemsTab' });` in each block.

`Snapshots.tsx` — replace the line-16 `.catch(() => {})`:

```tsx
    () => get<SnapshotRow[]>('/api/snapshots')
      .then(setRows)
      .catch(e => { setErr(describeError(e)); reportClientError(e, { component: 'Snapshots' }); }),
```

`AutoClickTab.tsx` line 28 and `KeepActiveTab.tsx` line 20 — keep the silent UX, add the record:

```tsx
    } catch (e) {
      // Worker may be gone / manager absent off-Windows: intentionally not
      // surfaced, but still recorded so the timeline is complete.
      reportClientError(e, { component: 'AutoClickTab', silent: true });
    }
```

Add to each modified file:

```tsx
import { describeError, reportClientError } from '../../diag/report';
```
(`Snapshots.tsx` uses `'../diag/report'` — it sits one level shallower.)

- [ ] **Step 4: Replace the two `console.warn` sites**

`Dashboard.tsx:15` — a failed 重偵 is the moment a user gives up and reports a
problem, so it must not vanish into a console nobody reads:

```tsx
    } catch (e) {
      setRescanError(describeError(e));
      reportClientError(e, { component: 'Dashboard.rescan' });
    } finally {
```

Add `const [rescanError, setRescanError] = useState<string | null>(null);` beside the
existing `rescanning` state, and render it under the panel header:

```tsx
        {rescanError && (
          <div role="status" style={{ color: 'var(--tt-bad)', fontSize: 12, marginBottom: 8 }}>
            重偵失敗：{rescanError}
          </div>
        )}
```

`useLiveChars.ts:18` — the WebSocket retry already covers the user experience, so this
one stays silent but still records:

```tsx
      } catch (e) {
        // The WS reconnect loop below covers the UX; record it so a "nothing
        // loads" report has a first cause in the timeline.
        reportClientError(e, { component: 'useLiveChars', silent: true });
      }
```

with `import { reportClientError } from '../diag/report';` at the top, and
`import { describeError, reportClientError } from '../diag/report';` in `Dashboard.tsx`.

- [ ] **Step 5: Type-check and verify in the app**

```bash
cd webui && npx tsc --noEmit && cd ..
```
Expected: no errors.

With the app running, stop the backend mid-session and click into 身家 (BodyTab). Confirm a
readable failure message appears rather than a blank panel.

- [ ] **Step 6: Commit**

```bash
git add webui/src/components/ErrorBoundary.tsx webui/src/App.tsx webui/src/pages/CharDetail/BodyTab.tsx webui/src/pages/CharDetail/ItemsTab.tsx webui/src/pages/CharDetail/AutoClickTab.tsx webui/src/pages/CharDetail/KeepActiveTab.tsx webui/src/pages/Snapshots.tsx webui/src/pages/Dashboard.tsx webui/src/hooks/useLiveChars.ts
git commit -m "feat(webui): error boundary; stop swallowing fetch failures"
```

---

### Task 18: The 脈案 diagnostics page

**Files:**
- Create: `webui/src/pages/Diagnostics.tsx`
- Modify: `webui/src/components/TopNav.tsx`
- Modify: `webui/src/App.tsx`
- Modify: `webui/src/api/types.ts`

**Interfaces:**
- Consumes: `GET /api/diagnostics/events`, `/summary`, `GET|PUT /api/diagnostics/verbose`, `GET /api/diagnostics/bundle`.
- Produces: `<Diagnostics />`; `PageKey` gains `'diagnostics'`.

**Design constraints (from the spec):**
- Level is never colour-only: each row carries a text marker `錯` / `警` / `訊` / `詳`.
- Body text `var(--tt-text)`; secondary `var(--tt-dim)` (6.3:1). Never `var(--tt-mute)` (3.3:1) for body text.
- The export toast uses `role="status"`. The event list gets **no** `aria-live` — a 2s cadence would flood a screen reader.
- Render 200 rows by default with a "載入更多" control; no virtual-list dependency.
- Poll `?since=<last_ts>` every 2s while mounted; stop on unmount.

- [ ] **Step 1: Add the type aliases**

In `webui/src/api/types.ts`, keeping the list alphabetical:

```typescript
export type ClientErrorRequest = S['ClientErrorRequest'];
export type DiagEventModel = S['DiagEventModel'];
export type DiagSummary = S['DiagSummary'];
export type VerboseState = S['VerboseState'];
```

- [ ] **Step 2: Build the page**

```tsx
// webui/src/pages/Diagnostics.tsx
import { useCallback, useEffect, useRef, useState } from 'react';
import { get, put } from '../api/client';
import { describeError, reportClientError } from '../diag/report';
import { Panel } from '../primitives';
import type { DiagEventModel, DiagSummary } from '../api/types';

const POLL_MS = 2_000;
const PAGE_SIZE = 200;

// Level is conveyed by a text marker as well as colour: colour alone fails
// users who cannot distinguish it.
const MARK: Record<string, { text: string; color: string }> = {
  ERROR:   { text: '錯', color: 'var(--tt-bad)' },
  WARNING: { text: '警', color: 'var(--tt-warn)' },
  INFO:    { text: '訊', color: 'var(--tt-dim)' },
  DEBUG:   { text: '詳', color: 'var(--tt-dim)' },
};

function ts(v: number): string {
  return new Date(v * 1000).toLocaleTimeString('zh-TW', { hour12: false });
}

export function Diagnostics() {
  const [summary, setSummary] = useState<DiagSummary | null>(null);
  const [events, setEvents] = useState<DiagEventModel[]>([]);
  const [level, setLevel] = useState<string>('');
  const [pid, setPid] = useState<string>('');
  const [query, setQuery] = useState('');
  const [shown, setShown] = useState(PAGE_SIZE);
  const [toast, setToast] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const lastTs = useRef(0);

  const loadSummary = useCallback(() => {
    get<DiagSummary>('/api/diagnostics/summary')
      .then(setSummary)
      .catch(e => reportClientError(e, { component: 'Diagnostics', silent: true }));
  }, []);

  useEffect(() => {
    let alive = true;
    loadSummary();

    const pull = () => {
      const since = lastTs.current ? `?since=${lastTs.current}` : '';
      get<DiagEventModel[]>(`/api/diagnostics/events${since}`)
        .then(fresh => {
          if (!alive || fresh.length === 0) return;
          lastTs.current = Math.max(lastTs.current, ...fresh.map(e => e.ts));
          setEvents(prev => [...fresh, ...prev]);
        })
        .catch(e => reportClientError(e, { component: 'Diagnostics', silent: true }));
    };
    pull();
    const timer = setInterval(pull, POLL_MS);
    return () => { alive = false; clearInterval(timer); };
  }, [loadSummary]);

  const toggleVerbose = async () => {
    if (!summary) return;
    setBusy(true);
    try {
      const next = await put<{ verbose: boolean }>('/api/diagnostics/verbose', {
        verbose: !summary.verbose,
      });
      setSummary({ ...summary, verbose: next.verbose });
      setToast(next.verbose ? '詳細記錄已開啟，請重現問題後再匯出' : '詳細記錄已關閉');
    } catch (e) {
      setToast(`切換失敗：${describeError(e)}`);
      reportClientError(e, { component: 'Diagnostics' });
    } finally {
      setBusy(false);
    }
  };

  const filtered = events.filter(e =>
    (!level || e.level === level) &&
    (!pid || String(e.pid ?? '') === pid) &&
    (!query || e.message.includes(query) || (e.code ?? '').includes(query)),
  );
  const visible = filtered.slice(0, shown);
  const pids = Array.from(new Set(events.map(e => e.pid).filter((p): p is number => p != null)));

  return (
    <div style={{ padding: 18, display: 'grid', gap: 14 }}>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 300px', gap: 14 }}>
        <Panel title="環境">
          {summary ? (
            <dl style={{
              margin: 0, display: 'grid', gridTemplateColumns: 'auto 1fr',
              gap: '4px 14px', fontSize: 12, fontFamily: 'var(--tt-font-mono)',
            }}>
              <dt style={{ color: 'var(--tt-dim)' }}>版本</dt>
              <dd style={{ margin: 0, color: 'var(--tt-text)' }}>{summary.environment.app_version}</dd>
              <dt style={{ color: 'var(--tt-dim)' }}>紀錄檔</dt>
              <dd style={{ margin: 0, color: 'var(--tt-text)', wordBreak: 'break-all' }}>
                {summary.events_path ?? '(記憶體暫存，未落地)'}
              </dd>
              <dt style={{ color: 'var(--tt-dim)' }}>靜態鏈</dt>
              <dd style={{ margin: 0, color: 'var(--tt-text)' }}>{summary.environment.static_base}</dd>
              <dt style={{ color: 'var(--tt-dim)' }}>知識庫</dt>
              <dd style={{ margin: 0, color: 'var(--tt-text)' }}>{summary.environment.knowledge_sha8}</dd>
              <dt style={{ color: 'var(--tt-dim)' }}>連線角色</dt>
              <dd style={{ margin: 0, color: 'var(--tt-text)' }}>
                {summary.sessions.length === 0
                  ? '(無)'
                  : summary.sessions.map(s => `${s.name ?? s.pid} (${s.link})`).join('、')}
              </dd>
            </dl>
          ) : (
            <div style={{ color: 'var(--tt-dim)', fontSize: 12 }}>載入中…</div>
          )}
        </Panel>

        <Panel title="操作">
          <div style={{ display: 'grid', gap: 10 }}>
            <button
              onClick={toggleVerbose}
              disabled={busy || !summary}
              className={summary?.verbose ? 'is-active' : undefined}
            >
              詳細記錄：{summary?.verbose ? '開' : '關'}
            </button>
            <a
              href="/api/diagnostics/bundle"
              download
              style={{
                display: 'block', textAlign: 'center', padding: '6px 14px',
                border: '1px solid var(--tt-line)', color: 'var(--tt-text)',
                textDecoration: 'none', fontSize: 13, letterSpacing: 2,
                cursor: 'pointer',
              }}
            >
              匯出診斷包
            </a>
            <div style={{ fontSize: 11, color: 'var(--tt-dim)', lineHeight: 1.6 }}>
              包含：錯誤紀錄、角色名稱與座標、道具清單、程式版本與安裝路徑。
              僅在你主動匯出時產生，程式不會自行上傳。
            </div>
          </div>
        </Panel>
      </div>

      <Panel title={`事件（${filtered.length}）`}>
        <div style={{ display: 'flex', gap: 8, marginBottom: 10, flexWrap: 'wrap' }}>
          <select value={level} onChange={e => setLevel(e.target.value)}>
            <option value="">全部等級</option>
            <option value="ERROR">錯誤</option>
            <option value="WARNING">警告</option>
            <option value="INFO">訊息</option>
            <option value="DEBUG">詳細</option>
          </select>
          <select value={pid} onChange={e => setPid(e.target.value)}>
            <option value="">全部角色</option>
            {pids.map(p => <option key={p} value={String(p)}>pid {p}</option>)}
          </select>
          <input
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="搜尋訊息或錯誤碼"
            aria-label="搜尋訊息或錯誤碼"
            style={{
              flex: 1, minWidth: 160, background: 'var(--tt-bg)',
              border: '1px solid var(--tt-line)', color: 'var(--tt-text)',
              padding: '6px 10px', fontSize: 12,
            }}
          />
        </div>

        <div style={{ maxHeight: 380, overflowY: 'auto', overflowX: 'auto' }}>
          {visible.length === 0 ? (
            <div style={{ color: 'var(--tt-dim)', fontSize: 12, padding: 8 }}>目前沒有事件。</div>
          ) : visible.map((e, i) => {
            const mark = MARK[e.level] ?? MARK.INFO;
            return (
              <div key={`${e.ts}-${i}`} style={{
                display: 'grid', gridTemplateColumns: '24px 72px 1fr',
                gap: 10, padding: '5px 4px', fontSize: 12,
                borderBottom: '1px solid var(--tt-line-soft)',
                fontFamily: 'var(--tt-font-mono)',
              }}>
                <span style={{ color: mark.color, fontFamily: 'var(--tt-font-serif)' }}>
                  {mark.text}
                </span>
                <span style={{ color: 'var(--tt-dim)' }}>{ts(e.ts)}</span>
                <span style={{ color: 'var(--tt-text)', wordBreak: 'break-word' }}>
                  {e.code && (
                    <code style={{ color: mark.color, marginRight: 8 }}>{e.code}</code>
                  )}
                  {e.char && <span style={{ color: 'var(--tt-dim)' }}>[{e.char}] </span>}
                  {e.message}
                  {e.detail && (
                    <details style={{ marginTop: 4 }}>
                      <summary style={{ color: 'var(--tt-dim)', cursor: 'pointer' }}>詳情</summary>
                      <pre style={{
                        margin: '4px 0 0', whiteSpace: 'pre-wrap', wordBreak: 'break-all',
                        color: 'var(--tt-dim)', fontSize: 11,
                      }}>{JSON.stringify(e.detail, null, 2)}</pre>
                    </details>
                  )}
                </span>
              </div>
            );
          })}
        </div>

        {filtered.length > shown && (
          <button style={{ marginTop: 10 }} onClick={() => setShown(s => s + PAGE_SIZE)}>
            載入更多（尚有 {filtered.length - shown} 筆）
          </button>
        )}
      </Panel>

      {toast && (
        <div role="status" style={{
          position: 'fixed', bottom: 18, right: 18, padding: '10px 16px',
          background: 'var(--tt-raised)', border: '1px solid var(--tt-gold)',
          color: 'var(--tt-text)', fontSize: 12,
        }}>
          {toast}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Add `put` to the API client**

`client.ts` from Task 16 has no `put`. Add it next to `post`:

```typescript
export async function put<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(`${base}${path}`, {
    method: 'PUT',
    headers: body !== undefined ? { 'content-type': 'application/json' } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) return fail(path, r);
  return r.json() as Promise<T>;
}
```

- [ ] **Step 4: Add the tab and fix the version string**

In `webui/src/components/TopNav.tsx`:

```typescript
export type PageKey = 'dashboard' | 'treasury' | 'snapshots' | 'diagnostics' | 'detail';
```

```typescript
  const tabs: { k: PageKey; n: string }[] = [
    { k: 'dashboard',   n: '江湖一覽' },
    { k: 'treasury',    n: '帳房' },
    { k: 'snapshots',   n: '留影' },
    { k: 'diagnostics', n: '脈案' },
  ];
```

Replace the hardcoded version (F9 — it reads `v0.7.2` while the app is `1.2.1`, so users
report a version that has not existed for five releases). Add at the top of the component:

```typescript
  const [version, setVersion] = useState<string>('');
  useEffect(() => {
    get<DiagSummary>('/api/diagnostics/summary')
      .then(s => setVersion(s.environment.app_version as string))
      .catch(() => { /* header cosmetics only; the diagnostics page reports the real failure */ });
  }, []);
```

with imports `import { useEffect, useState } from 'react';`, `import { get } from '../api/client';`,
`import type { DiagSummary } from '../api/types';`, and change the subtitle line to:

```tsx
          <div style={{ fontSize: 10, color: 'var(--tt-mute)', letterSpacing: 2 }}>
            tthol memory reader{version ? ` · v${version}` : ''}
          </div>
```

- [ ] **Step 5: Route the page in `App.tsx`**

```tsx
import { Diagnostics } from './pages/Diagnostics';
```

```tsx
        {page === 'diagnostics' && <Diagnostics />}
```

- [ ] **Step 6: Type-check, verify visually, commit**

```bash
cd webui && npx tsc --noEmit && npm run build && cd ..
```
Expected: no errors, build succeeds.

With `uv run app.py --dev` plus `npm run dev`, open 脈案 and confirm: the startup event is
listed, the version in the header reads `1.2.1`, the verbose toggle flips, and 匯出診斷包
downloads a zip through WebView2's flyout.

```bash
git add webui/src/pages/Diagnostics.tsx webui/src/components/TopNav.tsx webui/src/App.tsx webui/src/api/client.ts webui/src/api/types.ts
git commit -m "feat(webui): 脈案 diagnostics page; report the real app version"
```

---

### Task 19: Surface `last_error` on the dashboard

**Files:**
- Modify: `webui/src/pages/Dashboard.tsx`

**Interfaces:**
- Consumes: `CharacterRow.last_error` from Task 7, delivered over `/ws/world`.
- Produces: nothing downstream.

This closes the loop for F10: the user sees "倉庫請先在遊戲中開啟" on the card itself and can
self-serve, instead of reading an empty item list and reporting "獲取資料失敗".

- [ ] **Step 1: Render the error on the character card**

In `webui/src/pages/Dashboard.tsx`, inside the card body for each `CharacterRow c`:

```tsx
        {c.last_error && (
          <div style={{
            marginTop: 8, padding: '6px 8px',
            border: '1px solid var(--tt-bad)',
            color: 'var(--tt-text)', fontSize: 11, lineHeight: 1.5,
          }}>
            <span style={{
              color: 'var(--tt-bad)', fontFamily: 'var(--tt-font-serif)', marginRight: 6,
            }}>錯</span>
            {friendlyError(c.last_error)}
          </div>
        )}
```

- [ ] **Step 2: Add the message mapper**

At the bottom of the same file — codes are stable, so mapping on `code` rather than on
message text keeps the copy independent of the backend's prose:

```tsx
function friendlyError(e: NonNullable<CharacterRow['last_error']>): string {
  switch (e.code) {
    case 'E_WH_NOT_FOUND':
      return '倉庫尚未開啟 — 請先在遊戲中打開倉庫視窗';
    case 'E_INV_NOT_FOUND':
      return '找不到背包資料 — 可換張地圖後按「↻ 重偵」';
    case 'E_LOCATE_EXHAUSTED':
      return '找不到角色 — 請確認已登入，或按「↻ 重偵」';
    case 'E_PROC_GONE':
      return '無法連上遊戲程式 — 遊戲可能已關閉';
    case 'E_CHAIN_READ':
      return '尚未登入角色';
    default:
      return e.message;
  }
}
```

- [ ] **Step 3: Type-check and verify**

```bash
cd webui && npx tsc --noEmit && cd ..
```
Expected: no errors.

With the app running and the warehouse UI closed in game, request a warehouse scan and
confirm the card shows 倉庫尚未開啟 rather than an empty list.

- [ ] **Step 4: Commit**

```bash
git add webui/src/pages/Dashboard.tsx
git commit -m "feat(webui): surface the session's last error on the dashboard card"
```

---

### Task 20: Full-suite verification and release hygiene

**Files:**
- Modify: `.gitignore`
- Verify: `tthol-reader.spec`

- [ ] **Step 1: Ignore local diagnostic artifacts**

Add to `.gitignore`:

```
# Diagnostics
events.jsonl
events.jsonl.*
tthol-diag-*.zip
```

The old `tthol-reader.log` at the repo root is superseded by `events.jsonl`; remove it if it
is tracked:

```bash
git rm --cached tthol-reader.log 2>/dev/null || echo "not tracked — nothing to do"
```

- [ ] **Step 2: Confirm the PyInstaller spec needs no change**

`diag.py` is a developer/agent tool run from source; it is not an entry point in the frozen
bundle, and `.claude/commands/` is not shipped. Confirm nothing new needs bundling:

```bash
grep -n "datas\|hiddenimports" tthol-reader.spec
```

Expected: `services/*` is already collected via the `app.py` entry point; no new data files
are required (`runtime.json` and `events.jsonl` are written at runtime, not shipped).

- [ ] **Step 3: Run everything**

```bash
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
cd webui && npx tsc --noEmit && npm run build && cd ..
```
Expected: all green.

- [ ] **Step 4: End-to-end acceptance — the agent channel**

With the app running and no character logged in:

```bash
uv run diag.py summary
uv run diag.py events --level ERROR --json
```

Expected: `summary` reports the app as running with the correct version and chain constants;
`events` shows an `E_CHAIN_READ` or `E_LOCATE_EXHAUSTED` with a populated `detail`. Per the
spec's acceptance criterion, a fresh agent given only `/tthol-diag` must be able to reach the
right conclusion from this output alone.

- [ ] **Step 5: End-to-end acceptance — the human channel**

In the app: open 脈案, confirm the environment card is populated, toggle 詳細記錄 on, request
a warehouse scan with the warehouse closed in game, confirm the dashboard card shows
倉庫尚未開啟, then 匯出診斷包 and open `report.md` in the zip. It should name the failure.

- [ ] **Step 6: Commit and push**

```bash
git add .gitignore
git commit -m "chore(diag): ignore local diagnostic artifacts"
git push -u origin feat/diagnostics-observability
```

---

## Self-Review

**Spec coverage**

| Spec section | Tasks |
|---|---|
| §1 Log infrastructure — JSONL, fallback, rotation, header, noise, HTTP | 3, 4, 5, 9 (noise), 10 (HTTP) |
| §2 Diagnostics core — DiagEvent, buffer, handlers, bind, verbose, snapshot | 1, 2, 3, 6 |
| §3 Error propagation — on_error, bound logger, tick print, exception handlers, empty-vs-unscannable | 7, 8, 9, 10, 19 |
| §4 API and bundle | 11, 12 |
| §5 Frontend — ApiError, report.ts, ErrorBoundary, 脈案 page, catches, Dashboard, version | 16, 17, 18, 19 |
| §6 Agent surface — runtime.json, JSONL, codes, CLI, skill | 4, 3, 1, 14, 15 |
| §7 Testing | every task; Task 20 runs the whole suite |
| F1–F10 | F1→8, F2→10, F3→8/9, F4→5, F5→10, F6→16/17, F7→4, F8→9, F9→18, F10→9/19 |

**Placeholder scan:** no TBD/TODO; every code step carries real code; no "similar to Task N".

**Type consistency checks applied:**
- `on_error` keyword signature `(msg, *, cat, code, detail)` is identical in Tasks 8 (session), 9 (worker call sites), and the Task 9 test's `lambda msg, **kw`.
- `DiagnosticsBuffer.query` keyword names (`since`, `level`, `pid`, `cat`, `code`, `limit`) match Task 2's definition, Task 12's router, and Task 14's `filter_events`.
- `ErrorCode` constants are referenced identically in Tasks 1, 9, 10, 12, 15 and the Task 19 frontend mapper.
- `render_human_line` is defined in Task 11 and imported by Task 14.
- `logsetup._current_path` and `logsetup._reset_for_tests` are defined in Task 5 and used in Tasks 8, 9, 10, 12, 13.
- `describeError` / `reportClientError` are defined in Task 16 and consumed in 17, 18, 19.
- `put` is added in Task 18 Step 3 because Task 16's `client.ts` does not define it — flagged rather than assumed.
