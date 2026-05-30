# XDP Form CLI

Small Python CLI for editing Adobe XDP/XFA files, including PDFs that contain embedded XFA, on a copy and not on the source file.

## What it does

- Lists page subforms such as `Page1`, `Page26`, `Page33`
- Lists fields inside a page
- Replaces one page subform from an XML fragment file
- Preserves the source file by always writing to a new output file
- Supports PDF input/output when the PDF contains an embedded `/AcroForm` `/XFA` packet
- Prints progress in color in the terminal

## Requirements

- Python 3.10+
- `lxml`
- `pikepdf`

## Install locally

```powershell
python -m pip install -e .
```

## Commands

List pages in a standalone XDP:

```powershell
xdp-form-cli list-pages --input "C:\path\form.xdp"
```

List pages in a PDF with embedded XFA:

```powershell
xdp-form-cli list-pages --input "C:\path\form.pdf"
```

List fields in a page:

```powershell
xdp-form-cli list-fields --input "C:\path\form.xdp" --page Page26
```

Replace a page in a standalone XDP from a fragment XML file:

```powershell
xdp-form-cli replace-page --input "C:\path\form.xdp" --page Page26 --fragment "C:\path\Page26.xml" --output "C:\path\form_copy.xdp"
```

Replace a page inside a PDF with embedded XFA and write a new PDF:

```powershell
xdp-form-cli replace-page --input "C:\path\form.pdf" --page Page26 --fragment "C:\path\Page26.xml" --output "C:\path\form_copy.pdf"
```

## Fragment format

The fragment file should contain one `<subform ...>...</subform>` block for the target page.

Example:

```xml
<subform name="Page26" x="0pt" y="0pt" w="612pt" h="792pt">
   <break before="pageArea" beforeTarget="#PageArea1" startNew="1"/>
   <bind match="none"/>
</subform>
```

## Notes

- Do not pretty-print the output XML. This tool writes compact XML to reduce layout and formatting risk.
- PDF support requires a real embedded XFA packet at `/Root` -> `/AcroForm` -> `/XFA`.
- Encrypted, signed, certified, or Reader-extended PDFs may reject edits or lose validation/signature status after saving a modified copy.
- This tool edits the XFA XML. It does not redraw static PDF page content or convert XFA forms into AcroForms.
