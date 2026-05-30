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
#         knowledge.json, tthol.sqlite, webui/dist/

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = [
    ("knowledge.json", "."),
    ("tthol.sqlite", "."),
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
    icon=None,
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
