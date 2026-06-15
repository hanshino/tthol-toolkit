"""System backup envelope: build/parse the versioned JSON for snapshots.db.

Pure logic (no FastAPI, no pymem) so it is fully unit-testable. The merge
itself lives in SnapshotDB.import_merge (DB invariants stay in the DB layer);
this module only owns the file format / envelope.
"""

import json
from datetime import datetime

BACKUP_FORMAT = "tthol-memory-backup"
BACKUP_VERSION = 1

# Informational only (recorded in the file's envelope). Keep in sync with
# pyproject.toml [project].version; not resolvable via importlib.metadata
# because the project runs from source, not an installed distribution.
APP_VERSION = "1.2.1"


class BackupFormatError(ValueError):
    """Raised when a backup file's format/version is unsupported or unparseable.

    Carries an English message (project convention: Python stays English); the
    React layer renders the user-facing Chinese text on a 400.
    """


def build_backup(db, *, exported_at: str | None = None) -> dict:
    """Wrap a full db export in the versioned envelope."""
    data = db.export_all()
    return {
        "format": BACKUP_FORMAT,
        "version": BACKUP_VERSION,
        "exported_at": exported_at or datetime.now().isoformat(timespec="seconds"),
        "app_version": APP_VERSION,
        "accounts": data["accounts"],
        "character_accounts": data["character_accounts"],
        "snapshots": data["snapshots"],
    }


def parse_backup(raw: bytes) -> dict:
    """Decode + validate a backup file. Raises BackupFormatError on any problem.

    The compatibility gate is (format, version); content beyond that is left to
    the merge, which tolerates missing optional sections.
    """
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError) as exc:
        raise BackupFormatError("invalid backup file: not valid JSON") from exc
    if not isinstance(data, dict):
        raise BackupFormatError("invalid backup file: expected a JSON object")
    if data.get("format") != BACKUP_FORMAT or data.get("version") != BACKUP_VERSION:
        raise BackupFormatError("unsupported backup file format or version")
    return data
