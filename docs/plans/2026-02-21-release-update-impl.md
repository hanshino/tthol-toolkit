# Release & Auto-Update Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Set up a zero-installation, self-updating distribution using portable Python + Git
bundled in a zip, with a `start.bat` that auto-updates on every launch.

**Architecture:** Alas-style launcher — `start.bat` sets PATH to bundled toolkit, runs
`git pull`, syncs pip dependencies, then launches `gui_main.py`. The release zip bundles
portable Python 3.11 and portable Git so users need nothing pre-installed.

**Tech Stack:** Windows batch scripting, Python 3.11 embeddable, portable Git for Windows,
pip + requirements.txt (no uv at runtime).

---

### Task 1: Create requirements.txt

The deployed app uses pip directly (not uv). Extract pinned versions from the current
venv so the file is reproducible.

**Files:**
- Create: `requirements.txt`

**Step 1: Generate from the active venv**

```bash
uv pip freeze > requirements.txt
```

**Step 2: Verify the file looks reasonable**

Open `requirements.txt`. Expected: lines like `psutil==7.2.2`, `pymem==1.14.0`,
`pywin32==311`, `PySide6==6.x.x`, `PySide6-Addons==...`, `PySide6-Essentials==...`.
Remove any dev-only packages (e.g. `ruff`).

**Step 3: Commit**

```bash
git add requirements.txt
git commit -m "chore: add requirements.txt for pip-based deployment"
```

---

### Task 2: Add .gitattributes and update .gitignore

Mark `tthol.sqlite` as binary so git doesn't try to diff it. Ensure `tthol.sqlite`
is NOT in `.gitignore` (it should be committed).

**Files:**
- Create: `.gitattributes`
- Modify: `.gitignore`

**Step 1: Create .gitattributes**

```
# .gitattributes
tthol.sqlite binary
```

**Step 2: Check tthol.sqlite is tracked**

```bash
git status tthol.sqlite
```

Expected: either "nothing to commit" (already tracked) or "untracked files" (need to add).
If untracked, run `git add tthol.sqlite`.

**Step 3: Ensure tthol_inventory.db is ignored**

The existing `.gitignore` already has `*.db` which covers `tthol_inventory.db`. Verify:

```bash
git check-ignore -v tthol_inventory.db
```

Expected: `.gitignore:7:*.db    tthol_inventory.db`

**Step 4: Add toolkit/ to .gitignore**

The `toolkit/` directory (portable Python + Git) must NOT be committed — it is distributed
separately in the release zip. Add to `.gitignore`:

```
# Release toolkit (distributed in zip, not committed)
toolkit/
```

**Step 5: Commit**

```bash
git add .gitattributes .gitignore
git commit -m "chore: mark tthol.sqlite binary, ignore toolkit/"
```

---

### Task 3: Write start.bat

The single entry point users double-click. Handles UAC elevation, PATH setup, git pull,
pip install, and app launch.

**Files:**
- Create: `start.bat`

**Step 1: Write start.bat**

```bat
@echo off
setlocal EnableDelayedExpansion

:: ── UAC elevation ─────────────────────────────────────────────
:: If not running as admin, re-launch with elevation and exit.
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator privileges...
    powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

:: ── Paths ─────────────────────────────────────────────────────
set "_root=%~dp0"
set "PATH=%_root%toolkit\python;%_root%toolkit\python\Scripts;%_root%toolkit\git\cmd;%PATH%"

cd /d "%_root%"

:: ── Update ────────────────────────────────────────────────────
title Tthol Reader - Updating...
echo [1/2] Pulling latest code...
git pull --ff-only
if %errorlevel% neq 0 (
    echo.
    echo WARNING: git pull failed. Running with current version.
    echo Press any key to continue anyway...
    pause >nul
)

echo [2/2] Syncing dependencies...
python -m pip install -r requirements.txt -q
if %errorlevel% neq 0 (
    echo.
    echo ERROR: pip install failed. Cannot continue.
    pause
    exit /b 1
)

:: ── Launch ────────────────────────────────────────────────────
title Tthol Reader
python gui_main.py
if %errorlevel% neq 0 (
    echo.
    echo Application exited with an error.
    pause
)
```

**Step 2: Manual test (no toolkit yet — use system Python)**

Temporarily test with system Python by verifying the script logic:
- Double-click `start.bat` → UAC prompt should appear
- After elevation, it should attempt git pull and pip install
- It should eventually launch `gui_main.py`

Note: Full test with toolkit is done in Task 4 after building the zip.

**Step 3: Commit**

```bash
git add start.bat
git commit -m "feat: add start.bat launcher with auto-elevation and git pull update"
```

---

### Task 4: Build the release zip (manual checklist)

This task describes the one-time manual process to build the distributable zip.
No code changes needed — this is an ops checklist.

**Step 1: Download portable Python 3.11**

1. Go to https://www.python.org/downloads/windows/
2. Download "Windows embeddable package (64-bit)" for Python 3.11.x
3. Extract to `toolkit\python\` in the repo root

**Step 2: Enable pip in embeddable Python**

The embeddable package disables pip by default. Fix it:

1. Inside `toolkit\python\`, find `python311._pth` (or similar)
2. Uncomment the line `#import site` → `import site`
3. Download `get-pip.py` from https://bootstrap.pypa.io/get-pip.py
4. Run: `toolkit\python\python.exe get-pip.py`
5. Verify: `toolkit\python\python.exe -m pip --version`

**Step 3: Pre-install dependencies into the toolkit**

```bat
toolkit\python\python.exe -m pip install -r requirements.txt
```

This means users don't wait for a large download on first launch.

**Step 4: Download portable Git**

1. Go to https://git-scm.com/download/win
2. Download "64-bit Git for Windows Portable" (.exe self-extracting)
3. Extract to `toolkit\git\`
4. Verify: `toolkit\git\cmd\git.exe --version`

**Step 5: Initialize git in the folder**

The folder distributed to users must already be a git clone (so `git pull` works):

```bash
# From inside the tthol-memory folder (the release folder IS the repo):
git remote -v   # should show origin pointing to GitHub
```

The release zip is the repo root itself (minus `.venv`, with `toolkit/` added).

**Step 6: Zip the release**

Zip the repo root folder (excluding `.venv/`, `__pycache__/`, `.git/` is INCLUDED so
git pull works):

```
tthol-reader-v0.1.zip
└── tthol-reader/
    ├── .git/           ← required for git pull
    ├── toolkit/        ← portable Python + Git
    ├── gui/
    ├── start.bat
    ├── tthol.sqlite
    ├── requirements.txt
    └── ... (all other source files)
```

Use 7-Zip or PowerShell:
```powershell
Compress-Archive -Path "." -DestinationPath "..\tthol-reader-v0.1.zip" -Force
```
Run from inside the repo root. Then manually exclude `.venv/` if it got included.

**Step 7: Upload to GitHub Releases**

```bash
git tag v0.1
git push origin v0.1
```

Then on GitHub: Releases → Draft new release → tag `v0.1` → upload the zip.

---

### Task 5: Add README for users

**Files:**
- Create: `README.md`

**Step 1: Write README.md**

```markdown
# Tthol Reader

Tthol 遊戲記憶體讀取工具（唯讀，不修改遊戲）。

## 安裝

1. 從 [Releases](../../releases) 下載最新版 zip
2. 解壓縮到任意資料夾
3. 右鍵 `start.bat` → 以系統管理員身分執行
   （或直接雙擊，會自動要求提升權限）

## 使用

每次啟動會自動更新到最新版，完成後開啟主視窗。

## 常見問題

**防毒軟體誤報**：本工具需讀取遊戲 process 記憶體，部分防毒會誤判。
可將資料夾加入白名單，或至 GitHub 查看原始碼確認安全性。

**更新失敗**：請確認電腦能連上 GitHub (github.com)。
```

**Step 2: Commit**

```bash
git add README.md
git commit -m "docs: add user-facing README with install and usage instructions"
```
