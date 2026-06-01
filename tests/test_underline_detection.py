from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from xdp_form_cli.underline_detection import FieldPlacementSpec, analyze_text_field_against_image


def _render_fixture(tmp_path: Path) -> Path:
    pdftoppm = shutil.which("pdftoppm")
    if not pdftoppm:
        pytest.skip("pdftoppm is not installed")
    source = Path(__file__).parent / "fixtures" / "underline_fixture.pdf"
    prefix = tmp_path / "fixture"
    subprocess.run([pdftoppm, "-png", "-r", "150", "-f", "1", "-singlefile", str(source), str(prefix)], check=True)
    return prefix.with_suffix(".png")


def test_detects_underline_matching_field_bottom_and_width(tmp_path: Path) -> None:
    image_path = _render_fixture(tmp_path)
    field = FieldPlacementSpec(name="txtName", page=1, x=72, y=80, w=144, h=14, field_type="text")

    result = analyze_text_field_against_image(image_path, page_height_pt=200, field=field, dpi=150)

    assert result.skipped_reason is None
    assert result.underline is not None
    assert result.warnings == []
    assert result.underline.y_pt == pytest.approx(80, abs=1.0)
    assert result.underline.x0_pt == pytest.approx(72, abs=1.5)
    assert result.underline.width_pt == pytest.approx(144, abs=2.0)


def test_reports_width_mismatch_against_detected_underline(tmp_path: Path) -> None:
    image_path = _render_fixture(tmp_path)
    field = FieldPlacementSpec(name="txtName", page=1, x=72, y=80, w=100, h=14, field_type="text")

    result = analyze_text_field_against_image(image_path, page_height_pt=200, field=field, dpi=150)

    assert result.underline is not None
    assert any("width" in warning for warning in result.warnings)


def test_skips_with_warning_when_optional_image_dependency_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    original_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "PIL" or name.startswith("PIL."):
            raise ImportError("Pillow missing")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    field = FieldPlacementSpec(name="txtName", page=1, x=72, y=80, w=144, h=14, field_type="text")

    result = analyze_text_field_against_image("does-not-need-to-exist.png", page_height_pt=200, field=field)

    assert result.skipped_reason == "WARN: placement check skipped because Pillow is not installed"
    assert result.warnings == []
    assert result.underline is None
