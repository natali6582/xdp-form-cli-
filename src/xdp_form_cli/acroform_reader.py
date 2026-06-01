from __future__ import annotations

import re
from pathlib import Path

import pikepdf
from pikepdf import Dictionary, Name, String

from xdp_form_cli.field_truth import FieldMatch
from xdp_form_cli.xdp_editor import PageSummary

_PAGE_NAME_RE = re.compile(r"^Page(?P<index>\d+)$", re.IGNORECASE)


class PdfAcroFormEditor:
    """Reader/editor for plain AcroForm PDFs that have no embedded XFA.

    Implements the same interface the CLI expects from XdpEditor and
    PdfXfaEditor (page_summaries, field_names, convert_field_names,
    save_copy, close) by reading AcroForm widgets directly from the PDF.
    Pages are reported as Page1..PageN by their order in the document.
    """

    def __init__(self, source_path: str | Path) -> None:
        self.source_path = Path(source_path)
        self.pdf = pikepdf.Pdf.open(str(self.source_path))

    def page_summaries(self) -> list[PageSummary]:
        summaries: list[PageSummary] = []
        for index, page in enumerate(self.pdf.pages, start=1):
            summaries.append(
                PageSummary(
                    name=f"Page{index}",
                    index=index,
                    field_count=len(self._page_widget_fields(page.obj)),
                )
            )
        return summaries

    def field_names(self, page_name: str) -> list[str]:
        index = self._page_index(page_name)
        if index < 1 or index > len(self.pdf.pages):
            raise ValueError(
                f"Page {page_name} not found; PDF has {len(self.pdf.pages)} page(s)."
            )
        page = self.pdf.pages[index - 1]
        names: list[str] = []
        for field in self._page_widget_fields(page.obj):
            names.append(self._field_name(field))
        return names

    def convert_field_names(self, matcher) -> list[FieldMatch]:
        matches: list[FieldMatch] = []
        for field in self._iter_named_fields():
            original_name = str(field.get(Name("/T"))).strip()
            if not original_name:
                continue
            match = matcher(original_name)
            if match.changed:
                field[Name("/T")] = String(match.canonical_name)
            matches.append(match)
        return matches

    def save_copy(self, output_path: str | Path) -> Path:
        output = Path(output_path)
        if output.resolve() == self.source_path.resolve():
            raise ValueError("--output must be a new PDF file path, not the source PDF.")
        self.pdf.save(str(output))
        return output

    def close(self) -> None:
        self.pdf.close()

    def _page_index(self, page_name: str) -> int:
        match = _PAGE_NAME_RE.match(page_name.strip())
        if match is None:
            raise ValueError(
                f"Invalid page name '{page_name}'. Use Page1, Page2, ... for plain AcroForm PDFs."
            )
        return int(match.group("index"))

    def _page_widget_fields(self, page_obj: Dictionary) -> list[Dictionary]:
        fields: list[Dictionary] = []
        annots = page_obj.get(Name("/Annots"))
        if annots is None:
            return fields
        for annot in annots:
            if not isinstance(annot, Dictionary):
                continue
            if annot.get(Name("/Subtype")) != Name("/Widget"):
                continue
            field = self._terminal_field(annot)
            if field is not None:
                fields.append(field)
        return fields

    def _terminal_field(self, widget: Dictionary) -> Dictionary | None:
        # A widget may be the field itself (merged) or a kid whose /Parent
        # carries the field name. Walk up until a node with /T is found.
        node: object = widget
        while isinstance(node, Dictionary):
            if Name("/T") in node:
                return node
            node = node.get(Name("/Parent"))
        return None

    def _field_name(self, field: Dictionary) -> str:
        parts: list[str] = []
        node: object = field
        while isinstance(node, Dictionary):
            title = node.get(Name("/T"))
            if title is not None:
                parts.append(str(title))
            node = node.get(Name("/Parent"))
        return ".".join(reversed(parts))

    def _iter_named_fields(self):
        acroform = self.pdf.Root.get(Name("/AcroForm"))
        if acroform is None:
            return
        seen: set[int] = set()
        stack = list(acroform.get(Name("/Fields"), []))
        while stack:
            node = stack.pop()
            if not isinstance(node, Dictionary):
                continue
            marker = id(node)
            if marker in seen:
                continue
            seen.add(marker)
            if Name("/T") in node:
                yield node
            stack.extend(node.get(Name("/Kids"), []))
