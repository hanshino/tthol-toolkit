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
    # Subclasses (map_image_cache) swap these to cache other immutable images.
    max_bytes = MAX_ICON_BYTES
    log_cat = "items"

    def __init__(self, cache_dir: Path | None = None, fetch=None) -> None:
        self._dir = cache_dir or default_cache_dir()
        self._fetch = fetch or (lambda url: _download(url, self.max_bytes))

    def cache_name(self, url: str) -> str | None:
        """File name to cache url under, or None when the URL looks wrong."""
        name = url.rsplit("/", 1)[-1]
        return name if _SAFE_NAME.match(name) else None

    def is_valid(self, data: bytes) -> bool:
        return data.startswith(_PNG_MAGIC)

    def get(self, url: str) -> bytes | None:
        """Image bytes for a URL, from disk or fetched once; None if unavailable."""
        name = self.cache_name(url)
        if name is None:
            log.warning(
                "image url has an unexpected file name: %s", url, extra={"cat": self.log_cat}
            )
            return None
        path = self._dir / name
        try:
            return path.read_bytes()
        except FileNotFoundError:
            pass
        except OSError as exc:
            log.debug("image cache read failed: %s", exc, extra={"cat": self.log_cat})
        try:
            data = self._fetch(url)
        except Exception as exc:
            # Offline or host down: routine, the UI falls back to the name.
            log.debug("image fetch failed for %s: %s", url, exc, extra={"cat": self.log_cat})
            return None
        if not self.is_valid(data) or len(data) > self.max_bytes:
            log.warning(
                "image at %s is not the expected type; not cached", url, extra={"cat": self.log_cat}
            )
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
            log.debug("image cache write failed: %s", exc, extra={"cat": self.log_cat})
            if tmp is not None:
                Path(tmp).unlink(missing_ok=True)


def _download(url: str, max_bytes: int) -> bytes:
    with urllib.request.urlopen(url, timeout=FETCH_TIMEOUT) as resp:
        return resp.read(max_bytes + 1)


_cache = IconCache()


def get(url: str) -> bytes | None:
    return _cache.get(url)
