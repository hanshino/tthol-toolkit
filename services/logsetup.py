"""Wires the logging bus: handlers, landing path, context defaults, startup header.

Extracted from app.py::_setup_logging so it can be tested. The old version
wrote next to the exe inside `except Exception: pass`, which meant a
Program Files install logged nothing and said nothing about it.
"""

from __future__ import annotations

import copy
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


class ContextFormatter(logging.Formatter):
    """Console formatter that tolerates records without our `extra` fields.

    Third-party records (pymem, uvicorn) carry none of them, and CONSOLE_FORMAT
    would raise KeyError on the first one.

    The defaults are filled on a *copy* of the record. An earlier version used a
    logging.Filter that mutated the record in place, which leaked the display
    placeholder "-" into the structured event as the pid -- a presentation
    concern must not reach the record the JSONL sink and the API serialise.
    """

    def format(self, record: logging.LogRecord) -> str:
        if not hasattr(record, "char_pid") or not hasattr(record, "char_name"):
            record = copy.copy(record)
            if not hasattr(record, "char_pid"):
                record.char_pid = "-"
            if not hasattr(record, "char_name"):
                record.char_name = "-"
        return super().format(record)


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

    # No filter on the structured sinks: they must see an absent field as
    # absent. Only the console needs display placeholders.
    root.addHandler(DiagnosticsHandler(buffer))

    jsonl_handler, path = _make_jsonl_handler()
    if jsonl_handler is not None:
        root.addHandler(jsonl_handler)

    if console:
        stream = logging.StreamHandler()
        stream.setFormatter(ContextFormatter(CONSOLE_FORMAT))
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
