from __future__ import annotations

import json
import os
import tempfile
import uuid
from pathlib import Path

from flask import Flask, abort, render_template_string, request, send_file

from xdp_form_cli.auto_form import MAX_DOWNLOAD_BYTES, build_auto_client_form
from xdp_form_cli.acroform_builder import load_field_specs
from xdp_form_cli.field_transfer import transfer_fields_to_pdf


INDEX_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>XDP Form CLI</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 2rem; max-width: 56rem; }
    form { border: 1px solid #ccc; padding: 1rem; border-radius: 8px; }
    .error { color: #a40000; margin-bottom: 1rem; }
    .warning { color: #8a5a00; }
    .result { border: 1px solid #ccc; padding: 1rem; border-radius: 8px; margin-top: 1rem; }
    .muted { color: #555; font-size: 0.95rem; }
    button { padding: 0.6rem 1rem; }
  </style>
</head>
<body>
  <h1>Upload PDF</h1>
  <p class="muted">Upload a blank/new PDF. Optionally upload an older fielded PDF with the same layout to copy its fields.</p>
  {% if error %}
  <div class="error">{{ error }}</div>
  {% endif %}
  <form method="post" action="/upload" enctype="multipart/form-data">
    <p><strong>Blank/new PDF</strong></p>
    <input type="file" name="file" accept="application/pdf,.pdf" required>
    <p><strong>Optional template PDF with existing fields</strong></p>
    <input type="file" name="template_file" accept="application/pdf,.pdf">
    <button type="submit">Build fillable PDF</button>
  </form>
  {% if result %}
  <div class="result">
    <h2>Output PDF ready</h2>
    <p><strong>Detected fields:</strong> {{ result.field_count }}</p>
    <p><strong>Field types:</strong> {{ result.summary }}</p>
    {% if result.warnings %}
    <p><strong>Warnings:</strong></p>
    <ul>
      {% for warning in result.warnings %}
      <li class="warning">{{ warning }}</li>
      {% endfor %}
    </ul>
    {% endif %}
    <p><a href="{{ result.pdf_url }}">Download PDF</a></p>
    <p><a href="{{ result.csv_url }}">Download CSV</a></p>
  </div>
  {% endif %}
</body>
</html>
"""


def create_app(config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.update(
        JOB_STORAGE_DIR=Path(os.environ.get("JOB_STORAGE_DIR", Path(tempfile.gettempdir()) / "xdp-form-jobs")),
        MAX_CONTENT_LENGTH=MAX_DOWNLOAD_BYTES,
    )
    if config:
        app.config.update(config)
    app.config["JOB_STORAGE_DIR"] = Path(app.config["JOB_STORAGE_DIR"])
    app.config["JOB_STORAGE_DIR"].mkdir(parents=True, exist_ok=True)

    @app.get("/")
    def index():
        return render_template_string(INDEX_TEMPLATE, error=None, result=None)

    @app.get("/healthz")
    def healthz():
        return "ok"

    @app.post("/upload")
    def upload():
        file = request.files.get("file")
        template_file = request.files.get("template_file")
        if file is None or not file.filename:
            return render_template_string(INDEX_TEMPLATE, error="Choose a PDF file to upload.", result=None), 400

        original_name = Path(file.filename).name
        if Path(original_name).suffix.lower() != ".pdf":
            return render_template_string(INDEX_TEMPLATE, error="Only .pdf uploads are supported.", result=None), 400
        if template_file is not None and template_file.filename:
            template_name = Path(template_file.filename).name
            if Path(template_name).suffix.lower() != ".pdf":
                return render_template_string(
                    INDEX_TEMPLATE,
                    error="Template file must also be a .pdf.",
                    result=None,
                ), 400

        display_stem = Path(original_name).stem or "uploaded"
        job_id = uuid.uuid4().hex
        job_dir = app.config["JOB_STORAGE_DIR"] / job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        input_pdf = job_dir / original_name
        output_pdf = job_dir / f"{display_stem}_acroform.pdf"
        output_csv = job_dir / f"{display_stem}_fields.csv"
        manifest_path = job_dir / "manifest.json"

        file.save(input_pdf)
        template_pdf = None
        if template_file is not None and template_file.filename:
            template_pdf = job_dir / Path(template_file.filename).name
            template_file.save(template_pdf)

        try:
            if template_pdf is not None:
                _, _, count = transfer_fields_to_pdf(template_pdf, input_pdf, output_pdf, csv_path=output_csv)
                summary_text = "template-transfer"
                warnings = ()
            else:
                _, _, count, summary = build_auto_client_form(input_pdf, output_pdf, csv_path=output_csv)
                summary_text = _format_type_counts(summary.type_counts)
                warnings = summary.warnings
        except Exception as exc:  # pragma: no cover - exact PDF parser exceptions vary
            return render_template_string(INDEX_TEMPLATE, error=str(exc), result=None), 500

        manifest_path.write_text(
            json.dumps(
                {
                    "original_name": original_name,
                    "output_pdf_name": output_pdf.name,
                    "output_csv_name": output_csv.name,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        result = {
            "field_count": count,
            "summary": summary_text,
            "warnings": warnings,
            "pdf_url": f"/downloads/{job_id}/pdf",
            "csv_url": f"/downloads/{job_id}/csv",
        }
        return render_template_string(INDEX_TEMPLATE, error=None, result=result)

    @app.get("/downloads/<job_id>/<kind>")
    def download(job_id: str, kind: str):
        job_dir = app.config["JOB_STORAGE_DIR"] / job_id
        manifest_path = job_dir / "manifest.json"
        if not manifest_path.is_file():
            abort(404)

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if kind == "pdf":
            target = job_dir / manifest["output_pdf_name"]
            mimetype = "application/pdf"
        elif kind == "csv":
            target = job_dir / manifest["output_csv_name"]
            mimetype = "text/csv"
        else:
            abort(404)

        if not target.is_file():
            abort(404)

        return send_file(target, as_attachment=True, download_name=target.name, mimetype=mimetype)

    return app


def _summarize_csv_types(csv_path: Path) -> str:
    counts: dict[str, int] = {}
    for spec in load_field_specs(csv_path):
        counts[spec.field_type] = counts.get(spec.field_type, 0) + 1
    return ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))


def _format_type_counts(counts: dict[str, int]) -> str:
    return ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))


app = create_app()
