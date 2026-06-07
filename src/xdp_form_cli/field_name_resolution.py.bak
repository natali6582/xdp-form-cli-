from __future__ import annotations

import csv
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET


_FIELD_TOKEN_RE = re.compile(r"\b(?:txt|chk|img)[A-Za-z0-9_]+\b")
_BRACKET_INDEX_RE = re.compile(r"\[\d+\]")
_NUMBER_SUFFIX_RE = re.compile(r"_\d+$")
_DIRECT_STATUSES = {"direct match"}
_XLSX_NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


@dataclass(frozen=True)
class FieldNameResolution:
    name: str
    matched: bool
    method: str


class FieldNameResolver:
    """Resolve auto-detected field names to known Plan-T names when safe."""

    def __init__(
        self,
        known_names: set[str] | list[str],
        *,
        aliases: dict[str, str] | None = None,
        label_aliases: dict[str, str] | None = None,
    ) -> None:
        self.known_names = sorted({name.strip() for name in known_names if name.strip()})
        self._casefold_to_known = {name.casefold(): name for name in self.known_names}
        self._aliases = {
            alias.casefold(): canonical
            for alias, canonical in (aliases or {}).items()
            if alias.strip() and canonical.strip() and canonical.casefold() in self._casefold_to_known
        }
        self._label_aliases = {
            _normalize_label(alias): canonical
            for alias, canonical in (label_aliases or {}).items()
            if alias.strip() and canonical.strip() and canonical.casefold() in self._casefold_to_known
        }

    @classmethod
    def from_files(
        cls,
        *,
        fields_list_csv: str | Path | None = None,
        mapping_xlsx: str | Path | None = None,
        semantic_map_csv: str | Path | None = None,
    ) -> "FieldNameResolver | None":
        if fields_list_csv is None and mapping_xlsx is None and semantic_map_csv is None:
            return None

        known_names = load_known_field_names(fields_list_csv) if fields_list_csv else set()
        aliases = load_livecycle_mapping_aliases(mapping_xlsx, known_names) if mapping_xlsx else {}
        semantic_name_aliases, label_aliases = (
            load_semantic_field_aliases(semantic_map_csv, known_names)
            if semantic_map_csv else ({}, {})
        )
        aliases.update(semantic_name_aliases)
        if not known_names and aliases:
            known_names = set(aliases.values())
        if not known_names and label_aliases:
            known_names = set(label_aliases.values())
        return cls(known_names, aliases=aliases, label_aliases=label_aliases)

    def resolve(self, base_name: str, *, field_type: str, label: str = "") -> FieldNameResolution:
        prefix = _prefix_for_type(field_type)
        for candidate, method in _candidate_names(base_name):
            exact = self._exact(candidate)
            if exact and exact.startswith(prefix):
                return FieldNameResolution(exact, matched=True, method=method)

        alias = self._aliases.get(base_name.casefold())
        if alias and alias.startswith(prefix):
            return FieldNameResolution(alias, matched=True, method="livecycle-mapping")

        for label_key in _label_lookup_keys(label):
            label_alias = self._label_aliases.get(label_key)
            if label_alias and label_alias.startswith(prefix):
                return FieldNameResolution(label_alias, matched=True, method="semantic-label-map")

        label_match = self._resolve_by_unique_token_match(label, prefix=prefix)
        if label_match is not None:
            return FieldNameResolution(label_match, matched=True, method="label-token-match")

        return FieldNameResolution(base_name, matched=False, method="unmatched")

    def unique_name(self, canonical_name: str, used_names: dict[str, int]) -> str:
        count = used_names.get(canonical_name, 0) + 1
        used_names[canonical_name] = count
        if count == 1:
            return canonical_name

        for candidate in (f"{canonical_name}_{count - 1}", f"{canonical_name}{count}"):
            if candidate in self.known_names and candidate not in used_names:
                used_names[candidate] = 1
                return candidate

        # Reusing an exact Plan-T name is safer than inventing an unknown suffix.
        return canonical_name

    def is_known_name(self, name: str) -> bool:
        return name.casefold() in self._casefold_to_known

    def _exact(self, candidate: str) -> str | None:
        return self._casefold_to_known.get(candidate.casefold()) or self._aliases.get(candidate.casefold())

    def _resolve_by_unique_token_match(self, label: str, *, prefix: str) -> str | None:
        label_tokens = _tokens(label)
        if not label_tokens:
            return None

        scored: list[tuple[float, str]] = []
        for name in self.known_names:
            if not name.startswith(prefix):
                continue
            field_tokens = _tokens(_split_field_name(name))
            if not field_tokens:
                continue
            common = label_tokens & field_tokens
            if not common:
                continue
            score = len(common) / max(len(label_tokens), len(field_tokens))
            if label_tokens <= field_tokens:
                score += 0.25
            scored.append((score, name))

        if not scored:
            return None

        scored.sort(key=lambda item: (-item[0], item[1]))
        best_score, best_name = scored[0]
        if best_score < 0.75:
            return None
        if len(scored) > 1 and scored[1][0] >= best_score - 0.10:
            return None
        return best_name


def load_known_field_names(path: str | Path | None) -> set[str]:
    if path is None:
        return set()

    csv_path = Path(path)
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames and "field_name" in reader.fieldnames:
            return {
                (row.get("field_name") or "").strip()
                for row in reader
                if (row.get("field_name") or "").strip()
            }

    text = csv_path.read_text(encoding="utf-8-sig", errors="ignore")
    return set(_FIELD_TOKEN_RE.findall(text))


def load_livecycle_mapping_aliases(path: str | Path | None, known_names: set[str]) -> dict[str, str]:
    if path is None:
        return {}

    rows = _read_xlsx_rows(Path(path), sheet_index=1)
    if not rows:
        return {}

    headers = [cell.strip() for cell in rows[0]]
    try:
        current_idx = headers.index("Current field")
        suggested_idx = headers.index("Suggested code field")
        status_idx = headers.index("Status")
    except ValueError:
        return {}

    known_casefold = {name.casefold(): name for name in known_names}
    aliases: dict[str, str] = {}
    ambiguous: set[str] = set()

    for row in rows[1:]:
        current = _cell(row, current_idx)
        suggested = _cell(row, suggested_idx)
        status = _cell(row, status_idx).casefold()
        if status not in _DIRECT_STATUSES:
            continue

        suggestions = [_normal_known_name(part.strip(), known_casefold) for part in suggested.split("/") if part.strip()]
        suggestions = [item for item in suggestions if item]
        if len(set(suggestions)) != 1:
            continue

        canonical = suggestions[0]
        for alias in _candidate_aliases(current):
            key = alias.casefold()
            existing = aliases.get(key)
            if existing and existing != canonical:
                ambiguous.add(key)
                aliases.pop(key, None)
                continue
            if key not in ambiguous:
                aliases[alias] = canonical

    return aliases


def load_semantic_label_aliases(path: str | Path | None, known_names: set[str]) -> dict[str, str]:
    _name_aliases, label_aliases = load_semantic_field_aliases(path, known_names)
    return label_aliases


def load_semantic_field_aliases(
    path: str | Path | None,
    known_names: set[str],
) -> tuple[dict[str, str], dict[str, str]]:
    if path is None:
        return {}, {}

    known_casefold = {name.casefold(): name for name in known_names}
    name_aliases: dict[str, str] = {}
    label_aliases: dict[str, str] = {}
    ambiguous_names: set[str] = set()
    ambiguous_labels: set[str] = set()
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = set(reader.fieldnames or [])
        if "field_name" not in fieldnames or not ({"label", "name"} & fieldnames):
            raise ValueError("Semantic field map CSV must include field_name and at least one of label/name columns.")

        for row_number, row in enumerate(reader, start=2):
            label = (row.get("label") or "").strip()
            name = (row.get("name") or "").strip()
            field_name = (row.get("field_name") or "").strip()
            if not field_name or (not label and not name):
                continue
            canonical = known_casefold.get(field_name.casefold())
            if canonical is None:
                raise ValueError(
                    f"Semantic field map row {row_number} references unknown Plan-T field: {field_name}"
                )
            if name:
                _add_unambiguous_alias(
                    name_aliases,
                    ambiguous_names,
                    name.casefold(),
                    canonical,
                )
            if label:
                label_key = _normalize_label(label)
                if label_key:
                    _add_unambiguous_alias(
                        label_aliases,
                        ambiguous_labels,
                        label_key,
                        canonical,
                    )
    return name_aliases, label_aliases


def _add_unambiguous_alias(
    aliases: dict[str, str],
    ambiguous: set[str],
    key: str,
    canonical: str,
) -> None:
    existing = aliases.get(key)
    if existing and existing != canonical:
        ambiguous.add(key)
        aliases.pop(key, None)
        return
    if key not in ambiguous:
        aliases[key] = canonical


def _candidate_names(field_name: str) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(value: str, method: str) -> None:
        value = value.strip()
        if value and value not in seen:
            seen.add(value)
            candidates.append((value, method))

    add(field_name, "exact")
    without_indexes = _BRACKET_INDEX_RE.sub("", field_name)
    add(without_indexes, "strip-indexes")
    base = without_indexes.rsplit(".", 1)[-1]
    add(base, "base-name")
    denumbered = _NUMBER_SUFFIX_RE.sub("", base)
    add(denumbered, "strip-number-suffix")
    return candidates


def _candidate_aliases(field_name: str) -> set[str]:
    return {candidate for candidate, _method in _candidate_names(field_name)}


def _normal_known_name(name: str, known_casefold: dict[str, str]) -> str | None:
    return known_casefold.get(name.casefold())


def _prefix_for_type(field_type: str) -> str:
    lowered = field_type.lower()
    if lowered in {"checkbox", "check", "chk"}:
        return "chk"
    if lowered in {"image", "img"}:
        return "img"
    return "txt"


def _split_field_name(field_name: str) -> str:
    without_prefix = re.sub(r"^(txt|chk|img)", "", field_name)
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", without_prefix)
    return spaced.replace("_", " ")


def _tokens(text: str) -> set[str]:
    return {
        token.lower()
        for token in re.findall(r"[A-Za-z0-9]+", text)
        if len(token) >= 2
    }


def _normalize_label(label: str) -> str:
    normalized = re.sub(r"[\s:：־–—_-]+", " ", label.strip().casefold())
    normalized = re.sub(r"[^\w\u0590-\u05FF ]+", "", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _label_lookup_keys(label: str) -> list[str]:
    keys: list[str] = []
    _add_label_lookup_key(keys, label)

    if any("\u0590" <= char <= "\u05FF" for char in label):
        _add_label_lookup_key(keys, label[::-1])

    return keys


def _add_label_lookup_key(keys: list[str], label: str) -> None:
    normalized = _normalize_label(label)
    if normalized and normalized not in keys:
        keys.append(normalized)

    parts = normalized.split()
    if len(parts) != 1:
        return
    word = parts[0]
    if len(word) <= 3 or word[0] not in {"\u05d1", "\u05d4", "\u05dc"}:
        return
    without_prefix = word[1:]
    if without_prefix and without_prefix not in keys:
        keys.append(without_prefix)


def _cell(row: list[str], index: int) -> str:
    return row[index].strip() if index < len(row) else ""


def _read_xlsx_rows(path: Path, *, sheet_index: int) -> list[list[str]]:
    with zipfile.ZipFile(path) as archive:
        shared_strings = _read_shared_strings(archive)
        sheet_name = f"xl/worksheets/sheet{sheet_index}.xml"
        with archive.open(sheet_name) as handle:
            tree = ET.parse(handle)

    rows: list[list[str]] = []
    for row in tree.findall(".//x:sheetData/x:row", _XLSX_NS):
        values: dict[int, str] = {}
        for cell in row.findall("x:c", _XLSX_NS):
            ref = cell.attrib.get("r", "")
            col_idx = _column_index(ref)
            values[col_idx] = _read_cell_value(cell, shared_strings)
        if values:
            max_col = max(values)
            rows.append([values.get(idx, "") for idx in range(max_col + 1)])
    return rows


def _read_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        with archive.open("xl/sharedStrings.xml") as handle:
            tree = ET.parse(handle)
    except KeyError:
        return []

    strings: list[str] = []
    for item in tree.findall(".//x:si", _XLSX_NS):
        strings.append("".join(text.text or "" for text in item.findall(".//x:t", _XLSX_NS)))
    return strings


def _read_cell_value(cell: ET.Element, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t", "")
    if cell_type == "inlineStr":
        return "".join(text.text or "" for text in cell.findall(".//x:t", _XLSX_NS))

    value = cell.find("x:v", _XLSX_NS)
    if value is None or value.text is None:
        return ""
    if cell_type == "s":
        try:
            return shared_strings[int(value.text)]
        except (ValueError, IndexError):
            return ""
    return value.text


def _column_index(cell_ref: str) -> int:
    letters = re.match(r"[A-Z]+", cell_ref)
    if not letters:
        return 0
    value = 0
    for char in letters.group(0):
        value = value * 26 + (ord(char) - ord("A") + 1)
    return value - 1
