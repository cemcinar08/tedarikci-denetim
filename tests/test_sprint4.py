"""Sprint 4: özet, rapor (PDF/CSV) ve olay webhook servisi."""
import datetime as dt
import hashlib
import hmac
import json

import pytest

from app import events, summary
from app.reports import csv_export
from conftest import PW

T = dt.date(2026, 6, 1)


# ------------------------------------------------------------------ özet (saf)
def test_summarize():
    sups = [{"risk_level": "high", "decision": "rejected"}, {"risk_level": "low"}, {"risk_level": None}, {"risk_level": "high"}]
    caps = [{"status": "açık", "severity": "kritik", "due_date": "2026-05-01"},
            {"status": "onay bekliyor", "severity": "orta", "due_date": "2026-07-01"},
            {"status": "onaylandı", "severity": "yüksek", "due_date": "2026-01-01"}]
    certs = [{"id": 1, "expires_at": "2026-05-20"}, {"id": 2, "expires_at": "2026-07-15"}, {"id": 3, "expires_at": "2027-01-01"},
             {"id": 4, "expires_at": None}]
    r = summary.summarize(sups, caps, certs, T, days=60)
    assert r["risk_distribution"] == {"high": 2, "medium": 0, "low": 1, "none": 1} and r["rejected_suppliers"] == 1
    assert r["capa"]["open"] == 2 and r["capa"]["overdue"] == 1 and r["capa"]["by_status"]["onay bekliyor"] == 1
    assert [c["id"] for c in r["certificates"]["items"]] == [1, 2]
    assert r["certificates"]["expired"] == 1 and r["certificates"]["expiring"] == 1
    assert r["certificates"]["items"][0]["days_left"] == -12 and r["certificates"]["items"][1]["status"] == "yaklaşıyor"


# ------------------------------------------------------------------ webhook eşleştirme (saf)
@pytest.mark.parametrize("subs,sup,etype,esup,ok", [
    (["*"], None, "capa.created", 5, True),
    (["capa.*"], None, "capa.approved", 5, True),
    (["capa.*"], None, "finding.created", 5, False),
    (["finding.created"], None, "finding.created", None, True),
    (["*"], 3, "capa.created", 5, False),
    (["*"], 5, "capa.created", 5, True),
])
def test_matches(subs, sup, etype, esup, ok):
    assert events.matches(subs, sup, etype, esup) is ok


# ------------------------------------------------------------------ API
@pytest.fixture
def setup(admin, login):
    a = admin.post("/api/suppliers", json={"name": "Ölçüm & Şirket A.Ş."}).json()
    b = admin.post("/api/suppliers", json={"name": "B"}).json()
    admin.post("/api/users", json={"name": "sa", "email": "sa@x.com", "role": "supplier", "supplier_id": a["id"], "password": PW})
    admin.post("/api/users", json={"name": "sb", "email": "sb@x.com", "role": "supplier", "supplier_id": b["id"], "password": PW})
    t = admin.post("/api/question-sets", json={"name": "R", "sections": [{"title": "Güvenlik", "questions": [
        {"code": "k", "text": "=HYPERLINK(\"x\") kritik mi?", "critical": True}, {"code": "y", "text": "Yedekleme", "weight": 2}]}]}).json()
    aud = admin.post(f"/api/suppliers/{a['id']}/audits", json={"audit_date": "2026-03-01", "auditor": "Deniz",
                                                                "question_set_ids": [t["id"]]}).json()
    q = [x["id"] for x in aud["checklist"][0]["questions"]]
    return {"a": a, "b": b, "aud": aud, "q": q, "sa": login("sa@x.com"), "sb": login("sb@x.com")}


def test_summary_endpoint_scoped(admin, setup):
    admin.put(f"/api/audits/{setup['aud']['id']}", json={"status": "tamamlandı", "answers": {setup["q"][0]: {"answer": "uygun değil"}}})
    exp = (dt.date.today() + dt.timedelta(days=10)).isoformat()
    for sid in (setup["a"]["id"], setup["b"]["id"]):
        admin.post("/api/evidence", data={"kind": "sertifika", "supplier_id": sid, "expires_at": exp}, files={"file": ("c.pdf", b"%PDF")})
    s = admin.get("/api/summary").json()
    assert s["risk_distribution"]["high"] == 1 and s["risk_distribution"]["none"] == 1 and s["rejected_suppliers"] == 1
    assert s["capa"]["open"] == 2 and len(s["certificates"]["items"]) == 2  # kritik + düşük puan
    sb = setup["sb"].get("/api/summary").json()
    assert sb["capa"]["open"] == 0 and sb["risk_distribution"]["none"] == 1 and len(sb["certificates"]["items"]) == 1


def test_reports(admin, setup):
    aid = setup["aud"]["id"]
    admin.put(f"/api/audits/{aid}", json={"status": "tamamlandı", "answers": {
        setup["q"][0]: {"answer": "uygun değil", "note": "Politika yok"}, setup["q"][1]: {"answer": "uygun"}}})
    pdf = admin.get(f"/api/reports/audits/{aid}.pdf")
    assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-" and len(pdf.content) > 5000
    assert pdf.headers["content-type"] == "application/pdf" and "olcum-sirket" in pdf.headers["content-disposition"]
    c = admin.get(f"/api/reports/audits/{aid}.csv")
    assert c.status_code == 200 and c.content.startswith("﻿".encode())
    text = c.content.decode("utf-8-sig")
    lines = text.splitlines()
    assert lines[0].startswith("Tedarikçi;Denetim tarihi;Bölüm")
    assert "Ölçüm & Şirket A.Ş." in lines[1] and "Uygun değil" in lines[1] and "Politika yok" in lines[1]
    assert "'=HYPERLINK" in text                       # formül enjeksiyonu etkisiz
    assert "Karar;Onaysız" in text and "Kill-switch;1" in text
    # izolasyon: tedarikçi kendi raporunu alır, başkası alamaz
    assert setup["sa"].get(f"/api/reports/audits/{aid}.pdf").status_code == 200
    assert setup["sb"].get(f"/api/reports/audits/{aid}.csv").status_code == 404
    assert admin.get("/api/reports/audits/9999.pdf").status_code == 404
    assert csv_export._cell("+90 555") == "'+90 555" and csv_export._cell(2.0) == "2"


def test_event_webhooks(admin, setup, monkeypatch):
    sent = []

    async def fake_post(url, body, headers):
        sent.append((url, json.loads(body), headers, body))
        return 500 if "bozuk" in url else 200
    monkeypatch.setattr(events, "post", fake_post)
    a_id = setup["a"]["id"]
    assert admin.post("/api/webhooks", json={"name": "x", "url": "ftp://x"}).status_code == 422
    assert admin.post("/api/webhooks", json={"name": "x", "url": "https://x", "events": ["yok"]}).status_code == 422
    assert setup["sa"].get("/api/webhooks").status_code == 403
    siem = admin.post("/api/webhooks", json={"name": "SIEM", "url": "https://siem/hook", "secret": "s1", "events": ["*"]}).json()
    admin.post("/api/webhooks", json={"name": "Sadece B", "url": "https://b/hook", "events": ["*"], "supplier_id": setup["b"]["id"]})
    admin.post("/api/webhooks", json={"name": "Bozuk", "url": "https://bozuk/hook", "events": ["finding.created"]})

    # yeni bulgu -> finding.created (SIEM + Bozuk); durum değişimi -> finding.status_changed
    f = admin.post("/api/findings", json={"supplier_id": a_id, "title": "Yangın tüpü yok", "severity": "yüksek"}).json()
    got = [(u, b["event"]) for u, b, _, _ in sent]
    assert ("https://siem/hook", "finding.created") in got and ("https://bozuk/hook", "finding.created") in got
    assert not any("b/hook" in u for u, _ in got)                   # başka tedarikçinin aboneliği almaz
    url, body, headers, raw = next(x for x in sent if x[0] == "https://siem/hook")
    exp = "sha256=" + hmac.new(b"s1", headers["X-TD-Timestamp"].encode() + b"." + raw, hashlib.sha256).hexdigest()
    assert headers["X-TD-Signature"] == exp and headers["X-TD-Event"] == "finding.created"
    assert body["data"]["title"] == "Yangın tüpü yok" and body["supplier"]["name"] == "Ölçüm & Şirket A.Ş."
    sent.clear()
    admin.put(f"/api/findings/{f['id']}", json={"status": "devam ediyor"})
    assert [b["data"]["to"] for _, b, _, _ in sent if b["event"] == "finding.status_changed"] == ["devam ediyor"]
    sent.clear()
    admin.put(f"/api/findings/{f['id']}", json={"owner": "Ali"})     # durum değişmedi -> yeni olay yok
    assert [u for u, *_ in sent if "bozuk" not in u] == []          # (bozuk adrese yalnızca otomatik yeniden deneme gider)

    # denetim tamamlandı + CAPA olayları
    admin.put(f"/api/audits/{setup['aud']['id']}", json={"status": "tamamlandı", "answers": {setup["q"][0]: {"answer": "uygun değil"}}})
    types = [b["event"] for u, b, _, _ in sent if u == "https://siem/hook"]
    assert "audit.completed" in types and types.count("capa.created") == 2 and types.count("finding.created") == 2

    # başarısız teslimat: kayıtlı, otomatik en fazla MAX_ATTEMPTS deneme, sonra elle tekrar
    dels = admin.get("/api/webhooks/deliveries").json()
    bad = [d for d in dels if d["subscription_name"] == "Bozuk"]
    assert bad and all(d["status"] == "başarısız" and d["response_code"] == 500 for d in bad)
    for _ in range(5):
        admin.put(f"/api/findings/{f['id']}", json={"owner": "x"})   # her değişiklik isteği bekleyenleri yeniden dener
    d0 = next(d for d in admin.get("/api/webhooks/deliveries").json() if d["id"] == bad[-1]["id"])
    assert d0["attempts"] == events.MAX_ATTEMPTS
    r = admin.post(f"/api/webhooks/deliveries/{d0['id']}/retry").json()
    assert r["attempts"] == events.MAX_ATTEMPTS + 1
    assert admin.post(f"/api/webhooks/{siem['id']}/test").json()["status"] == "gönderildi"
