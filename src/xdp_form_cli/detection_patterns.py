"""Configurable label patterns for heuristic field detection.

Patterns map label text to a logical field type and a suggested width in
PDF points. Users can extend or override the defaults with their own
``detection-patterns.json`` file::

    {
      "patterns": [
        {"match": "מספר עוסק", "type": "text", "width": 99}
      ],
      "checkbox_glyphs": ["☐"]
    }

``match`` is a case-insensitive regular expression searched inside the
label. User patterns are checked before the defaults, so they win.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TEXT_WIDTH_PT = 150.0

# Glyphs that mark a checkbox drawn as a text character.
DEFAULT_CHECKBOX_GLYPHS = ("☐", "□", "◻")  # ☐ □ ◻

_DEFAULT_PATTERN_ROWS: list[dict[str, object]] = [
    {"match": r"signature|sign here|חתימה|חתימת", "type": "signature", "width": 180},
    {"match": r"\bdate\b|תאריך", "type": "date", "width": 80},
    {"match": r"\baddress\b|כתובת", "type": "text", "width": 240},
    {"match": r"\bphone\b|\bfax\b|טלפון|נייד|פקס", "type": "text", "width": 120},
    {"match": r"\be-?mail\b|דוא\"ל|דואל|מייל", "type": "text", "width": 180},
    {"match": r"\bid\b|ת\.?ז\.?|תעודת זהות|ח\.?פ\.?", "type": "text", "width": 100},
    {"match": r"account|מספר חשבון", "type": "text", "width": 140},
    {"match": r"amount|סכום", "type": "text", "width": 100},
]


@dataclass(frozen=True)
class LabelPattern:
    regex: re.Pattern[str]
    field_type: str
    width: float


@dataclass(frozen=True)
class DetectionPatterns:
    patterns: tuple[LabelPattern, ...]
    checkbox_glyphs: tuple[str, ...]


def load_patterns(path: str | Path | None) -> DetectionPatterns:
    """Load default patterns, optionally merged with a user JSON file.

    User patterns take priority over the defaults.
    """
    rows = list(_DEFAULT_PATTERN_ROWS)
    glyphs = list(DEFAULT_CHECKBOX_GLYPHS)
    if path is not None:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        user_rows = data.get("patterns", [])
        if not isinstance(user_rows, list):
            raise ValueError("'patterns' must be a list in the patterns file.")
        rows = user_rows + rows
        for glyph in data.get("checkbox_glyphs", []):
            if glyph not in glyphs:
                glyphs.append(glyph)

    compiled: list[LabelPattern] = []
    for row in rows:
        try:
            regex = re.compile(str(row["match"]), re.IGNORECASE)
            field_type = str(row.get("type", "text"))
            width = float(row.get("width", DEFAULT_TEXT_WIDTH_PT))
        except (KeyError, re.error, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid pattern row {row!r}: {exc}") from exc
        compiled.append(LabelPattern(regex, field_type, width))
    return DetectionPatterns(tuple(compiled), tuple(glyphs))


def classify_label(label: str, patterns: DetectionPatterns) -> tuple[str, float]:
    """Return ``(field_type, suggested_width_pt)`` for a label."""
    for glyph in patterns.checkbox_glyphs:
        if glyph in label:
            return "checkbox", 12.0
    for pattern in patterns.patterns:
        if pattern.regex.search(label):
            return pattern.field_type, pattern.width
    return "text", DEFAULT_TEXT_WIDTH_PT
