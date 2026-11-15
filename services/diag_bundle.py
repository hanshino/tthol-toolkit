"""Bundle assembly, shared by the API endpoint and the CLI.

One implementation so a zip built by either route has the same structure -- the
report the maintainer receives is the report the tool produces.
"""

from __future__ import annotations

import datetime as _dt
import io
import json
import zipfile
from pathlib import Path
from typing import Any

from services.diag_events import DiagEvent
from services.diag_jsonl import read_jsonl

MAX_REPORT_ERRORS = 20
MAX_TIMELINE_EVENTS = 200


def bundle_filename(now: _dt.datetime) -> str:
    return f"tthol-diag-{now:%Y%m%d-%H%M%S}.zip"


def render_human_line(ev: DiagEvent) -> str:
    ts = _dt.datetime.fromtimestamp(ev.ts).strftime("%Y-%m-%d %H:%M:%S")
    who = f"pid={ev.pid or '-'} char={ev.char or '-'}"
    code = f" [{ev.code}]" if ev.code else ""
    return f"{ts} {ev.level:<7} {ev.logger} [{who}]{code} {ev.message}"


def render_report_md(
    events: list[DiagEvent],
    header: dict[str, Any],
    sessions: list[dict[str, Any]],
) -> str:
    problems = [e for e in events if e.level in ("ERROR", "WARNING")]
    shown = problems[-MAX_REPORT_ERRORS:]

    lines: list[str] = ["# tthol-reader diagnostic report", ""]

    lines.append("## Environment")
    lines.append("")
    for key, value in header.items():
        lines.append(f"- **{key}**: `{value}`")
    lines.append("")

    lines.append("## Sessions")
    lines.append("")
    if sessions:
        for s in sessions:
            name = s.get("name") or "(unnamed)"
            lines.append(f"- pid `{s.get('pid')}` -- {name} -- link `{s.get('link')}`")
    else:
        lines.append("- (none)")
    lines.append("")

    # State the total alongside the cap so a truncated list never reads as
    # "this was everything".
    lines.append(f"## Problems ({len(problems)} total, showing last {len(shown)})")
    lines.append("")
    if shown:
        for ev in shown:
            lines.append(f"- `{render_human_line(ev)}`")
            if ev.detail:
                lines.append(f"    - detail: `{json.dumps(ev.detail, ensure_ascii=False)}`")
    else:
        lines.append("- (none)")
    lines.append("")

    timeline = events[-MAX_TIMELINE_EVENTS:]
    lines.append(f"## Timeline ({len(events)} events, showing last {len(timeline)})")
    lines.append("")
    lines.append("```")
    for ev in timeline:
        lines.append(render_human_line(ev))
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def build_bundle(
    events_path: Path | str,
    header: dict[str, Any],
    sessions: list[dict[str, Any]],
) -> bytes:
    events_path = Path(events_path)
    events = read_jsonl(events_path) if events_path.exists() else []

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("report.md", render_report_md(events, header, sessions))
        zf.writestr(
            "runtime.json",
            json.dumps({**header, "sessions": sessions}, indent=2, ensure_ascii=False),
        )
        if events_path.parent.exists():
            for candidate in sorted(events_path.parent.glob(f"{events_path.name}*")):
                if candidate.is_file():
                    zf.write(candidate, f"events/{candidate.name}")
    return buf.getvalue()
