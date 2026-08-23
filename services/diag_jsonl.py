"""JSONL sink -- the single on-disk format for diagnostic events.

One JSON object per line. Human-readable output is rendered on demand by
diag.py and by the bundle's report.md, so there is exactly one record and no
formatted text to parse back.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from services.diag_events import (
    DiagEvent,
    event_from_json_line,
    event_from_record,
    event_to_json_line,
)

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
        super().__init__(str(path), maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8")

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
    candidates = [path.with_name(f"{path.name}.{i}") for i in range(BACKUP_COUNT, 0, -1)]
    candidates.append(path)
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
