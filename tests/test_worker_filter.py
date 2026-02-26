from unittest.mock import MagicMock, patch


def test_worker_stores_offset_filters():
    """connect() with offset_filters stores them for use in _locate."""
    from gui.worker import ReaderWorker

    worker = ReaderWorker(pid=9999)
    worker.connect(hp_value=287, offset_filters={-36: 7})
    assert worker._offset_filters == {-36: 7}
    worker.terminate()


def test_worker_stores_none_when_no_filters():
    """connect() without offset_filters defaults to None."""
    from gui.worker import ReaderWorker

    worker = ReaderWorker(pid=9999)
    worker.connect(hp_value=287)
    assert worker._offset_filters is None
    worker.terminate()


def test_worker_locate_tries_chain_first():
    """_locate() tries read_hp_from_player_chain before falling back to manual HP."""
    from gui.worker import ReaderWorker

    worker = ReaderWorker(pid=9999)
    worker._hp_value = 287
    worker._offset_filters = {-36: 7}

    mock_pm = MagicMock()
    with (
        patch("gui.worker.read_hp_from_player_chain", return_value=500) as mock_chain,
        patch("gui.worker.locate_character", return_value=0x1000) as mock_lc,
    ):
        result = worker._locate(mock_pm)

    mock_chain.assert_called_once_with(mock_pm)
    mock_lc.assert_called_once_with(mock_pm, 500, worker._knowledge, {-36: 7}, compat_mode=False)
    assert result == 0x1000


def test_worker_locate_falls_back_to_manual_hp():
    """_locate() falls back to manual HP when chain fails."""
    from gui.worker import ReaderWorker

    worker = ReaderWorker(pid=9999)
    worker._hp_value = 287
    worker._offset_filters = {-36: 7}

    mock_pm = MagicMock()
    with (
        patch("gui.worker.read_hp_from_player_chain", return_value=None),
        patch("gui.worker.locate_character", return_value=0x2000) as mock_lc,
    ):
        result = worker._locate(mock_pm)

    mock_lc.assert_called_once_with(mock_pm, 287, worker._knowledge, {-36: 7}, compat_mode=False)
    assert result == 0x2000
