"""Grup F: ek çerçeveler, portföy raporu, DORA bilgi kaydı."""
import datetime as dt

from app.reports import portfolio
from conftest import PW


def test_month_keys_and_trend():
    assert portfolio.month_keys(dt.date(2026, 2, 15), 3) == ["2025-12", "2026-01", "2026-02"]
    tr = portfolio.monthly_trend([{"completed_at": "2026-02-03 10:00", "score": 80}, {"audit_date": "2026-02-10", "score": 60},
                                  {"audit_date": "2024-01-01", "score": 10}],
                                 [{"created_at": "2026-01-05", "closed_at": "2026-02-01"}], dt.date(2026, 2, 15), 3)
    assert [m["audits"] for m in tr] == [0, 0, 2] and tr[2]["avg_score"] == 70 and tr[1]["opened"] == 1 and tr[2]["closed"] == 1
    assert tr[2]["label"] == "Şub 26"


def test_group_stats():
    sups = [{"tier_name": "T1", "summary": {"overall_score": 50, "risk": "yüksek"}},
            {"tier_name": "T1", "summary": {"overall_score": 70, "risk": "orta"}},
            {"tier_name": None, "summary": {"overall_score": None, "risk": "bilinmiyor"}}]
    g = portfolio.group_stats(sups, "tier_name")
    assert g[0] == {"group": "T1", "count": 2, "high": 1, "avg": 60.0} and g[1]["group"] == "Atanmamış" and g[1]["avg"] is None


def test_new_frameworks(admin):
    sets = {x["standard"]: x for x in admin.get("/api/question-sets").json()}
    for k in ("NIST CSF", "SOC 2", "DORA", "NIS2"):
        assert sets[k]["builtin"] and sets[k]["question_count"] >= 10 and sets[k]["critical_count"] >= 1
    assert sets["SOC 2"]["conditional_count"] == 1


def test_portfolio_and_dora(admin, login):
    tiers = {t["level"]: t["id"] for t in admin.get("/api/supplier-tiers").json()}
    s = admin.post("/api/suppliers", json={"name": "Bulut BT", "category": "BT hizmeti", "tier_id": tiers[1], "lei": "5299001ABCDEF1234567",
                                          "country": "İrlanda"}).json()
    admin.post("/api/suppliers", json={"name": "=Kötü", "category": "Lojistik"})
    admin.post(f"/api/suppliers/{s['id']}/contracts", json={"title": "Bulut barındırma", "start_date": "2025-01-01", "end_date": "2027-12-31",
                                                           "notice_days": 90, "has_dpa": True, "value": 250000, "currency": "EUR"})
    j = admin.get("/api/reports/portfolio.json").json()
    assert j["totals"]["suppliers"] == 2 and len(j["trend"]) == 12 and j["by_tier"][0]["count"] >= 1
    pdf = admin.get("/api/reports/portfolio.pdf")
    assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-"
    c = admin.get("/api/reports/dora-register.csv").content.decode("utf-8-sig").splitlines()
    assert c[0].startswith("Sağlayıcı adı;LEI")
    row = next(x for x in c if x.startswith("Bulut BT"))
    assert "5299001ABCDEF1234567" in row and "Evet" in row and "Bulut barındırma" in row and "EUR" in row
    assert any(x.startswith("'=Kötü") for x in c)                      # formül enjeksiyonu etkisiz
    admin.post("/api/users", json={"name": "t", "email": "t@x.com", "role": "supplier", "supplier_id": s["id"], "password": PW})
    t = login("t@x.com")
    assert t.get("/api/reports/portfolio.pdf").status_code == 403 and t.get("/api/reports/dora-register.csv").status_code == 403
