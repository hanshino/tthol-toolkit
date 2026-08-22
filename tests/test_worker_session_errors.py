import logging

import pytest

from services import diagnostics, logsetup
from services.char_session import CharSession


@pytest.fixture(autouse=True)
def _bus(tmp_path, monkeypatch):
    root = logging.getLogger()
    saved = list(root.handlers)
    root.handlers.clear()
    logsetup._reset_for_tests()
    monkeypatch.setattr(logsetup, "candidate_paths", lambda: [tmp_path / "events.jsonl"])
    diagnostics.init(console=False)
    diagnostics.get_buffer().clear()
    yield
    root.handlers.clear()
    root.handlers.extend(saved)
    logsetup._reset_for_tests()


def test_on_error_is_no_longer_a_no_op():
    sess = CharSession(pid=27160)
    # The worker is constructed with a real callback, not `lambda _msg: None`.
    sess._worker._cb_error(
        "Inventory not found in memory",
        cat="inventory",
        code="E_INV_NOT_FOUND",
        detail={"hp_addr": 123},
    )

    assert sess.last_error is not None
    assert sess.last_error.code == "E_INV_NOT_FOUND"
    assert sess.last_error.cat == "inventory"

    events = diagnostics.get_buffer().query(code="E_INV_NOT_FOUND")
    assert len(events) == 1
    assert events[0].pid == 27160
    assert events[0].detail == {"hp_addr": 123}


def test_error_reaches_the_row_payload():
    sess = CharSession(pid=27160)
    sess._on_state("LOCATED")
    sess._on_stats([("角色名稱", "無塵"), ("血量", 100), ("最大血量", 120)])
    sess._on_error(
        "Warehouse not found -- open warehouse UI in game first",
        cat="warehouse",
        code="E_WH_NOT_FOUND",
    )

    row = sess.row()
    assert row is not None
    assert row.last_error is not None
    assert row.last_error.code == "E_WH_NOT_FOUND"
    assert sess.detail().last_error.code == "E_WH_NOT_FOUND"


def test_logger_rebinds_to_the_character_name_once_known():
    sess = CharSession(pid=27160)
    sess._on_stats([("角色名稱", "無塵")])
    sess._on_error("boom", cat="locate")

    ev = diagnostics.get_buffer().query(cat="locate")[0]
    assert ev.char == "無塵"


def test_default_cat_and_optional_code():
    sess = CharSession(pid=1)
    sess._on_error("plain message")
    assert sess.last_error.cat == "worker"
    assert sess.last_error.code is None
