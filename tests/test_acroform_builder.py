from __future__ import annotations

from pathlib import Path

import pikepdf
import pytest
from pikepdf import Array, Dictionary, Name, String

from xdp_form_cli.acroform_builder import create_acroform_pdf, load_field_specs
from xdp_form_cli.field_validation import validate_acroform


def _write_field_specs(path: Path, rows: list[str]) -> Path:
    path.write_text(
        "page,name,type,x,y,w,h,value\n" + "\n".join(rows) + "\n",
        encoding="utf-8",
    )
    return path


def _write_blank_pdf(path: Path) -> Path:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    pdf.save(path)
    return path


def _write_pdf_with_real_signature_field(path: Path) -> Path:
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=(200, 200))
    signature = pdf.make_indirect(
        Dictionary(
            Type=Name("/Annot"),
            Subtype=Name("/Widget"),
            FT=Name("/Sig"),
            T=String("sigExisting"),
            Rect=Array([20, 20, 120, 40]),
            P=page.obj,
        )
    )
    pdf.Root[Name("/AcroForm")] = Dictionary(Fields=Array([signature]))
    page.obj[Name("/Annots")] = Array([signature])
    pdf.save(path)
    return path


def test_rejects_real_signature_field_type(tmp_path: Path) -> None:
    specs = _write_field_specs(
        tmp_path / "fields.csv",
        ["1,imgPersonSignature,signature,20,20,100,20,"],
    )

    with pytest.raises(ValueError, match="signature fields must use type=image"):
        load_field_specs(specs)


def test_rejects_img_signature_name_when_not_image_type(tmp_path: Path) -> None:
    specs = _write_field_specs(
        tmp_path / "fields.csv",
        ["1,imgPersonSignature,text,20,20,100,20,"],
    )

    with pytest.raises(ValueError, match="img.*Signature.*type=image"):
        load_field_specs(specs)


def test_accepts_img_signature_name_as_image_type(tmp_path: Path) -> None:
    specs = _write_field_specs(
        tmp_path / "fields.csv",
        ["1,imgPersonSignature,image,20,20,100,20,"],
    )

    loaded = load_field_specs(specs)

    assert loaded[0].name == "imgPersonSignature"
    assert loaded[0].field_type == "image"


def test_create_acroform_rejects_existing_real_signature_fields(tmp_path: Path) -> None:
    source = _write_pdf_with_real_signature_field(tmp_path / "source.pdf")
    specs = _write_field_specs(
        tmp_path / "fields.csv",
        ["1,txtInvestorName,text,20,120,100,20,"],
    )

    with pytest.raises(ValueError, match="real PDF signature field"):
        create_acroform_pdf(source, specs, tmp_path / "output.pdf")


def test_create_acroform_never_writes_real_signature_fields(tmp_path: Path) -> None:
    source = _write_blank_pdf(tmp_path / "source.pdf")
    specs = _write_field_specs(
        tmp_path / "fields.csv",
        ["1,imgPersonSignature,image,20,20,100,20,"],
    )

    output, count = create_acroform_pdf(source, specs, tmp_path / "output.pdf")

    assert count == 1
    with pikepdf.Pdf.open(output) as pdf:
        fields = pdf.Root[Name("/AcroForm")][Name("/Fields")]
        assert len(fields) == 1
        assert fields[0].get(Name("/FT")) == Name("/Btn")
        assert fields[0].get(Name("/Ff")) == 65536


def test_generated_text_fields_are_transparent_images_are_push_buttons_and_checkboxes_have_appearances(tmp_path: Path) -> None:
    source = _write_blank_pdf(tmp_path / "source.pdf")
    specs = _write_field_specs(
        tmp_path / "fields.csv",
        [
            "1,txtInvestorName,text,20,150,100,20,",
            "1,txtNotes,textarea,20,110,100,30,",
            "1,imgPersonSignature,image,20,70,100,20,",
            "1,chkApproved,checkbox,20,40,12,12,1",
        ],
    )

    output, count = create_acroform_pdf(source, specs, tmp_path / "output.pdf")

    assert count == 4
    with pikepdf.Pdf.open(output) as pdf:
        fields = pdf.Root[Name("/AcroForm")][Name("/Fields")]
        assert len(fields) == 4

        transparent_names = {"txtInvestorName", "txtNotes"}
        for field in fields:
            if str(field.get(Name("/T"))) not in transparent_names:
                continue
            assert field.get(Name("/BS"), {}).get(Name("/W")) == 0
            assert list(field.get(Name("/Border"), [])) == [0, 0, 0]
            assert Name("/BG") not in field.get(Name("/MK"), {})

        image_field = next(field for field in fields if str(field.get(Name("/T"))) == "imgPersonSignature")
        assert image_field.get(Name("/FT")) == Name("/Btn")
        assert int(image_field.get(Name("/Ff"), 0)) & 65536
        assert image_field.get(Name("/H")) == Name("/P")
        assert list(image_field.get(Name("/Border"), [])) == [0, 0, 1]
        assert image_field[Name("/BS")].get(Name("/S")) == Name("/B")
        assert float(image_field[Name("/BS")].get(Name("/W"))) > 0
        background = [round(float(value), 3) for value in image_field[Name("/MK")][Name("/BG")]]
        assert background == [round(212 / 255, 3), round(208 / 255, 3), round(200 / 255, 3)]
        assert Name("/BC") in image_field[Name("/MK")]

        checkbox = next(field for field in fields if str(field.get(Name("/T"))) == "chkApproved")
        normal_appearance = checkbox[Name("/AP")][Name("/N")]
        assert Name("/Off") in normal_appearance
        assert Name("/Yes") in normal_appearance


def test_validation_accepts_image_signature_pushbutton_background(tmp_path: Path) -> None:
    source = _write_blank_pdf(tmp_path / "source.pdf")
    specs = _write_field_specs(
        tmp_path / "fields.csv",
        ["1,imgPersonSignature,image,20,20,100,20,"],
    )
    output, _ = create_acroform_pdf(source, specs, tmp_path / "output.pdf")

    with pikepdf.Pdf.open(output, allow_overwriting_input=True) as pdf:
        field = pdf.Root[Name("/AcroForm")][Name("/Fields")][0]
        field[Name("/Border")] = Array([0, 0, 1])
        field[Name("/BS")] = Dictionary(W=1, S=Name("/B"))
        field[Name("/MK")] = Dictionary(
            BG=Array([212 / 255, 208 / 255, 200 / 255]),
            BC=Array([0, 0, 0]),
        )
        field[Name("/H")] = Name("/P")
        pdf.save(output)

    result = validate_acroform(specs, output_pdf=output)

    assert result.errors == []
