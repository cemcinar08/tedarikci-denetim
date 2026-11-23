"""Örnek veri yükler:  python -m app.seed"""
import datetime as dt
import json

from . import checklist as cl
from .db import get_db, init_db

D = lambda n: (dt.date.today() + dt.timedelta(days=n)).isoformat()

SUPPLIERS = [
    ("Anadolu Metal San. A.Ş.", "Hammadde", "Türkiye", "yüksek", ["example.com"]),
    ("Ege Lojistik Ltd.", "Lojistik", "Türkiye", "orta", ["example.org"]),
    ("Marmara Ambalaj", "Ambalaj", "Türkiye", "düşük", []),
    ("Bulut Yazılım Hizmetleri", "Yazılım / SaaS", "Türkiye", "yüksek", ["example.net"]),
]


def main():
    init_db()
    with get_db() as db:
        if db.execute("SELECT COUNT(*) FROM suppliers").fetchone()[0]:
            print("Veritabanında zaten kayıt var; örnek veri yüklenmedi.")
            return
        qids = sorted(cl.all_question_ids())
        for i, (name, cat, country, crit, domains) in enumerate(SUPPLIERS):
            sid = db.execute(
                "INSERT INTO suppliers (name, category, country, criticality, domains, contact_name, email) VALUES (?,?,?,?,?,?,?)",
                (name, cat, country, crit, json.dumps(domains), "Satın Alma Yetkilisi", f"info@{domains[0] if domains else 'ornek.com.tr'}"),
            ).lastrowid
            if i == 2:
                db.execute("INSERT INTO audits (supplier_id, audit_date, audit_type, status) VALUES (?,?,?,?)",
                           (sid, D(14), "yerinde", "planlandı"))
                continue
            aid = db.execute(
                "INSERT INTO audits (supplier_id, audit_date, auditor, audit_type, status) VALUES (?,?,?,?,?)",
                (sid, D(-40 - 30 * i), "Kalite Ekibi", "yerinde", "tamamlandı")).lastrowid
            answers = {}
            for j, q in enumerate(qids):
                a = ["uygun", "uygun", "uygun", "kısmen", "uygun değil"][(j + i * 2) % 5] if i != 1 else ["uygun", "uygun", "kısmen"][j % 3]
                answers[q] = a
                db.execute("INSERT INTO audit_answers (audit_id, question_id, answer) VALUES (?,?,?)", (aid, q, a))
            db.execute("UPDATE audits SET score=? WHERE id=?", (cl.compute_score(answers)["score"], aid))
            for q, a in list(answers.items())[:6]:
                if a == "uygun değil":
                    db.execute(
                        "INSERT INTO findings (supplier_id, audit_id, source, title, severity, owner, due_date, action) VALUES (?,?,?,?,?,?,?,?)",
                        (sid, aid, "denetim", cl.question_text(q), "yüksek", "Tedarikçi Kalite Müh.", D(-5 if i == 0 else 20),
                         "Tedarikçiden kök neden analizi ve aksiyon planı talep edilecek."))
    print("Örnek veri yüklendi.")


if __name__ == "__main__":
    main()
