"""Grup B: kabul (doğal risk), çıkarma kontrol listesi, sözleşmeler."""
import datetime as dt

import pytest

from app import lifecycle
from conftest import PW

LOW = {"data": "yok", "access": "yok", "criticality": "dusuk", "substitute": "kolay", "spend": "dusuk", "location": "yurtici"}


def test_inherent_risk():
    r = lifecycle.inherent_risk(LOW)
    assert r["score"] == 0 and r["tier_level"] == 3 and r["criticality"] == "düşük" and r["templates"] == ["genel"]
    mid = lifecycle.inherent_risk({**LOW, "data": "kisisel", "criticality": "orta"})
    assert mid["tier_level"] == 2 and "kvkk" in mid["templates"] and "iso-27001-2022" in mid["templates"]
    hi = lifecycle.inherent_risk({**LOW, "access": "yetkili"}, ["pci"])
    assert hi["tier_level"] == 1 and "pci-dss-4" in hi["templates"]       # ayrıcalıklı erişim tek başına Tier 1
    full = lifecycle.inherent_risk({"data": "hassas", "access": "yetkili", "criticality": "yuksek", "substitute": "zor",
                                    "spend": "yuksek", "location": "yurtdisi"}, ["dora", "bilinmeyen"])
    assert full["score"] == 100 and full["regulations"] == ["dora", "kvkk"]
    with pytest.raises(ValueError):
        lifecycle.inherent_risk({"data": "yok"})


def test_contract_status():
    t = dt.date(2026, 6, 1)
    st = lambda **c: lifecycle.contract_status(c, t)["status"]
    assert st(end_date=None) == "süresiz"
    assert st(end_date="2026-05-31") == "süresi doldu"
    assert st(end_date="2026-06-20") == "bitiyor"
    assert st(end_date="2026-06-20", auto_renew=1) == "otomatik yenilenecek"
    assert st(end_date="2026-12-31") == "aktif"
    assert st(end_date="2026-09-15", notice_days=90) == "bitiyor"  # ihbar tarihi 17.06 -> 30 gün içinde
    assert st(start_date="2026-07-01", end_date="2027-07-01") == "başlamadı"
    assert lifecycle.contract_status({"end_date": "2026-09-15", "notice_days": 90}, t)["notice_deadline"] == "2026-06-17"


def test_onboarding_flow(admin):
    q = admin.get("/api/onboarding/questions").json()
    assert len(q["questions"]) == 6 and "kvkk" in q["regulations"]
    ans = {**LOW, "data": "kisisel"}
    p = admin.post("/api/onboarding/preview", json={"answers": ans}).json()
    assert p["tier"]["name"].startswith("Tier 2") and len(p["template_ids"]) >= 2
    assert admin.post("/api/onboarding/preview", json={"answers": {"data": "yok"}}).status_code == 422
    r = admin.post("/api/onboarding", json={"name": "Aday Bulut", "answers": ans, "domains": ["https://aday.com"]})
    assert r.status_code == 201
    sid = r.json()["supplier_id"]
    s = admin.get(f"/api/suppliers/{sid}").json()
    assert s["status"] == "aday" and s["tier_level"] == 2 and s["criticality"] == "orta" and s["domains"] == ["aday.com"]
    assert admin.post("/api/onboarding", json={"name": "aday bulut", "answers": ans}).status_code == 409
    assert admin.get(f"/api/suppliers/{sid}/intake").json()["score"] == p["score"]
    assert admin.post(f"/api/suppliers/{sid}/activate").json()["ok"]
    assert admin.get(f"/api/suppliers/{sid}").json()["status"] == "aktif"
    assert admin.post(f"/api/suppliers/{sid}/activate").status_code == 409
    other = admin.post("/api/suppliers", json={"name": "Formsuz", "status": "aday"}).json()
    assert admin.post(f"/api/suppliers/{other['id']}/activate").status_code == 422


def test_offboarding_flow(admin, login):
    s = admin.post("/api/suppliers", json={"name": "Ayrılan"}).json()
    admin.post("/api/users", json={"name": "t", "email": "t@x.com", "role": "supplier", "supplier_id": s["id"], "password": PW})
    t = login("t@x.com")
    admin.post("/api/webhooks", json={"name": "w", "url": "https://w.example", "supplier_id": s["id"]})
    assert admin.post(f"/api/suppliers/{s['id']}/offboarding", json={"reason": "x"}).status_code == 422
    o = admin.post(f"/api/suppliers/{s['id']}/offboarding", json={"reason": "Sözleşme bitti"}).json()
    assert o["progress"] == 0 and len(o["items"]) == len(lifecycle.OFFBOARDING_ITEMS)
    assert admin.get(f"/api/suppliers/{s['id']}").json()["status"] == "çıkış sürecinde"
    assert admin.post(f"/api/suppliers/{s['id']}/offboarding", json={"reason": "tekrar"}).status_code == 409
    assert admin.post(f"/api/suppliers/{s['id']}/offboarding/complete").status_code == 422
    for it in o["items"]:
        r = admin.put(f"/api/suppliers/{s['id']}/offboarding/items/{it['key']}", json={"done": True, "note": "ok"}).json()
    assert r["progress"] == len(o["items"]) and r["items"][0]["done_by"] == "Admin"
    assert admin.put(f"/api/suppliers/{s['id']}/offboarding/items/yok", json={"done": True}).status_code == 404
    c = admin.post(f"/api/suppliers/{s['id']}/offboarding/complete").json()
    assert c["completed_at"]
    assert admin.get(f"/api/suppliers/{s['id']}").json()["status"] == "pasif"
    assert t.get("/api/me").status_code == 401                               # tedarikçi hesabı kapatıldı
    assert not admin.get("/api/webhooks").json()["items"][0]["active"]


def test_contracts(admin, login):
    a = admin.post("/api/suppliers", json={"name": "A"}).json()
    b = admin.post("/api/suppliers", json={"name": "B"}).json()
    end = (dt.date.today() + dt.timedelta(days=10)).isoformat()
    assert admin.post(f"/api/suppliers/{a['id']}/contracts", json={"title": "x", "start_date": "2026-02-01", "end_date": "2026-01-01"}).status_code == 422
    assert admin.post(f"/api/suppliers/{a['id']}/contracts", json={"title": "x", "end_date": "01.01.2027"}).status_code == 422
    c = admin.post(f"/api/suppliers/{a['id']}/contracts", json={"title": "Hizmet sözleşmesi", "end_date": end, "has_dpa": True,
                                                               "value": 120000, "currency": "TRY"}).json()
    assert c["status"] == "bitiyor" and c["has_dpa"] is True and c["days_left"] == 10
    admin.post(f"/api/suppliers/{b['id']}/contracts", json={"title": "Uzun", "end_date": "2099-01-01"})
    assert [x["title"] for x in admin.get("/api/contracts?status=bitiyor").json()] == ["Hizmet sözleşmesi"]
    s = admin.get("/api/summary").json()
    assert [x["title"] for x in s["contracts"]] == ["Hizmet sözleşmesi"]
    u = admin.put(f"/api/contracts/{c['id']}", json={"title": "Hizmet sözleşmesi", "end_date": "2099-01-01", "auto_renew": True}).json()
    assert u["status"] == "aktif" and u["auto_renew"] is True
    # sözleşme dosyası; tedarikçi göremez
    f = admin.post("/api/evidence", data={"contract_id": c["id"]}, files={"file": ("s.pdf", b"%PDF")})
    assert f.status_code == 201 and f.json()["kind"] == "sözleşme"
    admin.post("/api/users", json={"name": "s", "email": "s@x.com", "role": "supplier", "supplier_id": a["id"], "password": PW})
    sa = login("s@x.com")
    assert sa.get(f"/api/suppliers/{a['id']}/contracts").status_code == 403
    assert sa.get(f"/api/evidence/{f.json()['id']}/download").status_code == 404
    assert sa.get(f"/api/evidence?supplier_id={a['id']}").json() == []
    assert "contracts" not in sa.get("/api/summary").json()
    admin.post("/api/users", json={"name": "v", "email": "v@x.com", "role": "viewer", "password": PW})
    v = login("v@x.com")
    assert len(v.get("/api/contracts").json()) == 2 and v.post(f"/api/suppliers/{a['id']}/contracts", json={"title": "x"}).status_code == 403
    assert admin.delete(f"/api/contracts/{c['id']}").status_code == 204
