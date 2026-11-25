"""One-time import of learning records from older Parrot data directories."""

from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path

MARKER = ".legacy-import-done"
LINK = "legacy-data-path"
SKIP_DIRS = {"models", "signing", "client-test-artifacts", "__pycache__", ".pycache"}


def support_dir() -> Path:
    override = os.environ.get("PARROT_SUPPORT_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / "Library/Application Support/Parrot"


def session_count(db_path: Path) -> int:
    if not db_path.is_file() or db_path.stat().st_size == 0:
        return 0
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as db:
            row = db.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='sessions'"
            ).fetchone()
            if not row or not row[0]:
                return 0
            count = db.execute("SELECT COUNT(*) FROM sessions").fetchone()
            return int(count[0]) if count else 0
    except sqlite3.Error:
        return 0


def legacy_candidates(target: Path) -> list[Path]:
    found: list[Path] = []
    link = target / LINK
    if link.is_file():
        text = link.read_text(encoding="utf-8").strip()
        if text:
            found.append(Path(text).expanduser())
    if explicit := os.environ.get("PARROT_LEGACY_DATA"):
        found.append(Path(explicit).expanduser())
        return _unique_paths(found)
    home = Path.home()
    for base in (
        home / "Desktop",
        home / "Documents",
        home / "Developer",
        home / "Projects",
        home / "code",
        home / "src",
    ):
        candidate = base / "Parrot" / ".data"
        if candidate.is_dir():
            found.append(candidate)
    return _unique_paths(found)


def _unique_paths(paths: list[Path]) -> list[Path]:
    ordered: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        try:
            key = str(path.resolve())
        except OSError:
            key = str(path)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(path)
    return ordered


def _copy_session_payload(source: Path, target: Path) -> None:
    for name in ("soundscribe.db", "soundscribe.db-wal", "soundscribe.db-shm"):
        src = source / name
        dst = target / name
        if src.is_file():
            shutil.copy2(src, dst)
        elif dst.exists():
            dst.unlink()
    for child in source.iterdir():
        if not child.is_dir() or child.name.startswith(".") or child.name in SKIP_DIRS:
            continue
        dest = target / child.name
        if dest.exists():
            continue
        shutil.copytree(child, dest)


def import_legacy_into(target: Path) -> Path | None:
    """One-shot bootstrap for the install store only.

    Install (`Application Support`) and dev (`.data`) stay separate databases.
    This copies from a legacy `.data` at most once, and only when the install
    store still has zero sessions. After that:

    - the `.legacy-import-done` marker blocks every later launch/upgrade
    - any existing install sessions are never overwritten by `.data`
    - later pkg upgrades (e.g. 1.0.1 → 1.0.3) keep using Application Support only
    """
    if os.environ.get("PARROT_DISABLE_LEGACY_IMPORT") == "1":
        return None
    target.mkdir(parents=True, exist_ok=True)
    marker = target / MARKER
    # Permanent latch: once decided, never import again on relaunch or upgrade.
    if marker.exists():
        return None
    current = session_count(target / "soundscribe.db")
    if current > 0:
        marker.write_text(f"skipped: already has {current} sessions\n", encoding="utf-8")
        return None

    best: Path | None = None
    best_count = 0
    try:
        target_key = str(target.resolve())
    except OSError:
        target_key = str(target)
    for source in legacy_candidates(target):
        if not source.is_dir():
            continue
        try:
            if str(source.resolve()) == target_key:
                continue
        except OSError:
            continue
        count = session_count(source / "soundscribe.db")
        if count > best_count:
            best = source
            best_count = count
    if not best or best_count == 0:
        marker.write_text("skipped: no legacy sessions found\n", encoding="utf-8")
        return None

    _copy_session_payload(best, target)
    marker.write_text(f"imported {best_count} sessions from {best}\n", encoding="utf-8")
    return best


def remember_legacy_path(data_dir: Path) -> None:
    """Leave a breadcrumb when a non-support data dir still holds records."""
    if os.environ.get("PARROT_DISABLE_LEGACY_IMPORT") == "1":
        return
    try:
        resolved = data_dir.resolve()
        if resolved == support_dir().resolve():
            return
    except OSError:
        return
    if session_count(data_dir / "soundscribe.db") <= 0:
        return
    support = support_dir()
    support.mkdir(parents=True, exist_ok=True)
    (support / LINK).write_text(str(resolved) + "\n", encoding="utf-8")
