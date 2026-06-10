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
import decimal
import html
import os
import re
import shutil
import subprocess
import tempfile
from contextlib import ExitStack
from dataclasses import dataclass
from importlib.resources import as_file, files
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import pikepdf
from pikepdf import Name

from xdp_form_cli.acroform_builder import create_acroform_pdf
from xdp_form_cli.field_name_resolution import FieldNameResolver
from xdp_form_cli.field_validation import ParsedField, _validate_original_content_overlap
from xdp_form_cli.xfa_stripper import pdf_has_xfa, strip_xfa


DEFAULT_PLAN_T_FIELDS_RESOURCE = "plan_t_fields.csv"
DEFAULT_PLAN_T_MAPPING_RESOURCE = "livecycle_plan_t_mapping.xlsx"
DEFAULT_PLAN_T_SEMANTIC_RESOURCE = "plan_t_semantic_labels.csv"


# Words that mark a box as a signature (image) field, in English and Hebrew.
HEBREW_SIGNATURE_KEYWORDS = ("חתום", "חתימה", "חתימת", "חותם", "החתום")
HEBREW_SIGNATURE_LEADING_WORDS = (
    "\u05d7\u05ea\u05d9\u05de\u05d4",
    "\u05d7\u05ea\u05d9\u05de\u05ea",
    "\u05d7\u05ea\u05d9\u05de\u05d5\u05ea",
    "\u05d7\u05ea\u05d5\u05dd",
    "\u05d7\u05ea\u05dd",
    "\u05d7\u05ea\u05de\u05d4",
    "\u05d7\u05ea\u05de\u05d5",
    "\u05d7\u05ea\u05de\u05ea\u05d9",
    "\u05d7\u05d5\u05ea\u05dd",
    "\u05d7\u05d5\u05ea\u05de\u05ea",
)
HEBREW_SIGNATURE_LABEL_FIRST_WORDS = HEBREW_SIGNATURE_LEADING_WORDS + (
    "\u05d4\u05d7\u05ea\u05d5\u05dd",
)
ENGLISH_SIGNATURE_LABEL_FIRST_WORDS = ("signature", "sign", "signed")
BBOX_WORD_RE = re.compile(
    r'<word\s+xMin="(?P<x0>[-0-9.]+)"\s+yMin="(?P<y0>[-0-9.]+)"\s+'
    r'xMax="(?P<x1>[-0-9.]+)"\s+yMax="(?P<y1>[-0-9.]+)">(?P<text>.*?)</word>'
)
BBOX_PAGE_RE = re.compile(r'<page\b[^>]*\bheight="(?P<height>[-0-9.]+)"')
SIGNATURE_KEYWORDS = ("signature", "sign here", "sign", "חתימה")

# Box-size gates (PDF points) for what counts as a fillable input rectangle.
MIN_BOX_WIDTH_PT = 40.0
MIN_BOX_HEIGHT_PT = 9.0
MAX_BOX_HEIGHT_PT = 120.0

# Field height cap: keeps the widget in the blank lower portion of a tall cell,
# below any printed label. Must match the /Arial 10 Tf default in acroform_builder.
FIELD_FONT_SIZE_PT = 10.0
MAX_FIELD_HEIGHT_PT = 2 * FIELD_FONT_SIZE_PT
UNDERLINE_FIELD_HEIGHT_PT = 12.0

# Checkbox size gates (PDF points): small, approximately square rectangles.
MIN_CHECKBOX_PT = 6.0
MAX_CHECKBOX_PT = 20.0
VISUAL_CHECKBOX_GLYPHS = {"□", "☐", "▢", "◻", "▫", "☑", "☒"}
VISUAL_CHECKBOX_MIN_PT = 10.0
VISUAL_CHECKBOX_MAX_PT = 20.0
CHECKBOX_GLYPHS = ("\x00\x86", "\x86", "☐", "□", "☑", "☒")

# Cap on a downloaded PDF to avoid pulling an unbounded response into memory.
MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024

# Optional Azure Document Intelligence integration. It is opt-in because each
# call sends the PDF to Azure and may incur cost.
AZURE_ENDPOINT_ENV = "AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT"
AZURE_KEY_ENV = "AZURE_DOCUMENT_INTELLIGENCE_KEY"
AZURE_MODEL_ID_ENV = "AZURE_DOCUMENT_INTELLIGENCE_MODEL_ID"
AZURE_USE_ENV = "XDP_FORM_USE_AZURE_DOCUMENT_INTELLIGENCE"
AZURE_DEFAULT_MODEL_ID = "prebuilt-layout"


@dataclass(frozen=True)
class TextAnchor:
    text: str
    x: float
    y: float


@dataclass(frozen=True)
class BBoxWord:
    page: int
    page_height: float
    text: str
    x0: float
    y0: float
    x1: float
    y1: float


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
    label: str = ""
    name_match_method: str = "generated"
    name_matched_plan_t: bool = False


@dataclass(frozen=True)
class SignatureContext:
    page: int
    x0: float
    x1: float
    y: float
    direction: str


@dataclass(frozen=True)
class AutoClientFormSummary:
    type_counts: dict[str, int]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class AzureLayoutResult:
    words_by_page: dict[int, list[BBoxWord]]
    anchors_by_page: dict[int, list[TextAnchor]]
    checkbox_boxes_by_page: dict[int, list[DetectedBox]]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResolvedAutoName:
    name: str
    matched: bool
    method: str


GraphicsMatrix = tuple[float, float, float, float, float, float]


IDENTITY_GRAPHICS_MATRIX: GraphicsMatrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _concat_graphics_matrix(left: GraphicsMatrix, right: GraphicsMatrix) -> GraphicsMatrix:
    la, lb, lc, ld, le, lf = left
    ra, rb, rc, rd, re_, rf = right
    return (
        la * ra + lc * rb,
        lb * ra + ld * rb,
        la * rc + lc * rd,
        lb * rc + ld * rd,
        la * re_ + lc * rf + le,
        lb * re_ + ld * rf + lf,
    )


def _transform_point(matrix: GraphicsMatrix, x: float, y: float) -> tuple[float, float]:
    a, b, c, d, e, f = matrix
    return (a * x + c * y + e, b * x + d * y + f)


def _transform_rect(
    matrix: GraphicsMatrix,
    x: float,
    y: float,
    w: float,
    h: float,
) -> tuple[float, float, float, float]:
    points = (
        _transform_point(matrix, x, y),
        _transform_point(matrix, x + w, y),
        _transform_point(matrix, x + w, y + h),
        _transform_point(matrix, x, y + h),
    )
    xs = [px for px, _ in points]
    ys = [py for _, py in points]
    min_x = min(xs)
    min_y = min(ys)
    return (min_x, min_y, max(xs) - min_x, max(ys) - min_y)


def build_auto_form(
    source: str | Path,
    output_path: str | Path,
    *,
    csv_path: str | Path | None = None,
    xfa_template_path: str | Path | None = None,
    use_azure_document_intelligence: bool | None = None,
) -> tuple[Path, Path, int]:
    """Build a fillable AcroForm from a URL or local PDF path.

    Returns ``(output_pdf, fields_csv, field_count)``.
    """
    output, csv_out, specs, _warnings = _build_detected_form(
        source,
        output_path,
        csv_path=csv_path,
        xfa_template_path=xfa_template_path,
        use_azure_document_intelligence=use_azure_document_intelligence,
    )
    return output, csv_out, len(specs)


def build_auto_client_form(
    source: str | Path,
    output_path: str | Path,
    *,
    csv_path: str | Path | None = None,
    use_azure_document_intelligence: bool | None = None,
    fields_list_path: str | Path | None = None,
    field_mapping_path: str | Path | None = None,
    semantic_map_path: str | Path | None = None,
    mapping_report_path: str | Path | None = None,
) -> tuple[Path, Path, int, AutoClientFormSummary]:
    """Build a client-upload fillable AcroForm and return a detection summary."""
    output, csv_out, specs, warnings = _build_detected_form(
        source,
        output_path,
        csv_path=csv_path,
        use_azure_document_intelligence=use_azure_document_intelligence,
        fields_list_path=fields_list_path,
        field_mapping_path=field_mapping_path,
        semantic_map_path=semantic_map_path,
    )
    if mapping_report_path is not None:
        write_mapping_report(specs, mapping_report_path)
    return output, csv_out, len(specs), _summarize_specs(specs, warnings=warnings)


def _build_detected_form(
    source: str | Path,
    output_path: str | Path,
    *,
    csv_path: str | Path | None,
    xfa_template_path: str | Path | None = None,
    use_azure_document_intelligence: bool | None = None,
    fields_list_path: str | Path | None = None,
    field_mapping_path: str | Path | None = None,
    semantic_map_path: str | Path | None = None,
) -> tuple[Path, Path, list[AutoFieldSpec], tuple[str, ...]]:
    output = Path(output_path)
    csv_out = Path(csv_path) if csv_path else output.with_suffix(".fields.csv")

    with tempfile.TemporaryDirectory() as tmp_dir:
        local_source = _resolve_source(source, Path(tmp_dir))

        has_xfa = pdf_has_xfa(local_source)

        # Detection runs on a stripped copy so the content stream is the one
        # the AcroForm will sit on top of, with no XFA-only quirks.
        stripped = Path(tmp_dir) / "stripped.pdf"
        strip_xfa(local_source, stripped)

        azure_layout = _load_azure_layout(
            stripped,
            enabled=_should_use_azure_document_intelligence(use_azure_document_intelligence),
        )
        field_name_resolver = _load_field_name_resolver(
            fields_list_csv=fields_list_path,
            mapping_xlsx=field_mapping_path,
            semantic_map_csv=semantic_map_path,
        )
        specs = detect_field_specs(
            stripped,
            azure_layout=azure_layout,
            field_name_resolver=field_name_resolver,
        )
        naming_warnings = _field_name_resolution_warnings(specs, field_name_resolver)
        if not specs:
            raise ValueError(
                "No fillable boxes were detected on this PDF. "
                "Supply a field CSV manually and use create-acroform instead."
            )

        write_field_csv(specs, csv_out)

        if xfa_template_path is not None:
            # User supplied an external XFA template: inject fields into it and
            # embed the modified template in the output PDF alongside AcroForm.
            from xdp_form_cli.xfa_field_injector import inject_xfa_fields_from_template

            with_xfa_fields = Path(tmp_dir) / "with_xfa_fields.pdf"
            inject_xfa_fields_from_template(local_source, xfa_template_path, with_xfa_fields, specs)
            create_acroform_pdf(with_xfa_fields, csv_out, output)
        elif has_xfa:
            # Preserve XFA: inject <field> elements into the template, then add
            # matching AcroForm widgets so both layers carry the same fields.
            from xdp_form_cli.xfa_field_injector import inject_xfa_fields

            with_xfa_fields = Path(tmp_dir) / "with_xfa_fields.pdf"
            inject_xfa_fields(local_source, with_xfa_fields, specs)
            create_acroform_pdf(with_xfa_fields, csv_out, output)
        else:
            create_acroform_pdf(stripped, csv_out, output)

    azure_warnings = azure_layout.warnings if azure_layout is not None else ()
    warnings = (*azure_warnings, *naming_warnings)
    return output, csv_out, specs, warnings


def _summarize_specs(specs: list[AutoFieldSpec], *, warnings: tuple[str, ...] = ()) -> AutoClientFormSummary:
    counts: dict[str, int] = {}
    for spec in specs:
        counts[spec.field_type] = counts.get(spec.field_type, 0) + 1
    return AutoClientFormSummary(type_counts=dict(sorted(counts.items())), warnings=warnings)


def _load_field_name_resolver(
    *,
    fields_list_csv: str | Path | None,
    mapping_xlsx: str | Path | None,
    semantic_map_csv: str | Path | None,
) -> FieldNameResolver | None:
    """Load explicit Plan-T mapping files, falling back to packaged defaults.

    Render and other deployed environments do not have the user's OneDrive
    files, so the canonical Plan-T field list and safe LiveCycle aliases are
    shipped as package resources.
    """
    with ExitStack() as stack:
        using_packaged_fields = fields_list_csv is None
        if fields_list_csv is None:
            fields_list_csv = _packaged_resource_path(stack, DEFAULT_PLAN_T_FIELDS_RESOURCE)
        if mapping_xlsx is None:
            mapping_xlsx = _packaged_resource_path(stack, DEFAULT_PLAN_T_MAPPING_RESOURCE)
        if semantic_map_csv is None and using_packaged_fields:
            semantic_map_csv = _packaged_resource_path(stack, DEFAULT_PLAN_T_SEMANTIC_RESOURCE)

        return FieldNameResolver.from_files(
            fields_list_csv=fields_list_csv,
            mapping_xlsx=mapping_xlsx,
            semantic_map_csv=semantic_map_csv,
        )


def _packaged_resource_path(stack: ExitStack, name: str) -> Path | None:
    try:
        resource = files("xdp_form_cli.resources").joinpath(name)
        if not resource.is_file():
            return None
        return Path(stack.enter_context(as_file(resource)))
    except (FileNotFoundError, ModuleNotFoundError):
        return None


def _field_name_resolution_warnings(
    specs: list[AutoFieldSpec],
    field_name_resolver: FieldNameResolver | None,
) -> tuple[str, ...]:
    if field_name_resolver is None:
        return ()

    matched = sum(1 for spec in specs if field_name_resolver.is_known_name(spec.name))
    if matched == len(specs):
        return (f"Plan-T field-name resolver matched all {matched} field(s).",)
    return (
        f"Plan-T field-name resolver matched {matched}/{len(specs)} field(s); "
        "unmatched fields kept generated names and need manual mapping.",
    )


def detect_field_specs(
    pdf_path: str | Path,
    *,
    azure_layout: AzureLayoutResult | None = None,
    field_name_resolver: FieldNameResolver | None = None,
) -> list[AutoFieldSpec]:
    """Detect input boxes on every page and turn them into field specs."""
    specs: list[AutoFieldSpec] = []
    used_names: dict[str, int] = {}
    bbox_xml = _read_bbox_xml(pdf_path)
    bbox_words_by_page = _bbox_words_by_page_from_xml(bbox_xml) if bbox_xml else {}
    bbox_underline_boxes = _bbox_underline_boxes_from_words(bbox_words_by_page) if bbox_words_by_page else {}
    bbox_checkbox_boxes = _bbox_checkbox_boxes_from_words(bbox_words_by_page) if bbox_words_by_page else {}
    azure_underline_boxes = (
        _bbox_underline_boxes_from_words(azure_layout.words_by_page)
        if azure_layout is not None else {}
    )
    azure_checkbox_boxes = (
        _bbox_checkbox_boxes_from_words(azure_layout.words_by_page)
        if azure_layout is not None else {}
    )

    with pikepdf.Pdf.open(str(pdf_path)) as pdf:
        for page_index, page in enumerate(pdf.pages, start=1):
            anchors = _extract_text_anchors(page)
            anchors.extend(_bbox_text_anchors_from_words(bbox_words_by_page.get(page_index, [])))
            if azure_layout is not None:
                anchors.extend(azure_layout.anchors_by_page.get(page_index, []))
            geo_boxes = _detect_boxes(page, page_index)
            raw_underline_boxes = _detect_underline_boxes(page, page_index)
            underline_boxes = [
                b for b in raw_underline_boxes
                if not _overlaps_any(b, geo_boxes)
            ]
            underline_boxes.extend(
                b for b in bbox_underline_boxes.get(page_index, [])
                if not _overlaps_any(b, geo_boxes) and not _overlaps_any(b, underline_boxes)
            )
            underline_boxes.extend(
                b for b in azure_underline_boxes.get(page_index, [])
                if not _overlaps_any(b, geo_boxes) and not _overlaps_any(b, underline_boxes)
            )
            checkbox_boxes = [
                b for b in _detect_checkbox_boxes(page, page_index)
                if not _overlaps_any(b, geo_boxes) and not _overlaps_any(b, underline_boxes)
            ]
            glyph_checkbox_boxes = [
                b for b in _detect_glyph_checkbox_boxes(page, page_index)
                if (
                    not _overlaps_any(b, geo_boxes)
                    and not _overlaps_any(b, underline_boxes)
                    and not _overlaps_any(b, checkbox_boxes)
                )
            ]
            checkbox_boxes.extend(glyph_checkbox_boxes)
            checkbox_boxes.extend(
                b for b in bbox_checkbox_boxes.get(page_index, [])
                if (
                    not _overlaps_any(b, geo_boxes)
                    and not _overlaps_any(b, underline_boxes)
                    and not _overlaps_any(b, checkbox_boxes)
                )
            )
            checkbox_boxes.extend(
                b for b in azure_checkbox_boxes.get(page_index, [])
                if (
                    not _overlaps_any(b, geo_boxes)
                    and not _overlaps_any(b, underline_boxes)
                    and not _overlaps_any(b, checkbox_boxes)
                )
            )
            if azure_layout is not None:
                checkbox_boxes.extend(
                    b for b in azure_layout.checkbox_boxes_by_page.get(page_index, [])
                    if (
                        not _overlaps_any(b, geo_boxes)
                        and not _overlaps_any(b, underline_boxes)
                        and not _overlaps_any(b, checkbox_boxes)
                    )
                )
            # Checkboxes are typed directly; text/image boxes go through label analysis.
            for box in checkbox_boxes:
                label = _nearest_label(box, anchors)
                base = _checkbox_base_name(label)
                resolution = _resolve_auto_field_name(
                    base,
                    field_type="checkbox",
                    label=label,
                    used_names=used_names,
                    field_name_resolver=field_name_resolver,
                )
                specs.append(AutoFieldSpec(
                    page=box.page, name=resolution.name, field_type="checkbox",
                    x=round(box.x, 2), y=round(box.y, 2),
                    w=round(box.w, 2), h=round(box.h, 2),
                    label=label,
                    name_match_method=resolution.method,
                    name_matched_plan_t=resolution.matched,
                ))
            geo_boxes = _filter_fillable_text_boxes(geo_boxes, anchors)
            underline_boxes.extend(
                b for b in raw_underline_boxes
                if not _overlaps_any(b, geo_boxes) and not _overlaps_any(b, underline_boxes)
            )
            # Underscore runs are themselves a strong fillable-field signal
            # (for example: "Name ______, Number").  Keep them separate from
            # drawn table separators, which are filtered above.
            boxes = geo_boxes + underline_boxes
            for box in boxes:
                label = _nearest_label(box, anchors)
                is_signature = (
                    _is_signature_label(label)
                    or _bbox_has_signature_label_near_box(box, bbox_words_by_page.get(page_index, []))
                )
                base = _field_base_name(label, is_signature)
                field_type = "image" if is_signature else "text"
                resolution = _resolve_auto_field_name(
                    base,
                    field_type=field_type,
                    label=label,
                    used_names=used_names,
                    field_name_resolver=field_name_resolver,
                )
                h = min(box.h, MAX_FIELD_HEIGHT_PT)
                specs.append(
                    AutoFieldSpec(
                        page=box.page,
                        name=resolution.name,
                        field_type=field_type,
                        x=round(box.x, 2),
                        y=round(box.y, 2),
                        w=round(box.w, 2),
                        h=round(h, 2),
                        label=label,
                        name_match_method=resolution.method,
                        name_matched_plan_t=resolution.matched,
                    )
                )
    specs = _filter_specs_by_original_content(pdf_path, specs)
    contexts = _signature_contexts_from_pdf_text(pdf_path)
    if azure_layout is not None:
        contexts.extend(_signature_contexts_from_azure_layout(azure_layout))
    return _apply_signature_context_rows(specs, contexts)


def _should_use_azure_document_intelligence(value: bool | None) -> bool:
    if value is not None:
        return value
    return os.environ.get(AZURE_USE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def _load_azure_layout(pdf_path: str | Path, *, enabled: bool) -> AzureLayoutResult | None:
    if not enabled:
        return None

    endpoint = os.environ.get(AZURE_ENDPOINT_ENV, "").strip()
    key = os.environ.get(AZURE_KEY_ENV, "").strip()
    model_id = os.environ.get(AZURE_MODEL_ID_ENV, AZURE_DEFAULT_MODEL_ID).strip() or AZURE_DEFAULT_MODEL_ID

    if not endpoint or not key:
        return AzureLayoutResult(
            words_by_page={},
            anchors_by_page={},
            checkbox_boxes_by_page={},
            warnings=(
                f"Azure Document Intelligence skipped: set {AZURE_ENDPOINT_ENV} and {AZURE_KEY_ENV}.",
            ),
        )

    try:
        from azure.ai.documentintelligence import DocumentIntelligenceClient
        from azure.core.credentials import AzureKeyCredential
    except ImportError:
        return AzureLayoutResult(
            words_by_page={},
            anchors_by_page={},
            checkbox_boxes_by_page={},
            warnings=(
                "Azure Document Intelligence skipped: install with `python -m pip install -e .[azure]`.",
            ),
        )

    try:
        client = DocumentIntelligenceClient(endpoint=endpoint, credential=AzureKeyCredential(key))
        with Path(pdf_path).open("rb") as handle:
            try:
                poller = client.begin_analyze_document(
                    model_id,
                    body=handle,
                    content_type="application/pdf",
                )
            except TypeError:
                handle.seek(0)
                poller = client.begin_analyze_document(model_id, handle)
            result = poller.result()
    except Exception as exc:  # pragma: no cover - exact Azure exceptions vary by SDK/service
        return AzureLayoutResult(
            words_by_page={},
            anchors_by_page={},
            checkbox_boxes_by_page={},
            warnings=(f"Azure Document Intelligence skipped after service error: {exc}",),
        )

    layout = _azure_layout_from_result(pdf_path, result)
    if not layout.words_by_page and not layout.anchors_by_page and not layout.checkbox_boxes_by_page:
        return AzureLayoutResult(
            words_by_page={},
            anchors_by_page={},
            checkbox_boxes_by_page={},
            warnings=("Azure Document Intelligence returned no layout items.",),
        )
    return layout


def _azure_layout_from_result(pdf_path: str | Path, result: object) -> AzureLayoutResult:
    page_sizes = _pdf_page_sizes(pdf_path)
    words_by_page: dict[int, list[BBoxWord]] = {}
    anchors_by_page: dict[int, list[TextAnchor]] = {}
    checkbox_boxes_by_page: dict[int, list[DetectedBox]] = {}

    for fallback_index, azure_page in enumerate(getattr(result, "pages", []) or [], start=1):
        page_number = int(getattr(azure_page, "page_number", fallback_index) or fallback_index)
        pdf_w, pdf_h = page_sizes.get(page_number, (612.0, 792.0))
        source_w = float(getattr(azure_page, "width", pdf_w) or pdf_w)
        source_h = float(getattr(azure_page, "height", pdf_h) or pdf_h)

        for word in getattr(azure_page, "words", []) or []:
            text = str(getattr(word, "content", "") or "").strip()
            if not text:
                continue
            bounds = _azure_polygon_bounds(getattr(word, "polygon", None))
            if bounds is None:
                continue
            x0, y0, x1, y1 = _scale_azure_bounds(bounds, source_w, source_h, pdf_w, pdf_h)
            words_by_page.setdefault(page_number, []).append(
                BBoxWord(
                    page=page_number,
                    page_height=pdf_h,
                    text=text,
                    x0=x0,
                    y0=y0,
                    x1=x1,
                    y1=y1,
                )
            )
            anchors_by_page.setdefault(page_number, []).append(TextAnchor(text, x0, pdf_h - y1))

        for line in getattr(azure_page, "lines", []) or []:
            text = str(getattr(line, "content", "") or "").strip()
            if not text:
                continue
            bounds = _azure_polygon_bounds(getattr(line, "polygon", None))
            if bounds is None:
                continue
            x0, _y0, _x1, y1 = _scale_azure_bounds(bounds, source_w, source_h, pdf_w, pdf_h)
            anchors_by_page.setdefault(page_number, []).append(TextAnchor(text, x0, pdf_h - y1))

        for mark in getattr(azure_page, "selection_marks", []) or []:
            bounds = _azure_polygon_bounds(getattr(mark, "polygon", None))
            if bounds is None:
                continue
            x0, y0, x1, y1 = _scale_azure_bounds(bounds, source_w, source_h, pdf_w, pdf_h)
            width = x1 - x0
            height = y1 - y0
            if not (5.0 <= width <= 30.0 and 5.0 <= height <= 30.0):
                continue
            checkbox_boxes_by_page.setdefault(page_number, []).append(
                DetectedBox(
                    page=page_number,
                    x=round(x0, 1),
                    y=round(pdf_h - y1, 1),
                    w=round(width, 1),
                    h=round(height, 1),
                )
            )

    return AzureLayoutResult(
        words_by_page={page: sorted(words, key=lambda w: (w.y0, w.x0)) for page, words in words_by_page.items()},
        anchors_by_page=anchors_by_page,
        checkbox_boxes_by_page=checkbox_boxes_by_page,
    )


def _pdf_page_sizes(pdf_path: str | Path) -> dict[int, tuple[float, float]]:
    sizes: dict[int, tuple[float, float]] = {}
    with pikepdf.Pdf.open(str(pdf_path)) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            box = page.mediabox
            sizes[page_number] = (float(box[2]) - float(box[0]), float(box[3]) - float(box[1]))
    return sizes


def _azure_polygon_bounds(polygon: object) -> tuple[float, float, float, float] | None:
    if not polygon:
        return None

    xs: list[float] = []
    ys: list[float] = []
    values = list(polygon)
    for item in values:
        if hasattr(item, "x") and hasattr(item, "y"):
            xs.append(float(item.x))
            ys.append(float(item.y))
        elif isinstance(item, dict) and "x" in item and "y" in item:
            xs.append(float(item["x"]))
            ys.append(float(item["y"]))

    if not xs and len(values) >= 4 and len(values) % 2 == 0:
        try:
            numbers = [float(value) for value in values]
        except (TypeError, ValueError):
            numbers = []
        xs = numbers[0::2]
        ys = numbers[1::2]

    if not xs or not ys:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def _scale_azure_bounds(
    bounds: tuple[float, float, float, float],
    source_w: float,
    source_h: float,
    pdf_w: float,
    pdf_h: float,
) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = bounds
    source_w = source_w or pdf_w
    source_h = source_h or pdf_h
    return (
        x0 / source_w * pdf_w,
        y0 / source_h * pdf_h,
        x1 / source_w * pdf_w,
        y1 / source_h * pdf_h,
    )


def _signature_contexts_from_azure_layout(layout: AzureLayoutResult) -> list[SignatureContext]:
    contexts: list[SignatureContext] = []
    for page, anchors in layout.anchors_by_page.items():
        for anchor in anchors:
            direction = _signature_context_direction(anchor.text)
            if direction:
                contexts.append(SignatureContext(page=page, x0=anchor.x, x1=anchor.x, y=anchor.y, direction=direction))
    return contexts


def _filter_specs_by_original_content(pdf_path: str | Path, specs: list[AutoFieldSpec]) -> list[AutoFieldSpec]:
    parsed = [
        ParsedField(
            page=spec.page,
            name=spec.name,
            field_type=spec.field_type,
            x=spec.x,
            y=spec.y,
            w=spec.w,
            h=spec.h,
        )
        for spec in specs
    ]
    issues = _validate_original_content_overlap(pdf_path, parsed)
    blocked = {
        (issue.page, issue.field)
        for issue in issues
        if issue.code == "field-over-original-content" and issue.page is not None and issue.field
    }
    if not blocked:
        return specs
    return [spec for spec in specs if (spec.page, spec.name) not in blocked]


def _signature_contexts_from_pdf_text(pdf_path: str | Path) -> list[SignatureContext]:
    if shutil.which("pdftotext") is None:
        return []

    with tempfile.TemporaryDirectory() as temp_dir:
        bbox_path = Path(temp_dir) / "bbox.html"
        command = [
            "pdftotext",
            "-bbox-layout",
            "-enc",
            "UTF-8",
            str(pdf_path),
            str(bbox_path),
        ]
        try:
            subprocess.run(command, check=True, capture_output=True)
        except (OSError, subprocess.CalledProcessError):
            return []

        contexts: list[SignatureContext] = []
        page_number = 0
        page_height = 0.0
        for line in bbox_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            page_match = BBOX_PAGE_RE.search(line)
            if page_match:
                page_number += 1
                page_height = float(page_match.group("height"))
                continue

            word_match = BBOX_WORD_RE.search(line)
            if page_number < 1 or page_height <= 0 or word_match is None:
                continue
            word = html.unescape(word_match.group("text"))
            direction = _signature_context_direction(word)
            if not direction:
                continue
            y_pdf = page_height - float(word_match.group("y1"))
            contexts.append(
                SignatureContext(
                    page=page_number,
                    x0=float(word_match.group("x0")),
                    x1=float(word_match.group("x1")),
                    y=y_pdf,
                    direction=direction,
                )
            )
        return contexts


def _matches_signature_context_word(word: str) -> bool:
    return _signature_context_direction(word) is not None


def _signature_context_direction(word: str) -> str | None:
    cleaned = re.sub(r"[^\w\u0590-\u05FF]+", "", word).casefold()
    if not cleaned:
        return None
    reversed_cleaned = cleaned[::-1]
    hebrew_keywords = HEBREW_SIGNATURE_LEADING_WORDS + HEBREW_SIGNATURE_KEYWORDS
    if any(keyword in cleaned or keyword in reversed_cleaned for keyword in hebrew_keywords):
        return "rtl"
    if any(keyword.replace(" ", "") in cleaned for keyword in SIGNATURE_KEYWORDS):
        return "ltr"
    return None


def _apply_signature_context_rows(
    specs: list[AutoFieldSpec],
    contexts: list[SignatureContext | tuple[int, float]],
) -> list[AutoFieldSpec]:
    if not contexts:
        return specs

    signature_targets: set[tuple[int, float, float, str]] = set()
    for raw_context in contexts:
        context = _coerce_signature_context(raw_context)
        candidates = [
            spec for spec in specs
            if (
                spec.page == context.page
                and spec.field_type == "text"
                and spec.w >= 70
                and _signature_context_allows_spec(context, spec)
            )
        ]
        if not candidates:
            continue
        closest_distance = min(_signature_context_vertical_distance(context, spec) for spec in candidates)
        row = [
            spec for spec in candidates
            if abs(_signature_context_vertical_distance(context, spec) - closest_distance) <= 3
        ]
        if len(row) < 2 and not (len(row) == 1 and closest_distance <= 50):
            continue
        for spec in row:
            if _can_convert_to_signature_image(spec):
                signature_targets.add((spec.page, spec.x, spec.y, spec.name))

    updated: list[AutoFieldSpec] = []
    for spec in specs:
        if (spec.page, spec.x, spec.y, spec.name) in signature_targets and spec.field_type == "text":
            updated.append(AutoFieldSpec(
                page=spec.page,
                name=spec.name.replace("txt", "img", 1) if spec.name.startswith("txt") else f"img{spec.name}",
                field_type="image",
                x=spec.x,
                y=spec.y,
                w=spec.w,
                h=spec.h,
                label=spec.label,
                name_match_method=spec.name_match_method,
                name_matched_plan_t=spec.name_matched_plan_t,
            ))
        else:
            updated.append(spec)
    return updated


def _coerce_signature_context(context: SignatureContext | tuple[int, float]) -> SignatureContext:
    if isinstance(context, SignatureContext):
        return context
    page, y = context
    return SignatureContext(page=page, x0=float("-inf"), x1=float("inf"), y=y, direction="any")


def _signature_context_allows_spec(context: SignatureContext, spec: AutoFieldSpec) -> bool:
    if _signature_context_is_same_line(context, spec):
        if context.direction == "rtl":
            return 0 <= context.x0 - (spec.x + spec.w) <= 90
        if context.direction == "ltr":
            return 0 <= spec.x - context.x1 <= 90
        return True

    if _signature_context_vertical_distance(context, spec) > 50:
        return False

    horizontal_gap = max(spec.x - context.x1, context.x0 - (spec.x + spec.w), 0)
    return horizontal_gap <= 300


def _signature_context_is_same_line(context: SignatureContext, spec: AutoFieldSpec) -> bool:
    return abs(context.y - spec.y) <= 8


def _signature_context_vertical_distance(context: SignatureContext, spec: AutoFieldSpec) -> float:
    if spec.y <= context.y <= spec.y + spec.h:
        return 0
    return min(abs(context.y - spec.y), abs(context.y - (spec.y + spec.h)))


def _can_convert_to_signature_image(spec: AutoFieldSpec) -> bool:
    """Guard context-based signature conversion from consuming normal fields.

    A nearby word such as "Signature" can describe a whole section, but ordinary
    labels in that section ("Amount", "Date", "Name", "Title") must remain text.
    """
    label = spec.label.strip()
    if not _looks_like_text(label):
        return True
    if _is_signature_label(label):
        return True

    normalized = re.sub(r"[^a-z0-9\u0590-\u05FF]+", " ", label.casefold()).strip()
    tokens = set(normalized.split())
    if not tokens:
        return True
    return tokens <= {"by"}


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


def write_mapping_report(specs: list[AutoFieldSpec], report_path: str | Path) -> Path:
    path = Path(report_path)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "page",
                "name",
                "type",
                "x",
                "y",
                "w",
                "h",
                "detected_label",
                "plan_t_matched",
                "match_method",
            ]
        )
        for spec in specs:
            writer.writerow(
                [
                    spec.page,
                    spec.name,
                    spec.field_type,
                    spec.x,
                    spec.y,
                    spec.w,
                    spec.h,
                    spec.label,
                    "yes" if spec.name_matched_plan_t else "no",
                    spec.name_match_method,
                ]
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


def _detect_bbox_underline_boxes(pdf_path: str | Path) -> dict[int, list[DetectedBox]]:
    """Use Poppler's visual word boxes as a fallback for RTL/custom-font underlines."""
    xml_text = _read_bbox_xml(pdf_path)
    if not xml_text:
        return {}
    return _bbox_underline_boxes_from_xml(xml_text)


def _detect_bbox_checkbox_boxes(pdf_path: str | Path) -> dict[int, list[DetectedBox]]:
    """Use Poppler visual word boxes to find checkbox glyphs near text labels."""
    xml_text = _read_bbox_xml(pdf_path)
    if not xml_text:
        return {}
    return _bbox_checkbox_boxes_from_xml(xml_text)


def _read_bbox_xml(pdf_path: str | Path) -> str:
    if shutil.which("pdftotext") is None:
        return ""

    with tempfile.TemporaryDirectory() as temp_dir:
        bbox_path = Path(temp_dir) / "bbox.xml"
        command = [
            "pdftotext",
            "-bbox-layout",
            "-enc",
            "UTF-8",
            str(pdf_path),
            str(bbox_path),
        ]
        try:
            subprocess.run(command, check=True, capture_output=True)
        except (OSError, subprocess.CalledProcessError):
            return ""
        if not bbox_path.exists():
            return ""
        return bbox_path.read_text(encoding="utf-8", errors="ignore")


def _bbox_underline_boxes_from_xml(xml_text: str) -> dict[int, list[DetectedBox]]:
    return _bbox_underline_boxes_from_words(_bbox_words_by_page_from_xml(xml_text))


def _bbox_underline_boxes_from_words(words_by_page: dict[int, list[BBoxWord]]) -> dict[int, list[DetectedBox]]:
    boxes_by_page: dict[int, list[DetectedBox]] = {}
    for page, words in words_by_page.items():
        boxes: list[DetectedBox] = []
        for word in words:
            box = _bbox_word_underline_box(word)
            if box is None:
                continue
            if not _bbox_has_fillable_context(word, box, words):
                continue
            if not _overlaps_any(box, boxes):
                boxes.append(box)
        if boxes:
            boxes_by_page[page] = sorted(boxes, key=lambda b: (-b.y, b.x))
    return boxes_by_page


def _bbox_checkbox_boxes_from_xml(xml_text: str) -> dict[int, list[DetectedBox]]:
    return _bbox_checkbox_boxes_from_words(_bbox_words_by_page_from_xml(xml_text))


def _bbox_checkbox_boxes_from_words(words_by_page: dict[int, list[BBoxWord]]) -> dict[int, list[DetectedBox]]:
    boxes_by_page: dict[int, list[DetectedBox]] = {}
    for page, words in words_by_page.items():
        boxes: list[DetectedBox] = []
        candidates = [
            (word, box)
            for word in words
            if (box := _bbox_word_checkbox_box(word)) is not None
        ]
        candidate_boxes = [box for _word, box in candidates]
        for word, box in candidates:
            if not _bbox_has_label_context(word, words) and not _is_grouped_checkbox_box(box, candidate_boxes):
                continue
            if not _overlaps_any(box, boxes):
                boxes.append(box)
        if boxes:
            boxes_by_page[page] = sorted(boxes, key=lambda b: (-b.y, b.x))
    return boxes_by_page


def _bbox_words_by_page_from_xml(xml_text: str) -> dict[int, list[BBoxWord]]:
    words_by_page: dict[int, list[BBoxWord]] = {}
    current_page = 0
    current_height = 0.0

    for line in xml_text.splitlines():
        page_match = BBOX_PAGE_RE.search(line)
        if page_match:
            current_page += 1
            current_height = float(page_match.group("height"))
            words_by_page.setdefault(current_page, [])
            continue

        word_match = BBOX_WORD_RE.search(line)
        if not word_match or current_page == 0:
            continue
        words_by_page.setdefault(current_page, []).append(
            BBoxWord(
                page=current_page,
                page_height=current_height,
                text=html.unescape(word_match.group("text")),
                x0=float(word_match.group("x0")),
                y0=float(word_match.group("y0")),
                x1=float(word_match.group("x1")),
                y1=float(word_match.group("y1")),
            )
        )
    return words_by_page


def _bbox_text_anchors_from_words(words: list[BBoxWord]) -> list[TextAnchor]:
    anchors: list[TextAnchor] = []
    for word in words:
        text = word.text.strip()
        if not text or _text_is_fill_line(text):
            continue
        anchors.append(TextAnchor(text, word.x0, word.page_height - word.y1))
    return anchors


def _text_is_fill_line(text: str) -> bool:
    longest_underscore = max((len(match.group(0)) for match in re.finditer(r"_+", text)), default=0)
    return longest_underscore >= 8 and longest_underscore / max(len(text), 1) >= 0.45


def _bbox_word_checkbox_box(word: BBoxWord) -> DetectedBox | None:
    if word.text.strip() not in VISUAL_CHECKBOX_GLYPHS:
        return None
    width = word.x1 - word.x0
    height = word.y1 - word.y0
    if not (VISUAL_CHECKBOX_MIN_PT <= width <= VISUAL_CHECKBOX_MAX_PT):
        return None
    if not (VISUAL_CHECKBOX_MIN_PT <= height <= VISUAL_CHECKBOX_MAX_PT):
        return None
    if abs(width - height) / max(width, height) > 0.35:
        return None
    return DetectedBox(
        page=word.page,
        x=round(word.x0, 1),
        y=round(word.page_height - word.y1, 1),
        w=round(width, 1),
        h=round(height, 1),
    )


def _is_grouped_checkbox_box(box: DetectedBox, boxes: list[DetectedBox]) -> bool:
    return any(
        other is not box and _checkbox_boxes_are_grouped(box, other)
        for other in boxes
    )


def _checkbox_boxes_are_grouped(a: DetectedBox, b: DetectedBox) -> bool:
    if a.page != b.page:
        return False
    if abs(a.w - b.w) > 3 or abs(a.h - b.h) > 3:
        return False

    same_column = abs(a.x - b.x) <= max(4.0, min(a.w, b.w) * 0.5)
    vertical_gap = max(a.y - (b.y + b.h), b.y - (a.y + a.h), 0)
    if same_column and 0 < vertical_gap <= 40:
        return True

    same_row = abs(a.y - b.y) <= max(4.0, min(a.h, b.h) * 0.5)
    horizontal_gap = max(a.x - (b.x + b.w), b.x - (a.x + a.w), 0)
    return same_row and 0 < horizontal_gap <= 50


def _bbox_word_underline_box(word: BBoxWord) -> DetectedBox | None:
    matches = list(re.finditer(r"_+", word.text))
    if not matches:
        return None
    match = max(matches, key=lambda item: item.end() - item.start())
    underline_len = match.end() - match.start()
    if underline_len < 8:
        return None

    text_len = max(len(word.text), 1)
    char_w = (word.x1 - word.x0) / text_len
    field_x = word.x0 + match.start() * char_w
    field_w = underline_len * char_w
    if field_w < MIN_BOX_WIDTH_PT:
        return None

    return DetectedBox(
        page=word.page,
        x=round(field_x, 1),
        y=round(word.page_height - word.y1, 1),
        w=round(field_w, 1),
        h=UNDERLINE_FIELD_HEIGHT_PT,
    )


def _bbox_has_fillable_context(source: BBoxWord, box: DetectedBox, words: list[BBoxWord]) -> bool:
    source_context = re.sub(r"_+", "", source.text)
    if _effective_text_len(source_context) > 0:
        return True
    if _bbox_has_signature_label_near_box(box, words):
        return True

    for word in words:
        if word is source or "_" in word.text:
            continue
        if _effective_text_len(word.text) == 0:
            continue
        if not _bbox_words_share_line(source, word):
            continue
        gap = max(box.x - word.x1, word.x0 - (box.x + box.w), 0)
        if gap <= 220:
            return True
    return False


def _bbox_has_signature_label_near_box(box: DetectedBox, words: list[BBoxWord]) -> bool:
    for word in words:
        if "_" in word.text or _effective_text_len(word.text) == 0:
            continue
        if not _bbox_word_is_signature_label(word.text):
            continue
        if _bbox_signature_word_allows_box(word, box):
            return True
    return False


def _bbox_word_is_signature_label(text: str) -> bool:
    return _bbox_word_signature_direction(text) is not None


def _bbox_word_signature_direction(text: str) -> str | None:
    cleaned = re.sub(r"[^\w\u0590-\u05FF]+", "", text).casefold()
    if not cleaned:
        return None
    reversed_cleaned = cleaned[::-1]
    if cleaned in HEBREW_SIGNATURE_LEADING_WORDS or reversed_cleaned in HEBREW_SIGNATURE_LEADING_WORDS:
        return "rtl"
    if any(keyword.replace(" ", "") in cleaned for keyword in SIGNATURE_KEYWORDS):
        return "ltr"
    return None


def _bbox_signature_word_allows_box(word: BBoxWord, box: DetectedBox) -> bool:
    if _bbox_word_gap_to_pdf_box(word, box) > 45:
        return False

    direction = _bbox_word_signature_direction(word.text)
    if direction is None:
        return False
    if not _bbox_word_is_same_line_as_pdf_box(word, box):
        return True
    if direction == "rtl":
        return 0 <= word.x0 - (box.x + box.w) <= 90
    if direction == "ltr":
        return 0 <= box.x - word.x1 <= 90
    return False


def _bbox_word_gap_to_pdf_box(word: BBoxWord, box: DetectedBox) -> float:
    box_x0 = box.x
    box_x1 = box.x + box.w
    box_y0 = word.page_height - (box.y + box.h)
    box_y1 = word.page_height - box.y
    horizontal_gap = max(box_x0 - word.x1, word.x0 - box_x1, 0)
    vertical_gap = max(box_y0 - word.y1, word.y0 - box_y1, 0)
    return max(horizontal_gap, vertical_gap)


def _bbox_word_is_same_line_as_pdf_box(word: BBoxWord, box: DetectedBox) -> bool:
    box_y0 = word.page_height - (box.y + box.h)
    box_y1 = word.page_height - box.y
    vertical_overlap = min(box_y1, word.y1) - max(box_y0, word.y0)
    return vertical_overlap >= 2


def _bbox_has_label_context(source: BBoxWord, words: list[BBoxWord]) -> bool:
    for word in words:
        if word is source or word.text.strip() in VISUAL_CHECKBOX_GLYPHS:
            continue
        if _effective_text_len(word.text) == 0:
            continue
        if not _bbox_words_share_line(source, word):
            continue
        gap = max(source.x0 - word.x1, word.x0 - source.x1, 0)
        if gap <= 260:
            return True
    return False


def _bbox_words_share_line(a: BBoxWord, b: BBoxWord) -> bool:
    vertical_overlap = min(a.y1, b.y1) - max(a.y0, b.y0)
    return vertical_overlap >= min(a.y1 - a.y0, b.y1 - b.y0) * 0.5


def _detect_underline_boxes(page: pikepdf.Page, page_index: int) -> list[DetectedBox]:
    """Detect fields rendered as underscore-character runs in TJ/Tj operators.

    XFA forms sometimes render input areas as a sequence of '_' characters
    (e.g. 'Signature: _______') rather than as drawn rectangles.  We locate
    every underscore run, compute its page-space x/width using the exact glyph
    advance from the embedded font (Widths array), falling back to 0.556em.
    Td/TD offsets are scaled by the Tm scale factor for correct page coordinates.
    """
    # Pre-build font metrics.  Some Hebrew PDFs encode visual underlines as a
    # repeated CID glyph rather than the ASCII "_" character.
    font_metrics = _font_metrics(page)

    boxes: list[DetectedBox] = []
    tm_a = tm_d = 1.0
    tm_e = tm_f = 0.0
    page_tx = page_ty = 0.0
    tf_size = 10.0
    tf_name: str | None = None

    for token in pikepdf.parse_content_stream(page):
        op = str(token.operator)

        if op == "BT":
            tm_a = tm_d = 1.0
            tm_e = tm_f = page_tx = page_ty = 0.0
            tf_size = 10.0
        elif op == "Tf":
            try:
                tf_name = str(token.operands[0])
                tf_size = float(token.operands[1])
            except (TypeError, ValueError, IndexError):
                pass
        elif op == "Tm" and len(token.operands) == 6:
            tm_a = float(token.operands[0])
            tm_d = float(token.operands[3])
            tm_e = float(token.operands[4])
            tm_f = float(token.operands[5])
            page_tx = tm_e
            page_ty = tm_f
        elif op in ("Td", "TD") and len(token.operands) == 2:
            # Td/TD arguments are in text space; multiply by Tm scale for page coords.
            page_tx += float(token.operands[0]) * tm_a
            page_ty += float(token.operands[1]) * tm_d
        elif op in ("TJ", "Tj"):
            txt = _decode_text_op(token)
            # Exact glyph advance from font Widths, fallback to 0.556 em (Arial standard).
            widths, default_advance_em = font_metrics.get(tf_name or "", ({}, 0.556))
            adv_em = widths.get(95, default_advance_em)
            char_w = adv_em * tf_size * abs(tm_a)
            if char_w > 0 and "_" in txt:
                for match in re.finditer(r"_+", txt):
                    under_idx = match.start()
                    n_under = match.end() - under_idx
                    if n_under < 10:  # skip short decorative underscores
                        continue
                    if txt[:under_idx].strip() == ".":
                        continue
                    field_x = page_tx + under_idx * char_w
                    field_w = n_under * char_w
                    if field_w < MIN_BOX_WIDTH_PT:
                        continue
                    boxes.append(DetectedBox(page_index, round(field_x, 1), round(page_ty, 1),
                                             round(field_w, 1), UNDERLINE_FIELD_HEIGHT_PT))
            raw = _raw_text_op_bytes(token)
            units = _cid_units(raw)
            if not units:
                continue
            for start, length in _repeated_cid_runs(units):
                if _cid_prefix_is_dot(units, start):
                    continue
                field_x = page_tx + _units_advance_em(units[:start], widths, default_advance_em) * tf_size * abs(tm_a)
                field_w = _units_advance_em(units[start:start + length], widths, default_advance_em) * tf_size * abs(tm_a)
                if field_w < MIN_BOX_WIDTH_PT:
                    continue
                boxes.append(DetectedBox(page_index, round(field_x, 1), round(page_ty, 1),
                                         round(field_w, 1), UNDERLINE_FIELD_HEIGHT_PT))

    return boxes


def _font_metrics(page: pikepdf.Page) -> dict[str, tuple[dict[int, float], float]]:
    metrics: dict[str, tuple[dict[int, float], float]] = {}
    res = page.resources
    if "/Font" not in res:
        return metrics

    for fname, fobj in res["/Font"].items():
        widths: dict[int, float] = {}
        default_advance_em = 0.556
        try:
            first_char = int(fobj.get("/FirstChar", 0))
            for offset, width in enumerate(fobj.get("/Widths", [])):
                widths[first_char + offset] = float(width) / 1000.0
        except Exception:
            pass

        try:
            if "/DescendantFonts" in fobj:
                descendant = fobj["/DescendantFonts"][0]
                default_advance_em = float(descendant.get("/DW", 1000)) / 1000.0
                _add_cid_widths(widths, descendant.get("/W", []))
        except Exception:
            pass

        metrics[str(fname)] = (widths, default_advance_em)
    return metrics


def _add_cid_widths(widths: dict[int, float], width_array) -> None:
    i = 0
    while i < len(width_array):
        first = int(width_array[i])
        second = width_array[i + 1]
        if isinstance(second, pikepdf.Array):
            for offset, width in enumerate(second):
                widths[first + offset] = float(width) / 1000.0
            i += 2
        else:
            last = int(second)
            width = float(width_array[i + 2]) / 1000.0
            for code in range(first, last + 1):
                widths[code] = width
            i += 3


def _raw_text_op_bytes(token) -> bytes:
    op = str(token.operator)
    if op == "Tj":
        try:
            return bytes(token.operands[0])
        except Exception:
            return b""

    chunks: list[bytes] = []
    for item in token.operands[0]:
        if isinstance(item, (int, float, decimal.Decimal)):
            continue
        try:
            chunks.append(bytes(item))
        except Exception:
            pass
    return b"".join(chunks)


def _cid_units(raw: bytes) -> list[int] | None:
    if len(raw) < 20 or len(raw) % 2:
        return None
    units = [int.from_bytes(raw[index:index + 2], "big") for index in range(0, len(raw), 2)]
    zero_high_bytes = sum(1 for index in range(0, len(raw), 2) if raw[index] == 0)
    if zero_high_bytes / len(units) < 0.5:
        return None
    return units


def _repeated_cid_runs(units: list[int]) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    start = 0
    while start < len(units):
        end = start + 1
        while end < len(units) and units[end] == units[start]:
            end += 1
        if end - start >= 10 and units[start] not in (0, 32):
            runs.append((start, end - start))
        start = end
    return runs


def _cid_prefix_is_dot(units: list[int], run_start: int) -> bool:
    meaningful = [unit for unit in units[:run_start] if unit not in (0, 32)]
    return meaningful == [ord(".")]


def _units_advance_em(units: list[int], widths: dict[int, float], default_advance_em: float) -> float:
    return sum(widths.get(unit, default_advance_em) for unit in units)


def _decode_text_op(token) -> str:
    """Return the text content of a Tj or TJ operator as a latin-1 string."""
    op = str(token.operator)
    if op == "Tj":
        try:
            return bytes(token.operands[0]).decode("latin-1", "ignore")
        except Exception:
            return ""
    # TJ — array of strings and kerning numbers
    parts: list[str] = []
    for item in token.operands[0]:
        if isinstance(item, (int, float, decimal.Decimal)):
            continue
        try:
            parts.append(bytes(item).decode("latin-1", "ignore"))
        except Exception:
            pass
    return "".join(parts)


def _overlaps_any(box: DetectedBox, others: list[DetectedBox]) -> bool:
    """Return True if box overlaps in both x and y with any box in others."""
    for o in others:
        x_overlap = box.x < o.x + o.w and box.x + box.w > o.x
        y_overlap = box.y < o.y + o.h and box.y + box.h > o.y
        if x_overlap and y_overlap:
            return True
    return False


def _detect_checkbox_boxes(page: pikepdf.Page, page_index: int) -> list[DetectedBox]:
    """Detect small approximately-square rectangles as checkbox fields."""
    checkboxes: list[tuple[float, float, float, float]] = []
    line_segments: list[tuple[float, float, float, float]] = []
    pending: list[tuple[float, float, float, float]] = []
    clip_pending: list[tuple[float, float, float, float]] = []
    last_clip_rects: list[tuple[float, float, float, float]] = []
    current_path: list[tuple[float, float]] = []
    graphics_matrix = IDENTITY_GRAPHICS_MATRIX
    graphics_stack: list[GraphicsMatrix] = []

    for token in pikepdf.parse_content_stream(page):
        op = str(token.operator)

        if op == "q":
            graphics_stack.append(graphics_matrix)
        elif op == "Q":
            graphics_matrix = graphics_stack.pop() if graphics_stack else IDENTITY_GRAPHICS_MATRIX
            current_path = []
        elif op == "cm" and len(token.operands) == 6:
            try:
                matrix: GraphicsMatrix = (
                    float(token.operands[0]),
                    float(token.operands[1]),
                    float(token.operands[2]),
                    float(token.operands[3]),
                    float(token.operands[4]),
                    float(token.operands[5]),
                )
            except (TypeError, ValueError):
                continue
            graphics_matrix = _concat_graphics_matrix(graphics_matrix, matrix)

        elif op == "re":
            try:
                x, y, w, h = (float(v) for v in token.operands)
            except (TypeError, ValueError):
                continue
            x, y, w, h = _transform_rect(graphics_matrix, x, y, w, h)
            if w < 0: x, w = x + w, -w
            if h < 0: y, h = y + h, -h
            if (MIN_CHECKBOX_PT <= w <= MAX_CHECKBOX_PT
                    and MIN_CHECKBOX_PT <= h <= MAX_CHECKBOX_PT
                    and abs(w - h) / max(w, h) < 0.3):
                pending.append((round(x, 1), round(y, 1), round(w, 1), round(h, 1)))

        elif op == "m" and len(token.operands) == 2:
            try:
                x, y = (float(v) for v in token.operands)
            except (TypeError, ValueError):
                current_path = []
                continue
            x, y = _transform_point(graphics_matrix, x, y)
            current_path = [(x, y)]
        elif op == "l" and len(token.operands) == 2:
            if not current_path:
                continue
            try:
                x, y = (float(v) for v in token.operands)
            except (TypeError, ValueError):
                current_path = []
                continue
            x, y = _transform_point(graphics_matrix, x, y)
            current_path.append((x, y))
        elif op == "h":
            # The stroke/fill operator will consume the path. Keeping the
            # points is enough; the close-path segment is implicit.
            pass
        elif op == "Do":
            for r in last_clip_rects:
                if r in checkboxes:
                    checkboxes.remove(r)
            last_clip_rects.clear()

        elif op in ("S", "s", "f", "F", "f*", "B", "B*", "b", "b*"):
            line_segment = _line_path_segment(current_path)
            if line_segment is not None:
                line_segments.append(line_segment)
            line_box = _line_path_checkbox(current_path)
            if line_box is not None:
                pending.append(line_box)
            checkboxes.extend(pending)
            pending.clear()
            clip_pending.clear()
            last_clip_rects.clear()
            current_path = []
        elif op in ("W", "W*"):
            clip_pending = list(pending)
            pending.clear()
            current_path = []
        elif op == "n":
            checkboxes.extend(clip_pending)
            last_clip_rects = list(clip_pending)
            clip_pending.clear()
            pending.clear()
            current_path = []

    checkboxes.extend(_checkboxes_from_line_segments(line_segments))
    unique = sorted(set(checkboxes), key=lambda r: (-r[1], r[0]))
    return [DetectedBox(page_index, x, y, w, h) for (x, y, w, h) in unique]


def _detect_glyph_checkbox_boxes(page: pikepdf.Page, page_index: int) -> list[DetectedBox]:
    """Detect checkboxes rendered as a font glyph rather than vector paths."""
    boxes: list[DetectedBox] = []
    tm_a = tm_d = 1.0
    page_tx = page_ty = 0.0
    tf_size = 10.0

    for token in pikepdf.parse_content_stream(page):
        op = str(token.operator)

        if op == "BT":
            tm_a = tm_d = 1.0
            page_tx = page_ty = 0.0
            tf_size = 10.0
        elif op == "Tf":
            try:
                tf_size = float(token.operands[1])
            except (TypeError, ValueError, IndexError):
                pass
        elif op == "Tm" and len(token.operands) == 6:
            tm_a = float(token.operands[0])
            tm_d = float(token.operands[3])
            page_tx = float(token.operands[4])
            page_ty = float(token.operands[5])
        elif op in ("Td", "TD") and len(token.operands) == 2:
            page_tx += float(token.operands[0]) * tm_a
            page_ty += float(token.operands[1]) * tm_d
        elif op in ("TJ", "Tj"):
            text = _decode_text_op(token)
            if not any(glyph in text for glyph in CHECKBOX_GLYPHS):
                continue
            size = abs(tm_d) * tf_size
            if not (MIN_CHECKBOX_PT <= size <= MAX_CHECKBOX_PT):
                size = max(MIN_CHECKBOX_PT, min(MAX_CHECKBOX_PT, size))
            boxes.append(
                DetectedBox(
                    page_index,
                    round(page_tx, 1),
                    round(page_ty, 1),
                    round(size, 1),
                    round(size, 1),
                )
            )

    return boxes


def _line_path_checkbox(points: list[tuple[float, float]]) -> tuple[float, float, float, float] | None:
    if len(points) < 4:
        return None
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    x = min(xs)
    y = min(ys)
    w = max(xs) - x
    h = max(ys) - y
    if not (MIN_CHECKBOX_PT <= w <= MAX_CHECKBOX_PT and MIN_CHECKBOX_PT <= h <= MAX_CHECKBOX_PT):
        return None
    if abs(w - h) / max(w, h) >= 0.3:
        return None

    corners = {
        (round(x, 1), round(y, 1)),
        (round(x + w, 1), round(y, 1)),
        (round(x + w, 1), round(y + h, 1)),
        (round(x, 1), round(y + h, 1)),
    }
    actual = {(round(px, 1), round(py, 1)) for px, py in points}
    if not corners.issubset(actual):
        return None
    return (round(x, 1), round(y, 1), round(w, 1), round(h, 1))


def _line_path_segment(points: list[tuple[float, float]]) -> tuple[float, float, float, float] | None:
    if len(points) != 2:
        return None
    (x1, y1), (x2, y2) = points
    if abs(x1 - x2) <= 0.5 and abs(y1 - y2) >= MIN_CHECKBOX_PT:
        x = (x1 + x2) / 2
        return (round(x, 1), round(min(y1, y2), 1), round(x, 1), round(max(y1, y2), 1))
    if abs(y1 - y2) <= 0.5 and abs(x1 - x2) >= MIN_CHECKBOX_PT:
        y = (y1 + y2) / 2
        return (round(min(x1, x2), 1), round(y, 1), round(max(x1, x2), 1), round(y, 1))
    return None


def _checkboxes_from_line_segments(
    segments: list[tuple[float, float, float, float]],
) -> list[tuple[float, float, float, float]]:
    horizontal = [segment for segment in segments if abs(segment[1] - segment[3]) <= 0.5]
    vertical = [segment for segment in segments if abs(segment[0] - segment[2]) <= 0.5]
    boxes: list[tuple[float, float, float, float]] = []

    for bottom in horizontal:
        bx0, by, bx1, _ = bottom
        for top in horizontal:
            tx0, ty, tx1, _ = top
            if ty <= by:
                continue
            h = ty - by
            w = bx1 - bx0
            if not (MIN_CHECKBOX_PT <= w <= MAX_CHECKBOX_PT and MIN_CHECKBOX_PT <= h <= MAX_CHECKBOX_PT):
                continue
            if abs(w - h) / max(w, h) >= 0.3:
                continue
            if abs(tx0 - bx0) > 1.0 or abs(tx1 - bx1) > 1.0:
                continue
            left = _has_matching_vertical_segment(vertical, bx0, by, ty)
            right = _has_matching_vertical_segment(vertical, bx1, by, ty)
            if left and right:
                boxes.append((round(bx0, 1), round(by, 1), round(w, 1), round(h, 1)))
    return boxes


def _has_matching_vertical_segment(
    segments: list[tuple[float, float, float, float]],
    x: float,
    y0: float,
    y1: float,
) -> bool:
    for segment in segments:
        sx, sy0, _sx2, sy1 = segment
        if abs(sx - x) <= 1.0 and abs(sy0 - y0) <= 1.0 and abs(sy1 - y1) <= 1.0:
            return True
    return False


def _detect_boxes(page: pikepdf.Page, page_index: int) -> list[DetectedBox]:
    clip_rects: list[tuple[float, float, float, float]] = []
    underline_candidates: list[tuple[float, float, float, float]] = []
    pending: list[tuple[float, float, float, float]] = []
    underline_pending: list[tuple[float, float, float, float]] = []
    clip_pending: list[tuple[float, float, float, float]] = []
    last_clip_rects: list[tuple[float, float, float, float]] = []
    current_path: list[tuple[float, float]] = []
    fill_is_white = False  # PDF default fill colour is black
    graphics_matrix = IDENTITY_GRAPHICS_MATRIX
    graphics_stack: list[GraphicsMatrix] = []

    for token in pikepdf.parse_content_stream(page):
        op = str(token.operator)

        if op == "q":
            graphics_stack.append(graphics_matrix)
        elif op == "Q":
            graphics_matrix = graphics_stack.pop() if graphics_stack else IDENTITY_GRAPHICS_MATRIX
            current_path = []
        elif op == "cm" and len(token.operands) == 6:
            try:
                matrix: GraphicsMatrix = (
                    float(token.operands[0]),
                    float(token.operands[1]),
                    float(token.operands[2]),
                    float(token.operands[3]),
                    float(token.operands[4]),
                    float(token.operands[5]),
                )
            except (TypeError, ValueError):
                continue
            graphics_matrix = _concat_graphics_matrix(graphics_matrix, matrix)

        # Track fill colour so we can skip shaded header rows.
        elif op == "g":  # grayscale fill: 1.0 = white
            try:
                fill_is_white = float(token.operands[0]) > 0.9
            except (TypeError, ValueError, IndexError):
                pass
        elif op == "rg":  # RGB fill
            try:
                r, g, b = (float(token.operands[i]) for i in range(3))
                fill_is_white = r > 0.9 and g > 0.9 and b > 0.9
            except (TypeError, ValueError, IndexError):
                pass
        elif op == "k":  # CMYK fill: 0 0 0 0 = white
            try:
                c, m, y, k = (float(token.operands[i]) for i in range(4))
                fill_is_white = c < 0.1 and m < 0.1 and y < 0.1 and k < 0.1
            except (TypeError, ValueError, IndexError):
                pass

        elif op == "re":
            last_clip_rects.clear()
            current_path = []
            try:
                x, y, w, h = (float(v) for v in token.operands)
            except (TypeError, ValueError):
                continue
            x, y, w, h = _transform_rect(graphics_matrix, x, y, w, h)
            if w < 0:
                x, w = x + w, -w
            if h < 0:
                y, h = y + h, -h
            if w >= MIN_BOX_WIDTH_PT:
                if MIN_BOX_HEIGHT_PT <= h <= MAX_BOX_HEIGHT_PT:
                    pending.append((round(x, 1), round(y, 1), round(w, 1), round(h, 1)))
                elif 0 < h < MIN_BOX_HEIGHT_PT:
                    # Thin rect — candidate for a standalone underline field.
                    underline_pending.append((round(x, 1), round(y, 1), round(w, 1)))

        elif op == "m" and len(token.operands) == 2:
            try:
                x, y = (float(v) for v in token.operands)
            except (TypeError, ValueError):
                current_path = []
                continue
            x, y = _transform_point(graphics_matrix, x, y)
            current_path = [(x, y)]
        elif op == "l" and len(token.operands) == 2:
            if not current_path:
                continue
            try:
                x, y = (float(v) for v in token.operands)
            except (TypeError, ValueError):
                current_path = []
                continue
            x, y = _transform_point(graphics_matrix, x, y)
            current_path.append((x, y))
        elif op == "h":
            pass

        elif op == "Do":
            # Image XObject rendered inside a clip path — discard the clip rects
            # that were just accepted, since this is a logo/image area, not a field.
            for r in last_clip_rects:
                if r in clip_rects:
                    clip_rects.remove(r)
            last_clip_rects.clear()

        elif op in ("S", "s"):
            # Stroke only — always an input border, no fill involved.
            line = _line_path_underline(current_path)
            if line is not None:
                underline_candidates.append(line)
            current_path = []
            clip_rects.extend(pending)
            pending.clear()
            clip_pending.clear()
            last_clip_rects.clear()
            current_path = []
            underline_candidates.extend(underline_pending)
            underline_pending.clear()
        elif op in ("f", "F", "f*"):
            # Fill only — keep if white (blank field background), skip if coloured header.
            if fill_is_white:
                clip_rects.extend(pending)
            pending.clear()
            clip_pending.clear()
            last_clip_rects.clear()
            current_path = []
            underline_candidates.extend(underline_pending)
            underline_pending.clear()
        elif op in ("B", "B*", "b", "b*"):
            # Fill + stroke — keep only if fill is white.
            if fill_is_white:
                clip_rects.extend(pending)
            pending.clear()
            clip_pending.clear()
            last_clip_rects.clear()
            current_path = []
            underline_candidates.extend(underline_pending)
            underline_pending.clear()
        elif op in ("W", "W*"):
            # Clip path — XFA-stripped PDFs use re W n to mark each field area.
            clip_pending = list(pending)
            pending.clear()
            underline_pending.clear()
            current_path = []
        elif op == "n":
            # End path without fill/stroke. If preceded by W/W* this is a clip-only
            # area (re W n), which XFA uses for every rendered field — treat as input.
            clip_rects.extend(clip_pending)
            last_clip_rects = list(clip_pending)
            clip_pending.clear()
            pending.clear()
            current_path = []

    # Keep only underlines that don't overlap with an existing clip-path field.
    standalone_underlines: list[tuple[float, float, float, float]] = []
    for ux, uy, uw in set(underline_candidates):
        covered = any(
            abs(ux - cx) <= 5 and abs(uw - cw) <= 5
            and cy - MAX_FIELD_HEIGHT_PT <= uy <= cy + ch + MAX_FIELD_HEIGHT_PT
            for cx, cy, cw, ch in clip_rects
        )
        if not covered:
            standalone_underlines.append((ux, uy, uw, UNDERLINE_FIELD_HEIGHT_PT))

    all_rects = clip_rects + standalone_underlines
    unique = sorted(set(all_rects), key=lambda r: (-r[1], r[0]))
    return [DetectedBox(page_index, x, y, w, h) for (x, y, w, h) in unique]


def _line_path_underline(points: list[tuple[float, float]]) -> tuple[float, float, float] | None:
    if len(points) != 2:
        return None
    (x1, y1), (x2, y2) = points
    if abs(y1 - y2) > 1:
        return None
    x = min(x1, x2)
    w = abs(x2 - x1)
    if w < MIN_BOX_WIDTH_PT:
        return None
    return (round(x, 1), round((y1 + y2) / 2, 1), round(w, 1))


def _extract_text_anchors(page: pikepdf.Page) -> list[TextAnchor]:
    anchors: list[TextAnchor] = []
    tm_a = tm_d = 1.0
    tx = ty = 0.0
    for token in pikepdf.parse_content_stream(page):
        op = str(token.operator)
        if op == "BT":
            # Text matrix resets to the identity at the start of each text object.
            tm_a = tm_d = 1.0
            tx = ty = 0.0
        elif op in ("Tm", "Td", "TD"):
            try:
                values = [float(v) for v in token.operands]
            except (TypeError, ValueError):
                continue
            if op == "Tm" and len(values) == 6:
                tm_a = values[0]
                tm_d = values[3]
                tx, ty = values[4], values[5]
            elif len(values) == 2:
                tx += values[0] * tm_a
                ty += values[1] * tm_d
        elif op == "Tj" and token.operands:
            if _text_op_is_fill_line_anchor(token):
                continue
            text = _operand_text(token.operands[0])
            if text.strip():
                anchors.append(TextAnchor(text.strip(), tx, ty))
        elif op == "TJ" and token.operands:
            if _text_op_is_fill_line_anchor(token):
                continue
            parts = [
                _operand_text(item)
                for item in token.operands[0]
                if not isinstance(item, (int, float, decimal.Decimal))
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


def _text_op_is_fill_line_anchor(token) -> bool:
    text = _decode_text_op(token)
    longest_underscore = max((len(match.group(0)) for match in re.finditer(r"_+", text)), default=0)
    if longest_underscore >= 10 and longest_underscore / max(len(text), 1) >= 0.45:
        return True

    units = _cid_units(_raw_text_op_bytes(token))
    if not units:
        return False
    longest_cid_run = max((length for _, length in _repeated_cid_runs(units)), default=0)
    return longest_cid_run >= 10 and longest_cid_run / len(units) >= 0.45


def _box_contains_text(box: DetectedBox, anchors: list[TextAnchor]) -> bool:
    """Return True if the box contains or overlaps with text anchors."""
    for anchor in anchors:
        if box.x <= anchor.x <= box.x + box.w and box.y <= anchor.y <= box.y + box.h:
            return True
    return False


def _is_fillable_text_box(box: DetectedBox, anchors: list[TextAnchor]) -> bool:
    """Return True when a text/image candidate looks like a blank fill area.

    Underlined headings and table row separators can look like input fields in
    the PDF drawing operators. Require a nearby label before, after, or under
    the blank area, and reject candidates with paragraph-like content directly
    under the line.
    """
    if _looks_like_full_row_separator(box):
        return False
    if _has_conflicting_text_content(box, anchors):
        return False
    return _has_fillable_label_context(box, anchors)


def _filter_fillable_text_boxes(
    boxes: list[DetectedBox],
    anchors: list[TextAnchor],
) -> list[DetectedBox]:
    direct = [box for box in boxes if _is_fillable_text_box(box, anchors)]
    accepted = list(direct)

    for box in boxes:
        if box in direct or _looks_like_full_row_separator(box) or _has_conflicting_text_content(box, anchors):
            continue
        if _has_fillable_row_peer(box, direct, boxes, anchors):
            accepted.append(box)
            continue
        if _has_immediate_above_label_context(box, anchors):
            accepted.append(box)

    for box in boxes:
        if box in accepted or _looks_like_full_row_separator(box) or _has_conflicting_text_content(box, anchors):
            continue
        if _is_blank_repeating_table_input_cell(box, boxes, anchors):
            accepted.append(box)

    return _dedupe_similar_boxes(accepted)


def _dedupe_similar_boxes(boxes: list[DetectedBox]) -> list[DetectedBox]:
    unique: list[DetectedBox] = []
    for box in boxes:
        if any(_boxes_are_near_duplicates(box, existing) for existing in unique):
            continue
        unique.append(box)
    return unique


def _boxes_are_near_duplicates(a: DetectedBox, b: DetectedBox) -> bool:
    if a.page != b.page:
        return False
    if abs(a.x - b.x) > 2 or abs(a.y - b.y) > 2:
        return False
    if abs(a.w - b.w) > 3 or abs(a.h - b.h) > 3:
        return False
    return _box_overlap_ratio(a, b) >= 0.9


def _box_overlap_ratio(a: DetectedBox, b: DetectedBox) -> float:
    x0 = max(a.x, b.x)
    y0 = max(a.y, b.y)
    x1 = min(a.x + a.w, b.x + b.w)
    y1 = min(a.y + a.h, b.y + b.h)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    intersection = (x1 - x0) * (y1 - y0)
    smaller_area = min(a.w * a.h, b.w * b.h)
    if smaller_area <= 0:
        return 0.0
    return intersection / smaller_area


def _has_conflicting_text_content(box: DetectedBox, anchors: list[TextAnchor]) -> bool:
    for anchor in anchors:
        if (
            box.x + 2 <= anchor.x <= box.x + box.w - 2
            and box.y + 2 <= anchor.y <= box.y + box.h - 2
        ):
            return True

    below = _below_label_anchors(box, anchors)
    return bool(below) and not _looks_like_short_below_label(below)


def _looks_like_full_row_separator(box: DetectedBox) -> bool:
    return box.h <= UNDERLINE_FIELD_HEIGHT_PT and box.w >= 450


def _has_fillable_label_context(box: DetectedBox, anchors: list[TextAnchor]) -> bool:
    adjacent = [
        anchor for anchor in anchors
        if (
            _effective_text_len(anchor.text) > 0
            and not _looks_like_section_number(anchor.text)
            and box.y - 10 <= anchor.y <= box.y + _same_line_upper_gap(box)
            and (
                0 <= box.x - anchor.x <= 180
                or 0 <= anchor.x - (box.x + box.w) <= 180
            )
        )
    ]
    if _looks_like_short_adjacent_label(adjacent):
        return True

    return _looks_like_short_below_label(_below_label_anchors(box, anchors))


def _same_line_upper_gap(box: DetectedBox) -> float:
    if box.h <= UNDERLINE_FIELD_HEIGHT_PT and box.w > 220:
        return 8
    return box.h + 12


def _has_fillable_row_peer(
    box: DetectedBox,
    direct: list[DetectedBox],
    all_boxes: list[DetectedBox],
    anchors: list[TextAnchor],
) -> bool:
    if box.w > 220:
        return False
    row = [
        other for other in all_boxes
        if (
            other.page == box.page
            and abs(other.y - box.y) <= 2
            and abs(other.h - box.h) <= 3
            and other.w <= 220
            and not _has_conflicting_text_content(other, anchors)
        )
    ]
    if not (2 <= len(row) <= 3):
        return False
    return any(peer in direct and peer != box for peer in row)


def _has_immediate_above_label_context(box: DetectedBox, anchors: list[TextAnchor]) -> bool:
    if box.w > 180 or box.h > 20:
        return False
    box_top = box.y + box.h
    for anchor in anchors:
        if _effective_text_len(anchor.text) == 0 or _looks_like_section_number(anchor.text):
            continue
        if not (0 < anchor.y - box_top <= 12):
            continue
        if box.x - 10 <= anchor.x <= box.x + box.w + 10:
            return True
    return False


def _is_blank_repeating_table_input_cell(
    box: DetectedBox,
    all_boxes: list[DetectedBox],
    anchors: list[TextAnchor],
) -> bool:
    if box.w > 220 or box.h > 32:
        return False

    blank_column_cells = [
        other for other in all_boxes
        if (
            other.page == box.page
            and abs(other.x - box.x) <= 2
            and abs(other.w - box.w) <= 3
            and abs(other.h - box.h) <= 6
            and not _has_conflicting_text_content(other, anchors)
        )
    ]
    if len(blank_column_cells) < 3:
        return False

    return _row_has_text_table_peer(box, all_boxes, anchors)


def _row_has_text_table_peer(
    box: DetectedBox,
    all_boxes: list[DetectedBox],
    anchors: list[TextAnchor],
) -> bool:
    row = [
        other for other in all_boxes
        if (
            other.page == box.page
            and other != box
            and abs(other.y - box.y) <= 3
            and abs(other.h - box.h) <= 6
        )
    ]
    if not row:
        return False
    return any(_has_conflicting_text_content(peer, anchors) for peer in row)


def _below_label_anchors(box: DetectedBox, anchors: list[TextAnchor]) -> list[TextAnchor]:
    return [
        anchor for anchor in anchors
        if (
            box.x - 10 <= anchor.x <= box.x + box.w + 10
            and box.y - 28 <= anchor.y < box.y
            and _effective_text_len(anchor.text) > 0
        )
    ]


def _looks_like_short_below_label(anchors: list[TextAnchor]) -> bool:
    label_anchors = [anchor for anchor in anchors if not _looks_like_section_number(anchor.text)]
    if not label_anchors or len(label_anchors) > 3:
        return False
    if max(anchor.y for anchor in label_anchors) - min(anchor.y for anchor in label_anchors) > 5:
        return False
    total_len = sum(_effective_text_len(anchor.text) for anchor in label_anchors)
    return 2 <= total_len <= 80


def _looks_like_short_adjacent_label(anchors: list[TextAnchor]) -> bool:
    label_anchors = [anchor for anchor in anchors if not _looks_like_section_number(anchor.text)]
    if not label_anchors or len(label_anchors) > 3:
        return False
    if max(anchor.y for anchor in label_anchors) - min(anchor.y for anchor in label_anchors) > 5:
        return False
    total_len = sum(_effective_text_len(anchor.text) for anchor in label_anchors)
    return 2 <= total_len <= 40


def _looks_like_section_number(text: str) -> bool:
    return re.fullmatch(r"\s*\d+(?:\.\d+)*\.?\s*", text) is not None


def _effective_text_len(text: str) -> int:
    return sum(1 for ch in text if ch.isalpha() or ch.isdigit())


def _nearest_label(box: DetectedBox, anchors: list[TextAnchor]) -> str:
    box_cx = box.x + box.w / 2
    box_top = box.y + box.h
    best: str | None = None
    best_score = float("inf")
    for anchor in anchors:
        if not _looks_like_readable_label(anchor.text):
            continue
        if _looks_like_section_number(anchor.text):
            continue
        below_gap = box.y - anchor.y
        if 0 < below_gap <= 28 and box.x - 10 <= anchor.x <= box.x + box.w + 10:
            score = below_gap + abs(anchor.x - box_cx) * 0.1
            if score < best_score:
                best_score = score
                best = anchor.text
            continue
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


def _looks_like_readable_label(label: str) -> bool:
    if _effective_text_len(label) == 0:
        return False
    if any(ord(ch) < 32 and ch not in "\t\r\n" for ch in label):
        return False
    non_printable = sum(1 for ch in label if not ch.isprintable())
    if non_printable and non_printable / max(len(label), 1) > 0.05:
        return False
    return True


def _is_signature_label(label: str) -> bool:
    return _label_starts_with_signature_word(label)


def _label_starts_with_signature_word(label: str) -> bool:
    if not _looks_like_text(label):
        return False

    lowered = label.casefold().lstrip()
    if lowered.startswith("sign here"):
        return True

    first_word_match = re.search(r"[A-Za-z\u0590-\u05FF]+", lowered)
    if first_word_match is None:
        return False

    first_word = first_word_match.group(0)
    reversed_first_word = first_word[::-1]
    if (
        first_word in HEBREW_SIGNATURE_LABEL_FIRST_WORDS
        or reversed_first_word in HEBREW_SIGNATURE_LABEL_FIRST_WORDS
    ):
        return True
    return first_word in ENGLISH_SIGNATURE_LABEL_FIRST_WORDS


def _looks_like_text(label: str) -> bool:
    """Reject labels that are clearly font-encoding noise, not real words.

    Some PDFs use custom font encodings where the raw content-stream bytes do
    not decode to readable characters. Such labels are dominated by digits or
    have no real word, so they should fall back to generic field names.
    """
    letters = sum(ch.isalpha() for ch in label)
    digits = sum(ch.isdigit() for ch in label)
    if letters < 2:
        return False
    if digits and digits / (letters + digits) > 0.2:
        return False
    alpha_chars = [ch.casefold() for ch in label if ch.isalpha()]
    if alpha_chars and max(alpha_chars.count(ch) for ch in set(alpha_chars)) / len(alpha_chars) > 0.8:
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


def _checkbox_base_name(label: str) -> str:
    if not _looks_like_text(label):
        return "checkboxField"
    slug = "".join(ch for ch in label.title() if ch.isalnum())
    if not slug:
        return "checkboxField"
    return f"checkbox{slug[:32]}"


def _resolve_auto_field_name(
    base: str,
    *,
    field_type: str,
    label: str,
    used_names: dict[str, int],
    field_name_resolver: FieldNameResolver | None,
) -> ResolvedAutoName:
    if field_name_resolver is None:
        return ResolvedAutoName(_unique_name(base, used_names), matched=False, method="generated")

    resolution = field_name_resolver.resolve(base, field_type=field_type, label=label)
    if resolution.matched:
        if resolution.name != base:
            _reserve_next_unique_name(base, used_names)
        return ResolvedAutoName(
            field_name_resolver.unique_name(resolution.name, used_names),
            matched=True,
            method=resolution.method,
        )

    generated_name, generated_count = _preview_unique_name(base, used_names)
    generated_resolution = field_name_resolver.resolve(generated_name, field_type=field_type, label="")
    if generated_resolution.matched:
        _reserve_unique_name(base, generated_count, used_names)
        return ResolvedAutoName(
            field_name_resolver.unique_name(generated_resolution.name, used_names),
            matched=True,
            method=generated_resolution.method,
        )

    if field_type.lower() in {"image", "img"} and field_name_resolver.is_known_name("imgPersonSignature"):
        _reserve_unique_name(base, generated_count, used_names)
        return ResolvedAutoName(
            field_name_resolver.unique_name("imgPersonSignature", used_names),
            matched=True,
            method="signature-default",
        )

    return ResolvedAutoName(
        _reserve_unique_name(resolution.name, generated_count, used_names),
        matched=False,
        method=resolution.method,
    )


def _unique_name(base: str, used_names: dict[str, int]) -> str:
    name, count = _preview_unique_name(base, used_names)
    return _reserve_unique_name(base, count, used_names)


def _reserve_next_unique_name(base: str, used_names: dict[str, int]) -> str:
    name, count = _preview_unique_name(base, used_names)
    return _reserve_unique_name(base, count, used_names)


def _preview_unique_name(base: str, used_names: dict[str, int]) -> tuple[str, int]:
    count = used_names.get(base, 0) + 1
    return (base if count == 1 else f"{base}{count}"), count


def _reserve_unique_name(base: str, count: int, used_names: dict[str, int]) -> str:
    used_names[base] = count
    return base if count == 1 else f"{base}{count}"
