#!/usr/bin/env python3
"""Branded quotation PDF for Russia-Philippines Trade Link.

    python3 .claude/skills/quote/make_quote.py quote.json [out.pdf]

quote.json: {"number", "date", "client": {"company", "contact", "email", "country"},
  "currency", "items": [{"name", "qty", "unit", "price"}], "discount_pct",
  "terms": {"Incoterms": "...", "Payment": "...", ...}, "valid_until", "notes", "lang": "en"|"ru"}
Output goes to outputs/ (git-ignored) unless a path is given.
"""
import json, os, sys
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

FONT_DIR = "/usr/share/fonts/truetype/dejavu"
pdfmetrics.registerFont(TTFont("Body", os.path.join(FONT_DIR, "DejaVuSans.ttf")))
pdfmetrics.registerFont(TTFont("Body-Bold", os.path.join(FONT_DIR, "DejaVuSans-Bold.ttf")))

INK, GREEN, MINT, LINE = (colors.HexColor(c) for c in ("#123f35", "#0d5042", "#e5f0eb", "#cbd9d2"))
L = {
    "en": dict(title="Commercial offer", to="Prepared for", item="Item", qty="Qty", unit="Unit",
               price="Unit price", total="Amount", subtotal="Subtotal", discount="Discount",
               grand="Total", terms="Terms", valid="Valid until", note="Prices, availability and documents "
               "are confirmed by the supplier for this inquiry. This offer is not a contract."),
    "ru": dict(title="Коммерческое предложение", to="Для", item="Позиция", qty="Кол-во", unit="Ед.",
               price="Цена", total="Сумма", subtotal="Итого", discount="Скидка", grand="К оплате",
               terms="Условия", valid="Действует до", note="Цены, наличие и документы подтверждаются "
               "поставщиком под эту заявку. Предложение не является договором."),
}


def money(v, cur):
    return f"{cur} {v:,.2f}"


def build(q, out):
    t = L[q.get("lang", "en")]
    cur = q.get("currency", "USD")
    st = lambda size=9, bold=False, color=INK, **kw: ParagraphStyle(
        "s", fontName="Body-Bold" if bold else "Body", fontSize=size, leading=size * 1.35, textColor=color, **kw)
    doc = SimpleDocTemplate(out, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm, title=f"{t['title']} {q['number']}")
    head = Table([[Paragraph("Russia–Philippines Trade Link", st(14, True, colors.white)),
                   Paragraph(f"{t['title']}<br/>№ {q['number']} · {q['date']}", st(9, False, colors.white, alignment=2))]],
                 colWidths=[100 * mm, 74 * mm])
    head.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), GREEN), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                              ("PADDING", (0, 0), (-1, -1), 10)]))
    c = q["client"]
    client = "<br/>".join(x for x in (c.get("company"), c.get("contact"), c.get("email"), c.get("country")) if x)
    rows = [[t["item"], t["qty"], t["unit"], t["price"], t["total"]]]
    subtotal = 0
    for it in q["items"]:
        amount = it["qty"] * it["price"]
        subtotal += amount
        rows.append([Paragraph(it["name"], st()), f"{it['qty']:,}", it.get("unit", ""), money(it["price"], cur), money(amount, cur)])
    disc = round(subtotal * q.get("discount_pct", 0) / 100, 2)
    rows.append(["", "", "", t["subtotal"], money(subtotal, cur)])
    if disc:
        rows.append(["", "", "", f"{t['discount']} {q['discount_pct']}%", "−" + money(disc, cur)])
    rows.append(["", "", "", t["grand"], money(subtotal - disc, cur)])
    n = len(q["items"])
    tbl = Table(rows, colWidths=[72 * mm, 18 * mm, 18 * mm, 32 * mm, 34 * mm], repeatRows=1)
    tbl.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), "Body", 9), ("FONT", (0, 0), (-1, 0), "Body-Bold", 9),
        ("TEXTCOLOR", (0, 0), (-1, -1), INK), ("BACKGROUND", (0, 0), (-1, 0), MINT),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 0), (-1, n), 0.5, LINE), ("FONT", (3, -1), (-1, -1), "Body-Bold", 10),
        ("LINEABOVE", (3, -1), (-1, -1), 1, GREEN), ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    terms = [[Paragraph(k, st(9, True)), Paragraph(str(v), st())] for k, v in q.get("terms", {}).items()]
    if q.get("valid_until"):
        terms.append([Paragraph(t["valid"], st(9, True)), Paragraph(q["valid_until"], st())])
    story = [head, Spacer(1, 8 * mm), Paragraph(t["to"], st(8, color=colors.HexColor("#4a655d"))),
             Paragraph(client, st(10, True)), Spacer(1, 6 * mm), tbl, Spacer(1, 7 * mm)]
    if terms:
        tt = Table(terms, colWidths=[40 * mm, 134 * mm])
        tt.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.5, LINE)]))
        story += [Paragraph(t["terms"], st(11, True)), Spacer(1, 2 * mm), tt, Spacer(1, 6 * mm)]
    if q.get("notes"):
        story += [Paragraph(q["notes"], st()), Spacer(1, 4 * mm)]
    story += [Paragraph(t["note"], st(8, color=colors.HexColor("#4a655d"))), Spacer(1, 4 * mm),
              Paragraph("Andrey · andrey@rusphiltrade.com · rusphiltrade.com", st(9, True, GREEN))]
    doc.build(story)
    return subtotal - disc


if __name__ == "__main__":
    q = json.load(open(sys.argv[1], encoding="utf-8"))
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join("outputs", f"quote-{q['number']}.pdf")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    total = build(q, out)
    print(f"{out}\nTOTAL {q.get('currency', 'USD')} {total:.2f}")
