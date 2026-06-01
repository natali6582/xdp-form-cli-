"""General auto-form builder.

Take a PDF (from a URL or local path) that is an XFA shell or flat layout with
*no working fields*, and produce a fillable AcroForm by:

1. Optionally downloading the source PDF from an http(s) URL.
2. Stripping any embedded XFA so the AcroForm widgets are honoured everywhere.
3. Detecting the form's real boxes (vector rectangles drawn on each page).
4. Placing a text field inside every detected box, naming each field from the
   nearest printed label, and marking signature boxes as image fields.

Signature fields are always built as image (pushbutton) widgets, never text,
matching the project rule that ``img...`` fields hold a stamped image.

The detector emits an editable CSV (``page,name,type,x,y,w,h,value``) alongside
the PDF so a human can nudge any box that landed in the wrong spot and rebuild
with ``create-acroform`` / ``strip-xfa --fields``.
"""

from __future__ import annotations

import csv
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import pikepdf
from pikepdf import Name

from xdp_form_cli.acroform_builder import create_acroform_pdf
from xdp_form_cli.xfa_stripper import strip_xfa


# Words that mark a box as a signature (image) field, in English and Hebrew.
SIGNATURE_KEYWORDS = ("signature", "sign here", "sign", "חתימה")

# Box-size gates (PDF points) for what counts as a fillable input rectangle.
MIN_BOX_WIDTH_PT = 40.0
MIN_BOX_HEIGHT_PT = 9.0
MAX_BOX_HEIGHT_PT = 120.0

# Cap on a downloaded PDF to avoid pulling an unbounded response into memory.
MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024


@dataclass(frozen=True)
class TextAnchor:
    text: str
    x: float
    y: float


@dataclass(frozen=True)
class DetectedBox:
    page: int
    x: float
    y: float
    w: float
    h: float


@dataclass(frozen=True)
class AutoFieldSpec:
    page: int
    name: str
    field_type: str
    x: float
    y: float
    w: float
    h: float


def build_auto_form(
    source: str | Path,
    output_path: str | Path,
    *,
    csv_path: str | Path | None = None,
) -> tuple[Path, Path, int]:
    """Build a fillable AcroForm from a URL or local PDF path.

    Returns ``(output_pdf, fields_csv, field_count)``.
    """
    output = Path(output_path)
    csv_out = Path(csv_path) if csv_path else output.with_suffix(".fields.csv")

    with tempfile.TemporaryDirectory() as tmp_dir:
        local_source = _resolve_source(source, Path(tmp_dir))

        # Strip XFA first so detection runs on the plain PDF we will fill.
        stripped = Path(tmp_dir) / "stripped.pdf"
        strip_xfa(local_source, stripped)

        specs = detect_field_specs(stripped)
        if not specs:
            raise ValueError(
                "No fillable boxes were detected on this PDF. "
                "Supply a field CSV manually and use create-acroform instead."
            )

        write_field_csv(specs, csv_out)
        create_acroform_pdf(stripped, csv_out, output)

    return output, csv_out, len(specs)


def detect_field_specs(pdf_path: str | Path) -> list[AutoFieldSpec]:
    """Detect input boxes on every page and turn them into field specs."""
    specs: list[AutoFieldSpec] = []
    used_names: dict[str, int] = {}

    with pikepdf.Pdf.open(str(pdf_path)) as pdf:
        for page_index, page in enumerate(pdf.pages, start=1):
            anchors = _extract_text_anchors(page)
            boxes = _detect_boxes(page, page_index)
            for box in boxes:
                label = _nearest_label(box, anchors)
                is_signature = _is_signature_label(label)
                base = _field_base_name(label, is_signature)
                name = _unique_name(base, used_names)
                specs.append(
                    AutoFieldSpec(
                        page=box.page,
                        name=name,
                        field_type="image" if is_signature else "text",
                        x=round(box.x, 2),
                        y=round(box.y, 2),
                        w=round(box.w, 2),
                        h=round(box.h, 2),
                    )
                )
    return specs


def write_field_csv(specs: list[AutoFieldSpec], csv_path: str | Path) -> Path:
    path = Path(csv_path)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["page", "name", "type", "x", "y", "w", "h", "value"])
        for spec in specs:
            writer.writerow(
                [spec.page, spec.name, spec.field_type, spec.x, spec.y, spec.w, spec.h, ""]
            )
    return path


def _resolve_source(source: str | Path, work_dir: Path) -> Path:
    text = str(source)
    parsed = urlparse(text)
    if parsed.scheme in ("http", "https"):
        return _download_pdf(text, work_dir / "source.pdf")
    # A single-letter scheme is a Windows drive letter (C:\...), not a URL.
    if parsed.scheme in ("", "file") or len(parsed.scheme) == 1:
        local = Path(parsed.path if parsed.scheme == "file" else text)
        if not local.is_file():
            raise ValueError(f"Source PDF not found: {local}")
        return local
    raise ValueError(f"Unsupported source scheme: {parsed.scheme or '(none)'}. Use http(s) or a file path.")


def _download_pdf(url: str, dest: Path) -> Path:
    request = Request(url, headers={"User-Agent": "xdp-form-cli/auto-form"})
    with urlopen(request, timeout=30) as response:  # noqa: S310 - scheme validated by caller
        data = response.read(MAX_DOWNLOAD_BYTES + 1)
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise ValueError("Downloaded file exceeds the 50 MB limit.")
    if not data.startswith(b"%PDF"):
        raise ValueError("Downloaded content is not a PDF (missing %PDF header).")
    dest.write_bytes(data)
    return dest


def _detect_boxes(page: pikepdf.Page, page_index: int) -> list[DetectedBox]:
    rects: list[tuple[float, float, float, float]] = []
    for token in pikepdf.parse_content_stream(page):
        if str(token.operator) != "re":
            continue
        try:
            x, y, w, h = (float(v) for v in token.operands)
        except (TypeError, ValueError):
            continue
        if w < 0:
            x, w = x + w, -w
        if h < 0:
            y, h = y + h, -h
        if w >= MIN_BOX_WIDTH_PT and MIN_BOX_HEIGHT_PT <= h <= MAX_BOX_HEIGHT_PT:
            rects.append((round(x, 1), round(y, 1), round(w, 1), round(h, 1)))

    unique = sorted(set(rects), key=lambda r: (-r[1], r[0]))
    return [DetectedBox(page_index, x, y, w, h) for (x, y, w, h) in unique]


def _extract_text_anchors(page: pikepdf.Page) -> list[TextAnchor]:
    anchors: list[TextAnchor] = []
    tx = ty = 0.0
    for token in pikepdf.parse_content_stream(page):
        op = str(token.operator)
        if op in ("Tm", "Td", "TD"):
            try:
                values = [float(v) for v in token.operands]
            except (TypeError, ValueError):
                continue
            if op == "Tm" and len(values) == 6:
                tx, ty = values[4], values[5]
            elif len(values) == 2:
                tx += values[0]
                ty += values[1]
        elif op == "Tj" and token.operands:
            text = _operand_text(token.operands[0])
            if text.strip():
                anchors.append(TextAnchor(text.strip(), tx, ty))
        elif op == "TJ" and token.operands:
            parts = [
                _operand_text(item)
                for item in token.operands[0]
                if not isinstance(item, (int, float))
            ]
            text = "".join(parts).strip()
            if text:
                anchors.append(TextAnchor(text, tx, ty))
    return anchors


def _operand_text(operand) -> str:
    try:
        return bytes(operand).decode("latin-1", errors="ignore")
    except (TypeError, ValueError):
        return str(operand)


def _nearest_label(box: DetectedBox, anchors: list[TextAnchor]) -> str:
    box_cx = box.x + box.w / 2
    box_top = box.y + box.h
    best: str | None = None
    best_score = float("inf")
    for anchor in anchors:
        # Prefer labels above or to the left of the box, close to it.
        dx = max(box.x - anchor.x, anchor.x - (box.x + box.w), 0)
        dy = anchor.y - box_top
        if dy < -box.h:  # anchor sits well below the box
            continue
        score = abs(dx) + abs(dy) + abs(anchor.x - box_cx) * 0.1
        if score < best_score:
            best_score = score
            best = anchor.text
    return best or ""


def _is_signature_label(label: str) -> bool:
    if not _looks_like_text(label):
        return False
    lowered = label.lower()
    return any(keyword in lowered for keyword in SIGNATURE_KEYWORDS)


def _looks_like_text(label: str) -> bool:
    """Reject labels that are clearly font-encoding noise, not real words.

    Some PDFs use custom font encodings where the raw content-stream bytes do
    not decode to readable characters. Such labels are dominated by digits or
    have no real word, so they should fall back to generic field names.
    """
    letters = sum(ch.isalpha() for ch in label)
    digits = sum(ch.isdigit() for ch in label)
    if letters < 3:
        return False
    if digits and digits / (letters + digits) > 0.2:
        return False
    return True


def _field_base_name(label: str, is_signature: bool) -> str:
    prefix = "img" if is_signature else "txt"
    if not _looks_like_text(label):
        return f"{prefix}Field"
    slug = re.sub(r"[^A-Za-z0-9]+", "", label.title())
    if not slug:
        return f"{prefix}Field"
    return f"{prefix}{slug[:32]}"


def _unique_name(base: str, used_names: dict[str, int]) -> str:
    count = used_names.get(base, 0) + 1
    used_names[base] = count
    return base if count == 1 else f"{base}{count}"
