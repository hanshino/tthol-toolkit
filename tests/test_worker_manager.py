from unittest.mock import patch
from services.api_types import WorldSnapshot
from services.worker_manager import WorkerManager


def test_world_snapshot_empty_initially():
    wm = WorkerManager()
    snap = wm.world_snapshot()
    assert isinstance(snap, WorldSnapshot)
    assert snap.chars == []


@patch("services.worker_manager.find_tthol_processes")
def test_list_characters_returns_processes(mock_find):
    mock_find.return_value = [{"pid": 1234}, {"pid": 5678}]
    wm = WorkerManager()
    chars = wm.list_characters()
    assert len(chars) == 2
    assert chars[0].pid == 1234
    assert chars[0].link == "lost"
