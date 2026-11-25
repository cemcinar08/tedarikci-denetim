"""Grup D: tedarikçi öz-değerlendirmesi."""
import datetime as dt

from app import assessments
from conftest import PW

SECS = [{"id": "s", "title": "S", "questions": [{"id": "a", "text": "A"}, {"id": "b", "text": "B", "condition": {"q": "a", "op": "eq", "value": "uygun değil"}},
                                                 {"id": "c", "text": "C"}]}]


def test_pure_rules():
    assert assessments.missing_answers(SECS, {}) == ["A", "C"]
    assert assessments.missing_answers(SECS, {"a": "uygun değil", "c": "na"}) == ["B"]       # açılan alt soru zorunlu
    assert assessments.missing_answers(SECS, {"a": "uygun", "c": "kısmen"}) == []
    a = {"respondent": "tedarikçi", "supplier_id": 1, "status": "gönderildi"}
    assert assessments.respond_allowed(a, {"role": "supplier", "supplier_id": 1}) is None
    assert assessments.respond_allowed(a, {"role": "supplier", "supplier_id": 2}) == "Kayıt bulunamadı"
    assert assessments.respond_allowed({**a, "status": "incelemede"}, {"role": "supplier", "supplier_id": 1})
    assert assessments.respond_allowed({**a, "respondent": "denetçi"}, {"role": "admin"})


def test_self_assessment_flow(admin, login, monkeypatch):
    from app import notify
    mails = []
    monkeypatch.setattr(notify, "send_email", lambda s, to, subj, text, html: mails.append(subj))
    admin.put("/api/settings", json={"smtp_host": "smtp.x", "smtp_from": "a@b.com", "org_name": "ABC"})
    s = admin.post("/api/suppliers", json={"name": "S", "notify_emails": "s@s.com"}).json()
    o = admin.post("/api/suppliers", json={"name": "O"}).json()
    for e, r, sid in [("t@x.com", "supplier", s["id"]), ("o@x.com", "supplier", o["id"]), ("d@x.com", "auditor", None)]:
        admin.post("/api/users", json={"name": e[0], "email": e, "role": r, "supplier_id": sid, "password": PW})
    t, ot, d = login("t@x.com"), login("o@x.com"), login("d@x.com")
    tpl = admin.post("/api/question-sets", json={"name": "Öz", "sections": [{"title": "Güvenlik", "questions": [
        {"code": "iso", "text": "ISO 27001 var mı?"}, {"code": "soc", "text": "Yoksa SOC 2?", "condition": {"q": "iso", "op": "eq", "value": "uygun değil"}},
        {"code": "mfa", "text": "MFA?", "critical": True}]}]}).json()
    due = (dt.date.today() + dt.timedelta(days=14)).isoformat()
    assert d.post(f"/api/suppliers/{s['id']}/self-assessments", json={"question_set_ids": [tpl["id"]], "due_date": "2020-01-01"}).status_code == 422
    assert t.post(f"/api/suppliers/{s['id']}/self-assessments", json={"question_set_ids": [tpl["id"]], "due_date": due}).status_code == 403
    a = d.post(f"/api/suppliers/{s['id']}/self-assessments", json={"question_set_ids": [tpl["id"]], "due_date": due, "message": "Teşekkürler"}).json()
    assert a["status"] == "gönderildi" and a["respondent"] == "tedarikçi" and a["due_date"] == due and a["audit_type"] == "öz-değerlendirme"
    assert any("Öz-değerlendirme" in m for m in mails)
    q = {x["text"][:3]: x["id"] for x in a["checklist"][0]["questions"]}
    # başka tedarikçi ve denetçi cevap yazamaz; tedarikçi PUT /api/audits ile değiştiremez
    assert ot.put(f"/api/audits/{a['id']}/respond", json={"answers": {q["ISO"]: {"answer": "uygun"}}}).status_code == 404
    assert t.put(f"/api/audits/{a['id']}", json={"status": "tamamlandı"}).status_code == 403
    r = t.put(f"/api/audits/{a['id']}/respond", json={"answers": {q["ISO"]: {"answer": "uygun değil", "note": "Süreç devam ediyor"}}}).json()
    assert r["status"] == "yanıtlanıyor" and q["Yok"] not in r["result"]["hidden_question_ids"]
    assert t.put(f"/api/audits/{a['id']}/respond", json={"answers": {"yok": {"answer": "uygun"}}}).status_code == 422
    assert t.post(f"/api/audits/{a['id']}/submit").status_code == 422          # eksik: SOC 2 + MFA
    t.put(f"/api/audits/{a['id']}/respond", json={"answers": {q["Yok"]: {"answer": "uygun"}, q["MFA"]: {"answer": "uygun"}}})
    up = t.post("/api/evidence", data={"audit_id": a["id"], "question_id": q["Yok"]}, files={"file": ("soc2.pdf", b"%PDF")})
    assert up.status_code == 201
    r = t.post(f"/api/audits/{a['id']}/submit").json()
    assert r["status"] == "incelemede" and r["submitted_by"] == "t"
    assert t.put(f"/api/audits/{a['id']}/respond", json={"answers": {q["MFA"]: {"answer": "kısmen"}}}).status_code == 409
    # denetçi revizyon ister -> tedarikçi düzeltir -> yeniden gönderir -> denetçi tamamlar
    assert d.post(f"/api/audits/{a['id']}/return", json={"comment": "x"}).status_code == 422
    r = d.post(f"/api/audits/{a['id']}/return", json={"comment": "SOC 2 raporunun tarihi eski, güncelini yükleyin."}).json()
    assert r["status"] == "revizyon istendi" and r["review_comment"].startswith("SOC 2")
    assert any("revizyon" in m for m in mails)
    t.put(f"/api/audits/{a['id']}/respond", json={"answers": {q["MFA"]: {"answer": "uygun değil"}}})
    t.post(f"/api/audits/{a['id']}/submit")
    done = d.put(f"/api/audits/{a['id']}", json={"status": "tamamlandı"}).json()
    assert done["decision"] == "rejected" and done["capa_created"] >= 1               # kritik MFA -> kill-switch + CAPA
    assert d.post(f"/api/audits/{a['id']}/return", json={"comment": "geç kaldı"}).status_code == 409
