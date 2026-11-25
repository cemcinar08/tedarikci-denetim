"""Olay servisi: durum değişimlerini kaydeder ve abone webhook'lara (iç/dış sistemler) imzalı olarak iletir.

Olay türleri: finding.created, finding.status_changed, capa.created, capa.submitted, capa.approved,
              capa.rejected, audit.completed   (EVENT_TYPES)

Akış: servis `emit(...)` çağırır (çağıranın DB işlemi içinde) -> events + webhook_deliveries satırları yazılır ->
istek bittikten sonra (işlem commit edilmişken) AuthMiddleware `deliver_pending()` ile gönderir.
Başarısız teslimatlar en fazla MAX_ATTEMPTS kez otomatik, sonrasında elle yeniden denenir.

İmza: X-TD-Signature: sha256=HMAC-SHA256(secret, "<X-TD-Timestamp>.<gövde>")  (notify.sign ile aynı)
"""
from __future__ import annotations

import datetime as dt
import json
import re
from typing import Literal, Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from .db import get_db, row, rows

router = APIRouter()
EVENT_TYPES = ["finding.created", "finding.status_changed", "capa.created", "capa.submitted", "capa.approved",
               "capa.rejected", "capa.commented", "audit.completed", "assessment.sent", "assessment.submitted",
               "alert.created"]
MAX_ATTEMPTS = 3


def matches(sub_events: list[str], sub_supplier: Optional[int], event_type: str, supplier_id: Optional[int]) -> bool:
    """Abonelik bu olayı almalı mı? (saf fonksiyon)"""
    type_ok = "*" in sub_events or event_type in sub_events or f"{event_type.split('.')[0]}.*" in sub_events
    return type_ok and (sub_supplier is None or sub_supplier == supplier_id)


def emit(event_type: str, supplier_id: Optional[int], entity: str, entity_id, data: dict, db) -> int:
    """Olayı kaydet ve eşleşen aboneliklere teslimat oluştur. Gönderim istek sonunda yapılır."""
    assert event_type in EVENT_TYPES, event_type
    payload = {"event": event_type, "entity": entity, "entity_id": entity_id, "data": data}
    eid = db.execute("INSERT INTO events (type, supplier_id, entity, entity_id, payload) VALUES (?,?,?,?,?)",
                     (event_type, supplier_id, entity, str(entity_id), json.dumps(payload, ensure_ascii=False))).lastrowid
    for s in db.execute("SELECT id, events, supplier_id FROM webhook_subscriptions WHERE active=1").fetchall():
        if matches(json.loads(s["events"] or '["*"]'), s["supplier_id"], event_type, supplier_id):
            db.execute("INSERT INTO webhook_deliveries (subscription_id, event_id) VALUES (?,?)", (s["id"], eid))
    return eid


def has_pending() -> bool:
    with get_db() as db:
        return db.execute("SELECT 1 FROM webhook_deliveries WHERE status='beklemede' OR (status='başarısız' AND attempts < ?) "
                          "LIMIT 1", (MAX_ATTEMPTS,)).fetchone() is not None


def _body(ev: dict, supplier_name: str | None) -> dict:
    p = json.loads(ev["payload"])
    return {**p, "id": ev["id"], "occurred_at": ev["created_at"] + "Z",
            "supplier": {"id": ev["supplier_id"], "name": supplier_name} if ev["supplier_id"] else None}


# ------------------------------------------------------------------ Slack / Teams mesaj biçimleri (saf)
EVENT_TITLES = {
    "finding.created": "Yeni bulgu", "finding.status_changed": "Bulgu durumu değişti", "capa.created": "CAPA açıldı",
    "capa.submitted": "CAPA onaya gönderildi", "capa.approved": "CAPA onaylandı", "capa.rejected": "CAPA reddedildi",
    "capa.commented": "CAPA'ya mesaj", "audit.completed": "Denetim tamamlandı", "assessment.sent": "Öz-değerlendirme gönderildi",
    "assessment.submitted": "Öz-değerlendirme cevaplandı", "alert.created": "Alarm", "test": "Test bildirimi",
}
SEV_EMOJI = {"kritik": "🔴", "yüksek": "🟠", "orta": "🟡", "düşük": "⚪", "bilgi": "🔵"}
LINKS = {"finding": "#/bulgular", "capa": "#/capa/{id}", "audit": "#/denetim/{id}", "alert": "#/alarmlar"}


def summarize_event(body: dict, base_url: Optional[str] = None) -> dict:
    """Saf: olaydan insan okunur başlık, satırlar ve bağlantı üretir."""
    data = body.get("data") or {}
    title = EVENT_TITLES.get(body.get("event"), body.get("event"))
    sev = data.get("severity")
    head = f"{SEV_EMOJI.get(sev, '')} {title}".strip()
    main = data.get("title") or data.get("message") or ""
    lines = []
    if body.get("supplier") and body["supplier"].get("name"):
        lines.append(f"Tedarikçi: {body['supplier']['name']}")
    if data.get("from") and data.get("to"):
        lines.append(f"Durum: {data['from']} → {data['to']}" + (f" ({data['reason']})" if data.get("reason") else ""))
    if data.get("score") is not None:
        lines.append(f"Puan: {data['score']}" + (f" · karar: {data['decision']}" if data.get("decision") else ""))
    if body.get("event") == "capa.commented" and data.get("message"):
        lines.append(f"{data.get('author', '')}: {data['message']}")
    if data.get("detail"):
        lines.append(str(data["detail"]))
    link = None
    if base_url and body.get("entity") in LINKS:
        link = base_url.rstrip("/") + "/" + LINKS[body["entity"]].format(id=body.get("entity_id"))
    return {"head": head, "main": main, "lines": lines, "link": link}


def slack_body(body: dict, base_url: Optional[str] = None) -> dict:
    m = summarize_event(body, base_url)
    text = f"*{m['head']}*" + (f"\n{m['main']}" if m["main"] else "") + "".join(f"\n{x}" for x in m["lines"])
    if m["link"]:
        text += f"\n<{m['link']}|Uygulamada aç>"
    return {"text": f"{m['head']}: {m['main']}"[:150], "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": text[:2900]}}]}


def teams_body(body: dict, base_url: Optional[str] = None) -> dict:
    """Teams 'Workflows' (Power Automate) gelen webhook'u için Adaptive Card mesajı."""
    m = summarize_event(body, base_url)
    items = [{"type": "TextBlock", "text": m["head"], "weight": "Bolder", "size": "Medium", "wrap": True}]
    if m["main"]:
        items.append({"type": "TextBlock", "text": m["main"], "wrap": True})
    items += [{"type": "TextBlock", "text": x, "isSubtle": True, "wrap": True, "spacing": "Small"} for x in m["lines"]]
    card = {"type": "AdaptiveCard", "$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "version": "1.4", "body": items}
    if m["link"]:
        card["actions"] = [{"type": "Action.OpenUrl", "title": "Uygulamada aç", "url": m["link"]}]
    return {"type": "message", "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "content": card}]}


async def _send(delivery_id: int) -> dict:
    from .notify import sign
    with get_db() as db:
        d = row(db.execute(
            "SELECT d.*, s.url, s.secret, s.format, e.type, e.payload, e.supplier_id, e.created_at AS ev_created, sp.name AS supplier_name "
            "FROM webhook_deliveries d JOIN webhook_subscriptions s ON s.id=d.subscription_id JOIN events e ON e.id=d.event_id "
            "LEFT JOIN suppliers sp ON sp.id=e.supplier_id WHERE d.id=?", (delivery_id,)).fetchone())
    if not d:
        raise HTTPException(404, "Teslimat bulunamadı")
    full = _body({"id": d["event_id"], "payload": d["payload"], "created_at": d["ev_created"], "supplier_id": d["supplier_id"]},
                 d["supplier_name"])
    fmt = d.get("format") or "json"
    if fmt in ("slack", "teams"):
        with get_db() as db:
            b = db.execute("SELECT value FROM settings WHERE key='public_base_url'").fetchone()
        full = (slack_body if fmt == "slack" else teams_body)(full, b[0] if b and b[0] else None)
    body = json.dumps(full, ensure_ascii=False).encode()
    ts = str(int(dt.datetime.now(dt.timezone.utc).timestamp()))
    headers = {"Content-Type": "application/json; charset=utf-8", "User-Agent": "TedarikciDenetim-Webhook/1.0",
               "X-TD-Event": d["type"], "X-TD-Delivery": str(delivery_id), "X-TD-Timestamp": ts}
    if d["secret"] and fmt == "json":
        headers["X-TD-Signature"] = sign(d["secret"], ts, body)
    status, code, err = "gönderildi", None, None
    try:
        code = await post(d["url"], body, headers)
        if code >= 300:
            status, err = "başarısız", f"HTTP {code}"
    except Exception as e:
        status, err = "başarısız", f"{type(e).__name__}: {e}"[:300]
    with get_db() as db:
        db.execute("UPDATE webhook_deliveries SET status=?, response_code=?, error=?, attempts=attempts+1, "
                   "delivered_at=CASE WHEN ?='gönderildi' THEN datetime('now') END WHERE id=?",
                   (status, code, err, status, delivery_id))
        return row(db.execute("SELECT * FROM webhook_deliveries WHERE id=?", (delivery_id,)).fetchone())


async def post(url: str, body: bytes, headers: dict) -> int:
    """Testlerde değiştirilebilir HTTP gönderimi."""
    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as c:
        r = await c.post(url, content=body, headers=headers)
    return r.status_code


async def deliver_pending() -> int:
    """Bekleyen ve deneme hakkı kalan başarısız teslimatları gönderir."""
    with get_db() as db:
        ids = [r[0] for r in db.execute(
            "SELECT id FROM webhook_deliveries WHERE status='beklemede' OR (status='başarısız' AND attempts < ?) "
            "ORDER BY id LIMIT 200", (MAX_ATTEMPTS,))]
    for i in ids:
        await _send(i)
    return len(ids)


# ------------------------------------------------------------------ API: abonelikler
class SubscriptionIn(BaseModel):
    name: str = Field(min_length=1)
    url: str
    secret: Optional[str] = None
    events: list[str] = ["*"]
    supplier_id: Optional[int] = None
    active: bool = True
    format: Literal["json", "slack", "teams"] = "json"

    @field_validator("url")
    @classmethod
    def _url(cls, v):
        if not re.match(r"^https?://[^\s/]+", v.strip()):
            raise ValueError("Adres http:// veya https:// ile başlamalı")
        return v.strip()

    @field_validator("events")
    @classmethod
    def _events(cls, v):
        allowed = set(EVENT_TYPES) | {"*", "finding.*", "capa.*", "audit.*", "assessment.*", "alert.*"}
        bad = [e for e in v if e not in allowed]
        if bad or not v:
            raise ValueError(f"Geçersiz olay türü: {', '.join(bad) or '(boş)'}")
        return v


def _sub(db, sid: int) -> dict:
    s = row(db.execute("SELECT w.*, sp.name AS supplier_name FROM webhook_subscriptions w "
                       "LEFT JOIN suppliers sp ON sp.id=w.supplier_id WHERE w.id=?", (sid,)).fetchone())
    if not s:
        raise HTTPException(404, "Abonelik bulunamadı")
    s["events"] = json.loads(s["events"])
    s["active"] = bool(s["active"])
    return s


@router.get("/api/webhooks")
def list_subscriptions():
    with get_db() as db:
        return {"event_types": EVENT_TYPES,
                "items": [_sub(db, r[0]) for r in db.execute("SELECT id FROM webhook_subscriptions ORDER BY id")]}


@router.post("/api/webhooks", status_code=201)
def create_subscription(body: SubscriptionIn):
    with get_db() as db:
        sid = db.execute("INSERT INTO webhook_subscriptions (name, url, secret, events, supplier_id, active, format) VALUES (?,?,?,?,?,?,?)",
                         (body.name, body.url, body.secret, json.dumps(body.events), body.supplier_id, int(body.active), body.format)).lastrowid
        return _sub(db, sid)


@router.put("/api/webhooks/{sid}")
def update_subscription(sid: int, body: SubscriptionIn):
    with get_db() as db:
        _sub(db, sid)
        db.execute("UPDATE webhook_subscriptions SET name=?, url=?, secret=?, events=?, supplier_id=?, active=?, format=? WHERE id=?",
                   (body.name, body.url, body.secret, json.dumps(body.events), body.supplier_id, int(body.active), body.format, sid))
        return _sub(db, sid)


@router.delete("/api/webhooks/{sid}", status_code=204)
def delete_subscription(sid: int):
    with get_db() as db:
        db.execute("DELETE FROM webhook_subscriptions WHERE id=?", (sid,))


@router.get("/api/webhooks/deliveries")
def list_deliveries(limit: int = 100):
    with get_db() as db:
        return rows(db.execute(
            "SELECT d.*, w.name AS subscription_name, e.type AS event_type, e.entity, e.entity_id FROM webhook_deliveries d "
            "JOIN webhook_subscriptions w ON w.id=d.subscription_id JOIN events e ON e.id=d.event_id "
            "ORDER BY d.id DESC LIMIT ?", (min(limit, 1000),)).fetchall())


@router.post("/api/webhooks/deliveries/{did}/retry")
async def retry_delivery(did: int):
    return await _send(did)


@router.post("/api/webhooks/{sid}/test")
async def test_subscription(sid: int):
    with get_db() as db:
        _sub(db, sid)
        eid = db.execute("INSERT INTO events (type, entity, entity_id, payload) VALUES (?,?,?,?)",
                         ("test", "webhook", str(sid), json.dumps({"event": "test", "data": {"message": "Test olayı"}}))).lastrowid
        did = db.execute("INSERT INTO webhook_deliveries (subscription_id, event_id) VALUES (?,?)", (sid, eid)).lastrowid
    return await _send(did)
