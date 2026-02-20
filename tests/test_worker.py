import sys
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication(sys.argv)

from unittest.mock import patch, MagicMock
from gui.worker import ReaderWorker


def test_connect_process_uses_pid_positionally():
    """_connect_process must call pymem.Pymem(pid) not pymem.Pymem(process_id=pid)."""
    worker = ReaderWorker(pid=1234)
    mock_pm = MagicMock()
    with patch("gui.worker.pymem.Pymem", return_value=mock_pm) as mock_pymem:
        result = worker._connect_process()
    mock_pymem.assert_called_once_with(1234)
    assert result is mock_pm
