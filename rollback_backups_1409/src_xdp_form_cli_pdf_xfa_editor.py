from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pikepdf
from pikepdf import Array, Name, Stream

from xdp_form_cli.field_truth import FieldMatch
from xdp_form_cli.xdp_editor import PageSummary, XdpEditor


@dataclass
class XfaLocation:
    xfa_object: object
    stream: Stream
    packet_index: int | None = None
    packet_name: str | None = None


class PdfXfaEditor:
    def __init__(self, source_path: str | Path) -> None:
        self.source_path = Path(source_path)
        self.pdf = pikepdf.Pdf.open(str(self.source_path))
        self.location = self._find_xfa_location()
        raw_xfa = self.location.stream.read_bytes()
        label = self.location.packet_name or "xfa"
        self.xdp = XdpEditor.from_bytes(raw_xfa, f"{self.source_path}:{label}")

    def page_summaries(self) -> list[PageSummary]:
        return self.xdp.page_summaries()

    def field_names(self, page_name: str) -> list[str]:
        return self.xdp.field_names(page_name)

    def replace_page_from_fragment(self, page_name: str, fragment_path: str | Path) -> None:
        self.xdp.replace_page_from_fragment(page_name, fragment_path)

    def convert_field_names(self, matcher) -> list[FieldMatch]:
        return self.xdp.convert_field_names(matcher)

    def normalize_fields(self) -> int:
        return self.xdp.normalize_fields()

    def save_copy(self, output_path: str | Path) -> Path:
        output = Path(output_path)
        if output.resolve() == self.source_path.resolve():
            raise ValueError("--output must be a new PDF file path, not the source PDF.")

        updated_xfa = self.xdp.to_bytes()
        self._write_xfa(updated_xfa)
        self.pdf.save(str(output))
        return output

    def close(self) -> None:
        self.pdf.close()

    def _find_xfa_location(self) -> XfaLocation:
        root = self.pdf.Root
        acroform = root.get(Name("/AcroForm"))
        if acroform is None:
            raise ValueError("PDF has no /AcroForm dictionary, so no embedded XFA was found.")

        xfa = acroform.get(Name("/XFA"))
        if xfa is None:
            raise ValueError("PDF has an /AcroForm dictionary but no /XFA entry.")

        if isinstance(xfa, Stream):
            return XfaLocation(xfa_object=xfa, stream=xfa)

        if isinstance(xfa, Array):
            return self._find_template_packet(xfa)

        raise ValueError(f"Unsupported /XFA object type: {type(xfa).__name__}")

    def _find_template_packet(self, xfa: Array) -> XfaLocation:
        if len(xfa) % 2 != 0:
            raise ValueError("Unsupported /XFA array: expected packet-name/stream pairs.")

        fallback: XfaLocation | None = None
        for index in range(0, len(xfa), 2):
            packet_name = self._packet_name(xfa[index])
            packet_stream = xfa[index + 1]
            if not isinstance(packet_stream, Stream):
                continue

            location = XfaLocation(
                xfa_object=xfa,
                stream=packet_stream,
                packet_index=index + 1,
                packet_name=packet_name,
            )
            if packet_name == "template":
                return location
            if packet_name in {"xdp", "form"} and fallback is None:
                fallback = location

        if fallback is not None:
            return fallback

        raise ValueError("Could not find an editable XFA template packet in the PDF.")

    def _write_xfa(self, raw_xml: bytes) -> None:
        stream = self.location.stream
        try:
            stream.write(raw_xml)
            return
        except AttributeError:
            pass

        if self.location.packet_index is None:
            raise ValueError("Could not update the embedded XFA stream.")

        replacement = self.pdf.make_stream(raw_xml)
        self.location.xfa_object[self.location.packet_index] = replacement

    def _packet_name(self, packet_name_object: object) -> str:
        value = str(packet_name_object).strip()
        if value.startswith("(") and value.endswith(")"):
            value = value[1:-1]
        if value.startswith("/"):
            value = value[1:]
        return value
