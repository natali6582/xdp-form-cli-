"""Tests for xdp_form_cli.safe_io — output-overwrite protection and backup utilities."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from xdp_form_cli.safe_io import backup_if_exists, require_safe_output


# ---------------------------------------------------------------------------
# require_safe_output
# ---------------------------------------------------------------------------


class TestRequireSafeOutput:
    def test_passes_when_file_does_not_exist(self, tmp_path: Path) -> None:
        target = tmp_path / "output.pdf"
        # Must not raise
        require_safe_output(target)

    def test_passes_when_overwrite_is_true_and_file_exists(self, tmp_path: Path) -> None:
        target = tmp_path / "output.pdf"
        target.write_bytes(b"existing")
        # Must not raise
        require_safe_output(target, overwrite=True)

    def test_raises_file_exists_error_when_file_exists_without_overwrite(
        self, tmp_path: Path
    ) -> None:
        target = tmp_path / "output.pdf"
        target.write_bytes(b"existing")

        with pytest.raises(FileExistsError) as exc_info:
            require_safe_output(target)

        assert str(target) in str(exc_info.value)

    def test_error_message_mentions_overwrite_flag(self, tmp_path: Path) -> None:
        target = tmp_path / "existing.pdf"
        target.write_bytes(b"data")

        with pytest.raises(FileExistsError) as exc_info:
            require_safe_output(target)

        assert "--overwrite" in str(exc_info.value)

    def test_raises_even_when_overwrite_is_false_explicitly(self, tmp_path: Path) -> None:
        target = tmp_path / "output.pdf"
        target.write_bytes(b"data")

        with pytest.raises(FileExistsError):
            require_safe_output(target, overwrite=False)

    def test_accepts_path_object(self, tmp_path: Path) -> None:
        target = tmp_path / "nonexistent.pdf"
        require_safe_output(Path(target))  # Must not raise

    def test_accepts_string_path(self, tmp_path: Path) -> None:
        target = tmp_path / "nonexistent.pdf"
        require_safe_output(str(target))  # Must not raise


# ---------------------------------------------------------------------------
# backup_if_exists
# ---------------------------------------------------------------------------


class TestBackupIfExists:
    def test_returns_none_when_file_does_not_exist(self, tmp_path: Path) -> None:
        target = tmp_path / "nonexistent.pdf"
        result = backup_if_exists(target)
        assert result is None

    def test_returns_backup_path_when_file_exists(self, tmp_path: Path) -> None:
        target = tmp_path / "output.pdf"
        target.write_bytes(b"original content")

        result = backup_if_exists(target)

        assert result is not None
        assert result != target

    def test_backup_file_contains_original_content(self, tmp_path: Path) -> None:
        target = tmp_path / "output.pdf"
        original_content = b"this is the original PDF content"
        target.write_bytes(original_content)

        backup_path = backup_if_exists(target)

        assert backup_path is not None
        assert backup_path.read_bytes() == original_content

    def test_original_file_still_exists_after_backup(self, tmp_path: Path) -> None:
        target = tmp_path / "output.pdf"
        target.write_bytes(b"original")

        backup_if_exists(target)

        assert target.exists()

    def test_backup_is_in_same_directory_as_original(self, tmp_path: Path) -> None:
        target = tmp_path / "output.pdf"
        target.write_bytes(b"data")

        backup_path = backup_if_exists(target)

        assert backup_path is not None
        assert backup_path.parent == target.parent

    def test_backup_name_contains_timestamp(self, tmp_path: Path) -> None:
        target = tmp_path / "output.pdf"
        target.write_bytes(b"data")

        backup_path = backup_if_exists(target)

        assert backup_path is not None
        # Timestamp pattern: digits in name
        import re
        assert re.search(r"\d{8}", backup_path.name), (
            f"Expected a date-like timestamp in backup name, got: {backup_path.name}"
        )

    def test_backup_name_shares_stem_with_original(self, tmp_path: Path) -> None:
        target = tmp_path / "my_form.pdf"
        target.write_bytes(b"data")

        backup_path = backup_if_exists(target)

        assert backup_path is not None
        assert backup_path.name.startswith("my_form")

    def test_backup_preserves_suffix(self, tmp_path: Path) -> None:
        target = tmp_path / "report.csv"
        target.write_bytes(b"col1,col2\n1,2\n")

        backup_path = backup_if_exists(target)

        assert backup_path is not None
        assert backup_path.suffix == ".csv"

    def test_two_calls_produce_distinct_backup_paths(self, tmp_path: Path) -> None:
        """Two backups of the same file must not collide (unique timestamp)."""
        target = tmp_path / "output.pdf"
        target.write_bytes(b"v1")
        backup1 = backup_if_exists(target)

        # Overwrite the file with new content
        target.write_bytes(b"v2")
        backup2 = backup_if_exists(target)

        assert backup1 != backup2

    def test_accepts_string_path(self, tmp_path: Path) -> None:
        target = tmp_path / "output.pdf"
        target.write_bytes(b"content")
        result = backup_if_exists(str(target))
        assert result is not None
        assert result.read_bytes() == b"content"
