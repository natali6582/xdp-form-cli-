"""Regenerate packaged Plan-T resources from pdf-fields-normalized.csv.

Reads the normalized Plan-T field inventory (field_name + Hebrew description
per field) and rebuilds two package resources:

- ``plan_t_fields.csv``        — known field names (union of existing + inventory)
- ``plan_t_semantic_labels.csv`` — label,field_name pairs; curated rows kept
  first and winning on conflicts, inventory descriptions appended, ambiguous
  descriptions (same label -> different fields) dropped.

Usage: py examples/build_semantic_map.py <pdf-fields-normalized.csv>
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

RESOURCES = Path(__file__).resolve().parents[1] / "src" / "xdp_form_cli" / "resources"
FIELDS_CSV = RESOURCES / "plan_t_fields.csv"
SEMANTIC_CSV = RESOURCES / "plan_t_semantic_labels.csv"

_VALID_FIELD_RE = re.compile(r"^(txt|chk|img)[A-Za-z0-9_]+$")


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def build(normalized_csv: Path) -> tuple[int, int, int]:
    inventory = _read_csv(normalized_csv)

    known: dict[str, str] = {}  # name -> prefix
    for row in _read_csv(FIELDS_CSV):
        name = (row.get("field_name") or "").strip()
        if name:
            known[name] = name[:3]
    for row in inventory:
        name = (row.get("field_name") or "").strip()
        if name and _VALID_FIELD_RE.match(name):
            known.setdefault(name, name[:3])

    labels: dict[str, str] = {}
    for row in _read_csv(SEMANTIC_CSV):  # curated rows win
        label = (row.get("label") or "").strip()
        field = (row.get("field_name") or "").strip()
        if label and field in known:
            labels[label] = field

    ambiguous: set[str] = set()
    added = 0
    for row in inventory:
        label = (row.get("description") or "").strip()
        field = (row.get("field_name") or "").strip()
        if not label or field not in known or label in ambiguous:
            continue
        existing = labels.get(label)
        if existing is None:
            labels[label] = field
            added += 1
        elif existing != field and label not in {r["label"] for r in _read_csv(SEMANTIC_CSV)}:
            # Same description used for two different inventory fields: unsafe.
            ambiguous.add(label)
            labels.pop(label, None)
            added -= 1

    with FIELDS_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["field_name", "prefix"])
        for name in sorted(known):
            writer.writerow([name, known[name]])

    with SEMANTIC_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, quoting=csv.QUOTE_ALL)
        writer.writerow(["label", "field_name"])
        for label in sorted(labels):
            writer.writerow([label, labels[label]])

    return len(known), len(labels), len(ambiguous)


if __name__ == "__main__":
    fields, labels, dropped = build(Path(sys.argv[1]))
    print(f"known fields: {fields}, semantic labels: {labels}, ambiguous dropped: {dropped}")
