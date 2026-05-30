import struct
from unittest.mock import MagicMock, patch

import pytest

from reader import HEAP_MIN_ADDR


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


# Candidate addresses must sit on the heap (>= HEAP_MIN_ADDR); real character
# structs never live in low static/module memory. Tests place the buffer at a
# heap base so the address-range guard does not reject the synthetic candidate.
_HEAP_BASE = HEAP_MIN_ADDR + 0x10000000
_POS = 228  # 4-byte aligned, leaves room for negative offsets down to -228


def _struct_buf(level=99):
    buf = bytearray(1024)
    struct.pack_into("<i", buf, _POS, 287)  # offset 0: hp
    struct.pack_into("<i", buf, _POS + 4, 287)  # offset 4: max_hp
    struct.pack_into("<i", buf, _POS + 8, 100)  # offset 8: mp
    struct.pack_into("<i", buf, _POS + 12, 100)  # offset 12: max_mp
    struct.pack_into("<i", buf, _POS + 24, 0)  # offset 24: weight
    struct.pack_into("<i", buf, _POS + 28, 1000)  # offset 28: max_weight
    struct.pack_into("<i", buf, _POS - 36, level)  # offset -36: level
    return buf


def _fake_pm(buf, base=_HEAP_BASE, read_int=None):
    pm = MagicMock()
    pm.process_handle = MagicMock()
    pm.read_bytes.return_value = bytes(buf)
    # Tests address memory absolutely (base + index); map back to buffer index.
    pm.read_int.side_effect = read_int or (
        lambda addr: struct.unpack_from("<i", buf, addr - base)[0]
    )
    return pm


def test_locate_character_respects_filters():
    """Candidate passes verify_structure but level(offset -36) is 99, not 7 -> filtered out."""
    from reader import locate_character, load_knowledge

    knowledge = load_knowledge()
    buf = _struct_buf()
    with patch("reader.get_memory_regions", return_value=[(_HEAP_BASE, len(buf))]):
        with patch("reader.verify_structure", return_value=1.0):
            result = locate_character(_fake_pm(buf), 287, knowledge, offset_filters={-36: 7})

    assert result is None  # filtered out because level(99) != 7


def test_locate_character_no_filters_keeps_candidate():
    """Without filters, candidate is found normally."""
    from reader import locate_character, load_knowledge

    knowledge = load_knowledge()
    buf = _struct_buf()
    with patch("reader.get_memory_regions", return_value=[(_HEAP_BASE, len(buf))]):
        with patch("reader.verify_structure", return_value=1.0):
            result = locate_character(_fake_pm(buf), 287, knowledge, offset_filters={})

    assert result == _HEAP_BASE + _POS  # found at correct heap address


def test_locate_character_filter_match_keeps_candidate():
    """Filter matches the actual value -> candidate is kept."""
    from reader import locate_character, load_knowledge

    knowledge = load_knowledge()
    buf = _struct_buf()
    with patch("reader.get_memory_regions", return_value=[(_HEAP_BASE, len(buf))]):
        with patch("reader.verify_structure", return_value=1.0):
            result = locate_character(_fake_pm(buf), 287, knowledge, offset_filters={-36: 99})

    assert result == _HEAP_BASE + _POS  # filter matches level=99, candidate kept


def test_locate_character_filter_read_error_drops_candidate():
    """If filter read_int raises, candidate is dropped but scan continues."""
    from reader import locate_character, load_knowledge

    knowledge = load_knowledge()
    buf = _struct_buf()

    def read_int_raises_on_filter(addr):
        if addr == _HEAP_BASE + _POS - 36:  # the filter offset read raises
            raise OSError("cannot read")
        return struct.unpack_from("<i", buf, addr - _HEAP_BASE)[0]

    with patch("reader.get_memory_regions", return_value=[(_HEAP_BASE, len(buf))]):
        with patch("reader.verify_structure", return_value=1.0):
            result = locate_character(
                _fake_pm(buf, read_int=read_int_raises_on_filter),
                287,
                knowledge,
                offset_filters={-36: 99},
            )

    assert result is None  # candidate dropped due to read error


def test_locate_character_rejects_static_low_address():
    """A struct-shaped match below the heap floor (static/module memory) is
    rejected even when verify_structure would score it 1.0 — this is the
    false-positive lock that froze a second client on dead memory at 0x00A3BE14.
    """
    from reader import locate_character, load_knowledge

    knowledge = load_knowledge()
    buf = _struct_buf()
    low_base = 0x00A30000  # below HEAP_MIN_ADDR, like the real false positive
    with patch("reader.get_memory_regions", return_value=[(low_base, len(buf))]):
        with patch("reader.verify_structure", return_value=1.0):
            result = locate_character(_fake_pm(buf, base=low_base), 287, knowledge)

    assert result is None  # rejected by the heap address-range guard


def test_locate_character_accepts_heap_address():
    """The same struct on the heap (>= HEAP_MIN_ADDR) is kept."""
    from reader import locate_character, load_knowledge

    knowledge = load_knowledge()
    buf = _struct_buf()
    with patch("reader.get_memory_regions", return_value=[(_HEAP_BASE, len(buf))]):
        with patch("reader.verify_structure", return_value=1.0):
            result = locate_character(_fake_pm(buf), 287, knowledge)

    assert result == _HEAP_BASE + _POS
