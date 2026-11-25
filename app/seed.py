"""Örnek veri yükler:  python -m app.seed"""
import datetime as dt

from .db import get_db, init_db
from . import main as m

D = lambda n: (dt.date.today() + dt.timedelta(days=n)).isoformat()

# ad, kategori, ülke, kritiklik, domain'ler, kullanılacak soru setleri (key)
SUPPLIERS = [
    ("Anadolu Metal San. A.Ş.", "Hammadde", "Türkiye", "yüksek", ["example.com"], ["genel", "iso-9001-2015"]),
    ("Ege Lojistik Ltd.", "Lojistik", "Türkiye", "orta", ["example.org"], ["genel"]),
    ("Marmara Ambalaj", "Ambalaj", "Türkiye", "düşük", [], ["iso-9001-2015", "iso-14001-2015"]),
    ("Bulut Ödeme Hizmetleri", "Ödeme / SaaS", "Türkiye", "yüksek", ["example.net"], ["iso-27001-2022", "pci-dss-4", "kvkk"]),
]
PATTERNS = [["uygun"] * 7 + ["kısmen", "uygun değil"], ["uygun"] * 5 + ["kısmen"],
            None, ["uygun"] * 12 + ["kısmen", "uygun değil"]]


def main():
    init_db()
    with get_db() as db:
        if db.execute("SELECT COUNT(*) FROM suppliers").fetchone()[0]:
            print("Veritabanında zaten kayıt var; örnek veri yüklenmedi.")
            return
        set_ids = {r["key"]: r["id"] for r in db.execute("SELECT id, key FROM audit_templates WHERE key IS NOT NULL")}

    for i, (name, cat, country, crit, domains, keys) in enumerate(SUPPLIERS):
        s = m.create_supplier(m.SupplierIn(name=name, category=cat, country=country, criticality=crit, domains=domains,
                                           contact_name="Satın Alma Yetkilisi",
                                           email=f"info@{domains[0] if domains else 'ornek.com.tr'}"))
        ids = [set_ids[k] for k in keys]
        if PATTERNS[i] is None:  # planlanmış, henüz yapılmamış denetim
            m.create_audit(s["id"], m.AuditIn(question_set_ids=ids, audit_date=D(14), audit_type="yerinde"))
            continue
        a = m.create_audit(s["id"], m.AuditIn(question_set_ids=ids, audit_date=D(-40 - 30 * i), auditor="Kalite Ekibi"))
        qids = [q["id"] for sec in a["checklist"] for q in sec["questions"]]
        answers = {q: m.AnswerIn(answer=PATTERNS[i][(j + i) % len(PATTERNS[i])]) for j, q in enumerate(qids)}
        m.update_audit(a["id"], m.AuditUpdate(status="tamamlandı", answers=answers))  # bulgu + CAPA otomatik açılır
        with get_db() as db:  # bazı bulgulara sorumlu/termin ver; birini gecikmiş yap
            db.execute("UPDATE findings SET owner='Tedarikçi Kalite Müh.', "
                       "action='Tedarikçiden kök neden analizi ve aksiyon planı talep edilecek.' WHERE audit_id=?", (a["id"],))
            if i == 0:
                db.execute("UPDATE findings SET due_date=? WHERE id=(SELECT MIN(id) FROM findings WHERE audit_id=?)",
                           (D(-5), a["id"]))
    print("Örnek veri yüklendi.")


if __name__ == "__main__":
    main()
