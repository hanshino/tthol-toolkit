"""Local cache for minimap images.

map_images stores each map's full-size WebP on img.hanshino.dev (up to ~10 MB,
9600 px a side). PictShare resizes on request: inserting `/<n>/` before the
file name returns the image fitted inside an n x n box, so the app only ever
downloads and caches a thumbnail. PictShare cannot resize images over roughly
30 MP (HTTP 500, ~135 of 647 maps), so for those the original is cached and the
WebView scales it. Files live under %APPDATA%\\御心鑒\\maps and,
like icons, are never refreshed (the hash in the URL is a content key).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from services._paths import app_root
from services.icon_cache import IconCache

MINIMAP_SIZES = (512, 1024, 2048)  # longest side in px; a closed set keeps the cache small
DEFAULT_MINIMAP_SIZE = 1024
MAX_MAP_BYTES = 12 * 1024 * 1024  # originals stay under PictShare's 10 MB upload cap

_WEBP_RIFF = b"RIFF"
_WEBP_TAG = b"WEBP"
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}\.webp$")


def default_cache_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) / "御心鑒" if appdata else app_root()
    return base / "maps"


def thumbnail_url(url: str, size: int) -> str:
    """The PictShare URL for url resized to fit inside size x size."""
    head, name = url.rsplit("/", 1)
    return f"{head}/{size}/{name}"


class MapImageCache(IconCache):
    max_bytes = MAX_MAP_BYTES
    log_cat = "maps"

    def __init__(self, cache_dir: Path | None = None, fetch=None) -> None:
        super().__init__(cache_dir or default_cache_dir(), fetch)

    def cache_name(self, url: str) -> str | None:
        # ".../<size>/<hash>.webp" -> "<size>_<hash>.webp"; an original
        # ".../<hash>.webp" -> "full_<hash>.webp"
        parts = url.rsplit("/", 2)
        if len(parts) != 3 or not _SAFE_NAME.match(parts[2]):
            return None
        prefix = parts[1] if parts[1].isdigit() else "full"
        return f"{prefix}_{parts[2]}"

    def is_valid(self, data: bytes) -> bool:
        return data[:4] == _WEBP_RIFF and data[8:12] == _WEBP_TAG


_cache = MapImageCache()


def get(url: str, size: int = DEFAULT_MINIMAP_SIZE) -> bytes | None:
    """WebP thumbnail bytes of a map_images URL, else the original; None when unavailable."""
    return _cache.get(thumbnail_url(url, size)) or _cache.get(url)
