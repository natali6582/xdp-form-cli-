"""Safe file-output utilities: overwrite protection and timestamped backups.

These helpers enforce the project's irreversibility policy:
- No file may be silently overwritten without the caller explicitly opting in.
- When overwrite is permitted, a timestamped backup is created first so the
  previous content can always be recovered.
"""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path


def require_safe_output(path: str | Path, *, overwrite: bool = False) -> None:
    """Raise FileExistsError if *path* already exists and *overwrite* is False.

    Call this before any write operation that would replace an existing file.
    When *overwrite* is True the function returns without raising, allowing the
    caller to proceed (and ideally call :func:`backup_if_exists` first).
    """
    output = Path(path)
    if not overwrite and output.exists():
        raise FileExistsError(
            f"Output file already exists: {output}\n"
            "Pass --overwrite to replace it (a timestamped backup will be created automatically)."
        )


def backup_if_exists(path: str | Path) -> Path | None:
    """Copy *path* to a timestamped sibling file and return the backup path.

    Returns ``None`` when the file does not exist (nothing to back up).

    The backup name format is ``{stem}.bak-YYYYMMDD-HHMMSS{suffix}`` so it
    sits next to the original, is easy to find, and sorts chronologically.
    """
    output = Path(path)
    if not output.exists():
        return None

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup_name = f"{output.stem}.bak-{timestamp}{output.suffix}"
    backup_path = output.parent / backup_name
    shutil.copy2(output, backup_path)
    return backup_path
