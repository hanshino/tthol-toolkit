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


def test_worker_locate_passes_filters_to_locate_character():
    """_locate() forwards _offset_filters to locate_character."""
    from gui.worker import ReaderWorker

    worker = ReaderWorker(pid=9999)
    worker._hp_value = 287
    worker._offset_filters = {-36: 7}

    mock_pm = MagicMock()
    with patch("gui.worker.locate_character", return_value=0x1000) as mock_lc:
        result = worker._locate(mock_pm)

    mock_lc.assert_called_once_with(mock_pm, 287, worker._knowledge, {-36: 7})
    assert result == 0x1000
