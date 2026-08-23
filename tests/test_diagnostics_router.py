import io
import logging
import zipfile

import pytest
from httpx import ASGITransport, AsyncClient

from services import diagnostics, logsetup
from services.api import build_app
from services.backup import APP_VERSION


@pytest.fixture(autouse=True)
def _bus(tmp_path, monkeypatch):
    root = logging.getLogger()
    saved = list(root.handlers)
    root.handlers.clear()
    logsetup._reset_for_tests()
    monkeypatch.setattr(logsetup, "candidate_paths", lambda: [tmp_path / "events.jsonl"])
    diagnostics.init(console=False)
    diagnostics.get_buffer().clear()
    diagnostics.set_verbose(False)
    yield
    root.handlers.clear()
    root.handlers.extend(saved)
    logsetup._reset_for_tests()
    diagnostics.set_verbose(False)


@pytest.fixture
async def client():
    app = build_app(services=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def test_events_returns_newest_first_and_filters(client):
    log = logging.getLogger("tthol.test")
    log.info("one", extra={"cat": "locate", "char_pid": 1})
    log.error("two", extra={"cat": "inventory", "char_pid": 2, "code": "E_INV_NOT_FOUND"})

    body = (await client.get("/api/diagnostics/events")).json()
    assert [e["message"] for e in body][:2] == ["two", "one"]

    filtered = (await client.get("/api/diagnostics/events?level=ERROR")).json()
    assert [e["code"] for e in filtered] == ["E_INV_NOT_FOUND"]

    by_pid = (await client.get("/api/diagnostics/events?pid=1")).json()
    assert [e["message"] for e in by_pid] == ["one"]

    by_code = (await client.get("/api/diagnostics/events?code=E_INV_NOT_FOUND")).json()
    assert len(by_code) == 1


async def test_events_since_returns_only_newer(client):
    log = logging.getLogger("tthol.test")
    log.info("first", extra={"cat": "locate"})
    first_ts = (await client.get("/api/diagnostics/events")).json()[0]["ts"]
    log.info("second", extra={"cat": "locate"})

    body = (await client.get(f"/api/diagnostics/events?since={first_ts}")).json()
    messages = [e["message"] for e in body]
    # Assert the property, not an exact list: third-party loggers (httpx here,
    # pymem in the real app) share the bus by design, so anything can appear
    # alongside ours.
    assert "second" in messages
    assert "first" not in messages


async def test_absent_pid_stays_null_rather_than_a_display_placeholder(client):
    # Regression: the console formatter's "-" default used to be applied by a
    # record-mutating filter, which leaked into the structured event and made
    # DiagEventModel reject pid as a non-integer.
    logging.getLogger("tthol.test").info("no identity", extra={"cat": "startup"})
    body = (await client.get("/api/diagnostics/events?cat=startup")).json()
    ours = [e for e in body if e["message"] == "no identity"]
    assert ours and ours[0]["pid"] is None and ours[0]["char"] is None


async def test_summary_carries_environment_and_counts(client):
    logging.getLogger("tthol.test").error("bad", extra={"cat": "locate"})
    body = (await client.get("/api/diagnostics/summary")).json()
    assert body["environment"]["app_version"] == APP_VERSION
    assert body["counts"]["ERROR"] >= 1
    assert "events_path" in body
    assert body["verbose"] is False


async def test_verbose_toggle_roundtrips(client):
    assert (await client.get("/api/diagnostics/verbose")).json()["verbose"] is False
    resp = await client.put("/api/diagnostics/verbose", json={"verbose": True})
    assert resp.json()["verbose"] is True
    assert diagnostics.is_verbose() is True
    assert (await client.get("/api/diagnostics/verbose")).json()["verbose"] is True


async def test_client_error_lands_on_the_same_bus(client):
    resp = await client.post(
        "/api/diagnostics/client-error",
        json={
            "message": "TypeError: x is not a function",
            "url": "/detail",
            "stack": "at Foo\nat Bar",
            "component": "ItemsTab",
        },
    )
    assert resp.status_code == 200

    events = diagnostics.get_buffer().query(cat="client")
    assert len(events) == 1
    assert events[0].code == "E_CLIENT"
    assert events[0].detail["component"] == "ItemsTab"


async def test_client_error_dedupes_within_the_window(client):
    payload = {"message": "same boom", "url": "/x"}
    for _ in range(5):
        await client.post("/api/diagnostics/client-error", json=payload)
    assert len(diagnostics.get_buffer().query(cat="client")) == 1

    await client.post("/api/diagnostics/client-error", json={"message": "different", "url": "/x"})
    assert len(diagnostics.get_buffer().query(cat="client")) == 2


async def test_bundle_is_a_zip_with_the_expected_members(client):
    logging.getLogger("tthol.test").error("bad", extra={"cat": "locate"})
    resp = await client.get("/api/diagnostics/bundle")
    assert resp.status_code == 200
    assert "attachment" in resp.headers["content-disposition"]
    assert "tthol-diag-" in resp.headers["content-disposition"]

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        names = set(zf.namelist())
        assert "report.md" in names
        assert "runtime.json" in names
