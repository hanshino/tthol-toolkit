"""Main app entry. Starts uvicorn in a daemon thread, then opens a
pywebview window pointed at the local server.
"""

from __future__ import annotations

import argparse
import asyncio
import socket
import sys
import threading
import time
from pathlib import Path

import uvicorn
import webview

from services.api import build_app
from services.auto_click import AutoClickManager
from services.snapshot_db import SnapshotDB
from services.worker_manager import WorkerManager

DEV_PORT_FILE = Path(".omc/.dev-port")


def _pick_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _build_services(dev: bool) -> dict:
    db = SnapshotDB()
    return {
        "worker_manager": WorkerManager(snapshot_db=db),
        "snapshot_db": db,
        "autoclick_manager": AutoClickManager(),
    }


async def _tick_runner(services: dict, stream) -> None:
    wm: WorkerManager = services["worker_manager"]
    await wm.run_tick_loop(stream)


def _serve(app, port: int) -> None:
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    asyncio.set_event_loop(asyncio.new_event_loop())
    loop = asyncio.get_event_loop()
    # Tick loop and WS handlers must share this loop — asyncio.Queue/Lock in
    # WorldStream are loop-bound, so the tick task is scheduled here, not on
    # the main thread.
    loop.create_task(_tick_runner(app.state.services, app.state.services["world_stream"]))
    loop.run_until_complete(server.serve())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dev", action="store_true", help="point window at Vite dev server")
    args = parser.parse_args()

    services = _build_services(args.dev)
    app = build_app(services=services)

    port = _pick_port()
    if args.dev:
        DEV_PORT_FILE.parent.mkdir(parents=True, exist_ok=True)
        DEV_PORT_FILE.write_text(str(port))

    if not args.dev:
        from fastapi.staticfiles import StaticFiles

        dist = Path("webui/dist")
        if dist.exists():
            app.mount("/", StaticFiles(directory=str(dist), html=True), name="webui")

    server_thread = threading.Thread(target=_serve, args=(app, port), daemon=True)
    server_thread.start()

    deadline = time.time() + 5.0
    while time.time() < deadline:
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=0.2)
            s.close()
            break
        except OSError:
            time.sleep(0.05)

    target_url = "http://127.0.0.1:5173" if args.dev else f"http://127.0.0.1:{port}"
    webview.create_window("御心鑒", target_url, width=1440, height=900)
    webview.start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
