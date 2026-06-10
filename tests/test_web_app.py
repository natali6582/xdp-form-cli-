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


def _write_uploadable_plan_t_alias_pdf(path: Path) -> Path:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(320, 420))
    content = (
        b"BT /F1 10 Tf 40 320 Td (Name Of Account Owner) Tj ET\n"
        b"170 312 120 14 re S\n"
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
    assert "PDF without fields" in text
    assert "template_file" not in text
    assert "Optional template" not in text
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


def test_upload_uses_packaged_plan_t_defaults_for_field_names(tmp_path: Path) -> None:
    source = _write_uploadable_plan_t_alias_pdf(tmp_path / "source.pdf")
    app = create_app({"TESTING": True, "JOB_STORAGE_DIR": tmp_path / "jobs"})
    client = app.test_client()

    with source.open("rb") as handle:
        response = client.post(
            "/upload",
            data={"file": (handle, source.name)},
            content_type="multipart/form-data",
        )

    assert response.status_code == 200
    job_dir = next((tmp_path / "jobs").iterdir())
    csv_download = client.get(f"/downloads/{job_dir.name}/csv")

    assert csv_download.status_code == 200
    csv_text = csv_download.get_data(as_text=True)
    # The full Plan-T inventory resolves "Account Owner Full Name" to the
    # owner-name field, which is itself a canonical Plan-T name.
    assert "txtNameOfAccountOwner" in csv_text


def test_upload_ignores_unexpected_template_file_and_uses_single_pdf_flow(tmp_path: Path) -> None:
    source = _write_uploadable_pdf(tmp_path / "source.pdf")
    extra = _write_uploadable_pdf(tmp_path / "extra.pdf")
    app = create_app({"TESTING": True, "JOB_STORAGE_DIR": tmp_path / "jobs"})
    client = app.test_client()

    with source.open("rb") as source_handle, extra.open("rb") as extra_handle:
        response = client.post(
            "/upload",
            data={
                "file": (source_handle, source.name),
                "template_file": (extra_handle, extra.name),
            },
            content_type="multipart/form-data",
        )

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "Output PDF ready" in page
    assert "template-transfer" not in page
    job_dir = next((tmp_path / "jobs").iterdir())
    output_csv = job_dir / "source_fields.csv"
    assert output_csv.is_file()
