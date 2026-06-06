from __future__ import annotations

from pathlib import Path

from xdp_form_cli.auto_form import AzureLayoutResult, BBoxWord, DetectedBox, TextAnchor
from xdp_form_cli.azure_report import build_azure_layout_report


def test_build_azure_layout_report_uses_known_field_list(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fields_list = tmp_path / "known.csv"
    fields_list.write_text(
        "field_name,prefix\n"
        "txtPersonFullName,txt\n"
        "txtCommitmentAmount,txt\n"
        "chkApproved,chk\n",
        encoding="utf-8",
    )
    input_pdf = tmp_path / "input.pdf"
    input_pdf.write_bytes(b"%PDF-1.7\n")

    layout = AzureLayoutResult(
        words_by_page={
            1: [
                BBoxWord(
                    page=1,
                    page_height=400,
                    text="Commitment",
                    x0=40,
                    y0=80,
                    x1=120,
                    y1=95,
                ),
                BBoxWord(
                    page=1,
                    page_height=400,
                    text="Amount",
                    x0=125,
                    y0=80,
                    x1=180,
                    y1=95,
                ),
            ]
        },
        anchors_by_page={1: [TextAnchor("Commitment Amount", 40, 305)]},
        checkbox_boxes_by_page={1: [DetectedBox(page=1, x=30, y=250, w=10, h=10)]},
    )
    monkeypatch.setattr("xdp_form_cli.azure_report._load_azure_layout", lambda _path, enabled: layout)

    summary = build_azure_layout_report(
        input_pdf,
        fields_list,
        tmp_path / "azure.csv",
        output_json=tmp_path / "azure.json",
    )

    assert summary.known_field_count == 3
    assert summary.word_count == 2
    assert summary.checkbox_count == 1
    report = summary.csv_path.read_text(encoding="utf-8-sig")
    assert "txtCommitmentAmount" in report
    assert "candidate_checkbox" in report
    assert summary.json_path is not None
    assert summary.json_path.is_file()
