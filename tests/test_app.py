import asyncio

import pytest
from fastapi.testclient import TestClient

from app import checklist as cl
from app.intel import scanner


# ------------------------------------------------------------------ checklist
def test_checklist_score_weights_and_na():
    ids = [q["id"] for sec in cl.legacy_sections() for q in sec["questions"]]
    assert cl.compute_score({})["score"] is None
    assert cl.compute_score({q: "uygun" for q in ids})["score"] == 100
    assert cl.compute_score({q: "uygun değil" for q in ids})["score"] == 0
    # N/A hesaba katılmaz
    r = cl.compute_score({ids[0]: "uygun", ids[1]: "na"})
    assert r["score"] == 100
    assert cl.rating(84.9) == "Şartlı Onaylı" and cl.rating(85) == "Onaylı" and cl.rating(10) == "Onaysız"


# ------------------------------------------------------------------ scanner
def test_normalize_domain():
    assert scanner.normalize_domain("https://www.Ornek.com.tr/path") == "ornek.com.tr"
    with pytest.raises(ValueError):
        scanner.normalize_domain("not a domain")
    with pytest.raises(ValueError):
        scanner.normalize_domain("192.168.1.1")


class FakeResolver:
    use_doh = True

    def __init__(self, records):
        self.records = records

    async def query(self, name, rtype):
        return self.records.get((name, rtype), [])


def _dns(records):
    checks, info = asyncio.run(scanner.check_dns("ornek.com", FakeResolver(records)))
    return {c.id: c for c in checks}, info


def test_dns_good_posture():
    c, info = _dns({
        ("ornek.com", "A"): ["93.184.216.34"], ("ornek.com", "NS"): ["ns1.x.net."],
        ("ornek.com", "MX"): ["10 mx.ornek.com."],
        ("ornek.com", "TXT"): ['"v=spf1 include:_spf.google.com" " -all"'],
        ("_dmarc.ornek.com", "TXT"): ['"v=DMARC1; p=reject; rua=mailto:d@ornek.com"'],
        ("ornek.com", "CAA"): ['0 issue "letsencrypt.org"'], ("ornek.com", "DNSKEY"): ["257 3 13 abc"],
    })
    assert c["email.spf"].status == "pass"
    assert c["email.dmarc"].status == "pass"
    assert c["dns.dnssec"].status == "pass" and c["dns.caa"].status == "pass"
    assert info["mx"] == ["mx.ornek.com"]


def test_dns_weak_posture():
    c, _ = _dns({
        ("ornek.com", "A"): ["1.2.3.4"], ("ornek.com", "NS"): ["ns1.x.net."],
        ("ornek.com", "TXT"): ['"v=spf1 a mx +all"'],
        ("_dmarc.ornek.com", "TXT"): ['"v=DMARC1; p=none"'],
    })
    assert c["email.spf"].status == "warn"
    assert c["email.dmarc"].status == "warn"
    assert c["dns.dnssec"].status == "warn"


def test_dns_missing_email_records():
    c, _ = _dns({("ornek.com", "A"): ["1.2.3.4"], ("ornek.com", "NS"): ["ns1.x.net."]})
    assert c["email.spf"].status == "fail" and c["email.dmarc"].status == "fail"


def test_dns_unreliable_resolver_not_scored_as_fail():
    # NS dönmüyorsa (DNS engelli) e-posta kontrolleri "başarısız" sayılmamalı
    c, info = _dns({("ornek.com", "A"): ["1.2.3.4"]})
    assert list(c) == ["dns.resolve"] and c["dns.resolve"].status == "error"
    assert info["dns_ok"] is False


def test_score_and_grade():
    C = scanner.Check
    assert scanner.score([C("a", "x", "a", "pass", weight=10), C("b", "x", "b", "fail", weight=10)]) == (50.0, "F")
    assert scanner.score([C("a", "x", "a", "pass", weight=10), C("b", "x", "b", "error", weight=0)]) == (100.0, "A")
    assert scanner.score([C("a", "x", "a", "info", weight=0)]) == (None, None)


# ------------------------------------------------------------------ API
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("app.db.DB_PATH", tmp_path / "t.db")
    from app.main import app
    with TestClient(app) as c:
        yield c


def fake_result(domain, dmarc_status):
    checks = [
        scanner.Check("email.spf", "E-posta", "SPF kaydı", "pass", "ok", weight=10),
        scanner.Check("email.dmarc", "E-posta", "DMARC politikası", dmarc_status, "x", weight=10,
                      severity="yüksek", recommendation="DMARC ekleyin"),
    ]
    from dataclasses import asdict
    s, g = scanner.score(checks)
    return {"domain": domain, "web_host": domain, "scanned_at": "2026-01-01T00:00:00+00:00", "duration_s": 0.1,
            "score": s, "grade": g, "summary": {"pass": 1, "warn": 0, "fail": 1, "info": 0, "error": 0},
            "checks": [asdict(c) for c in checks]}


def test_full_flow(client, monkeypatch):
    r = client.post("/api/suppliers", json={"name": "Test A.Ş.", "domains": ["https://www.ornek.com/", "ornek.com"], "criticality": "yüksek"})
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["domains"] == ["ornek.com"]
    sid = s["id"]

    assert client.post("/api/suppliers", json={"name": "X", "domains": ["geçersiz domain"]}).status_code == 422

    # Soru seti seçilmeden denetim oluşturulamaz
    assert client.post(f"/api/suppliers/{sid}/audits", json={"audit_date": "2026-01-10"}).status_code == 422
    genel = next(x for x in client.get("/api/question-sets").json() if x["standard"] == "Genel")

    # Denetim + cevaplar + puan
    a = client.post(f"/api/suppliers/{sid}/audits", json={"audit_date": "2026-01-10", "question_set_ids": [genel["id"]]}).json()
    ids = [q["id"] for sec in a["checklist"] for q in sec["questions"]]
    assert len(ids) == 27
    answers = {q: {"answer": "uygun"} for q in ids}
    answers[ids[0]] = {"answer": "uygun değil", "note": "kanıt yok"}
    answers[ids[1]] = {"answer": "kısmen"}
    a = client.put(f"/api/audits/{a['id']}", json={"status": "tamamlandı", "answers": answers}).json()
    assert 80 < a["score"] < 100
    assert client.put(f"/api/audits/{a['id']}", json={"answers": {"yok.1": {"answer": "uygun"}}}).status_code == 422

    g = client.post(f"/api/audits/{a['id']}/generate-findings").json()
    assert g["created"] == 2
    assert client.post(f"/api/audits/{a['id']}/generate-findings").json()["created"] == 0  # tekrar oluşturmaz

    # Dış istihbarat: başarısız kontrol -> bulgu
    results = iter([fake_result("ornek.com", "fail"), fake_result("ornek.com", "fail"), fake_result("ornek.com", "pass")])

    async def fake_scan(d):
        return next(results)
    monkeypatch.setattr("app.main.scan_domain", fake_scan)

    r1 = client.post(f"/api/suppliers/{sid}/scan").json()
    assert r1[0]["findings_created"] == 1 and r1[0]["grade"] == "F"
    r2 = client.post(f"/api/suppliers/{sid}/scan").json()
    assert r2[0]["findings_created"] == 0  # aynı bulgu tekrar açılmaz
    r3 = client.post(f"/api/suppliers/{sid}/scan").json()
    assert r3[0]["findings_closed"] == 1   # düzelince otomatik kapanır

    intel = client.get(f"/api/findings?supplier_id={sid}&source=istihbarat").json()
    assert len(intel) == 1 and intel[0]["status"] == "kapandı" and intel[0]["closed_at"]

    detail = client.get(f"/api/suppliers/{sid}").json()
    sm = detail["summary"]
    assert sm["intel_grade"] == "A" and sm["intel_score"] == 100
    assert sm["open_findings"] == 2
    assert sm["overall_score"] == round(a["score"] * 0.6 + 100 * 0.4, 1)
    assert len(detail["scan_history"]) == 3

    d = client.get("/api/dashboard").json()
    assert d["totals"]["suppliers"] == 1 and d["totals"]["closed_last_30"] == 1

    # Bulgu güncelleme
    f = client.get("/api/findings?status=açık-tümü").json()[0]
    upd = client.put(f"/api/findings/{f['id']}", json={"status": "kapandı", "owner": "Ali"}).json()
    assert upd["status"] == "kapandı" and upd["closed_at"] and upd["owner"] == "Ali"

    assert client.get("/").status_code == 200
    assert client.delete(f"/api/suppliers/{sid}").status_code == 204
    assert client.get("/api/findings").json() == []


def test_question_sets_and_combined_audit(client):
    sets = client.get("/api/question-sets").json()
    names = {x["standard"] for x in sets if x["builtin"]}
    assert {"PCI DSS", "ISO 27001", "ISO 9001", "ISO 14001", "ISO 45001", "KVKK", "Genel"} <= names
    pci = next(x for x in sets if x["standard"] == "PCI DSS")
    iso = next(x for x in sets if x["standard"] == "ISO 27001")

    # Standart setler değiştirilemez / silinemez
    body = {"name": "X", "sections": [{"title": "B", "questions": ["S?"]}]}
    assert client.put(f"/api/question-sets/{pci['id']}", json=body).status_code == 403
    assert client.delete(f"/api/question-sets/{pci['id']}").status_code == 403
    assert client.post("/api/question-sets", json={"name": "X", "sections": [{"title": "B", "questions": [" "]}]}).status_code == 422

    # Kopyala -> düzenle
    c = client.post(f"/api/question-sets/{iso['id']}/clone").json()
    assert not c["builtin"] and c["question_count"] == iso["question_count"]
    c2 = client.put(f"/api/question-sets/{c['id']}", json={"name": "Bulut BG", "standard": "ISO 27001", "sections": [
        {"title": "Erişim", "weight": 30, "questions": ["MFA var mı?", "Yetki gözden geçirme yapılıyor mu?"]}]}).json()
    assert c2["question_count"] == 2 and c2["sections"][0]["questions"][1]["id"] == "s1.2"

    # Çoklu set + özel sorular + kütüphaneye kaydet
    sid = client.post("/api/suppliers", json={"name": "Ödeme A.Ş."}).json()["id"]
    a = client.post(f"/api/suppliers/{sid}/audits", json={
        "audit_date": "2026-02-01", "question_set_ids": [pci["id"], c2["id"], pci["id"]],
        "custom_sections": [{"title": "Sahaya özel", "weight": 5, "questions": ["Kamera kayıtları 90 gün saklanıyor mu?"]}],
        "save_custom_as": "Saha kontrolleri"}).json()
    assert a["set_names"] == ["PCI DSS v4.0", "Bulut BG", "Saha kontrolleri"]
    qids = [q["id"] for sec in a["checklist"] for q in sec["questions"]]
    assert len(qids) == len(set(qids)) == pci["question_count"] + 2 + 1
    assert any(x["name"] == "Saha kontrolleri" for x in client.get("/api/question-sets").json())

    # Set sonradan silinse/değişse de denetim kendi kopyasını korur
    client.delete(f"/api/question-sets/{c2['id']}")
    answers = {q: {"answer": "uygun"} for q in qids}
    answers[qids[-1]] = {"answer": "uygun değil"}
    r = client.put(f"/api/audits/{a['id']}", json={"answers": answers, "status": "tamamlandı"}).json()
    assert 0 < r["score"] < 100 and len(r["result"]["sections"]) == len(a["checklist"])
    assert client.post(f"/api/audits/{a['id']}/generate-findings").json()["created"] == 1
    f = client.get(f"/api/findings?supplier_id={sid}").json()[0]
    assert f["title"] == "[Sahaya özel] Kamera kayıtları 90 gün saklanıyor mu?"


def test_legacy_db_migration(tmp_path, monkeypatch):
    """Soru seti özelliğinden önceki veritabanı açılabilmeli, eski denetim cevapları korunmalı."""
    import sqlite3
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript("""
      CREATE TABLE suppliers (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, category TEXT, country TEXT,
        contact_name TEXT, email TEXT, phone TEXT, domains TEXT NOT NULL DEFAULT '[]', criticality TEXT NOT NULL DEFAULT 'orta',
        status TEXT NOT NULL DEFAULT 'aktif', notes TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now')));
      CREATE TABLE audits (id INTEGER PRIMARY KEY AUTOINCREMENT, supplier_id INTEGER NOT NULL, audit_date TEXT NOT NULL,
        auditor TEXT, audit_type TEXT NOT NULL DEFAULT 'yerinde', status TEXT NOT NULL DEFAULT 'planlandı', score REAL,
        notes TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now')));
      CREATE TABLE audit_answers (audit_id INTEGER NOT NULL, question_id TEXT NOT NULL, answer TEXT, note TEXT,
        PRIMARY KEY (audit_id, question_id));
      INSERT INTO suppliers (name) VALUES ('Eski');
      INSERT INTO audits (supplier_id, audit_date, status, score) VALUES (1, '2025-01-01', 'tamamlandı', 0);
      INSERT INTO audit_answers VALUES (1, 'kalite.1', 'uygun değil', NULL);
    """)
    old.commit(); old.close()
    monkeypatch.setattr("app.db.DB_PATH", path)
    from app.main import app
    with TestClient(app) as c:
        a = c.get("/api/audits/1").json()
        assert a["set_names"] == ["Genel Tedarikçi Denetimi"] and a["answers"]["kalite.1"]["answer"] == "uygun değil"
        assert a["result"]["score"] == 0
        assert len(c.get("/api/question-sets").json()) >= 7


def test_question_set_unique_names(client):
    body = {"name": "Depo", "sections": [{"title": "B", "questions": ["S?"]}]}
    a = client.post("/api/question-sets", json=body)
    assert a.status_code == 201
    assert client.post("/api/question-sets", json={**body, "name": "depo "}).status_code == 409
    c1 = client.post(f"/api/question-sets/{a.json()['id']}/clone").json()
    c2 = client.post(f"/api/question-sets/{a.json()['id']}/clone").json()
    assert c1["name"] == "Depo (kopya)" and c2["name"] == "Depo (kopya 2)"
    assert client.put(f"/api/question-sets/{c1['id']}", json=body).status_code == 409
    assert client.put(f"/api/question-sets/{a.json()['id']}", json=body).status_code == 200
