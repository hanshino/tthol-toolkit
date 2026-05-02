from services.char_session import CharSession


def test_link_lost_when_disconnected():
    sess = CharSession(pid=1234)
    assert sess.link == "lost"


def test_callbacks_update_state():
    sess = CharSession(pid=1234)
    sess._on_state("LOCATED")
    sess._on_stats(
        [
            ("level", 20),
            ("hp", 100),
            ("hp_max", 120),
            ("mp", 50),
            ("mp_max", 60),
            ("weight", 30),
            ("weight_max", 200),
            ("x", 1),
            ("y", 2),
        ]
    )
    sess.name = "無塵"
    sess.sect = "少林"
    row = sess.row()
    assert row is not None
    assert row.link == "ok"
    assert row.vitals.hp == 100
