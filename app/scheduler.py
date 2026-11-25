"""Arka plan zamanlayıcısı (uygulama açıkken çalışır) ve alarm merkezi.

Görevler (her biri günde en fazla bir kez; "Şimdi çalıştır" ile elle de tetiklenir):
  monitor     — domain'leri olan aktif tedarikçileri yeniden tarar; not düşüşü / yeni ciddi sorun -> alarm
  reassess    — tier denetim sıklığına göre sonraki denetimi otomatik planlar
  reminders   — geciken CAPA ve öz-değerlendirmeler için tedarikçiye e-posta; sertifika/sözleşme/denetim uyarıları -> alarm
  waivers     — süresi dolan risk kabullerini sonlandırır
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import auth, events
from .auth import scoped
from .db import get_db, row, rows

router = APIRouter()
GRADES = "ABCDF"
SETTINGS_DEFAULTS = {"monitor_enabled": True, "monitor_interval_hours": 24, "reassess_enabled": True, "reassess_lead_days": 30,
                     "reminders_enabled": True, "reminder_every_days": 7}


# ------------------------------------------------------------------ saf kurallar
def diff_scans(prev: Optional[dict], new: dict) -> list[dict]:
    """Saf: iki tarama sonucunu karşılaştırır, alarm listesi döner."""
    out = []
    dom = new["domain"]
    pg, ng = (prev or {}).get("grade"), new.get("grade")
    if pg and ng and GRADES.index(ng) > GRADES.index(pg):
        drop = GRADES.index(ng) - GRADES.index(pg)
        out.append({"kind": "siber not düştü", "severity": "yüksek" if drop >= 2 or ng in "DF" else "orta",
                    "title": f"{dom}: siber not {pg} → {ng}", "detail": f"Puan {prev.get('score')} → {new.get('score')}"})
    prev_fail = {c["id"] for c in (prev or {}).get("checks", []) if c["status"] == "fail"}
    for c in new.get("checks", []):
        if c["status"] == "fail" and c["id"] not in prev_fail and c.get("severity") in ("kritik", "yüksek") and prev is not None:
            out.append({"kind": "yeni siber sorun", "severity": c["severity"], "title": f"{dom}: {c['title']}", "detail": c.get("detail")})
        if c["id"] == "tls.cert" and (c.get("data") or {}).get("days_left") is not None and 0 <= c["data"]["days_left"] <= 14:
            out.append({"kind": "TLS sertifikası bitiyor", "severity": "orta", "title": f"{dom}: TLS sertifikası {c['data']['days_left']} gün içinde bitiyor",
                        "detail": c.get("detail")})
    return out


def next_audit_due(last_completed: Optional[str], interval_days: int, created_at: str) -> str:
    """Saf: son tamamlanan denetimden (yoksa kayıt tarihinden) itibaren tier sıklığına göre sonraki denetim tarihi."""
    base = dt.date.fromisoformat((last_completed or created_at)[:10])
    return (base + dt.timedelta(days=interval_days)).isoformat() if last_completed else base.isoformat()


def reminder_due(last_sent: Optional[str], every_days: int, today: dt.date) -> bool:
    return not last_sent or (today - dt.date.fromisoformat(last_sent[:10])).days >= every_days


# ------------------------------------------------------------------ ayarlar ve alarmlar
def get_conf(db) -> dict:
    r = db.execute("SELECT value FROM settings WHERE key='scheduler'").fetchone()
    try:
        c = json.loads(r[0]) if r and r[0] else {}
    except ValueError:
        c = {}
    return {**SETTINGS_DEFAULTS, **{k: v for k, v in c.items() if k in SETTINGS_DEFAULTS}}


def add_alert(db, supplier_id: Optional[int], kind: str, severity: str, title: str, detail: Optional[str] = None,
              entity: Optional[str] = None, entity_id=None, dedupe: Optional[str] = None) -> Optional[int]:
    """Aynı dedupe anahtarıyla tekrar alarm üretmez. Yeni alarm webhook'lara 'alert.created' olarak gider."""
    key = dedupe or f"{kind}|{entity}|{entity_id}|{title}"
    if db.execute("SELECT 1 FROM alerts WHERE dedupe_key=?", (key,)).fetchone():
        return None
    aid = db.execute("INSERT INTO alerts (supplier_id, kind, severity, title, detail, entity, entity_id, dedupe_key) VALUES (?,?,?,?,?,?,?,?)",
                     (supplier_id, kind, severity, title, detail, entity, None if entity_id is None else str(entity_id), key)).lastrowid
    events.emit("alert.created", supplier_id, "alert", aid, {"id": aid, "kind": kind, "severity": severity, "title": title, "detail": detail}, db)
    return aid


def _last_run(db, job: str) -> Optional[str]:
    r = db.execute("SELECT value FROM settings WHERE key=?", (f"job_last_{job}",)).fetchone()
    return r[0] if r else None


def _mark(db, job: str, result: dict) -> None:
    db.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (f"job_last_{job}", dt.datetime.now().isoformat(timespec="seconds")))
    db.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (f"job_result_{job}", json.dumps(result, ensure_ascii=False)))


# ------------------------------------------------------------------ görevler
async def job_monitor(force: bool = False) -> dict:
    from .intel.scanner import scan_domain
    from .main import _store_scan
    from . import notify
    with get_db() as db:
        conf = get_conf(db)
        sups = rows(db.execute("SELECT id, name, domains FROM suppliers WHERE status IN ('aktif','aday') AND domains != '[]'").fetchall())
    hours = int(conf["monitor_interval_hours"])
    scanned, alerts = 0, 0
    for s in sups:
        for d in s["domains"] or []:
            with get_db() as db:
                prev = row(db.execute("SELECT * FROM intel_scans WHERE supplier_id=? AND domain=? ORDER BY id DESC LIMIT 1", (s["id"], d)).fetchone())
            if prev and not force:
                age = dt.datetime.now() - dt.datetime.fromisoformat(prev["scanned_at"][:19])
                if age < dt.timedelta(hours=hours):
                    continue
            try:
                res = await scan_domain(d)
            except Exception:
                continue
            with get_db() as db:
                meta = _store_scan(db, s["id"], res, True)
                for a in diff_scans(prev["result"] if prev else None, res):
                    if add_alert(db, s["id"], a["kind"], a["severity"], a["title"], a["detail"], "scan", meta["scan_id"]):
                        alerts += 1
            await notify.auto_notify(s["id"], meta.pop("new_finding_ids"), meta["scan_id"])
            scanned += 1
    return {"scanned": scanned, "alerts": alerts}


def job_reassess(today: Optional[dt.date] = None) -> dict:
    """Tier sıklığına göre denetimi yaklaşan/geçen ve planlı denetimi olmayan tedarikçilere denetim planlar."""
    t = today or dt.date.today()
    created = 0
    with get_db() as db:
        conf = get_conf(db)
        lead = int(conf["reassess_lead_days"])
        sups = rows(db.execute(
            "SELECT s.id, s.name, s.created_at, t.audit_interval_days FROM suppliers s JOIN supplier_tiers t ON t.id=s.tier_id "
            "WHERE s.status='aktif'").fetchall())
        for s in sups:
            if db.execute("SELECT 1 FROM audits WHERE supplier_id=? AND status!='tamamlandı'", (s["id"],)).fetchone():
                continue
            last = row(db.execute("SELECT audit_date, template_ids, set_names, checklist FROM audits WHERE supplier_id=? AND status='tamamlandı' "
                                  "ORDER BY audit_date DESC, id DESC LIMIT 1", (s["id"],)).fetchone())
            due = next_audit_due(last["audit_date"] if last else None, s["audit_interval_days"], s["created_at"])
            if (dt.date.fromisoformat(due) - t).days > lead:
                continue
            tids = (last or {}).get("template_ids") or []
            if not tids:
                g = db.execute("SELECT id FROM audit_templates WHERE key='genel'").fetchone()
                tids = [g[0]] if g else []
            if not tids:
                continue
            from .main import AuditIn, _build_checklist
            body = AuditIn(question_set_ids=tids, audit_date=max(due, t.isoformat()), audit_type="yerinde",
                           notes="Tier denetim sıklığına göre otomatik planlandı.")
            sections, names, ids = _build_checklist(db, body)
            aid = db.execute("INSERT INTO audits (supplier_id, audit_date, audit_type, status, notes, checklist, set_names, template_ids) "
                             "VALUES (?,?,?,?,?,?,?,?)", (s["id"], body.audit_date, "yerinde", "planlandı", body.notes,
                                                          json.dumps(sections, ensure_ascii=False), json.dumps(names, ensure_ascii=False),
                                                          json.dumps(ids))).lastrowid
            add_alert(db, s["id"], "yeniden değerlendirme", "bilgi", f"{s['name']}: yeniden denetim planlandı ({body.audit_date})",
                      f"Tier sıklığı {s['audit_interval_days']} gün", "audit", aid)
            created += 1
    return {"planned": created}


def job_reminders(today: Optional[dt.date] = None) -> dict:
    from . import notify
    t = today or dt.date.today()
    ts = t.isoformat()
    sent, alerts = 0, 0
    with get_db() as db:
        conf = get_conf(db)
        every = int(conf["reminder_every_days"])
        s_conf = notify.get_settings(db)
        mail_ok = notify.smtp_ready(s_conf)
        # 1) geciken CAPA ve öz-değerlendirmeler -> tedarikçiye toplu e-posta
        per_sup: dict[int, list[str]] = {}
        for c in rows(db.execute("SELECT c.id, c.supplier_id, c.title, c.due_date FROM capa_items c WHERE c.status IN ('açık','reddedildi') "
                                 "AND c.due_date < ?", (ts,)).fetchall()):
            per_sup.setdefault(c["supplier_id"], []).append(f"CAPA (termin {c['due_date']}): {c['title']}")
            if add_alert(db, c["supplier_id"], "geciken CAPA", "orta", f"CAPA termini geçti: {c['title'][:90]}", None, "capa", c["id"]):
                alerts += 1
        for a in rows(db.execute("SELECT id, supplier_id, set_names, due_date FROM audits WHERE respondent='tedarikçi' AND "
                                 "status IN ('gönderildi','yanıtlanıyor','revizyon istendi') AND due_date < ?", (ts,)).fetchall()):
            per_sup.setdefault(a["supplier_id"], []).append(f"Öz-değerlendirme (son tarih {a['due_date']}): {', '.join(a['set_names'] or [])}")
            if add_alert(db, a["supplier_id"], "geciken anket", "orta", "Öz-değerlendirme son tarihi geçti", None, "audit", a["id"]):
                alerts += 1
        for sid, items in per_sup.items():
            sup = row(db.execute("SELECT name, notify_emails FROM suppliers WHERE id=?", (sid,)).fetchone())
            last = db.execute("SELECT MAX(sent_at) FROM reminders_sent WHERE supplier_id=?", (sid,)).fetchone()[0]
            if not (mail_ok and sup and sup["notify_emails"] and reminder_due(last, every, t)):
                continue
            text = "Aşağıdaki kayıtların süresi geçmiştir; lütfen tedarikçi portalından tamamlayın:\n\n- " + "\n- ".join(items)
            try:
                import html as _h
                notify.send_email(s_conf, notify.parse_emails(sup["notify_emails"]), f"[{s_conf['org_name'] or 'Tedarikçi Denetim'}] Hatırlatma: "
                                  f"{len(items)} geciken kayıt", text, f"<p style='white-space:pre-wrap'>{_h.escape(text)}</p>")
                db.execute("INSERT INTO reminders_sent (supplier_id, items, sent_at) VALUES (?,?,?)", (sid, len(items), ts))
                sent += 1
            except Exception:
                pass
        # 2) iç uyarılar: sertifika, sözleşme ihbar tarihi, gecikmiş denetim
        for e in rows(db.execute("SELECT e.id, e.supplier_id, e.title, e.original_name, e.expires_at, s.name FROM evidence_files e JOIN suppliers s ON "
                                 "s.id=e.supplier_id WHERE e.kind='sertifika' AND e.expires_at IS NOT NULL").fetchall()):
            days = (dt.date.fromisoformat(e["expires_at"]) - t).days
            for lim, sev in ((0, "yüksek"), (7, "orta"), (30, "bilgi")):
                if days <= lim:
                    msg = "süresi doldu" if days < 0 else f"{days} gün içinde bitiyor"
                    if add_alert(db, e["supplier_id"], "sertifika", sev, f"{e['name']}: {e['title'] or e['original_name']} {msg}", None, "evidence",
                                 e["id"], dedupe=f"cert|{e['id']}|{lim}"):
                        alerts += 1
                    break
        from .lifecycle import contract_status
        for c in rows(db.execute("SELECT c.*, s.name AS sname FROM contracts c JOIN suppliers s ON s.id=c.supplier_id WHERE c.end_date IS NOT NULL").fetchall()):
            st = contract_status(c, t)
            if st["status"] in ("bitiyor", "otomatik yenilenecek", "süresi doldu"):
                if add_alert(db, c["supplier_id"], "sözleşme", "orta" if st["status"] != "süresi doldu" else "yüksek",
                             f"{c['sname']}: '{c['title']}' {st['status']} ({c['end_date']})",
                             f"İhbar son tarihi {st['notice_deadline']}" if st["notice_deadline"] else None, "contract", c["id"],
                             dedupe=f"contract|{c['id']}|{st['status']}|{c['end_date']}"):
                    alerts += 1
        for a in rows(db.execute("SELECT a.id, a.supplier_id, a.audit_date, s.name FROM audits a JOIN suppliers s ON s.id=a.supplier_id "
                                 "WHERE a.respondent='denetçi' AND a.status IN ('planlandı','devam ediyor') AND a.audit_date < ?", (ts,)).fetchall()):
            if add_alert(db, a["supplier_id"], "gecikmiş denetim", "orta", f"{a['name']}: planlı denetim tarihi geçti ({a['audit_date']})",
                         None, "audit", a["id"]):
                alerts += 1
    return {"emails": sent, "alerts": alerts}


def job_waivers() -> dict:
    from .exceptions import expire_waivers
    with get_db() as db:
        return {"expired": expire_waivers(db)}


async def run_all(force: bool = False) -> dict:
    """Günlük görevleri çalıştırır (force=False iken bugün çalışmış olanları atlar)."""
    out = {}
    today = dt.date.today().isoformat()
    with get_db() as db:
        conf = get_conf(db)
        last = {j: _last_run(db, j) for j in ("monitor", "reassess", "reminders", "waivers")}
    plan = [("waivers", True, job_waivers), ("reassess", conf["reassess_enabled"], job_reassess),
            ("reminders", conf["reminders_enabled"], job_reminders), ("monitor", conf["monitor_enabled"], None)]
    for name, enabled, fn in plan:
        if not enabled or (not force and last[name] and last[name][:10] == today):
            continue
        try:
            res = (await job_monitor(force)) if name == "monitor" else fn()
        except Exception as e:  # bir görevin hatası diğerlerini durdurmasın
            res = {"error": f"{type(e).__name__}: {e}"[:300]}
        with get_db() as db:
            _mark(db, name, res)
        out[name] = res
    if out:
        from . import events as ev
        try:
            if ev.has_pending():
                await ev.deliver_pending()
        except Exception:
            pass
    return out


_task: Optional[asyncio.Task] = None


async def _loop():
    await asyncio.sleep(20)  # açılışı yavaşlatmasın
    while True:
        try:
            await run_all()
        except Exception:
            pass
        await asyncio.sleep(3600)


def start() -> None:
    global _task
    if os.environ.get("TD_SCHEDULER", "1") == "0" or _task is not None:
        return
    _task = asyncio.get_event_loop().create_task(_loop())


def stop() -> None:
    global _task
    if _task:
        _task.cancel()
        _task = None


# ------------------------------------------------------------------ API
@router.get("/api/alerts")
def list_alerts(unread: bool = False, limit: int = 200):
    w, p = scoped("a.supplier_id")
    q = "SELECT a.*, s.name AS supplier_name FROM alerts a LEFT JOIN suppliers s ON s.id=a.supplier_id WHERE 1=1" + w
    if unread:
        q += " AND a.read_at IS NULL"
    with get_db() as db:
        items = rows(db.execute(q + " ORDER BY a.id DESC LIMIT ?", [*p, min(limit, 1000)]).fetchall())
        n = db.execute("SELECT COUNT(*) FROM alerts a WHERE read_at IS NULL" + w, p).fetchone()[0]
    return {"unread": n, "items": items}


@router.post("/api/alerts/{aid}/read")
def read_alert(aid: int):
    with get_db() as db:
        db.execute("UPDATE alerts SET read_at=datetime('now') WHERE id=? AND read_at IS NULL", (aid,))
    return {"ok": True}


@router.post("/api/alerts/read-all")
def read_all():
    with get_db() as db:
        n = db.execute("UPDATE alerts SET read_at=datetime('now') WHERE read_at IS NULL").rowcount
    return {"marked": n}


class ConfIn(BaseModel):
    monitor_enabled: bool = True
    monitor_interval_hours: int = Field(default=24, ge=1, le=720)
    reassess_enabled: bool = True
    reassess_lead_days: int = Field(default=30, ge=0, le=365)
    reminders_enabled: bool = True
    reminder_every_days: int = Field(default=7, ge=1, le=90)


@router.get("/api/scheduler")
def scheduler_status():
    with get_db() as db:
        jobs = {}
        for j in ("monitor", "reassess", "reminders", "waivers"):
            r = db.execute("SELECT value FROM settings WHERE key=?", (f"job_result_{j}",)).fetchone()
            jobs[j] = {"last_run": _last_run(db, j), "result": json.loads(r[0]) if r else None}
        return {"config": get_conf(db), "jobs": jobs, "running": _task is not None}


@router.put("/api/scheduler")
def scheduler_config(body: ConfIn):
    with get_db() as db:
        db.execute("INSERT INTO settings (key, value) VALUES ('scheduler', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (json.dumps(body.model_dump()),))
        auth.log("settings.scheduler", "settings", None, body.model_dump(), db=db)
    return scheduler_status()


@router.post("/api/scheduler/run")
async def scheduler_run(force: bool = True):
    if force is not True and force is not False:
        raise HTTPException(422, "force true/false olmalı")
    res = await run_all(force=force)
    auth.log("scheduler.run", "settings", None, res)
    return {"ran": res, **scheduler_status()}
