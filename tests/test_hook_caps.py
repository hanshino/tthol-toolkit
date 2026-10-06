from services.hook_caps import FEATURES, HookCaps
from services.hook_cmd import PipeBusy


class Channel:
    def __init__(self, *cmds, raise_=None):
        self.cmds = list(cmds)
        self.raise_ = raise_
        self.sent = 0

    def send(self, pid, line, priority=0):
        assert line == "caps"
        self.sent += 1
        if self.raise_:
            raise self.raise_
        return {"ok": True, "commands": [{"cmd": c} for c in self.cmds]}


def make(channel, connected=True):
    t = {"now": 0.0}
    caps = HookCaps(
        channel, clock=lambda: t["now"], connected=lambda pid: connected, background=False
    )
    return caps, t


def test_features_follow_the_manifest():
    caps, _ = make(Channel("use", *FEATURES["daily.tower"]))
    assert set(caps.features(1)) == {"chat", "guard", "daily.tower"}
    old, _ = make(Channel("use"))
    old.features(1)
    assert set(old.features(1)) == {"chat", "guard"}


def test_no_hook_pipe_no_features():
    ch = Channel("use")
    caps, _ = make(ch, connected=False)
    assert caps.features(1) == [] and ch.sent == 0


def test_a_new_hook_drops_the_old_manifest():
    ch = Channel("use")
    caps, _ = make(ch)
    caps.features(1)
    assert "daily.tower" not in caps.features(1)
    ch.cmds += list(FEATURES["daily.tower"])  # re-injected with more commands
    caps.invalidate(1)  # what the hook hub's hello does
    caps.features(1)
    assert "daily.tower" in caps.features(1)


def test_cached_reads_and_a_busy_pipe_backs_off():
    ch = Channel("use")
    caps, t = make(ch)
    caps.get(1, max_age=30)
    caps.get(1, max_age=30)
    assert ch.sent == 1
    busy = Channel(raise_=PipeBusy())
    caps, t = make(busy)
    caps.features(1)
    caps.features(1)
    assert busy.sent == 1  # retried only after RETRY
    t["now"] = 10.0
    caps.features(1)
    assert busy.sent == 2


def test_not_in_game_is_not_cached_as_no_manifest():
    import pytest

    from services.hook_caps import HookCaps
    from services.hook_cmd import NoReply

    class Channel:
        def send(self, pid, line):
            return {"ok": False, "error": "not in game: the game loop is idle"}

    caps = HookCaps(Channel(), connected=lambda pid: True)
    with pytest.raises(NoReply):
        caps.read(1)
    assert caps.cached(1) is None and 1 not in caps._entries
