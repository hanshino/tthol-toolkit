"""Tests for launcher window helpers."""

from gui.launcher_window import format_pip_line


def test_skips_already_satisfied():
    assert format_pip_line("Requirement already satisfied: psutil in ...") is None


def test_skips_empty_line():
    assert format_pip_line("") is None
    assert format_pip_line("   ") is None


def test_keeps_installing_line():
    result = format_pip_line("Installing collected packages: psutil")
    assert result == "Installing collected packages: psutil"


def test_keeps_successfully_installed():
    result = format_pip_line("Successfully installed psutil-7.2.2")
    assert result == "Successfully installed psutil-7.2.2"


def test_keeps_error_line():
    result = format_pip_line("ERROR: Could not find a version that satisfies")
    assert result == "ERROR: Could not find a version that satisfies"


def test_skips_downloading_line():
    assert format_pip_line("Downloading psutil-7.2.2-cp311-win32.whl (380 kB)") is None


def test_strips_trailing_whitespace():
    result = format_pip_line("Successfully installed psutil-7.2.2   \n")
    assert result == "Successfully installed psutil-7.2.2"


def test_skips_using_cached_line():
    assert format_pip_line("Using cached psutil-7.2.2-cp311-win32.whl") is None


def test_skips_obtaining_line():
    assert format_pip_line("Obtaining file:///some/path") is None
