from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from xdp_form_cli.field_truth import FieldTruth


_FIELD_NAME_RE = re.compile(r"<field\b[^>]*\bname=(['\"])(.*?)\1", re.IGNORECASE | re.DOTALL)
_PLANT_FIELD_RE = re.compile(r"^(?:txt|chk|img)[A-Za-z0-9_]+$")
_EXAMPLE_EXTENSIONS = {".xml", ".xdp", ".txt"}


@dataclass
class ExampleLoadReport:
    files: int
    discovered: int
    accepted_style: int
    added: int


def add_example_fields_to_truth(truth: FieldTruth, paths: list[str]) -> ExampleLoadReport:
    files = 0
    discovered_names: set[str] = set()
    accepted_names: set[str] = set()

    for file_path in _iter_example_files(paths):
        files += 1
        names = _extract_field_names(file_path)
        discovered_names.update(names)
        accepted_names.update(name for name in names if _PLANT_FIELD_RE.match(name))

    added = truth.add_names(accepted_names, source="examples")
    return ExampleLoadReport(
        files=files,
        discovered=len(discovered_names),
        accepted_style=len(accepted_names),
        added=added,
    )


def _iter_example_files(paths: list[str]) -> list[Path]:
    files: list[Path] = []

    for raw_path in paths:
        path = Path(raw_path)
        if path.is_file():
            files.append(path)
            continue

        if path.is_dir():
            files.extend(
                item
                for item in sorted(path.rglob("*"))
                if item.is_file() and item.suffix.lower() in _EXAMPLE_EXTENSIONS
            )
            continue

        raise FileNotFoundError(f"Example path does not exist: {path}")

    return files


def _extract_field_names(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8-sig", errors="ignore")
    return {match.group(2).strip() for match in _FIELD_NAME_RE.finditer(text) if match.group(2).strip()}
