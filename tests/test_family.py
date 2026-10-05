from services.family import FamilyTracker, decode_family

# 0x31 from pid 31276 on 2026-10-05 (tthol-hook capture), confirmed in game.
SAMPLE = bytes.fromhex(
    "31a4fdaacca142a4d1a455004947455237ab6eae63b45f5700393935000decc34548005a005e4e2d000c6104"
    "a4fd00a5a1fb007d00b14e000d0001740000a6da00808080808080a6da001b2b80801b2ba6da001f33ffffffff"
    "a6da000023000c6800"
)


class Settings:
    def __init__(self):
        self.rows = {}

    def get_setting(self, name, section):
        return self.rows.get((name, section))

    def set_setting(self, name, section, value):
        self.rows[(name, section)] = value


def test_decode_sample():
    info = decode_family(SAMPLE, 100.0)
    assert info is not None
    assert info.name == "王者、天下"
    assert (info.level, info.manor_id) == (12, 1121)
    assert (info.members, info.member_cap) == (72, 90)
    assert info.manor_name == "天巧莊"
    assert info.received_at == 100.0


def test_decode_rejects_other_packets():
    assert decode_family(b"\x32" + SAMPLE[1:], 0) is None
    assert decode_family(SAMPLE[:20], 0) is None


def test_tracker_keeps_last_by_name_across_restart():
    db = Settings()
    tracker = FamilyTracker(character_name=lambda pid: "阿明" if pid == 1 else None, db=db)
    assert tracker.get("阿明") is None
    tracker.on_packet(1, SAMPLE, 5.0, None)
    assert tracker.get("阿明").manor_id == 1121
    tracker.on_packet(2, SAMPLE, 6.0, None)  # pid not located: ignored
    fresh = FamilyTracker(character_name=lambda pid: None, db=db)
    assert fresh.get("阿明").name == "王者、天下"
    assert fresh.get("別人") is None
