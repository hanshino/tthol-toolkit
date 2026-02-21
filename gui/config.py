"""User preference persistence (config.json in project root)."""

import json
from pathlib import Path

_DEFAULT_PATH = Path(__file__).parent.parent / "config.json"


def load_theme(path: Path = _DEFAULT_PATH) -> str:
    """Return saved theme ('dark' or 'light'). Falls back to 'dark'."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        value = data.get("theme", "dark")
        return value if value in ("dark", "light") else "dark"
    except Exception:
        return "dark"


def save_theme(mode: str, path: Path = _DEFAULT_PATH) -> None:
    """Persist theme preference to config.json."""
    if mode not in ("dark", "light"):
        raise ValueError(f"Invalid theme: {mode!r}. Expected 'dark' or 'light'.")
    try:
        existing: dict = {}
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass
        existing["theme"] = mode
        path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        import sys

        print(f"[config] Failed to save theme: {e}", file=sys.stderr)
