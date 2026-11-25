"""Grup E: sürekli izleme, alarmlar, otomatik yeniden değerlendirme, hatırlatmalar."""
import asyncio
import datetime as dt

from app import scheduler
from conftest import PW


def _scan(dom, grade, score, fails=(), tls_days=None):
    checks = [{"id": f, "title": f, "status": "fail", "severity": "yüksek", "detail": "d", "recommendation": "r", "category": "c"} for f in fails]
    if tls_days is not None:
        checks.append({"id": "tls.cert", "title": "TLS", "status": "pass", "data": {"days_left": tls_days}, "detail": "x"})
    return {"domain": dom, "grade": grade, "score": score, "checks": checks, "summary": {}, "scanned_at": dt.datetime.now().isoformat(timespec="seconds"), "web_host": dom,
            "duration_s": 0}


def test_diff_scans():
    assert scheduler.diff_scans(None, _scan("a.com", "C", 70, ["x"])) == []          # ilk tarama alarmı yok
    al = scheduler.diff_scans(_scan("a.com", "B", 85), _scan("a.com", "D", 62, ["dmarc"], tls_days=5))
    kinds = {a["kind"]: a for a in al}
    assert kinds["siber not düştü"]["severity"] == "yüksek" and "B → D" in kinds["siber not düştü"]["title"]
    assert "yeni siber sorun" in kinds and "TLS sertifikası bitiyor" in kinds
    assert scheduler.diff_scans(_scan("a.com", "C", 70, ["dmarc"]), _scan("a.com", "B", 80, ["dmarc"])) == []


def test_next_due_and_reminder():
    assert scheduler.next_audit_due("2026-01-10", 365, "2025-01-01") == "2027-01-10"
    assert scheduler.next_audit_due(None, 365, "2026-03-05 10:00:00") == "2026-03-05"   # hiç denetlenmemiş -> hemen
    t = dt.date(2026, 6, 10)
    assert scheduler.reminder_due(None, 7, t) and scheduler.reminder_due("2026-06-03", 7, t) and not scheduler.reminder_due("2026-06-05", 7, t)


def test_jobs(admin, monkeypatch):
    from app import notify
    mails = []
    monkeypatch.setattr(notify, "send_email", lambda s, to, subj, text, html: mails.append((to, subj)))
    admin.put("/api/settings", json={"smtp_host": "smtp.x", "smtp_from": "a@b.com"})
    tiers = {t["level"]: t["id"] for t in admin.get("/api/supplier-tiers").json()}
    s = admin.post("/api/suppliers", json={"name": "Tier1", "tier_id": tiers[1], "domains": ["ornek.com"], "notify_emails": "t@t.com"}).json()
    s2 = admin.post("/api/suppliers", json={"name": "Tier3", "tier_id": tiers[3]}).json()
    # yeniden değerlendirme: hiç denetlenmemiş aktif tedarikçilere denetim planlanır, ikinci kez planlanmaz
    r = scheduler.job_reassess()
    assert r["planned"] == 2 and scheduler.job_reassess()["planned"] == 0
    aud = [a for a in admin.get("/api/audits").json() if a["supplier_id"] == s["id"]]
    assert aud[0]["status"] == "planlandı" and aud[0]["set_names"] == ["Genel Tedarikçi Denetimi"]
    # tamamlanan denetim sonrası: tier sıklığı (365 gün) dolmadan yeni plan yok
    q = [x["id"] for sec in aud[0]["checklist"] for x in sec["questions"]]
    admin.put(f"/api/audits/{aud[0]['id']}", json={"status": "tamamlandı", "audit_date": dt.date.today().isoformat(),
                                                   "answers": {i: {"answer": "uygun"} for i in q}})
    for a in admin.get("/api/audits").json():
        if a["supplier_id"] == s2["id"]:
            admin.delete(f"/api/audits/{a['id']}")
    assert scheduler.job_reassess()["planned"] == 1                          # yalnız Tier3 (hiç denetlenmemiş)
    assert scheduler.job_reassess(dt.date.today() + dt.timedelta(days=340))["planned"] == 1   # Tier1: 365-30 gün sonra
    # hatırlatma: geciken CAPA -> tedarikçiye tek e-posta (7 günde bir), alarm
    f = admin.post("/api/findings", json={"supplier_id": s["id"], "title": "Eksik", "due_date": "2020-01-01"}).json()
    admin.post("/api/capa", json={"finding_id": f["id"], "due_date": "2020-01-01"})
    exp = (dt.date.today() + dt.timedelta(days=5)).isoformat()
    admin.post("/api/evidence", data={"kind": "sertifika", "supplier_id": s["id"], "expires_at": exp}, files={"file": ("c.pdf", b"%PDF")})
    r = scheduler.job_reminders()
    assert r["emails"] == 1 and mails[-1][0] == ["t@t.com"] and "Hatırlatma" in mails[-1][1]
    assert scheduler.job_reminders()["emails"] == 0                          # aynı hafta tekrar yok
    al = admin.get("/api/alerts").json()
    kinds = {a["kind"] for a in al["items"]}
    assert {"geciken CAPA", "sertifika", "yeniden değerlendirme"} <= kinds and al["unread"] >= 3
    # izleme: not düşüşü alarmı (sahte tarama)
    seq = iter([_scan("ornek.com", "B", 85), _scan("ornek.com", "F", 40, ["dmarc"])])

    async def fake(d):
        return next(seq)
    monkeypatch.setattr("app.intel.scanner.scan_domain", fake)
    assert asyncio.run(scheduler.job_monitor())["scanned"] == 1
    assert asyncio.run(scheduler.job_monitor())["scanned"] == 0              # 24 saat dolmadı
    r = asyncio.run(scheduler.job_monitor(force=True))
    assert r["scanned"] == 1 and r["alerts"] >= 2
    assert any("B → F" in a["title"] for a in admin.get("/api/alerts").json()["items"])
    # okundu işaretleme
    assert admin.post("/api/alerts/read-all").json()["marked"] >= 4
    assert admin.get("/api/alerts?unread=true").json()["unread"] == 0


def test_scheduler_api_and_perms(admin, login):
    st = admin.get("/api/scheduler").json()
    assert st["config"]["monitor_interval_hours"] == 24 and not st["running"]
    c = admin.put("/api/scheduler", json={"monitor_enabled": False, "monitor_interval_hours": 12, "reassess_enabled": True,
                                          "reassess_lead_days": 10, "reminders_enabled": True, "reminder_every_days": 3}).json()
    assert c["config"]["monitor_interval_hours"] == 12
    r = admin.post("/api/scheduler/run").json()
    assert set(r["ran"]) == {"waivers", "reassess", "reminders"} and r["jobs"]["reassess"]["last_run"]
    s = admin.post("/api/suppliers", json={"name": "S"}).json()
    admin.post("/api/users", json={"name": "t", "email": "t@x.com", "role": "supplier", "supplier_id": s["id"], "password": PW})
    admin.post("/api/users", json={"name": "d", "email": "d@x.com", "role": "auditor", "password": PW})
    assert login("t@x.com").get("/api/alerts").status_code == 403
    assert login("d@x.com").get("/api/alerts").status_code == 200 and login("d@x.com").post("/api/scheduler/run").status_code == 403
