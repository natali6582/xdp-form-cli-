from __future__ import annotations

import io
import os
import time
from pathlib import Path

import pikepdf
from pikepdf import Name

from xdp_form_cli.web_app import create_app


def _write_uploadable_pdf(path: Path) -> Path:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(320, 420))
    content = (
        b"BT /F1 10 Tf 40 360 Td (Investor name) Tj ET\n"
        b"150 352 140 14 re S\n"
    )
    page = pdf.pages[0]
    page.obj[Name("/Contents")] = pdf.make_stream(content)
    pdf.save(path)
    return path


def _upload(client, pdf_path: Path):
    return client.post(
        "/upload",
        data={"file": (io.BytesIO(pdf_path.read_bytes()), pdf_path.name)},
        content_type="multipart/form-data",
    )


def test_upload_deletes_input_pdf_after_success(tmp_path: Path) -> None:
    app = create_app({"TESTING": True, "JOB_STORAGE_DIR": tmp_path / "jobs"})
    client = app.test_client()
    source = _write_uploadable_pdf(tmp_path / "client form.pdf")

    response = _upload(client, source)

    assert response.status_code == 200
    job_dirs = [p for p in (tmp_path / "jobs").iterdir() if p.is_dir()]
    assert len(job_dirs) == 1
    leftovers = {p.name for p in job_dirs[0].iterdir()}
    assert "client form.pdf" not in leftovers
    assert "client form_acroform.pdf" in leftovers
    assert "client form_fields.csv" in leftovers


def test_outputs_remain_downloadable_after_input_deletion(tmp_path: Path) -> None:
    app = create_app({"TESTING": True, "JOB_STORAGE_DIR": tmp_path / "jobs"})
    client = app.test_client()
    source = _write_uploadable_pdf(tmp_path / "form.pdf")

    page = _upload(client, source).get_data(as_text=True)
    job_id = page.split("/downloads/")[1].split("/")[0]

    pdf_response = client.get(f"/downloads/{job_id}/pdf")
    csv_response = client.get(f"/downloads/{job_id}/csv")

    assert pdf_response.status_code == 200
    assert pdf_response.data.startswith(b"%PDF")
    assert csv_response.status_code == 200


def test_upload_sweeps_expired_jobs(tmp_path: Path) -> None:
    storage = tmp_path / "jobs"
    expired = storage / ("e" * 32)
    expired.mkdir(parents=True)
    (expired / "old_client.pdf").write_bytes(b"%PDF-1.4 stale")
    stamp = time.time() - 8 * 3600
    os.utime(expired, (stamp, stamp))

    app = create_app({"TESTING": True, "JOB_STORAGE_DIR": storage})
    client = app.test_client()
    source = _write_uploadable_pdf(tmp_path / "fresh.pdf")

    response = _upload(client, source)

    assert response.status_code == 200
    assert not expired.exists()


def test_create_app_sweeps_expired_jobs_at_startup(tmp_path: Path) -> None:
    storage = tmp_path / "jobs"
    expired = storage / ("f" * 32)
    expired.mkdir(parents=True)
    stamp = time.time() - 8 * 3600
    os.utime(expired, (stamp, stamp))

    create_app({"TESTING": True, "JOB_STORAGE_DIR": storage})

    assert not expired.exists()


def test_ttl_is_configurable(tmp_path: Path) -> None:
    storage = tmp_path / "jobs"
    recent = storage / ("d" * 32)
    recent.mkdir(parents=True)
    stamp = time.time() - 120
    os.utime(recent, (stamp, stamp))

    create_app({"TESTING": True, "JOB_STORAGE_DIR": storage, "JOB_TTL_SECONDS": 60})

    assert not recent.exists()


def test_basic_auth_blocks_unauthenticated_requests(tmp_path: Path) -> None:
    app = create_app(
        {
            "TESTING": True,
            "JOB_STORAGE_DIR": tmp_path,
            "BASIC_AUTH_USERNAME": "plan-t",
            "BASIC_AUTH_PASSWORD": "secret",
        }
    )
    client = app.test_client()

    response = client.get("/")

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"].startswith("Basic")


def test_basic_auth_rejects_wrong_credentials(tmp_path: Path) -> None:
    app = create_app(
        {
            "TESTING": True,
            "JOB_STORAGE_DIR": tmp_path,
            "BASIC_AUTH_USERNAME": "plan-t",
            "BASIC_AUTH_PASSWORD": "secret",
        }
    )
    client = app.test_client()

    response = client.get("/", auth=("plan-t", "wrong"))

    assert response.status_code == 401


def test_basic_auth_allows_correct_credentials(tmp_path: Path) -> None:
    app = create_app(
        {
            "TESTING": True,
            "JOB_STORAGE_DIR": tmp_path,
            "BASIC_AUTH_USERNAME": "plan-t",
            "BASIC_AUTH_PASSWORD": "secret",
        }
    )
    client = app.test_client()

    response = client.get("/", auth=("plan-t", "secret"))

    assert response.status_code == 200


def test_healthz_stays_open_when_auth_enabled(tmp_path: Path) -> None:
    app = create_app(
        {
            "TESTING": True,
            "JOB_STORAGE_DIR": tmp_path,
            "BASIC_AUTH_USERNAME": "plan-t",
            "BASIC_AUTH_PASSWORD": "secret",
        }
    )
    client = app.test_client()

    response = client.get("/healthz")

    assert response.status_code == 200


def test_app_stays_open_when_auth_not_configured(tmp_path: Path) -> None:
    app = create_app({"TESTING": True, "JOB_STORAGE_DIR": tmp_path})
    client = app.test_client()

    response = client.get("/")

    assert response.status_code == 200
