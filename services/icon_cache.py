"""Local cache for item icons.

The DB only stores each icon's URL on img.hanshino.dev. Icons are fetched on
first use and kept under %APPDATA%\\御心鑒\\icons, so a page full of items
loads from disk afterwards and still renders offline. Icons never change for a
given URL (the file name is a content key), so cached files are never refreshed.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
import urllib.request
from pathlib import Path

from services._paths import app_root

log = logging.getLogger("tthol.icon_cache")

FETCH_TIMEOUT = 5.0
MAX_ICON_BYTES = 256 * 1024  # icons are ~2 KB; anything this big is not an icon
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
# The cache file is named after the URL's last path segment; only accept a
# plain file name so a URL can never write outside the cache directory.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}\.png$")


def default_cache_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) / "御心鑒" if appdata else app_root()
    return base / "icons"


class IconCache:
    def __init__(self, cache_dir: Path | None = None, fetch=None) -> None:
        self._dir = cache_dir or default_cache_dir()
        self._fetch = fetch or _download

    def get(self, url: str) -> bytes | None:
        """PNG bytes for an icon URL, from disk or fetched once; None if unavailable."""
        name = url.rsplit("/", 1)[-1]
        if not _SAFE_NAME.match(name):
            log.warning("icon url has an unexpected file name: %s", url, extra={"cat": "items"})
            return None
        path = self._dir / name
        try:
            return path.read_bytes()
        except FileNotFoundError:
            pass
        except OSError as exc:
            log.debug("icon cache read failed: %s", exc, extra={"cat": "items"})
        try:
            data = self._fetch(url)
        except Exception as exc:
            # Offline or host down: routine, the UI falls back to the name.
            log.debug("icon fetch failed for %s: %s", url, exc, extra={"cat": "items"})
            return None
        if not data.startswith(_PNG_MAGIC) or len(data) > MAX_ICON_BYTES:
            log.warning("icon at %s is not a PNG; not cached", url, extra={"cat": "items"})
            return None
        self._store(path, data)
        return data

    def _store(self, path: Path, data: bytes) -> None:
        tmp: str | None = None
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            # Write then rename, so a concurrent request never reads half a file.
            fd, tmp = tempfile.mkstemp(dir=self._dir, suffix=".tmp")
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
        except OSError as exc:
            log.debug("icon cache write failed: %s", exc, extra={"cat": "items"})
            if tmp is not None:
                Path(tmp).unlink(missing_ok=True)


def _download(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=FETCH_TIMEOUT) as resp:
        return resp.read(MAX_ICON_BYTES + 1)


_cache = IconCache()


def get(url: str) -> bytes | None:
    return _cache.get(url)
