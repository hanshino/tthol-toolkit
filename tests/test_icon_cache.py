"""Tests for the on-disk item icon cache and its endpoint."""

from fastapi.testclient import TestClient

from services.icon_cache import IconCache

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
URL = "https://img.hanshino.dev/ja8dw8.png"


def test_fetches_once_then_serves_from_disk(tmp_path):
    calls = []

    def fetch(url):
        calls.append(url)
        return PNG

    cache = IconCache(tmp_path, fetch=fetch)
    assert cache.get(URL) == PNG
    assert (tmp_path / "ja8dw8.png").read_bytes() == PNG
    # A fresh instance (next app start) reads the file without fetching.
    assert IconCache(tmp_path, fetch=fetch).get(URL) == PNG
    assert calls == [URL]


def test_offline_returns_none_and_caches_nothing(tmp_path):
    def fetch(_url):
        raise OSError("no network")

    assert IconCache(tmp_path, fetch=fetch).get(URL) is None
    assert list(tmp_path.iterdir()) == []


def test_non_png_response_is_not_cached(tmp_path):
    # e.g. a captive portal or proxy error page answering in place of the host
    cache = IconCache(tmp_path, fetch=lambda _u: b"<html>login</html>")
    assert cache.get(URL) is None
    assert list(tmp_path.iterdir()) == []


def test_unexpected_file_name_is_refused(tmp_path):
    cache = IconCache(tmp_path / "icons", fetch=lambda _u: PNG)
    assert cache.get("https://img.hanshino.dev/..%2F..%2Fevil.png") is None
    assert cache.get("https://img.hanshino.dev/a/../../x.exe") is None
    assert not (tmp_path / "icons").exists()


def test_icon_endpoint(monkeypatch, tmp_path):
    from services import icon_cache, item_catalog
    from services.api import build_app

    monkeypatch.setattr(item_catalog, "icon_url", lambda i: URL if i == 24007 else None)
    monkeypatch.setattr(icon_cache, "_cache", IconCache(tmp_path, fetch=lambda _u: PNG))
    client = TestClient(build_app())
    r = client.get("/api/items/24007/icon")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content == PNG
    assert client.get("/api/items/1/icon").status_code == 404
