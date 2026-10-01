"""Keep tthol.sqlite out of git: the file lives on a GitHub Release, and the
repo only tracks db.lock.json, which pins the DB version this commit uses
(tag + sha256). Same scheme as the genbu project.

    uv run scripts/db_release.py pull [--force]   download the pinned DB (skips if already current)
    uv run scripts/db_release.py publish          upload the working DB as a new release, rewrite the lock

`pull` uses the public download URL and needs no token; `publish` needs a
logged-in gh CLI. DB releases are tagged db-YYYY-MM-DD and published as
prereleases that are never marked latest, so they do not trigger release.yml
(which builds on v* tags) and do not replace the app download on the
Releases page.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import urllib.request
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPO = "hanshino/tthol-toolkit"
DB_FILE = "tthol.sqlite"
DB_PATH = PROJECT_ROOT / DB_FILE
LOCK_PATH = PROJECT_ROOT / "db.lock.json"
CHUNK = 1 << 20


class DbReleaseError(Exception):
    pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def read_lock(path: Path = LOCK_PATH) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def release_url(lock: dict) -> str:
    return f"https://github.com/{REPO}/releases/download/{lock['tag']}/{lock['asset']}"


def download_locked(lock: dict, dest: Path, opener=urllib.request.urlopen) -> None:
    """Download the pinned DB to dest; replace dest only once the sha256 matches.

    Writes to a temp file first, so a failed or corrupt download never damages
    the existing file.
    """
    tmp = dest.with_name(dest.name + ".download")
    try:
        try:
            resp = opener(release_url(lock), timeout=60)
        except OSError as exc:
            raise DbReleaseError(
                f"Download of {lock['tag']} failed: {exc} (is the release published?)"
            ) from exc
        with resp, open(tmp, "wb") as f:
            while chunk := resp.read(CHUNK):
                f.write(chunk)
        actual = sha256_file(tmp)
        if actual != lock["sha256"]:
            raise DbReleaseError(f"sha256 mismatch: expected {lock['sha256']}, got {actual}")
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)


def pull(
    force: bool = False, db_path: Path = DB_PATH, lock_path: Path = LOCK_PATH, opener=None
) -> str:
    """Bring db_path to the pinned version. Returns "current" or "downloaded"."""
    lock = read_lock(lock_path)
    if lock is None:
        raise DbReleaseError(f"{lock_path.name} not found")
    if db_path.exists() and not force:
        if sha256_file(db_path) == lock["sha256"]:
            print(f"{DB_FILE} is already {lock['tag']}; nothing to download.")
            return "current"
        # Differs from the lock: may be a new DB not yet published. Never
        # overwrite it silently.
        raise DbReleaseError(
            f"The working {DB_FILE} differs from {lock_path.name} ({lock['tag']}).\n"
            f"If it is a new DB to ship, run: uv run scripts/db_release.py publish\n"
            f"To discard it and take the pinned version, add --force."
        )
    print(f"Downloading {lock['tag']} ({lock['size'] / 1024 / 1024:.1f} MB)...")
    download_locked(lock, db_path, **({"opener": opener} if opener else {}))
    print(f"{DB_FILE} is now {lock['tag']}.")
    return "downloaded"


def _gh(*args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["gh", *args], cwd=PROJECT_ROOT, capture_output=True, text=True, encoding="utf-8"
        )
    except FileNotFoundError as exc:
        raise DbReleaseError("gh CLI not found; install it and run `gh auth login`") from exc


def next_tag(exists=None, today: date | None = None) -> str:
    exists = exists or (lambda tag: _gh("release", "view", tag, "--repo", REPO).returncode == 0)
    base = f"db-{(today or date.today()).isoformat()}"
    n = 1
    while True:
        tag = base if n == 1 else f"{base}-{n}"
        if not exists(tag):
            return tag
        n += 1


def publish() -> None:
    if not DB_PATH.exists():
        raise DbReleaseError(f"{DB_FILE} not found")
    sha256 = sha256_file(DB_PATH)
    lock = read_lock()
    if lock and lock["sha256"] == sha256:
        print(f"{DB_FILE} matches {LOCK_PATH.name} ({lock['tag']}); nothing to publish.")
        return
    tag = next_tag()
    size = DB_PATH.stat().st_size
    print(f"Uploading {DB_FILE} as {tag} ({size / 1024 / 1024:.1f} MB)...")
    r = _gh(
        "release", "create", tag, str(DB_PATH),
        "--repo", REPO,
        "--prerelease",
        "--latest=false",
        "--title", f"{tag}（遊戲資料庫，開發用）",
        "--notes",
        "tthol.sqlite 遊戲資料庫，給開發與打包用；玩家請下載 v* 版本的 zip。\n\n"
        f"sha256: `{sha256}`",
    )  # fmt: skip
    if r.returncode != 0:
        raise DbReleaseError(f"gh release create failed: {(r.stderr or r.stdout).strip()}")
    new_lock = {"tag": tag, "asset": DB_FILE, "sha256": sha256, "size": size}
    LOCK_PATH.write_text(json.dumps(new_lock, indent=2) + "\n", encoding="utf-8")
    print(f"Published {tag} and updated {LOCK_PATH.name}. Commit {LOCK_PATH.name} and push.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_pull = sub.add_parser("pull", help="download the DB pinned in db.lock.json")
    p_pull.add_argument("--force", action="store_true", help="overwrite a differing local DB")
    sub.add_parser("publish", help="upload the working DB as a new release")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "pull":
            pull(force=args.force)
        else:
            publish()
    except DbReleaseError as exc:
        print(f"[X] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
