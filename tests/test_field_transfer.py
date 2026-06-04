from __future__ import annotations

from pathlib import Path

import pikepdf
from pikepdf import Array, Dictionary, Name, String

from xdp_form_cli.acroform_builder import create_acroform_pdf
from xdp_form_cli.field_transfer import extract_field_specs_from_pdf, transfer_fields_to_pdf


def _write_blank_pdf(path: Path) -> Path:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(300, 400))
    pdf.save(path)
    return path


def _write_field_specs(path: Path) -> Path:
    path.write_text(
        "\n".join(
            [
                "page,name,type,x,y,w,h,value",
                "1,txtInvestorName,text,20,320,120,18,",
                "1,checkboxConsent,checkbox,25,270,12,12,",
                "1,imgPersonSignature,image,30,220,140,22,",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _write_pdf_with_text_signature_field(path: Path) -> Path:
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=(300, 400))
    signature = pdf.make_indirect(
        Dictionary(
            Type=Name("/Annot"),
            Subtype=Name("/Widget"),
            FT=Name("/Tx"),
            T=String("imgPersonSignature"),
            Rect=Array([30, 220, 170, 242]),
            P=page.obj,
        )
    )
    pdf.Root[Name("/AcroForm")] = Dictionary(Fields=Array([signature]))
    page.obj[Name("/Annots")] = Array([signature])
    pdf.save(path)
    return path


def _write_pdf_with_real_signature_field(path: Path) -> Path:
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=(300, 400))
    signature = pdf.make_indirect(
        Dictionary(
            Type=Name("/Annot"),
            Subtype=Name("/Widget"),
            FT=Name("/Sig"),
            T=String("Signature1"),
            Rect=Array([30, 220, 170, 242]),
            P=page.obj,
        )
    )
    pdf.Root[Name("/AcroForm")] = Dictionary(Fields=Array([signature]))
    page.obj[Name("/Annots")] = Array([signature])
    pdf.save(path)
    return path


def test_extract_field_specs_from_pdf_reads_types_and_rectangles(tmp_path: Path) -> None:
    source = _write_blank_pdf(tmp_path / "fielded_source.pdf")
    fields = _write_field_specs(tmp_path / "fields.csv")
    fielded = tmp_path / "fielded.pdf"
    create_acroform_pdf(source, fields, fielded)

    specs = extract_field_specs_from_pdf(fielded)

    by_name = {spec.name: spec for spec in specs}
    assert set(by_name) == {"txtInvestorName", "checkboxConsent", "imgPersonSignature"}
    assert by_name["txtInvestorName"].field_type == "text"
    assert by_name["txtInvestorName"].x == 20.0
    assert by_name["txtInvestorName"].y == 320.0
    assert by_name["checkboxConsent"].field_type == "checkbox"
    assert by_name["imgPersonSignature"].field_type == "image"


def test_transfer_fields_to_pdf_recreates_template_fields_on_blank_target(tmp_path: Path) -> None:
    template_blank = _write_blank_pdf(tmp_path / "template_blank.pdf")
    template_specs = _write_field_specs(tmp_path / "fields.csv")
    template_filled = tmp_path / "template_with_fields.pdf"
    create_acroform_pdf(template_blank, template_specs, template_filled)

    target_blank = _write_blank_pdf(tmp_path / "target_blank.pdf")
    output_pdf = tmp_path / "target_with_fields.pdf"
    output_csv = tmp_path / "target_fields.csv"

    out_pdf, out_csv, count = transfer_fields_to_pdf(template_filled, target_blank, output_pdf, csv_path=output_csv)

    assert out_pdf == output_pdf
    assert out_csv == output_csv
    assert count == 3
    csv_text = output_csv.read_text(encoding="utf-8")
    assert "imgPersonSignature,image" in csv_text
    assert "checkboxConsent,checkbox" in csv_text

    with pikepdf.Pdf.open(output_pdf) as pdf:
        fields = pdf.Root[Name("/AcroForm")][Name("/Fields")]
        names = {str(field.get(Name("/T"))) for field in fields}
        assert names == {"txtInvestorName", "checkboxConsent", "imgPersonSignature"}


def test_extract_field_specs_normalizes_text_signature_field_to_image(tmp_path: Path) -> None:
    template = _write_pdf_with_text_signature_field(tmp_path / "template_text_signature.pdf")

    specs = extract_field_specs_from_pdf(template)

    assert len(specs) == 1
    assert specs[0].name == "imgPersonSignature"
    assert specs[0].field_type == "image"


def test_transfer_fields_to_pdf_rebuilds_real_sig_template_as_image_pushbutton(tmp_path: Path) -> None:
    template = _write_pdf_with_real_signature_field(tmp_path / "template_real_sig.pdf")
    target = _write_blank_pdf(tmp_path / "target.pdf")
    output = tmp_path / "output.pdf"

    transfer_fields_to_pdf(template, target, output)

    with pikepdf.Pdf.open(output) as pdf:
        field = pdf.Root[Name("/AcroForm")][Name("/Fields")][0]
        assert str(field.get(Name("/T"))) == "Signature1"
        assert field.get(Name("/FT")) == Name("/Btn")
        assert int(field.get(Name("/Ff"), 0)) & 65536
        assert field.get(Name("/H")) == Name("/P")
