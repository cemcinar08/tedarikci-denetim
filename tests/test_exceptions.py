"""Grup C: itiraz, risk kabulü, CAPA mesajlaşması."""
import datetime as dt

import pytest

from app import capa, exceptions
from conftest import PW

T = dt.date(2026, 6, 1)


def test_validate_request():
    exceptions.validate_request("itiraz", "açık", None, True, T)
    exceptions.validate_request("risk kabulü", "devam ediyor", "2026-12-01", False, T)
    for args in [("itiraz", "kapandı", None, False), ("risk kabulü", "açık", None, False), ("risk kabulü", "açık", "2026-05-01", False),
                 ("risk kabulü", "açık", "2027-07-01", False), ("risk kabulü", "açık", "2026-12-01", True)]:
        with pytest.raises(ValueError):
            exceptions.validate_request(*args, T)
    assert exceptions.outcome("itiraz", "approve") == "kapandı" and exceptions.outcome("risk kabulü", "approve") == "risk kabul edildi"
    assert exceptions.outcome("itiraz", "reject") is None
    assert capa.next_status("açık", "cancel") == "iptal edildi"
    with pytest.raises(ValueError):
        capa.next_status("onaylandı", "cancel")


@pytest.fixture
def w(admin, login):
    s = admin.post("/api/suppliers", json={"name": "S", "notify_emails": "s@s.com"}).json()
    o = admin.post("/api/suppliers", json={"name": "Öteki"}).json()
    for e, r, sid in [("den@x.com", "auditor", None), ("den2@x.com", "auditor", None), ("t@x.com", "supplier", s["id"]),
                      ("o@x.com", "supplier", o["id"]), ("v@x.com", "viewer", None)]:
        admin.post("/api/users", json={"name": e.split("@")[0], "email": e, "role": r, "supplier_id": sid, "password": PW})
    f = admin.post("/api/findings", json={"supplier_id": s["id"], "title": "TLS 1.0 açık", "severity": "yüksek"}).json()
    c = admin.post("/api/capa", json={"finding_id": f["id"]}).json()
    return {"s": s, "f": f, "c": c, "den": login("den@x.com"), "den2": login("den2@x.com"), "t": login("t@x.com"),
            "o": login("o@x.com"), "v": login("v@x.com"), "admin": admin}


def test_dispute_flow(w):
    fid = w["f"]["id"]
    assert w["v"].post(f"/api/findings/{fid}/exceptions", json={"type": "itiraz", "reason": "x" * 12}).status_code == 403
    assert w["o"].post(f"/api/findings/{fid}/exceptions", json={"type": "itiraz", "reason": "x" * 12}).status_code == 404
    assert w["t"].post(f"/api/findings/{fid}/exceptions", json={"type": "risk kabulü", "reason": "x" * 12,
                                                              "expires_at": "2099-01-01"}).status_code == 403
    assert w["t"].post(f"/api/findings/{fid}/exceptions", json={"type": "itiraz", "reason": "kısa"}).status_code == 422
    e = w["t"].post(f"/api/findings/{fid}/exceptions", json={"type": "itiraz", "reason": "Bu IP adresi bize ait değil, eski barındırma."}).json()
    assert e["status"] == "beklemede" and e["requested_by_role"] == "supplier"
    assert w["t"].post(f"/api/findings/{fid}/exceptions", json={"type": "itiraz", "reason": "ikinci talep metni"}).status_code == 409
    assert w["t"].post(f"/api/exceptions/{e['id']}/decide", json={"decision": "approve"}).status_code == 403
    assert w["den"].post(f"/api/exceptions/{e['id']}/decide", json={"decision": "reject"}).status_code == 422
    r = w["den"].post(f"/api/exceptions/{e['id']}/decide", json={"decision": "approve", "comment": "WHOIS doğrulandı"}).json()
    assert r["status"] == "onaylandı" and r["decided_by_name"] == "den"
    f = next(x for x in w["admin"].get("/api/findings").json() if x["id"] == fid)
    assert f["status"] == "kapandı" and "yanlış pozitif" in f["description"]
    c = w["admin"].get(f"/api/capa/{w['c']['id']}").json()
    assert c["status"] == "iptal edildi" and c["events"][-1]["action"] == "iptal edildi"
    assert w["den"].post(f"/api/exceptions/{e['id']}/decide", json={"decision": "reject", "comment": "x"}).status_code == 409
    assert [x["id"] for x in w["o"].get("/api/exceptions").json()] == []


def test_waiver_flow_and_expiry(w):
    fid = w["f"]["id"]
    exp = (dt.date.today() + dt.timedelta(days=90)).isoformat()
    e = w["den"].post(f"/api/findings/{fid}/exceptions", json={"type": "risk kabulü", "reason": "Sistem 3 ay içinde kapatılacak.",
                                                                "compensating_controls": "WAF kuralı", "expires_at": exp}).json()
    assert w["den2"].post(f"/api/exceptions/{e['id']}/decide", json={"decision": "approve"}).status_code == 403  # yalnız yönetici
    r = w["admin"].post(f"/api/exceptions/{e['id']}/decide", json={"decision": "approve", "comment": "Kabul"}).json()
    assert r["status"] == "onaylandı"
    s = w["admin"].get(f"/api/suppliers/{w['s']['id']}").json()
    assert s["summary"]["open_findings"] == 0 and s["findings"][0]["status"] == "risk kabul edildi"
    assert w["admin"].get("/api/findings?status=açık-tümü").json() == []
    # süresi dolunca bulgu yeniden açılır
    from app.db import get_db
    with get_db() as db:
        n = exceptions.expire_waivers(db, dt.date.today() + dt.timedelta(days=91))
    assert n == 1
    f = next(x for x in w["admin"].get("/api/findings").json() if x["id"] == fid)
    assert f["status"] == "açık"
    assert w["admin"].get(f"/api/findings/{fid}/exceptions").json()[0]["status"] == "süresi doldu"


def test_self_approval_blocked(w):
    fid = w["f"]["id"]
    exp = (dt.date.today() + dt.timedelta(days=30)).isoformat()
    e = w["admin"].post(f"/api/findings/{fid}/exceptions", json={"type": "risk kabulü", "reason": "Yönetici kendi talebi", "expires_at": exp}).json()
    assert w["admin"].post(f"/api/exceptions/{e['id']}/decide", json={"decision": "approve"}).status_code == 403


def test_capa_comments(w, monkeypatch):
    from app import notify
    sent = []
    monkeypatch.setattr(notify, "send_email", lambda s, to, subj, text, html: sent.append((to, subj)))
    w["admin"].put("/api/settings", json={"smtp_host": "smtp.x", "smtp_from": "a@b.com"})
    cid = w["c"]["id"]
    assert w["v"].post(f"/api/capa/{cid}/comments", json={"body": "merhaba"}).status_code == 403
    assert w["o"].get(f"/api/capa/{cid}/comments").status_code == 404
    w["den"].post(f"/api/capa/{cid}/comments", json={"body": "Kök neden analizini ekler misiniz?"})
    assert sent and sent[0][0] == ["s@s.com"]
    w["t"].post(f"/api/capa/{cid}/comments", json={"body": "Yarın ekliyoruz."})
    assert len(sent) == 1                                   # tedarikçi mesajı tedarikçiye e-posta göndermez
    msgs = w["t"].get(f"/api/capa/{cid}/comments").json()
    assert [(m["side"], m["author"]) for m in msgs] == [("kurum", "den"), ("tedarikçi", "t")]
    assert w["t"].post(f"/api/capa/{cid}/comments", json={"body": ""}).status_code == 422
