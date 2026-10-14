"""Dungeon scripts: what to do in each area of a dungeon.

The engine (services/dungeon.py) finds the area from the own tile and runs that
area's steps in order. Positions, portal conditions and landings come from the
DB (services/map_areas.py), so a script only names spawn groups, portal event
tags and NPC ids.

An area is done when its exit works, not when its groups die: a crystal, a
room group or a boss may be absent because it has not spawned yet. When every
step ran and the character is still in the area, the steps start again.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Clear:
    """Fight the live monsters of these spawn groups in this area; with none in
    view, walk the groups' spawn points once."""

    groups: tuple[int, ...]


@dataclass(frozen=True)
class Enter:
    """Step on a portal of this area. Done when the character lands somewhere
    else; a closed portal is an error for this pass of the area."""

    event: int


@dataclass(frozen=True)
class FirstOpen:
    """Step on each portal in order; the first one that works moves the
    character. None works: go on with the next step. The portals open in this
    order (凌霄閣: left, middle, right), so once one was taken the next pass
    starts after it, and after the last one the step is skipped."""

    events: tuple[int, ...]


@dataclass(frozen=True)
class Shortcut:
    """Try the exit before anything else, but only while no monster of this
    area is in view: someone else may have opened it already."""

    event: int


@dataclass(frozen=True)
class Hold:
    """Fight the `fight` groups as they respawn until the `until_dead` group
    shows up and dies (a boss that spawns after enough kills)."""

    fight: tuple[int, ...]
    until_dead: int


@dataclass(frozen=True)
class Talk:
    """Walk to an NPC of this map and pick dialog options by jump id.
    `mission`: only when the character's daily-mission setting is on.
    `optional`: the wanted option may be missing (already taken)."""

    npc: int
    want: tuple[int, ...]
    mission: bool = False
    optional: bool = False


Step = Clear | Enter | FirstOpen | Shortcut | Hold | Talk


@dataclass(frozen=True)
class Area:
    name: str
    stage: int
    landing: int  # a landing tag in the area (map_placements trigger), to find it
    steps: tuple[Step, ...]
    floor: str | None = None  # progress label; None: same as the area before


@dataclass(frozen=True)
class Dungeon:
    key: str  # also the 日常 module key
    title: str
    cooldown_h: float  # the game's re-entry timer; the client cannot read it
    entry_stage: int  # map with the entrance and the mission NPC
    entry: tuple[Step, ...]  # on entry_stage before the run
    areas: tuple[Area, ...]
    finish: tuple[Step, ...]  # on entry_stage after the last exit
    last_exit: int  # the portal that ends the run (back to entry_stage)
    floors: tuple[str, ...]  # progress ladder labels, in order
    chests: tuple[int, ...] = ()  # item ids counted in the bag before and after

    @property
    def stages(self) -> frozenset[int]:
        return frozenset(a.stage for a in self.areas)


def _c(*groups: int) -> Clear:
    return Clear(groups)


# 凌霄閣 (stage 42) with 凌霄天梯 (stage 60) between floors; entrance 凌霄閣入口 (100).
# Investigator 凌霄閣調查 7189: option 6653 (願意) takes daily mission 18906;
# after the run its first message jumps to the report 6655 (chest 31019). The last exit (20) gives chest 27229 without the mission.
LINGXIAO = Dungeon(
    key="lingxiao",
    title="凌霄閣",
    cooldown_h=4,
    entry_stage=100,
    entry=(Talk(7189, (6653,), mission=True, optional=True), Enter(257)),
    areas=(
        Area("大廳", 42, 1, (Shortcut(257), _c(11, 12), Enter(257)), floor="大廳"),
        Area("凌霄天梯", 60, 1, (Enter(257),)),
        Area(
            "紫微垣",
            42,
            3,
            (Shortcut(259), _c(15), FirstOpen((263, 265, 267)), _c(13, 14), Enter(259)),
            floor="紫微垣",
        ),
        Area("天樞之間", 42, 103, (_c(16), Enter(264))),
        Area("天璇之間", 42, 105, (_c(17), Enter(266))),
        Area("咒殺女鬼之間", 42, 107, (_c(18), Enter(268))),
        Area("凌霄天梯", 60, 3, (Enter(259),)),
        Area(
            "太微垣",
            42,
            5,
            (Shortcut(261), FirstOpen((269, 271, 273, 275)), _c(23, 24), Enter(261)),
            floor="太微垣",
        ),
        Area("開陽之間", 42, 109, (_c(20), Enter(270))),
        Area("搖光之間", 42, 111, (_c(19), Enter(272))),
        Area("天府之間", 42, 113, (_c(21), Enter(274))),
        Area("天梁之間", 42, 115, (_c(22), Enter(276))),
        Area("凌霄天梯", 60, 5, (Enter(261),)),
        Area("天機之間", 42, 117, (_c(28), Enter(278)), floor="天市垣"),
        Area("天同之間", 42, 119, (_c(49), Enter(280))),
        Area("天相之間", 42, 121, (_c(30), Enter(282))),
        Area("七殺之間", 42, 123, (_c(31), Enter(284))),
        Area("天市垣", 42, 124, (Shortcut(20), Hold((32,), 36), Enter(20))),
    ),
    # 6651 jumps to the report (6655) by itself once the run is done: no option.
    finish=(Talk(7189, (), mission=True, optional=True),),
    last_exit=20,
    floors=("大廳", "紫微垣", "太微垣", "天市垣"),
    chests=(27229, 31019),
)

DUNGEONS: tuple[Dungeon, ...] = (LINGXIAO,)
