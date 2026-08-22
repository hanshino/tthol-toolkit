from services.api_types import AutoClickStatus, CharacterRow, ErrorInfo, Position, Vitals


def _row(**kw) -> CharacterRow:
    base = dict(
        pid=1,
        name="無塵",
        sect="少林",
        link="ok",
        level=20,
        vitals=Vitals(hp=1, hp_max=2, mp=1, mp_max=2, weight=1, weight_max=2),
        position=Position(map_name=None, x=0, y=0),
        autoclick=AutoClickStatus(running=False),
    )
    base.update(kw)
    return CharacterRow(**base)


def test_last_error_defaults_to_none():
    assert _row().last_error is None


def test_last_error_roundtrips():
    row = _row(
        last_error=ErrorInfo(
            ts=1.0,
            message="Inventory not found in memory",
            cat="inventory",
            code="E_INV_NOT_FOUND",
        )
    )
    dumped = row.model_dump()
    assert dumped["last_error"]["code"] == "E_INV_NOT_FOUND"
    assert CharacterRow(**dumped).last_error.message == "Inventory not found in memory"


def test_error_info_code_is_optional():
    assert ErrorInfo(ts=1.0, message="m", cat="worker").code is None
