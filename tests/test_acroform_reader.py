from __future__ import annotations

from pathlib import Path

import pikepdf
import pytest

from xdp_form_cli.acroform_builder import create_acroform_pdf
from xdp_form_cli.acroform_reader import PdfAcroFormEditor


def _write_field_specs(path: Path, rows: list[str]) -> Path:
    path.write_text(
        "page,name,type,x,y,w,h,value\n" + "\n".join(rows) + "\n",
        encoding="utf-8",
    )
    return path


def _make_static_pdf_with_fields(tmp_path: Path) -> Path:
    blank = tmp_path / "blank.pdf"
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(300, 300))
    pdf.add_blank_page(page_size=(300, 300))
    pdf.save(blank)

    fields = _write_field_specs(
        tmp_path / "fields.csv",
        [
            "1,txtName,text,50,200,120,14,",
            "1,chkAgree,checkbox,50,160,12,12,",
            "2,txtNotes,textarea,50,100,150,40,",
        ],
    )
    output = tmp_path / "with_fields.pdf"
    create_acroform_pdf(blank, fields, output)
    return output


def test_reader_reports_pages_and_field_counts(tmp_path: Path) -> None:
    pdf_path = _make_static_pdf_with_fields(tmp_path)
    editor = PdfAcroFormEditor(pdf_path)
    try:
        summaries = editor.page_summaries()
        assert [s.name for s in summaries] == ["Page1", "Page2"]
        assert summaries[0].field_count == 2
        assert summaries[1].field_count == 1
    finally:
        editor.close()


def test_reader_lists_field_names_for_page(tmp_path: Path) -> None:
    pdf_path = _make_static_pdf_with_fields(tmp_path)
    editor = PdfAcroFormEditor(pdf_path)
    try:
        assert sorted(editor.field_names("Page1")) == ["chkAgree", "txtName"]
        assert editor.field_names("Page2") == ["txtNotes"]
    finally:
        editor.close()


def test_reader_rejects_invalid_page_name(tmp_path: Path) -> None:
    pdf_path = _make_static_pdf_with_fields(tmp_path)
    editor = PdfAcroFormEditor(pdf_path)
    try:
        with pytest.raises(ValueError):
            editor.field_names("Page99")
    finally:
        editor.close()


def test_reader_converts_field_names(tmp_path: Path) -> None:
    pdf_path = _make_static_pdf_with_fields(tmp_path)
    editor = PdfAcroFormEditor(pdf_path)

    def matcher(name: str):
        from xdp_form_cli.field_truth import FieldMatch

        canonical = "renamed_" + name
        return FieldMatch(
            original_name=name,
            canonical_name=canonical,
            matched=True,
            changed=True,
            method="test",
        )

    try:
        matches = editor.convert_field_names(matcher)
        assert len(matches) == 3
        out = editor.save_copy(tmp_path / "renamed.pdf")
    finally:
        editor.close()

    reread = PdfAcroFormEditor(out)
    try:
        all_names = sorted(
            name for page in reread.page_summaries() for name in reread.field_names(page.name)
        )
        assert all_names == ["renamed_chkAgree", "renamed_txtName", "renamed_txtNotes"]
    finally:
        reread.close()
