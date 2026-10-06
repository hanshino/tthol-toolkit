"""The characters the batch dispatch can log in (one row each), secrets encrypted.

The user keeps one row per character; password and 保護密碼 belong to the
account, so saving them on one row updates every row with that username,
and a new row for a known account takes its stored secrets.
"""

from __future__ import annotations

from dataclasses import dataclass

from services import secret_box
from services.api_types import LoginEntry, LoginEntryIn

# Per-character settings an export carries along (character_settings
# sections), so the other computer can run the dispatch straight away.
# tower.record (today's tower progress) stays behind.
TRANSFER_SECTIONS = (
    "daily.queue",
    "tower",
    "combat",
    "guard.potion",
    "guard.buff",
    "items",
    "family",
)


@dataclass(frozen=True)
class LoginSecrets:
    """What the login flow types. Never logged or returned by the API."""

    character: str
    username: str
    server: str
    password: str
    protect: str | None


class LoginStore:
    def __init__(self, db, protect=secret_box.protect, unprotect=secret_box.unprotect) -> None:
        self._db = db
        self._protect = protect
        self._unprotect = unprotect

    def list(self) -> list[LoginEntry]:
        return [_entry(r) for r in self._db.login_rows()]

    def get(self, character: str) -> LoginEntry | None:
        return next((e for e in self.list() if e.character == character), None)

    def save(self, entry: LoginEntryIn) -> LoginEntry:
        rows = self._db.login_rows()
        mine = next((r for r in rows if r["character"] == entry.character), None)
        # A known account brings its stored secrets to a new row (or a row
        # moved to another account).
        same = next((r for r in rows if r["username"] == entry.username), None)
        keep = mine if mine is not None and mine["username"] == entry.username else same
        self._db.login_upsert(
            {
                "character": entry.character,
                "username": entry.username,
                "server": entry.server,
                "password": keep["password"] if keep else None,
                "protect": keep["protect"] if keep else None,
                "enabled": int(entry.enabled),
                "sort": entry.sort,
            }
        )
        for column, value in (("password", entry.password), ("protect", entry.protect)):
            if value is not None:
                blob = self._protect(value) if value else None
                self._db.login_set_secret(entry.username, column, blob)
        return self.get(entry.character)

    def update(self, entry: LoginEntryIn) -> LoginEntry | None:
        """Change a character already on the list; None when it is not (new
        characters come in from the game: 加入自動登入)."""
        if self.get(entry.character) is None:
            return None
        return self.save(entry)

    def delete(self, character: str) -> bool:
        return self._db.login_delete(character)

    def export_rows(self) -> list[dict]:
        """Every row with its secrets decrypted, for a sealed export only."""
        out = []
        for r in self._db.login_rows():
            out.append(
                {
                    "character": r["character"],
                    "username": r["username"],
                    "server": r["server"],
                    "password": self._unprotect(r["password"]) if r["password"] else None,
                    "protect": self._unprotect(r["protect"]) if r["protect"] else None,
                    "enabled": bool(r["enabled"]),
                    "sort": r["sort"],
                    "settings": self._settings(r["character"]),
                }
            )
        return out

    def _settings(self, character: str) -> dict[str, dict]:
        out = {}
        for section in TRANSFER_SECTIONS:
            data = self._db.get_setting(character, section)
            if data is not None:
                out[section] = data
        return out

    def import_rows(self, rows: list[dict], overwrite: bool) -> dict[str, int]:
        """Rows from an export. A character already here is replaced only with
        `overwrite`. The settings the file carries go in the same way, per
        section: without `overwrite` only the sections this computer lacks.
        Returns {added, updated, skipped, settings}; settings counts the
        characters that took any section."""
        counts = {"added": 0, "updated": 0, "skipped": 0, "settings": 0}
        start = len(self._db.login_rows())
        for r in rows:
            try:
                entry = LoginEntryIn(
                    character=r["character"],
                    username=r["username"],
                    server=r["server"],
                    enabled=bool(r.get("enabled", True)),
                    # "" clears: an exported row without a secret has none here either.
                    password=r.get("password") or "",
                    protect=r.get("protect") or "",
                    sort=int(r.get("sort", 0)),
                )
            except (KeyError, TypeError, ValueError):
                counts["skipped"] += 1
                continue
            if self._import_settings(entry.character, r.get("settings"), overwrite):
                counts["settings"] += 1
            known = self.get(entry.character) is not None
            if known and not overwrite:
                counts["skipped"] += 1
                continue
            if not known:
                entry = entry.model_copy(update={"sort": start + counts["added"]})
            self.save(entry)
            counts["updated" if known else "added"] += 1
        return counts

    def _import_settings(self, character: str, settings: object, overwrite: bool) -> bool:
        if not isinstance(settings, dict):
            return False
        took = False
        for section in TRANSFER_SECTIONS:
            data = settings.get(section)
            if not isinstance(data, dict):
                continue
            if overwrite or self._db.get_setting(character, section) is None:
                self._db.set_setting(character, section, data)
                took = True
        return took

    def secrets(self, character: str) -> LoginSecrets | None:
        """Decrypted, for the login flow only. None when the row or its password is missing."""
        row = next((r for r in self._db.login_rows() if r["character"] == character), None)
        if row is None or not row["password"]:
            return None
        return LoginSecrets(
            character=row["character"],
            username=row["username"],
            server=row["server"],
            password=self._unprotect(row["password"]),
            protect=self._unprotect(row["protect"]) if row["protect"] else None,
        )


def _entry(r: dict) -> LoginEntry:
    return LoginEntry(
        character=r["character"],
        username=r["username"],
        server=r["server"],
        enabled=bool(r["enabled"]),
        has_password=bool(r["password"]),
        has_protect=bool(r["protect"]),
        sort=r["sort"],
    )
