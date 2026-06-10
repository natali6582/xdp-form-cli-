"""Build a minimal XFA template from scratch and embed it in a PDF.

Static PDFs have no XFA packet, so a PDF produced by ``create-acroform``
fills fine in PDF viewers but shows no fields in LiveCycle Designer's
Design View (Designer edits the XFA template, not AcroForm widgets).

This module generates a minimal, valid XDP packet — pageSet sized from the
real PDF pages plus one ``PageN`` subform per page carrying the detected
fields — and embeds it at ``/Root /AcroForm /XFA``. The AcroForm layer is
left untouched, so PDF view keeps working exactly as before. The feature
is opt-in via ``create-acroform --design-xfa``.
"""

from __future__ import annotations

from pathlib import Path

import pikepdf
from lxml import etree
from pikepdf import Dictionary, Name

from xdp_form_cli.auto_form import AutoFieldSpec
from xdp_form_cli.safe_io import backup_if_exists, require_safe_output
from xdp_form_cli.xfa_field_injector import XFA_TEMPLATE_NS, _build_field_element

XDP_NS = "http://ns.adobe.com/xdp/"


def build_scratch_xfa_bytes(
    page_sizes_pt: list[tuple[float, float]],
    specs: list[AutoFieldSpec],
) -> bytes:
    """Build an XDP packet with one PageN subform per page and its fields.

    ``page_sizes_pt`` holds ``(width, height)`` per page, in PDF points.
    Field coordinates in ``specs`` are PDF bottom-left points; the standard
    pt-to-mm/top-left conversion happens in the shared field builder.
    """
    if not page_sizes_pt:
        raise ValueError("At least one page size is required to build an XFA template.")

    xdp = etree.Element(f"{{{XDP_NS}}}xdp", nsmap={"xdp": XDP_NS})
    template = etree.SubElement(
        xdp, f"{{{XFA_TEMPLATE_NS}}}template", nsmap={None: XFA_TEMPLATE_NS}
    )
    topmost = etree.SubElement(template, f"{{{XFA_TEMPLATE_NS}}}subform")
    topmost.set("name", "topmostSubform")
    topmost.set("layout", "tb")
    topmost.set("locale", "en_US")

    page_set = etree.SubElement(topmost, f"{{{XFA_TEMPLATE_NS}}}pageSet")
    for index, (width_pt, height_pt) in enumerate(page_sizes_pt, start=1):
        page_area = etree.SubElement(page_set, f"{{{XFA_TEMPLATE_NS}}}pageArea")
        page_area.set("name", f"PageArea{index}")
        page_area.set("id", f"PageArea{index}")
        content_area = etree.SubElement(page_area, f"{{{XFA_TEMPLATE_NS}}}contentArea")
        content_area.set("x", "0pt")
        content_area.set("y", "0pt")
        content_area.set("w", f"{width_pt:g}pt")
        content_area.set("h", f"{height_pt:g}pt")
        medium = etree.SubElement(page_area, f"{{{XFA_TEMPLATE_NS}}}medium")
        medium.set("stock", "default")
        medium.set("short", f"{width_pt:g}pt")
        medium.set("long", f"{height_pt:g}pt")

    for index, (width_pt, height_pt) in enumerate(page_sizes_pt, start=1):
        page_subform = etree.SubElement(topmost, f"{{{XFA_TEMPLATE_NS}}}subform")
        page_subform.set("name", f"Page{index}")
        page_subform.set("x", "0pt")
        page_subform.set("y", "0pt")
        page_subform.set("w", f"{width_pt:g}pt")
        page_subform.set("h", f"{height_pt:g}pt")
        break_el = etree.SubElement(page_subform, f"{{{XFA_TEMPLATE_NS}}}break")
        break_el.set("before", "pageArea")
        break_el.set("beforeTarget", f"#PageArea{index}")
        break_el.set("startNew", "1")
        bind = etree.SubElement(page_subform, f"{{{XFA_TEMPLATE_NS}}}bind")
        bind.set("match", "none")

        for spec in specs:
            if spec.page != index:
                continue
            page_subform.append(_build_field_element(spec, height_pt))

    return etree.tostring(xdp, encoding="UTF-8", xml_declaration=True, pretty_print=False)


def embed_scratch_xfa(
    source_pdf: str | Path,
    output_pdf: str | Path,
    specs: list[AutoFieldSpec],
    *,
    overwrite: bool = False,
) -> Path:
    """Embed a scratch-built XFA packet into a copy of the source PDF.

    Only ``/AcroForm /XFA`` is added; existing AcroForm fields and widget
    annotations are preserved unchanged.
    """
    source = Path(source_pdf)
    output = Path(output_pdf)
    if output.resolve() == source.resolve():
        raise ValueError("--output must be a new file path, not the source file.")
    require_safe_output(output, overwrite=overwrite)
    if overwrite:
        backup_if_exists(output)

    with pikepdf.Pdf.open(str(source)) as pdf:
        page_sizes = _page_sizes_pt(pdf)
        raw_xfa = build_scratch_xfa_bytes(page_sizes, specs)
        acroform = pdf.Root.get(Name("/AcroForm"))
        if acroform is None:
            acroform = pdf.make_indirect(Dictionary())
            pdf.Root[Name("/AcroForm")] = acroform
        acroform[Name("/XFA")] = pdf.make_stream(raw_xfa)
        pdf.save(str(output))

    return output


def _page_sizes_pt(pdf: pikepdf.Pdf) -> list[tuple[float, float]]:
    sizes: list[tuple[float, float]] = []
    for page in pdf.pages:
        media = page.obj.get(Name("/MediaBox"))
        try:
            width = float(media[2]) - float(media[0])
            height = float(media[3]) - float(media[1])
        except (TypeError, ValueError, IndexError):
            width, height = 595.0, 842.0
        sizes.append((width, height))
    return sizes
