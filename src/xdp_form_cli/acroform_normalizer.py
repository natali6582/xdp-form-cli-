from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal

import pikepdf
from pikepdf import Array, Dictionary, Name, Stream, String

from xdp_form_cli.acroform_builder import (
    ACROFORM_DEFAULT_APPEARANCE,
    ACROFORM_FONT_RESOURCE,
)
from xdp_form_cli.field_truth import FieldMatch


PdfFormKind = Literal["xfa", "acroform", "static"]


@dataclass
class ExistingAcroformNormalizeReport:
    output_path: Path
    form_kind: PdfFormKind
    field_count: int = 0
    font_normalized: int = 0
    transparent_normalized: int = 0
    image_signature_normalized: int = 0
    renamed: int = 0
    matches: list[FieldMatch] = field(default_factory=list)


def detect_pdf_form_kind(input_path: str | Path) -> PdfFormKind:
    with pikepdf.Pdf.open(str(input_path)) as pdf:
        acroform = pdf.Root.get(Name("/AcroForm"))
        if acroform is None:
            return "static"
        if acroform.get(Name("/XFA")) is not None:
            return "xfa"
        if len(list(acroform.get(Name("/Fields"), []))) > 0:
            return "acroform"
        return "static"


def normalize_existing_acroform_pdf(
    input_path: str | Path,
    output_path: str | Path,
    *,
    matcher: Callable[[str], FieldMatch] | None = None,
) -> ExistingAcroformNormalizeReport:
    source = Path(input_path)
    output = Path(output_path)
    if output.resolve() == source.resolve():
        raise ValueError("--output must be a new PDF file path, not the source PDF.")

    report = ExistingAcroformNormalizeReport(output_path=output, form_kind="acroform")

    with pikepdf.Pdf.open(str(source)) as pdf:
        acroform = pdf.Root.get(Name("/AcroForm"))
        if acroform is None or Name("/Fields") not in acroform:
            raise ValueError("PDF does not contain an editable AcroForm field tree.")

        _ensure_acroform_defaults(acroform)
        for field_obj in _iter_field_tree(acroform.get(Name("/Fields"), [])):
            report.field_count += 1
            original_name = _field_name(field_obj)
            field_type = field_obj.get(Name("/FT"))
            flags = int(field_obj.get(Name("/Ff"), 0))

            if matcher is not None and original_name:
                match = matcher(original_name)
                report.matches.append(match)
                if match.changed:
                    field_obj[Name("/T")] = String(match.canonical_name)
                    original_name = match.canonical_name
                    report.renamed += 1

            if field_type == Name("/Tx"):
                field_obj[Name("/DA")] = String(ACROFORM_DEFAULT_APPEARANCE)
                report.font_normalized += 1
                if _remove_visible_box(field_obj):
                    report.transparent_normalized += 1
                if Name("/AP") in field_obj:
                    del field_obj[Name("/AP")]
                continue

            if _is_image_signature_field(original_name, field_type, flags):
                field_obj[Name("/FT")] = Name("/Btn")
                field_obj[Name("/Ff")] = flags | 65536
                if Name("/V") in field_obj:
                    del field_obj[Name("/V")]
                if Name("/DV") in field_obj:
                    del field_obj[Name("/DV")]
                _remove_visible_box(field_obj)
                field_obj[Name("/AP")] = Dictionary(N=_transparent_appearance(pdf, field_obj))
                report.image_signature_normalized += 1
                report.transparent_normalized += 1

        if report.field_count == 0:
            raise ValueError("PDF has an AcroForm dictionary but no fields.")

        pdf.save(str(output))

    return report


def _ensure_acroform_defaults(acroform: Dictionary) -> None:
    acroform[Name("/NeedAppearances")] = True
    acroform[Name("/DA")] = String(ACROFORM_DEFAULT_APPEARANCE)
    resources = acroform.get(Name("/DR"))
    if resources is None:
        resources = Dictionary()
        acroform[Name("/DR")] = resources

    fonts = resources.get(Name("/Font"))
    if fonts is None:
        fonts = Dictionary()
        resources[Name("/Font")] = fonts

    fonts[Name(f"/{ACROFORM_FONT_RESOURCE}")] = Dictionary(
        Type=Name("/Font"),
        Subtype=Name("/Type1"),
        BaseFont=Name("/Arial"),
    )


def _iter_field_tree(fields: object) -> list[Dictionary]:
    result: list[Dictionary] = []
    for item in fields:
        _walk_field(item, result)
    return result


def _walk_field(field_obj: Dictionary, result: list[Dictionary]) -> None:
    if Name("/T") in field_obj or Name("/FT") in field_obj or field_obj.get(Name("/Subtype")) == Name("/Widget"):
        result.append(field_obj)

    kids = field_obj.get(Name("/Kids"))
    if kids is None:
        return
    for kid in kids:
        _walk_field(kid, result)


def _field_name(field_obj: Dictionary) -> str:
    value = field_obj.get(Name("/T"))
    if value is None:
        return ""
    return str(value).strip()


def _is_image_signature_field(name: str, field_type: object, flags: int) -> bool:
    lowered = name.casefold()
    if field_type == Name("/Btn") and (flags & 65536):
        return True
    return lowered.startswith("img") and "signature" in lowered


def _remove_visible_box(field_obj: Dictionary) -> bool:
    changed = False
    if field_obj.get(Name("/Border")) != Array([0, 0, 0]):
        field_obj[Name("/Border")] = Array([0, 0, 0])
        changed = True

    border_style = field_obj.get(Name("/BS"))
    if border_style is None or float(border_style.get(Name("/W"), 0)) != 0:
        field_obj[Name("/BS")] = Dictionary(W=0, S=Name("/S"))
        changed = True

    appearance = field_obj.get(Name("/MK"))
    if appearance is not None:
        for key in (Name("/BG"), Name("/BC")):
            if key in appearance:
                del appearance[key]
                changed = True

    return changed


def _transparent_appearance(pdf: pikepdf.Pdf, field_obj: Dictionary) -> Stream:
    rect = list(field_obj.get(Name("/Rect"), [0, 0, 1, 1]))
    if len(rect) >= 4:
        width = max(float(rect[2]) - float(rect[0]), 1.0)
        height = max(float(rect[3]) - float(rect[1]), 1.0)
    else:
        width = 1.0
        height = 1.0

    return Stream(
        pdf,
        b"",
        Type=Name("/XObject"),
        Subtype=Name("/Form"),
        BBox=Array([0, 0, width, height]),
        Matrix=Array([1, 0, 0, 1, 0, 0]),
    )
