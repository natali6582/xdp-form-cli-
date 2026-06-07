from __future__ import annotations

import argparse
import sys
from pathlib import Path

from xdp_form_cli import __version__
from xdp_form_cli.acroform_builder import create_acroform_pdf
from xdp_form_cli.auto_form import build_auto_client_form, build_auto_form
from xdp_form_cli.approved_visual_fields import APPROVED_VISUAL_FIELDS
from xdp_form_cli.azure_report import build_azure_layout_report
from xdp_form_cli import colors
from xdp_form_cli.field_conversion import convert_editor_fields
from xdp_form_cli.field_examples import add_example_fields_to_truth
from xdp_form_cli.field_truth import FieldTruth
from xdp_form_cli.field_validation import ValidationIssue, ValidationResult, validate_acroform
from xdp_form_cli.acroform_reader import PdfAcroFormEditor
from xdp_form_cli.pdf_xfa_editor import PdfXfaEditor
from xdp_form_cli.xdp_editor import XdpEditor
from xdp_form_cli.xfa_stripper import strip_xfa


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xdp-form-cli",
        description="Safely copy and edit Adobe XDP/XFA XML files or PDFs with embedded XFA.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="command", required=True)

    list_pages = subparsers.add_parser("list-pages", help="List page subforms.")
    list_pages.add_argument("--input", required=True, help="Path to the source XDP/XML/PDF file.")

    list_fields = subparsers.add_parser("list-fields", help="List fields in a page.")
    list_fields.add_argument("--input", required=True, help="Path to the source XDP/XML/PDF file.")
    list_fields.add_argument("--page", required=True, help="Page subform name, for example Page26.")

    replace_page = subparsers.add_parser(
        "replace-page",
        help="Replace a page subform from a fragment file and write a new output copy.",
    )
    replace_page.add_argument("--input", required=True, help="Path to the source XDP/XML/PDF file.")
    replace_page.add_argument("--page", required=True, help="Page subform name, for example Page26.")
    replace_page.add_argument(
        "--fragment",
        required=True,
        help="Path to an XML fragment file containing one <subform> block.",
    )
    replace_page.add_argument(
        "--output",
        required=True,
        help="Path to the new output XDP/XML/PDF file. Must not be the source file.",
    )

    strip_xfa_parser = subparsers.add_parser(
        "strip-xfa",
        help="Remove the embedded XFA packet and write a plain, fillable AcroForm PDF copy.",
    )
    strip_xfa_parser.add_argument("--input", required=True, help="Path to the source PDF with embedded XFA.")
    strip_xfa_parser.add_argument(
        "--output",
        required=True,
        help="Path to the new output PDF file. Must not be the source file.",
    )
    strip_xfa_parser.add_argument(
        "--fields",
        default=None,
        help="Optional CSV (page,name,type,x,y,w,h,value) to place AcroForm fields after stripping XFA.",
    )
    strip_xfa_parser.add_argument(
        "--strict-validation",
        action="store_true",
        help="With --fields, fail on validation warnings, not only errors.",
    )

    auto_form_parser = subparsers.add_parser(
        "auto-form",
        help="From a URL or PDF with no working fields, auto-detect boxes and build a fillable AcroForm (signatures as image).",
    )
    auto_form_parser.add_argument(
        "--url",
        default=None,
        help="http(s) URL of the source PDF (XFA shell or flat layout).",
    )
    auto_form_parser.add_argument(
        "--input",
        default=None,
        help="Local source PDF path. Provide either --url or --input.",
    )
    auto_form_parser.add_argument(
        "--output",
        required=True,
        help="Path to the new fillable PDF file. Must not be the source file.",
    )
    auto_form_parser.add_argument(
        "--fields-csv",
        default=None,
        help="Optional path for the emitted editable field CSV (defaults next to --output).",
    )
    auto_form_parser.add_argument(
        "--xfa",
        default=None,
        help="Optional XFA/XDP template file. Fields are injected into this template "
        "and embedded in the output PDF alongside the AcroForm layer.",
    )
    auto_form_parser.add_argument(
        "--azure-document-intelligence",
        action="store_true",
        help="Use Azure Document Intelligence prebuilt-layout as an optional OCR/layout fallback.",
    )

    auto_client_form_parser = subparsers.add_parser(
        "auto-client-form",
        help="Client upload flow: auto-detect text fields, checkboxes, and signature image placeholders from a flat PDF.",
    )
    auto_client_form_parser.add_argument(
        "--url",
        default=None,
        help="http(s) URL of the uploaded client PDF.",
    )
    auto_client_form_parser.add_argument(
        "--input",
        default=None,
        help="Local uploaded client PDF path. Provide either --url or --input.",
    )
    auto_client_form_parser.add_argument(
        "--output",
        required=True,
        help="Path to the new fillable PDF file. Must not be the source file.",
    )
    auto_client_form_parser.add_argument(
        "--fields-csv",
        default=None,
        help="Optional path for the emitted editable field CSV (defaults next to --output).",
    )
    auto_client_form_parser.add_argument(
        "--fields-list",
        default=None,
        help="Optional CSV of Plan-T-supported field names. Safe matches are renamed to these canonical names.",
    )
    auto_client_form_parser.add_argument(
        "--field-mapping-xlsx",
        default=None,
        help="Optional LiveCycle-to-Plan-T mapping workbook, such as מיפוי שדות LiveCycle מול קוד המערכת.xlsx.",
    )
    auto_client_form_parser.add_argument(
        "--semantic-field-map",
        default=None,
        help="Optional CSV with label,field_name columns for flat-PDF semantic mapping to Plan-T names.",
    )
    auto_client_form_parser.add_argument(
        "--mapping-report",
        default=None,
        help="Optional CSV report showing detected labels, final names, and Plan-T match status.",
    )
    auto_client_form_parser.add_argument(
        "--azure-document-intelligence",
        action="store_true",
        help="Use Azure Document Intelligence prebuilt-layout as an optional OCR/layout fallback.",
    )

    azure_report_parser = subparsers.add_parser(
        "azure-layout-report",
        help="Run Azure Document Intelligence only and write a raw layout/candidate-field report.",
    )
    azure_report_parser.add_argument("--input", required=True, help="Path to the source PDF file.")
    azure_report_parser.add_argument(
        "--fields-list",
        required=True,
        help="CSV of Plan-T-supported field names, usually רשימת שדות מהקוד.csv.",
    )
    azure_report_parser.add_argument("--output-csv", required=True, help="Path to write the Azure layout CSV report.")
    azure_report_parser.add_argument("--output-json", default=None, help="Optional path to write the Azure layout JSON report.")

    create_acroform = subparsers.add_parser(
        "create-acroform",
        help="Create a new PDF copy with AcroForm fields from a CSV field specification.",
    )
    create_acroform.add_argument("--input", required=True, help="Path to the source static PDF file.")
    create_acroform.add_argument(
        "--fields",
        required=True,
        help="CSV with columns: page,name,type,x,y,w,h,value. Coordinates are PDF points from the bottom-left.",
    )
    create_acroform.add_argument(
        "--output",
        required=True,
        help="Path to the new output PDF file. Must not be the source file.",
    )
    create_acroform.add_argument(
        "--strict-validation",
        action="store_true",
        help="Fail create-acroform on validation warnings, not only errors.",
    )

    validate_acroform_parser = subparsers.add_parser(
        "validate-acroform",
        help="Validate a field-spec CSV and optionally the generated AcroForm PDF.",
    )
    validate_acroform_parser.add_argument(
        "--fields",
        required=True,
        help="CSV with columns: page,name,type,x,y,w,h,value.",
    )
    validate_acroform_parser.add_argument(
        "--input",
        default=None,
        help="Optional source PDF used to validate page count and page bounds.",
    )
    validate_acroform_parser.add_argument(
        "--pdf",
        default=None,
        help="Optional generated PDF used to validate AcroForm field structure.",
    )
    validate_acroform_parser.add_argument(
        "--strict",
        action="store_true",
        help="Return a failure exit code when warnings are found.",
    )

    convert_fields = subparsers.add_parser(
        "convert-fields",
        help="Rename PDF/XDP field names to canonical code-list names and save a new output copy.",
    )
    convert_fields.add_argument("--input", required=True, help="Path to the source XDP/XML/PDF file.")
    convert_fields.add_argument("--output", required=True, help="Path to the new output XDP/XML/PDF file.")
    convert_fields.add_argument(
        "--truth-code",
        default=None,
        help="Optional override for the canonical Plan-T code file. Defaults to PDFFormsBL*plan-t.cs near the workspace.",
    )
    convert_fields.add_argument(
        "--examples",
        action="append",
        default=[],
        help="Optional XML/XDP/TXT example file or directory accepted by the system. Can be repeated.",
    )
    convert_fields.add_argument(
        "--report",
        default=None,
        help="Optional CSV report path with original and canonical field names.",
    )

    return parser


def _is_pdf(path: str) -> bool:
    return Path(path).suffix.lower() == ".pdf"


def _pdf_has_xfa(input_path: str) -> bool:
    import pikepdf
    from pikepdf import Name

    with pikepdf.Pdf.open(input_path) as pdf:
        acroform = pdf.Root.get(Name("/AcroForm"))
        return acroform is not None and Name("/XFA") in acroform


def _load_editor(input_path: str) -> XdpEditor | PdfXfaEditor | PdfAcroFormEditor:
    if _is_pdf(input_path):
        if _pdf_has_xfa(input_path):
            colors.info("Detected PDF input with embedded XFA; using XFA editor.")
            return PdfXfaEditor(input_path)
        colors.info("Detected plain AcroForm PDF (no XFA); using AcroForm reader.")
        return PdfAcroFormEditor(input_path)
    colors.info("Detected standalone XML/XDP input.")
    return XdpEditor(input_path)


def _close_editor(editor: XdpEditor | PdfXfaEditor) -> None:
    close = getattr(editor, "close", None)
    if callable(close):
        close()


def cmd_list_pages(args: argparse.Namespace) -> int:
    colors.step(f"Loading input: {args.input}")
    editor = _load_editor(args.input)
    try:
        pages = editor.page_summaries()
        colors.success(f"Found {len(pages)} page subforms.")
        for page in pages:
            print(f"{page.index:>2}  {page.name}  fields={page.field_count}")
    finally:
        _close_editor(editor)
    return 0


def cmd_list_fields(args: argparse.Namespace) -> int:
    colors.step(f"Loading input: {args.input}")
    editor = _load_editor(args.input)
    try:
        colors.step(f"Reading fields from {args.page}")
        fields = editor.field_names(args.page)
        colors.success(f"Found {len(fields)} fields in {args.page}.")
        for name in fields:
            print(name)
    finally:
        _close_editor(editor)
    return 0


def cmd_replace_page(args: argparse.Namespace) -> int:
    if args.input == args.output:
        raise ValueError("--output must be a new file path, not the source file.")
    if _is_pdf(args.input) != _is_pdf(args.output):
        raise ValueError("Input and output must use the same file type, for example PDF to PDF.")

    colors.step(f"Loading input: {args.input}")
    editor = _load_editor(args.input)
    try:
        colors.step(f"Replacing {args.page} from fragment: {args.fragment}")
        editor.replace_page_from_fragment(args.page, args.fragment)
        colors.step(f"Writing output copy: {args.output}")
        if isinstance(editor, (PdfXfaEditor, PdfAcroFormEditor)):
            output = editor.save_copy(args.output)
            colors.success(f"Saved updated PDF copy: {output}")
        else:
            output = editor.write_copy(args.output)
            colors.success(f"Saved updated XDP/XML copy: {output}")
    finally:
        _close_editor(editor)
    return 0


def cmd_strip_xfa(args: argparse.Namespace) -> int:
    if args.input == args.output:
        raise ValueError("--output must be a new file path, not the source file.")
    if not _is_pdf(args.input) or not _is_pdf(args.output):
        raise ValueError("strip-xfa only supports PDF input and PDF output.")

    colors.step(f"Loading PDF input: {args.input}")

    if args.fields:
        # Strip XFA to a temporary PDF, then place fields onto the plain AcroForm.
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            stripped = Path(tmp_dir) / "stripped.pdf"
            _, had_xfa = strip_xfa(args.input, stripped)
            if had_xfa:
                colors.success("Removed embedded XFA packet.")
            else:
                colors.warn("No XFA packet found; input was already a plain AcroForm.")

            colors.step(f"Loading field specification: {args.fields}")
            precheck = validate_acroform(args.fields, input_pdf=str(stripped))
            _print_validation_result(precheck, strict=args.strict_validation, title="Pre-create validation")
            if precheck.has_failures(strict=args.strict_validation):
                raise ValueError("Validation failed. Fix the field specification before creating the PDF.")

            output, count = create_acroform_pdf(str(stripped), args.fields, args.output)
            colors.success(f"Saved fillable AcroForm PDF with {count} field(s): {output}")

            postcheck = validate_acroform(args.fields, input_pdf=str(stripped), output_pdf=output)
            _print_validation_result(postcheck, strict=args.strict_validation, title="Post-create PDF validation")
            if postcheck.has_failures(strict=args.strict_validation):
                raise ValueError("Generated PDF failed validation.")
        return 0

    output, had_xfa = strip_xfa(args.input, args.output)
    if had_xfa:
        colors.success(f"Removed embedded XFA; saved plain AcroForm PDF copy: {output}")
    else:
        colors.warn(f"No XFA packet found; saved AcroForm PDF copy unchanged: {output}")
    return 0


def cmd_auto_form(args: argparse.Namespace) -> int:
    if bool(args.url) == bool(args.input):
        raise ValueError("Provide exactly one of --url or --input.")
    if not _is_pdf(args.output):
        raise ValueError("auto-form output must be a PDF file.")
    source = args.url or args.input
    if args.input and not _is_pdf(args.input):
        raise ValueError("auto-form --input must be a PDF file.")

    colors.step(f"Loading source: {source}")
    output, csv_path, count = build_auto_form(
        source,
        args.output,
        csv_path=args.fields_csv,
        xfa_template_path=args.xfa,
        use_azure_document_intelligence=args.azure_document_intelligence,
    )
    colors.success(f"Detected and placed {count} field(s).")
    colors.success(f"Saved editable field CSV: {csv_path}")
    colors.success(f"Saved fillable AcroForm PDF: {output}")
    if args.xfa:
        colors.info(f"Embedded XFA template from: {args.xfa}")
    colors.info("Adjust any box in the CSV and rerun create-acroform if a field landed off.")
    return 0


def cmd_auto_client_form(args: argparse.Namespace) -> int:
    if bool(args.url) == bool(args.input):
        raise ValueError("Provide exactly one of --url or --input.")
    if not _is_pdf(args.output):
        raise ValueError("auto-client-form output must be a PDF file.")
    source = args.url or args.input
    if args.input and not _is_pdf(args.input):
        raise ValueError("auto-client-form --input must be a PDF file.")

    colors.step(f"Loading client upload: {source}")
    output, csv_path, count, summary = build_auto_client_form(
        source,
        args.output,
        csv_path=args.fields_csv,
        use_azure_document_intelligence=args.azure_document_intelligence,
        fields_list_path=args.fields_list,
        field_mapping_path=args.field_mapping_xlsx,
        semantic_map_path=args.semantic_field_map,
        mapping_report_path=args.mapping_report,
    )
    colors.success(f"Detected and placed {count} field(s).")
    colors.info(
        "Field type counts: "
        + ", ".join(f"{field_type}={amount}" for field_type, amount in summary.type_counts.items())
    )
    if summary.warnings:
        for warning in summary.warnings:
            colors.warn(warning)
    colors.success(f"Saved editable field CSV: {csv_path}")
    if args.mapping_report:
        colors.success(f"Saved field-name mapping report: {args.mapping_report}")
    colors.success(f"Saved fillable AcroForm PDF: {output}")
    colors.info("Review the CSV before production use; rerun create-acroform after manual corrections if needed.")
    return 0


def cmd_azure_layout_report(args: argparse.Namespace) -> int:
    if not _is_pdf(args.input):
        raise ValueError("azure-layout-report --input must be a PDF file.")

    colors.step("Running Azure Document Intelligence prebuilt-layout only.")
    colors.info("No local field-detection logic is used in this report.")
    summary = build_azure_layout_report(
        args.input,
        args.fields_list,
        args.output_csv,
        output_json=args.output_json,
    )
    colors.success(f"Loaded {summary.known_field_count} Plan-T field name(s).")
    colors.success(
        "Azure result: "
        f"words={summary.word_count}, text-lines={summary.anchor_count}, "
        f"candidate-text-fields={summary.candidate_text_field_count}, "
        f"candidate-checkboxes={summary.checkbox_count}."
    )
    colors.success(f"Saved Azure CSV report: {summary.csv_path}")
    if summary.json_path is not None:
        colors.success(f"Saved Azure JSON report: {summary.json_path}")
    return 0


def cmd_create_acroform(args: argparse.Namespace) -> int:
    if args.input == args.output:
        raise ValueError("--output must be a new file path, not the source file.")
    if not _is_pdf(args.input) or not _is_pdf(args.output):
        raise ValueError("create-acroform only supports PDF input and PDF output.")

    colors.step(f"Loading static PDF input: {args.input}")
    colors.step(f"Loading field specification: {args.fields}")
    precheck = validate_acroform(args.fields, input_pdf=args.input)
    _print_validation_result(precheck, strict=args.strict_validation, title="Pre-create validation")
    if precheck.has_failures(strict=args.strict_validation):
        raise ValueError("Validation failed. Fix the field specification before creating the PDF.")

    output, count = create_acroform_pdf(args.input, args.fields, args.output)
    colors.success(f"Saved AcroForm PDF copy with {count} field(s): {output}")

    postcheck = validate_acroform(args.fields, input_pdf=args.input, output_pdf=output)
    _print_validation_result(postcheck, strict=args.strict_validation, title="Post-create PDF validation")
    if postcheck.has_failures(strict=args.strict_validation):
        raise ValueError("Generated PDF failed validation.")
    return 0


def cmd_validate_acroform(args: argparse.Namespace) -> int:
    colors.step(f"Validating field specification: {args.fields}")
    if args.input:
        colors.info(f"Using source PDF for page-bound checks: {args.input}")
    if args.pdf:
        colors.info(f"Using generated PDF for AcroForm checks: {args.pdf}")

    result = validate_acroform(args.fields, input_pdf=args.input, output_pdf=args.pdf)
    _print_validation_result(result, strict=args.strict, title="Validation")
    return 1 if result.has_failures(strict=args.strict) else 0


def cmd_convert_fields(args: argparse.Namespace) -> int:
    if args.input == args.output:
        raise ValueError("--output must be a new file path, not the source file.")
    if _is_pdf(args.input) != _is_pdf(args.output):
        raise ValueError("Input and output must use the same file type, for example PDF to PDF.")

    truth = FieldTruth(args.truth_code) if args.truth_code else FieldTruth.default()
    colors.step(f"Loading source-of-truth fields: {truth.code_path}")
    colors.info(f"Loaded {truth.count} fields from the Plan-T code file.")

    approved_added = truth.add_names(set(APPROVED_VISUAL_FIELDS), source="approved-visual")
    colors.info(
        f"Loaded {len(APPROVED_VISUAL_FIELDS)} approved visual field(s); added={approved_added}."
    )

    if args.examples:
        examples = add_example_fields_to_truth(truth, args.examples)
        colors.info(
            f"Loaded {examples.files} example file(s): discovered={examples.discovered}, accepted-style={examples.accepted_style}, added={examples.added}."
        )

    colors.step(f"Loading input: {args.input}")
    editor = _load_editor(args.input)
    try:
        report = convert_editor_fields(editor, truth)
        colors.info(
            f"Processed {report.total_fields} fields. Known={report.exact_or_known}, renamed={report.renamed}, unmatched={report.unmatched}."
        )

        colors.step(f"Writing output copy: {args.output}")
        if isinstance(editor, (PdfXfaEditor, PdfAcroFormEditor)):
            output = editor.save_copy(args.output)
            colors.success(f"Saved updated PDF copy: {output}")
        else:
            output = editor.write_copy(args.output)
            colors.success(f"Saved updated XDP/XML copy: {output}")

        if args.report:
            report_path = report.write_csv(args.report)
            colors.success(f"Saved conversion report: {report_path}")
    finally:
        _close_editor(editor)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "list-pages":
            return cmd_list_pages(args)
        if args.command == "list-fields":
            return cmd_list_fields(args)
        if args.command == "replace-page":
            return cmd_replace_page(args)
        if args.command == "strip-xfa":
            return cmd_strip_xfa(args)
        if args.command == "auto-form":
            return cmd_auto_form(args)
        if args.command == "auto-client-form":
            return cmd_auto_client_form(args)
        if args.command == "azure-layout-report":
            return cmd_azure_layout_report(args)
        if args.command == "create-acroform":
            return cmd_create_acroform(args)
        if args.command == "validate-acroform":
            return cmd_validate_acroform(args)
        if args.command == "convert-fields":
            return cmd_convert_fields(args)
        parser.error(f"Unsupported command: {args.command}")
        return 2
    except Exception as exc:
        colors.error(str(exc))
        return 1


def _print_validation_result(result: ValidationResult, *, strict: bool, title: str) -> None:
    colors.step(title)
    colors.info(
        f"Validated {len(result.fields)} field spec row(s). "
        f"Errors={len(result.errors)}, warnings={len(result.warnings)}."
    )

    if not result.issues:
        colors.success("No validation issues found.")
        return

    for issue in result.issues:
        _print_validation_issue(issue)

    if result.has_failures(strict=strict):
        colors.error("Validation result is failing.")
    else:
        colors.warn("Validation completed with warnings only.")


def _print_validation_issue(issue: ValidationIssue) -> None:
    parts = [issue.code]
    if issue.page is not None:
        parts.append(f"page={issue.page}")
    if issue.field:
        parts.append(f"field={issue.field}")
    message = f"{' | '.join(parts)}: {issue.message}"
    if issue.severity == "ERROR":
        colors.error(message)
    else:
        colors.warn(message)


if __name__ == "__main__":
    sys.exit(main())
