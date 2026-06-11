"""TTL cleanup for the web app job storage."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

DEFAULT_TTL_SECONDS = 6 * 3600


def sweep_expired_jobs(
    storage_dir: str | Path,
    *,
    ttl_seconds: float = DEFAULT_TTL_SECONDS,
    now: float | None = None,
) -> list[Path]:
    """Delete expired job directories directly under *storage_dir*."""
    storage = Path(storage_dir)
    if not storage.is_dir():
        return []

    cutoff = (now if now is not None else time.time()) - ttl_seconds
    deleted: list[Path] = []
    for entry in sorted(storage.iterdir()):
        if not entry.is_dir():
            continue
        try:
            if entry.stat().st_mtime > cutoff:
                continue
            shutil.rmtree(entry)
        except OSError:
            continue
        deleted.append(entry)
    return deleted
