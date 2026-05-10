"""Splash launcher: HTTP self-update from GitHub Releases, then spawn app.py.

Replaces the older git-pull + uv-sync flow. Reads the local VERSION file,
queries the GitHub Releases API for the latest tag, and if newer, downloads
the release zip, extracts it to _pending_update/, and hands off to a small
swap script that waits for this process to exit, mirrors the staging tree
over the install root, and relaunches bootstrap.py.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import webview

REPO = Path(__file__).resolve().parent
VERSION_FILE = REPO / "VERSION"
PENDING_DIR = REPO / "_pending_update"
SWAP_SCRIPT = REPO / "_swap.cmd"

GITHUB_REPO = "hanshino/tthol-toolkit"
RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
ASSET_PREFIX = "tthol-reader-"


# Swap script:
#   %1 = parent pid (this process)
#   %2 = staging dir (extracted update)
#   %3 = install dir (repo root)
SWAP_CMD = """@echo off
setlocal

:wait
tasklist /NH /FI "PID eq %1" 2>NUL | find ":" >NUL
if not errorlevel 1 goto swap
timeout /t 1 /nobreak >NUL 2>&1
goto wait

:swap
robocopy "%~2" "%~3" /E /NFL /NDL /NJH /NJS /NC /NS /NP /R:3 /W:1 >NUL
rmdir /S /Q "%~2" 2>NUL
start "" "%~3\\toolkit\\python\\pythonw.exe" "%~3\\bootstrap.py"
del "%~f0" 2>NUL
"""


def _local_version() -> str:
    if VERSION_FILE.exists():
        return VERSION_FILE.read_text(encoding="utf-8").strip()
    return "v0.0.0"


def _fetch_latest_release() -> dict:
    req = urllib.request.Request(
        RELEASE_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "tthol-bootstrap",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def _pick_asset(meta: dict) -> dict | None:
    for asset in meta.get("assets", []) or []:
        name = asset.get("name", "")
        if name.startswith(ASSET_PREFIX) and name.endswith(".zip"):
            return asset
    return None


def _download(url: str, dest: Path, on_progress) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "tthol-bootstrap"})
    with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length", "0") or 0)
        read = 0
        last_pct = -1
        while True:
            chunk = r.read(64 * 1024)
            if not chunk:
                break
            f.write(chunk)
            read += len(chunk)
            if total:
                pct = int(read * 100 / total)
                if pct != last_pct and pct % 5 == 0:
                    on_progress(f"download {pct}%  ({read >> 20} / {total >> 20} MB)")
                    last_pct = pct


class SplashApi:
    def __init__(self) -> None:
        self._window: webview.Window | None = None

    def _set_status(self, s: str) -> None:
        if self._window:
            self._window.evaluate_js(f"setStatus({json.dumps(s)})")

    def _log(self, line: str, err: bool = False) -> None:
        if self._window:
            self._window.evaluate_js(f"appendLog({json.dumps(line)}, {str(err).lower()})")

    def do_update(self) -> dict:
        try:
            local = _local_version()
            self._set_status("checking for updates ...")
            self._log(f"current version {local}")

            try:
                meta = _fetch_latest_release()
            except urllib.error.URLError as e:
                self._log(f"no network or GitHub unreachable: {e}", err=True)
                return {"ok": True, "skipped": True}
            except Exception as e:
                self._log(f"release lookup failed: {e}", err=True)
                return {"ok": True, "skipped": True}

            latest = meta.get("tag_name") or ""
            if not latest:
                self._log("no latest release found")
                return {"ok": True, "skipped": True}

            if latest == local:
                self._log("already up to date")
                return {"ok": True, "skipped": True}

            asset = _pick_asset(meta)
            if not asset:
                self._log(f"no release asset for {latest}", err=True)
                return {"ok": False, "error": "no asset"}

            self._log(f"new version {latest} found")
            self._set_status(f"downloading {latest} ...")

            with tempfile.TemporaryDirectory(prefix="tthol-update-") as tmp:
                zip_path = Path(tmp) / asset["name"]
                _download(asset["browser_download_url"], zip_path, self._log)

                self._set_status("extracting ...")
                if PENDING_DIR.exists():
                    shutil.rmtree(PENDING_DIR, ignore_errors=True)
                PENDING_DIR.mkdir(parents=True)
                with zipfile.ZipFile(zip_path) as zf:
                    zf.extractall(PENDING_DIR)

            self._set_status("applying update ...")
            SWAP_SCRIPT.write_text(SWAP_CMD, encoding="ascii")

            DETACHED_PROCESS = 0x00000008
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            subprocess.Popen(
                [
                    "cmd",
                    "/c",
                    str(SWAP_SCRIPT),
                    str(os.getpid()),
                    str(PENDING_DIR),
                    str(REPO),
                ],
                creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
                close_fds=True,
            )
            return {"ok": True, "swapped": True}
        except Exception as e:
            self._log(f"update error: {e}", err=True)
            return {"ok": False, "error": str(e)}

    def launch_app(self) -> None:
        subprocess.Popen([sys.executable, str(REPO / "app.py")], cwd=REPO)
        if self._window:
            self._window.destroy()

    def run_anyway(self) -> None:
        self.launch_app()

    def quit(self) -> None:
        if self._window:
            self._window.destroy()


def main() -> int:
    api = SplashApi()
    window = webview.create_window(
        "御心鑒 — 啟動",
        str(REPO / "bootstrap_splash.html"),
        js_api=api,
        width=460,
        height=260,
        frameless=False,
        resizable=False,
    )
    api._window = window
    webview.start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
