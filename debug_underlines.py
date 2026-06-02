import pikepdf, tempfile, decimal
from pathlib import Path
from xdp_form_cli.xfa_stripper import strip_xfa

src = Path(r"C:\Users\NatalieK\Downloads\LLLLLLL2.pdf")
with tempfile.TemporaryDirectory() as tmp:
    stripped = Path(tmp) / "stripped.pdf"
    strip_xfa(src, stripped)
    with pikepdf.Pdf.open(str(stripped)) as pdf:
        page = pdf.pages[0]

        # Read all fonts on the page
        fonts = {}
        res = page.resources
        if "/Font" in res:
            for fname, fobj in res["/Font"].items():
                first_char = int(fobj.get("/FirstChar", 0))
                widths = fobj.get("/Widths", [])
                underscore_code = 95
                idx = underscore_code - first_char
                if 0 <= idx < len(widths):
                    w = float(widths[idx])  # in 1/1000 em
                else:
                    w = None
                subtype = str(fobj.get("/Subtype", "?"))
                base = str(fobj.get("/BaseFont", "?"))
                fonts[fname] = {"advance_1000": w, "subtype": subtype, "base": base}
                print(f"Font {fname}: {base} ({subtype}), '_' advance = {w}")

        print()
        # Now show underline widths with exact advance
        tm_a = 1.0; tf_size = 10.0; tf_name = None
        page_tx = page_ty = 0.0; tm_e = tm_f = 0.0; tm_d = 1.0

        for token in pikepdf.parse_content_stream(page):
            op = str(token.operator)
            if op == "BT":
                tm_a = tm_d = 1.0; tm_e = tm_f = page_tx = page_ty = 0.0; tf_size = 10.0
            elif op == "Tf":
                try:
                    tf_name = str(token.operands[0])
                    tf_size = float(token.operands[1])
                except: pass
            elif op == "Tm" and len(token.operands) == 6:
                tm_a = float(token.operands[0]); tm_d = float(token.operands[3])
                tm_e = float(token.operands[4]); tm_f = float(token.operands[5])
                page_tx = tm_e; page_ty = tm_f
            elif op in ("Td", "TD") and len(token.operands) == 2:
                page_tx += float(token.operands[0]) * tm_a
                page_ty += float(token.operands[1]) * tm_d
            elif op in ("TJ", "Tj") and 100 < page_ty < 220:
                if op == "Tj":
                    try: txt = bytes(token.operands[0]).decode("latin-1", "ignore")
                    except: continue
                else:
                    txt = ""
                    for p in token.operands[0]:
                        if isinstance(p, (int, float, decimal.Decimal)): continue
                        try: txt += bytes(p).decode("latin-1", "ignore")
                        except: pass

                if "_" not in txt:
                    continue

                under_idx = txt.index("_")
                n_under = len(txt[under_idx:].rstrip()) - len(txt[under_idx:].rstrip().lstrip("_"))
                if n_under < 5:
                    continue

                # exact width from font
                adv_1000 = fonts.get(tf_name, {}).get("advance_1000") if tf_name else None
                if adv_1000 is not None:
                    char_w = adv_1000 / 1000.0 * tf_size * abs(tm_a)
                    method = "font_metrics"
                else:
                    char_w = abs(tm_a) * tf_size * 0.55
                    method = "estimate"

                prefix_w = under_idx * char_w
                field_x = page_tx + prefix_w
                field_w = n_under * char_w
                print(f"n={n_under} underscores, char_w={char_w:.3f}pt [{method}]")
                print(f"  field_x={field_x:.1f}  field_w={field_w:.1f}  (ends at {field_x+field_w:.1f})")
                print(f"  text: {repr(txt[:50])}")
