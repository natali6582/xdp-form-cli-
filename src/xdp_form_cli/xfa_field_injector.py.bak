"""Inject XFA <field> elements into an existing XFA template packet.

The auto-form flow detects fields from the PDF content stream and writes them
to the AcroForm layer. When the source PDF carries an XFA packet (as Adobe
LiveCycle shells do), we also need to add matching <field> elements into the
XFA template so XFA-aware viewers (Adobe Acrobat) see the same fields.

Coordinates: PDF uses points with origin at the page's bottom-left; XFA uses
millimetres with origin at the subform's top-left.  We convert by multiplying
by PT_TO_MM and flipping the y-axis against the page height.
"""

from __future__ import annotations

from pathlib import Path

import pikepdf
from lxml import etree
from pikepdf import Array, Dictionary, Name, Stream

from xdp_form_cli.auto_form import AutoFieldSpec
from xdp_form_cli.pdf_xfa_editor import PdfXfaEditor
from xdp_form_cli.xdp_editor import XdpEditor


XFA_TEMPLATE_NS = "http://www.xfa.org/schema/xfa-template/2.5/"
NSMAP = {None: XFA_TEMPLATE_NS}
PT_TO_MM = 0.3528
DEFAULT_FONT_TYPEFACE = "Arial"
DEFAULT_FONT_SIZE_PT = 10.0
CHECKBOX_TYPEFACE = "Adobe Pi Std"
BUTTON_TYPEFACE = "Myriad Pro"
BUTTON_FILL_COLOR = "212,208,200"


def inject_xfa_fields(
    input_path: str | Path,
    output_path: str | Path,
    specs: list[AutoFieldSpec],
) -> Path:
    """Add a <field> for each spec into the matching PageN subform, save to output."""
    source = Path(input_path)
    output = Path(output_path)
    if output.resolve() == source.resolve():
        raise ValueError("XFA injector output must differ from the source PDF path.")

    editor = PdfXfaEditor(source)
    try:
        page_heights = _page_heights_pt(editor.pdf)
        topmost = editor.xdp._find_topmost_subform()
        page_subforms: dict[str, etree._Element] = {}
        for child in topmost:
            if not isinstance(child.tag, str):
                continue
            if etree.QName(child).localname != "subform":
                continue
            name = child.get("name") or ""
            if name.startswith("Page"):
                page_subforms[name] = child

        for spec in specs:
            page_name = f"Page{spec.page}"
            page_subform = page_subforms.get(page_name)
            if page_subform is None:
                # No matching XFA page; skip silently rather than failing the whole run.
                continue
            page_h_pt = page_heights.get(spec.page, 841.92)
            field_element = _build_field_element(spec, page_h_pt)
            page_subform.append(field_element)

        editor.save_copy(output)
    finally:
        editor.close()

    return output


def inject_xfa_fields_from_template(
    source_pdf: str | Path,
    xfa_template_path: str | Path,
    output_pdf: str | Path,
    specs: list[AutoFieldSpec],
) -> Path:
    """Inject fields into an external XFA template and embed it in the source PDF.

    Template may be a raw .xdp/.xml file or another PDF that carries an XFA packet
    (we extract the template packet from it).
    """
    source = Path(source_pdf)
    output = Path(output_pdf)
    template_path = Path(xfa_template_path)
    if output.resolve() == source.resolve():
        raise ValueError("XFA injector output must differ from the source PDF path.")
    if not template_path.is_file():
        raise ValueError(f"XFA template file not found: {template_path}")

    if template_path.suffix.lower() == ".pdf":
        template_editor = PdfXfaEditor(template_path)
        try:
            xdp = template_editor.xdp
            _inject_specs_into_xdp(xdp, source, specs)
            updated_xfa = xdp.to_bytes()
        finally:
            template_editor.close()
    else:
        raw_template = template_path.read_bytes()
        xdp = XdpEditor.from_bytes(raw_template, str(template_path))
        _inject_specs_into_xdp(xdp, source, specs)
        updated_xfa = xdp.to_bytes()

    with pikepdf.Pdf.open(str(source)) as pdf:
        root = pdf.Root
        acroform = root.get(Name("/AcroForm"))
        if acroform is None:
            acroform = Dictionary()
            root[Name("/AcroForm")] = acroform
        acroform[Name("/XFA")] = pdf.make_stream(updated_xfa)
        pdf.save(str(output))

    return output


def _inject_specs_into_xdp(xdp: XdpEditor, source_pdf: Path, specs: list[AutoFieldSpec]) -> None:
    topmost = xdp._find_topmost_subform()
    page_subforms: dict[str, etree._Element] = {}
    for child in topmost:
        if not isinstance(child.tag, str):
            continue  # skip comments/processing instructions
        if etree.QName(child).localname != "subform":
            continue
        name = child.get("name") or ""
        if name.startswith("Page"):
            page_subforms[name] = child
    with pikepdf.Pdf.open(str(source_pdf)) as pdf:
        page_heights = _page_heights_pt(pdf)
    for spec in specs:
        page_name = f"Page{spec.page}"
        page_subform = page_subforms.get(page_name)
        if page_subform is None:
            continue
        page_h_pt = page_heights.get(spec.page, 841.92)
        field_element = _build_field_element(spec, page_h_pt)
        page_subform.append(field_element)


def _page_heights_pt(pdf: pikepdf.Pdf) -> dict[int, float]:
    heights: dict[int, float] = {}
    for index, page in enumerate(pdf.pages, start=1):
        media = page.obj.get(Name("/MediaBox"))
        if media is None:
            continue
        try:
            heights[index] = float(media[3]) - float(media[1])
        except (TypeError, ValueError, IndexError):
            continue
    return heights


def _build_field_element(spec: AutoFieldSpec, page_height_pt: float) -> etree._Element:
    x_mm = spec.x * PT_TO_MM
    y_mm = (page_height_pt - spec.y - spec.h) * PT_TO_MM
    w_mm = spec.w * PT_TO_MM
    h_mm = spec.h * PT_TO_MM

    field = etree.Element(
        f"{{{XFA_TEMPLATE_NS}}}field",
        nsmap=NSMAP,
        attrib={
            "name": spec.name,
            "x": f"{x_mm:.4f}mm",
            "y": f"{y_mm:.4f}mm",
            "w": f"{w_mm:.4f}mm",
            "h": f"{h_mm:.4f}mm",
        },
    )

    if spec.field_type in {"checkbox", "check", "chk"}:
        _populate_checkbox(field)
    elif spec.field_type in {"image", "img"}:
        _populate_image(field)
    else:
        _populate_text(field)

    return field


def _populate_text(field: etree._Element) -> None:
    ui = etree.SubElement(field, f"{{{XFA_TEMPLATE_NS}}}ui")
    etree.SubElement(ui, f"{{{XFA_TEMPLATE_NS}}}textEdit")
    font = etree.SubElement(field, f"{{{XFA_TEMPLATE_NS}}}font")
    font.set("typeface", DEFAULT_FONT_TYPEFACE)
    font.set("size", f"{DEFAULT_FONT_SIZE_PT:g}pt")
    value = etree.SubElement(field, f"{{{XFA_TEMPLATE_NS}}}value")
    etree.SubElement(value, f"{{{XFA_TEMPLATE_NS}}}text")


def _populate_checkbox(field: etree._Element) -> None:
    ui = etree.SubElement(field, f"{{{XFA_TEMPLATE_NS}}}ui")
    check_button = etree.SubElement(ui, f"{{{XFA_TEMPLATE_NS}}}checkButton")
    border = etree.SubElement(check_button, f"{{{XFA_TEMPLATE_NS}}}border")
    border.set("hand", "right")
    edge = etree.SubElement(border, f"{{{XFA_TEMPLATE_NS}}}edge")
    edge.set("stroke", "lowered")

    font = etree.SubElement(field, f"{{{XFA_TEMPLATE_NS}}}font")
    font.set("typeface", CHECKBOX_TYPEFACE)
    font.set("size", "0pt")
    etree.SubElement(field, f"{{{XFA_TEMPLATE_NS}}}margin")
    para = etree.SubElement(field, f"{{{XFA_TEMPLATE_NS}}}para")
    para.set("vAlign", "middle")

    hidden_items = etree.SubElement(field, f"{{{XFA_TEMPLATE_NS}}}items")
    hidden_items.set("save", "1")
    hidden_items.set("presence", "hidden")
    etree.SubElement(hidden_items, f"{{{XFA_TEMPLATE_NS}}}text").text = "1"
    etree.SubElement(hidden_items, f"{{{XFA_TEMPLATE_NS}}}text").text = "0"

    visible_items = etree.SubElement(field, f"{{{XFA_TEMPLATE_NS}}}items")
    etree.SubElement(visible_items, f"{{{XFA_TEMPLATE_NS}}}text").text = "2"


def _populate_image(field: etree._Element) -> None:
    ui = etree.SubElement(field, f"{{{XFA_TEMPLATE_NS}}}ui")
    button = etree.SubElement(ui, f"{{{XFA_TEMPLATE_NS}}}button")
    button.set("highlight", "push")

    font = etree.SubElement(field, f"{{{XFA_TEMPLATE_NS}}}font")
    font.set("typeface", BUTTON_TYPEFACE)

    caption = etree.SubElement(field, f"{{{XFA_TEMPLATE_NS}}}caption")
    caption_para = etree.SubElement(caption, f"{{{XFA_TEMPLATE_NS}}}para")
    caption_para.set("vAlign", "middle")
    caption_para.set("hAlign", "center")

    border = etree.SubElement(field, f"{{{XFA_TEMPLATE_NS}}}border")
    border.set("hand", "right")
    border.set("break", "open")
    edge = etree.SubElement(border, f"{{{XFA_TEMPLATE_NS}}}edge")
    edge.set("stroke", "raised")
    edge.set("cap", "butt")
    fill = etree.SubElement(border, f"{{{XFA_TEMPLATE_NS}}}fill")
    color = etree.SubElement(fill, f"{{{XFA_TEMPLATE_NS}}}color")
    color.set("value", BUTTON_FILL_COLOR)

    bind = etree.SubElement(field, f"{{{XFA_TEMPLATE_NS}}}bind")
    bind.set("match", "none")
