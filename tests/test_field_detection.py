from __future__ import annotations

import csv
from pathlib import Path

import pikepdf
from pikepdf import Name

from xdp_form_cli.cli import main
from xdp_form_cli.field_detection import detect_fields, write_detected_csv


def _write_pdf(path: Path, content: bytes, page_size=(300, 400)) -> Path:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=page_size)
    page = pdf.pages[0]
    page.obj[Name("/Contents")] = pdf.make_stream(content)
    pdf.save(path)
    return path


def test_detects_boxes_and_names_them_from_labels(tmp_path: Path) -> None:
    source = _write_pdf(
        tmp_path / "boxed.pdf",
        b"BT /F1 10 Tf 50 350 Td (Full Name) Tj ET\n"
        b"BT /F1 10 Tf 50 250 Td (Signature) Tj ET\n"
        b"100 340 150 20 re S\n"
        b"100 240 150 20 re S\n",
    )

    fields = detect_fields(source)

    by_name = {f.name: f for f in fields}
    assert "txtFullName" in by_name
    assert "imgSignature" in by_name
    assert by_name["imgSignature"].field_type == "image"


def test_label_with_colon_and_no_box_synthesizes_field(tmp_path: Path) -> None:
    source = _write_pdf(
        tmp_path / "colon.pdf",
        b"BT /F1 10 Tf 50 350 Td (Name:) Tj ET\n",
    )

    fields = detect_fields(source)

    assert len(fields) == 1
    field = fields[0]
    assert field.field_type == "text"
    # Field sits to the right of the label, on the same line.
    assert field.x > 50
    assert abs(field.y - 350) < 10


def test_label_without_colon_and_no_box_yields_nothing(tmp_path: Path) -> None:
    source = _write_pdf(
        tmp_path / "plain.pdf",
        b"BT /F1 10 Tf 50 350 Td (Just a paragraph of text) Tj ET\n",
    )

    fields = detect_fields(source)

    assert fields == []


def test_no_synthesis_when_box_already_next_to_label(tmp_path: Path) -> None:
    source = _write_pdf(
        tmp_path / "boxed_colon.pdf",
        b"BT /F1 10 Tf 50 350 Td (Name:) Tj ET\n"
        b"100 345 150 18 re S\n",
    )

    fields = detect_fields(source)

    # Only the drawn box, no extra synthesized duplicate.
    assert len(fields) == 1
    assert fields[0].w == 150.0


def test_date_label_resolves_to_plan_t_name_when_known(tmp_path: Path) -> None:
    source = _write_pdf(
        tmp_path / "date.pdf",
        b"BT /F1 10 Tf 50 350 Td (Date) Tj ET\n"
        b"100 340 150 20 re S\n",
    )

    fields = detect_fields(source)

    assert len(fields) == 1
    # "Date" matches the canonical Plan-T field, which wins over the dt prefix.
    assert fields[0].name == "txtDate"
    # CSV output type must stay within supported AcroForm types.
    assert fields[0].field_type == "text"


def test_date_label_keeps_dt_prefix_without_plan_t_names(tmp_path: Path) -> None:
    source = _write_pdf(
        tmp_path / "date.pdf",
        b"BT /F1 10 Tf 50 350 Td (Date) Tj ET\n"
        b"100 340 150 20 re S\n",
    )

    fields = detect_fields(source, use_plan_t_names=False)

    assert len(fields) == 1
    assert fields[0].name == "dtDate"
    assert fields[0].field_type == "text"


def test_alignment_clustering_snaps_close_x_values(tmp_path: Path) -> None:
    source = _write_pdf(
        tmp_path / "snap.pdf",
        b"BT /F1 10 Tf 50 350 Td (First) Tj ET\n"
        b"BT /F1 10 Tf 50 250 Td (Second) Tj ET\n"
        b"100 340 150 20 re S\n"
        b"102 240 150 20 re S\n",
    )

    fields = detect_fields(source)

    assert len(fields) == 2
    assert fields[0].x == fields[1].x


def test_write_detected_csv_round_trips(tmp_path: Path) -> None:
    source = _write_pdf(
        tmp_path / "boxed.pdf",
        b"BT /F1 10 Tf 50 350 Td (Full Name) Tj ET\n"
        b"100 340 150 20 re S\n",
    )
    fields = detect_fields(source)
    out = write_detected_csv(fields, tmp_path / "fields.csv")

    with out.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == 1
    assert rows[0]["name"] == "txtFullName"
    assert rows[0]["type"] == "text"
    assert float(rows[0]["w"]) == 150.0


def test_hebrew_label_resolves_to_plan_t_name(tmp_path: Path) -> None:
    # "שם סוכן" in cp1255 bytes; the packaged semantic map maps it to txtAgentName.
    source = _write_pdf(
        tmp_path / "agent.pdf",
        b"BT /F1 10 Tf 50 350 Td (\xf9\xed \xf1\xe5\xeb\xef) Tj ET\n"
        b"110 340 150 20 re S\n",
    )

    fields = detect_fields(source)

    assert len(fields) == 1
    assert fields[0].name == "txtAgentName"


def test_hebrew_checkbox_label_resolves_to_plan_t_name(tmp_path: Path) -> None:
    # "יועץ פנסיוני" in cp1255 bytes maps to chkPensionCounselor.
    source = _write_pdf(
        tmp_path / "counselor.pdf",
        b"BT /F1 10 Tf 80 352 Td (\xe9\xe5\xf2\xf5 \xf4\xf0\xf1\xe9\xe5\xf0\xe9) Tj ET\n"
        b"60 350 12 12 re S\n",
    )

    fields = detect_fields(source)

    assert len(fields) == 1
    assert fields[0].name == "chkPensionCounselor"
    assert fields[0].field_type == "checkbox"


def test_unmatched_label_keeps_generated_name(tmp_path: Path) -> None:
    source = _write_pdf(
        tmp_path / "boxed.pdf",
        b"BT /F1 10 Tf 50 350 Td (Full Name) Tj ET\n"
        b"100 340 150 20 re S\n",
    )

    fields = detect_fields(source)

    assert fields[0].name == "txtFullName"


def test_cli_detect_command_writes_csv(tmp_path: Path) -> None:
    source = _write_pdf(
        tmp_path / "boxed.pdf",
        b"BT /F1 10 Tf 50 350 Td (Full Name) Tj ET\n"
        b"100 340 150 20 re S\n",
    )
    out_csv = tmp_path / "detected.csv"

    exit_code = main(
        ["detect", "--input", str(source), "--output", str(out_csv)]
    )

    assert exit_code == 0
    header = out_csv.read_text(encoding="utf-8").splitlines()[0]
    assert header == "page,name,type,x,y,w,h,value"


def test_cli_detect_rejects_writing_over_input(tmp_path: Path) -> None:
    source = _write_pdf(
        tmp_path / "boxed.pdf",
        b"100 340 150 20 re S\n",
    )

    exit_code = main(
        ["detect", "--input", str(source), "--output", str(source)]
    )

    assert exit_code == 1
