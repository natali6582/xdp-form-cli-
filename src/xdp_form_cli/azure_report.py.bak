from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path

from xdp_form_cli.auto_form import (
    AzureLayoutResult,
    DetectedBox,
    _bbox_underline_boxes_from_words,
    _load_azure_layout,
    _nearest_label,
)


@dataclass(frozen=True)
class AzureReportSummary:
    csv_path: Path
    json_path: Path | None
    known_field_count: int
    word_count: int
    anchor_count: int
    candidate_text_field_count: int
    checkbox_count: int


def build_azure_layout_report(
    input_pdf: str | Path,
    fields_list_csv: str | Path,
    output_csv: str | Path,
    *,
    output_json: str | Path | None = None,
) -> AzureReportSummary:
    """Run Azure Document Intelligence only and write a visual layout report."""
    known_fields = _load_known_field_names(fields_list_csv)
    layout = _load_azure_layout(input_pdf, enabled=True)
    if layout is None:
        raise ValueError("Azure Document Intelligence did not run.")
    if layout.warnings and not _has_layout_items(layout):
        raise ValueError(" ".join(layout.warnings))

    rows = _report_rows(layout, known_fields)
    csv_path = Path(output_csv)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "page",
                "item_type",
                "text_or_label",
                "x",
                "y",
                "w",
                "h",
                "suggested_plan_t_fields",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    json_path = Path(output_json) if output_json else None
    if json_path is not None:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps(
                {
                    "known_field_count": len(known_fields),
                    "warnings": list(layout.warnings),
                    "rows": rows,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    return AzureReportSummary(
        csv_path=csv_path,
        json_path=json_path,
        known_field_count=len(known_fields),
        word_count=sum(len(words) for words in layout.words_by_page.values()),
        anchor_count=sum(len(anchors) for anchors in layout.anchors_by_page.values()),
        candidate_text_field_count=sum(1 for row in rows if row["item_type"] == "candidate_text_field"),
        checkbox_count=sum(1 for row in rows if row["item_type"] == "candidate_checkbox"),
    )


def _has_layout_items(layout: AzureLayoutResult) -> bool:
    return any(
        (
            layout.words_by_page,
            layout.anchors_by_page,
            layout.checkbox_boxes_by_page,
        )
    )


def _report_rows(layout: AzureLayoutResult, known_fields: list[str]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    candidate_boxes = _bbox_underline_boxes_from_words(layout.words_by_page)

    for page, anchors in sorted(layout.anchors_by_page.items()):
        for anchor in anchors:
            rows.append(
                _row(
                    page=page,
                    item_type="azure_text_line",
                    text=anchor.text,
                    x=anchor.x,
                    y=anchor.y,
                    suggestions=_suggest_known_fields(anchor.text, known_fields),
                )
            )

    for page, words in sorted(layout.words_by_page.items()):
        for word in words:
            rows.append(
                _row(
                    page=page,
                    item_type="azure_word",
                    text=word.text,
                    x=word.x0,
                    y=word.page_height - word.y1,
                    w=word.x1 - word.x0,
                    h=word.y1 - word.y0,
                    suggestions=_suggest_known_fields(word.text, known_fields),
                )
            )

    for page, boxes in sorted(candidate_boxes.items()):
        anchors = layout.anchors_by_page.get(page, [])
        for box in boxes:
            label = _nearest_label(box, anchors)
            rows.append(
                _row(
                    page=page,
                    item_type="candidate_text_field",
                    text=label,
                    x=box.x,
                    y=box.y,
                    w=box.w,
                    h=box.h,
                    suggestions=_suggest_known_fields(label, known_fields),
                )
            )

    for page, boxes in sorted(layout.checkbox_boxes_by_page.items()):
        anchors = layout.anchors_by_page.get(page, [])
        for box in boxes:
            label = _nearest_label(box, anchors)
            rows.append(
                _row(
                    page=page,
                    item_type="candidate_checkbox",
                    text=label,
                    x=box.x,
                    y=box.y,
                    w=box.w,
                    h=box.h,
                    suggestions=_suggest_known_fields(label, known_fields, prefix="chk"),
                )
            )

    return sorted(rows, key=lambda row: (int(row["page"]), row["item_type"], -float(row["y"] or 0), float(row["x"] or 0)))


def _row(
    *,
    page: int,
    item_type: str,
    text: str,
    x: float,
    y: float,
    w: float | None = None,
    h: float | None = None,
    suggestions: list[str] | None = None,
) -> dict[str, str]:
    return {
        "page": str(page),
        "item_type": item_type,
        "text_or_label": text,
        "x": _fmt(x),
        "y": _fmt(y),
        "w": _fmt(w) if w is not None else "",
        "h": _fmt(h) if h is not None else "",
        "suggested_plan_t_fields": ";".join(suggestions or []),
    }


def _fmt(value: float) -> str:
    return f"{value:.2f}"


def _load_known_field_names(path: str | Path) -> list[str]:
    csv_path = Path(path)
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames and "field_name" in reader.fieldnames:
            return sorted({(row.get("field_name") or "").strip() for row in reader if (row.get("field_name") or "").strip()})

    text = csv_path.read_text(encoding="utf-8-sig", errors="ignore")
    return sorted(set(re.findall(r"\b(?:txt|chk|img)[A-Za-z0-9_]+\b", text)))


def _suggest_known_fields(label: str, known_fields: list[str], *, prefix: str | None = None) -> list[str]:
    label_tokens = _tokens(label)
    if not label_tokens:
        return []

    scored: list[tuple[float, str]] = []
    for field_name in known_fields:
        if prefix is not None and not field_name.startswith(prefix):
            continue
        field_tokens = _tokens(_split_field_name(field_name))
        if not field_tokens:
            continue
        common = label_tokens & field_tokens
        if not common:
            continue
        score = len(common) / max(len(label_tokens), len(field_tokens))
        scored.append((score, field_name))

    scored.sort(key=lambda item: (-item[0], item[1]))
    return [name for _score, name in scored[:5]]


def _split_field_name(field_name: str) -> str:
    without_prefix = re.sub(r"^(txt|chk|img)", "", field_name)
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", without_prefix)
    spaced = spaced.replace("_", " ")
    return spaced


def _tokens(text: str) -> set[str]:
    return {
        token.lower()
        for token in re.findall(r"[A-Za-z0-9]+", text)
        if len(token) >= 2
    }
