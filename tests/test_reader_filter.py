import pytest


def test_parse_filters_returns_dict():
    from reader import parse_filters

    result = parse_filters(["等級=7", "真氣=150"])
    assert result == {"等級": 7, "真氣": 150}


def test_parse_filters_empty():
    from reader import parse_filters

    assert parse_filters([]) == {}


def test_parse_filters_invalid_value_raises():
    from reader import parse_filters

    with pytest.raises(SystemExit):
        parse_filters(["等級=abc"])


def test_parse_filters_no_equals_raises():
    from reader import parse_filters

    with pytest.raises(SystemExit):
        parse_filters(["等級abc"])


def test_parse_filters_empty_name_raises():
    from reader import parse_filters

    with pytest.raises(SystemExit):
        parse_filters(["=7"])


def test_resolve_filters_maps_name_to_offset():
    from reader import resolve_filters, load_knowledge

    knowledge = load_knowledge()
    filters = {"等級": 7, "真氣": 150}
    result = resolve_filters(filters, knowledge)
    assert result == {-36: 7, 8: 150}


def test_resolve_filters_unknown_field_raises():
    from reader import resolve_filters, load_knowledge

    knowledge = load_knowledge()
    with pytest.raises(SystemExit):
        resolve_filters({"不存在欄位": 1}, knowledge)


def test_resolve_filters_unknown_name_raises():
    from reader import resolve_filters, load_knowledge

    knowledge = load_knowledge()
    with pytest.raises(SystemExit):
        resolve_filters({"未知": 1}, knowledge)


from unittest.mock import MagicMock, patch
import struct


def test_locate_character_respects_filters():
    """Candidate passes verify_structure but level(offset -36) is 99, not 7 -> filtered out."""
    from reader import locate_character, load_knowledge

    knowledge = load_knowledge()

    hp = 287
    # Build a buffer with hp at pos=228 (room for negative offsets down to -228)
    buf = bytearray(1024)
    pos = 228  # 4-byte aligned
    struct.pack_into("<i", buf, pos, hp)  # offset 0: hp
    struct.pack_into("<i", buf, pos + 4, hp)  # offset 4: max_hp
    struct.pack_into("<i", buf, pos + 8, 100)  # offset 8: mp
    struct.pack_into("<i", buf, pos + 12, 100)  # offset 12: max_mp
    struct.pack_into("<i", buf, pos + 24, 0)  # offset 24: weight
    struct.pack_into("<i", buf, pos + 28, 1000)  # offset 28: max_weight
    struct.pack_into("<i", buf, pos - 36, 99)  # offset -36: level=99 (NOT 7)

    pm = MagicMock()
    pm.process_handle = MagicMock()
    with patch("reader.get_memory_regions", return_value=[(0, len(buf))]):
        with patch("reader.verify_structure", return_value=1.0):
            pm.read_bytes.return_value = bytes(buf)
            pm.read_int.side_effect = lambda addr: struct.unpack_from("<i", buf, addr)[0]
            result = locate_character(pm, hp, knowledge, offset_filters={-36: 7})

    assert result is None  # filtered out because level(99) != 7


def test_locate_character_no_filters_keeps_candidate():
    """Without filters, candidate is found normally."""
    from reader import locate_character, load_knowledge

    knowledge = load_knowledge()

    hp = 287
    buf = bytearray(1024)
    pos = 228
    struct.pack_into("<i", buf, pos, hp)
    struct.pack_into("<i", buf, pos + 4, hp)
    struct.pack_into("<i", buf, pos + 8, 100)
    struct.pack_into("<i", buf, pos + 12, 100)
    struct.pack_into("<i", buf, pos + 24, 0)
    struct.pack_into("<i", buf, pos + 28, 1000)
    struct.pack_into("<i", buf, pos - 36, 99)

    pm = MagicMock()
    pm.process_handle = MagicMock()
    with patch("reader.get_memory_regions", return_value=[(0, len(buf))]):
        with patch("reader.verify_structure", return_value=1.0):
            pm.read_bytes.return_value = bytes(buf)
            pm.read_int.side_effect = lambda addr: struct.unpack_from("<i", buf, addr)[0]
            result = locate_character(pm, hp, knowledge, offset_filters={})

    assert result == pos  # found at correct position
