"""Heuristic form-field detection: PDF in, field-spec CSV out.

Deterministic algorithms only — no LLM, no ML, no AI APIs:

1. Vector geometry (boxes, underlines, checkbox squares) reused from the
   auto-form detector.
2. Label patterns (``detection-patterns.json``) that type fields and
   synthesize a field next to a ``Label:`` that has no drawn box.
3. Alignment clustering that snaps nearly-aligned coordinates to a common
   grid line, as forms usually align fields in columns and rows.
4. Rule-based naming from the nearest label (dictionary + transliteration).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import pikepdf

from xdp_form_cli.auto_form import (
    AutoFieldSpec,
    MAX_FIELD_HEIGHT_PT,
    TextAnchor,
    _box_contains_text,
    _detect_boxes,
    _detect_checkbox_boxes,
    _detect_underline_boxes,
    _extract_text_anchors,
    _nearest_label,
    _overlaps_any,
    write_field_csv,
)
from xdp_form_cli.detection_patterns import DetectionPatterns, classify_label, load_patterns
from xdp_form_cli.field_naming import generate_field_name

# Approximate average glyph advance at the 10pt label font, used to estimate
# where a label's text ends so a synthesized field can start after it.
LABEL_CHAR_WIDTH_PT = 5.5
SYNTH_FIELD_HEIGHT_PT = 14.0
SYNTH_GAP_PT = 4.0
# A ``Label:`` anchor with a drawn box on the same line within this horizontal
# reach already has its field; do not synthesize a duplicate.
SYNTH_SUPPRESS_REACH_PT = 300.0
SYNTH_LINE_TOLERANCE_PT = 10.0
# Coordinates closer than this snap to the same alignment grid line.
ALIGNMENT_SNAP_PT = 3.0

# Logical detection type -> supported field-spec CSV type.
CSV_TYPE_BY_LOGICAL = {
    "text": "text",
    "textarea": "textarea",
    "date": "text",
    "checkbox": "checkbox",
    "signature": "image",
    "image": "image",
}

# cp1255 Hebrew letters occupy 0xE0..0xFA; the same bytes decoded as latin-1
# become accented Latin characters. Map them back so Hebrew labels written
# with a cp1255-style encoding match the Hebrew patterns and dictionary.
_LATIN1_TO_HEBREW = {chr(0xE0 + i): chr(0x05D0 + i) for i in range(27)}


@dataclass(frozen=True)
class _Candidate:
    page: int
    label: str
    logical_type: str
    x: float
    y: float
    w: float
    h: float


def detect_fields(
    pdf_path: str | Path,
    *,
    patterns: DetectionPatterns | None = None,
    patterns_path: str | Path | None = None,
) -> list[AutoFieldSpec]:
    """Detect likely form fields on every page of a PDF."""
    if patterns is None:
        patterns = load_patterns(patterns_path)

    candidates: list[_Candidate] = []
    with pikepdf.Pdf.open(str(pdf_path)) as pdf:
        for page_index, page in enumerate(pdf.pages, start=1):
            anchors = [
                TextAnchor(_normalize_label(a.text), a.x, a.y)
                for a in _extract_text_anchors(page)
            ]
            candidates.extend(_page_candidates(page, page_index, anchors, patterns))

    candidates = _snap_alignment(candidates)
    return _name_candidates(candidates)


def write_detected_csv(specs: list[AutoFieldSpec], csv_path: str | Path) -> Path:
    """Write detected fields as a standard field-spec CSV."""
    return write_field_csv(specs, csv_path)


def _page_candidates(
    page: pikepdf.Page,
    page_index: int,
    anchors: list[TextAnchor],
    patterns: DetectionPatterns,
) -> list[_Candidate]:
    geo_boxes = _detect_boxes(page, page_index)
    underline_boxes = [
        b for b in _detect_underline_boxes(page, page_index)
        if not _overlaps_any(b, geo_boxes)
    ]
    checkbox_boxes = [
        b for b in _detect_checkbox_boxes(page, page_index)
        if not _overlaps_any(b, geo_boxes) and not _overlaps_any(b, underline_boxes)
    ]
    geo_boxes = [b for b in geo_boxes if not _box_contains_text(b, anchors)]

    candidates: list[_Candidate] = []
    for box in checkbox_boxes:
        label = _nearest_label(box, anchors)
        candidates.append(
            _Candidate(page_index, label, "checkbox", box.x, box.y, box.w, box.h)
        )
    for box in geo_boxes + underline_boxes:
        label = _nearest_label(box, anchors)
        logical_type, _ = classify_label(label, patterns)
        if logical_type == "checkbox":
            logical_type = "text"  # a wide box labelled near a glyph is still a text input
        h = min(box.h, MAX_FIELD_HEIGHT_PT)
        candidates.append(
            _Candidate(page_index, label, logical_type, box.x, box.y, box.w, h)
        )

    candidates.extend(
        _synthesize_from_labels(page_index, anchors, candidates, patterns)
    )
    return candidates


def _synthesize_from_labels(
    page_index: int,
    anchors: list[TextAnchor],
    existing: list[_Candidate],
    patterns: DetectionPatterns,
) -> list[_Candidate]:
    """Create fields for ``Label:`` anchors and checkbox glyphs with no box."""
    synthesized: list[_Candidate] = []
    for anchor in anchors:
        text = anchor.text.strip()
        if not text:
            continue
        has_glyph = any(g in text for g in patterns.checkbox_glyphs)
        if not has_glyph and not text.endswith(":"):
            continue
        if _has_box_on_line(anchor, existing):
            continue

        if has_glyph:
            synthesized.append(
                _Candidate(page_index, text, "checkbox", anchor.x, anchor.y, 12.0, 12.0)
            )
            continue

        label = text.rstrip(":").strip()
        logical_type, width = classify_label(label, patterns)
        label_width = len(text) * LABEL_CHAR_WIDTH_PT
        if _is_rtl(label):
            x = max(0.0, anchor.x - width - SYNTH_GAP_PT)
        else:
            x = anchor.x + label_width + SYNTH_GAP_PT
        synthesized.append(
            _Candidate(
                page_index, label, logical_type,
                round(x, 2), round(anchor.y - 3.0, 2), width, SYNTH_FIELD_HEIGHT_PT,
            )
        )
    return synthesized


def _has_box_on_line(anchor: TextAnchor, candidates: list[_Candidate]) -> bool:
    for c in candidates:
        same_line = c.y - SYNTH_LINE_TOLERANCE_PT <= anchor.y <= c.y + c.h + SYNTH_LINE_TOLERANCE_PT
        within_reach = abs(c.x - anchor.x) <= SYNTH_SUPPRESS_REACH_PT
        if same_line and within_reach:
            return True
    return False


def _snap_alignment(candidates: list[_Candidate]) -> list[_Candidate]:
    """Snap nearly-equal x and y values to a shared grid line per page."""
    snapped = list(candidates)
    for page in {c.page for c in snapped}:
        idx = [i for i, c in enumerate(snapped) if c.page == page]
        for attr in ("x", "y"):
            values = sorted({getattr(snapped[i], attr) for i in idx})
            mapping = _cluster_values(values)
            for i in idx:
                new = mapping[getattr(snapped[i], attr)]
                snapped[i] = replace(snapped[i], **{attr: new})
    return snapped


def _cluster_values(values: list[float]) -> dict[float, float]:
    """Map each value to its cluster's rounded mean (clusters span <= snap)."""
    mapping: dict[float, float] = {}
    cluster: list[float] = []
    for value in values:
        if cluster and value - cluster[-1] > ALIGNMENT_SNAP_PT:
            _assign_cluster(cluster, mapping)
            cluster = []
        cluster.append(value)
    if cluster:
        _assign_cluster(cluster, mapping)
    return mapping


def _assign_cluster(cluster: list[float], mapping: dict[float, float]) -> None:
    snap = round(sum(cluster) / len(cluster), 2)
    for value in cluster:
        mapping[value] = snap


def _name_candidates(candidates: list[_Candidate]) -> list[AutoFieldSpec]:
    used_names: dict[str, int] = {}
    specs: list[AutoFieldSpec] = []
    ordered = sorted(candidates, key=lambda c: (c.page, -c.y, c.x))
    for c in ordered:
        name = generate_field_name(c.label, c.logical_type, used_names)
        csv_type = CSV_TYPE_BY_LOGICAL.get(c.logical_type, "text")
        specs.append(
            AutoFieldSpec(
                page=c.page, name=name, field_type=csv_type,
                x=round(c.x, 2), y=round(c.y, 2), w=round(c.w, 2), h=round(c.h, 2),
            )
        )
    return specs


def _normalize_label(text: str) -> str:
    """Recover Hebrew letters from labels decoded as latin-1 (cp1255 bytes)."""
    hebrew_range = sum(1 for ch in text if ch in _LATIN1_TO_HEBREW)
    if hebrew_range < 2:
        return text
    return "".join(_LATIN1_TO_HEBREW.get(ch, ch) for ch in text)


def _is_rtl(text: str) -> bool:
    return any("֐" <= ch <= "׿" for ch in text)
