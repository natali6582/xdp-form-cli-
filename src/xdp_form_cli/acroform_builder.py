from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

import pikepdf
from pikepdf import Array, Dictionary, Name, Stream, String


ACROFORM_FONT_RESOURCE = "Arial"
ACROFORM_DEFAULT_APPEARANCE = f"/{ACROFORM_FONT_RESOURCE} 10 Tf 0 g"
SUPPORTED_TYPES = {"text", "tx", "textarea", "checkbox", "check", "chk", "image", "img"}
BENEFICIARY_TABLE_COLUMNS = (
    "Name",
    "ID",
    "DOB",
    "CitizenshipCountry",
    "TaxResidencyCountry",
    "BenefitPercent",
)
BENEFICIARY_TABLE_RE = re.compile(
    r"^txtBeneficiary(?P<row>\d+)(?P<column>"
    + "|".join(BENEFICIARY_TABLE_COLUMNS)
    + r")$"
)


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
        _ensure_no_real_signature_fields(pdf)
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

            field_type = (row.get("type") or "text").strip().lower()
            _validate_field_type(name, field_type, row_number)
            specs.append(
                AcroFieldSpec(
                    page=_parse_int(row, "page", row_number),
                    name=name,
                    field_type=field_type,
                    x=_parse_float(row, "x", row_number),
                    y=_parse_float(row, "y", row_number),
                    w=_parse_float(row, "w", row_number),
                    h=_parse_float(row, "h", row_number),
                    value=(row.get("value") or "").strip(),
                )
            )

    if not specs:
        raise ValueError("Field spec CSV contains no fields.")

    _validate_repeated_table_rows(specs)
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
        Border=Array([0, 0, 0]),
        BS=Dictionary(W=0, S=Name("/S")),
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
        widget[Name("/Border")] = Array([0, 0, 1])
        widget[Name("/BS")] = Dictionary(W=1, S=Name("/S"))
        widget[Name("/MK")] = Dictionary(BC=Array([0, 0, 0]))
        widget[Name("/V")] = Name("/Yes") if _truthy(spec.value) else Name("/Off")
        widget[Name("/AS")] = widget[Name("/V")]
        widget[Name("/AP")] = Dictionary(
            N=Dictionary(
                Off=_make_empty_appearance(pdf, spec.w, spec.h),
                Yes=_make_checkbox_yes_appearance(pdf, spec.w, spec.h),
            )
        )
                Off=_checkbox_appearance(pdf, spec.w, spec.h, checked=False),
                Yes=_checkbox_appearance(pdf, spec.w, spec.h, checked=True),
            )
        )
        widget[Name("/H")] = Name("/N")
        return pdf.make_indirect(widget)

    if spec.field_type in {"image", "img"}:
        widget[Name("/FT")] = Name("/Btn")
        # Pushbutton widgets are the closest AcroForm placeholder for image injection.
        widget[Name("/Ff")] = 65536
        widget[Name("/AP")] = Dictionary(N=_make_empty_appearance(pdf, spec.w, spec.h))
        widget[Name("/AP")] = Dictionary(N=_transparent_appearance(pdf, spec.w, spec.h))
        return pdf.make_indirect(widget)

    if spec.field_type in {"signature", "sig"}:
        widget[Name("/FT")] = Name("/Sig")
        return pdf.make_indirect(widget)

    raise ValueError(
        f"Unsupported field type for {spec.name}: {spec.field_type}. Use text, textarea, checkbox, or image."
    )


def _validate_field_type(name: str, field_type: str, row_number: int) -> None:
    if field_type in {"signature", "sig"}:
        raise ValueError(
            f"Field spec row {row_number} uses {field_type}; signature fields must use type=image."
        )
    if field_type not in SUPPORTED_TYPES:
        raise ValueError(
            f"Unsupported field type for {name}: {field_type}. Use text, textarea, checkbox, or image."
        )
    if _is_image_signature_name(name) and field_type not in {"image", "img"}:
        raise ValueError(f"Field {name} is an img...Signature field and must use type=image.")


def _is_image_signature_name(name: str) -> bool:
    normalized = name.lower()
    return normalized.startswith("img") and "signature" in normalized


def _ensure_no_real_signature_fields(pdf: pikepdf.Pdf) -> None:
    acroform = pdf.Root.get(Name("/AcroForm"))
    if acroform is None:
        return
    for field in acroform.get(Name("/Fields"), []):
        if _field_tree_contains_signature(field):
            raise ValueError("PDF contains a real PDF signature field (/Sig); signatures must be image fields.")


def _field_tree_contains_signature(field: object) -> bool:
    if not isinstance(field, Dictionary):
        return False
    if field.get(Name("/FT")) == Name("/Sig"):
        return True
    return any(_field_tree_contains_signature(child) for child in field.get(Name("/Kids"), []))


def _make_empty_appearance(pdf: pikepdf.Pdf, width: float, height: float) -> pikepdf.Stream:
    return _make_appearance_stream(pdf, b"", width, height)


def _make_checkbox_yes_appearance(pdf: pikepdf.Pdf, width: float, height: float) -> pikepdf.Stream:
    x0 = max(1.0, width * 0.18)
    y0 = max(1.0, height * 0.50)
    x1 = max(x0 + 1.0, width * 0.42)
    y1 = max(1.0, height * 0.22)
    x2 = max(x1 + 1.0, width * 0.82)
    y2 = max(y0 + 1.0, height * 0.82)
    commands = f"q 1.4 w 0 0 0 RG {x0:.2f} {y0:.2f} m {x1:.2f} {y1:.2f} l {x2:.2f} {y2:.2f} l S Q"
    return _make_appearance_stream(pdf, commands.encode("ascii"), width, height)


def _make_appearance_stream(pdf: pikepdf.Pdf, data: bytes, width: float, height: float) -> pikepdf.Stream:
    stream = pikepdf.Stream(pdf, data)
    stream[Name("/Type")] = Name("/XObject")
    stream[Name("/Subtype")] = Name("/Form")
    stream[Name("/FormType")] = 1
    stream[Name("/BBox")] = Array([0, 0, width, height])
    stream[Name("/Resources")] = Dictionary()
    return pdf.make_indirect(stream)


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


def _validate_repeated_table_rows(specs: list[AcroFieldSpec]) -> None:
    names_by_page: dict[int, set[str]] = {}
    for spec in specs:
        names_by_page.setdefault(spec.page, set()).add(spec.name)

    for page, names in names_by_page.items():
        table_rows: dict[int, set[str]] = {}
        for name in names:
            match = BENEFICIARY_TABLE_RE.match(name)
            if match is None:
                continue
            table_rows.setdefault(int(match.group("row")), set()).add(match.group("column"))

        if not table_rows:
            continue

        complete_rows = sorted(
            row for row, columns in table_rows.items() if set(BENEFICIARY_TABLE_COLUMNS).issubset(columns)
        )
        expected_rows = list(range(min(table_rows), min(table_rows) + 3))
        missing: list[str] = []
        for row in expected_rows:
            for column in BENEFICIARY_TABLE_COLUMNS:
                if column not in table_rows.get(row, set()):
                    missing.append(f"txtBeneficiary{row}{column}")

        if missing:
            raise ValueError(
                f"Page {page} has a beneficiary table, but missing repeated row field(s): "
                + ", ".join(missing)
            )
        if len(complete_rows) < 3:
            raise ValueError(
                f"Page {page} has a beneficiary table, but only {len(complete_rows)} complete row(s). "
                "Add fields for all visible table rows."
            )


def _checkbox_appearance(pdf: pikepdf.Pdf, width: float, height: float, *, checked: bool) -> Stream:
    w = max(float(width), 6.0)
    h = max(float(height), 6.0)
    border = f"q 1 w 0 0 0 RG 1 1 {w - 2:.3f} {h - 2:.3f} re S Q\n"
    mark = ""
    if checked:
        mark = (
            f"q 1.4 w 0 0 0 RG "
            f"{w * 0.22:.3f} {h * 0.48:.3f} m "
            f"{w * 0.43:.3f} {h * 0.24:.3f} l "
            f"{w * 0.78:.3f} {h * 0.76:.3f} l S Q\n"
        )

    return Stream(
        pdf,
        (border + mark).encode("ascii"),
        Type=Name("/XObject"),
        Subtype=Name("/Form"),
        BBox=Array([0, 0, w, h]),
        Matrix=Array([1, 0, 0, 1, 0, 0]),
    )


def _transparent_appearance(pdf: pikepdf.Pdf, width: float, height: float) -> Stream:
    w = max(float(width), 1.0)
    h = max(float(height), 1.0)
    return Stream(
        pdf,
        b"",
        Type=Name("/XObject"),
        Subtype=Name("/Form"),
        BBox=Array([0, 0, w, h]),
        Matrix=Array([1, 0, 0, 1, 0, 0]),
    )
