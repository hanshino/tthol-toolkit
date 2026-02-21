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
