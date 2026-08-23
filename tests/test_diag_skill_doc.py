from pathlib import Path

from services.diag_events import ErrorCode

SKILL = Path(".claude/commands/tthol-diag.md")


def test_skill_exists():
    assert SKILL.exists()


def test_every_error_code_is_documented():
    text = SKILL.read_text(encoding="utf-8")
    codes = [n for n in dir(ErrorCode) if n.startswith("E_")]
    missing = [c for c in codes if c not in text]
    assert missing == [], f"undocumented error codes: {missing}"


def test_skill_uses_uv_run_for_every_command():
    text = SKILL.read_text(encoding="utf-8")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("diag.py") or stripped.startswith("python diag.py"):
            raise AssertionError(f"command must be invoked via `uv run`: {stripped}")
    assert "uv run diag.py" in text


def test_skill_points_at_the_escalation_path():
    text = SKILL.read_text(encoding="utf-8")
    assert "tthol-update-scan" in text
    assert "runtime.json" in text
