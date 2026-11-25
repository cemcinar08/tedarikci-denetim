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
        r = c.post("/api/auth/setup", json={"name": "Admin", "email": "admin@test.com", "password": "parola1234"})
        assert r.status_code == 200, r.text
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
    assert len(ids) == 29  # 27 + 2 koşullu alt soru
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
        c.post("/api/auth/setup", json={"name": "Admin", "email": "admin@test.com", "password": "parola1234"})
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


# ------------------------------------------------------------------ bildirimler
def test_webhook_signature():
    from app import notify
    sig = notify.sign("gizli", "1700000000", b'{"a":1}')
    import hashlib
    import hmac
    assert sig == "sha256=" + hmac.new(b"gizli", b'1700000000.{"a":1}', hashlib.sha256).hexdigest()


def test_notification_flow(client, monkeypatch):
    from app import notify
    sent_mail, sent_hooks = [], []
    monkeypatch.setattr(notify, "send_email", lambda s, to, subj, text, html: sent_mail.append((to, subj, text, html)))

    async def fake_hook(url, secret, payload):
        sent_hooks.append((url, secret, payload))
        if "bozuk" in url:
            raise RuntimeError("HTTP 500: hata")
        return 200
    monkeypatch.setattr(notify, "send_webhook", fake_hook)

    # Geçersiz alanlar reddedilir
    assert client.post("/api/suppliers", json={"name": "X", "notify_emails": "yanlis"}).status_code == 422
    assert client.post("/api/suppliers", json={"name": "X", "webhook_url": "ftp://x"}).status_code == 422

    sid = client.post("/api/suppliers", json={
        "name": "Tedarikçi <b>A.Ş.</b>", "notify_emails": "a@t.com; b@t.com",
        "webhook_url": "https://t.com/hook", "webhook_secret": "s3cr3t"}).json()["id"]
    f1 = client.post("/api/findings", json={"supplier_id": sid, "title": "MFA yok <script>", "severity": "yüksek"}).json()
    f2 = client.post("/api/findings", json={"supplier_id": sid, "title": "Yedek testi yok", "severity": "orta"}).json()
    other = client.post("/api/suppliers", json={"name": "Başka"}).json()["id"]
    f3 = client.post("/api/findings", json={"supplier_id": other, "title": "x"}).json()

    # SMTP yokken e-posta başarısız olarak kaydedilir, webhook gider
    r = client.post("/api/notifications", json={"supplier_id": sid, "finding_ids": [f1["id"], f2["id"]],
                                                "channels": ["email", "webhook"], "note": "15 gün içinde dönün"}).json()
    by = {n["channel"]: n for n in r}
    assert by["email"]["status"] == "başarısız" and "SMTP" in by["email"]["error"]
    assert by["webhook"]["status"] == "gönderildi" and by["webhook"]["response_code"] == 200
    url, secret, payload = sent_hooks[-1]
    assert secret == "s3cr3t" and payload["event"] == "finding.notification" and len(payload["findings"]) == 2
    assert payload["findings"][0]["severity"] == "yüksek" and payload["message"] == "15 gün içinde dönün"
    assert payload["response"]["api"].endswith(by["webhook"]["token"])

    # Başka tedarikçinin bulgusu eklenemez
    assert client.post("/api/notifications", json={"supplier_id": sid, "finding_ids": [f3["id"]], "channels": ["webhook"]}).status_code == 422

    # SMTP ayarla -> e-posta tekrar denenir
    client.put("/api/settings", json={"org_name": "ABC", "smtp_host": "smtp.x.com", "smtp_from": "d@abc.com", "smtp_password": "p"})
    st = client.get("/api/settings").json()
    assert st["smtp_ready"] and st["smtp_password_set"] and "smtp_password" not in st
    rr = client.post(f"/api/notifications/{by['email']['id']}/retry").json()
    assert rr["status"] == "gönderildi" and sent_mail[-1][0] == ["a@t.com", "b@t.com"]
    to, subj, text, html_body = sent_mail[-1]
    assert subj.startswith("[ABC]") and "&lt;script&gt;" in html_body and "<script>" not in html_body
    assert client.post(f"/api/notifications/{by['email']['id']}/retry").status_code == 409

    f = client.get(f"/api/findings?supplier_id={sid}").json()
    assert all(x["supplier_status"] == "bildirildi" and x["notified_at"] for x in f)

    # Tedarikçi yanıtı (API)
    tok = by["webhook"]["token"]
    v = client.get(f"/api/public/respond/{tok}").json()
    assert v["supplier"] == "Tedarikçi <b>A.Ş.</b>" and len(v["findings"]) == 2 and "id" in v["findings"][0]
    assert client.get("/api/public/respond/yanlis-token-1234567890abc").status_code == 404
    assert client.post(f"/api/public/respond/{tok}", json={"finding_id": f3["id"], "status": "okundu"}).status_code == 422
    assert client.post(f"/api/public/respond/{tok}", json={"status": "bilinmiyor"}).status_code == 422
    r = client.post(f"/api/public/respond/{tok}", json={"finding_id": f1["id"], "status": "aksiyon planlandı",
                                                         "comment": "MFA devreye alınacak", "planned_date": "2026-12-20", "responder": "Ali"})
    assert r.status_code == 201
    client.post(f"/api/public/respond/{tok}", json={"status": "okundu"})  # tüm bulgulara
    f = {x["id"]: x for x in client.get(f"/api/findings?supplier_id={sid}").json()}
    assert f[f1["id"]]["supplier_status"] == "okundu" and f[f1["id"]]["supplier_due_date"] == "2026-12-20"
    assert f[f1["id"]]["status"] == "devam ediyor" and f[f2["id"]]["status"] == "açık"

    n = client.get(f"/api/notifications?supplier_id={sid}").json()
    hook = next(x for x in n if x["channel"] == "webhook")
    assert len(hook["responses"]) == 3 and hook["viewed_at"]
    assert client.get(f"/yanit/{tok}").status_code == 200

    # Önizleme ve manuel kanal
    pv = client.post("/api/notifications/preview", json={"supplier_id": sid, "finding_ids": [f2["id"]]}).json()
    assert pv["base_url"] == "http://testserver" and "Yedek testi yok" in pv["text"]
    client.put("/api/settings", json={"public_base_url": "https://denetim.abc.com/"})
    pv = client.post("/api/notifications/preview", json={"supplier_id": sid, "finding_ids": [f2["id"]]}).json()
    assert pv["base_url"] == "https://denetim.abc.com" and not pv["base_is_local"]
    assert "https://denetim.abc.com/yanit/" in pv["text"]
    assert client.put("/api/settings", json={"public_base_url": "denetim.abc.com"}).status_code == 422
    m = client.post("/api/notifications", json={"supplier_id": sid, "finding_ids": [f2["id"]], "channels": ["manuel"]}).json()[0]
    assert m["status"] == "manuel" and "mailto_body" in m and m["token"] in m["mailto_body"]

    # Bozuk webhook -> başarısız + hata kaydı
    client.put(f"/api/suppliers/{sid}", json={"name": "T", "webhook_url": "https://t.com/bozuk"})
    b = client.post("/api/notifications", json={"supplier_id": sid, "finding_ids": [f2["id"]], "channels": ["webhook"]}).json()[0]
    assert b["status"] == "başarısız" and b["response_code"] == 500


def test_auto_notify_on_scan(client, monkeypatch):
    from app import notify
    hooks = []

    async def fake_hook(url, secret, payload):
        hooks.append(payload); return 204
    monkeypatch.setattr(notify, "send_webhook", fake_hook)

    async def fake_scan(d):
        return fake_result(d, "fail")
    monkeypatch.setattr("app.main.scan_domain", fake_scan)

    sid = client.post("/api/suppliers", json={"name": "Oto", "domains": ["ornek.com"], "webhook_url": "https://t.com/h"}).json()["id"]
    r = client.post(f"/api/suppliers/{sid}/scan").json()
    assert r[0]["findings_created"] == 1 and r[0]["notified"] == [] and not hooks  # otomatik kapalı

    client.put(f"/api/suppliers/{sid}", json={"name": "Oto", "domains": ["ornek.com"], "webhook_url": "https://t.com/h", "auto_notify": True})
    async def fake_scan2(d):
        res = fake_result(d, "fail"); res["checks"][0]["status"] = "fail"; res["checks"][0]["title"] = "Yeni sorun"; return res
    monkeypatch.setattr("app.main.scan_domain", fake_scan2)
    r = client.post(f"/api/suppliers/{sid}/scan").json()
    assert r[0]["findings_created"] == 1 and r[0]["notified"] == ["webhook"]
    assert hooks[-1]["scan"]["domain"] == "ornek.com" and len(hooks[-1]["findings"]) == 1
    assert hooks[-1]["event"] == "finding.notification"
