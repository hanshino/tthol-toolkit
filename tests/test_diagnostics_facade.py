import logging

import pytest

from services import diagnostics, logsetup


@pytest.fixture(autouse=True)
def _clean_root():
    root = logging.getLogger()
    saved = list(root.handlers)
    root.handlers.clear()
    logsetup._reset_for_tests()
    diagnostics.get_buffer().clear()
    diagnostics.set_verbose(False)
    yield
    root.handlers.clear()
    root.handlers.extend(saved)
    logsetup._reset_for_tests()
    diagnostics.set_verbose(False)


def test_bind_attaches_pid_and_name_to_every_line(tmp_path, monkeypatch):
    monkeypatch.setattr(logsetup, "candidate_paths", lambda: [tmp_path / "events.jsonl"])
    diagnostics.init(console=False)
    buf = diagnostics.get_buffer()
    buf.clear()

    log = diagnostics.bind(27160, "無塵")
    log.error("boom", extra={"cat": "locate", "code": "E_LOCK_LOST"})

    ev = buf.query()[0]
    assert ev.pid == 27160
    assert ev.char == "無塵"
    assert ev.code == "E_LOCK_LOST"


def test_bind_merges_call_site_extra_instead_of_replacing_it(tmp_path, monkeypatch):
    # Regression: the stdlib LoggerAdapter.process() overwrites kwargs["extra"]
    # wholesale, which silently drops cat/code/detail and leaves every bound
    # line uncategorised. bind() must merge, with call-site keys winning.
    monkeypatch.setattr(logsetup, "candidate_paths", lambda: [tmp_path / "events.jsonl"])
    diagnostics.init(console=False)
    buf = diagnostics.get_buffer()
    buf.clear()

    diagnostics.bind(42, "甲").warning(
        "merged",
        extra={"cat": "inventory", "code": "E_INV_NOT_FOUND", "detail": {"k": 1}},
    )
    ev = buf.query()[0]
    assert (ev.pid, ev.char) == (42, "甲")  # bound identity survives
    assert ev.cat == "inventory"  # call-site extra survives
    assert ev.code == "E_INV_NOT_FOUND"
    assert ev.detail == {"k": 1}

    # A call site with better information may override the bound identity.
    buf.clear()
    diagnostics.bind(42, "甲").info("override", extra={"cat": "locate", "char_name": "乙"})
    assert buf.query()[0].char == "乙"


def test_bind_rebind_updates_name(tmp_path, monkeypatch):
    monkeypatch.setattr(logsetup, "candidate_paths", lambda: [tmp_path / "events.jsonl"])
    diagnostics.init(console=False)
    buf = diagnostics.get_buffer()
    buf.clear()

    diagnostics.bind(1, None).info("before", extra={"cat": "locate"})
    diagnostics.bind(1, "無塵").info("after", extra={"cat": "locate"})

    by_msg = {e.message: e for e in buf.query()}
    assert by_msg["before"].char is None
    assert by_msg["after"].char == "無塵"


def test_verbose_only_moves_the_tthol_logger(tmp_path, monkeypatch):
    monkeypatch.setattr(logsetup, "candidate_paths", lambda: [tmp_path / "events.jsonl"])
    diagnostics.init(console=False)

    assert logging.getLogger("tthol").level == logging.INFO
    diagnostics.set_verbose(True)
    assert diagnostics.is_verbose() is True
    assert logging.getLogger("tthol").level == logging.DEBUG
    # Root must stay at INFO or pymem's DEBUG output floods everything.
    assert logging.getLogger().level == logging.INFO

    diagnostics.set_verbose(False)
    assert logging.getLogger("tthol").level == logging.INFO


def test_snapshot_reports_every_declared_key_even_when_probes_raise():
    class ExplodingPm:
        def read_bytes(self, *a, **kw):
            raise RuntimeError("process gone")

    snap = diagnostics.snapshot_locate_failure(
        ExplodingPm(), hp_addr=0x1BE7A430, score=0.4, failed_fields=["等級", "防禦"]
    )
    for key in (
        "chain_hp",
        "compat_false",
        "compat_true",
        "bytes_hex",
        "score",
        "failed_fields",
        "process_alive",
        "hp_addr",
    ):
        assert key in snap, f"missing {key}"
    assert snap["score"] == 0.4
    assert snap["failed_fields"] == ["等級", "防禦"]
    assert snap["hp_addr"] == "0x1be7a430"
    # A probe that raises must degrade to a string, never propagate.
    assert isinstance(snap["bytes_hex"], str)


def test_snapshot_never_raises_on_a_none_process():
    snap = diagnostics.snapshot_locate_failure(None)
    assert snap["process_alive"] is False
