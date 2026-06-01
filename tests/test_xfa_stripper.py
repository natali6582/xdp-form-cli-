from __future__ import annotations

from pathlib import Path

import pikepdf
from pikepdf import Array, Dictionary, Name

from xdp_form_cli.xfa_stripper import pdf_has_xfa, strip_xfa


def _write_xfa_pdf(path: Path) -> Path:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    template = pdf.make_stream(b"<template xmlns='http://www.xfa.org/schema/xfa-template/3.0/'/>")
    pdf.Root[Name("/AcroForm")] = Dictionary(
        Fields=Array(),
        XFA=Array([pikepdf.String("template"), template]),
    )
    pdf.save(path)
    return path


def _write_plain_acroform_pdf(path: Path) -> Path:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    pdf.Root[Name("/AcroForm")] = Dictionary(Fields=Array())
    pdf.save(path)
    return path


def test_pdf_has_xfa_detects_xfa(tmp_path: Path) -> None:
    xfa_pdf = _write_xfa_pdf(tmp_path / "xfa.pdf")
    plain_pdf = _write_plain_acroform_pdf(tmp_path / "plain.pdf")

    assert pdf_has_xfa(xfa_pdf) is True
    assert pdf_has_xfa(plain_pdf) is False


def test_strip_xfa_removes_packet(tmp_path: Path) -> None:
    source = _write_xfa_pdf(tmp_path / "xfa.pdf")
    output = tmp_path / "stripped.pdf"

    result, had_xfa = strip_xfa(source, output)

    assert had_xfa is True
    assert result == output
    with pikepdf.Pdf.open(str(output)) as pdf:
        acroform = pdf.Root.get(Name("/AcroForm"))
        assert Name("/XFA") not in acroform
        assert bool(acroform.get(Name("/NeedAppearances"))) is True


def test_strip_xfa_on_plain_acroform_is_noop_flagged(tmp_path: Path) -> None:
    source = _write_plain_acroform_pdf(tmp_path / "plain.pdf")
    output = tmp_path / "out.pdf"

    result, had_xfa = strip_xfa(source, output)

    assert had_xfa is False
    assert result == output
    with pikepdf.Pdf.open(str(output)) as pdf:
        acroform = pdf.Root.get(Name("/AcroForm"))
        assert Name("/XFA") not in acroform


def test_strip_xfa_rejects_same_path(tmp_path: Path) -> None:
    source = _write_xfa_pdf(tmp_path / "xfa.pdf")
    try:
        strip_xfa(source, source)
    except ValueError as exc:
        assert "must be a new PDF file path" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Expected ValueError for same input/output path.")
