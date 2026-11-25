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
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from .db import get_db, row, rows

router = APIRouter()
EVENT_TYPES = ["finding.created", "finding.status_changed", "capa.created", "capa.submitted", "capa.approved",
               "capa.rejected", "audit.completed"]
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
        return db.execute("SELECT 1 FROM webhook_deliveries WHERE status='beklemede' LIMIT 1").fetchone() is not None


def _body(ev: dict, supplier_name: str | None) -> dict:
    p = json.loads(ev["payload"])
    return {**p, "id": ev["id"], "occurred_at": ev["created_at"] + "Z",
            "supplier": {"id": ev["supplier_id"], "name": supplier_name} if ev["supplier_id"] else None}


async def _send(delivery_id: int) -> dict:
    from .notify import sign
    with get_db() as db:
        d = row(db.execute(
            "SELECT d.*, s.url, s.secret, e.type, e.payload, e.supplier_id, e.created_at AS ev_created, sp.name AS supplier_name "
            "FROM webhook_deliveries d JOIN webhook_subscriptions s ON s.id=d.subscription_id JOIN events e ON e.id=d.event_id "
            "LEFT JOIN suppliers sp ON sp.id=e.supplier_id WHERE d.id=?", (delivery_id,)).fetchone())
    if not d:
        raise HTTPException(404, "Teslimat bulunamadı")
    body = json.dumps(_body({"id": d["event_id"], "payload": d["payload"], "created_at": d["ev_created"],
                             "supplier_id": d["supplier_id"]}, d["supplier_name"]), ensure_ascii=False).encode()
    ts = str(int(dt.datetime.now(dt.timezone.utc).timestamp()))
    headers = {"Content-Type": "application/json; charset=utf-8", "User-Agent": "TedarikciDenetim-Webhook/1.0",
               "X-TD-Event": d["type"], "X-TD-Delivery": str(delivery_id), "X-TD-Timestamp": ts}
    if d["secret"]:
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

    @field_validator("url")
    @classmethod
    def _url(cls, v):
        if not re.match(r"^https?://[^\s/]+", v.strip()):
            raise ValueError("Adres http:// veya https:// ile başlamalı")
        return v.strip()

    @field_validator("events")
    @classmethod
    def _events(cls, v):
        allowed = set(EVENT_TYPES) | {"*", "finding.*", "capa.*", "audit.*"}
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
        sid = db.execute("INSERT INTO webhook_subscriptions (name, url, secret, events, supplier_id, active) VALUES (?,?,?,?,?,?)",
                         (body.name, body.url, body.secret, json.dumps(body.events), body.supplier_id, int(body.active))).lastrowid
        return _sub(db, sid)


@router.put("/api/webhooks/{sid}")
def update_subscription(sid: int, body: SubscriptionIn):
    with get_db() as db:
        _sub(db, sid)
        db.execute("UPDATE webhook_subscriptions SET name=?, url=?, secret=?, events=?, supplier_id=?, active=? WHERE id=?",
                   (body.name, body.url, body.secret, json.dumps(body.events), body.supplier_id, int(body.active), sid))
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
