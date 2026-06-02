import pikepdf, tempfile, decimal
from pathlib import Path
from xdp_form_cli.xfa_stripper import strip_xfa

src = Path(r"C:\Users\NatalieK\Downloads\LLLLLLL2.pdf")
with tempfile.TemporaryDirectory() as tmp:
    stripped = Path(tmp) / "stripped.pdf"
    strip_xfa(src, stripped)
    with pikepdf.Pdf.open(str(stripped)) as pdf:
        page = pdf.pages[0]

        # Find the cm operator and current CTM
        print("=== cm operators ===")
        for token in pikepdf.parse_content_stream(page):
            op = str(token.operator)
            if op == "cm":
                print(f"cm: {[str(v) for v in token.operands]}")

        print()
        print("=== Page MediaBox ===")
        print(page.mediabox)

        # Track CTM to convert text coords to page coords
        # CTM starts as identity; cm multiplies it
        # page coords = CTM * text_coords
        # For cm [a b c d e f]: new_x = a*x + c*y + e, new_y = b*x + d*y + f
        ctm = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]  # [a,b,c,d,e,f] identity

        def apply_ctm(ctm, x, y):
            a, b, c, d, e, f = ctm
            return a*x + c*y + e, b*x + d*y + f

        def mul_matrix(m1, m2):
            # m1 and m2 are [a,b,c,d,e,f] = [[a,b],[c,d]] + translation [e,f]
            a1,b1,c1,d1,e1,f1 = m1
            a2,b2,c2,d2,e2,f2 = m2
            return [
                a1*a2 + c1*b2, b1*a2 + d1*b2,
                a1*c2 + c1*d2, b1*c2 + d1*d2,
                a1*e2 + c1*f2 + e1, b1*e2 + d1*f2 + f1
            ]

        print()
        print("=== Text runs with underscores (page coords) ===")
        tx = ty = 0.0
        tm_a = 1.0
        ctm_stack = [ctm[:]]
        current_ctm = ctm[:]

        for token in pikepdf.parse_content_stream(page):
            op = str(token.operator)
            if op == "q":
                ctm_stack.append(current_ctm[:])
            elif op == "Q":
                if ctm_stack:
                    current_ctm = ctm_stack.pop()
            elif op == "cm":
                vals = [float(v) for v in token.operands]
                current_ctm = mul_matrix(current_ctm, vals)
            elif op == "BT":
                tx = ty = 0.0; tm_a = 1.0
            elif op == "Tm" and len(token.operands) == 6:
                tm_a = float(token.operands[0])
                tx = float(token.operands[4])
                ty = float(token.operands[5])
            elif op == "Td" and len(token.operands) == 2:
                tx += float(token.operands[0])
                ty += float(token.operands[1])
            elif op == "TJ" and 80 < ty < 220:
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
                if "_" in txt or txt.strip():
                    px, py = apply_ctm(current_ctm, tx, ty)
                    # Also apply text matrix scale to understand field width
                    n_under = txt.count("_")
                    print(f"  TJ local=({tx:.1f},{ty:.1f}) page=({px:.1f},{py:.1f}) "
                          f"a={tm_a:.2f} kern={total_kern:.0f} n_={n_under}: {repr(txt[:50])}")
