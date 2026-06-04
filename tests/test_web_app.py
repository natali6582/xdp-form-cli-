from __future__ import annotations

import io
from pathlib import Path

import pikepdf
from pikepdf import Name

from xdp_form_cli.web_app import create_app


def _write_uploadable_pdf(path: Path) -> Path:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(320, 420))
    content = (
        b"BT /F1 10 Tf 40 360 Td (Investor name) Tj ET\n"
        b"BT /F1 10 Tf 40 300 Td (I approve) Tj ET\n"
        b"BT /F1 10 Tf 40 240 Td (Signature) Tj ET\n"
        b"150 352 140 14 re S\n"
        b"150 296 10 10 re S\n"
        b"150 226 140 24 re S\n"
    )
    page = pdf.pages[0]
    page.obj[Name("/Contents")] = pdf.make_stream(content)
    pdf.save(path)
    return path


def test_index_renders_upload_form(tmp_path: Path) -> None:
    app = create_app({"TESTING": True, "JOB_STORAGE_DIR": tmp_path})
    client = app.test_client()

    response = client.get("/")

    assert response.status_code == 200
    text = response.get_data(as_text=True)
    assert "Upload PDF" in text
    assert "multipart/form-data" in text


def test_healthz_returns_ok(tmp_path: Path) -> None:
    app = create_app({"TESTING": True, "JOB_STORAGE_DIR": tmp_path})
    client = app.test_client()

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.get_data(as_text=True) == "ok"


def test_upload_rejects_missing_file(tmp_path: Path) -> None:
    app = create_app({"TESTING": True, "JOB_STORAGE_DIR": tmp_path})
    client = app.test_client()

    response = client.post("/upload", data={}, content_type="multipart/form-data")

    assert response.status_code == 400
    assert "Choose a PDF file to upload." in response.get_data(as_text=True)


def test_upload_rejects_non_pdf(tmp_path: Path) -> None:
    app = create_app({"TESTING": True, "JOB_STORAGE_DIR": tmp_path})
    client = app.test_client()

    response = client.post(
        "/upload",
        data={"file": (io.BytesIO(b"plain text"), "notes.txt")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert "Only .pdf uploads are supported." in response.get_data(as_text=True)


def test_upload_builds_pdf_and_csv_and_exposes_downloads(tmp_path: Path) -> None:
    source = _write_uploadable_pdf(tmp_path / "source.pdf")
    app = create_app({"TESTING": True, "JOB_STORAGE_DIR": tmp_path / "jobs"})
    client = app.test_client()

    with source.open("rb") as handle:
        response = client.post(
            "/upload",
            data={"file": (handle, source.name)},
            content_type="multipart/form-data",
        )

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "Output PDF ready" in page
    assert "Download PDF" in page
    assert "Download CSV" in page

    job_dir = next((tmp_path / "jobs").iterdir())
    output_pdf = job_dir / "source_acroform.pdf"
    output_csv = job_dir / "source_fields.csv"
    assert output_pdf.is_file()
    assert output_csv.is_file()

    pdf_download = client.get(f"/downloads/{job_dir.name}/pdf")
    csv_download = client.get(f"/downloads/{job_dir.name}/csv")

    assert pdf_download.status_code == 200
    assert pdf_download.headers["Content-Type"] == "application/pdf"
    assert csv_download.status_code == 200
    assert "page,name,type,x,y,w,h,value" in csv_download.get_data(as_text=True)


def test_upload_accepts_template_pdf_and_blank_target_pdf(tmp_path: Path) -> None:
    from xdp_form_cli.acroform_builder import create_acroform_pdf

    template_blank = _write_uploadable_pdf(tmp_path / "template_blank.pdf")
    template_fields = tmp_path / "template_fields.csv"
    template_fields.write_text(
        "\n".join(
            [
                "page,name,type,x,y,w,h,value",
                "1,txtInvestorName,text,150,352,140,14,",
                "1,checkboxConsent,checkbox,150,296,10,10,",
                "1,imgPersonSignature,image,150,226,140,24,",
                "",
            ]
        ),
        encoding="utf-8",
    )
    template_with_fields = tmp_path / "template_with_fields.pdf"
    create_acroform_pdf(template_blank, template_fields, template_with_fields)

    target_blank = _write_uploadable_pdf(tmp_path / "target_blank.pdf")
    app = create_app({"TESTING": True, "JOB_STORAGE_DIR": tmp_path / "jobs"})
    client = app.test_client()

    with template_with_fields.open("rb") as template_handle, target_blank.open("rb") as target_handle:
        response = client.post(
            "/upload",
            data={
                "template_file": (template_handle, template_with_fields.name),
                "file": (target_handle, target_blank.name),
            },
            content_type="multipart/form-data",
        )

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "Output PDF ready" in page
    job_dir = next((tmp_path / "jobs").iterdir())
    output_csv = job_dir / "target_blank_fields.csv"
    csv_text = output_csv.read_text(encoding="utf-8")
    assert "imgPersonSignature,image" in csv_text
    assert "checkboxConsent,checkbox" in csv_text
