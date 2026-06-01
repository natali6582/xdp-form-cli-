from __future__ import annotations

from pathlib import Path

import pikepdf
from pikepdf import Name


def pdf_has_xfa(input_path: str | Path) -> bool:
    """Return True when the PDF has an /AcroForm with an /XFA entry."""
    with pikepdf.Pdf.open(str(input_path)) as pdf:
        acroform = pdf.Root.get(Name("/AcroForm"))
        return acroform is not None and Name("/XFA") in acroform


def strip_xfa(input_path: str | Path, output_path: str | Path) -> tuple[Path, bool]:
    """Remove the embedded XFA packet and leave a plain AcroForm PDF.

    Dropping /XFA forces XFA-aware viewers (such as Adobe) to fall back to the
    AcroForm widgets, which is required for reliable filling. Returns the output
    path and whether an XFA packet was actually present and removed.
    """
    source = Path(input_path)
    output = Path(output_path)
    if output.resolve() == source.resolve():
        raise ValueError("--output must be a new PDF file path, not the source PDF.")

    with pikepdf.Pdf.open(str(source)) as pdf:
        acroform = pdf.Root.get(Name("/AcroForm"))
        had_xfa = acroform is not None and Name("/XFA") in acroform

        if acroform is None:
            acroform = pikepdf.Dictionary()
            pdf.Root[Name("/AcroForm")] = acroform

        if had_xfa:
            del acroform[Name("/XFA")]

        # Ensure viewers regenerate field appearances from the AcroForm widgets.
        acroform[Name("/NeedAppearances")] = True

        if Name("/Fields") not in acroform:
            acroform[Name("/Fields")] = pikepdf.Array()

        pdf.save(str(output))

    return output, had_xfa
