from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from xdp_form_cli.field_truth import FieldMatch, FieldTruth


@dataclass
class ConversionReport:
    total_fields: int
    exact_or_known: int
    renamed: int
    unmatched: int
    details: list[FieldMatch]

    def write_csv(self, output_path: str | Path) -> Path:
        output = Path(output_path)
        with output.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["original_name", "canonical_name", "matched", "changed", "method"])
            for item in self.details:
                writer.writerow(
                    [
                        item.original_name,
                        item.canonical_name,
                        "yes" if item.matched else "no",
                        "yes" if item.changed else "no",
                        item.method,
                    ]
                )
        return output


def build_report(matches: list[FieldMatch]) -> ConversionReport:
    return ConversionReport(
        total_fields=len(matches),
        exact_or_known=sum(1 for match in matches if match.matched),
        renamed=sum(1 for match in matches if match.changed),
        unmatched=sum(1 for match in matches if not match.matched),
        details=matches,
    )


def convert_editor_fields(editor: object, truth: FieldTruth) -> ConversionReport:
    matches = editor.convert_field_names(truth.match)
    return build_report(matches)
