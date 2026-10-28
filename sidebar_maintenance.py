"""Conservative low-frequency maintenance for the sidebar installation."""
from __future__ import annotations

import json
import fnmatch
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Iterable

DEFAULT_LOG_LIMIT = 2 * 1024 * 1024
MIN_LOG_LIMIT = 1 * 1024 * 1024
MAX_LOG_LIMIT = 2 * 1024 * 1024
CONNECTION_HISTORY_LIMIT = 20
BACKUP_LIMIT = 3
KNOWN_LOGS = ("interaction.log", "native-error.log", "error.log", "supervisor-error.log")
# Match outputs of the repository's preview tools, not arbitrary user images.
KNOWN_PREVIEW_PATTERNS = ("details-preview-*.png", "preview-*-tooltip.png", "quota-outer-signal-preview.png")


def _bounded_limit(limit: int) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError, OverflowError):
        value = DEFAULT_LOG_LIMIT
    return max(MIN_LOG_LIMIT, min(MAX_LOG_LIMIT, value))


def _plain_path(path: Path) -> bool:
    """Refuse symlinks and Windows junctions, including any parent redirect."""
    try:
        return all(not (part.is_symlink() or getattr(part.lstat(), "st_file_attributes", 0) & 0x400)
                   for part in (path.absolute(), *path.absolute().parents))
    except OSError:
        return False


def _signature(path: Path) -> tuple[int, int, int]:
    info = path.stat()
    return info.st_ino, info.st_size, info.st_mtime_ns


def rotate_log_tail(path: str | os.PathLike[str], limit: int = DEFAULT_LOG_LIMIT) -> bool:
    """Atomically retain the newest complete lines when a known log is too large."""
    target = Path(path)
    max_bytes = _bounded_limit(limit)
    try:
        if not _plain_path(target):
            return False
        original = _signature(target)
        if target.stat().st_size <= max_bytes:
            return False
        with target.open("rb") as source:
            source.seek(-max_bytes, os.SEEK_END)
            tail = source.read(max_bytes)
        first_newline = tail.find(b"\n")
        if first_newline >= 0:
            tail = tail[first_newline + 1 :]
        fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=str(target.parent))
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(tail)
                output.flush()
                os.fsync(output.fileno())
            if not _plain_path(target) or _signature(target) != original:
                return False  # A writer changed it while the tail was being read.
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return True
    except (OSError, ValueError):
        return False


def _atomic_json_write(path: Path, value: Any, original: tuple[int, int, int]) -> bool:
    try:
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as output:
                json.dump(value, output, ensure_ascii=False, separators=(",", ":"))
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            if not _plain_path(path) or _signature(path) != original:
                return False
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return True
    except (OSError, TypeError, ValueError):
        return False


def trim_connection_history(path: str | os.PathLike[str], limit: int = CONNECTION_HISTORY_LIMIT) -> bool:
    """Trim a valid connection-history array while preserving its newest entries."""
    target = Path(path)
    try:
        if not _plain_path(target):
            return False
        original = _signature(target)
        with target.open("r", encoding="utf-8") as source:
            value = json.load(source)
        if not isinstance(value, list):
            return False
        keep = max(0, int(limit))
        trimmed = value[-keep:] if keep else []
        if trimmed == value:
            return False
        return _atomic_json_write(target, trimmed, original)
    except (OSError, ValueError, TypeError, OverflowError, RecursionError):
        return False


def prune_update_backups(root: str | os.PathLike[str], limit: int = BACKUP_LIMIT) -> list[str]:
    """Prune only timestamp directories created by update.ps1; never follow links."""
    backup_root = Path(root) / ".update-backups"
    removed: list[str] = []
    try:
        if not _plain_path(backup_root):
            return removed
        entries = []
        for item in backup_root.iterdir():
            try:
                if re.fullmatch(r"\d{8}-\d{9}", item.name) and _plain_path(item) and item.is_dir():
                    entries.append((item.stat().st_mtime_ns, item))
            except OSError:
                continue
        entries.sort(key=lambda entry: (entry[0], entry[1].name), reverse=True)
        keep = max(0, int(limit))
        for _, item in entries[keep:]:
            try:
                if _plain_path(item):
                    shutil.rmtree(item)
                    removed.append(item.name)
            except OSError:
                continue
        return removed
    except (OSError, ValueError, TypeError, OverflowError):
        return removed


def _remove_matching(root: Path, patterns: Iterable[str]) -> list[str]:
    removed: list[str] = []
    try:
        for item in root.iterdir():
            try:
                if any(fnmatch.fnmatchcase(item.name, pattern) for pattern in patterns) and _plain_path(item) and item.is_file():
                    item.unlink()
                    removed.append(item.name)
            except OSError:
                continue
    except OSError:
        pass
    return removed


def clean_known_artifacts(root: str | os.PathLike[str]) -> dict[str, list[str]]:
    """Clean installation-root artifacts without scanning backups or dependencies."""
    base = Path(root)
    removed: dict[str, list[str]] = {"pycache": [], "previews": []}
    if not _plain_path(base):
        return removed
    cache = base / "__pycache__"
    try:
        if _plain_path(cache) and cache.is_dir():
            shutil.rmtree(cache)
            removed["pycache"].append(cache.name)
    except OSError:
        pass
    removed["previews"] = _remove_matching(base, KNOWN_PREVIEW_PATTERNS)
    return removed


def maintain(root: str | os.PathLike[str] | None = None, *, log_limit: int = DEFAULT_LOG_LIMIT) -> dict[str, Any]:
    """Run the allowlisted cleanup once; suitable for an infrequent caller."""
    base = Path(root) if root is not None else Path(__file__).resolve().parent
    rotated = [name for name in KNOWN_LOGS if rotate_log_tail(base / name, log_limit)]
    artifacts = clean_known_artifacts(base)
    return {
        "rotated_logs": rotated,
        "connection_history_trimmed": trim_connection_history(base / "connection-history.json"),
        "removed_backups": prune_update_backups(base),
        "removed_pycache": artifacts["pycache"],
        "removed_previews": artifacts["previews"],
    }


__all__ = ["clean_known_artifacts", "maintain", "prune_update_backups", "rotate_log_tail", "trim_connection_history"]
