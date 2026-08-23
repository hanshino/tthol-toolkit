"""In-memory ring buffer of diagnostic events, and the logging handler that fills it.

Feeds the live diagnostics page. Durability is the JSONL sink's job, not this
module's -- the buffer is deliberately bounded and process-local.
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
