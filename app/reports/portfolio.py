"""Portföy (yönetim kurulu) raporu ve DORA bilgi kaydı hazırlık çıktısı."""
from __future__ import annotations

import csv
import datetime as dt
import io
from pathlib import Path

from fpdf import FPDF
from fpdf.fonts import FontFace

from ..db import get_db, rows

FONTS = Path(__file__).parent / "fonts"
RISK_COLOR = {"yüksek": (185, 28, 28), "orta": (180, 83, 9), "düşük": (21, 128, 61), "bilinmiyor": (148, 163, 184)}
TR_MONTHS = ["Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"]


# ------------------------------------------------------------------ saf yardımcılar
def month_keys(today: dt.date, months: int = 12) -> list[str]:
    y, m, out = today.year, today.month, []
    for _ in range(months):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out[::-1]


def monthly_trend(audits: list[dict], findings: list[dict], today: dt.date, months: int = 12) -> list[dict]:
    """Saf: ay bazında tamamlanan denetim sayısı, ortalama puan, açılan/kapanan bulgu."""
    keys = month_keys(today, months)
    acc = {k: {"month": k, "audits": 0, "score_sum": 0.0, "opened": 0, "closed": 0} for k in keys}
    for a in audits:
        k = (a.get("completed_at") or a.get("audit_date") or "")[:7]
        if k in acc:
            acc[k]["audits"] += 1
            acc[k]["score_sum"] += a.get("score") or 0
    for f in findings:
        if (f.get("created_at") or "")[:7] in acc:
            acc[f["created_at"][:7]]["opened"] += 1
        if (f.get("closed_at") or "")[:7] in acc:
            acc[f["closed_at"][:7]]["closed"] += 1
    out = []
    for k in keys:
        r = acc[k]
        out.append({"month": k, "label": TR_MONTHS[int(k[5:]) - 1] + " " + k[2:4], "audits": r["audits"],
                    "avg_score": round(r["score_sum"] / r["audits"], 1) if r["audits"] else None, "opened": r["opened"], "closed": r["closed"]})
    return out


def group_stats(suppliers: list[dict], key: str) -> list[dict]:
    """Saf: tier veya kategoriye göre adet, ortalama puan, yüksek risk sayısı."""
    g: dict[str, dict] = {}
    for s in suppliers:
        k = s.get(key) or "Atanmamış"
        e = g.setdefault(k, {"group": k, "count": 0, "scores": [], "high": 0})
        e["count"] += 1
        if s["summary"]["overall_score"] is not None:
            e["scores"].append(s["summary"]["overall_score"])
        e["high"] += s["summary"]["risk"] == "yüksek"
    return sorted([{"group": e["group"], "count": e["count"], "high": e["high"],
                    "avg": round(sum(e["scores"]) / len(e["scores"]), 1) if e["scores"] else None} for e in g.values()],
                  key=lambda x: (-x["high"], -x["count"]))


# ------------------------------------------------------------------ veri
def build_portfolio(today: dt.date | None = None) -> dict:
    from ..main import _summary
    from .. import summary as summ
    today = today or dt.date.today()
    with get_db() as db:
        sups = rows(db.execute("SELECT s.*, t.name AS tier_name, t.level AS tier_level FROM suppliers s LEFT JOIN supplier_tiers t "
                               "ON t.id=s.tier_id WHERE s.status NOT IN ('pasif')").fetchall())
        for s in sups:
            s["summary"] = _summary(db, s)
        since = (today.replace(day=1) - dt.timedelta(days=370)).isoformat()
        audits = rows(db.execute("SELECT audit_date, completed_at, score FROM audits WHERE status='tamamlandı' AND "
                                 "COALESCE(completed_at, audit_date) >= ?", (since,)).fetchall())
        finds = rows(db.execute("SELECT created_at, closed_at FROM findings WHERE created_at >= ? OR closed_at >= ?", (since, since)).fetchall())
        org = db.execute("SELECT value FROM settings WHERE key='org_name'").fetchone()
    sm = summ.summary(60, None)
    rank = {"yüksek": 0, "orta": 1, "düşük": 2, "bilinmiyor": 3}
    top = sorted(sups, key=lambda s: (rank[s["summary"]["risk"]], s["summary"]["overall_score"] if s["summary"]["overall_score"] is not None else 999))
    return {
        "generated_at": dt.datetime.now().isoformat(timespec="minutes"), "org_name": org[0] if org and org[0] else None,
        "totals": {"suppliers": len(sups), "high": sum(s["summary"]["risk"] == "yüksek" for s in sups),
                   "rejected": sum(s["summary"]["audit_decision"] == "rejected" for s in sups),
                   "open_findings": sum(s["summary"]["open_findings"] for s in sups), "open_capa": sm["capa"]["open"],
                   "overdue_capa": sm["capa"]["overdue"], "sanctions": sum(s["summary"]["sanctions"] in ("olası eşleşme", "doğrulandı") for s in sups),
                   "avg_score": round(sum(x) / len(x), 1) if (x := [s["summary"]["overall_score"] for s in sups if s["summary"]["overall_score"] is not None]) else None},
        "risk": {k: sum(s["summary"]["risk"] == k for s in sups) for k in rank},
        "by_tier": group_stats(sups, "tier_name"), "by_category": group_stats(sups, "category")[:10],
        "top": [{"name": s["name"], "tier": s["tier_name"], "category": s["category"], **{k: s["summary"][k] for k in
                 ("risk", "overall_score", "audit_decision", "intel_grade", "open_capa", "overdue_capa", "sanctions")}} for s in top[:15]],
        "trend": monthly_trend(audits, finds, today), "certificates": sm["certificates"]["items"][:10],
        "contracts": (sm.get("contracts") or [])[:10], "capa": sm["capa"],
    }


# ------------------------------------------------------------------ PDF
class _PDF(FPDF):
    def __init__(self, d):
        super().__init__(format="A4")
        self.d = d
        self.add_font("DejaVu", "", str(FONTS / "DejaVuSans.ttf"))
        self.add_font("DejaVu", "B", str(FONTS / "DejaVuSans-Bold.ttf"))
        self.set_auto_page_break(True, 16)
        self.set_margins(14, 14, 14)

    def footer(self):
        self.set_y(-12)
        self.set_font("DejaVu", "", 8)
        self.set_text_color(130)
        self.cell(0, 6, f"Tedarikçi Portföy Raporu · {self.d['generated_at'].replace('T', ' ')}")
        self.set_x(-40)
        self.cell(26, 6, f"Sayfa {self.page_no()}/{{nb}}", align="R")

    def h2(self, t):
        self.ln(3)
        self.set_font("DejaVu", "B", 12)
        self.set_text_color(15, 23, 42)
        self.cell(0, 8, t, new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(226, 232, 240)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(2)


def portfolio_pdf(d: dict) -> bytes:
    pdf = _PDF(d)
    pdf.add_page()
    head = FontFace(emphasis="BOLD", fill_color=(248, 250, 252))
    pdf.set_font("DejaVu", "", 9)
    pdf.set_text_color(100)
    pdf.cell(0, 5, "YÖNETİM RAPORU" + (f" · {d['org_name']}" if d["org_name"] else ""), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("DejaVu", "B", 18)
    pdf.set_text_color(15, 23, 42)
    pdf.cell(0, 10, "Tedarikçi Risk Portföyü", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("DejaVu", "", 9)
    pdf.set_text_color(100)
    pdf.cell(0, 5, dt.date.fromisoformat(d["generated_at"][:10]).strftime("%d.%m.%Y"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    # metrik şeridi
    t = d["totals"]
    metrics = [("Aktif tedarikçi", t["suppliers"]), ("Yüksek risk", t["high"]), ("Reddedilen", t["rejected"]),
               ("Ort. olgunluk", "—" if t["avg_score"] is None else f"{t['avg_score']:.1f}".replace(".", ",")),
               ("Açık CAPA", t["open_capa"]), ("Geciken CAPA", t["overdue_capa"]), ("Yaptırım uyarısı", t["sanctions"])]
    w = (pdf.w - 28) / len(metrics)
    y = pdf.get_y()
    pdf.set_draw_color(226, 232, 240)
    for i, (lbl, v) in enumerate(metrics):
        x = 14 + i * w
        pdf.rect(x, y, w, 18)
        pdf.set_xy(x + 2, y + 2)
        pdf.set_font("DejaVu", "", 7)
        pdf.set_text_color(100)
        pdf.cell(w - 4, 4, lbl)
        pdf.set_xy(x + 2, y + 7)
        pdf.set_font("DejaVu", "B", 14)
        pdf.set_text_color(*(RISK_COLOR["yüksek"] if lbl in ("Yüksek risk", "Reddedilen", "Geciken CAPA", "Yaptırım uyarısı") and v else (15, 23, 42)))
        pdf.cell(w - 4, 8, str(v))
    pdf.set_y(y + 22)
    # risk dağılımı çubuğu
    pdf.h2("Risk dağılımı")
    total = max(1, sum(d["risk"].values()))
    x, y, W = 14, pdf.get_y(), pdf.w - 28
    for k in ("yüksek", "orta", "düşük", "bilinmiyor"):
        ww = W * d["risk"][k] / total
        if ww:
            pdf.set_fill_color(*RISK_COLOR[k])
            pdf.rect(x, y, ww, 5, style="F")
            x += ww
    pdf.set_fill_color(255, 255, 255)
    pdf.set_y(y + 7)
    pdf.set_font("DejaVu", "", 8)
    pdf.set_text_color(60)
    pdf.cell(0, 5, "   ".join(f"{k.capitalize() if k != 'bilinmiyor' else 'Değerlendirilmedi'}: {d['risk'][k]}" for k in d["risk"]),
             new_x="LMARGIN", new_y="NEXT")
    # en riskli tedarikçiler
    pdf.h2("Öncelikli tedarikçiler")
    pdf.set_font("DejaVu", "", 8)
    pdf.set_text_color(15, 23, 42)
    dec = {"approved": "Onaylı", "conditional": "Şartlı", "improvement_required": "Gelişim gerekli", "rejected": "Reddedildi"}
    with pdf.table(col_widths=(52, 22, 18, 16, 26, 14, 16, 18), headings_style=head, line_height=5, text_align="LEFT") as tb:
        tb.row(["Tedarikçi", "Tier", "Risk", "Puan", "Son karar", "Siber", "Açık CAPA", "Yaptırım"])
        for s in d["top"]:
            r = tb.row()
            r.cell(s["name"])
            r.cell((s["tier"] or "—").split(" ")[0] + " " + ((s["tier"] or "").split(" ")[1] if s["tier"] and " " in s["tier"] else ""))
            r.cell(s["risk"] if s["risk"] != "bilinmiyor" else "—", style=FontFace(color=RISK_COLOR[s["risk"]], emphasis="BOLD"))
            r.cell("—" if s["overall_score"] is None else f"{s['overall_score']:.1f}".replace(".", ","))
            r.cell(dec.get(s["audit_decision"], "—"))
            r.cell(s["intel_grade"] or "—")
            r.cell(f"{s['open_capa']}" + (f" ({s['overdue_capa']} geç)" if s["overdue_capa"] else ""))
            r.cell(s["sanctions"] if s["sanctions"] in ("olası eşleşme", "doğrulandı") else "—")
    # tier / kategori
    pdf.h2("Tier ve kategori kırılımı")
    pdf.set_font("DejaVu", "", 8)
    with pdf.table(col_widths=(70, 25, 30, 30), headings_style=head, line_height=5, text_align=("LEFT", "RIGHT", "RIGHT", "RIGHT")) as tb:
        tb.row(["Tier", "Tedarikçi", "Ort. olgunluk", "Yüksek risk"])
        for g in d["by_tier"]:
            tb.row([g["group"], str(g["count"]), "—" if g["avg"] is None else f"{g['avg']:.1f}".replace(".", ","), str(g["high"])])
    pdf.ln(2)
    with pdf.table(col_widths=(70, 25, 30, 30), headings_style=head, line_height=5, text_align=("LEFT", "RIGHT", "RIGHT", "RIGHT")) as tb:
        tb.row(["Kategori", "Tedarikçi", "Ort. olgunluk", "Yüksek risk"])
        for g in d["by_category"]:
            tb.row([g["group"], str(g["count"]), "—" if g["avg"] is None else f"{g['avg']:.1f}".replace(".", ","), str(g["high"])])
    # trend
    pdf.h2("Son 12 ay")
    tr = d["trend"]
    mx = max([1] + [m["opened"] for m in tr] + [m["closed"] for m in tr] + [m["audits"] for m in tr])
    x0, y0, cw, chh = 14, pdf.get_y() + 2, (pdf.w - 28) / len(tr), 30
    for i, m in enumerate(tr):
        x = x0 + i * cw
        for j, (val, col) in enumerate(((m["audits"], (15, 23, 42)), (m["opened"], (185, 28, 28)), (m["closed"], (21, 128, 61)))):
            h = chh * val / mx
            pdf.set_fill_color(*col)
            if h:
                pdf.rect(x + 2 + j * (cw - 4) / 3, y0 + chh - h, (cw - 6) / 3, h, style="F")
        pdf.set_xy(x, y0 + chh + 1)
        pdf.set_font("DejaVu", "", 6)
        pdf.set_text_color(100)
        pdf.cell(cw, 3, m["label"], align="C")
    pdf.set_y(y0 + chh + 6)
    pdf.set_font("DejaVu", "", 7)
    for lbl, col in (("Tamamlanan denetim", (15, 23, 42)), ("Açılan bulgu", (185, 28, 28)), ("Kapanan bulgu", (21, 128, 61))):
        pdf.set_fill_color(*col)
        pdf.rect(pdf.get_x(), pdf.get_y() + 1, 3, 3, style="F")
        pdf.set_x(pdf.get_x() + 4)
        pdf.set_text_color(80)
        pdf.cell(pdf.get_string_width(lbl) + 6, 5, lbl)
    pdf.set_fill_color(255, 255, 255)
    pdf.ln(6)
    # yaklaşanlar
    if d["certificates"] or d["contracts"]:
        pdf.h2("Yaklaşan bitişler (60 gün)")
        pdf.set_font("DejaVu", "", 8)
        pdf.set_text_color(15, 23, 42)
        with pdf.table(col_widths=(20, 60, 70, 24), headings_style=head, line_height=5, text_align="LEFT") as tb:
            tb.row(["Tür", "Tedarikçi", "Belge / sözleşme", "Bitiş"])
            for c in d["certificates"]:
                tb.row(["Sertifika", c["supplier_name"], c.get("title") or c.get("original_name") or "", c["expires_at"]])
            for c in d["contracts"]:
                tb.row(["Sözleşme", c["supplier_name"], c["title"], c["end_date"]])
    return bytes(pdf.output())


# ------------------------------------------------------------------ DORA bilgi kaydı (hazırlık)
DORA_COLUMNS = [
    "Sağlayıcı adı", "LEI / kimlik no", "Vergi / sicil no", "Ülke", "Hizmet türü (kategori)", "Desteklenen işlev kritik/önemli mi",
    "Tier", "Sözleşme", "Sözleşme no", "Başlangıç", "Bitiş", "Fesih ihbar süresi (gün)", "Otomatik yenileme", "Yıllık bedel",
    "Para birimi", "Veri işleme sözleşmesi", "Denetim hakkı / güvenlik maddesi", "İkame edilebilirlik", "Veri / hizmet lokasyonu",
    "Son değerlendirme tarihi", "Son değerlendirme kararı",
]


def dora_register_csv() -> bytes:
    from ..auth import scoped
    w, p = scoped("s.id")
    subst = {"kolay": "kolay", "orta": "orta", "zor": "zor / tek kaynak"}
    with get_db() as db:
        data = rows(db.execute(
            "SELECT s.id, s.name, s.lei, s.tax_no, s.country, s.category, s.criticality, t.name AS tier, t.level, "
            "c.title, c.contract_no, c.start_date, c.end_date, c.notice_days, c.auto_renew, c.value, c.currency, c.has_dpa, "
            "c.has_security_clause, (SELECT answers FROM supplier_intake i WHERE i.supplier_id=s.id ORDER BY i.id DESC LIMIT 1) AS intake, "
            "(SELECT audit_date || '|' || COALESCE(decision,'') FROM audits a WHERE a.supplier_id=s.id AND a.status='tamamlandı' "
            " ORDER BY audit_date DESC LIMIT 1) AS last_audit "
            "FROM suppliers s LEFT JOIN supplier_tiers t ON t.id=s.tier_id LEFT JOIN contracts c ON c.supplier_id=s.id "
            "WHERE s.status != 'pasif'" + w + " ORDER BY s.name, c.end_date", p).fetchall())
    import json
    dec = {"approved": "Onaylı", "conditional": "Şartlı onaylı", "improvement_required": "Gelişim gerekli", "rejected": "Reddedildi", "": ""}
    buf = io.StringIO()
    wr = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    wr.writerow(DORA_COLUMNS)
    for r in data:
        intake = json.loads(r["intake"]) if r["intake"] else {}
        la = (r["last_audit"] or "|").split("|")
        cell = lambda v: ("'" + str(v)) if str(v)[:1] in "=+-@" else ("" if v is None else v)  # noqa: E731
        wr.writerow([cell(x) for x in (
            r["name"], r["lei"], r["tax_no"], r["country"], r["category"], "Evet" if r["level"] == 1 or r["criticality"] == "yüksek" else "Hayır",
            r["tier"], r["title"], r["contract_no"], r["start_date"], r["end_date"], r["notice_days"], "Evet" if r["auto_renew"] else ("Hayır" if r["title"] else ""),
            r["value"], r["currency"], "Evet" if r["has_dpa"] else ("Hayır" if r["title"] else ""), "Evet" if r["has_security_clause"] else ("Hayır" if r["title"] else ""),
            subst.get(intake.get("substitute"), ""), {"yurtici": "Yurt içi", "yurtdisi": "Yurt dışı"}.get(intake.get("location"), r["country"] or ""),
            la[0], dec.get(la[1], la[1]))])
    return ("﻿" + buf.getvalue()).encode("utf-8")
