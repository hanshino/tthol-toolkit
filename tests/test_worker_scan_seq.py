"""Regression: inventory/warehouse scan callbacks must fire on EVERY exit path
(including not-found and error), so the session's _inv_seq/_wh_seq advances and
the waiting API request returns promptly instead of blocking the full
INVENTORY_SCAN_TIMEOUT / WAREHOUSE_SCAN_TIMEOUT before 504-ing.
"""

import services.worker as worker_mod
from services.worker import ReaderWorker


def _make_worker():
    captured: dict[str, list | None] = {"inv": None, "wh": None}
    w = ReaderWorker(
        pid=1234,
        on_state=lambda _s: None,
        on_stats=lambda _r: None,
        on_inventory=lambda items: captured.__setitem__("inv", items),
        on_warehouse=lambda items: captured.__setitem__("wh", items),
        # on_error now takes keyword cat/code/detail alongside the message.
        on_error=lambda _m, **_kw: None,
    )
    return w, captured


def _raise(_pm):
    raise RuntimeError("simulated scan failure")


def test_inventory_not_found_still_fires_callback(monkeypatch):
    w, captured = _make_worker()
    monkeypatch.setattr(worker_mod, "locate_inventory", lambda _pm: None)
    w._do_inventory_scan(pm=None)
    assert captured["inv"] == []


def test_inventory_error_still_fires_callback(monkeypatch):
    w, captured = _make_worker()
    monkeypatch.setattr(worker_mod, "locate_inventory", _raise)
    w._do_inventory_scan(pm=None)
    assert captured["inv"] == []


def test_warehouse_not_found_still_fires_callback(monkeypatch):
    w, captured = _make_worker()
    monkeypatch.setattr(worker_mod, "locate_inventory", lambda _pm: None)
    monkeypatch.setattr(worker_mod, "locate_all_slot_arrays", lambda _pm: [])
    w._do_warehouse_scan(pm=None)
    assert captured["wh"] == []


def test_warehouse_error_still_fires_callback(monkeypatch):
    w, captured = _make_worker()
    monkeypatch.setattr(worker_mod, "locate_inventory", _raise)
    w._do_warehouse_scan(pm=None)
    assert captured["wh"] == []
