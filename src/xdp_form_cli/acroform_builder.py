from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import pikepdf
from pikepdf import Array, Dictionary, Name, String


ACROFORM_FONT_RESOURCE = "Arial"
ACROFORM_DEFAULT_APPEARANCE = f"/{ACROFORM_FONT_RESOURCE} 10 Tf 0 g"


@dataclass
class AcroFieldSpec:
    page: int
    name: str
    field_type: str
    x: float
    y: float
    w: float
    h: float
    value: str = ""


def create_acroform_pdf(input_path: str | Path, fields_path: str | Path, output_path: str | Path) -> tuple[Path, int]:
    source = Path(input_path)
    output = Path(output_path)
    if output.resolve() == source.resolve():
        raise ValueError("--output must be a new PDF file path, not the source PDF.")

    specs = load_field_specs(fields_path)
    with pikepdf.Pdf.open(str(source)) as pdf:
        acroform = _ensure_acroform(pdf)
        if Name("/Fields") not in acroform:
            acroform[Name("/Fields")] = Array()
        fields = acroform[Name("/Fields")]

        for spec in specs:
            if spec.page < 1 or spec.page > len(pdf.pages):
                raise ValueError(f"Field {spec.name} references page {spec.page}, but PDF has {len(pdf.pages)} page(s).")

            page = pdf.pages[spec.page - 1]
            field = _build_widget(pdf, page.obj, spec)
            fields.append(field)
            if Name("/Annots") not in page.obj:
                page.obj[Name("/Annots")] = Array()
            annots = page.obj[Name("/Annots")]
            annots.append(field)

        pdf.save(str(output))

    return output, len(specs)


def load_field_specs(fields_path: str | Path) -> list[AcroFieldSpec]:
    path = Path(fields_path)
    specs: list[AcroFieldSpec] = []

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"page", "name", "type", "x", "y", "w", "h"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Field spec CSV is missing required column(s): {', '.join(sorted(missing))}")

        for row_number, row in enumerate(reader, start=2):
            name = (row.get("name") or "").strip()
            if not name:
                raise ValueError(f"Field spec row {row_number} has an empty name.")

            specs.append(
                AcroFieldSpec(
                    page=_parse_int(row, "page", row_number),
                    name=name,
                    field_type=(row.get("type") or "text").strip().lower(),
                    x=_parse_float(row, "x", row_number),
                    y=_parse_float(row, "y", row_number),
                    w=_parse_float(row, "w", row_number),
                    h=_parse_float(row, "h", row_number),
                    value=(row.get("value") or "").strip(),
                )
            )

    if not specs:
        raise ValueError("Field spec CSV contains no fields.")

    return specs


def _ensure_acroform(pdf: pikepdf.Pdf) -> Dictionary:
    root = pdf.Root
    acroform = root.get(Name("/AcroForm"))
    if acroform is None:
        acroform = Dictionary()
        root[Name("/AcroForm")] = acroform

    acroform[Name("/NeedAppearances")] = True
    if Name("/DA") not in acroform:
        acroform[Name("/DA")] = String(ACROFORM_DEFAULT_APPEARANCE)
    if Name("/DR") not in acroform:
        acroform[Name("/DR")] = Dictionary(
            Font=Dictionary(
                Arial=Dictionary(
                    Type=Name("/Font"),
                    Subtype=Name("/Type1"),
                    BaseFont=Name("/Arial"),
                )
            )
        )
    return acroform


def _build_widget(pdf: pikepdf.Pdf, page_obj: Dictionary, spec: AcroFieldSpec) -> Dictionary:
    rect = Array([spec.x, spec.y, spec.x + spec.w, spec.y + spec.h])
    widget = Dictionary(
        Type=Name("/Annot"),
        Subtype=Name("/Widget"),
        T=String(spec.name),
        Rect=rect,
        F=4,
        P=page_obj,
        BS=Dictionary(W=1, S=Name("/S")),
        MK=Dictionary(BC=Array([0, 0, 0]), BG=Array([1, 1, 1])),
    )

    if spec.field_type in {"text", "tx", "textarea"}:
        widget[Name("/FT")] = Name("/Tx")
        widget[Name("/DA")] = String(ACROFORM_DEFAULT_APPEARANCE)
        if spec.field_type == "textarea":
            widget[Name("/Ff")] = 4096
        if spec.value:
            widget[Name("/V")] = String(spec.value)
        return pdf.make_indirect(widget)

    if spec.field_type in {"checkbox", "check", "chk"}:
        widget[Name("/FT")] = Name("/Btn")
        widget[Name("/V")] = Name("/Yes") if _truthy(spec.value) else Name("/Off")
        widget[Name("/AS")] = widget[Name("/V")]
        return pdf.make_indirect(widget)

    raise ValueError(f"Unsupported field type for {spec.name}: {spec.field_type}. Use text, textarea, or checkbox.")


def _parse_int(row: dict[str, str], column: str, row_number: int) -> int:
    try:
        return int((row.get(column) or "").strip())
    except ValueError as exc:
        raise ValueError(f"Field spec row {row_number} has invalid integer in {column}.") from exc


def _parse_float(row: dict[str, str], column: str, row_number: int) -> float:
    try:
        return float((row.get(column) or "").strip())
    except ValueError as exc:
        raise ValueError(f"Field spec row {row_number} has invalid number in {column}.") from exc


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on", "checked", "v"}
