# Import / Export — Design Spec

**Date:** 2026-06-14
**Status:** Draft, awaiting user review
**Codename:** 御心鑒 (yu xin jian)

---

## 1. Summary

Add two distinct, independent data-movement features. They share the underlying
`SnapshotDB` but have separate endpoints, formats, and UI entry points so the
user always knows which kind of operation they are performing.

| Feature | Audience | Scope | Format | Direction | UI home |
|---------|----------|-------|--------|-----------|---------|
| **System backup / restore** | system-level | the whole `snapshots.db` (all 3 tables) | versioned JSON | export + import (merge) | 留影 (Snapshots) page |
| **Treasury report** | end user | the current 帳房 aggregated view | CSV (UTF-8 + BOM) | export only | 帳房 (Treasury) page |

### Background

The original CSV export lived in the old PySide6 GUI
(`gui/inventory_manager_tab.py`, commit `929e706`) with two modes (detail +
summary). The UI redesign (commit `9faeeaf`) deleted `gui/` and **the export
was never re-implemented** in the FastAPI + React stack — the redesign spec
planned `POST /api/export/csv`, but no such endpoint exists in the current code.
This spec re-implements export in the new architecture and adds import.

The app is a **read-only** memory reader and never writes to game memory.
Therefore "import" means importing into the app's own `snapshots.db` — never
back into the game.

---

## 2. Goals and Non-Goals

### Goals
1. **System backup/restore** — export the entire `snapshots.db` content to a
   single file and merge it back, for reinstall / new-machine migration.
2. **Treasury report export** — revive the detail + summary CSV export, readable
   in Excel.
3. **Non-destructive import** — merge, never clobber (per user decision).
4. **No changes to `app.py` / pywebview** — pure-web download + upload.
5. **Testable without a running game** — all logic exercised by `uv run pytest`
   (no pymem, no Qt).

### Non-Goals (explicit out-of-scope)
- **CSV import** — CSV is lossy (names not ids, no checksum); not meaningful.
- **"Replace" import mode** — user chose merge only.
- **Encryption / compression** of backup files — payloads are small (item_id + qty).
- **Selective / partial backup** — backup = everything; report = treasury. Keep
  the two features cleanly separated.
- **Cloud sync.**
- **Round-trip of live character / buff / position state** — only persisted
  `snapshots.db` content is backed up (snapshots, accounts, character_accounts).

---

## 3. Feature A — System Backup / Restore

### 3.1 Backup file format (version 1)

A single `.json` file:

```jsonc
{
  "format": "tthol-memory-backup",
  "version": 1,
  "exported_at": "2026-06-14T12:34:56",   // ISO 8601, informational
  "app_version": "1.1.0",                  // informational
  "accounts": ["帳號甲", "帳號乙"],
  "character_accounts": [
    { "character": "角色A", "account": "帳號甲" }
  ],
  "snapshots": [
    {
      "character": "角色A",
      "source": "inventory",               // "inventory" | "warehouse"
      "scanned_at": "2026-06-10T09:00:00",
      "items": [ { "item_id": 1234, "qty": 5 } ]
    }
  ]
}
```

Design decisions:
- **Accounts are referenced by name, not id.** `character_accounts` points at an
  account *name*. This makes merge independent of `accounts.id` autoincrement, so
  importing onto another machine never collides on ids.
- **`checksum` is not stored in the file.** On import the server always recomputes
  the canonical checksum from `items` (see `_canonical` / `_checksum` in
  `snapshot_db.py`), preserving the DB invariant regardless of file contents.
- **`format` / `version` are the compatibility gate.** A future format change
  bumps `version`; unknown format/version is rejected with HTTP 400.

> Why JSON and not a raw `snapshots.db` file copy: the user requires **merge**
> semantics. A raw sqlite copy can only be restored by overwriting the whole
> file — you cannot merge two sqlite files without parsing them. Structured JSON
> still captures 100% of the db content while remaining mergeable and
> human-inspectable.

### 3.2 Import merge rules (non-destructive)

All steps run inside a **single transaction** — any failure rolls back the whole
import so a bad file never leaves the db half-merged.

1. **accounts** — for each name, `create_account()` (already idempotent: returns
   the existing id if the name exists).
2. **character_accounts** — for each entry:
   - character not yet assigned → assign it (resolving account name → id);
   - character already assigned to a *different* account → **keep existing, count
     as a conflict** (do not overwrite);
   - character already assigned to the *same* account → no-op.
3. **snapshots** — existence key `(character, source, scanned_at, checksum)`:
   - already exists → skip (count skipped);
   - otherwise insert, **preserving the original `scanned_at`**.

Consequence: re-importing the same file is **idempotent** (everything skipped).

Import returns a summary:

```
{ snapshots_added, snapshots_skipped, accounts_added,
  characters_assigned, account_conflicts }
```

The UI surfaces it as a toast: 「新增 N 筆 / 略過 M 筆 / 帳號衝突 J 個」.

### 3.3 Code changes

**`services/snapshot_db.py`** — add methods (existing methods untouched):
- `export_all() -> dict` — read all three tables: account names,
  `character_accounts` as character → account-name, and snapshot rows with raw
  `scanned_at` + parsed `items`.
- `snapshot_exists(character, source, scanned_at, checksum) -> bool`
- `import_merge(data: dict) -> dict` — the §3.2 merge, wrapped in one transaction
  (DB-invariant logic stays in the DB layer). Returns the summary dict.

> The existing `save_snapshot()` forces `scanned_at = now()` and only dedups
> against the *last* row, so it is unsuitable for restore. `import_merge` is a
> separate path; `save_snapshot()` is not changed.

**`services/backup.py`** (new — pure logic, easily unit-tested):
- `build_backup(db) -> dict` — call `export_all()`, wrap with the envelope
  (`format` / `version` / `exported_at` / `app_version`).
- `parse_backup(raw: bytes) -> dict` — JSON-decode and validate `format` /
  `version`; raise `BackupFormatError` on failure.

**`services/api/backup.py`** (new router, prefix `/api/backup`):
- `GET /api/backup/export` →
  `Response(json, media_type="application/json",
   headers={"Content-Disposition": "attachment; filename=tthol-backup-YYYYMMDD-HHMMSS.json"})`
- `POST /api/backup/import` (FastAPI `UploadFile`) →
  `parse_backup` → `db.import_merge` → return `BackupImportResult`.
  Malformed JSON or bad format/version → **HTTP 400** with a Chinese message.
- Register the router in `services/api/__init__.py`.

**`services/api_types.py`** — add:
```python
class BackupImportResult(_Base):
    snapshots_added: int
    snapshots_skipped: int
    accounts_added: int
    characters_assigned: int
    account_conflicts: int
```

---

## 4. Feature B — Treasury Report (CSV)

### 4.1 Endpoint

`GET /api/treasury/export.csv?mode=detail|summary`

- `Response(csv_text, media_type="text/csv; charset=utf-8",
   headers={"Content-Disposition": "attachment; filename=..."})`
- **Encode as UTF-8 with BOM (`utf-8-sig`)** so Windows Excel (cp950 locale)
  opens Chinese names without mojibake — matches the project's encoding caution.
- Build rows with the stdlib `csv` module into an `io.StringIO`.
- Data source reuses the existing path: `db.load_latest_snapshots()` plus the
  treasury router's `_aggregate()` — no new aggregation logic.
- Lives on the existing `treasury` router (it is treasury data).
- Empty db → header-only CSV. Unknown `mode` → default to `summary`.

### 4.2 Columns

**detail** — one row per (character × source × item):

`角色, 帳號, 來源, 道具ID, 道具名, 類型, 數量, 掃描時間`

- `來源` localized: `inventory` → 隨身, `warehouse` → 庫房.

**summary** — one row per item (revives the old summary mode):

`道具ID, 道具名, 類型, 身上, 庫房, 合計, 持有角色數`

- `持有角色數` = number of distinct characters holding the item.

---

## 5. Frontend (React)

**`webui/src/api/client.ts`** — add an `upload<T>(path, file)` helper
(multipart `fetch`). Downloads use a plain `<a href download>` link — no helper
needed.

**帳房 page (`Treasury.tsx`)** — add a 「匯出報表 ▾」control in the panel header
with two items, 明細 / 彙總, each linking to
`/api/treasury/export.csv?mode=detail|summary`.

**留影 page (`Snapshots.tsx`)** — add a 「系統備份」section:
- 「匯出備份」button → downloads `/api/backup/export`.
- 「匯入備份」button → triggers a hidden `<input type="file" accept=".json">`; on
  change, `upload()` to `/api/backup/import`, then show the summary toast and
  refresh the snapshot list. On 400, show the error message.

**Types** — after adding the models to `api_types.py`, run
`uv run scripts/gen_openapi.py` to regenerate `webui/src/api/schema.ts`
(generated file, not hand-edited).

---

## 6. Delivery mechanism (deviation note)

The 2026-05-03 redesign spec planned approach **B3** (server writes the file to a
fixed folder + `os.startfile` to open it). This spec uses approach **B1 — pure
web download / upload**:

- `GET` with `Content-Disposition: attachment` for export (WebView2 / browser
  downloads it; for a backup the user *wants* to choose where it lands).
- `<input type="file">` + multipart `POST` for import.

Rationale: no `app.py` change, no pywebview `js_api` bridge, identical behavior in
dev (Vite browser) and prod (webview), and fully testable via `curl` / pytest.

---

## 7. Error handling

- Import body is not JSON / fails to parse → 400「備份檔格式無效」.
- `format` / `version` mismatch → 400「不支援的備份檔格式或版本」.
- Missing optional sections tolerated (treated as empty).
- Any insert failure → whole import rolls back (transaction).
- `checksum` recomputed server-side; the file's value (if any) is never trusted.
- Export when `snapshot_db` service is absent → 503 (should not happen in the real app).

---

## 8. Testing (`uv run pytest`, pure Python, no pymem / Qt)

**`tests/test_backup.py`**
- Round-trip: seed a db → `build_backup` → import into a fresh db → assert
  snapshots / accounts / assignments are equal.
- Idempotent: import the same backup twice → second import adds 0, skips all.
- Merge: pre-existing data + import → only new rows added, existing kept, account
  conflict counted and not overwritten.
- Account remap: account ids differ between source and target db, names match →
  assignments resolve by name correctly.
- Format validation: bad `format` / `version` / non-JSON → raises `BackupFormatError`.

**`tests/test_treasury_csv.py`**
- detail / summary row counts and column headers.
- BOM present (`utf-8-sig`).
- Chinese item names survive a round-trip read.
- Empty db → header-only output.

**`tests/test_backup_router.py`** (FastAPI `TestClient`)
- `GET /api/backup/export` → correct `Content-Disposition` and JSON body.
- `POST /api/backup/import` multipart happy path → summary shape.
- Malformed upload → 400.

---

## 9. File change summary

| File | Change |
|------|--------|
| `services/snapshot_db.py` | add `export_all`, `snapshot_exists`, `import_merge` |
| `services/backup.py` | **new** — `build_backup`, `parse_backup`, `BackupFormatError` |
| `services/api/backup.py` | **new** — `/api/backup/export`, `/api/backup/import` |
| `services/api/treasury.py` | add `GET /export.csv?mode=` |
| `services/api/__init__.py` | register backup router |
| `services/api_types.py` | add `BackupImportResult` |
| `webui/src/api/client.ts` | add `upload()` helper |
| `webui/src/pages/Treasury.tsx` | add 匯出報表 control |
| `webui/src/pages/Snapshots.tsx` | add 系統備份 section (export + import) |
| `webui/src/api/schema.ts` | regenerated via `scripts/gen_openapi.py` |
| `tests/test_backup.py` | **new** |
| `tests/test_treasury_csv.py` | **new** |
| `tests/test_backup_router.py` | **new** |
