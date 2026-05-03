from unittest.mock import patch
from services.api_types import WorldSnapshot
from services.worker_manager import WorkerManager


@patch("services.worker_manager.find_tthol_processes")
def test_world_snapshot_empty_when_no_processes(mock_find):
    mock_find.return_value = []
    wm = WorkerManager()
    snap = wm.world_snapshot()
    assert isinstance(snap, WorldSnapshot)
    assert snap.chars == []


@patch("services.worker_manager.CharSession")
@patch("services.worker_manager.find_tthol_processes")
def test_world_snapshot_emits_placeholder_for_unconnected(mock_find, mock_sess_cls):
    mock_find.return_value = [{"pid": 1234}]
    mock_sess = mock_sess_cls.return_value
    mock_sess.row.return_value = None
    mock_sess.link = "weak"
    wm = WorkerManager()
    snap = wm.world_snapshot()
    assert len(snap.chars) == 1
    assert snap.chars[0].pid == 1234
    assert snap.chars[0].link == "weak"
    assert snap.chars[0].name == "(連線中)"


@patch("services.worker_manager.find_tthol_processes")
def test_list_characters_returns_processes(mock_find):
    mock_find.return_value = [{"pid": 1234}, {"pid": 5678}]
    wm = WorkerManager()
    chars = wm.list_characters()
    assert len(chars) == 2
    assert chars[0].pid == 1234
    assert chars[0].link == "lost"
