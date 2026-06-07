from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from xdp_form_cli.field_name_resolution import (
    FieldNameResolver,
    load_livecycle_mapping_aliases,
    load_semantic_field_aliases,
    load_semantic_label_aliases,
)


def test_livecycle_mapping_loads_only_direct_safe_aliases(tmp_path: Path) -> None:
    workbook = _write_mapping_workbook(
        tmp_path / "mapping.xlsx",
        [
            ("txtNameOfAccountOwner", "txtAccountName", "Direct match", "safe"),
            ("CheckBox2[0]", "chkPersonMale / chkPersonFemale", "Context dependent", "visual order"),
            ("txtUnknown", "txtDoesNotExist", "Direct match", "not in Plan-T"),
        ],
    )
    known = {"txtAccountName", "chkPersonMale", "chkPersonFemale"}

    aliases = load_livecycle_mapping_aliases(workbook, known)

    assert aliases == {"txtNameOfAccountOwner": "txtAccountName"}


def test_resolver_uses_livecycle_alias_before_leaving_generated_name(tmp_path: Path) -> None:
    workbook = _write_mapping_workbook(
        tmp_path / "mapping.xlsx",
        [("txtNameOfAccountOwner", "txtAccountName", "Direct match", "safe")],
    )
    fields_csv = tmp_path / "fields.csv"
    fields_csv.write_text("field_name,prefix\ntxtAccountName,txt\n", encoding="utf-8")

    resolver = FieldNameResolver.from_files(fields_list_csv=fields_csv, mapping_xlsx=workbook)

    assert resolver is not None
    assert resolver.resolve("txtNameOfAccountOwner", field_type="text").name == "txtAccountName"
    unresolved = resolver.resolve("txtDifferentLabel", field_type="text")
    assert unresolved.name == "txtDifferentLabel"
    assert not unresolved.matched


def test_resolver_does_not_choose_between_ambiguous_label_matches() -> None:
    resolver = FieldNameResolver({"txtPersonFullName", "txtApplicantFullName"})

    resolution = resolver.resolve("txtFullName", field_type="text", label="Full Name")

    assert resolution.name == "txtFullName"
    assert not resolution.matched


def test_semantic_label_map_resolves_hebrew_or_flat_pdf_labels(tmp_path: Path) -> None:
    semantic_map = tmp_path / "semantic.csv"
    semantic_map.write_text(
        "label,field_name\n"
        "שם בעל החשבון,txtAccountName\n"
        "TIN,txtPersonITIN\n",
        encoding="utf-8",
    )
    known = {"txtAccountName", "txtPersonITIN"}

    aliases = load_semantic_label_aliases(semantic_map, known)
    resolver = FieldNameResolver(known, label_aliases=aliases)

    assert resolver.resolve("txtField", field_type="text", label="שם בעל החשבון").name == "txtAccountName"
    assert resolver.resolve("txtTin", field_type="text", label="TIN").name == "txtPersonITIN"


def test_semantic_label_map_resolves_reversed_hebrew_pdf_labels(tmp_path: Path) -> None:
    label = "\u05d7\u05ea\u05d9\u05de\u05d4"
    reversed_label = label[::-1]
    semantic_map = tmp_path / "semantic.csv"
    semantic_map.write_text(
        f"label,field_name\n{label},imgPersonSignature\n",
        encoding="utf-8",
    )
    known = {"imgPersonSignature"}

    aliases = load_semantic_label_aliases(semantic_map, known)
    resolver = FieldNameResolver(known, label_aliases=aliases)

    resolution = resolver.resolve("imgField", field_type="image", label=reversed_label)

    assert resolution.name == "imgPersonSignature"
    assert resolution.matched
    assert resolution.method == "semantic-label-map"


def test_semantic_label_map_resolves_reversed_hebrew_label_with_prefix(tmp_path: Path) -> None:
    label = "\u05ea\u05d0\u05e8\u05d9\u05da"
    prefixed_label = "\u05d1" + label
    semantic_map = tmp_path / "semantic.csv"
    semantic_map.write_text(
        f"label,field_name\n{label},txtDate\n",
        encoding="utf-8",
    )
    known = {"txtDate"}

    aliases = load_semantic_label_aliases(semantic_map, known)
    resolver = FieldNameResolver(known, label_aliases=aliases)

    resolution = resolver.resolve("txtField", field_type="text", label=prefixed_label[::-1])

    assert resolution.name == "txtDate"
    assert resolution.matched
    assert resolution.method == "semantic-label-map"


def test_semantic_map_can_resolve_generated_field_names(tmp_path: Path) -> None:
    semantic_map = tmp_path / "semantic.csv"
    semantic_map.write_text(
        "name,label,field_name\n"
        "txtField2,TIN,txtPersonITIN\n",
        encoding="utf-8",
    )
    known = {"txtPersonITIN"}

    name_aliases, label_aliases = load_semantic_field_aliases(semantic_map, known)
    resolver = FieldNameResolver(known, aliases=name_aliases, label_aliases=label_aliases)

    assert resolver.resolve("txtField2", field_type="text", label="").name == "txtPersonITIN"


def _write_mapping_workbook(path: Path, rows: list[tuple[str, str, str, str]]) -> Path:
    sheet_rows = [("Current field", "Suggested code field", "Status", "Notes"), *rows]
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<sheetData>"
        + "".join(_row_xml(index, row) for index, row in enumerate(sheet_rows, start=1))
        + "</sheetData></worksheet>"
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Mapping" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    rels_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/></Relationships>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        "</Types>"
    )

    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr("xl/_rels/workbook.xml.rels", rels_xml)
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    return path


def _row_xml(row_index: int, values: tuple[str, str, str, str]) -> str:
    return (
        f'<row r="{row_index}">'
        + "".join(_cell_xml(row_index, col_index, value) for col_index, value in enumerate(values))
        + "</row>"
    )


def _cell_xml(row_index: int, col_index: int, value: str) -> str:
    ref = f"{chr(ord('A') + col_index)}{row_index}"
    return f'<c r="{ref}" t="inlineStr"><is><t>{_escape(value)}</t></is></c>'


def _escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------------------------------------------------------------------------
# XLSX zip-bomb protection
# ---------------------------------------------------------------------------


def _write_oversized_workbook(path: Path, *, decompressed_bytes: int) -> Path:
    """Write an XLSX where sheet1 reports a decompressed size above the limit."""
    import io
    import zipfile as _zipfile

    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<sheetData>"
        '<row r="1"><c r="A1" t="inlineStr"><is><t>Header</t></is></c></row>'
        "</sheetData></worksheet>"
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        "<sheets>"
        '<sheet name="Sheet1" sheetId="1" r:id="rId1"/>'
        "</sheets></workbook>"
    )

    buf = io.BytesIO()
    with _zipfile.ZipFile(buf, "w", compression=_zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", "")
        zf.writestr("xl/workbook.xml", workbook_xml)
        # Manually set file_size on the ZipInfo to simulate a large entry.
        info = _zipfile.ZipInfo("xl/worksheets/sheet1.xml")
        info.compress_type = _zipfile.ZIP_DEFLATED
        zf.writestr(info, sheet_xml)

    # Re-open and patch the file_size in the central directory.
    buf.seek(0)
    raw = buf.read()
    # Write the archive normally; then patch file_size via monkeypatching in the test.
    path.write_bytes(raw)
    return path


class TestXlsxZipBombProtection:
    """_read_xlsx_rows must reject sheets that exceed the decompressed size limit."""

    def test_read_xlsx_rows_raises_on_oversized_sheet(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import zipfile
        import xdp_form_cli.field_name_resolution as fnr_module

        workbook = _write_mapping_workbook(
            tmp_path / "mapping.xlsx",
            [("txtA", "txtB", "Direct match", "safe")],
        )

        # Patch ZipInfo.file_size to exceed the limit.
        original_open = zipfile.ZipFile.open

        def _patched_getinfo(self, name):
            info = self._real_getinfo(name)
            if "sheet" in name:
                info.file_size = fnr_module.MAX_XLSX_DECOMPRESSED_BYTES + 1
            return info

        monkeypatch.setattr(zipfile.ZipFile, "_real_getinfo", zipfile.ZipFile.getinfo, raising=False)
        monkeypatch.setattr(zipfile.ZipFile, "getinfo", _patched_getinfo)

        with pytest.raises(ValueError, match="exceeds maximum"):
            fnr_module._read_xlsx_rows(workbook, sheet_index=1)

    def test_max_xlsx_decompressed_bytes_constant_is_positive(self) -> None:
        import xdp_form_cli.field_name_resolution as fnr_module

        assert hasattr(fnr_module, "MAX_XLSX_DECOMPRESSED_BYTES")
        assert fnr_module.MAX_XLSX_DECOMPRESSED_BYTES > 0
