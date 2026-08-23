"""Public diagnostics facade -- the only diagnostics module the app imports.

Keeps call sites stable while the buffer / sink internals move behind it.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from services.diag_buffer import DiagnosticsBuffer

_buffer = DiagnosticsBuffer()
_verbose = False


def get_buffer() -> DiagnosticsBuffer:
    return _buffer


def init(console: bool = True) -> Path | None:
    from services.logsetup import setup_logging

    return setup_logging(_buffer, console=console)


class _BoundAdapter(logging.LoggerAdapter):
    """LoggerAdapter that merges `extra` instead of replacing it.

    The stdlib default overwrites kwargs["extra"] wholesale, which would drop
    the `cat` / `code` / `detail` the call site passes -- silently, leaving
    every bound line uncategorised. (Python 3.13 added merge_extra=True; this
    project targets 3.11.) Call-site keys win so a caller can override the
    bound identity when it has better information.
    """

    def process(self, msg, kwargs):
        merged = dict(self.extra or {})
        merged.update(kwargs.get("extra") or {})
        kwargs["extra"] = merged
        return msg, kwargs


def bind(pid: int, name: str | None = None) -> logging.LoggerAdapter:
    """Logger pre-loaded with this session's identity.

    Multi-boxing is a core feature; without this every worker's lines land in
    one undifferentiated stream.
    """
    return _BoundAdapter(logging.getLogger("tthol.worker"), {"char_pid": pid, "char_name": name})


def set_verbose(on: bool) -> None:
    """Raise only the `tthol` tree to DEBUG.

    Never the root logger: pymem logs at DEBUG per read and would bury
    everything else.
    """
    global _verbose
    _verbose = on
    logging.getLogger("tthol").setLevel(logging.DEBUG if on else logging.INFO)


def is_verbose() -> bool:
    return _verbose


def _probe(fn) -> Any:
    """Run a probe, returning its value or a string describing the failure.

    A snapshot is taken on an already-broken path; a probe that raises must
    degrade to a field, never replace the snapshot with an exception.
    """
    try:
        return fn()
    except Exception as exc:
        return f"<{type(exc).__name__}: {exc}>"


def _chain_walk(pm: Any) -> list[str]:
    """Each deref of the player HP chain, as raw hex.

    `chain_hp: null` alone cannot separate "no character yet" from "the base
    constant is stale after a game update" -- both read as null, and the two
    call for completely different responses (wait vs. re-run the pointer scan).
    The raw hops separate them: a first hop of 0x1 or 0xffffffff is not a heap
    pointer, so the constant moved.
    """
    import struct

    import reader

    walk: list[str] = []
    addr = reader.PLAYER_HP_CHAIN_BASE
    for off in reader.PLAYER_HP_CHAIN_OFFSETS:
        try:
            ptr = struct.unpack("<I", pm.read_bytes(addr, 4))[0]
        except Exception as exc:
            walk.append(f"<{type(exc).__name__} at {addr:#x}>")
            break
        walk.append(f"{ptr:#x}+{off:#x}")
        if ptr == 0 or ptr > 0x7FFFFFFF:
            break
        addr = ptr + off
    return walk


def snapshot_locate_failure(
    pm: Any,
    hp_addr: int | None = None,
    knowledge: dict | None = None,
    hp_value: int | None = None,
    score: float | None = None,
    failed_fields: list[str] | None = None,
) -> dict[str, Any]:
    """Structured context for a locate/read failure.

    Every declared key is always present so a consumer can read
    detail["bytes_hex"] without defensive probing.
    """
    snap: dict[str, Any] = {
        "hp_addr": hex(hp_addr) if isinstance(hp_addr, int) else None,
        "hp_value": hp_value,
        "score": score,
        "failed_fields": failed_fields,
        "process_alive": pm is not None,
        "chain_hp": None,
        "chain_walk": None,
        "compat_false": None,
        "compat_true": None,
        "bytes_hex": None,
    }
    if pm is None:
        return snap

    import reader

    snap["chain_hp"] = _probe(lambda: reader.read_hp_from_player_chain(pm))
    snap["chain_walk"] = _chain_walk(pm)
    if isinstance(hp_addr, int):
        snap["bytes_hex"] = _probe(lambda: pm.read_bytes(hp_addr, 32).hex())

    kb = knowledge if knowledge is not None else _probe(reader.load_knowledge)
    probe_hp = snap["chain_hp"] if isinstance(snap["chain_hp"], int) else hp_value
    if isinstance(kb, dict) and isinstance(probe_hp, int):
        for key, compat in (("compat_false", False), ("compat_true", True)):
            snap[key] = _probe(
                lambda c=compat: reader.locate_character(pm, probe_hp, kb, None, compat_mode=c)
            )
    return snap
