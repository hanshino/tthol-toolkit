from unittest.mock import patch, MagicMock
from gui.process_detector import detect_game_windows

def test_returns_empty_when_no_game_running():
    with patch("gui.process_detector.psutil.process_iter", return_value=[]):
        result = detect_game_windows()
    assert result == []

def test_labels_windows_in_pid_order():
    proc1 = MagicMock(); proc1.info = {"pid": 100, "name": "tthola.dat"}
    proc2 = MagicMock(); proc2.info = {"pid": 200, "name": "tthola.dat"}
    with patch("gui.process_detector.psutil.process_iter", return_value=[proc1, proc2]):
        with patch("gui.process_detector._hwnd_for_pid", side_effect=[0x1000, 0x2000]):
            result = detect_game_windows()
    assert result == [(100, 0x1000, "視窗 1"), (200, 0x2000, "視窗 2")]

def test_ignores_other_processes():
    proc = MagicMock(); proc.info = {"pid": 999, "name": "notepad.exe"}
    with patch("gui.process_detector.psutil.process_iter", return_value=[proc]):
        result = detect_game_windows()
    assert result == []
