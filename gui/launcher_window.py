"""PySide6 launcher window: shows update progress before starting the main GUI."""

from __future__ import annotations


_SKIP_PREFIXES = (
    "Requirement already satisfied",
    "Downloading ",
    "Using cached ",
    "Obtaining ",
)


def format_pip_line(line: str) -> str | None:
    """Return a cleaned pip output line to display, or None to skip it."""
    stripped = line.strip()
    if not stripped:
        return None
    for prefix in _SKIP_PREFIXES:
        if stripped.startswith(prefix):
            return None
    return stripped
