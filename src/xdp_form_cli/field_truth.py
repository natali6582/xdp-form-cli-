from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


_BRACKET_INDEX_RE = re.compile(r"\[\d+\]")
_DUPLICATED_SUFFIX_RE = re.compile(r"_DUPLICATED_\d+$", re.IGNORECASE)
_NUMBER_SUFFIX_RE = re.compile(r"_\d+$")
_FIELD_TOKEN_RE = re.compile(r"\b(?:txt|chk|img)[A-Za-z0-9_]+\b")


@dataclass
class FieldMatch:
    original_name: str
    canonical_name: str
    matched: bool
    changed: bool
    method: str


class FieldTruth:
    def __init__(self, code_path: str | Path) -> None:
        self.code_path = Path(code_path)
        source = self.code_path.read_text(encoding="utf-8-sig", errors="ignore")
        names = sorted(set(_FIELD_TOKEN_RE.findall(source)))

        self.names: list[str] = []
        self.name_set: set[str] = set()
        self.casefold_map: dict[str, str] = {}
        self.source_map: dict[str, str] = {}
        self.add_names(names, source="code")

    @classmethod
    def default(cls) -> "FieldTruth":
        repo_root = Path(__file__).resolve().parents[2]
        search_roots = [repo_root.parent.parent, repo_root.parent]

        for root in search_roots:
            preferred = sorted(root.glob("PDFFormsBL*plan-t.cs"))
            if preferred:
                return cls(preferred[0])

        for root in search_roots:
            fallback = sorted(root.glob("**/PDFFormsBL*.cs"))
            if fallback:
                return cls(fallback[0])

        raise FileNotFoundError(
            "Could not find the Plan-T code field source file (PDFFormsBL*.cs) near the workspace."
        )

    def match(self, field_name: str) -> FieldMatch:
        raw = field_name.strip()
        for candidate, method in self._candidate_names(raw):
            exact = self._exact_match(candidate)
            if exact is not None:
                canonical, source = exact
                match_method = method if source == "code" else f"{method}:{source}"
                return FieldMatch(
                    original_name=raw,
                    canonical_name=canonical,
                    matched=True,
                    changed=canonical != raw,
                    method=match_method,
                )

        return FieldMatch(
            original_name=raw,
            canonical_name=raw,
            matched=False,
            changed=False,
            method="unmatched",
        )

    def add_names(self, names: list[str] | set[str], source: str) -> int:
        added = 0
        for name in sorted(set(names)):
            clean = name.strip()
            if not clean:
                continue

            if clean not in self.name_set:
                self.names.append(clean)
                self.name_set.add(clean)
                added += 1

            key = clean.casefold()
            if key not in self.casefold_map:
                self.casefold_map[key] = clean
                self.source_map[key] = source

        self.names.sort()
        return added

    @property
    def count(self) -> int:
        return len(self.name_set)

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

    def _exact_match(self, candidate: str) -> tuple[str, str] | None:
        if candidate in self.name_set:
            return candidate, self.source_map.get(candidate.casefold(), "code")

        key = candidate.casefold()
        canonical = self.casefold_map.get(key)
        if canonical is None:
            return None
        return canonical, self.source_map.get(key, "code")
