from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path


_BRACKET_INDEX_RE = re.compile(r"\[\d+\]")
_DUPLICATED_SUFFIX_RE = re.compile(r"_DUPLICATED_\d+$", re.IGNORECASE)
_NUMBER_SUFFIX_RE = re.compile(r"_\d+$")


@dataclass
class FieldMatch:
    original_name: str
    canonical_name: str
    matched: bool
    changed: bool
    method: str


class FieldTruth:
    def __init__(self, csv_path: str | Path) -> None:
        self.csv_path = Path(csv_path)
        with self.csv_path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))

        names = [row["field_name"].strip() for row in rows if row.get("field_name", "").strip()]
        self.names = names
        self.name_set = set(names)
        self.casefold_map = {name.casefold(): name for name in names}

    @classmethod
    def default(cls) -> "FieldTruth":
        repo_root = Path(__file__).resolve().parents[2]
        csv_path = repo_root.parent / "מיכון טפסים" / "רשימת שדות מהקוד.csv"
        return cls(csv_path)

    def match(self, field_name: str) -> FieldMatch:
        raw = field_name.strip()
        for candidate, method in self._candidate_names(raw):
            exact = self._exact_match(candidate)
            if exact is not None:
                return FieldMatch(
                    original_name=raw,
                    canonical_name=exact,
                    matched=True,
                    changed=exact != raw,
                    method=method,
                )

        return FieldMatch(
            original_name=raw,
            canonical_name=raw,
            matched=False,
            changed=False,
            method="unmatched",
        )

    def _candidate_names(self, field_name: str) -> list[tuple[str, str]]:
        candidates: list[tuple[str, str]] = []
        seen: set[str] = set()

        def add(value: str, method: str) -> None:
            value = value.strip()
            if not value or value in seen:
                return
            seen.add(value)
            candidates.append((value, method))

        add(field_name, "exact")

        if "." in field_name:
            add(field_name.rsplit(".", 1)[-1], "path-last-segment")

        without_indexes = _BRACKET_INDEX_RE.sub("", field_name)
        add(without_indexes, "strip-indexes")

        if "." in without_indexes:
            add(without_indexes.rsplit(".", 1)[-1], "path-last-segment-strip-indexes")

        base = without_indexes.rsplit(".", 1)[-1]
        add(base, "base-name")

        dedup = _DUPLICATED_SUFFIX_RE.sub("", base)
        add(dedup, "strip-duplicated-suffix")

        denumbered = _NUMBER_SUFFIX_RE.sub("", dedup)
        add(denumbered, "strip-number-suffix")

        return candidates

    def _exact_match(self, candidate: str) -> str | None:
        if candidate in self.name_set:
            return candidate
        return self.casefold_map.get(candidate.casefold())
