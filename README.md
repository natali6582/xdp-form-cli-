# XDP Form CLI

Small Python CLI for editing Adobe XDP/XFA files, including PDFs that contain embedded XFA, on a copy and not on the source file.

The tool is fund-agnostic. Do not encode investment fund, real-estate fund, or deal names in the code, reports, examples, or default output names.

## What it does

- Lists page subforms such as `Page1`, `Page26`, `Page33`
- Lists fields inside a page
- Replaces one page subform from an XML fragment file
- Creates AcroForm fields over static PDFs from a CSV field specification
- Validates field specs and generated PDFs before/after AcroForm creation
- Generates text fields as transparent widgets so they do not cover original PDF text
- Generates image/signature placeholders as LiveCycle-style push buttons, not PDF `/Sig` fields
- Converts embedded field names to canonical names extracted from the Plan-T code file
- Forces generated or modified fields to use Arial
- Treats explicitly approved visually-filled fields as known fields
- Can supplement the known field list from accepted mechanized XML/XDP/TXT examples
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

## Internal web app

This repository also includes a small web app for review flows:

- upload a flat PDF and run auto-detection
- download the generated fillable PDF
- download the generated editable CSV
- or upload two PDFs together:
  - template/old PDF that already contains fields
  - blank/new PDF with the same layout
  - the app copies the field structure from the template onto the new PDF

Run locally:

```powershell
python -m flask --app xdp_form_cli.web_app run
```

Open [http://127.0.0.1:5000](http://127.0.0.1:5000).

### Render deployment

Use Render as a **Docker Web Service**, not a plain Python Web Service. The detection flow depends on Poppler tools such as `pdftotext` and `pdftoppm`, and the Docker image installs them explicitly.

Recommended setup:

- Repository: `natali6582/xdp-form-cli`
- Runtime: `Docker`
- Branch: `master`
- Plan: at least `Starter`
- Health check path: `/healthz`

You can either create the service from `render.yaml` or create a new Docker web service and let Render detect the repository `Dockerfile`.

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

Create AcroForm fields over a static PDF:

```powershell
xdp-form-cli create-acroform --input "C:\path\static.pdf" --fields "C:\path\field-spec.csv" --output "C:\path\static_with_fields.pdf"
```

Field-spec CSV format:

```csv
page,name,type,x,y,w,h,value
1,txtInvestorName,text,120,650,240,18,
1,chkQualifiedInvestor,checkbox,92,610,12,12,0
1,txtNotes,textarea,120,520,320,60,
1,imgInvestorSignature,image,120,470,180,28,
```

Supported field types are `text`, `textarea`, `checkbox`, and `image`. All signature placeholders, including names such as `imgPersonSignature`, must use `type=image`; real PDF digital-signature fields (`/Sig`) are intentionally not supported. Image fields are generated as regular push-button placeholders with push highlighting, raised border, and grey button fill. Coordinates are PDF points from the bottom-left of the page.

Validate a field-spec CSV before creating a PDF:

```powershell
xdp-form-cli validate-acroform --input "C:\path\static.pdf" --fields "C:\path\field-spec.csv"
```

Validate both the CSV and an already generated PDF:

```powershell
xdp-form-cli validate-acroform --input "C:\path\static.pdf" --fields "C:\path\field-spec.csv" --pdf "C:\path\static_with_fields.pdf"
```

Use strict validation when duplicate names and other warnings should fail the command:

```powershell
xdp-form-cli validate-acroform --input "C:\path\static.pdf" --fields "C:\path\field-spec.csv" --pdf "C:\path\static_with_fields.pdf" --strict
```

`create-acroform` runs validation automatically before and after creating the output PDF. Add `--strict-validation` when warnings should block creation. If `pdftoppm` and Pillow are available, validation also renders the source PDF and warns when a field rectangle appears to sit on existing dark content, which can indicate that the field may overlap original text.

Build a fillable PDF from a client-uploaded flat PDF with no existing fields:

```powershell
xdp-form-cli auto-client-form --input "C:\path\uploaded.pdf" --output "C:\path\uploaded_acroform.pdf" --fields-csv "C:\path\uploaded_fields.csv"
```

`auto-client-form` detects visible field areas, including text underlines/boxes, vector checkboxes, glyph-rendered checkboxes, and signature labels when they can be read from the PDF. It keeps fill lines that have a label before, after, or below the line, including cases such as `example____`, `____example`, `example____example`, `*____`, `By: ____`, and `Date: ____`. It also supports repeated underscore/CID glyph lines, multiple fields on the same row, and long labels below a line such as an approved commitment amount. The detector filters likely underlined headings, normal text tables, and fields that would cover existing dark printed content. It writes both the fillable PDF and an editable CSV; review the CSV before production use because flat PDFs can contain font-encoded labels that cannot always be decoded reliably.

Convert field names in a PDF using the default Plan-T code file:

```powershell
xdp-form-cli convert-fields --input "C:\path\form.pdf" --output "C:\path\form_converted.pdf" --report "C:\path\form_converted_report.csv"
```

Convert field names using an explicit Plan-T code file:

```powershell
xdp-form-cli convert-fields --input "C:\path\form.pdf" --output "C:\path\form_converted.pdf" --truth-code "C:\path\PDFFormsBL_plan-t.cs" --report "C:\path\form_converted_report.csv"
```

Convert field names and also learn accepted `txt`/`chk`/`img` field names from mechanized XML examples:

```powershell
xdp-form-cli convert-fields --input "C:\path\form.pdf" --output "C:\path\form_converted.pdf" --examples "C:\path\accepted-form.xml" --report "C:\path\form_converted_report.csv"
```

`--examples` can point to one XML/XDP/TXT file or to a directory. It can also be repeated.

## Fragment format

The fragment file should contain one `<subform ...>...</subform>` block for the target page.

Example:

```xml
<subform name="Page26" x="0pt" y="0pt" w="612pt" h="792pt">
   <break before="pageArea" beforeTarget="#PageArea1" startNew="1"/>
   <bind match="none"/>
</subform>
```

## Optional placement-analysis dependencies

The standalone underline-detection prototype uses optional `Pillow` and `numpy` dependencies. Install them with:

```powershell
python -m pip install -e .[placement]
```

If these libraries are missing, placement analysis must warn and skip rather than hard-crashing. This check is not wired into validation until its detection results are approved.

## Notes

- Do not pretty-print the output XML. This tool writes compact XML to reduce layout and formatting risk.
- Any field created or modified by the tool should use Arial. Avoid LiveCycle/default fonts such as Myriad Pro in generated field XML.
- PDF support requires a real embedded XFA packet at `/Root` -> `/AcroForm` -> `/XFA`.
- Static PDFs do not contain field names or coordinates. Use `create-acroform` with a field-spec CSV to add real AcroForm fields before trying to fill or validate fields.
- Validation checks required cells, supported types, duplicate names, duplicate signature names, image-signature format, field sizes, page bounds, repeated beneficiary table rows, likely overlap with original text, PDF field count, transparent text/image widgets, checkbox appearances, and unexpected `/Sig` digital-signature fields.
- `convert-fields` uses the `PDFFormsBL*plan-t.cs` file by default unless `--truth-code` is provided.
- `approved_visual_fields.py` contains fields that are considered valid because they are filled visually in accepted forms.
- Example files are supplemental, not a blind import. Only field names that look like Plan-T data fields (`txt...`, `chk...`, `img...`) are added. Generic LiveCycle names such as `CheckBox20` are ignored.
- The conversion is conservative. Exact known names stay as-is, clear canonical reductions are renamed, and uncertain names remain unchanged and appear in the report.
- Encrypted, signed, certified, or Reader-extended PDFs may reject edits or lose validation/signature status after saving a modified copy. Real PDF digital-signature fields are rejected; signatures are represented as image fields only.
- This tool edits existing XFA XML when present. For static PDFs, it can create AcroForm fields from explicit coordinates, but it does not infer field placement automatically.
