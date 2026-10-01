"""Paper-doll frames for the character head avatar (hair + cap).

The worker reads the head / cap doll sequences the client draws (see
reader.read_appearance). This module turns them into image layers from the
bundled tthol.sqlite (`doll_frame_images`, rows from the tthol-data project).

Only the standing ('wait') frame is used, facing AVATAR_DIR. Directions without
their own art are drawn as the mirror of another one (`doll_slot_rules`), so
dir 6 loads dir 8 and flips it about the anchor. Head and cap both hang off the
same body point, so stacking them on one anchor is the whole layout.

An older DB without the doll tables still works: every avatar is just None.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path
from typing import NamedTuple

from services._paths import bundled
from services.api_types import Avatar, DollLayer

log = logging.getLogger("tthol.doll_catalog")

DB_PATH = bundled("tthol.sqlite")

AVATAR_DIR = 6
AVATAR_ACTION = "wait"
# Bottom to top; matches doll_slot_rules.z_order (head 4, cap 5) for every dir.
AVATAR_SLOTS = ("head", "cap")


class Frame(NamedTuple):
    url: str
    width: int
    height: int
    anchor_x: int
    anchor_y: int


def frame_path(gender: str, slot: str, sequence: int, color: int) -> str:
    """UI-facing path of the frame image endpoint (services.api.doll)."""
    return f"/api/doll/{gender}/{slot}/{sequence}.png?color={color}"


class DollCatalog:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        self._db_path = db_path
        # (gender, slot, sequence, color) -> frame, for the avatar's source dir
        self._frames: dict[tuple[str, str, int, int], Frame] | None = None
        self._mirror = False
        self._lock = threading.Lock()

    def _load(self) -> tuple[dict[tuple[str, str, int, int], Frame], bool]:
        try:
            con = sqlite3.connect(f"file:{self._db_path}?mode=ro", uri=True)
        except sqlite3.Error as exc:
            log.error("doll catalog unavailable: %s", exc, extra={"cat": "items"})
            return {}, False
        try:
            row = con.execute(
                "SELECT mirror_of FROM doll_slot_rules WHERE slot = 'head' AND dir = ?",
                (AVATAR_DIR,),
            ).fetchone()
            src_dir = row[0] if row and row[0] is not None else AVATAR_DIR
            placeholders = ",".join("?" * len(AVATAR_SLOTS))
            rows = con.execute(
                "SELECT gender, slot, sequence, color, url, width, height, anchor_x, anchor_y"
                " FROM doll_frame_images"
                f" WHERE action = ? AND dir = ? AND slot IN ({placeholders})",
                (AVATAR_ACTION, src_dir, *AVATAR_SLOTS),
            ).fetchall()
        except sqlite3.Error as exc:
            log.warning("doll tables missing; avatars fall back to the name: %s", exc)
            return {}, False
        finally:
            con.close()
        frames = {(g, s, seq, c): Frame(*rest) for g, s, seq, c, *rest in rows}
        return frames, src_dir != AVATAR_DIR

    def _ensure(self) -> dict[tuple[str, str, int, int], Frame]:
        with self._lock:
            if self._frames is None:
                self._frames, self._mirror = self._load()
            return self._frames

    def frame(self, gender: str, slot: str, sequence: int, color: int) -> Frame | None:
        """Frame for a layer; a cap (or an undyed head) only ships color 0."""
        frames = self._ensure()
        return frames.get((gender, slot, sequence, color)) or frames.get(
            (gender, slot, sequence, 0)
        )

    def avatar(self, appearance: dict | None) -> Avatar | None:
        """Head avatar layers for reader.read_appearance() output, or None."""
        if not appearance:
            return None
        gender = appearance["gender"]
        color = appearance["hair_color"]
        layers = []
        for slot in AVATAR_SLOTS:
            seq = appearance.get(slot)
            if seq is None:
                continue
            # Dye only applies to hair; caps ship color 0 only.
            layer_color = color if slot == "head" else 0
            frame = self.frame(gender, slot, seq, layer_color)
            if frame is None:
                if slot == "head":
                    return None  # no head art: a lone hat would look broken
                continue
            layers.append(
                DollLayer(
                    src=frame_path(gender, slot, seq, layer_color),
                    width=frame.width,
                    height=frame.height,
                    anchor_x=frame.anchor_x,
                    anchor_y=frame.anchor_y,
                )
            )
        return Avatar(mirror=self._mirror, layers=layers)


_catalog = DollCatalog()


def frame(gender: str, slot: str, sequence: int, color: int) -> Frame | None:
    return _catalog.frame(gender, slot, sequence, color)


def avatar(appearance: dict | None) -> Avatar | None:
    return _catalog.avatar(appearance)
