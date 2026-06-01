from __future__ import annotations

import csv
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import pikepdf


REQUIRED_COLUMNS = ("page", "name", "type", "x", "y", "w", "h")
OPTIONAL_COLUMNS = ("value",)
SUPPORTED_TYPES = {"text", "tx", "textarea", "checkbox", "check", "chk", "image", "img", "signature", "sig"}
BENEFICIARY_COLUMNS = (
    "Name",
    "ID",
    "DOB",
    "CitizenshipCountry",
    "TaxResidencyCountry",
    "BenefitPercent",
)
BENEFICIARY_RE = re.compile(r"^txtBeneficiary(?P<row>\d+)(?P<column>" + "|".join(BENEFICIARY_COLUMNS) + r")$")


@dataclass(frozen=True)
class ValidationIssue:
    severity: str
    code: str
    message: str
    page: int | None = None
    field: str | None = None


@dataclass(frozen=True)
class ParsedField:
    page: int
    name: str
    field_type: str
    x: float
    y: float
    w: float
    h: float
    value: str = ""


@dataclass
class ValidationResult:
    fields: list[ParsedField]
    issues: list[ValidationIssue]

    @property
    def errors(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "ERROR"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "WARN"]

    def has_failures(self, *, strict: bool) -> bool:
        return bool(self.errors or (strict and self.warnings))


def validate_acroform(
    fields_path: str | Path,
    *,
    input_pdf: str | Path | None = None,
    output_pdf: str | Path | None = None,
) -> ValidationResult:
    fields, issues = _parse_field_specs(fields_path)
    if fields:
        issues.extend(_validate_field_specs(fields, input_pdf=input_pdf))
    if output_pdf is not None:
        issues.extend(_validate_output_pdf(output_pdf, fields))
    return ValidationResult(fields=fields, issues=issues)


def _parse_field_specs(fields_path: str | Path) -> tuple[list[ParsedField], list[ValidationIssue]]:
    path = Path(fields_path)
    issues: list[ValidationIssue] = []
    fields: list[ParsedField] = []

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = set(reader.fieldnames or [])
        missing_columns = [column for column in REQUIRED_COLUMNS if column not in fieldnames]
        extra_check = [column for column in OPTIONAL_COLUMNS if column not in fieldnames]
        if missing_columns:
            issues.append(
                ValidationIssue("ERROR", "missing-columns", f"Missing required CSV column(s): {', '.join(missing_columns)}")
            )
            return fields, issues
        if extra_check:
            issues.append(
                ValidationIssue("WARN", "missing-optional-columns", f"Missing optional CSV column(s): {', '.join(extra_check)}")
            )

        for row_number, row in enumerate(reader, start=2):
            empty = [column for column in REQUIRED_COLUMNS if not (row.get(column) or "").strip()]
            if empty:
                issues.append(
                    ValidationIssue(
                        "ERROR",
                        "empty-required-cell",
                        f"Row {row_number} has empty required cell(s): {', '.join(empty)}",
                    )
                )
                continue

            name = (row.get("name") or "").strip()
            field_type = (row.get("type") or "").strip().lower()
            if field_type not in SUPPORTED_TYPES:
                issues.append(
                    ValidationIssue(
                        "ERROR",
                        "unsupported-type",
                        f"Row {row_number} has unsupported field type: {field_type}",
                        field=name,
                    )
                )
                continue

            try:
                page = int((row.get("page") or "").strip())
                x = float((row.get("x") or "").strip())
                y = float((row.get("y") or "").strip())
                w = float((row.get("w") or "").strip())
                h = float((row.get("h") or "").strip())
            except ValueError:
                issues.append(
                    ValidationIssue(
                        "ERROR",
                        "invalid-number",
                        f"Row {row_number} has invalid page or coordinate number.",
                        field=name,
                    )
                )
                continue

            fields.append(
                ParsedField(
                    page=page,
                    name=name,
                    field_type=_normalize_type(field_type),
                    x=x,
                    y=y,
                    w=w,
                    h=h,
                    value=(row.get("value") or "").strip(),
                )
            )

    return fields, issues


def _validate_field_specs(fields: list[ParsedField], *, input_pdf: str | Path | None) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    names = Counter(field.name for field in fields)
    for name, count in sorted(names.items()):
        if count > 1:
            issues.append(
                ValidationIssue("WARN", "duplicate-name", f"Field name appears {count} times. AcroForm fillers may treat duplicate names as the same value.", field=name)
            )
            duplicate_types = sorted({field.field_type for field in fields if field.name == name})
            if len(duplicate_types) > 1:
                issues.append(
                    ValidationIssue(
                        "WARN",
                        "duplicate-name-mixed-types",
                        f"Duplicate field name is used with multiple types: {', '.join(duplicate_types)}.",
                        field=name,
                    )
                )

    exact_cells = Counter((field.page, field.name, field.x, field.y, field.w, field.h) for field in fields)
    for (page, name, x, y, w, h), count in sorted(exact_cells.items()):
        if count > 1:
            issues.append(
                ValidationIssue(
                    "ERROR",
                    "duplicate-cell",
                    f"Identical field cell appears {count} times at x={x}, y={y}, w={w}, h={h}.",
                    page=page,
                    field=name,
                )
            )

    issues.extend(_validate_dimensions(fields))
    issues.extend(_validate_signature_fields(fields, names))
    issues.extend(_validate_repeated_beneficiary_tables(fields))

    if input_pdf is not None:
        issues.extend(_validate_pdf_bounds(input_pdf, fields))

    return issues


def _validate_dimensions(fields: list[ParsedField]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for field in fields:
        if field.page < 1:
            issues.append(ValidationIssue("ERROR", "invalid-page", "Page number must be 1 or greater.", field=field.name))
        if field.w <= 0 or field.h <= 0:
            issues.append(ValidationIssue("ERROR", "invalid-size", "Field width and height must be positive.", page=field.page, field=field.name))
            continue

        if field.field_type in {"text", "textarea"} and field.h < 10:
            issues.append(ValidationIssue("WARN", "small-text-height", f"Text field height is low: {field.h:.2f}pt.", page=field.page, field=field.name))
        if field.field_type == "textarea" and field.h < 16:
            issues.append(ValidationIssue("WARN", "small-textarea-height", f"Textarea height is low: {field.h:.2f}pt.", page=field.page, field=field.name))
        if field.field_type == "checkbox" and (field.w < 6 or field.h < 6 or field.w > 30 or field.h > 30):
            issues.append(ValidationIssue("WARN", "checkbox-size", f"Checkbox size looks unusual: {field.w:.2f}x{field.h:.2f}pt.", page=field.page, field=field.name))
        if field.field_type == "image" and (field.w < 20 or field.h < 12):
            issues.append(ValidationIssue("WARN", "small-image-field", f"Image field size is low: {field.w:.2f}x{field.h:.2f}pt.", page=field.page, field=field.name))
        if field.name.startswith("txtBeneficiary") and field.h < 20:
            issues.append(ValidationIssue("WARN", "small-beneficiary-field", f"Beneficiary table field height should be at least 20pt; got {field.h:.2f}pt.", page=field.page, field=field.name))

    return issues


def _validate_signature_fields(fields: list[ParsedField], names: Counter[str]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for field in fields:
        is_image_signature = _is_image_signature_name(field.name)
        if is_image_signature and field.field_type != "image":
            issues.append(
                ValidationIssue(
                    "ERROR",
                    "signature-format",
                    "Signature image fields must use type=image, not type="
                    + field.field_type
                    + ".",
                    page=field.page,
                    field=field.name,
                )
            )
        if field.field_type == "signature":
            issues.append(
                ValidationIssue(
                    "ERROR",
                    "digital-signature-type",
                    "Use type=image for image signatures. type=signature creates a real digital-signature field.",
                    page=field.page,
                    field=field.name,
                )
            )
        if field.field_type == "image" and not field.name.startswith("img"):
            issues.append(
                ValidationIssue("WARN", "image-name-format", "Image field names should start with img.", page=field.page, field=field.name)
            )

    for name, count in sorted(names.items()):
        if count > 1 and _is_image_signature_name(name):
            issues.append(
                ValidationIssue("WARN", "duplicate-signature-name", f"Signature field name appears {count} times.", field=name)
            )

    return issues


def _validate_repeated_beneficiary_tables(fields: list[ParsedField]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    by_page: dict[int, dict[int, set[str]]] = defaultdict(lambda: defaultdict(set))
    for field in fields:
        match = BENEFICIARY_RE.match(field.name)
        if match is None:
            continue
        by_page[field.page][int(match.group("row"))].add(match.group("column"))

    required = set(BENEFICIARY_COLUMNS)
    for page, rows in sorted(by_page.items()):
        start = min(rows)
        expected_rows = range(start, start + 3)
        missing = [
            f"txtBeneficiary{row}{column}"
            for row in expected_rows
            for column in BENEFICIARY_COLUMNS
            if column not in rows.get(row, set())
        ]
        if missing:
            issues.append(
                ValidationIssue("ERROR", "incomplete-beneficiary-table", f"Missing repeated beneficiary table field(s): {', '.join(missing)}", page=page)
            )
        for row, columns in sorted(rows.items()):
            missing_columns = sorted(required - columns)
            if missing_columns:
                issues.append(
                    ValidationIssue(
                        "ERROR",
                        "incomplete-beneficiary-row",
                        f"Beneficiary row {row} is missing column(s): {', '.join(missing_columns)}.",
                        page=page,
                    )
                )

    return issues


def _validate_pdf_bounds(input_pdf: str | Path, fields: list[ParsedField]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    with pikepdf.Pdf.open(str(input_pdf)) as pdf:
        page_count = len(pdf.pages)
        for field in fields:
            if field.page > page_count:
                issues.append(
                    ValidationIssue(
                        "ERROR",
                        "page-out-of-range",
                        f"Field references page {field.page}, but PDF has {page_count} page(s).",
                        page=field.page,
                        field=field.name,
                    )
                )
                continue

            page = pdf.pages[field.page - 1]
            media_box = [float(value) for value in page.obj.get("/MediaBox", [0, 0, 612, 792])]
            page_w = media_box[2] - media_box[0]
            page_h = media_box[3] - media_box[1]
            if field.x < 0 or field.y < 0 or field.x + field.w > page_w + 0.5 or field.y + field.h > page_h + 0.5:
                issues.append(
                    ValidationIssue(
                        "ERROR",
                        "field-out-of-page",
                        f"Field rectangle is outside page bounds {page_w:.2f}x{page_h:.2f}.",
                        page=field.page,
                        field=field.name,
                    )
                )

    return issues


def _validate_output_pdf(output_pdf: str | Path, fields: list[ParsedField]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    expected = Counter(field.name for field in fields)

    with pikepdf.Pdf.open(str(output_pdf)) as pdf:
        acroform = pdf.Root.get("/AcroForm")
        if acroform is None or "/Fields" not in acroform:
            return [ValidationIssue("ERROR", "missing-acroform", "Output PDF does not contain an AcroForm field tree.")]

        pdf_fields = list(acroform["/Fields"])
        actual = Counter(str(field.get("/T")) for field in pdf_fields)
        if len(pdf_fields) != len(fields):
            issues.append(
                ValidationIssue("ERROR", "field-count-mismatch", f"Output PDF has {len(pdf_fields)} field(s), expected {len(fields)}.")
            )

        for name, count in sorted(expected.items()):
            if actual.get(name, 0) != count:
                issues.append(
                    ValidationIssue("ERROR", "field-name-count-mismatch", f"Output PDF has {actual.get(name, 0)} instance(s), expected {count}.", field=name)
                )

        for name, count in sorted(actual.items()):
            if count > 1:
                issues.append(
                    ValidationIssue("WARN", "pdf-duplicate-name", f"Output PDF contains {count} fields named {name}.", field=name)
                )

        spec_types_by_name: dict[str, set[str]] = defaultdict(set)
        for field in fields:
            spec_types_by_name[field.name].add(field.field_type)

        for pdf_field in pdf_fields:
            name = str(pdf_field.get("/T"))
            field_type = str(pdf_field.get("/FT"))
            if field_type == "/Sig":
                issues.append(ValidationIssue("ERROR", "pdf-signature-field", "Output PDF contains /Sig field; image signatures should be /Btn pushbuttons.", field=name))
            if _is_image_signature_name(name):
                flags = int(pdf_field.get("/Ff", 0))
                if field_type != "/Btn" or not (flags & 65536):
                    issues.append(
                        ValidationIssue("ERROR", "pdf-image-signature-format", "Image signature field must be a pushbutton /Btn with flag 65536.", field=name)
                    )
            flags = int(pdf_field.get("/Ff", 0))
            if field_type == "/Btn" and not (flags & 65536) and "checkbox" in spec_types_by_name.get(name, set()):
                normal = (pdf_field.get("/AP") or {}).get("/N") if pdf_field.get("/AP") else None
                normal_keys = set(normal.keys()) if normal is not None else set()
                if "/Off" not in normal_keys or "/Yes" not in normal_keys:
                    issues.append(
                        ValidationIssue("ERROR", "pdf-checkbox-appearance", "Checkbox field must include /AP /N appearances for /Off and /Yes.", field=name)
                    )

    return issues


def _normalize_type(field_type: str) -> str:
    if field_type in {"tx"}:
        return "text"
    if field_type in {"check", "chk"}:
        return "checkbox"
    if field_type == "img":
        return "image"
    if field_type == "sig":
        return "signature"
    return field_type


def _is_image_signature_name(name: str) -> bool:
    lowered = name.lower()
    return lowered.startswith("img") and "signature" in lowered
