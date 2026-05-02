"""Splash launcher: git pull + uv sync, then spawn app.py and exit."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import webview

REPO = Path(__file__).resolve().parent


class SplashApi:
    def __init__(self, window: webview.Window | None = None) -> None:
        self._window = window
        self._cancelled = False

    def _log(self, line: str, err: bool = False) -> None:
        if self._window is None:
            return
        line = line.replace("\\", "\\\\").replace("'", "\\'").replace("\n", " ")
        self._window.evaluate_js(f"appendLog('{line}', {str(err).lower()})")

    def do_update(self) -> dict:
        try:
            self._log("git pull --ff-only ...")
            r = subprocess.run(
                ["git", "pull", "--ff-only"],
                cwd=REPO,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self._log(r.stdout.strip() or "(empty)")
            if r.returncode != 0:
                self._log(r.stderr.strip(), err=True)
                return {"ok": False, "error": "git pull failed"}

            self._log("uv sync ...")
            r = subprocess.run(
                ["uv", "sync"],
                cwd=REPO,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self._log(r.stdout.strip() or "(empty)")
            if r.returncode != 0:
                self._log(r.stderr.strip(), err=True)
                return {"ok": False, "error": "uv sync failed"}
            return {"ok": True}
        except Exception as e:
            self._log(str(e), err=True)
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
