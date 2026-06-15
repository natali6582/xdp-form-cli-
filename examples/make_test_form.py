"""Generate a small static test form PDF for exercising the detect command.

Usage: py examples/make_test_form.py <output.pdf>
"""

from __future__ import annotations

import sys

import pikepdf
from pikepdf import Name


def make_test_form(path: str) -> None:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(595, 842))  # A4
    # Define /F1 as a real standard font so design tools (e.g. LiveCycle
    # Designer) can render the page text instead of placeholder glyphs.
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=Name("/Font"),
            Subtype=Name("/Type1"),
            BaseFont=Name("/Helvetica"),
            Encoding=Name("/WinAnsiEncoding"),
        )
    )
    content = (
        # Title (no colon, should not produce a field)
        b"BT /F1 14 Tf 200 800 Td (Investor Details Form) Tj ET\n"
        # Labeled boxes
        b"BT /F1 10 Tf 60 740 Td (Full Name) Tj ET\n"
        b"130 732 200 18 re S\n"
        b"BT /F1 10 Tf 60 700 Td (Address) Tj ET\n"
        b"130 692 240 18 re S\n"
        b"BT /F1 10 Tf 60 660 Td (Date) Tj ET\n"
        b"130 652 100 18 re S\n"
        # Label with colon and NO box -> synthesized field
        b"BT /F1 10 Tf 60 620 Td (Email:) Tj ET\n"
        # Underscore run (text-rendered input line)
        b"BT /F1 10 Tf 60 580 Td (Phone: ____________________) Tj ET\n"
        # Checkbox squares
        b"60 540 12 12 re S\n"
        b"BT /F1 10 Tf 80 542 Td (Qualified investor) Tj ET\n"
        b"60 515 12 12 re S\n"
        b"BT /F1 10 Tf 80 517 Td (Accredited investor) Tj ET\n"
        # Signature box
        b"BT /F1 10 Tf 60 460 Td (Signature) Tj ET\n"
        b"130 440 180 35 re S\n"
    )
    page = pdf.pages[0]
    page.obj[Name("/Contents")] = pdf.make_stream(content)
    page.obj[Name("/Resources")] = pikepdf.Dictionary(
        Font=pikepdf.Dictionary(F1=font)
    )
    pdf.save(path)


if __name__ == "__main__":
    make_test_form(sys.argv[1])
    print(f"wrote {sys.argv[1]}")
