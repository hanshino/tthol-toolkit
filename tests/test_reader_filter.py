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
