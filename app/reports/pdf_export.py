"""PDF denetim raporu (fpdf2). Font: DejaVu Sans (fonts/LICENSE-DejaVu.txt)."""
from __future__ import annotations

from pathlib import Path

from fpdf import FPDF
from fpdf.fonts import FontFace

FONTS = Path(__file__).parent / "fonts"
COLORS = {"approved": (26, 127, 75), "conditional": (154, 100, 0), "improvement_required": (180, 35, 24), "rejected": (122, 16, 16)}
SEV = {"kritik": (122, 16, 16), "yüksek": (180, 35, 24), "orta": (154, 100, 0), "düşük": (71, 84, 103)}
ANS = {"uygun": (26, 127, 75), "kısmen": (154, 100, 0), "uygun değil": (180, 35, 24)}


class _PDF(FPDF):
    def __init__(self, report: dict):
        super().__init__(format="A4")
        self.r = report
        self.add_font("DejaVu", "", str(FONTS / "DejaVuSans.ttf"))
        self.add_font("DejaVu", "B", str(FONTS / "DejaVuSans-Bold.ttf"))
        self.set_auto_page_break(True, margin=16)
        self.set_margins(14, 14, 14)
        self.set_title(f"Denetim Raporu — {report['supplier']['name']}")
        self.set_creator("Tedarikçi Denetim")

    def header(self):
        if self.page_no() == 1:
            return
        self.set_font("DejaVu", "", 8)
        self.set_text_color(110)
        self.cell(0, 6, f"{self.r['supplier']['name']} · Denetim #{self.r['audit']['id']} · {self.r['audit']['audit_date']}",
                  new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def footer(self):
        self.set_y(-12)
        self.set_font("DejaVu", "", 8)
        self.set_text_color(130)
        self.cell(0, 6, f"Oluşturulma: {self.r['generated_at'].replace('T', ' ')}", align="L")
        self.set_x(-40)
        self.cell(26, 6, f"Sayfa {self.page_no()}/{{nb}}", align="R")

    def h2(self, text):
        self.ln(3)
        self.set_font("DejaVu", "B", 12)
        self.set_text_color(29, 35, 48)
        self.cell(0, 8, text, new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(226, 229, 234)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(2)


def to_pdf(report: dict) -> bytes:
    r, res = report, report["result"]
    pdf = _PDF(r)
    pdf.add_page()
    # Başlık
    pdf.set_font("DejaVu", "", 9)
    pdf.set_text_color(110)
    pdf.cell(0, 5, ("TEDARİKÇİ DENETİM RAPORU" + (f" · {r['org_name']}" if r["org_name"] else "")), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("DejaVu", "B", 18)
    pdf.set_text_color(29, 35, 48)
    pdf.multi_cell(0, 9, r["supplier"]["name"], new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("DejaVu", "", 10)
    pdf.set_text_color(80)
    meta = [f"Denetim tarihi: {r['audit']['audit_date']}", f"Tür: {r['audit']['audit_type']}",
            f"Denetçi: {r['audit']['auditor'] or '—'}", f"Durum: {r['audit']['status']}"]
    sup = [x for x in (r["supplier"]["category"], r["supplier"]["country"], r["supplier"]["tier"],
                       f"Kritiklik: {r['supplier']['criticality']}") if x]
    pdf.multi_cell(0, 5, " · ".join(meta) + "\n" + " · ".join(sup) + "\nSoru setleri: " + (", ".join(r["template_names"]) or "—"),
                   new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    # Sonuç kutusu
    col = COLORS.get(res["decision"], (71, 84, 103))
    y = pdf.get_y()
    pdf.set_fill_color(245, 246, 248)
    pdf.rect(pdf.l_margin, y, pdf.w - pdf.l_margin - pdf.r_margin, 24, style="F")
    pdf.set_xy(pdf.l_margin + 4, y + 3)
    pdf.set_font("DejaVu", "B", 22)
    pdf.set_text_color(*col)
    pdf.cell(32, 12, "—" if res["score"] is None else f"{res['score']:.1f}".replace(".", ","))
    pdf.set_font("DejaVu", "B", 12)
    pdf.cell(68, 12, ("Reddedildi (kritik madde)" if res["kill_switches"] else res["decision_tr"]) or "Değerlendirilmedi")
    pdf.set_font("DejaVu", "", 10)
    pdf.set_text_color(80)
    pdf.cell(0, 12, f"Risk: {res['risk_tr'] or '—'} · {res['answered']}/{res['visible']} madde cevaplı")
    pdf.set_xy(pdf.l_margin + 4, y + 15)
    pdf.set_font("DejaVu", "", 8)
    pdf.cell(0, 5, "Ağırlıklı ortalama: Uygun 1 · Kısmen 0,5 · Uygun değil 0 (N/A hariç) · ≥85 Onaylı · ≥70 Şartlı · ≥50 Gelişim gerekli")
    pdf.set_y(y + 27)
    if res["kill_switches"]:
        pdf.set_fill_color(253, 231, 229)
        pdf.set_text_color(122, 16, 16)
        pdf.set_font("DejaVu", "B", 10)
        pdf.multi_cell(0, 6, "KILL-SWITCH: Kritik madde uygun değil — denetim puandan bağımsız olarak REDDEDİLDİ.",
                       fill=True, align="L", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("DejaVu", "", 9)
        for k in res["kill_switches"]:
            pdf.multi_cell(0, 5, f"• [{k['section']}] {k['text']}", fill=True, align="L", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)
    pdf.set_fill_color(255, 255, 255)
    # Bölüm puanları
    pdf.h2("Bölüm puanları")
    pdf.set_font("DejaVu", "", 9)
    pdf.set_text_color(29, 35, 48)
    head = FontFace(emphasis="BOLD", fill_color=(245, 246, 248))
    with pdf.table(col_widths=(110, 25, 25), headings_style=head, line_height=6, text_align=("LEFT", "RIGHT", "RIGHT")) as t:
        t.row(["Bölüm", "Puan", "Cevaplı"])
        for s in res["sections"]:
            t.row([s["title"] + (f"  ({s['source']})" if s.get("source") and len(r["template_names"]) > 1 else ""),
                   "—" if s["score"] is None else f"{s['score']:.0f}", f"{s['answered']}/{s['questions']}"])
    # Maddeler
    pdf.h2("Denetim maddeleri")
    pdf.set_font("DejaVu", "", 8)
    with pdf.table(col_widths=(88, 22, 52, 12), headings_style=head, line_height=4.6, text_align="LEFT") as t:
        t.row(["Madde", "Cevap", "Not", "Kanıt"])
        cur = None
        for it in r["items"]:
            if it["section"] != cur:
                cur = it["section"]
                rr = t.row()
                rr.cell(cur, colspan=4, style=FontFace(emphasis="BOLD", fill_color=(232, 238, 252)))
            rr = t.row()
            rr.cell(("[KRİTİK] " if it["critical"] else "") + it["question"] + (f"  ×{it['weight']:g}" if it["weight"] != 1 else ""))
            rr.cell(it["answer_tr"], style=FontFace(color=ANS.get(it["answer"], (110, 110, 110)), emphasis="BOLD"))
            rr.cell(it["note"])
            rr.cell(str(it["evidence"] or ""))
    # CAPA
    if r["capas"]:
        pdf.h2(f"Düzeltici faaliyetler (CAPA) — {len(r['capas'])}")
        pdf.set_font("DejaVu", "", 8)
        with pdf.table(col_widths=(18, 96, 28, 32), headings_style=head, line_height=4.6, text_align="LEFT") as t:
            t.row(["Önem", "CAPA", "Durum", "Termin"])
            for c in r["capas"]:
                rr = t.row()
                rr.cell(c["severity"], style=FontFace(color=SEV.get(c["severity"], (80, 80, 80)), emphasis="BOLD"))
                rr.cell(c["title"])
                rr.cell(c["status"])
                rr.cell(c["due_date"] or "—")
    if r["audit"]["notes"]:
        pdf.h2("Denetçi notları")
        pdf.set_font("DejaVu", "", 9)
        pdf.multi_cell(0, 5, r["audit"]["notes"], new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())
