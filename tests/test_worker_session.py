from services.char_session import CharSession


def test_link_lost_when_disconnected():
    sess = CharSession(pid=1234)
    assert sess.link == "lost"


def test_callbacks_update_state():
    sess = CharSession(pid=1234)
    sess._on_state("LOCATED")
    sess._on_stats(
        [
            ("角色名稱", "無塵"),
            ("等級", 20),
            ("血量", 100),
            ("最大血量", 120),
            ("真氣", 50),
            ("最大真氣", 60),
            ("負重", 30),
            ("最大負重", 200),
            ("X座標", 1),
            ("Y座標", 2),
        ]
    )
    sess.sect = "少林"
    row = sess.row()
    assert row is not None
    assert row.link == "ok"
    assert row.name == "無塵"
    assert row.vitals.hp == 100
    assert row.vitals.hp_max == 120
    assert row.position.x == 1
