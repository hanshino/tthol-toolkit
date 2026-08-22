import app as app_module


def test_inline_setup_logging_is_gone():
    # Logging wiring lives in services/logsetup.py so it can be tested; a
    # lingering copy here would install a second set of handlers.
    assert not hasattr(app_module, "_setup_logging")


def test_main_writes_and_clears_runtime_json(monkeypatch, tmp_path):
    import services.runtime_info as ri

    monkeypatch.setattr(ri, "RUNTIME_DIR", tmp_path)

    written: list[tuple] = []
    monkeypatch.setattr(app_module, "_write_runtime", lambda port: written.append(("w", port)))
    monkeypatch.setattr(app_module, "_clear_runtime", lambda: written.append(("c",)))

    # Drive only the runtime-pointer lifecycle, not the whole GUI.
    app_module._runtime_lifecycle(51234, lambda: None)
    assert written == [("w", 51234), ("c",)]


def test_runtime_is_cleared_even_when_the_window_raises(monkeypatch, tmp_path):
    import services.runtime_info as ri

    monkeypatch.setattr(ri, "RUNTIME_DIR", tmp_path)
    written: list[tuple] = []
    monkeypatch.setattr(app_module, "_write_runtime", lambda port: written.append(("w", port)))
    monkeypatch.setattr(app_module, "_clear_runtime", lambda: written.append(("c",)))

    def boom():
        raise RuntimeError("window died")

    try:
        app_module._runtime_lifecycle(1, boom)
    except RuntimeError:
        pass
    assert written == [("w", 1), ("c",)]
