"""Tests for the click-to-walk planner (services/walk_path.py)."""

from services import walk_path as wp
from services.walk_path import MapGrid


def grid(rows: list[str], arrivals=(), npcs=()) -> MapGrid:
    """Build a grid from text rows drawn top-down ('.' open, '#' wall), like the map image."""
    h, w = len(rows), len(rows[0])
    walkable = {
        (x, h - 1 - r) for r, line in enumerate(rows) for x, ch in enumerate(line) if ch == "."
    }
    return MapGrid(w, h, walkable, list(arrivals), list(npcs))


OPEN = ["." * 30] * 20


def straight_line_ok(g: MapGrid, a, b, cells) -> bool:
    def centre(c):
        return c[0] + 0.5, c[1] + 0.5

    return wp._traverse(centre(a), centre(b), lambda c, r: (c, r) in cells)


def test_click_point_matches_the_probe():
    # Probe: (+80, 0) went two tiles east, (0, -80) two tiles north.
    assert wp.click_point((73, 122), (75, 122)) == (480, 300)
    assert wp.click_point((75, 122), (75, 124)) == (400, 220)


def test_open_field_reaches_goal_and_every_hop_is_clickable():
    g = grid(OPEN)
    r = wp.plan_on(g, (2, 10), (27, 10))
    assert r.reason is None and r.hops[-1] == (27, 10)
    prev = (2, 10)
    for hop in r.hops:
        assert wp.clickable(*wp.click_point(prev, hop))
        prev = hop


def test_detours_around_a_wall_with_straight_hops():
    rows = ["." * 20 for _ in range(15)]
    for r in range(2, 15):  # wall in column 10, gap at the top two rows
        rows[r] = rows[r][:10] + "#" + rows[r][11:]
    g = grid(rows)
    r = wp.plan_on(g, (5, 2), (15, 2))
    assert r.reason is None and r.hops[-1] == (15, 2)
    prev = (5, 2)
    for hop in r.hops:
        assert straight_line_ok(g, prev, hop, g.walkable)
        prev = hop
    assert any(y >= 13 for _, y in r.hops)  # went through the gap


def test_unreachable_and_unwalkable_targets():
    rows = ["....#....", "....#....", "....#...."]
    g = grid(rows)
    assert wp.plan_on(g, (1, 1), (7, 1)).reason == "target is not reachable on foot"
    g2 = grid(["." * 5 + "#" * 10])
    assert wp.plan_on(g2, (1, 0), (12, 0)).reason == "target is not walkable"


def test_avoids_teleport_zone_unless_it_is_the_goal():
    arrival = [(15, 10, 7)]
    g = grid(OPEN, arrivals=arrival)
    zone = wp._teleport_cells(g, (25, 10))
    r = wp.plan_on(g, (8, 10), (22, 10))
    assert r.reason is None
    prev = (8, 10)
    for hop in r.hops:
        assert hop not in zone
        assert straight_line_ok(g, prev, hop, g.walkable - zone)
        prev = hop
    # Asking for the exit itself is allowed.
    assert wp.plan_on(g, (8, 10), (15, 10)).hops[-1] == (15, 10)


def test_hops_never_land_on_npc_sprites():
    g = grid(OPEN, npcs=[(12, 10)])
    sprite = wp._npc_sprite_cells(g)
    r = wp.plan_on(g, (4, 10), (20, 10))
    assert r.reason is None
    assert not [h for h in r.hops if h in sprite]


def test_no_click_rects_cover_the_ui():
    assert not wp.clickable(400, 500)  # 個人狀態 expand zone
    assert not wp.clickable(100, 520)  # chat
    assert not wp.clickable(100, 20)  # skill bar
    assert not wp.clickable(400, 595)  # bottom bar / client edge
    assert wp.clickable(400, 380)


def test_city_route_skips_the_room_door():
    # 成都少城: walking north past the door at arrival (84,165) teleported the
    # probe character into a room; a route to the north-east must go around it.
    g = wp.load_grid(53)
    assert g is not None
    goal = (100, 170)
    zone = wp._teleport_cells(g, goal)
    assert (84, 165) in zone and (83, 163) in zone
    r = wp.plan_on(g, (83, 150), goal)
    assert r.reason is None and r.hops[-1] == goal
    assert not [h for h in r.hops if h in zone]


def test_walk_endpoint_returns_map_pixels():
    from fastapi.testclient import TestClient

    from services.api import build_app

    r = TestClient(build_app()).get("/api/maps/53/walk?x=75&y=124&tx=89&ty=121")
    assert r.status_code == 200
    body = r.json()
    assert body["start"] == {"x": 75, "y": 124}
    assert body["goal"] == body["hops"][-1]
    assert body["reason"] is None


def test_one_tag_spread_over_doors_only_frees_the_goal_door():
    # Same tag on two doors far apart, plus untagged arrivals: going to one door
    # must still treat the other as a wall.
    g = grid(OPEN, arrivals=[(5, 10, 7), (25, 10, 7), (15, 3, None), (15, 17, None)])
    zone = wp._teleport_cells(g, (25, 10))
    assert (5, 10) in zone and (15, 3) in zone and (15, 17) in zone
    assert (25, 10) not in zone
    r = wp.plan_on(g, (2, 14), (25, 10))
    assert r.reason is None and r.hops[-1] == (25, 10)


def test_start_next_to_a_door_walks_out_of_it():
    g = grid(OPEN, arrivals=[(15, 10, 7)])
    for start in ((15, 10), (15, 11)):
        r = wp.plan_on(g, start, (25, 10))
        assert r.reason is None and r.hops[-1] == (25, 10)


def test_goal_snaps_and_partial_plan_reports_goal():
    g = grid(["." * 10 + "#" * 3 + "." * 10] * 5)
    r = wp.plan_on(g, (2, 2), (10, 2))  # wall tile: snaps to the nearest open one
    assert r.goal == (9, 2) and r.hops[-1] == (9, 2)
    same = wp.plan_on(g, (2, 2), (2, 2))
    assert same.reason is None and same.hops == [] and same.goal == (2, 2)
