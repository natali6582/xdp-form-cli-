from __future__ import annotations

import subprocess
from pathlib import Path

import pikepdf
import pytest

import xdp_form_cli.field_validation as field_validation_module
from xdp_form_cli.field_validation import _validate_original_content_overlap, SUBPROCESS_TIMEOUT_SECONDS


def _write_simple_pdf(path: Path) -> Path:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(300, 400))
    pdf.save(path)
    return path


class TestSubprocessTimeoutHardening:
    """Verify that pdftoppm subprocess degrades gracefully on timeout."""

    def test_overlap_check_returns_warn_issue_on_subprocess_timeout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = _write_simple_pdf(tmp_path / "simple.pdf")

        # Pretend pdftoppm is installed.
        monkeypatch.setattr(field_validation_module.shutil, "which", lambda name: "/usr/bin/pdftoppm")

        def _raise_timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd=args[0], timeout=60)

        monkeypatch.setattr(field_validation_module.subprocess, "run", _raise_timeout)

        from xdp_form_cli.field_validation import ParsedField

        fields = [
            ParsedField(
                page=1, name="txtTest", field_type="text",
                x=50.0, y=50.0, w=100.0, h=12.0, value="",
            )
        ]

        issues = _validate_original_content_overlap(source, fields)

        # Must produce exactly one WARN issue (not raise, not crash).
        assert len(issues) == 1
        assert issues[0].severity == "WARN"
        assert issues[0].code == "original-content-check-skipped"

    def test_subprocess_timeout_constant_is_positive_integer(self) -> None:
        assert isinstance(SUBPROCESS_TIMEOUT_SECONDS, int)
        assert SUBPROCESS_TIMEOUT_SECONDS > 0
