from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FieldPlacementSpec:
    name: str
    page: int
    x: float
    y: float
    w: float
    h: float
    field_type: str


@dataclass(frozen=True)
class DetectedUnderline:
    x0_pt: float
    x1_pt: float
    y_pt: float

    @property
    def width_pt(self) -> float:
        return self.x1_pt - self.x0_pt


@dataclass(frozen=True)
class PlacementAnalysisResult:
    field_name: str
    page: int
    underline: DetectedUnderline | None
    warnings: list[str]
    skipped_reason: str | None = None


def analyze_text_field_against_image(
    image_path: str | Path,
    *,
    page_height_pt: float,
    field: FieldPlacementSpec,
    dpi: int = 150,
    bottom_tolerance_pt: float = 2.0,
    width_tolerance_pt: float = 5.0,
    width_tolerance_ratio: float = 0.10,
) -> PlacementAnalysisResult:
    deps = _load_image_deps()
    if isinstance(deps, str):
        return PlacementAnalysisResult(field.name, field.page, None, [], skipped_reason=deps)

    Image, np = deps
    image = Image.open(image_path).convert("L")
    pixels = np.array(image)
    dark = pixels < 100
    scale = dpi / 72.0

    field_x0_px = max(0, int(round(field.x * scale)))
    field_x1_px = min(dark.shape[1], int(round((field.x + field.w) * scale)))
    field_bottom_px = int(round((page_height_pt - field.y) * scale))
    tolerance_px = max(1, int(round(bottom_tolerance_pt * scale)))

    underline = _find_best_underline(dark, field_x0_px, field_x1_px, field_bottom_px, tolerance_px, scale, page_height_pt)
    warnings: list[str] = []
    if underline is None:
        warnings.append("no horizontal underline detected near field bottom edge")
        return PlacementAnalysisResult(field.name, field.page, None, warnings)

    bottom_delta = abs(underline.y_pt - field.y)
    if bottom_delta > bottom_tolerance_pt:
        warnings.append(
            f"field bottom y={field.y:.2f}pt is {bottom_delta:.2f}pt from detected underline y={underline.y_pt:.2f}pt"
        )

    width_delta = abs(underline.width_pt - field.w)
    width_tolerance = max(width_tolerance_pt, abs(field.w) * width_tolerance_ratio)
    if width_delta > width_tolerance:
        warnings.append(
            f"field width={field.w:.2f}pt differs from detected underline width={underline.width_pt:.2f}pt by {width_delta:.2f}pt"
        )

    if _field_area_has_printed_text_above_line(dark, field, underline, scale, page_height_pt):
        warnings.append("field rectangle overlaps printed text above the underline")

    return PlacementAnalysisResult(field.name, field.page, underline, warnings)


def _load_image_deps() -> tuple[object, object] | str:
    try:
        from PIL import Image
    except ImportError:
        return "WARN: placement check skipped because Pillow is not installed"
    try:
        import numpy as np
    except ImportError:
        return "WARN: placement check skipped because numpy is not installed"
    return Image, np


def _find_best_underline(
    dark: object,
    field_x0_px: int,
    field_x1_px: int,
    field_bottom_px: int,
    tolerance_px: int,
    scale: float,
    page_height_pt: float,
) -> DetectedUnderline | None:
    height = dark.shape[0]
    y0 = max(0, field_bottom_px - tolerance_px)
    y1 = min(height - 1, field_bottom_px + tolerance_px)
    best: tuple[int, int, int, int] | None = None

    for row in range(y0, y1 + 1):
        for run_start, run_end in _dark_runs(dark[row]):
            overlap = max(0, min(run_end, field_x1_px) - max(run_start, field_x0_px))
            if overlap <= 0:
                continue
            run_width = run_end - run_start
            distance = abs(row - field_bottom_px)
            score = (overlap, run_width, -distance)
            if best is None or score > (best[0], best[1], -best[2]):
                best = (overlap, run_width, distance, row, run_start, run_end)

    if best is None:
        return None

    _, _, _, row, run_start, run_end = best
    return DetectedUnderline(
        x0_pt=run_start / scale,
        x1_pt=run_end / scale,
        y_pt=page_height_pt - (row / scale),
    )


def _dark_runs(row: object) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for index, value in enumerate(row):
        if bool(value):
            if start is None:
                start = index
        elif start is not None:
            if index - start >= 8:
                runs.append((start, index))
            start = None
    if start is not None and len(row) - start >= 8:
        runs.append((start, len(row)))
    return runs


def _field_area_has_printed_text_above_line(
    dark: object,
    field: FieldPlacementSpec,
    underline: DetectedUnderline,
    scale: float,
    page_height_pt: float,
) -> bool:
    x0 = max(0, int(round(field.x * scale)))
    x1 = min(dark.shape[1], int(round((field.x + field.w) * scale)))
    top = max(0, int(round((page_height_pt - (field.y + field.h)) * scale)))
    line_row = int(round((page_height_pt - underline.y_pt) * scale))
    bottom = max(top, line_row - max(2, int(round(1.5 * scale))))
    if x1 <= x0 or bottom <= top:
        return False

    area = dark[top:bottom, x0:x1]
    dark_count = int(area.sum())
    return dark_count > max(12, int(area.size * 0.002))
