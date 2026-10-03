"""Export every running client's character as TTHOL1, without the app.

    uv run python scripts/stat_sim_export.py <out_dir>

Writes <name>-Lv<level>.json and .txt (the TTHOL1 string) per character, for
genbu import samples and checks. Locates the character the way the worker
does (HP from the player chain, then the scan in both layouts) and builds the
payload with services/stat_sim_export. Read-only.
"""

import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymem  # noqa: E402
import pymem.process  # noqa: E402

import reader  # noqa: E402
from services import stat_sim_export as ex  # noqa: E402
from services.backup import APP_VERSION  # noqa: E402


def locate(pm, knowledge):
    """(hp_addr, compat_mode) like ReaderWorker._locate, or None."""
    hp = reader.read_hp_from_player_chain(pm)
    if hp is None:
        return None
    for compat in (False, True):
        addr = reader.locate_character(pm, hp, knowledge, compat_mode=compat)
        if addr is not None:
            return addr, compat
    return None


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    out_dir = Path(sys.argv[1])
    out_dir.mkdir(parents=True, exist_ok=True)
    knowledge = reader.load_knowledge()
    pids = [
        p.th32ProcessID
        for p in pymem.process.list_processes()
        if p.szExeFile.decode(errors="replace").lower() == "tthola.dat"
    ]
    if not pids:
        sys.exit("no tthola.dat process")
    for pid in pids:
        pm = pymem.Pymem(pid)
        found = locate(pm, knowledge)
        if found is None:
            print(f"pid {pid}: character not located")
            continue
        hp_addr, compat = found
        try:
            raw = ex.read_character(pm, hp_addr, compat)
        except ex.NotReady as exc:
            print(f"pid {pid}: not ready ({exc})")
            continue
        payload = ex.build_payload(raw, APP_VERSION, datetime.datetime.now(datetime.timezone.utc))
        code = ex.encode(payload)
        stem = f"{payload['name']}-Lv{payload['level']}"
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        (out_dir / f"{stem}.json").write_text(text, encoding="utf-8")
        (out_dir / f"{stem}.txt").write_text(code + "\n", encoding="utf-8")
        print(f"pid {pid}: {stem} compat={compat} code={len(code)}B")


if __name__ == "__main__":
    main()
