import pytest

from services import route_plan as rp
from services._paths import bundled

pytestmark = pytest.mark.skipif(
    not bundled("tthol.sqlite").exists(), reason="tthol.sqlite not pulled"
)

TIANQIAO = 1121  # 天巧莊: the user's manor (0x31, 2026-10-05)


def hops(level: int, manor: int | None, src: int, dst: int) -> list[tuple[str, int]]:
    route = rp.plan(rp.build_graph(level, manor), src, dst)
    assert route is not None
    return [(s.kind, s.dst) for s in route.steps]


# Routes the user judged on 2026-10-05 (天巧莊, a high-level character).
@pytest.mark.parametrize(
    ("src", "dst", "want"),
    [
        (53, 30, [("family", 30)]),  # 成都少城 -> 天外天境
        (1, 51, [("town", 12), ("family", 51)]),  # 莫愁谷入口 -> 洛陽外城
        (53, 231, [("family", 75), ("walk", 228), ("walk", 231)]),  # -> 杭州城
        (2, 40, [("family", 51), ("walk", 188), ("walk", 40)]),  # 莫愁谷村莊 -> 碑林
        (9, 19, [("walk", 19)]),  # 藏海村 -> 塞外沙漠東
        (9, 53, [("family", 53)]),  # 藏海村 -> 成都少城
    ],
)
def test_reviewed_routes(src, dst, want):
    assert hops(120, TIANQIAO, src, dst) == want


def test_quest_passage_is_not_a_route():
    # 天外天一號道 -> 天外密道 warps only with mission steps running.
    route = hops(120, TIANQIAO, 30, 265)  # 天外天境 -> 月泉之森
    assert 708 not in [dst for _k, dst in route]
    assert route == [("family", 53), ("town", 264), ("walk", 265)]


def test_level_gate():
    # 成都城郊 -> 天山通道 needs level 20.
    g19 = rp.build_graph(19, None)
    assert 266 not in {e.step.dst for e in g19.edges.get(10, [])}
    g20 = rp.build_graph(20, None)
    assert 266 in {e.step.dst for e in g20.edges.get(10, [])}


def test_family_horse_follows_the_manor():
    t = rp._tables()

    def dests(manor):
        return {d for d, _tag, _horse in rp._family_dests(rp._Script(t, 120, manor))}

    assert dests(1001) == {2, 12, 30, 57}
    assert 23 in dests(1051)  # 檀泉別苑 from 地文莊 up
    assert 34 in dests(TIANQIAO) and 40 not in dests(TIANQIAO)  # 御風台 yes, 碑林 no
    assert {40, 84, 203} <= dests(1151)  # 天劍莊


def test_no_family_rides_town_horses_over_walking():
    # 藏海村 -> 成都少城 without a family: 藏海村馬夫 to 檀泉, then 檀泉別苑車伕.
    assert hops(120, None, 9, 53) == [("town", 23), ("town", 53)]


def test_chengdu_carriage():
    # User, 2026-10-05: 成都 rides straight to 曼陀羅城 (成都車伕), also to 狐隱村.
    assert hops(120, None, 53, 264) == [("town", 264)]
    assert hops(120, None, 53, 243) == [("town", 243)]


def test_story_map_never_used():
    g = rp.build_graph(120, None)
    assert all(e.step.dst != 311 for edges in g.edges.values() for e in edges)


def test_family_step_names_the_horse():
    # 峨嵋派 is on the 中階 menu: the 6347 horse, not the 低階 6382 by the door.
    route = rp.plan(rp.build_graph(120, TIANQIAO), 243, 36)  # 狐隱村 -> 峨嵋派
    assert [(s.kind, s.dst, s.horse) for s in route.steps] == [("family", 36, 6347)]


def test_out_of_a_room_and_into_one():
    g = rp.build_graph(120, TIANQIAO)
    # 成都少城 錢莊 (伙計 at (21,17)) is its own room: leave by its door first.
    out = rp.plan(g, 53, 2, start=(21, 17))  # -> 莫愁谷村莊
    assert [s.kind for s in out.steps] == ["door", "family"]
    # ...and going there from the street ends with the door in.
    into = rp.plan(g, 53, 53, start=(47, 168), goal_tile=(21, 17))
    assert [s.kind for s in into.steps] == ["door"]


def test_click_zones_are_marked():
    # 清音瀑布 crane statue (tag 258, event_kind 1) fires on a click; its exits do not.
    g = rp.build_graph(120, None)
    steps = {(e.step.kind, e.step.at): e.step.touch for e in g.edges[202]}
    assert steps[("door", rp._tables().middle(202, "arrival", 258))] is True
    assert all(not touch for (kind, _at), touch in steps.items() if kind == "walk")
