from __future__ import annotations

from pathlib import Path

import pikepdf
from pikepdf import Dictionary, Name

from xdp_form_cli.auto_form import (
    build_auto_form,
    detect_field_specs,
    write_field_csv,
)


def _write_boxed_pdf(path: Path) -> Path:
    """A page with two input boxes; one is labelled 'Signature'."""
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(300, 400))
    content = (
        b"BT /F1 10 Tf 50 350 Td (Full Name) Tj ET\n"
        b"BT /F1 10 Tf 50 250 Td (Signature) Tj ET\n"
        b"100 340 150 20 re S\n"
        b"100 240 150 20 re S\n"
    )
    page = pdf.pages[0]
    page.obj[Name("/Contents")] = pdf.make_stream(content)
    pdf.save(path)
    return path


def test_detect_field_specs_finds_boxes(tmp_path: Path) -> None:
    source = _write_boxed_pdf(tmp_path / "boxed.pdf")
    specs = detect_field_specs(source)

    assert len(specs) == 2
    assert {round(s.y) for s in specs} == {240, 340}


def test_detect_field_specs_marks_signature_as_image(tmp_path: Path) -> None:
    source = _write_boxed_pdf(tmp_path / "boxed.pdf")
    specs = detect_field_specs(source)

    by_y = {round(s.y): s for s in specs}
    assert by_y[240].field_type == "image"
    assert by_y[340].field_type == "text"


def test_build_auto_form_strips_xfa_and_places_fields(tmp_path: Path) -> None:
    source = _write_boxed_pdf(tmp_path / "boxed.pdf")
    output = tmp_path / "out.pdf"

    out_pdf, csv_path, count = build_auto_form(source, output)

    assert out_pdf == output
    assert csv_path.is_file()
    assert count == 2

    with pikepdf.Pdf.open(str(out_pdf)) as pdf:
        acroform = pdf.Root.get(Name("/AcroForm"))
        assert Name("/XFA") not in acroform
        widgets = [
            a
            for pg in pdf.pages
            for a in (pg.obj.get(Name("/Annots")) or [])
            if isinstance(a, Dictionary) and str(a.get(Name("/Subtype"))) == "/Widget"
        ]
        assert len(widgets) == 2


def test_write_field_csv_has_header(tmp_path: Path) -> None:
    source = _write_boxed_pdf(tmp_path / "boxed.pdf")
    specs = detect_field_specs(source)
    csv_path = write_field_csv(specs, tmp_path / "fields.csv")

    header = csv_path.read_text(encoding="utf-8").splitlines()[0]
    assert header == "page,name,type,x,y,w,h,value"
