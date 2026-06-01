from __future__ import annotations

from dataclasses import dataclass


SINGLE_LINE_TEXT_MAX_HEIGHT = 12.0


@dataclass(frozen=True)
class FieldRect:
    x: float
    y: float
    w: float
    h: float
    changed: bool = False


def fit_field_rect(name: str, field_type: str, x: float, y: float, w: float, h: float) -> FieldRect:
    normalized_type = _normalize_type(field_type)
    if normalized_type != "text" or _keeps_original_height(name):
        return FieldRect(x, y, w, h, False)

    if h <= SINGLE_LINE_TEXT_MAX_HEIGHT:
        return FieldRect(x, y, w, h, False)

    fitted_h = SINGLE_LINE_TEXT_MAX_HEIGHT
    fitted_y = y
    return FieldRect(x, fitted_y, w, fitted_h, True)


def _keeps_original_height(name: str) -> bool:
    if name.startswith("txtBeneficiary"):
        return True
    return False


def _normalize_type(field_type: str) -> str:
    field_type = field_type.strip().lower()
    if field_type == "tx":
        return "text"
    return field_type
