from __future__ import annotations

import os
import time
from pathlib import Path

from xdp_form_cli.job_cleanup import DEFAULT_TTL_SECONDS, sweep_expired_jobs


def _make_job_dir(storage: Path, name: str, age_seconds: float) -> Path:
    job_dir = storage / name
    job_dir.mkdir(parents=True)
    (job_dir / "input.pdf").write_bytes(b"%PDF-1.4 fake")
    stamp = time.time() - age_seconds
    os.utime(job_dir, (stamp, stamp))
    return job_dir


def test_sweep_removes_expired_job_dirs_and_keeps_fresh_ones(tmp_path: Path) -> None:
    expired = _make_job_dir(tmp_path, "a" * 32, age_seconds=7200)
    fresh = _make_job_dir(tmp_path, "b" * 32, age_seconds=60)

    deleted = sweep_expired_jobs(tmp_path, ttl_seconds=3600)

    assert not expired.exists()
    assert fresh.exists()
    assert deleted == [expired]


def test_sweep_removes_job_contents_recursively(tmp_path: Path) -> None:
    expired = _make_job_dir(tmp_path, "c" * 32, age_seconds=7200)
    (expired / "client_form_acroform.pdf").write_bytes(b"%PDF-1.4 out")
    (expired / "manifest.json").write_text("{}", encoding="utf-8")
    stamp = time.time() - 7200
    os.utime(expired, (stamp, stamp))

    sweep_expired_jobs(tmp_path, ttl_seconds=3600)

    assert not expired.exists()


def test_sweep_handles_missing_storage_dir(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"

    deleted = sweep_expired_jobs(missing, ttl_seconds=3600)

    assert deleted == []


def test_sweep_ignores_stray_files_in_storage_dir(tmp_path: Path) -> None:
    stray = tmp_path / "notes.txt"
    stray.write_text("keep me", encoding="utf-8")
    old = time.time() - 7200
    os.utime(stray, (old, old))

    deleted = sweep_expired_jobs(tmp_path, ttl_seconds=3600)

    assert stray.exists()
    assert deleted == []


def test_default_ttl_is_six_hours() -> None:
    assert DEFAULT_TTL_SECONDS == 6 * 3600
