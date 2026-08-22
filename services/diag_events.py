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
    the whole event -- a degraded field beats a missing record.
    """
    return json.dumps(asdict(ev), ensure_ascii=False, default=repr)


def event_from_json_line(line: str) -> DiagEvent:
    return DiagEvent(**json.loads(line))
