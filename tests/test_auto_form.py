from __future__ import annotations

import shutil
from pathlib import Path

import pikepdf
import pytest
from pikepdf import Dictionary, Name

from xdp_form_cli.auto_form import (
    AutoFieldSpec,
    AzureLayoutResult,
    DetectedBox,
    MAX_FIELD_HEIGHT_PT,
    TextAnchor,
    build_auto_client_form,
    build_auto_form,
    detect_field_specs,
    _apply_signature_context_rows,
    _bbox_checkbox_boxes_from_xml,
    _bbox_underline_boxes_from_xml,
    _checkbox_base_name,
    _filter_specs_by_original_content,
    _is_signature_label,
    _matches_signature_context_word,
    _nearest_label,
    write_field_csv,
)
from xdp_form_cli.cli import main
from xdp_form_cli.field_name_resolution import FieldNameResolver


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


def test_signature_label_matches_signature_keyword_anywhere() -> None:
    assert _is_signature_label("Signature") is True
    assert _is_signature_label("Signature date") is True
    assert _is_signature_label("Date signature") is True


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


def _write_tall_box_pdf(path: Path, box_h: float) -> Path:
    """A page with one box of the given height; label is outside the box."""
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(300, 400))
    label_y = 100 + box_h + 5  # label sits above the box
    content = (
        f"BT /F1 10 Tf 50 {label_y} Td (Label) Tj ET\n"
        f"100 100 150 {box_h} re S\n"
    ).encode()
    page = pdf.pages[0]
    page.obj[Name("/Contents")] = pdf.make_stream(content)
    pdf.save(path)
    return path


def test_tall_box_height_is_capped(tmp_path: Path) -> None:
    source = _write_tall_box_pdf(tmp_path / "tall.pdf", box_h=80.0)
    specs = detect_field_specs(source)

    assert len(specs) == 1
    spec = specs[0]
    assert spec.h == MAX_FIELD_HEIGHT_PT
    assert spec.y == 100.0  # anchored to box bottom


def test_short_box_height_is_unchanged(tmp_path: Path) -> None:
    source = _write_tall_box_pdf(tmp_path / "short.pdf", box_h=12.0)
    specs = detect_field_specs(source)

    assert len(specs) == 1
    assert specs[0].h == 12.0


def test_write_field_csv_has_header(tmp_path: Path) -> None:
    source = _write_boxed_pdf(tmp_path / "boxed.pdf")
    specs = detect_field_specs(source)
    csv_path = write_field_csv(specs, tmp_path / "fields.csv")

    header = csv_path.read_text(encoding="utf-8").splitlines()[0]
    assert header == "page,name,type,x,y,w,h,value"


def _write_client_pdf_with_checkbox_and_signature(path: Path) -> Path:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(360, 420))
    content = (
        b"BT /F1 10 Tf 40 360 Td (Investor name) Tj ET\n"
        b"BT /F1 10 Tf 40 300 Td (I approve) Tj ET\n"
        b"BT /F1 10 Tf 40 240 Td (Signature) Tj ET\n"
        b"150 352 160 14 re S\n"
        b"150 296 10 10 re S\n"
        b"150 226 160 24 re S\n"
    )
    page = pdf.pages[0]
    page.obj[Name("/Contents")] = pdf.make_stream(content)
    pdf.save(path)
    return path


def test_build_auto_client_form_detects_text_checkbox_and_image(tmp_path: Path) -> None:
    source = _write_client_pdf_with_checkbox_and_signature(tmp_path / "client.pdf")
    output = tmp_path / "client_acroform.pdf"
    csv_path = tmp_path / "client_fields.csv"

    out_pdf, out_csv, count, summary = build_auto_client_form(source, output, csv_path=csv_path)

    assert out_pdf == output
    assert out_csv == csv_path
    assert count == 3
    assert summary.type_counts == {"checkbox": 1, "image": 1, "text": 1}
    csv_text = csv_path.read_text(encoding="utf-8")
    assert ",checkbox," in csv_text
    assert ",image," in csv_text

    with pikepdf.Pdf.open(output) as pdf:
        fields = pdf.Root[Name("/AcroForm")][Name("/Fields")]
        assert len(fields) == 3
        assert not any(field.get(Name("/FT")) == Name("/Sig") for field in fields)


def test_auto_client_form_cli_writes_pdf_and_csv(tmp_path: Path) -> None:
    source = _write_client_pdf_with_checkbox_and_signature(tmp_path / "client.pdf")
    output = tmp_path / "client_acroform.pdf"
    csv_path = tmp_path / "client_fields.csv"

    exit_code = main([
        "auto-client-form",
        "--input", str(source),
        "--output", str(output),
        "--fields-csv", str(csv_path),
    ])

    assert exit_code == 0
    assert output.is_file()
    assert csv_path.is_file()


def _write_account_owner_pdf(path: Path) -> Path:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(320, 400))
    content = (
        b"BT /F1 10 Tf 40 320 Td (Name Of Account Owner) Tj ET\n"
        b"170 312 120 14 re S\n"
    )
    page = pdf.pages[0]
    page.obj[Name("/Contents")] = pdf.make_stream(content)
    pdf.save(path)
    return path


def test_detect_field_specs_uses_plan_t_resolver_alias(tmp_path: Path) -> None:
    source = _write_account_owner_pdf(tmp_path / "account_owner.pdf")
    resolver = FieldNameResolver(
        {"txtAccountName"},
        aliases={"txtNameOfAccountOwner": "txtAccountName"},
    )

    specs = detect_field_specs(source, field_name_resolver=resolver)

    assert len(specs) == 1
    assert specs[0].name == "txtAccountName"


def _write_account_name_pdf(path: Path) -> Path:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(320, 400))
    content = (
        b"BT /F1 10 Tf 40 320 Td (Account Name) Tj ET\n"
        b"170 312 120 14 re S\n"
    )
    page = pdf.pages[0]
    page.obj[Name("/Contents")] = pdf.make_stream(content)
    pdf.save(path)
    return path


def test_build_auto_client_form_reports_plan_t_match_count(tmp_path: Path) -> None:
    source = _write_account_name_pdf(tmp_path / "account_name.pdf")
    fields_list = tmp_path / "plan_t_fields.csv"
    fields_list.write_text("field_name,prefix\ntxtAccountName,txt\n", encoding="utf-8")

    _output, csv_path, count, summary = build_auto_client_form(
        source,
        tmp_path / "out.pdf",
        fields_list_path=fields_list,
    )

    assert count == 1
    assert "txtAccountName" in csv_path.read_text(encoding="utf-8")
    assert summary.warnings == ("Plan-T field-name resolver matched all 1 field(s).",)


def test_build_auto_client_form_uses_semantic_map_and_writes_report(tmp_path: Path) -> None:
    source = _write_account_owner_pdf(tmp_path / "account_owner.pdf")
    fields_list = tmp_path / "plan_t_fields.csv"
    fields_list.write_text("field_name,prefix\ntxtAccountName,txt\n", encoding="utf-8")
    semantic_map = tmp_path / "semantic.csv"
    semantic_map.write_text("label,field_name\nName Of Account Owner,txtAccountName\n", encoding="utf-8")
    mapping_report = tmp_path / "mapping_report.csv"

    _output, csv_path, count, summary = build_auto_client_form(
        source,
        tmp_path / "out.pdf",
        fields_list_path=fields_list,
        semantic_map_path=semantic_map,
        mapping_report_path=mapping_report,
    )

    assert count == 1
    assert "txtAccountName" in csv_path.read_text(encoding="utf-8")
    report = mapping_report.read_text(encoding="utf-8-sig")
    assert "detected_label" in report
    assert "Name Of Account Owner" in report
    assert "semantic-label-map" in report
    assert summary.warnings == ("Plan-T field-name resolver matched all 1 field(s).",)


def _write_two_unreadable_label_fields_pdf(path: Path) -> Path:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(360, 420))
    content = (
        b"BT /F1 10 Tf 40 360 Td (BBBBBBBBBBBBBBBB) Tj ET\n"
        b"BT /F1 10 Tf 40 300 Td (BBBBBBBBBBBBBBBB) Tj ET\n"
        b"150 352 120 14 re S\n"
        b"150 292 120 14 re S\n"
    )
    page = pdf.pages[0]
    page.obj[Name("/Contents")] = pdf.make_stream(content)
    pdf.save(path)
    return path


def test_build_auto_client_form_semantic_map_can_target_generated_names(tmp_path: Path) -> None:
    source = _write_two_unreadable_label_fields_pdf(tmp_path / "two_fields.pdf")
    fields_list = tmp_path / "plan_t_fields.csv"
    fields_list.write_text("field_name,prefix\ntxtPersonITIN,txt\n", encoding="utf-8")
    semantic_map = tmp_path / "semantic.csv"
    semantic_map.write_text("name,field_name\ntxtField2,txtPersonITIN\n", encoding="utf-8")

    _output, csv_path, count, summary = build_auto_client_form(
        source,
        tmp_path / "out.pdf",
        fields_list_path=fields_list,
        semantic_map_path=semantic_map,
    )

    assert count == 2
    csv_text = csv_path.read_text(encoding="utf-8")
    assert "txtPersonITIN" in csv_text
    assert "txtField," in csv_text
    assert summary.warnings == (
        "Plan-T field-name resolver matched 1/2 field(s); unmatched fields kept generated names and need manual mapping.",
    )


def test_nearest_label_ignores_garbled_local_text_when_azure_label_is_readable() -> None:
    box = DetectedBox(page=1, x=100.0, y=200.0, w=120.0, h=12.0)
    anchors = [
        TextAnchor("\x00\x03\x00¦\x00³\x00\x03\x00", 105.0, 196.0),
        TextAnchor("TIN", 108.0, 195.0),
    ]

    assert _nearest_label(box, anchors) == "TIN"


def _write_line_drawn_checkbox_pdf(path: Path) -> Path:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(300, 400))
    content = (
        b"BT /F1 10 Tf 50 300 Td (Confirm) Tj ET\n"
        b"120 296 m 132 296 l 132 308 l 120 308 l h S\n"
    )
    page = pdf.pages[0]
    page.obj[Name("/Contents")] = pdf.make_stream(content)
    pdf.save(path)
    return path


def test_detect_field_specs_finds_line_drawn_checkboxes(tmp_path: Path) -> None:
    source = _write_line_drawn_checkbox_pdf(tmp_path / "line_checkbox.pdf")

    specs = detect_field_specs(source)

    assert len(specs) == 1
    assert specs[0].field_type == "checkbox"
    assert specs[0].x == 120.0
    assert specs[0].y == 296.0
    assert specs[0].w == 12.0
    assert specs[0].h == 12.0
    assert specs[0].name.startswith("checkbox")


def _write_transformed_line_and_checkbox_pdf(path: Path) -> Path:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(300, 400))
    content = (
        b"BT /F1 10 Tf 50 300 Td (Amount) Tj ET\n"
        b"BT /F1 10 Tf 50 260 Td (Independent) Tj ET\n"
        b"q 0.1 0 0 0.1 120 296 cm 0 0 m 700 0 l S Q\n"
        b"q 0.1 0 0 0.1 120 256 cm 0 0 m 120 0 l 120 120 l 0 120 l h S Q\n"
    )
    page = pdf.pages[0]
    page.obj[Name("/Contents")] = pdf.make_stream(content)
    pdf.save(path)
    return path


def test_detect_field_specs_applies_graphics_transform_to_lines_and_checkboxes(tmp_path: Path) -> None:
    source = _write_transformed_line_and_checkbox_pdf(tmp_path / "transformed.pdf")

    specs = detect_field_specs(source)

    text_fields = [spec for spec in specs if spec.field_type == "text"]
    checkboxes = [spec for spec in specs if spec.field_type == "checkbox"]
    assert len(text_fields) == 1
    assert text_fields[0].x == 120.0
    assert text_fields[0].y == 296.0
    assert text_fields[0].w == 70.0
    assert len(checkboxes) == 1
    assert checkboxes[0].x == 120.0
    assert checkboxes[0].y == 256.0
    assert checkboxes[0].w == 12.0
    assert checkboxes[0].h == 12.0


def _write_glyph_checkbox_pdf(path: Path) -> Path:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(300, 400))
    content = (
        b"BT /F1 10 Tf 50 300 Td (Confirm) Tj ET\n"
        b"BT /TT3 12 Tf 120 296 Td (\000\206) Tj ET\n"
    )
    page = pdf.pages[0]
    page.obj[Name("/Contents")] = pdf.make_stream(content)
    pdf.save(path)
    return path


def test_detect_field_specs_finds_glyph_checkboxes(tmp_path: Path) -> None:
    source = _write_glyph_checkbox_pdf(tmp_path / "glyph_checkbox.pdf")

    specs = detect_field_specs(source)

    assert len(specs) == 1
    assert specs[0].field_type == "checkbox"
    assert specs[0].x == 120.0
    assert specs[0].y == 296.0
    assert specs[0].w == 12.0
    assert specs[0].h == 12.0
    assert specs[0].name.startswith("checkbox")


def test_checkbox_base_name_uses_checkbox_prefix() -> None:
    assert _checkbox_base_name("Insurance confirmation") == "checkboxInsuranceConfirmation"
    assert _checkbox_base_name("BBBBBBBBBBBBBBBB") == "checkboxField"


def test_bbox_checkbox_detection_finds_small_square_next_to_text() -> None:
    xml = """
    <page width="420.000000" height="400.000000">
      <word xMin="100.000000" yMin="100.000000" xMax="250.000000" yMax="112.000000">Insurance</word>
      <word xMin="270.000000" yMin="98.000000" xMax="286.000000" yMax="114.000000">\u25a1</word>
    </page>
    """

    boxes = _bbox_checkbox_boxes_from_xml(xml)

    assert list(boxes) == [1]
    assert len(boxes[1]) == 1
    assert boxes[1][0].x == 270.0
    assert boxes[1][0].y == 286.0
    assert boxes[1][0].w == 16.0
    assert boxes[1][0].h == 16.0


def test_bbox_checkbox_detection_ignores_square_without_text_context() -> None:
    xml = """
    <page width="420.000000" height="400.000000">
      <word xMin="270.000000" yMin="98.000000" xMax="286.000000" yMax="114.000000">\u25a1</word>
    </page>
    """

    boxes = _bbox_checkbox_boxes_from_xml(xml)

    assert boxes == {}


def _write_underlined_heading_pdf(path: Path) -> Path:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(300, 400))
    content = (
        b"BT /F1 12 Tf 100 350 Td (Governing Law) Tj ET\n"
        b"80 320 160 0.8 re S\n"
    )
    page = pdf.pages[0]
    page.obj[Name("/Contents")] = pdf.make_stream(content)
    pdf.save(path)
    return path


def test_detect_field_specs_ignores_underlined_headings(tmp_path: Path) -> None:
    source = _write_underlined_heading_pdf(tmp_path / "heading.pdf")

    specs = detect_field_specs(source)

    assert specs == []


def _write_table_row_separator_pdf(path: Path) -> Path:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(360, 420))
    content = (
        b"80 300 240 0.8 re S\n"
        b"BT /F1 10 Tf 90 292 Td (Long table content, not a field label) Tj ET\n"
        b"BT /F1 10 Tf 90 280 Td (More paragraph content below the row separator) Tj ET\n"
    )
    page = pdf.pages[0]
    page.obj[Name("/Contents")] = pdf.make_stream(content)
    pdf.save(path)
    return path


def test_detect_field_specs_ignores_table_row_separators_with_content_below(tmp_path: Path) -> None:
    source = _write_table_row_separator_pdf(tmp_path / "table_row.pdf")

    specs = detect_field_specs(source)

    assert specs == []


def _write_full_width_separator_pdf(path: Path) -> Path:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(595, 420))
    content = (
        b"40 300 510 0.8 re S\n"
        b"BT /F1 10 Tf 220 288 Td (Section note) Tj ET\n"
    )
    page = pdf.pages[0]
    page.obj[Name("/Contents")] = pdf.make_stream(content)
    pdf.save(path)
    return path


def test_detect_field_specs_ignores_full_width_section_separators(tmp_path: Path) -> None:
    source = _write_full_width_separator_pdf(tmp_path / "full_width_separator.pdf")

    specs = detect_field_specs(source)

    assert specs == []


def _write_table_cell_with_adjacent_column_text_pdf(path: Path) -> Path:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(380, 420))
    content = (
        b"80 300 240 0.8 re S\n"
        b"BT /F1 10 Tf 330 312 Td (Adjacent) Tj ET\n"
        b"BT /F1 10 Tf 330 304 Td (column) Tj ET\n"
        b"BT /F1 10 Tf 330 296 Td (content) Tj ET\n"
        b"BT /F1 10 Tf 330 288 Td (not label) Tj ET\n"
    )
    page = pdf.pages[0]
    page.obj[Name("/Contents")] = pdf.make_stream(content)
    pdf.save(path)
    return path


def test_detect_field_specs_ignores_table_cells_with_busy_adjacent_text(tmp_path: Path) -> None:
    source = _write_table_cell_with_adjacent_column_text_pdf(tmp_path / "table_cell.pdf")

    specs = detect_field_specs(source)

    assert specs == []


def _write_short_blank_cell_with_label_above_pdf(path: Path) -> Path:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(360, 420))
    content = (
        b"BT /F1 10 Tf 105 318 Td (Management fee %) Tj ET\n"
        b"90 300 130 12 re S\n"
    )
    page = pdf.pages[0]
    page.obj[Name("/Contents")] = pdf.make_stream(content)
    pdf.save(path)
    return path


def test_detect_field_specs_keeps_short_blank_cell_with_label_immediately_above(tmp_path: Path) -> None:
    source = _write_short_blank_cell_with_label_above_pdf(tmp_path / "label_above_cell.pdf")

    specs = detect_field_specs(source)

    assert [(spec.x, spec.y, spec.w, spec.h) for spec in specs] == [(90.0, 300.0, 130.0, 12.0)]


def _write_blank_table_input_column_pdf(path: Path) -> Path:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(420, 420))
    content = (
        b"BT /F1 10 Tf 60 350 Td (Allocation %) Tj ET\n"
        b"BT /F1 10 Tf 170 350 Td (Investment route) Tj ET\n"
        b"BT /F1 10 Tf 315 350 Td (Code) Tj ET\n"
        b"40 320 110 18 re S 150 320 150 18 re S 300 320 60 18 re S\n"
        b"40 302 110 18 re S 150 302 150 18 re S 300 302 60 18 re S\n"
        b"40.4 302 110 18 re S\n"
        b"40 284 110 18 re S 150 284 150 18 re S 300 284 60 18 re S\n"
        b"BT /F1 10 Tf 170 326 Td (General track) Tj ET\n"
        b"BT /F1 10 Tf 315 326 Td (962) Tj ET\n"
        b"BT /F1 10 Tf 170 308 Td (Shares track) Tj ET\n"
        b"BT /F1 10 Tf 315 308 Td (963) Tj ET\n"
        b"BT /F1 10 Tf 170 290 Td (Bond track) Tj ET\n"
        b"BT /F1 10 Tf 315 290 Td (972) Tj ET\n"
    )
    page = pdf.pages[0]
    page.obj[Name("/Contents")] = pdf.make_stream(content)
    pdf.save(path)
    return path


def test_detect_field_specs_keeps_blank_repeating_table_input_column(tmp_path: Path) -> None:
    source = _write_blank_table_input_column_pdf(tmp_path / "blank_table_column.pdf")

    specs = detect_field_specs(source)

    text_fields = [spec for spec in specs if spec.field_type == "text"]
    assert [(field.x, field.y, field.w, field.h) for field in text_fields] == [
        (40.0, 320.0, 110.0, 18.0),
        (40.0, 302.0, 110.0, 18.0),
        (40.0, 284.0, 110.0, 18.0),
    ]


def _write_same_line_underline_pdf(path: Path, label_before: bool) -> Path:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(300, 400))
    if label_before:
        content = (
            b"BT /F1 10 Tf 40 300 Td (Name:) Tj ET\n"
            b"90 297 120 0.8 re S\n"
        )
    else:
        content = (
            b"40 297 120 0.8 re S\n"
            b"BT /F1 10 Tf 170 300 Td (Number) Tj ET\n"
        )
    page = pdf.pages[0]
    page.obj[Name("/Contents")] = pdf.make_stream(content)
    pdf.save(path)
    return path


def test_detect_field_specs_keeps_underlines_with_label_before(tmp_path: Path) -> None:
    source = _write_same_line_underline_pdf(tmp_path / "before.pdf", label_before=True)

    specs = detect_field_specs(source)

    assert len(specs) == 1
    assert specs[0].field_type == "text"
    assert specs[0].x == 90.0
    assert specs[0].y == 297.0
    assert specs[0].w == 120.0
    assert specs[0].h == 12.0


def test_detect_field_specs_keeps_underlines_with_label_after(tmp_path: Path) -> None:
    source = _write_same_line_underline_pdf(tmp_path / "after.pdf", label_before=False)

    specs = detect_field_specs(source)

    assert len(specs) == 1
    assert specs[0].field_type == "text"
    assert specs[0].x == 40.0
    assert specs[0].y == 297.0
    assert specs[0].w == 120.0


def _write_below_label_underline_pdf(path: Path) -> Path:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(300, 400))
    content = (
        b"80 300 160 0.8 re S\n"
        b"BT /F1 10 Tf 125 286 Td (Signature) Tj ET\n"
    )
    page = pdf.pages[0]
    page.obj[Name("/Contents")] = pdf.make_stream(content)
    pdf.save(path)
    return path


def test_detect_field_specs_keeps_underlines_with_label_below(tmp_path: Path) -> None:
    source = _write_below_label_underline_pdf(tmp_path / "below.pdf")

    specs = detect_field_specs(source)

    assert len(specs) == 1
    assert specs[0].field_type == "image"
    assert specs[0].x == 80.0
    assert specs[0].y == 300.0
    assert specs[0].w == 160.0


def _write_long_below_label_underline_pdf(path: Path) -> Path:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(420, 400))
    content = (
        b"90 300 220 0.8 re S\n"
        b"BT /F1 10 Tf 95 292 Td (Commitment amount approved by the authorized general partner:) Tj ET\n"
    )
    page = pdf.pages[0]
    page.obj[Name("/Contents")] = pdf.make_stream(content)
    pdf.save(path)
    return path


def test_detect_field_specs_keeps_underlines_with_long_single_line_label_below(tmp_path: Path) -> None:
    source = _write_long_below_label_underline_pdf(tmp_path / "long_below.pdf")

    specs = detect_field_specs(source)

    assert len(specs) == 1
    assert specs[0].x == 90.0
    assert specs[0].y == 300.0
    assert specs[0].w == 220.0


def _write_scaled_tm_below_label_underline_pdf(path: Path) -> Path:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(420, 400))
    content = (
        b"90 300 220 0.8 re S\n"
        b"BT /F1 1 Tf 10 0 0 10 0 0 Tm 9.5 29.2 Td (Commitment amount approved by the authorized general partner:) Tj ET\n"
    )
    page = pdf.pages[0]
    page.obj[Name("/Contents")] = pdf.make_stream(content)
    pdf.save(path)
    return path


def test_detect_field_specs_uses_scaled_text_matrix_for_below_labels(tmp_path: Path) -> None:
    source = _write_scaled_tm_below_label_underline_pdf(tmp_path / "scaled_below.pdf")

    specs = detect_field_specs(source)

    assert len(specs) == 1
    assert specs[0].x == 90.0
    assert specs[0].y == 300.0
    assert specs[0].w == 220.0


def _write_section_number_below_underline_pdf(path: Path) -> Path:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(420, 400))
    content = (
        b"90 300 120 0.8 re S\n"
        b"BT /F1 10 Tf 160 292 Td (2.1) Tj ET\n"
    )
    page = pdf.pages[0]
    page.obj[Name("/Contents")] = pdf.make_stream(content)
    pdf.save(path)
    return path


def test_detect_field_specs_does_not_treat_section_numbers_as_labels(tmp_path: Path) -> None:
    source = _write_section_number_below_underline_pdf(tmp_path / "section_number.pdf")

    specs = detect_field_specs(source)

    assert specs == []


def _write_dark_content_overlap_pdf(path: Path) -> Path:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(420, 400))
    content = b"0 g 100 303 30 4 re f\n"
    page = pdf.pages[0]
    page.obj[Name("/Contents")] = pdf.make_stream(content)
    pdf.save(path)
    return path


def test_filter_specs_by_original_content_drops_fields_over_dark_content(tmp_path: Path) -> None:
    if shutil.which("pdftoppm") is None:
        pytest.skip("pdftoppm is not installed")
    pytest.importorskip("PIL")
    source = _write_dark_content_overlap_pdf(tmp_path / "dark_overlap.pdf")
    bad = AutoFieldSpec(page=1, name="txtBad", field_type="text", x=90.0, y=300.0, w=120.0, h=12.0)
    good = AutoFieldSpec(page=1, name="txtGood", field_type="text", x=90.0, y=250.0, w=120.0, h=12.0)

    filtered = _filter_specs_by_original_content(source, [bad, good])

    assert filtered == [good]


def _write_neighboring_underlines_pdf(path: Path) -> Path:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(360, 420))
    content = (
        b"80 300 100 0.8 re S\n"
        b"220 300 100 0.8 re S\n"
        b"BT /F1 10 Tf 245 286 Td (Name) Tj ET\n"
    )
    page = pdf.pages[0]
    page.obj[Name("/Contents")] = pdf.make_stream(content)
    pdf.save(path)
    return path


def test_detect_field_specs_keeps_neighboring_blank_lines_when_one_has_label(tmp_path: Path) -> None:
    source = _write_neighboring_underlines_pdf(tmp_path / "neighboring.pdf")

    specs = detect_field_specs(source)

    assert len(specs) == 2
    assert {spec.x for spec in specs} == {80.0, 220.0}


def _write_horizontal_line_field_pdf(path: Path) -> Path:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(300, 400))
    content = (
        b"BT /F1 10 Tf 40 300 Td (Email:) Tj ET\n"
        b"90 297 m 210 297 l S\n"
    )
    page = pdf.pages[0]
    page.obj[Name("/Contents")] = pdf.make_stream(content)
    pdf.save(path)
    return path


def test_detect_field_specs_finds_horizontal_line_fields(tmp_path: Path) -> None:
    source = _write_horizontal_line_field_pdf(tmp_path / "horizontal_line.pdf")

    specs = detect_field_specs(source)

    assert len(specs) == 1
    assert specs[0].field_type == "text"
    assert specs[0].x == 90.0
    assert specs[0].y == 297.0
    assert specs[0].w == 120.0
    assert specs[0].h == 12.0


def _write_dot_prefixed_underscore_text_pdf(path: Path) -> Path:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(300, 400))
    content = b"BT /F1 10 Tf 120 300 Td (._______________) Tj ET\n"
    page = pdf.pages[0]
    page.obj[Name("/Contents")] = pdf.make_stream(content)
    pdf.save(path)
    return path


def test_detect_field_specs_ignores_dot_prefixed_underscore_text(tmp_path: Path) -> None:
    source = _write_dot_prefixed_underscore_text_pdf(tmp_path / "dot_underscore.pdf")

    specs = detect_field_specs(source)

    assert specs == []


def _write_two_underscore_fields_in_one_text_op_pdf(path: Path) -> Path:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(420, 400))
    content = (
        b"BT /F1 10 Tf 40 300 Td (Name ____________ Phone ____________) Tj ET\n"
    )
    page = pdf.pages[0]
    page.obj[Name("/Contents")] = pdf.make_stream(content)
    pdf.save(path)
    return path


def test_detect_field_specs_finds_multiple_underscore_fields_on_same_text_line(tmp_path: Path) -> None:
    source = _write_two_underscore_fields_in_one_text_op_pdf(tmp_path / "two_fields.pdf")

    specs = detect_field_specs(source)

    assert len(specs) == 2
    assert [spec.field_type for spec in specs] == ["text", "text"]
    assert [spec.h for spec in specs] == [12.0, 12.0]
    assert specs[0].x < specs[1].x
    assert specs[0].w == specs[1].w


def _write_symbol_bounded_underscore_text_pdf(path: Path, text: bytes) -> Path:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(420, 400))
    page = pdf.pages[0]
    page.obj[Name("/Contents")] = pdf.make_stream(
        b"BT /F1 10 Tf 40 300 Td (" + text + b") Tj ET\n"
    )
    pdf.save(path)
    return path


def test_detect_field_specs_keeps_underscore_field_between_symbol_and_text(tmp_path: Path) -> None:
    source = _write_symbol_bounded_underscore_text_pdf(
        tmp_path / "symbol_before.pdf",
        b"%____________________Amount",
    )

    specs = detect_field_specs(source)

    assert len(specs) == 1
    assert specs[0].field_type == "text"
    assert specs[0].x > 40
    assert specs[0].w >= 100


def test_detect_field_specs_keeps_underscore_field_between_text_and_symbol(tmp_path: Path) -> None:
    source = _write_symbol_bounded_underscore_text_pdf(
        tmp_path / "symbol_after.pdf",
        b"Amount____________________%",
    )

    specs = detect_field_specs(source)

    assert len(specs) == 1
    assert specs[0].field_type == "text"
    assert specs[0].x > 60
    assert specs[0].w >= 100


def test_bbox_underline_detection_keeps_field_between_symbol_and_text() -> None:
    xml = """
    <page width="420.000000" height="400.000000">
      <word xMin="100.000000" yMin="100.000000" xMax="110.000000" yMax="112.000000">%</word>
      <word xMin="110.000000" yMin="100.000000" xMax="170.000000" yMax="112.000000">________</word>
      <word xMin="178.000000" yMin="100.000000" xMax="230.000000" yMax="112.000000">Amount</word>
    </page>
    """

    boxes = _bbox_underline_boxes_from_xml(xml)

    assert list(boxes) == [1]
    assert len(boxes[1]) == 1
    assert boxes[1][0].x == 110.0
    assert boxes[1][0].y == 288.0
    assert boxes[1][0].w == 60.0


def test_bbox_underline_detection_keeps_field_between_text_and_symbol() -> None:
    xml = """
    <page width="420.000000" height="400.000000">
      <word xMin="100.000000" yMin="100.000000" xMax="220.000000" yMax="112.000000">Amount________%</word>
    </page>
    """

    boxes = _bbox_underline_boxes_from_xml(xml)

    assert len(boxes[1]) == 1
    assert boxes[1][0].x > 130.0
    assert boxes[1][0].w >= 60.0


def test_bbox_underline_detection_ignores_contextless_dot_prefixed_line() -> None:
    xml = """
    <page width="420.000000" height="400.000000">
      <word xMin="100.000000" yMin="100.000000" xMax="190.000000" yMax="112.000000">._______________</word>
    </page>
    """

    boxes = _bbox_underline_boxes_from_xml(xml)

    assert boxes == {}


def _write_cid_repeated_glyph_underline_pdf(path: Path) -> Path:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(420, 400))
    page = pdf.pages[0]
    descendant = Dictionary(
        Type=Name("/Font"),
        Subtype=Name("/CIDFontType2"),
        DW=500,
        W=[65, [500], 66, [500]],
    )
    font = Dictionary(
        Type=Name("/Font"),
        Subtype=Name("/Type0"),
        BaseFont=Name("/TestCID"),
        DescendantFonts=[descendant],
    )
    page.obj[Name("/Resources")] = Dictionary(Font=Dictionary(TT2=font))
    content = b"BT /TT2 1 Tf 10 0 0 10 40 300 Tm <0041004200420042004200420042004200420042004200420042> Tj ET\n"
    page.obj[Name("/Contents")] = pdf.make_stream(content)
    pdf.save(path)
    return path


def test_detect_field_specs_finds_cid_repeated_glyph_underlines(tmp_path: Path) -> None:
    source = _write_cid_repeated_glyph_underline_pdf(tmp_path / "cid_repeated.pdf")

    specs = detect_field_specs(source)

    assert len(specs) == 1
    assert specs[0].field_type == "text"
    assert specs[0].x == 45.0
    assert specs[0].y == 300.0
    assert specs[0].w == 60.0
    assert specs[0].h == 12.0


def _write_label_next_to_cid_repeated_glyph_underline_pdf(path: Path) -> Path:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(420, 400))
    page = pdf.pages[0]
    descendant = Dictionary(
        Type=Name("/Font"),
        Subtype=Name("/CIDFontType2"),
        DW=500,
        W=[66, [500]],
    )
    cid_font = Dictionary(
        Type=Name("/Font"),
        Subtype=Name("/Type0"),
        BaseFont=Name("/TestCID"),
        DescendantFonts=[descendant],
    )
    latin_font = Dictionary(
        Type=Name("/Font"),
        Subtype=Name("/Type1"),
        BaseFont=Name("/Helvetica"),
    )
    page.obj[Name("/Resources")] = Dictionary(Font=Dictionary(F1=latin_font, TT2=cid_font))
    content = (
        b"BT /F1 10 Tf 60 300 Td (By :) Tj ET\n"
        b"BT /TT2 1 Tf 10 0 0 10 100 300 Tm <004200420042004200420042004200420042004200420042> Tj ET\n"
    )
    page.obj[Name("/Contents")] = pdf.make_stream(content)
    pdf.save(path)
    return path


def test_detect_field_specs_names_cid_repeated_glyph_field_from_real_label(tmp_path: Path) -> None:
    source = _write_label_next_to_cid_repeated_glyph_underline_pdf(tmp_path / "cid_label.pdf")

    specs = detect_field_specs(source)

    assert len(specs) == 1
    assert specs[0].name == "txtBy"


def test_repeated_glyph_noise_is_not_used_as_field_name() -> None:
    source_name = "BBBBBBBBBBBBBBBB"

    from xdp_form_cli.auto_form import _field_base_name

    assert _field_base_name(source_name, is_signature=False) == "txtField"


def test_matches_signature_context_word_in_reversed_hebrew() -> None:
    assert _matches_signature_context_word("\u05dd\u05d5\u05ea\u05d7\u05d4") is True
    assert _matches_signature_context_word("\u05d4\u05de\u05d9\u05ea\u05d7") is True
    assert _matches_signature_context_word("\u05d7\u05d5\u05e7\u05dc\u05d4") is False


def test_apply_signature_context_rows_marks_only_closest_row_below_context_as_images() -> None:
    specs = [
        AutoFieldSpec(page=11, name="txtLeft", field_type="text", x=100.0, y=280.0, w=120.0, h=12.0),
        AutoFieldSpec(page=11, name="txtRight", field_type="text", x=380.0, y=280.0, w=120.0, h=12.0),
        AutoFieldSpec(page=11, name="txtName", field_type="text", x=230.0, y=268.0, w=170.0, h=12.0),
        AutoFieldSpec(page=12, name="txtOther", field_type="text", x=100.0, y=280.0, w=120.0, h=12.0),
    ]

    updated = _apply_signature_context_rows(specs, [(11, 336.0)])

    by_name = {spec.name: spec for spec in updated}
    assert by_name["imgLeft"].field_type == "image"
    assert by_name["imgRight"].field_type == "image"
    assert by_name["txtName"].field_type == "text"
    assert by_name["txtOther"].field_type == "text"


def test_apply_signature_context_rows_does_not_convert_labeled_amount_fields_to_images() -> None:
    specs = [
        AutoFieldSpec(
            page=19,
            name="txtAmount",
            field_type="text",
            x=80.0,
            y=280.0,
            w=160.0,
            h=12.0,
            label="Amount:",
            name_match_method="exact",
            name_matched_plan_t=True,
        ),
        AutoFieldSpec(
            page=19,
            name="txtDate",
            field_type="text",
            x=270.0,
            y=280.0,
            w=120.0,
            h=12.0,
            label="Date:",
            name_match_method="exact",
            name_matched_plan_t=True,
        ),
    ]

    updated = _apply_signature_context_rows(specs, [(19, 336.0)])

    assert [(spec.name, spec.field_type) for spec in updated] == [
        ("txtAmount", "text"),
        ("txtDate", "text"),
    ]
    assert all(spec.name_matched_plan_t for spec in updated)


def _write_unlabeled_horizontal_line_pdf(path: Path) -> Path:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(300, 400))
    page = pdf.pages[0]
    page.obj[Name("/Contents")] = pdf.make_stream(b"120 250 m 240 250 l S\n")
    pdf.save(path)
    return path


def test_azure_anchor_keeps_line_when_pdf_text_is_not_decodable(tmp_path: Path) -> None:
    source = _write_unlabeled_horizontal_line_pdf(tmp_path / "azure_label.pdf")
    layout = AzureLayoutResult(
        words_by_page={},
        anchors_by_page={1: [TextAnchor("Approved commitment amount", 122.0, 232.0)]},
        checkbox_boxes_by_page={},
    )

    specs = detect_field_specs(source, azure_layout=layout)

    assert len(specs) == 1
    assert specs[0].field_type == "text"
    assert round(specs[0].x) == 120
    assert round(specs[0].w) == 120


def test_azure_selection_mark_is_used_as_checkbox(tmp_path: Path) -> None:
    source = _write_unlabeled_horizontal_line_pdf(tmp_path / "azure_checkbox.pdf")
    layout = AzureLayoutResult(
        words_by_page={},
        anchors_by_page={1: [TextAnchor("I approve", 80.0, 96.0)]},
        checkbox_boxes_by_page={1: [DetectedBox(page=1, x=50.0, y=92.0, w=11.0, h=11.0)]},
    )

    specs = detect_field_specs(source, azure_layout=layout)

    checkboxes = [spec for spec in specs if spec.field_type == "checkbox"]
    assert len(checkboxes) == 1
    assert checkboxes[0].name.startswith("checkbox")


def test_build_auto_client_form_warns_when_azure_credentials_are_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_DOCUMENT_INTELLIGENCE_KEY", raising=False)
    source = _write_boxed_pdf(tmp_path / "boxed.pdf")

    _output, _csv, count, summary = build_auto_client_form(
        source,
        tmp_path / "out.pdf",
        use_azure_document_intelligence=True,
    )

    assert count == 2
    assert any("Azure Document Intelligence skipped" in warning for warning in summary.warnings)
