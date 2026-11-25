"""Jira Cloud / ServiceNow entegrasyonu: bulgu veya CAPA'dan ticket açma ve durum eşitleme.

Jira:        POST {base}/rest/api/3/issue    (Basic: e-posta + API token)
ServiceNow:  POST {instance}/api/now/table/{tablo}   (Basic: kullanıcı + parola)
Uzak ticket "tamamlandı" olduğunda (ayarda açıksa) bağlı bulgu kapatılır.
"""
from __future__ import annotations

import base64
import json
import re
from typing import Literal, Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import auth, events
from .auth import ensure_supplier_access
from .db import get_db, row, rows

router = APIRouter()
SECRET_FIELDS = {"jira_api_token", "servicenow_password"}
DEFAULTS = {"provider": "", "jira_base_url": "", "jira_email": "", "jira_api_token": "", "jira_project_key": "", "jira_issue_type": "Task",
            "servicenow_instance_url": "", "servicenow_username": "", "servicenow_password": "", "servicenow_table": "incident",
            "servicenow_assignment_group": "", "auto_close": False}
SN_URGENCY = {"kritik": "1", "yüksek": "1", "orta": "2", "düşük": "3"}
JIRA_PRIORITY = {"kritik": "Highest", "yüksek": "High", "orta": "Medium", "düşük": "Low"}


# ------------------------------------------------------------------ saf yardımcılar
def ticket_text(item: dict, link: Optional[str]) -> tuple[str, str]:
    """Saf: (özet, açıklama) — item: {kind, title, supplier_name, severity, description, action, due_date}"""
    summary = f"[{item['supplier_name']}] {item['title']}"[:250]
    parts = [f"Tedarikçi: {item['supplier_name']}", f"Önem: {item.get('severity') or '-'}",
             f"Kaynak: {'CAPA' if item['kind'] == 'capa' else 'Bulgu'} #{item['id']}"]
    if item.get("due_date"):
        parts.append(f"Termin: {item['due_date']}")
    if item.get("description"):
        parts += ["", item["description"]]
    if item.get("action"):
        parts += ["", "Beklenen aksiyon: " + item["action"]]
    if link:
        parts += ["", f"Uygulamada: {link}"]
    return summary, "\n".join(parts)


def adf(text: str) -> dict:
    """Saf: düz metni Jira Atlassian Document Format'a çevirir."""
    paras = [p for p in text.split("\n")]
    return {"type": "doc", "version": 1, "content": [
        {"type": "paragraph", "content": ([{"type": "text", "text": p}] if p else [])} for p in paras]}


def jira_payload(cfg: dict, item: dict, link: Optional[str]) -> dict:
    s, d = ticket_text(item, link)
    return {"fields": {"project": {"key": cfg["jira_project_key"]}, "summary": s, "issuetype": {"name": cfg["jira_issue_type"] or "Task"},
                       "description": adf(d), "labels": ["tedarikci-denetim", f"onem-{(item.get('severity') or 'orta').replace('ü', 'u').replace('ş', 's')}"]}}


def servicenow_payload(cfg: dict, item: dict, link: Optional[str]) -> dict:
    s, d = ticket_text(item, link)
    p = {"short_description": s, "description": d, "urgency": SN_URGENCY.get(item.get("severity"), "2"),
         "impact": SN_URGENCY.get(item.get("severity"), "2"), "category": "Tedarikçi riski"}
    if cfg.get("servicenow_assignment_group"):
        p["assignment_group"] = cfg["servicenow_assignment_group"]
    return p


def is_done(provider: str, status: dict) -> bool:
    """Saf: uzak ticket tamamlandı mı? Jira statusCategory=done; ServiceNow state 6 (Resolved) / 7 (Closed)."""
    if provider == "jira":
        return status.get("category") == "done"
    return str(status.get("state")) in ("6", "7", "3")


def _basic(u: str, p: str) -> str:
    return "Basic " + base64.b64encode(f"{u}:{p}".encode()).decode()


# ------------------------------------------------------------------ ayarlar
def get_cfg(db) -> dict:
    r = db.execute("SELECT value FROM settings WHERE key='ticketing'").fetchone()
    try:
        c = json.loads(r[0]) if r and r[0] else {}
    except ValueError:
        c = {}
    return {**DEFAULTS, **{k: v for k, v in c.items() if k in DEFAULTS}}


def public_cfg(c: dict) -> dict:
    out = {k: v for k, v in c.items() if k not in SECRET_FIELDS}
    out.update({f"{k}_set": bool(c.get(k)) for k in SECRET_FIELDS})
    out["ready"] = ready(c)
    return out


def ready(c: dict) -> bool:
    if c["provider"] == "jira":
        return all(c[k] for k in ("jira_base_url", "jira_email", "jira_api_token", "jira_project_key"))
    if c["provider"] == "servicenow":
        return all(c[k] for k in ("servicenow_instance_url", "servicenow_username", "servicenow_password"))
    return False


class CfgIn(BaseModel):
    provider: Literal["", "jira", "servicenow"] = ""
    jira_base_url: Optional[str] = ""
    jira_email: Optional[str] = ""
    jira_api_token: Optional[str] = None
    jira_project_key: Optional[str] = ""
    jira_issue_type: Optional[str] = "Task"
    servicenow_instance_url: Optional[str] = ""
    servicenow_username: Optional[str] = ""
    servicenow_password: Optional[str] = None
    servicenow_table: Optional[str] = "incident"
    servicenow_assignment_group: Optional[str] = ""
    auto_close: bool = False


@router.get("/api/integrations/ticketing")
def read_cfg():
    with get_db() as db:
        return public_cfg(get_cfg(db))


@router.put("/api/integrations/ticketing")
def write_cfg(body: CfgIn):
    with get_db() as db:
        cur = get_cfg(db)
        d = body.model_dump()
        for k in SECRET_FIELDS:
            if not d.get(k):
                d[k] = cur[k]          # boş bırakılırsa mevcut gizli değer korunur
        for k in ("jira_base_url", "servicenow_instance_url"):
            if d[k]:
                d[k] = d[k].strip().rstrip("/")
                if not re.match(r"^https://[^\s/]+", d[k]):
                    raise HTTPException(422, "Adres https:// ile başlamalı.")
        if d["servicenow_table"] and not re.match(r"^[a-z_][a-z0-9_]*$", d["servicenow_table"]):
            raise HTTPException(422, "Geçersiz ServiceNow tablo adı.")
        db.execute("INSERT INTO settings (key, value) VALUES ('ticketing', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (json.dumps(d),))
        auth.log("settings.ticketing", "settings", None, {"provider": d["provider"]}, db=db)
        return public_cfg(get_cfg(db))


# ------------------------------------------------------------------ HTTP (testlerde değiştirilebilir)
async def http(method: str, url: str, headers: dict, body: Optional[dict] = None) -> tuple[int, dict]:
    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as c:
        r = await c.request(method, url, headers=headers, json=body)
    try:
        data = r.json()
    except ValueError:
        data = {"raw": r.text[:300]}
    return r.status_code, data


async def create_remote(cfg: dict, item: dict, link: Optional[str]) -> dict:
    if cfg["provider"] == "jira":
        h = {"Authorization": _basic(cfg["jira_email"], cfg["jira_api_token"]), "Accept": "application/json"}
        code, data = await http("POST", f"{cfg['jira_base_url']}/rest/api/3/issue", h, jira_payload(cfg, item, link))
        if code >= 300:
            raise HTTPException(502, f"Jira hatası (HTTP {code}): {json.dumps(data.get('errors') or data.get('errorMessages') or data, ensure_ascii=False)[:300]}")
        return {"key": data["key"], "remote_id": data.get("id"), "url": f"{cfg['jira_base_url']}/browse/{data['key']}"}
    h = {"Authorization": _basic(cfg["servicenow_username"], cfg["servicenow_password"]), "Accept": "application/json"}
    code, data = await http("POST", f"{cfg['servicenow_instance_url']}/api/now/table/{cfg['servicenow_table']}", h, servicenow_payload(cfg, item, link))
    if code >= 300:
        raise HTTPException(502, f"ServiceNow hatası (HTTP {code}): {json.dumps(data.get('error') or data, ensure_ascii=False)[:300]}")
    r = data["result"]
    return {"key": r.get("number"), "remote_id": r["sys_id"],
            "url": f"{cfg['servicenow_instance_url']}/nav_to.do?uri={cfg['servicenow_table']}.do?sys_id={r['sys_id']}"}


async def fetch_status(cfg: dict, t: dict) -> dict:
    if t["provider"] == "jira":
        h = {"Authorization": _basic(cfg["jira_email"], cfg["jira_api_token"]), "Accept": "application/json"}
        code, data = await http("GET", f"{cfg['jira_base_url']}/rest/api/3/issue/{t['key']}?fields=status", h)
        if code >= 300:
            raise HTTPException(502, f"Jira durum okunamadı (HTTP {code})")
        st = data["fields"]["status"]
        return {"name": st.get("name"), "category": (st.get("statusCategory") or {}).get("key")}
    h = {"Authorization": _basic(cfg["servicenow_username"], cfg["servicenow_password"]), "Accept": "application/json"}
    code, data = await http("GET", f"{cfg['servicenow_instance_url']}/api/now/table/{cfg['servicenow_table']}/{t['remote_id']}"
                                   "?sysparm_fields=state,number&sysparm_display_value=all", h)
    if code >= 300:
        raise HTTPException(502, f"ServiceNow durum okunamadı (HTTP {code})")
    st = data["result"]["state"]
    return {"name": st.get("display_value") if isinstance(st, dict) else st, "state": st.get("value") if isinstance(st, dict) else st}


# ------------------------------------------------------------------ API
class TicketIn(BaseModel):
    entity: Literal["finding", "capa"]
    entity_id: int


def _item(db, entity: str, eid: int) -> dict:
    tbl = "findings" if entity == "finding" else "capa_items"
    r = row(db.execute(f"SELECT x.*, s.name AS supplier_name FROM {tbl} x JOIN suppliers s ON s.id=x.supplier_id WHERE x.id=?", (eid,)).fetchone())
    if not r:
        raise HTTPException(404, "Kayıt bulunamadı")
    ensure_supplier_access(r["supplier_id"])
    r["kind"] = entity
    return r


@router.get("/api/tickets")
def list_tickets(entity: Optional[str] = None, entity_id: Optional[int] = None):
    q, p = "SELECT * FROM external_tickets WHERE 1=1", []
    if entity:
        q += " AND entity=?"; p.append(entity)
    if entity_id:
        q += " AND entity_id=?"; p.append(entity_id)
    with get_db() as db:
        return rows(db.execute(q + " ORDER BY id DESC LIMIT 500", p).fetchall())


@router.post("/api/tickets", status_code=201)
async def create_ticket(body: TicketIn):
    with get_db() as db:
        cfg = get_cfg(db)
        if not ready(cfg):
            raise HTTPException(400, "Jira veya ServiceNow entegrasyonu yapılandırılmamış (Ayarlar → Entegrasyonlar).")
        item = _item(db, body.entity, body.entity_id)
        if db.execute("SELECT 1 FROM external_tickets WHERE entity=? AND entity_id=? AND provider=?", (body.entity, body.entity_id, cfg["provider"])).fetchone():
            raise HTTPException(409, "Bu kayıt için zaten ticket açılmış.")
        base = db.execute("SELECT value FROM settings WHERE key='public_base_url'").fetchone()
    link = None
    if base and base[0]:
        link = f"{base[0].rstrip('/')}/#/" + (f"capa/{item['id']}" if body.entity == "capa" else "bulgular")
    r = await create_remote(cfg, item, link)
    with get_db() as db:
        tid = db.execute("INSERT INTO external_tickets (entity, entity_id, supplier_id, provider, key, remote_id, url, status) VALUES (?,?,?,?,?,?,?,?)",
                         (body.entity, body.entity_id, item["supplier_id"], cfg["provider"], r["key"], r["remote_id"], r["url"], "açık")).lastrowid
        auth.log("ticket.create", body.entity, body.entity_id, {"provider": cfg["provider"], "key": r["key"]}, db=db)
        return row(db.execute("SELECT * FROM external_tickets WHERE id=?", (tid,)).fetchone())


async def sync_ticket(tid: int) -> dict:
    with get_db() as db:
        cfg = get_cfg(db)
        t = row(db.execute("SELECT * FROM external_tickets WHERE id=?", (tid,)).fetchone())
    if not t:
        raise HTTPException(404, "Ticket bulunamadı")
    if not ready(cfg) or cfg["provider"] != t["provider"]:
        raise HTTPException(400, "Bu ticket'ın sağlayıcısı için entegrasyon yapılandırılmamış.")
    st = await fetch_status(cfg, t)
    done = is_done(t["provider"], st)
    with get_db() as db:
        db.execute("UPDATE external_tickets SET status=?, remote_status=?, synced_at=datetime('now') WHERE id=?",
                   ("tamamlandı" if done else "açık", st.get("name"), tid))
        if done and cfg["auto_close"] and t["entity"] == "finding":
            f = db.execute("SELECT id, supplier_id, title, status FROM findings WHERE id=?", (t["entity_id"],)).fetchone()
            if f and f["status"] in ("açık", "devam ediyor"):
                db.execute("UPDATE findings SET status='kapandı', closed_at=datetime('now'), description=COALESCE(description,'') || ? WHERE id=?",
                           (f"\n[{t['provider']} {t['key']} tamamlandı]", f["id"]))
                events.emit("finding.status_changed", f["supplier_id"], "finding", f["id"],
                            {"id": f["id"], "title": f["title"], "from": f["status"], "to": "kapandı", "reason": f"{t['provider']} {t['key']} tamamlandı"}, db)
        return row(db.execute("SELECT * FROM external_tickets WHERE id=?", (tid,)).fetchone())


@router.post("/api/tickets/{tid}/sync")
async def sync(tid: int):
    return await sync_ticket(tid)


async def sync_open() -> dict:
    """Zamanlayıcı: açık ticket'ların durumunu eşitler."""
    with get_db() as db:
        if not ready(get_cfg(db)):
            return {"skipped": "yapılandırılmamış"}
        ids = [r[0] for r in db.execute("SELECT id FROM external_tickets WHERE status='açık'")]
    n = 0
    for i in ids:
        try:
            await sync_ticket(i)
            n += 1
        except HTTPException:
            continue
    return {"synced": n}


@router.post("/api/integrations/ticketing/test")
async def test_connection():
    with get_db() as db:
        cfg = get_cfg(db)
    if not ready(cfg):
        raise HTTPException(400, "Önce bağlantı bilgilerini kaydedin.")
    if cfg["provider"] == "jira":
        code, data = await http("GET", f"{cfg['jira_base_url']}/rest/api/3/project/{cfg['jira_project_key']}",
                                {"Authorization": _basic(cfg["jira_email"], cfg["jira_api_token"]), "Accept": "application/json"})
        ok = code == 200
        msg = f"Proje: {data.get('name')}" if ok else f"HTTP {code}"
    else:
        code, data = await http("GET", f"{cfg['servicenow_instance_url']}/api/now/table/{cfg['servicenow_table']}?sysparm_limit=1",
                                {"Authorization": _basic(cfg["servicenow_username"], cfg["servicenow_password"]), "Accept": "application/json"})
        ok = code == 200
        msg = "Tabloya erişim var" if ok else f"HTTP {code}"
    if not ok:
        raise HTTPException(502, f"Bağlantı başarısız: {msg}")
    return {"ok": True, "message": msg}

