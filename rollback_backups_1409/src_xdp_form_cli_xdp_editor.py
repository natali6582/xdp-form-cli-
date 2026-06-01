from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from shutil import copy2

from lxml import etree

from xdp_form_cli.field_truth import FieldMatch


XDP_NS = "http://ns.adobe.com/xdp/"
XFA_TEMPLATE_NS = "http://www.xfa.org/schema/xfa-template/2.5/"
DEFAULT_FIELD_FONT = "Arial"


def _local_name(element: etree._Element) -> str:
    tag = getattr(element, "tag", None)
    if not isinstance(tag, str):
        return ""
    return etree.QName(element).localname


@dataclass
class PageSummary:
    name: str
    index: int
    field_count: int


class XdpEditor:
    def __init__(self, source_path: str | Path) -> None:
        self.source_path = Path(source_path)
        self.source_label = str(self.source_path)
        parser = etree.XMLParser(
            remove_blank_text=False,
            strip_cdata=False,
            resolve_entities=False,
            remove_comments=False,
            remove_pis=False,
        )
        self.tree = etree.parse(str(self.source_path), parser)
        self.root = self.tree.getroot()

    @classmethod
    def from_bytes(cls, raw_xml: bytes, source_label: str = "<embedded-xfa>") -> "XdpEditor":
        parser = etree.XMLParser(
            remove_blank_text=False,
            strip_cdata=False,
            resolve_entities=False,
            remove_comments=False,
            remove_pis=False,
        )
        instance = cls.__new__(cls)
        instance.source_path = None
        instance.source_label = source_label
        instance.root = etree.fromstring(raw_xml, parser)
        instance.tree = etree.ElementTree(instance.root)
        return instance

    def page_summaries(self) -> list[PageSummary]:
        pages = []
        for index, page in enumerate(self._iter_page_subforms(), start=1):
            pages.append(
                PageSummary(
                    name=page.get("name", f"Page{index}"),
                    index=index,
                    field_count=len(self._child_fields(page)),
                )
            )
        return pages

    def field_names(self, page_name: str) -> list[str]:
        page = self._find_page_subform(page_name)
        return [field.get("name", "") for field in self._child_fields(page)]

    def convert_field_names(self, matcher) -> list[FieldMatch]:
        matches: list[FieldMatch] = []
        for field in self._iter_all_fields():
            original_name = field.get("name", "").strip()
            if not original_name:
                continue
            match = matcher(original_name)
            if match.changed:
                field.set("name", match.canonical_name)
                self._ensure_field_font(field)
            matches.append(match)
        return matches

    def normalize_fields(self) -> int:
        count = 0
        for field in self._iter_all_fields():
            self._ensure_field_font(field)
            count += 1
        return count

    def replace_page_from_fragment(
        self, page_name: str, fragment_path: str | Path
    ) -> None:
        target_page = self._find_page_subform(page_name)
        fragment_root = self._parse_fragment(fragment_path)

        if _local_name(fragment_root) != "subform":
            raise ValueError("Fragment root must be a <subform> element.")

        fragment_name = fragment_root.get("name")
        if fragment_name and fragment_name != page_name:
            raise ValueError(
                f"Fragment subform name '{fragment_name}' does not match target page '{page_name}'."
            )

        parent = target_page.getparent()
        if parent is None:
            raise ValueError(f"Could not find parent for page '{page_name}'.")

        replacement = etree.fromstring(etree.tostring(fragment_root))
        self._ensure_fields_font(replacement)
        parent.replace(target_page, replacement)

    def write_copy(self, output_path: str | Path) -> Path:
        if self.source_path is None:
            raise ValueError("Embedded XML editors cannot write directly to disk.")
        output = Path(output_path)
        if output.resolve() != self.source_path.resolve():
            copy2(self.source_path, output)
        output.write_bytes(self.to_bytes())
        return output

    def to_bytes(self) -> bytes:
        xml_declaration = _local_name(self.root) != "template"
        return etree.tostring(
            self.tree,
            encoding="UTF-8",
            xml_declaration=xml_declaration,
            pretty_print=False,
        )

    def _iter_page_subforms(self) -> list[etree._Element]:
        topmost = self._find_topmost_subform()
        pages: list[etree._Element] = []
        for child in topmost:
            if _local_name(child) != "subform":
                continue
            name = child.get("name", "")
            if name.startswith("Page"):
                pages.append(child)
        return pages

    def _find_topmost_subform(self) -> etree._Element:
        if _local_name(self.root) == "template":
            template = self.root
        else:
            template = None
            for child in self.root:
                if _local_name(child) == "template":
                    template = child
                    break
        if template is None:
            raise ValueError("Could not find <template> in XDP.")

        for child in template:
            if _local_name(child) == "subform" and child.get("name") == "topmostSubform":
                return child
        raise ValueError("Could not find topmostSubform.")

    def _find_page_subform(self, page_name: str) -> etree._Element:
        for page in self._iter_page_subforms():
            if page.get("name") == page_name:
                return page
        raise ValueError(f"Page '{page_name}' was not found.")

    def _child_fields(self, page: etree._Element) -> list[etree._Element]:
        return [child for child in page if _local_name(child) == "field"]

    def _iter_all_fields(self) -> list[etree._Element]:
        return [element for element in self.root.iter() if _local_name(element) == "field"]

    def _parse_fragment(self, fragment_path: str | Path) -> etree._Element:
        raw = Path(fragment_path).read_bytes().strip()
        if not raw:
            raise ValueError("Fragment file is empty.")
        return etree.fromstring(raw)

    def _ensure_fields_font(self, root: etree._Element) -> None:
        for field in root.iter():
            if _local_name(field) == "field":
                self._ensure_field_font(field)

    def _ensure_field_font(self, field: etree._Element) -> None:
        font = None
        for child in field:
            if _local_name(child) == "font":
                font = child
                break

        if font is None:
            font = etree.Element(f"{{{XFA_TEMPLATE_NS}}}font")
            insert_at = 0
            for index, child in enumerate(field):
                if _local_name(child) == "ui":
                    insert_at = index + 1
                    break
            field.insert(insert_at, font)

        font.set("typeface", DEFAULT_FIELD_FONT)
