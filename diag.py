"""Diagnostics CLI -- the agent-facing entry point.

Source resolution: runtime.json -> live app over HTTP; if the app is not
running, its events_path on disk; or an explicit bundle. One command set
covers live, post-mortem, and someone-else's-bundle.

    uv run diag.py events --since 10m --level ERROR --json
    uv run diag.py events --code E_INV_NOT_FOUND --json
    uv run diag.py summary
    uv run diag.py tail
    uv run diag.py inspect <bundle.zip>
    uv run diag.py bundle --out <path>
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys
import time
import urllib.request
import zipfile
from dataclasses import asdict
from pathlib import Path

from services.diag_bundle import build_bundle, bundle_filename, render_human_line
from services.diag_events import DiagEvent, event_from_json_line
from services.diag_jsonl import read_jsonl
from services.runtime_info import read_runtime_json, was_clean_exit

_DURATION = re.compile(r"^(\d+)([smhd])$")
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_since(text: str | None, now: float | None = None) -> float | None:
    if text is None:
        return None
    m = _DURATION.match(text.strip())
    if not m:
        raise ValueError(f"bad --since {text!r}; expected forms like 30s, 10m, 2h, 1d")
    now = time.time() if now is None else now
    return now - int(m.group(1)) * _UNITS[m.group(2)]


def _default_events_path() -> Path | None:
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return None
    return Path(local) / "tthol-reader" / "logs" / "events.jsonl"


def _app_is_live(info: dict) -> bool:
    port = info.get("port")
    if not port:
        return False
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=0.5) as r:
            return r.status == 200
    except Exception:
        return False


def _events_from_live(info: dict) -> list[DiagEvent]:
    url = f"http://127.0.0.1:{info['port']}/api/diagnostics/events?limit=1000"
    with urllib.request.urlopen(url, timeout=5.0) as r:
        rows = json.loads(r.read().decode("utf-8"))
    # The API returns newest-first; the file sources return oldest-first.
    # Normalise so every source reads the same way.
    return sorted((DiagEvent(**row) for row in rows), key=lambda e: e.ts)


def _events_from_zip(path: Path) -> list[DiagEvent]:
    out: list[DiagEvent] = []
    with zipfile.ZipFile(path) as zf:
        for name in sorted(n for n in zf.namelist() if n.startswith("events/")):
            for line in zf.read(name).decode("utf-8", "replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(event_from_json_line(line))
                except Exception:
                    continue
    return sorted(out, key=lambda e: e.ts)


def load_events(args) -> tuple[list[DiagEvent], dict | None]:
    """Returns (events, runtime_info). Raises SystemExit(2) with a clear message."""
    target = getattr(args, "target", None)
    if target:
        return (_events_from_zip(Path(target)), None)

    info = read_runtime_json()
    if info is None:
        # Last resort: the pointer is gone but the events may not be. A partial
        # answer beats "I cannot look".
        fallback = _default_events_path()
        if fallback is not None and fallback.exists():
            print(f"no runtime.json; reading {fallback}", file=sys.stderr)
            return (read_jsonl(fallback), None)
        print(
            "no runtime.json found -- start the app once, or pass a bundle:\n"
            "  uv run diag.py inspect <bundle.zip>",
            file=sys.stderr,
        )
        raise SystemExit(2)

    if _app_is_live(info):
        return (_events_from_live(info), info)
    events_path = info.get("events_path")
    if not events_path or not Path(events_path).exists():
        print(f"app is not running and {events_path!r} is missing", file=sys.stderr)
        raise SystemExit(2)
    return (read_jsonl(events_path), info)


def filter_events(
    events: list[DiagEvent],
    since: float | None = None,
    level: str | None = None,
    pid: int | None = None,
    cat: str | None = None,
    code: str | None = None,
) -> list[DiagEvent]:
    return [
        e
        for e in events
        if (since is None or e.ts > since)
        and (level is None or e.level == level)
        and (pid is None or e.pid == pid)
        and (cat is None or e.cat == cat)
        and (code is None or e.code == code)
    ]


def _emit(events: list[DiagEvent], as_json: bool) -> None:
    for ev in events:
        if as_json:
            print(json.dumps(asdict(ev), ensure_ascii=False))
        else:
            print(render_human_line(ev))


def _cmd_events(args) -> int:
    events, _ = load_events(args)
    selected = filter_events(
        events, parse_since(args.since), args.level, args.pid, args.cat, args.code
    )
    _emit(selected, args.json)
    return 0


def _cmd_inspect(args) -> int:
    return _cmd_events(args)


def _cmd_summary(args) -> int:
    events, info = load_events(args)
    counts: dict[str, int] = {}
    for e in events:
        counts[e.level] = counts.get(e.level, 0) + 1

    if info:
        live = _app_is_live(info)
        if live:
            state = "running"
        elif was_clean_exit(info):
            state = "not running (exited cleanly)"
        else:
            # No shutdown stamp and the process is gone: it died without running
            # its exit path. That is itself a finding.
            state = "not running (CRASHED -- no clean-exit stamp)"
        print(f"app:        {state} (pid {info.get('pid')})")
        print(f"version:    {info.get('app_version')}")
        print(f"port:       {info.get('port')}")
        print(f"events:     {info.get('events_path')}")
        print(
            f"chain:      static={info.get('static_base')} "
            f"player={info.get('player_hp_chain_base')}"
        )
        print(f"knowledge:  {info.get('knowledge_sha8')}")
    print(f"events:     {len(events)}")
    for level in ("ERROR", "WARNING", "INFO", "DEBUG"):
        if level in counts:
            print(f"  {level:<8} {counts[level]}")
    return 0


def _cmd_tail(args) -> int:
    seen = 0.0
    try:
        while True:
            events, _ = load_events(args)
            fresh = [e for e in events if e.ts > seen]
            if fresh:
                seen = max(e.ts for e in fresh)
                _emit(fresh, args.json)
            time.sleep(2.0)
    except KeyboardInterrupt:
        return 0


def _cmd_bundle(args) -> int:
    info = read_runtime_json()
    if info is None:
        print("no runtime.json found -- start the app once first", file=sys.stderr)
        return 2
    blob = build_bundle(
        events_path=info.get("events_path") or "events.jsonl",
        header={k: v for k, v in info.items() if k != "sessions"},
        sessions=info.get("sessions", []),
    )
    out = Path(args.out) if args.out else Path(bundle_filename(_dt.datetime.now()))
    out.write_bytes(blob)
    print(str(out))
    return 0


def _add_filters(p: argparse.ArgumentParser) -> None:
    p.add_argument("--since", help="relative window, e.g. 30s / 10m / 2h / 1d")
    p.add_argument("--level", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    p.add_argument("--pid", type=int)
    p.add_argument("--cat")
    p.add_argument("--code")
    p.add_argument("--json", action="store_true", help="NDJSON to stdout")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="diag.py", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_events = sub.add_parser("events", help="print recorded events")
    _add_filters(p_events)
    p_events.set_defaults(func=_cmd_events, target=None)

    p_summary = sub.add_parser("summary", help="environment and event counts")
    p_summary.set_defaults(
        func=_cmd_summary,
        target=None,
        since=None,
        level=None,
        pid=None,
        cat=None,
        code=None,
        json=False,
    )

    p_tail = sub.add_parser("tail", help="follow new events")
    _add_filters(p_tail)
    p_tail.set_defaults(func=_cmd_tail, target=None)

    p_inspect = sub.add_parser("inspect", help="read events out of a bundle zip")
    p_inspect.add_argument("target")
    _add_filters(p_inspect)
    p_inspect.set_defaults(func=_cmd_inspect)

    p_bundle = sub.add_parser("bundle", help="write a diagnostic bundle")
    p_bundle.add_argument("--out")
    p_bundle.set_defaults(func=_cmd_bundle)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except SystemExit as exc:
        return int(exc.code or 0)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
