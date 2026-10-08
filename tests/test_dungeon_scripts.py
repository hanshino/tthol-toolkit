import pytest

from services._paths import bundled
from services.dungeon_scripts import DUNGEONS, Clear, Enter, FirstOpen, Hold, Shortcut, Talk
from services.map_areas import load_areas

pytestmark = pytest.mark.skipif(
    not bundled("tthol.sqlite").exists(), reason="tthol.sqlite not pulled"
)


def _portals(step):
    if isinstance(step, (Enter, Shortcut)):
        return (step.event,)
    if isinstance(step, FirstOpen):
        return step.events
    return ()


def _groups(step):
    if isinstance(step, Clear):
        return step.groups
    if isinstance(step, Hold):
        return (*step.fight, step.until_dead)
    return ()


@pytest.mark.parametrize("dungeon", DUNGEONS, ids=lambda d: d.key)
def test_every_area_step_matches_the_db(dungeon):
    seen = set()
    for area in dungeon.areas:
        areas = load_areas("stage", area.stage)
        here = areas.landing_area(area.landing)
        assert here is not None, f"{area.name}: landing {area.landing} is not on a walkable part"
        assert (area.stage, here) not in seen, f"{area.name}: two script areas share one map area"
        seen.add((area.stage, here))
        for step in area.steps:
            for g in _groups(step):
                assert areas.group_area.get(g) == here, f"{area.name}: G{g} is not in this area"
            for event in _portals(step):
                portal = areas.portals.get(event)
                assert portal is not None, f"{area.name}: {event} is not a portal"
                assert portal.area == here, f"{area.name}: portal {event} is in another area"
            if isinstance(step, Talk):
                assert step.npc in areas.npcs, f"{area.name}: npc {step.npc} is not on this map"


@pytest.mark.parametrize("dungeon", DUNGEONS, ids=lambda d: d.key)
def test_every_portal_lands_in_a_script_area(dungeon):
    known = {(a.stage, load_areas("stage", a.stage).landing_area(a.landing)) for a in dungeon.areas}
    for area in dungeon.areas:
        areas = load_areas("stage", area.stage)
        for step in area.steps:
            for event in _portals(step):
                p = areas.portals[event]
                to_stage = area.stage if p.to_stage is None else p.to_stage
                if to_stage == dungeon.entry_stage:
                    assert event == dungeon.last_exit, f"{area.name}: {event} leaves early"
                    continue
                target = load_areas("stage", to_stage).landing_area(p.to_tag)
                assert (to_stage, target) in known, f"{area.name}: {event} lands outside the script"


@pytest.mark.parametrize("dungeon", DUNGEONS, ids=lambda d: d.key)
def test_entry_and_finish_are_on_the_entry_map(dungeon):
    areas = load_areas("stage", dungeon.entry_stage)
    for step in (*dungeon.entry, *dungeon.finish):
        if isinstance(step, Talk):
            assert step.npc in areas.npcs
        for event in _portals(step):
            p = areas.portals[event]
            assert p.to_stage in dungeon.stages
