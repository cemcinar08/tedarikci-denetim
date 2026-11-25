"""Bulgu istisnaları: itiraz (yanlış pozitif) ve süreli risk kabulü (waiver) — onay akışı ve denetim izi.

* İtiraz: tedarikçi veya denetçi açar; denetçi/yönetici karar verir. Onaylanırsa bulgu 'kapandı' (yanlış pozitif),
  bağlı açık CAPA 'iptal edildi' olur.
* Risk kabulü: denetçi talep eder (gerekçe + telafi edici kontroller + bitiş tarihi, en fazla 1 yıl);
  yalnızca yönetici onaylar. Onaylanırsa bulgu 'risk kabul edildi' olur, açık bulgu sayılmaz; süresi dolunca
  bulgu yeniden 'açık' olur.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import auth, events
from .auth import current_user, ensure_supplier_access, has_perm, is_supplier_user, scoped
from .db import get_db, row, rows

router = APIRouter()
MAX_WAIVER_DAYS = 365
ACCEPTED = "risk kabul edildi"
OPEN_STATES = ("açık", "devam ediyor")


# ------------------------------------------------------------------ saf kurallar
def validate_request(kind: str, finding_status: str, expires_at: Optional[str], is_supplier: bool, today: dt.date) -> None:
    """Saf: istisna talebi geçerli mi? Geçersizse ValueError."""
    if finding_status not in OPEN_STATES:
        raise ValueError("Yalnızca açık veya devam eden bulgular için talep açılabilir.")
    if kind == "risk kabulü":
        if is_supplier:
            raise ValueError("Risk kabulünü yalnızca kurum talep edebilir; tedarikçi itiraz açabilir.")
        if not expires_at or not re.match(r"^\d{4}-\d{2}-\d{2}$", expires_at):
            raise ValueError("Risk kabulü için bitiş tarihi (YYYY-AA-GG) zorunlu.")
        d = dt.date.fromisoformat(expires_at)
        if d <= today:
            raise ValueError("Bitiş tarihi ileri bir tarih olmalı.")
        if (d - today).days > MAX_WAIVER_DAYS:
            raise ValueError(f"Risk kabulü en fazla {MAX_WAIVER_DAYS} gün olabilir.")


def outcome(kind: str, decision: str) -> Optional[str]:
    """Saf: kararın bulguya etkisi (yeni bulgu durumu) — None ise bulgu değişmez."""
    if decision == "reject":
        return None
    return "kapandı" if kind == "itiraz" else ACCEPTED


def can_decide(kind: str, user: dict) -> bool:
    return has_perm(user, "risk:accept") if kind == "risk kabulü" else has_perm(user, "finding:write")


# ------------------------------------------------------------------ servis
def expire_waivers(db, today: Optional[dt.date] = None) -> int:
    """Süresi dolan risk kabullerini sonlandırır, bulguyu yeniden açar. Zamanlayıcı ve okuma uçları çağırır."""
    t = (today or dt.date.today()).isoformat()
    n = 0
    for e in rows(db.execute("SELECT e.*, f.supplier_id, f.title FROM finding_exceptions e JOIN findings f ON f.id=e.finding_id "
                             "WHERE e.type='risk kabulü' AND e.status='onaylandı' AND e.expires_at < ?", (t,)).fetchall()):
        db.execute("UPDATE finding_exceptions SET status='süresi doldu' WHERE id=?", (e["id"],))
        cur = db.execute("SELECT status FROM findings WHERE id=?", (e["finding_id"],)).fetchone()
        if cur and cur[0] == ACCEPTED:
            db.execute("UPDATE findings SET status='açık', closed_at=NULL WHERE id=?", (e["finding_id"],))
            events.emit("finding.status_changed", e["supplier_id"], "finding", e["finding_id"],
                        {"id": e["finding_id"], "title": e["title"], "from": ACCEPTED, "to": "açık", "reason": "Risk kabulünün süresi doldu"}, db)
        n += 1
    return n


def _get(db, eid: int) -> dict:
    e = row(db.execute("SELECT e.*, f.title AS finding_title, f.supplier_id, f.severity, f.status AS finding_status, s.name AS supplier_name "
                       "FROM finding_exceptions e JOIN findings f ON f.id=e.finding_id JOIN suppliers s ON s.id=f.supplier_id "
                       "WHERE e.id=?", (eid,)).fetchone())
    if not e:
        raise HTTPException(404, "Talep bulunamadı")
    ensure_supplier_access(e["supplier_id"])
    return e


class ExceptionIn(BaseModel):
    type: Literal["itiraz", "risk kabulü"]
    reason: str = Field(min_length=10, max_length=4000)
    compensating_controls: Optional[str] = Field(default=None, max_length=4000)
    expires_at: Optional[str] = None


class DecisionIn(BaseModel):
    decision: Literal["approve", "reject"]
    comment: Optional[str] = Field(default=None, max_length=2000)


@router.get("/api/exceptions")
def list_exceptions(status: Optional[str] = None, type: Optional[str] = None):
    w, p = scoped("f.supplier_id")
    q = ("SELECT e.*, f.title AS finding_title, f.supplier_id, f.severity, s.name AS supplier_name FROM finding_exceptions e "
         "JOIN findings f ON f.id=e.finding_id JOIN suppliers s ON s.id=f.supplier_id WHERE 1=1" + w)
    if status:
        q += " AND e.status=?"; p.append(status)
    if type:
        q += " AND e.type=?"; p.append(type)
    with get_db() as db:
        expire_waivers(db)
        return rows(db.execute(q + " ORDER BY e.status='beklemede' DESC, e.id DESC LIMIT 500", p).fetchall())


@router.get("/api/findings/{fid}/exceptions")
def finding_exceptions(fid: int):
    with get_db() as db:
        f = db.execute("SELECT supplier_id FROM findings WHERE id=?", (fid,)).fetchone()
        if not f:
            raise HTTPException(404, "Bulgu bulunamadı")
        ensure_supplier_access(f[0])
        return rows(db.execute("SELECT * FROM finding_exceptions WHERE finding_id=? ORDER BY id DESC", (fid,)).fetchall())


@router.post("/api/findings/{fid}/exceptions", status_code=201)
def request_exception(fid: int, body: ExceptionIn):
    u = current_user()
    with get_db() as db:
        f = row(db.execute("SELECT * FROM findings WHERE id=?", (fid,)).fetchone())
        if not f:
            raise HTTPException(404, "Bulgu bulunamadı")
        ensure_supplier_access(f["supplier_id"])
        if body.type == "risk kabulü" and not has_perm(u, "finding:write"):
            raise HTTPException(403, "Risk kabulü talebi için yetkiniz yok.")
        try:
            validate_request(body.type, f["status"], body.expires_at, is_supplier_user(), dt.date.today())
        except ValueError as e:
            raise HTTPException(422, str(e))
        if db.execute("SELECT 1 FROM finding_exceptions WHERE finding_id=? AND status='beklemede'", (fid,)).fetchone():
            raise HTTPException(409, "Bu bulgu için bekleyen bir talep zaten var.")
        eid = db.execute(
            "INSERT INTO finding_exceptions (finding_id, type, reason, compensating_controls, expires_at, requested_by, requested_by_name, "
            "requested_by_role) VALUES (?,?,?,?,?,?,?,?)",
            (fid, body.type, body.reason, body.compensating_controls, body.expires_at if body.type == "risk kabulü" else None,
             u.get("id"), u.get("name"), u.get("role"))).lastrowid
        auth.log(f"finding.{'dispute' if body.type == 'itiraz' else 'waiver'}_request", "finding", fid, {"exception_id": eid}, db=db)
        return _get(db, eid)


@router.post("/api/exceptions/{eid}/decide")
def decide(eid: int, body: DecisionIn):
    u = current_user()
    with get_db() as db:
        e = _get(db, eid)
        if e["status"] != "beklemede":
            raise HTTPException(409, "Bu talep zaten sonuçlandı.")
        if not can_decide(e["type"], u):
            raise HTTPException(403, "Risk kabulünü yalnızca yönetici onaylayabilir." if e["type"] == "risk kabulü" else "Yetkiniz yok.")
        if body.decision == "reject" and not (body.comment or "").strip():
            raise HTTPException(422, "Red için gerekçe yazın.")
        if e["requested_by"] and e["requested_by"] == u.get("id") and e["type"] == "risk kabulü":
            raise HTTPException(403, "Kendi risk kabulü talebinizi onaylayamazsınız (görevler ayrılığı).")
        db.execute("UPDATE finding_exceptions SET status=?, decided_by=?, decided_by_name=?, decided_at=datetime('now'), decision_comment=? "
                   "WHERE id=?", ("onaylandı" if body.decision == "approve" else "reddedildi", u.get("id"), u.get("name"), body.comment, eid))
        new = outcome(e["type"], body.decision)
        if new and e["finding_status"] in OPEN_STATES:
            note = (f"\n[İtiraz kabul edildi — yanlış pozitif] {body.comment or ''}" if e["type"] == "itiraz"
                    else f"\n[Risk kabul edildi, {e['expires_at']} tarihine kadar] {body.comment or ''}")
            db.execute("UPDATE findings SET status=?, closed_at=datetime('now'), description=COALESCE(description,'') || ? WHERE id=?",
                       (new, note, e["finding_id"]))
            events.emit("finding.status_changed", e["supplier_id"], "finding", e["finding_id"],
                        {"id": e["finding_id"], "title": e["finding_title"], "from": e["finding_status"], "to": new,
                         "reason": "İtiraz kabul edildi" if e["type"] == "itiraz" else "Risk kabul edildi"}, db)
            for c in db.execute("SELECT id, status FROM capa_items WHERE finding_id=? AND status IN ('açık','reddedildi','onay bekliyor')",
                                (e["finding_id"],)).fetchall():
                db.execute("UPDATE capa_items SET status='iptal edildi', closed_at=datetime('now'), review_comment=? WHERE id=?",
                           (f"{e['type'].capitalize()} onaylandı: {body.comment or ''}", c["id"]))
                db.execute("INSERT INTO capa_events (capa_id, user_id, actor, action, from_status, to_status, comment) VALUES (?,?,?,?,?,?,?)",
                           (c["id"], u.get("id"), u.get("name"), "iptal edildi", c["status"], "iptal edildi", f"{e['type']} onaylandı"))
        auth.log(f"exception.{body.decision}", "finding", e["finding_id"], {"exception_id": eid, "type": e["type"]}, db=db)
        return _get(db, eid)
