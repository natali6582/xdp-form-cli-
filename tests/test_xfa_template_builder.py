from __future__ import annotations

from pathlib import Path

import pikepdf
from lxml import etree
from pikepdf import Dictionary, Name

from xdp_form_cli.auto_form import AutoFieldSpec
from xdp_form_cli.cli import main
from xdp_form_cli.pdf_xfa_editor import PdfXfaEditor
from xdp_form_cli.xfa_template_builder import (
    build_scratch_xfa_bytes,
    embed_scratch_xfa,
)

XFA_NS = "http://www.xfa.org/schema/xfa-template/2.5/"


def _specs() -> list[AutoFieldSpec]:
    return [
        AutoFieldSpec(page=1, name="txtFullName", field_type="text", x=130, y=732, w=200, h=18),
        AutoFieldSpec(page=1, name="chkApproved", field_type="checkbox", x=60, y=540, w=12, h=12),
        AutoFieldSpec(page=1, name="imgSignature", field_type="image", x=130, y=440, w=180, h=20),
    ]


def _write_static_pdf(path: Path) -> Path:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(595, 842))
    pdf.save(path)
    return path


def test_scratch_template_has_page_subform_with_fields() -> None:
    raw = build_scratch_xfa_bytes([(595.0, 842.0)], _specs())

    root = etree.fromstring(raw)
    fields = root.findall(f".//{{{XFA_NS}}}subform[@name='Page1']/{{{XFA_NS}}}field")

    names = {f.get("name") for f in fields}
    assert names == {"txtFullName", "chkApproved", "imgSignature"}


def test_scratch_template_page_area_matches_page_size() -> None:
    raw = build_scratch_xfa_bytes([(595.0, 842.0)], _specs())

    root = etree.fromstring(raw)
    medium = root.find(f".//{{{XFA_NS}}}pageArea/{{{XFA_NS}}}medium")
    assert medium is not None
    assert medium.get("short") == "595pt"
    assert medium.get("long") == "842pt"

    page1 = root.find(f".//{{{XFA_NS}}}subform[@name='Page1']")
    assert page1.get("w") == "595pt"
    assert page1.get("h") == "842pt"


def test_scratch_template_field_coordinates_are_top_left_mm() -> None:
    raw = build_scratch_xfa_bytes([(595.0, 842.0)], _specs())

    root = etree.fromstring(raw)
    field = root.find(f".//{{{XFA_NS}}}field[@name='txtFullName']")
    # x: 130pt * 0.3528; y: (842 - 732 - 18)pt from the top * 0.3528.
    assert abs(float(field.get("x").rstrip("m")) - 130 * 0.3528) < 0.01
    assert abs(float(field.get("y").rstrip("m")) - (842 - 732 - 18) * 0.3528) < 0.01


def test_embed_scratch_xfa_adds_packet_and_keeps_widgets(tmp_path: Path) -> None:
    source = _write_static_pdf(tmp_path / "static.pdf")
    # Give the source one AcroForm widget to prove the layer survives.
    with pikepdf.Pdf.open(source, allow_overwriting_input=True) as pdf:
        page = pdf.pages[0]
        widget = pdf.make_indirect(
            Dictionary(
                Type=Name("/Annot"), Subtype=Name("/Widget"), FT=Name("/Tx"),
                T=pikepdf.String("txtExisting"), Rect=[100, 100, 200, 120],
            )
        )
        page.obj[Name("/Annots")] = pdf.make_indirect([widget])
        pdf.Root[Name("/AcroForm")] = pdf.make_indirect(Dictionary(Fields=[widget]))
        pdf.save()

    output = tmp_path / "with_xfa.pdf"
    embed_scratch_xfa(source, output, _specs())

    with pikepdf.Pdf.open(output) as pdf:
        acroform = pdf.Root.get(Name("/AcroForm"))
        assert Name("/XFA") in acroform
        annots = pdf.pages[0].obj.get(Name("/Annots"))
        assert annots is not None and len(annots) == 1


def test_embedded_packet_is_readable_by_the_xfa_editor(tmp_path: Path) -> None:
    source = _write_static_pdf(tmp_path / "static.pdf")
    output = tmp_path / "with_xfa.pdf"
    embed_scratch_xfa(source, output, _specs())

    editor = PdfXfaEditor(output)
    try:
        pages = editor.page_summaries()
        assert [p.name for p in pages] == ["Page1"]
        assert pages[0].field_count == 3
    finally:
        editor.close()


def test_embed_scratch_xfa_rejects_same_input_output(tmp_path: Path) -> None:
    source = _write_static_pdf(tmp_path / "static.pdf")

    try:
        embed_scratch_xfa(source, source, _specs())
        raised = False
    except ValueError:
        raised = True

    assert raised


def test_cli_create_acroform_design_xfa_flag(tmp_path: Path) -> None:
    source = _write_static_pdf(tmp_path / "static.pdf")
    fields_csv = tmp_path / "fields.csv"
    fields_csv.write_text(
        "page,name,type,x,y,w,h,value\n"
        "1,txtFullName,text,130,732,200,18,\n",
        encoding="utf-8",
    )
    output = tmp_path / "out.pdf"

    exit_code = main(
        [
            "create-acroform",
            "--input", str(source),
            "--fields", str(fields_csv),
            "--output", str(output),
            "--design-xfa",
        ]
    )

    assert exit_code == 0
    with pikepdf.Pdf.open(output) as pdf:
        acroform = pdf.Root.get(Name("/AcroForm"))
        assert Name("/XFA") in acroform
        widgets = [
            a for a in (pdf.pages[0].obj.get(Name("/Annots")) or [])
            if str(a.get(Name("/Subtype"))) == "/Widget"
        ]
        assert len(widgets) == 1


def test_cli_create_acroform_without_flag_adds_no_xfa(tmp_path: Path) -> None:
    # Regression guard: the default path must stay exactly as before.
    source = _write_static_pdf(tmp_path / "static.pdf")
    fields_csv = tmp_path / "fields.csv"
    fields_csv.write_text(
        "page,name,type,x,y,w,h,value\n"
        "1,txtFullName,text,130,732,200,18,\n",
        encoding="utf-8",
    )
    output = tmp_path / "out.pdf"

    exit_code = main(
        [
            "create-acroform",
            "--input", str(source),
            "--fields", str(fields_csv),
            "--output", str(output),
        ]
    )

    assert exit_code == 0
    with pikepdf.Pdf.open(output) as pdf:
        acroform = pdf.Root.get(Name("/AcroForm"))
        assert Name("/XFA") not in acroform
