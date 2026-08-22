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

CONSOLE_FORMAT = (
    "%(asctime)s %(levelname)-7s %(name)s [pid=%(char_pid)s char=%(char_name)s] %(message)s"
)

_EVENTS_FILENAME = "events.jsonl"

_configured = False
_current_path: Path | None = None


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


def _set_current_path(path: Path | None) -> None:
    global _current_path
    _current_path = path


def current_path() -> Path | None:
    return _current_path


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
