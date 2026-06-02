import pikepdf, tempfile, decimal
from pathlib import Path
from xdp_form_cli.xfa_stripper import strip_xfa

src = Path(r"C:\Users\NatalieK\Downloads\LLLLLLL2.pdf")
with tempfile.TemporaryDirectory() as tmp:
    stripped = Path(tmp) / "stripped.pdf"
    strip_xfa(src, stripped)
    with pikepdf.Pdf.open(str(stripped)) as pdf:
        page = pdf.pages[0]
        tx = ty = 0.0
        a = 1.0
        for token in pikepdf.parse_content_stream(page):
            op = str(token.operator)
            if op == "BT":
                tx = ty = 0.0
                a = 1.0
            elif op == "Tm" and len(token.operands) == 6:
                a = float(token.operands[0])
                tx = float(token.operands[4])
                ty = float(token.operands[5])
                if 100 < ty < 220:
                    print(f"Tm a={a} tx={tx:.1f} ty={ty:.1f}")
            elif op == "Td" and len(token.operands) == 2:
                tx += float(token.operands[0])
                ty += float(token.operands[1])
            elif op == "TJ" and 100 < ty < 220:
                items = token.operands[0]
                total_kern = 0.0
                txt = ""
                for p in items:
                    if isinstance(p, (int, float, decimal.Decimal)):
                        total_kern += float(p)
                    else:
                        try:
                            txt += bytes(p).decode("latin-1", "ignore")
                        except Exception:
                            pass
                n_under = txt.count("_")
                print(f"  TJ x={tx:.1f} y={ty:.1f} a={a} kern={total_kern:.0f} n_={n_under} text={repr(txt[:60])}")
            elif op == "Tj" and 100 < ty < 220:
                try:
                    txt = bytes(token.operands[0]).decode("latin-1", "ignore")
                except Exception:
                    txt = "?"
                print(f"  Tj x={tx:.1f} y={ty:.1f} text={repr(txt[:60])}")
