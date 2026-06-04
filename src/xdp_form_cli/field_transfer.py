from __future__ import annotations

import csv
from pathlib import Path

import pikepdf
from pikepdf import Dictionary, Name

from xdp_form_cli.acroform_builder import AcroFieldSpec, create_acroform_pdf


PAGE_SIZE_TOLERANCE_PT = 2.0


def extract_field_specs_from_pdf(source_pdf: str | Path) -> list[AcroFieldSpec]:
    source = Path(source_pdf)
    specs: list[AcroFieldSpec] = []

    with pikepdf.Pdf.open(str(source)) as pdf:
        for page_index, page in enumerate(pdf.pages, start=1):
            annots = page.obj.get(Name("/Annots")) or []
            for annot in annots:
                if not isinstance(annot, Dictionary):
                    continue
                if annot.get(Name("/Subtype")) != Name("/Widget"):
                    continue
                field = _terminal_field(annot)
                if field is None or Name("/T") not in field or Name("/Rect") not in field:
                    continue
                spec = _field_spec_from_widget(page_index, field)
                if spec is not None:
                    specs.append(spec)

    if not specs:
        raise ValueError("Template PDF contains no supported AcroForm fields to transfer.")
    return specs


def transfer_fields_to_pdf(
    template_pdf: str | Path,
    target_pdf: str | Path,
    output_pdf: str | Path,
    *,
    csv_path: str | Path | None = None,
) -> tuple[Path, Path, int]:
    template = Path(template_pdf)
    target = Path(target_pdf)
    output = Path(output_pdf)
    csv_out = Path(csv_path) if csv_path else output.with_suffix(".fields.csv")

    _validate_layout_compatibility(template, target)
    specs = extract_field_specs_from_pdf(template)
    _write_field_specs_csv(specs, csv_out)
    out_pdf, count = create_acroform_pdf(target, csv_out, output)
    return out_pdf, csv_out, count


def _field_spec_from_widget(page_index: int, field: Dictionary) -> AcroFieldSpec | None:
    rect = field.get(Name("/Rect"))
    if rect is None or len(rect) != 4:
        return None

    x0 = float(rect[0])
    y0 = float(rect[1])
    x1 = float(rect[2])
    y1 = float(rect[3])
    name = str(field.get(Name("/T"))).strip()
    if not name:
        return None

    field_type = _map_field_type(field, name)
    if field_type is None:
        return None

    return AcroFieldSpec(
        page=page_index,
        name=name,
        field_type=field_type,
        x=round(min(x0, x1), 2),
        y=round(min(y0, y1), 2),
        w=round(abs(x1 - x0), 2),
        h=round(abs(y1 - y0), 2),
        value="",
    )


def _map_field_type(field: Dictionary, name: str) -> str | None:
    ft = field.get(Name("/FT"))
    if ft == Name("/Sig"):
        return "image"
    if _looks_like_signature_field_name(name):
        return "image"
    if ft == Name("/Tx"):
        flags = int(field.get(Name("/Ff"), 0))
        return "textarea" if flags & 4096 else "text"
    if ft == Name("/Btn"):
        flags = int(field.get(Name("/Ff"), 0))
        if flags & 65536:
            return "image"
        return "checkbox"
    return None


def _looks_like_signature_field_name(name: str) -> bool:
    lowered = name.casefold()
    return (
        (lowered.startswith("img") and "signature" in lowered)
        or "signature" in lowered
        or "חתימה" in lowered
        or "חתום" in lowered
        or "חותם" in lowered
    )


def _terminal_field(widget: Dictionary) -> Dictionary | None:
    node: object = widget
    while isinstance(node, Dictionary):
        if Name("/T") in node:
            return node
        node = node.get(Name("/Parent"))
    return None


def _page_size(pdf: pikepdf.Pdf, page_index: int) -> tuple[float, float]:
    page = pdf.pages[page_index]
    media_box = page.obj.get(Name("/MediaBox"))
    if media_box is None or len(media_box) != 4:
        raise ValueError(f"PDF page {page_index + 1} has no usable MediaBox.")
    width = float(media_box[2]) - float(media_box[0])
    height = float(media_box[3]) - float(media_box[1])
    return round(width, 2), round(height, 2)


def _validate_layout_compatibility(template_pdf: Path, target_pdf: Path) -> None:
    with pikepdf.Pdf.open(str(template_pdf)) as template, pikepdf.Pdf.open(str(target_pdf)) as target:
        if len(template.pages) != len(target.pages):
            raise ValueError(
                f"Template/target page-count mismatch: template has {len(template.pages)} pages, target has {len(target.pages)}."
            )
        for index in range(len(template.pages)):
            template_size = _page_size(template, index)
            target_size = _page_size(target, index)
            if (
                abs(template_size[0] - target_size[0]) > PAGE_SIZE_TOLERANCE_PT
                or abs(template_size[1] - target_size[1]) > PAGE_SIZE_TOLERANCE_PT
            ):
                raise ValueError(
                    "Template and target page sizes do not match closely enough for field transfer."
                )


def _write_field_specs_csv(specs: list[AcroFieldSpec], csv_path: Path) -> Path:
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["page", "name", "type", "x", "y", "w", "h", "value"])
        for spec in specs:
            writer.writerow([spec.page, spec.name, spec.field_type, spec.x, spec.y, spec.w, spec.h, spec.value])
    return csv_path
