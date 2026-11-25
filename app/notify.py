"""Tedarikçiye bildirim gönderme ve tedarikçiden yanıt alma.

Kanallar
  * email   — Ayarlar sayfasındaki SMTP sunucusu üzerinden
  * webhook — tedarikçinin sistemine (ticket, SIEM, GRC vb.) imzalı JSON POST
  * manuel  — kullanıcının kendi e-posta programıyla gönderdiği bildirim (yalnızca kayıt + yanıt linki)

Tedarikçi yanıtı
  * Yanıt sayfası:  GET  /yanit/{token}
  * Yanıt API'si:   GET/POST /api/public/respond/{token}   (tedarikçinin sistemi doğrudan çağırabilir)

Webhook imzası
  X-TD-Signature: sha256=<HMAC-SHA256(secret, "<X-TD-Timestamp>.<gövde>")>
"""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import hmac
import html
import json
import re
import secrets
import smtplib
import ssl
import uuid
from email.message import EmailMessage
from email.utils import formataddr, getaddresses, make_msgid
from typing import Literal, Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .db import get_db, row, rows

router = APIRouter()

SETTINGS_DEFAULTS = {
    "org_name": "",
    "public_base_url": "",
    "smtp_host": "",
    "smtp_port": "587",
    "smtp_security": "starttls",  # starttls / ssl / none
    "smtp_user": "",
    "smtp_password": "",
    "smtp_from": "",
    "reply_to": "",
}
SECRET_KEYS = {"smtp_password"}
RESPONSE_STATUSES = ["okundu", "aksiyon planlandı", "giderildi", "itiraz"]
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


# ============================================================ Ayarlar
def get_settings(db) -> dict:
    s = dict(SETTINGS_DEFAULTS)
    s.update({r["key"]: r["value"] for r in db.execute("SELECT key, value FROM settings")})
    return s


def smtp_ready(s: dict) -> bool:
    return bool(s["smtp_host"] and (s["smtp_from"] or s["smtp_user"]))


class SettingsIn(BaseModel):
    org_name: Optional[str] = None
    public_base_url: Optional[str] = None
    smtp_host: Optional[str] = None
    smtp_port: Optional[str] = None
    smtp_security: Optional[Literal["starttls", "ssl", "none"]] = None
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None   # boş bırakılırsa mevcut parola korunur
    smtp_from: Optional[str] = None
    reply_to: Optional[str] = None


def _public_settings(s: dict) -> dict:
    out = {k: v for k, v in s.items() if k not in SECRET_KEYS}
    out["smtp_password_set"] = bool(s.get("smtp_password"))
    out["smtp_ready"] = smtp_ready(s)
    return out


@router.get("/api/settings")
def read_settings():
    with get_db() as db:
        return _public_settings(get_settings(db))


@router.put("/api/settings")
def write_settings(body: SettingsIn):
    d = body.model_dump(exclude_unset=True)
    if d.get("smtp_password") in (None, ""):
        d.pop("smtp_password", None)
    if d.get("public_base_url"):
        u = d["public_base_url"].strip().rstrip("/")
        if not re.match(r"^https?://", u):
            raise HTTPException(422, "Genel adres http:// veya https:// ile başlamalı.")
        d["public_base_url"] = u
    if d.get("smtp_port") and not str(d["smtp_port"]).isdigit():
        raise HTTPException(422, "SMTP portu sayı olmalı.")
    with get_db() as db:
        for k, v in d.items():
            db.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (k, (v or "").strip()))
        return _public_settings(get_settings(db))


class TestEmailIn(BaseModel):
    to: str


@router.post("/api/settings/test-email")
async def test_email(body: TestEmailIn):
    with get_db() as db:
        s = get_settings(db)
    if not smtp_ready(s):
        raise HTTPException(400, "Önce SMTP sunucusu ve gönderen adresini kaydedin.")
    try:
        await asyncio.to_thread(send_email, s, parse_emails(body.to), "Tedarikçi Denetim — test e-postası",
                                "SMTP ayarlarınız çalışıyor.", "<p>SMTP ayarlarınız çalışıyor.</p>")
    except Exception as e:
        raise HTTPException(502, f"E-posta gönderilemedi: {type(e).__name__}: {e}")
    return {"ok": True}


# ============================================================ İçerik
def parse_emails(value: str | None) -> list[str]:
    if not value:
        return []
    addrs = [a.strip() for _, a in getaddresses([value.replace(";", ",")]) if a.strip()]
    bad = [a for a in addrs if not EMAIL_RE.match(a)]
    if bad:
        raise HTTPException(422, f"Geçersiz e-posta adresi: {', '.join(bad)}")
    return list(dict.fromkeys(addrs))


def base_url(s: dict, request: Request | None) -> str:
    if s.get("public_base_url"):
        return s["public_base_url"].rstrip("/")
    if request is not None:
        return str(request.base_url).rstrip("/")
    return "http://localhost:8000"


def _finding_public(f: dict) -> dict:
    return {k: f.get(k) for k in ("id", "title", "severity", "description", "action", "due_date", "status", "source",
                                  "supplier_status", "supplier_comment", "supplier_due_date")}


def _scan_public(scan: dict | None) -> dict | None:
    if not scan:
        return None
    r = scan["result"]
    return {
        "domain": r["domain"], "scanned_at": r["scanned_at"], "score": r["score"], "grade": r["grade"],
        "summary": r["summary"],
        "issues": [{"check": c["title"], "category": c["category"], "status": c["status"], "detail": c["detail"],
                    "recommendation": c["recommendation"]}
                   for c in r["checks"] if c["status"] in ("fail", "warn")],
    }


def build_payload(*, event: str, delivery_id: str, org: str, supplier: dict, findings: list[dict],
                  scan: dict | None, note: str | None, token: str, base: str) -> dict:
    return {
        "event": event,
        "delivery_id": delivery_id,
        "sent_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "from": org or None,
        "supplier": {"name": supplier["name"]},
        "message": note or None,
        "findings": [_finding_public(f) for f in findings],
        "scan": _scan_public(scan),
        "response": {
            "url": f"{base}/yanit/{token}",
            "api": f"{base}/api/public/respond/{token}",
            "method": "POST",
            "statuses": RESPONSE_STATUSES,
            "example": {"finding_id": findings[0]["id"] if findings else None, "status": "aksiyon planlandı",
                        "comment": "Kök neden analizi yapıldı, düzeltme planlandı.", "planned_date": "2026-12-31",
                        "responder": "Ad Soyad"},
        },
    }


SEV_TR = {"kritik": "KRİTİK", "yüksek": "YÜKSEK", "orta": "ORTA", "düşük": "DÜŞÜK"}
SEV_COLOR = {"kritik": "#7a1010", "yüksek": "#b42318", "orta": "#9a6400", "düşük": "#475467"}


def render_email(p: dict) -> tuple[str, str, str]:
    org = p["from"] or "Müşteriniz"
    findings, scan = p["findings"], p["scan"]
    if scan and not findings:
        subject = f"[{org}] {scan['domain']} dış güvenlik değerlendirmesi: not {scan['grade'] or '-'}"
    else:
        subject = f"[{org}] {p['supplier']['name']} için {len(findings)} bulgu bildirimi"
    lines = [f"Sayın {p['supplier']['name']} yetkilisi,", "",
             f"{org} tarafından yürütülen tedarikçi değerlendirmesi kapsamında aşağıdaki konular bilginize sunulmuştur."]
    if p["message"]:
        lines += ["", p["message"]]
    if scan:
        lines += ["", f"DIŞ GÜVENLİK DEĞERLENDİRMESİ — {scan['domain']}",
                  f"Not: {scan['grade'] or '-'}  Puan: {scan['score'] if scan['score'] is not None else '-'}/100  ({scan['scanned_at'][:10]})"]
        for i in scan["issues"][:25]:
            lines.append(f"  - [{'Başarısız' if i['status'] == 'fail' else 'Uyarı'}] {i['check']}: {i['detail']}")
            if i["recommendation"]:
                lines.append(f"      Öneri: {i['recommendation']}")
    if findings:
        lines += ["", "BULGULAR"]
        for n, f in enumerate(findings, 1):
            lines.append(f"{n}. [{SEV_TR.get(f['severity'], f['severity'])}] {f['title']}")
            if f["description"]:
                lines.append(f"   Açıklama: {f['description']}")
            if f["action"]:
                lines.append(f"   Beklenen aksiyon: {f['action']}")
            if f["due_date"]:
                lines.append(f"   Termin: {f['due_date']}")
    lines += ["", "Lütfen aşağıdaki bağlantıdan her bulgu için durumunuzu (okundu / aksiyon planlandı / giderildi / itiraz) iletin:",
              p["response"]["url"], "",
              "Sistemleriniz üzerinden otomatik yanıt vermek için API:",
              f"POST {p['response']['api']}  (JSON: {json.dumps(p['response']['example'], ensure_ascii=False)})",
              "", "Saygılarımızla,", org]
    text = "\n".join(lines)

    e = html.escape
    parts = ["<div style='font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#1d2330;max-width:720px'>",
             f"<p>Sayın <b>{e(p['supplier']['name'])}</b> yetkilisi,</p>",
             f"<p>{e(org)} tarafından yürütülen tedarikçi değerlendirmesi kapsamında aşağıdaki konular bilginize sunulmuştur.</p>"]
    if p["message"]:
        parts.append(f"<p style='white-space:pre-wrap;background:#f5f6f8;padding:10px;border-radius:6px'>{e(p['message'])}</p>")
    if scan:
        parts.append(f"<h3 style='margin-bottom:4px'>Dış güvenlik değerlendirmesi — {e(scan['domain'])}</h3>"
                     f"<p style='margin-top:0'>Not: <b>{e(str(scan['grade'] or '-'))}</b> · Puan: <b>{e(str(scan['score']))}</b>/100 · {e(scan['scanned_at'][:10])}</p><ul>")
        for i in scan["issues"][:25]:
            col = "#b42318" if i["status"] == "fail" else "#9a6400"
            parts.append(f"<li><b style='color:{col}'>{'Başarısız' if i['status'] == 'fail' else 'Uyarı'}</b> — {e(i['check'])}: {e(i['detail'])}"
                         + (f"<br><span style='color:#2952cc'>Öneri: {e(i['recommendation'])}</span>" if i["recommendation"] else "") + "</li>")
        parts.append("</ul>")
    if findings:
        parts.append("<h3>Bulgular</h3><table cellpadding='6' style='border-collapse:collapse;width:100%'>"
                     "<tr style='background:#f5f6f8;text-align:left'><th>Önem</th><th>Bulgu</th><th>Termin</th></tr>")
        for f in findings:
            parts.append(
                f"<tr style='border-top:1px solid #e2e5ea;vertical-align:top'><td><b style='color:{SEV_COLOR.get(f['severity'], '#475467')}'>"
                f"{e(SEV_TR.get(f['severity'], f['severity']))}</b></td><td><b>{e(f['title'])}</b>"
                + (f"<br><span style='color:#6b7280'>{e(f['description'])}</span>" if f["description"] else "")
                + (f"<br>Beklenen aksiyon: {e(f['action'])}" if f["action"] else "")
                + f"</td><td>{e(f['due_date'] or '-')}</td></tr>")
        parts.append("</table>")
    parts.append(f"<p style='margin:24px 0'><a href='{e(p['response']['url'])}' style='background:#2952cc;color:#fff;padding:10px 18px;"
                 f"border-radius:6px;text-decoration:none'>Yanıt ver</a></p>"
                 f"<p style='font-size:12px;color:#6b7280'>Sistemleriniz üzerinden otomatik yanıt için: POST {e(p['response']['api'])}</p>"
                 f"<p>Saygılarımızla,<br>{e(org)}</p></div>")
    return subject, text, "".join(parts)


# ============================================================ Gönderim
def send_email(s: dict, to: list[str], subject: str, text: str, html_body: str) -> None:
    if not to:
        raise ValueError("Alıcı yok")
    msg = EmailMessage()
    sender = s["smtp_from"] or s["smtp_user"]
    msg["From"] = formataddr((s["org_name"], sender)) if s["org_name"] else sender
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid()
    if s.get("reply_to"):
        msg["Reply-To"] = s["reply_to"]
    msg.set_content(text)
    msg.add_alternative(html_body, subtype="html")
    port = int(s["smtp_port"] or 587)
    ctx = ssl.create_default_context()
    if s["smtp_security"] == "ssl":
        server = smtplib.SMTP_SSL(s["smtp_host"], port, timeout=20, context=ctx)
    else:
        server = smtplib.SMTP(s["smtp_host"], port, timeout=20)
    with server:
        if s["smtp_security"] == "starttls":
            server.starttls(context=ctx)
        if s["smtp_user"]:
            server.login(s["smtp_user"], s["smtp_password"])
        server.send_message(msg)


def sign(secret: str, timestamp: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


async def send_webhook(url: str, secret: str | None, payload: dict) -> int:
    body = json.dumps(payload, ensure_ascii=False).encode()
    ts = str(int(dt.datetime.now(dt.timezone.utc).timestamp()))
    headers = {"Content-Type": "application/json; charset=utf-8", "User-Agent": "TedarikciDenetim-Webhook/1.0",
               "X-TD-Event": payload["event"], "X-TD-Delivery": payload["delivery_id"], "X-TD-Timestamp": ts}
    if secret:
        headers["X-TD-Signature"] = sign(secret, ts, body)
    async with httpx.AsyncClient(timeout=15, follow_redirects=False) as c:
        r = await c.post(url, content=body, headers=headers)
    if r.status_code >= 300:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
    return r.status_code


# ============================================================ Gönderim akışı
def _load(db, supplier_id: int, finding_ids: list[int], scan_id: int | None):
    sup = row(db.execute("SELECT * FROM suppliers WHERE id=?", (supplier_id,)).fetchone())
    if not sup:
        raise HTTPException(404, "Tedarikçi bulunamadı")
    findings = []
    if finding_ids:
        q = f"SELECT * FROM findings WHERE supplier_id=? AND id IN ({','.join('?' * len(finding_ids))}) ORDER BY " \
            "CASE severity WHEN 'kritik' THEN 0 WHEN 'yüksek' THEN 1 WHEN 'orta' THEN 2 ELSE 3 END, id"
        findings = rows(db.execute(q, [supplier_id, *finding_ids]).fetchall())
        if len(findings) != len(set(finding_ids)):
            raise HTTPException(422, "Seçilen bulguların bir kısmı bu tedarikçiye ait değil veya bulunamadı.")
    scan = None
    if scan_id:
        scan = row(db.execute("SELECT * FROM intel_scans WHERE id=? AND supplier_id=?", (scan_id, supplier_id)).fetchone())
        if not scan:
            raise HTTPException(404, "Tarama bulunamadı")
    return sup, findings, scan


def available_channels(sup: dict, s: dict) -> list[str]:
    ch = []
    if sup.get("notify_emails") and smtp_ready(s):
        ch.append("email")
    if sup.get("webhook_url"):
        ch.append("webhook")
    return ch


async def dispatch(supplier_id: int, finding_ids: list[int], scan_id: int | None, channels: list[str] | None,
                   note: str | None = None, auto: bool = False, request: Request | None = None,
                   to_override: str | None = None) -> list[dict]:
    """Bildirimi oluşturur, seçilen kanallardan gönderir ve kaydeder. channels=None -> yapılandırılmış tüm kanallar."""
    with get_db() as db:
        s = get_settings(db)
        sup, findings, scan = _load(db, supplier_id, finding_ids, scan_id)
    if not findings and not scan:
        raise HTTPException(422, "Bildirilecek bulgu veya tarama seçilmedi.")
    if channels is None:
        channels = available_channels(sup, s)
    base = base_url(s, request)
    event = "intel.notification" if scan and not findings else "finding.notification"
    kind = "istihbarat" if scan else "bulgu"
    results = []
    for ch in dict.fromkeys(channels):
        token = secrets.token_urlsafe(24)
        delivery = str(uuid.uuid4())
        payload = build_payload(event=event, delivery_id=delivery, org=s["org_name"], supplier=sup, findings=findings,
                                scan=scan, note=note, token=token, base=base)
        subject, text, html_body = render_email(payload)
        if ch == "email":
            target = ", ".join(parse_emails(to_override or sup.get("notify_emails")))
        elif ch == "webhook":
            target = sup.get("webhook_url") or ""
        elif ch == "manuel":
            target = ", ".join(parse_emails(to_override or sup.get("notify_emails"))) or "(manuel)"
        else:
            raise HTTPException(422, f"Bilinmeyen kanal: {ch}")
        status, error = "beklemede", None
        if ch == "email" and not smtp_ready(s):
            status, error = "başarısız", "SMTP ayarları yapılmamış (Ayarlar sayfası)."
        elif not target:
            status, error = "başarısız", ("Tedarikçi için bildirim e-postası tanımlı değil." if ch == "email"
                                          else "Tedarikçi için webhook adresi tanımlı değil.")
        stored = json.dumps(payload, ensure_ascii=False) if ch == "webhook" else text
        with get_db() as db:
            nid = db.execute(
                "INSERT INTO notifications (supplier_id, scan_id, kind, channel, target, subject, payload, status, error, token, auto) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (supplier_id, scan_id, kind, ch, target, subject, stored, status, error, token, int(auto))).lastrowid
            db.executemany("INSERT INTO notification_findings (notification_id, finding_id) VALUES (?,?)",
                           [(nid, f["id"]) for f in findings])
        if status == "beklemede":
            await _deliver(nid, s, sup, payload, subject, text, html_body)
        results.append(get_notification(nid) | ({"mailto_body": text} if ch == "manuel" else {}))
    return results


async def _deliver(nid: int, s: dict, sup: dict, payload: dict, subject: str, text: str, html_body: str) -> None:
    with get_db() as db:
        n = row(db.execute("SELECT * FROM notifications WHERE id=?", (nid,)).fetchone())
    status, error, code = "gönderildi", None, None
    try:
        if n["channel"] == "email":
            await asyncio.to_thread(send_email, s, parse_emails(n["target"]), subject, text, html_body)
        elif n["channel"] == "webhook":
            code = await send_webhook(n["target"], sup.get("webhook_secret"), payload)
        elif n["channel"] == "manuel":
            status = "manuel"
    except HTTPException as e:
        status, error = "başarısız", e.detail
    except Exception as e:
        status, error = "başarısız", f"{type(e).__name__}: {e}"[:500]
        m = re.match(r"HTTP (\d+)", str(e))
        code = int(m.group(1)) if m else None
    with get_db() as db:
        db.execute("UPDATE notifications SET status=?, error=?, response_code=?, attempts=attempts+1, sent_at=? WHERE id=?",
                   (status, error, code, now() if status != "başarısız" else None, nid))
        if status != "başarısız":
            db.execute("UPDATE findings SET notified_at=?, supplier_status=COALESCE(supplier_status, 'bildirildi') "
                       "WHERE id IN (SELECT finding_id FROM notification_findings WHERE notification_id=?)", (now(), nid))


def get_notification(nid: int) -> dict:
    with get_db() as db:
        n = row(db.execute(
            "SELECT n.*, s.name AS supplier_name FROM notifications n JOIN suppliers s ON s.id=n.supplier_id WHERE n.id=?",
            (nid,)).fetchone())
        if not n:
            raise HTTPException(404, "Bildirim bulunamadı")
        n["finding_ids"] = [r[0] for r in db.execute(
            "SELECT finding_id FROM notification_findings WHERE notification_id=?", (nid,))]
        n["responses"] = rows(db.execute(
            "SELECT r.*, f.title AS finding_title FROM supplier_responses r LEFT JOIN findings f ON f.id=r.finding_id "
            "WHERE notification_id=? ORDER BY r.id DESC", (nid,)).fetchall())
        n.pop("payload", None)
        return n


async def auto_notify(supplier_id: int, finding_ids: list[int], scan_id: int | None = None) -> list[dict]:
    """Tedarikçide otomatik bildirim açıksa yeni bulguları bildirir. Hata olursa sessizce kaydeder."""
    if not finding_ids:
        return []
    with get_db() as db:
        sup = row(db.execute("SELECT * FROM suppliers WHERE id=?", (supplier_id,)).fetchone())
        s = get_settings(db)
    if not sup or not sup.get("auto_notify") or not available_channels(sup, s):
        return []
    return await dispatch(supplier_id, finding_ids, scan_id, None, auto=True)


# ============================================================ API
class NotifyIn(BaseModel):
    supplier_id: int
    finding_ids: list[int] = []
    scan_id: Optional[int] = None
    channels: list[Literal["email", "webhook", "manuel"]] = Field(min_length=1)
    note: Optional[str] = None
    to: Optional[str] = None  # e-posta alıcısını bu gönderim için değiştir


class PreviewIn(BaseModel):
    supplier_id: int
    finding_ids: list[int] = []
    scan_id: Optional[int] = None
    note: Optional[str] = None


@router.post("/api/notifications/preview")
def preview(body: PreviewIn, request: Request):
    with get_db() as db:
        s = get_settings(db)
        sup, findings, scan = _load(db, body.supplier_id, body.finding_ids, body.scan_id)
    p = build_payload(event="finding.notification" if findings else "intel.notification", delivery_id="önizleme",
                      org=s["org_name"], supplier=sup, findings=findings, scan=scan, note=body.note,
                      token="<gönderimde-oluşturulur>", base=base_url(s, request))
    subject, text, html_body = render_email(p)
    base = base_url(s, request)
    return {
        "subject": subject, "text": text, "html": html_body, "payload": p,
        "recipients": {"email": sup.get("notify_emails") or "", "webhook": sup.get("webhook_url") or ""},
        "smtp_ready": smtp_ready(s), "webhook_signed": bool(sup.get("webhook_secret")),
        "base_url": base, "base_is_local": bool(re.match(r"^https?://(localhost|127\.|0\.0\.0\.0|\[::1\])", base)),
    }


@router.post("/api/notifications", status_code=201)
async def notify(body: NotifyIn, request: Request):
    return await dispatch(body.supplier_id, body.finding_ids, body.scan_id, body.channels, body.note,
                          request=request, to_override=body.to)


@router.get("/api/notifications")
def list_notifications(supplier_id: Optional[int] = None, finding_id: Optional[int] = None, limit: int = 200):
    q = "SELECT n.id FROM notifications n WHERE 1=1"
    p: list = []
    if supplier_id:
        q += " AND n.supplier_id=?"; p.append(supplier_id)
    if finding_id:
        q += " AND n.id IN (SELECT notification_id FROM notification_findings WHERE finding_id=?)"; p.append(finding_id)
    q += " ORDER BY n.id DESC LIMIT ?"; p.append(min(limit, 1000))
    with get_db() as db:
        ids = [r[0] for r in db.execute(q, p)]
    return [get_notification(i) for i in ids]


@router.post("/api/notifications/{nid}/retry")
async def retry(nid: int, request: Request):
    with get_db() as db:
        n = row(db.execute("SELECT * FROM notifications WHERE id=?", (nid,)).fetchone())
        if not n:
            raise HTTPException(404, "Bildirim bulunamadı")
        s = get_settings(db)
        sup = row(db.execute("SELECT * FROM suppliers WHERE id=?", (n["supplier_id"],)).fetchone())
        fids = [r[0] for r in db.execute("SELECT finding_id FROM notification_findings WHERE notification_id=?", (nid,))]
    if n["status"] != "başarısız":
        raise HTTPException(409, "Yalnızca başarısız bildirimler yeniden denenebilir.")
    # Güncel tedarikçi ayarlarıyla, aynı token kullanılarak yeniden gönder
    with get_db() as db:
        _, findings, scan = _load(db, n["supplier_id"], fids, n["scan_id"])
        target = (", ".join(parse_emails(sup.get("notify_emails"))) if n["channel"] == "email"
                  else sup.get("webhook_url") if n["channel"] == "webhook" else n["target"])
        db.execute("UPDATE notifications SET target=? WHERE id=?", (target or n["target"], nid))
    if n["channel"] == "email" and not smtp_ready(s):
        raise HTTPException(400, "SMTP ayarları yapılmamış (Ayarlar sayfası).")
    if not target:
        raise HTTPException(400, "Tedarikçinin bu kanal için adresi tanımlı değil.")
    note = None
    payload = build_payload(event="intel.notification" if scan and not findings else "finding.notification",
                            delivery_id=str(uuid.uuid4()), org=s["org_name"], supplier=sup, findings=findings,
                            scan=scan, note=note, token=n["token"], base=base_url(s, request))
    subject, text, html_body = render_email(payload)
    await _deliver(nid, s, sup, payload, subject, text, html_body)
    return get_notification(nid)


@router.post("/api/suppliers/{sid}/webhook-test")
async def webhook_test(sid: int):
    with get_db() as db:
        sup = row(db.execute("SELECT * FROM suppliers WHERE id=?", (sid,)).fetchone())
        s = get_settings(db)
    if not sup:
        raise HTTPException(404, "Tedarikçi bulunamadı")
    if not sup.get("webhook_url"):
        raise HTTPException(400, "Webhook adresi tanımlı değil.")
    payload = {"event": "test", "delivery_id": str(uuid.uuid4()), "sent_at": now(), "from": s["org_name"] or None,
               "supplier": {"name": sup["name"]}, "message": "Bu bir test bildirimidir."}
    try:
        code = await send_webhook(sup["webhook_url"], sup.get("webhook_secret"), payload)
    except Exception as e:
        raise HTTPException(502, f"Webhook başarısız: {type(e).__name__}: {e}"[:400])
    return {"ok": True, "status_code": code}


# ---------------------------------------------------------------- Tedarikçinin kullandığı uçlar (token ile)
class ResponseIn(BaseModel):
    finding_id: Optional[int] = None       # boşsa bildirimdeki tüm bulgulara uygulanır
    status: Literal["okundu", "aksiyon planlandı", "giderildi", "itiraz"]
    comment: Optional[str] = Field(default=None, max_length=5000)
    planned_date: Optional[str] = None
    responder: Optional[str] = Field(default=None, max_length=200)


def _by_token(db, token: str) -> dict:
    if not token or len(token) < 20:
        raise HTTPException(404, "Geçersiz bağlantı")
    n = row(db.execute("SELECT * FROM notifications WHERE token=?", (token,)).fetchone())
    if not n:
        raise HTTPException(404, "Geçersiz veya süresi dolmuş bağlantı")
    return n


@router.get("/api/public/respond/{token}")
def public_view(token: str):
    with get_db() as db:
        n = _by_token(db, token)
        s = get_settings(db)
        sup = row(db.execute("SELECT name FROM suppliers WHERE id=?", (n["supplier_id"],)).fetchone())
        findings = rows(db.execute(
            "SELECT f.* FROM findings f JOIN notification_findings nf ON nf.finding_id=f.id WHERE nf.notification_id=? "
            "ORDER BY CASE f.severity WHEN 'kritik' THEN 0 WHEN 'yüksek' THEN 1 WHEN 'orta' THEN 2 ELSE 3 END",
            (n["id"],)).fetchall())
        scan = row(db.execute("SELECT * FROM intel_scans WHERE id=?", (n["scan_id"],)).fetchone()) if n["scan_id"] else None
        responses = rows(db.execute(
            "SELECT finding_id, status, comment, planned_date, responder, created_at FROM supplier_responses "
            "WHERE notification_id=? ORDER BY id DESC", (n["id"],)).fetchall())
        if not n["viewed_at"]:
            db.execute("UPDATE notifications SET viewed_at=? WHERE id=?", (now(), n["id"]))
    return {
        "from": s["org_name"] or None, "supplier": sup["name"], "subject": n["subject"], "sent_at": n["sent_at"] or n["created_at"],
        "findings": [_finding_public(f) for f in findings], "scan": _scan_public(scan),
        "responses": responses, "statuses": RESPONSE_STATUSES,
    }


@router.post("/api/public/respond/{token}", status_code=201)
def public_respond(token: str, body: ResponseIn, request: Request):
    if body.planned_date and not re.match(r"^\d{4}-\d{2}-\d{2}$", body.planned_date):
        raise HTTPException(422, "planned_date YYYY-AA-GG biçiminde olmalı")
    via = "sayfa" if request.headers.get("x-td-client") == "yanit-sayfasi" else "api"
    with get_db() as db:
        n = _by_token(db, token)
        allowed = [r[0] for r in db.execute("SELECT finding_id FROM notification_findings WHERE notification_id=?", (n["id"],))]
        if body.finding_id is not None and body.finding_id not in allowed:
            raise HTTPException(422, "Bu bulgu bu bildirime ait değil.")
        targets = [body.finding_id] if body.finding_id is not None else (allowed or [None])
        for fid in targets:
            db.execute(
                "INSERT INTO supplier_responses (notification_id, finding_id, status, comment, planned_date, responder, via) "
                "VALUES (?,?,?,?,?,?,?)", (n["id"], fid, body.status, body.comment, body.planned_date, body.responder, via))
            if fid is not None:
                db.execute(
                    "UPDATE findings SET supplier_status=?, supplier_comment=?, supplier_due_date=COALESCE(?, supplier_due_date), "
                    "supplier_updated_at=?, status=CASE WHEN status='açık' AND ? IN ('aksiyon planlandı','giderildi') "
                    "THEN 'devam ediyor' ELSE status END WHERE id=?",
                    (body.status, body.comment, body.planned_date, now(), body.status, fid))
    return {"ok": True, "applied_to": [t for t in targets if t is not None]}
