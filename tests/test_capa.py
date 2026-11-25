"""Sprint 3: kanıt/sertifika, otomatik CAPA ve tedarikçi-denetçi CAPA iş akışı."""
import datetime as dt

import pytest

from app import capa, evidence
from app import templates_engine as eng

CHECK = [{"id": "s", "title": "Bölüm", "weight": 10, "questions": [
    {"id": "a", "text": "A"}, {"id": "b", "text": "B", "critical": True},
    {"id": "c", "text": "C", "condition": {"q": "a", "op": "eq", "value": "uygun"}}, {"id": "d", "text": "D"}]}]


# ------------------------------------------------------------------ saf fonksiyonlar
def test_capa_candidates():
    ans = {"a": "kısmen", "b": "uygun değil", "c": "uygun değil", "d": "uygun"}
    res = eng.score_audit(CHECK, ans)
    cands = {c["question_id"]: c for c in capa.capa_candidates(CHECK, ans, res)}
    assert cands["a"]["severity"] == "orta" and cands["a"]["trigger"] == "uygunsuzluk"
    assert cands["b"]["severity"] == "kritik" and cands["b"]["trigger"] == "kill-switch"
    assert "c" not in cands                       # gizli soru (a 'uygun' değil)
    assert cands["__score__"]["trigger"] == "düşük puan"   # (0.5+0+1)/3 = 50 < 70
    ok = {"a": "uygun", "b": "uygun", "c": "uygun", "d": "uygun"}
    assert capa.capa_candidates(CHECK, ok, eng.score_audit(CHECK, ok)) == []


def test_state_machine():
    assert capa.next_status("açık", "submit") == "onay bekliyor"
    assert capa.next_status("onay bekliyor", "approve") == "onaylandı"
    assert capa.next_status("onay bekliyor", "reject") == "reddedildi"
    assert capa.next_status("reddedildi", "submit") == "onay bekliyor"
    for cur, act in [("açık", "approve"), ("onaylandı", "submit"), ("onaylandı", "reject"), ("reddedildi", "approve")]:
        with pytest.raises(ValueError):
            capa.next_status(cur, act)


def test_cert_status():
    t = dt.date(2026, 1, 1)
    assert evidence.cert_status("2025-12-31", t) == "süresi doldu"
    assert evidence.cert_status("2026-02-15", t) == "yaklaşıyor"
    assert evidence.cert_status("2026-06-01", t) == "geçerli"
    assert evidence.cert_status(None, t) is None


# ------------------------------------------------------------------ API akışı
def _complete(c, w, answers):
    return c.put(f"/api/audits/{w['audit']['id']}", json={"status": "tamamlandı",
                                                          "answers": {w["q"][k]: {"answer": v} for k, v in answers.items()}}).json()


def test_auto_capa_on_completion_idempotent(world):
    den = world["den"]
    r = _complete(den, world, {"MFA": "uygun değil", "Yed": "kısmen", "Log": "uygun"})
    assert r["decision"] == "rejected" and r["capa_created"] == 3   # kritik + kısmen + düşük puan
    caps = den.get(f"/api/capa?audit_id={world['audit']['id']}").json()
    assert {c["trigger"] for c in caps} == {"kill-switch", "uygunsuzluk", "düşük puan"}
    assert all(c["finding_id"] and c["status"] == "açık" for c in caps)
    # yeniden aç-kapat: tekrar oluşturmaz
    den.put(f"/api/audits/{world['audit']['id']}", json={"status": "devam ediyor"})
    assert _complete(den, world, {"MFA": "uygun değil", "Yed": "kısmen", "Log": "uygun"})["capa_created"] == 0
    assert len(den.get("/api/capa").json()) == 3


def test_capa_workflow_and_isolation(world):
    den, sa, sb = world["den"], world["sa"], world["sb"]
    _complete(den, world, {"MFA": "uygun değil", "Yed": "uygun", "Log": "uygun"})
    cap = next(c for c in sa.get("/api/capa").json() if c["trigger"] == "kill-switch")
    cid = cap["id"]
    # izolasyon
    assert sb.get("/api/capa").json() == []
    assert sb.get(f"/api/capa/{cid}").status_code == 404
    assert sb.post("/api/evidence", data={"capa_id": cid}, files={"file": ("x.pdf", b"%PDF")}).status_code == 404
    # tedarikçi onaylayamaz, denetçi gönderemez rolü yoksa
    assert sa.post(f"/api/capa/{cid}/review", json={"decision": "approve"}).status_code == 403
    sub = {"root_cause": "MFA politikası yoktu", "corrective_action": "Tüm yönetici hesaplarında MFA açıldı", "planned_date": "2026-02-01"}
    assert sa.post(f"/api/capa/{cid}/submit", json=sub).status_code == 422        # kritik: kanıt zorunlu
    up = sa.post("/api/evidence", data={"capa_id": cid, "title": "MFA ekran görüntüsü"}, files={"file": ("mfa.png", b"\x89PNG...")})
    assert up.status_code == 201, up.text
    r = sa.post(f"/api/capa/{cid}/submit", json=sub)
    assert r.status_code == 200 and r.json()["status"] == "onay bekliyor" and r.json()["round"] == 1
    assert sa.post(f"/api/capa/{cid}/submit", json=sub).status_code == 409       # zaten onayda
    # onaydaki CAPA'nın kanıtı tedarikçi tarafından silinemez
    assert sa.delete(f"/api/evidence/{up.json()['id']}").status_code == 409
    # red (gerekçe zorunlu) -> yeniden gönder -> onay
    assert den.post(f"/api/capa/{cid}/review", json={"decision": "reject"}).status_code == 422
    r = den.post(f"/api/capa/{cid}/review", json={"decision": "reject", "comment": "Servis hesaplarını da kapsayın"}).json()
    assert r["status"] == "reddedildi" and r["review_comment"]
    r = sa.post(f"/api/capa/{cid}/submit", json={**sub, "corrective_action": "Servis hesapları dahil MFA"}).json()
    assert r["round"] == 2 and r["status"] == "onay bekliyor"
    r = den.post(f"/api/capa/{cid}/review", json={"decision": "approve", "comment": "Doğrulandı"}).json()
    assert r["status"] == "onaylandı" and r["closed_at"]
    assert [e["action"] for e in r["events"]] == ["oluşturuldu", "kanıt eklendi", "gönderildi", "reddedildi", "gönderildi", "onaylandı"]
    f = den.get(f"/api/findings?supplier_id={world['a']['id']}").json()
    assert next(x for x in f if x["id"] == cap["finding_id"])["status"] == "kapandı"
    assert sa.post("/api/evidence", data={"capa_id": cid}, files={"file": ("y.pdf", b"%PDF")}).status_code == 409


def test_evidence_upload_rules(world, monkeypatch):
    den, sa, sb = world["den"], world["sa"], world["sb"]
    aid, q = world["audit"]["id"], world["q"]["MFA"]
    r = den.post("/api/evidence", data={"audit_id": aid, "question_id": q}, files={"file": ("politika.pdf", b"%PDF-1.4 icerik")})
    assert r.status_code == 201 and r.json()["supplier_id"] == world["a"]["id"] and r.json()["sha256"]
    eid = r.json()["id"]
    assert den.post("/api/evidence", data={"audit_id": aid, "question_id": "yok"}, files={"file": ("p.pdf", b"x")}).status_code == 422
    assert den.post("/api/evidence", data={"audit_id": aid}, files={"file": ("zararli.exe", b"MZ")}).status_code == 415
    assert den.post("/api/evidence", data={"audit_id": aid}, files={"file": ("bos.pdf", b"")}).status_code == 422
    monkeypatch.setattr(evidence, "MAX_BYTES", 10)
    assert den.post("/api/evidence", data={"audit_id": aid}, files={"file": ("buyuk.pdf", b"x" * 50)}).status_code == 413
    monkeypatch.setattr(evidence, "MAX_BYTES", 20 * 1024 * 1024)
    # sertifika: bitiş tarihi zorunlu
    assert sa.post("/api/evidence", data={"kind": "sertifika", "supplier_id": world["a"]["id"]},
                   files={"file": ("iso.pdf", b"%PDF")}).status_code == 422
    c = sa.post("/api/evidence", data={"kind": "sertifika", "supplier_id": world["a"]["id"], "title": "ISO 27001",
                                       "expires_at": (dt.date.today() + dt.timedelta(days=20)).isoformat()},
                files={"file": ("iso.pdf", b"%PDF")})
    assert c.status_code == 201 and c.json()["cert_status"] == "yaklaşıyor"
    assert sa.post("/api/evidence", data={"kind": "sertifika", "supplier_id": world["b"]["id"], "expires_at": "2027-01-01"},
                   files={"file": ("iso.pdf", b"%PDF")}).status_code == 404       # başka tedarikçiye yükleyemez
    # indirme: ek olarak, içerik aynı; başka tedarikçi indiremez
    d = sa.get(f"/api/evidence/{eid}/download")
    assert d.status_code == 200 and d.content == b"%PDF-1.4 icerik" and "attachment" in d.headers["content-disposition"]
    assert d.headers["x-content-type-options"] == "nosniff"
    assert sb.get(f"/api/evidence/{eid}/download").status_code == 404
    assert [e["id"] for e in sb.get("/api/evidence").json()] == []
    # tedarikçi denetçinin dosyasını silemez, kendi sertifikasını güncelleyebilir
    assert sa.delete(f"/api/evidence/{eid}").status_code == 403
    assert sa.put(f"/api/evidence/{c.json()['id']}", json={"expires_at": "2028-01-01"}).json()["cert_status"] == "geçerli"
    assert sa.put(f"/api/evidence/{c.json()['id']}", json={"expires_at": "01.01.2028"}).status_code == 422
    # tamamlanmış denetime tedarikçi doğrudan kanıt ekleyemez
    _complete(den, world, {"MFA": "uygun", "Yed": "uygun", "Log": "uygun"})
    assert sa.post("/api/evidence", data={"audit_id": aid, "question_id": q}, files={"file": ("x.pdf", b"%PDF")}).status_code == 409
    assert den.delete(f"/api/evidence/{eid}").status_code == 204
    assert den.get(f"/api/evidence/{eid}").status_code == 404


def test_manual_capa_from_finding(world):
    den = world["den"]
    f = den.post("/api/findings", json={"supplier_id": world["a"]["id"], "title": "Yangın tüpü eksik", "severity": "yüksek"}).json()
    c = den.post("/api/capa", json={"finding_id": f["id"]})
    assert c.status_code == 201 and c.json()["severity"] == "yüksek" and c.json()["supplier_id"] == world["a"]["id"]
    assert den.post("/api/capa", json={"finding_id": f["id"]}).status_code == 409
    assert world["sa"].post("/api/capa", json={"supplier_id": world["a"]["id"], "title": "x"}).status_code == 403
