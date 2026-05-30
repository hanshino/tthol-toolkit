# PyInstaller spec — onedir build of 御心鑒.
#
# Builds `tthol-reader.exe` from app.py — double-click goes straight into
# the FastAPI + pywebview main app. Run with:
#     uv run pyinstaller --noconfirm tthol-reader.spec
#
# Output layout (in dist/tthol-reader/):
#     tthol-reader.exe
#     _internal/
#         python311.dll, base_library.zip, ...
#         knowledge.json, tthol.sqlite, icon.ico, webui/dist/

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# Guard: refuse to build with a pythonnet that breaks the frozen WinForms/.NET
# (netfx) loader. pythonnet >= 3.1 (built with .NET SDK 10) ships a
# Python.Runtime whose .NET dependencies aren't satisfied on a clean end-user
# machine, so the bundle crashes at startup with
# "Failed to resolve Python.Runtime.Loader.Initialize" (it can still load on a
# dev box with .NET SDKs installed, which makes the bug easy to miss). uv.lock
# and the pyproject `pythonnet<3.1` pin keep us on the tested 3.0.x line; this
# is the last-line guard if both are bypassed (e.g. a manual pip install).
import importlib.metadata as _md

_pnet = _md.version("pythonnet")
if tuple(int(x) for x in _pnet.split(".")[:2]) >= (3, 1):
    raise SystemExit(
        f"tthol-reader.spec: refusing to build with pythonnet {_pnet}. "
        ">= 3.1 breaks the frozen netfx loader on clean machines. "
        "Pin pythonnet<3.1 (see pyproject.toml / uv.lock)."
    )

datas = [
    ("knowledge.json", "."),
    ("tthol.sqlite", "."),
    ("icon.ico", "."),
    ("webui/dist", "webui/dist"),
]
datas += collect_data_files("webview")

hiddenimports = [
    # pywebview backends — pywebview's own hook usually covers these but
    # bundling them explicitly avoids surprises on first packaging.
    *collect_submodules("webview"),
    # uvicorn[standard] extras
    *collect_submodules("uvicorn"),
    "httptools",
    "websockets",
    "websockets.legacy",
    "websockets.legacy.server",
    # FastAPI + Starlette internals reached via string imports
    *collect_submodules("fastapi"),
    "anyio._backends._asyncio",
    # pywin32
    "win32con",
    "win32gui",
    "win32process",
    # Local scripts imported by services.worker via top-level names
    "reader",
    "warehouse_scan",
    "auto_detect",
]

excludes = [
    "tkinter",
    "PySide6",
    "PyQt5",
    "PyQt6",
    "matplotlib",
    "numpy",
    "pandas",
    "IPython",
    "notebook",
    "pytest",
]

a = Analysis(
    ["app.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

import os

_DEBUG = os.environ.get("TTHOL_BUILD_DEBUG") == "1"

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="tthol-reader",
    console=_DEBUG,
    uac_admin=not _DEBUG,
    disable_windowed_traceback=False,
    debug=False,
    strip=False,
    upx=False,
    icon="icon.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="tthol-reader",
)
