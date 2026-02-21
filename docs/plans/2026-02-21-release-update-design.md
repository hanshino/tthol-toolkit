# Release & Auto-Update Design

Date: 2026-02-21

## Goal

Distribute the Tthol memory reader to non-technical players with zero installation
requirements, and provide automatic updates on every launch via git pull.

## Pattern: Portable Toolkit + Git Pull on Launch (Alas-style)

Inspired by AzurLaneAutoScript. Ship a zip that includes portable Python and Git
so users need no pre-installed tools. Every launch auto-updates the codebase and
dependencies before starting the GUI.

## Release Artifact

```
tthol-reader-vX.Y.zip
├── toolkit/
│   ├── python/          # Python 3.11 Windows embeddable package
│   └── git/             # Portable Git for Windows
├── start.bat            # Entry point
├── start.exe            # bat-to-exe wrapper with icon (optional, reduces AV noise)
└── (all repo files cloned in here on first run or pre-populated)
```

## start.bat Behaviour (Every Launch)

1. Set `PATH` to include `toolkit\python` and `toolkit\git\cmd`
2. `git pull --ff-only` — pull latest code
3. `pip install -r requirements.txt` — sync dependencies
4. `python gui_main.py` — launch GUI

If git pull or pip fails, show error and pause (do not silently continue with stale code).

## Dependencies File

Use `requirements.txt` (not uv) for the deployed version — portable and universally
understood by pip without extra tooling.

The `pyproject.toml` / `uv.lock` remain for developer workflow.

## Data Files in Git

- `tthol.sqlite` — committed as binary directly; infrequently updated, small size
- Add `.gitattributes` entry: `tthol.sqlite binary` to suppress diff noise
- `tthol_inventory.db` — user-generated, excluded via `.gitignore`

## Admin Privileges

`pymem` requires reading another process's memory, which requires elevated privileges
on Windows. `start.bat` must request UAC elevation or document clearly that users
must right-click → "Run as administrator".

Preferred: auto-elevate inside the bat using a VBScript snippet so users do not
need to remember.

## GitHub Repository Layout

- `main` branch — stable releases only
- Development happens on feature branches, merged to `main` on release
- Tag each release: `git tag vX.Y && git push origin vX.Y`
- GitHub Releases page hosts the zip artifact for each tag

## Build Process (Manual for Now)

1. Download Python 3.11 embeddable zip, extract to `toolkit/python/`
2. Download portable Git, extract to `toolkit/git/`
3. `pip install -r requirements.txt --target toolkit/python/Lib/site-packages`
   (pre-warm packages so first launch is fast)
4. Zip everything → upload to GitHub Releases

Automation via GitHub Actions can be added later if release cadence increases.

## What Is NOT in Scope

- PyInstaller packaging (rejected: antivirus issues, complex for memory-reading apps)
- Auto-download of new zip (overkill for small version releases; git pull suffices)
- Version number check / update notification banner (can add later if needed)
