from __future__ import annotations

import argparse
import sys
from pathlib import Path

from xdp_form_cli import __version__
from xdp_form_cli import colors
from xdp_form_cli.field_conversion import convert_editor_fields
from xdp_form_cli.field_truth import FieldTruth
from xdp_form_cli.pdf_xfa_editor import PdfXfaEditor
from xdp_form_cli.xdp_editor import XdpEditor


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
        "--report",
        default=None,
        help="Optional CSV report path with original and canonical field names.",
    )

    return parser


def _is_pdf(path: str) -> bool:
    return Path(path).suffix.lower() == ".pdf"


def _load_editor(input_path: str) -> XdpEditor | PdfXfaEditor:
    if _is_pdf(input_path):
        colors.info("Detected PDF input; using embedded XFA editor.")
        return PdfXfaEditor(input_path)
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
        if isinstance(editor, PdfXfaEditor):
            output = editor.save_copy(args.output)
            colors.success(f"Saved updated PDF copy: {output}")
        else:
            output = editor.write_copy(args.output)
            colors.success(f"Saved updated XDP/XML copy: {output}")
    finally:
        _close_editor(editor)
    return 0


def cmd_convert_fields(args: argparse.Namespace) -> int:
    if args.input == args.output:
        raise ValueError("--output must be a new file path, not the source file.")
    if _is_pdf(args.input) != _is_pdf(args.output):
        raise ValueError("Input and output must use the same file type, for example PDF to PDF.")

    truth = FieldTruth(args.truth_code) if args.truth_code else FieldTruth.default()
    colors.step(f"Loading input: {args.input}")
    editor = _load_editor(args.input)
    try:
        colors.step(f"Loading source-of-truth fields: {truth.code_path}")
        report = convert_editor_fields(editor, truth)
        colors.info(
            f"Processed {report.total_fields} fields. Known={report.exact_or_known}, renamed={report.renamed}, unmatched={report.unmatched}."
        )

        colors.step(f"Writing output copy: {args.output}")
        if isinstance(editor, PdfXfaEditor):
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
        if args.command == "convert-fields":
            return cmd_convert_fields(args)
        parser.error(f"Unsupported command: {args.command}")
        return 2
    except Exception as exc:
        colors.error(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
