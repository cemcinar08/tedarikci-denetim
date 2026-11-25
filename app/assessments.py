"""Tedarikçi öz-değerlendirmesi: anket tedarikçiye gönderilir, tedarikçi portalda cevaplar ve gönderir, denetçi inceler.

Durumlar:  gönderildi -> yanıtlanıyor -> incelemede -> tamamlandı
                              ^                |
                              └─ revizyon istendi (denetçi geri gönderir)
"""
from __future__ import annotations

import datetime as dt
import json
import re
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import auth, events
from . import templates_engine as eng
from .auth import current_user
from .db import get_db, row

router = APIRouter()
EDITABLE = ("gönderildi", "yanıtlanıyor", "revizyon istendi")


# ------------------------------------------------------------------ saf kurallar
def missing_answers(checklist: list[dict], answers: dict[str, str]) -> list[str]:
    """Saf: cevaplanmamış görünür soruların metinleri."""
    vis = eng.visible_question_ids(checklist, answers)
    return [q["text"] for s in checklist for q in s["questions"] if q["id"] in vis and answers.get(q["id"]) not in eng.VALID_ANSWERS]


def respond_allowed(audit: dict, user: dict) -> Optional[str]:
    """Saf: tedarikçinin bu ankete cevap yazıp yazamayacağı; engel varsa mesaj döner."""
    if audit.get("respondent") != "tedarikçi":
        return "Bu denetim tedarikçi tarafından cevaplanmaz."
    if user.get("role") == "supplier" and user.get("supplier_id") != audit["supplier_id"]:
        return "Kayıt bulunamadı"
    if audit["status"] not in EDITABLE:
        return f"'{audit['status']}' durumundaki anket değiştirilemez."
    return None


# ------------------------------------------------------------------ API
class SendIn(BaseModel):
    question_set_ids: list[int] = []
    custom_sections: list = []
    due_date: str
    message: Optional[str] = Field(default=None, max_length=2000)


class RespondIn(BaseModel):
    answers: dict[str, dict]   # {soru_id: {answer, note}}


class ReturnIn(BaseModel):
    comment: str = Field(min_length=3, max_length=2000)


def _audit(db, aid: int) -> dict:
    from .main import get_audit
    return get_audit(aid, db)


@router.post("/api/suppliers/{sid}/self-assessments", status_code=201)
def send(sid: int, body: SendIn):
    from .main import AuditIn, create_audit
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", body.due_date) or body.due_date < dt.date.today().isoformat():
        raise HTTPException(422, "Son yanıt tarihi ileri bir tarih olmalı (YYYY-AA-GG).")
    a = create_audit(sid, AuditIn(question_set_ids=body.question_set_ids, custom_sections=body.custom_sections,
                                  audit_date=dt.date.today().isoformat(), audit_type="öz-değerlendirme", status="planlandı",
                                  notes=body.message))
    with get_db() as db:
        db.execute("UPDATE audits SET respondent='tedarikçi', status='gönderildi', due_date=?, sent_at=datetime('now'), auditor=? WHERE id=?",
                   (body.due_date, current_user().get("name"), a["id"]))
        events.emit("assessment.sent", sid, "audit", a["id"], {"id": a["id"], "due_date": body.due_date, "sets": a["set_names"]}, db)
        auth.log("assessment.send", "audit", a["id"], {"supplier_id": sid, "due_date": body.due_date}, db=db)
        sup = row(db.execute("SELECT name, notify_emails FROM suppliers WHERE id=?", (sid,)).fetchone())
        out = _audit(db, a["id"])
    _mail(sup, f"Öz-değerlendirme anketi: son tarih {body.due_date}",
          f"{', '.join(a['set_names'])} anketi tarafınıza gönderildi. Lütfen {body.due_date} tarihine kadar tedarikçi portalından "
          f"cevaplayıp gönderin." + (f"\n\nNot: {body.message}" if body.message else ""))
    return out


@router.put("/api/audits/{aid}/respond")
def respond(aid: int, body: RespondIn):
    u = current_user()
    with get_db() as db:
        a = _audit(db, aid)
        err = respond_allowed(a, u)
        if err:
            raise HTTPException(404 if err == "Kayıt bulunamadı" else 409, err)
        valid = {q["id"] for s in a["checklist"] for q in s["questions"]}
        for qid, v in body.answers.items():
            if qid not in valid:
                raise HTTPException(422, f"Bilinmeyen soru: {qid}")
            ans = v.get("answer")
            if ans is not None and ans not in eng.VALID_ANSWERS:
                raise HTTPException(422, "Geçersiz cevap")
            db.execute("INSERT INTO audit_answers (audit_id, question_id, answer, note) VALUES (?,?,?,?) "
                       "ON CONFLICT(audit_id, question_id) DO UPDATE SET answer=excluded.answer, note=excluded.note",
                       (aid, qid, ans, (v.get("note") or None)))
        if a["status"] == "gönderildi":
            db.execute("UPDATE audits SET status='yanıtlanıyor' WHERE id=?", (aid,))
        a = _audit(db, aid)
        r = a["result"]
        db.execute("UPDATE audits SET score=?, risk_level=?, decision=?, kill_switches=? WHERE id=?",
                   (r["score"], r["risk_level"], r["decision"], json.dumps(r["kill_switches"], ensure_ascii=False), aid))
        return a


@router.post("/api/audits/{aid}/submit")
def submit(aid: int):
    u = current_user()
    with get_db() as db:
        a = _audit(db, aid)
        err = respond_allowed(a, u)
        if err:
            raise HTTPException(404 if err == "Kayıt bulunamadı" else 409, err)
        miss = missing_answers(a["checklist"], {k: v["answer"] for k, v in a["answers"].items() if v["answer"]})
        if miss:
            raise HTTPException(422, f"{len(miss)} soru cevaplanmadı. İlk eksik: {miss[0][:120]}")
        db.execute("UPDATE audits SET status='incelemede', submitted_at=datetime('now'), submitted_by=? WHERE id=?", (u.get("name"), aid))
        events.emit("assessment.submitted", a["supplier_id"], "audit", aid, {"id": aid, "score": a["result"]["score"]}, db)
        auth.log("assessment.submit", "audit", aid, db=db)
        return _audit(db, aid)


@router.post("/api/audits/{aid}/return")
def return_for_revision(aid: int, body: ReturnIn):
    with get_db() as db:
        a = _audit(db, aid)
        if a.get("respondent") != "tedarikçi" or a["status"] != "incelemede":
            raise HTTPException(409, "Yalnızca incelemedeki öz-değerlendirme revizyona gönderilebilir.")
        db.execute("UPDATE audits SET status='revizyon istendi', review_comment=? WHERE id=?", (body.comment, aid))
        auth.log("assessment.return", "audit", aid, {"comment": body.comment}, db=db)
        sup = row(db.execute("SELECT name, notify_emails FROM suppliers WHERE id=?", (a["supplier_id"],)).fetchone())
        out = _audit(db, aid)
    _mail(sup, "Öz-değerlendirme: revizyon istendi", f"Denetçi notu:\n{body.comment}\n\nLütfen portalda cevaplarınızı güncelleyip yeniden gönderin.")
    return out


def _mail(sup: Optional[dict], subject: str, text: str) -> None:
    from . import notify
    if not sup or not sup.get("notify_emails"):
        return
    try:
        with get_db() as db:
            s = notify.get_settings(db)
        if notify.smtp_ready(s):
            import html as _h
            notify.send_email(s, notify.parse_emails(sup["notify_emails"]), f"[{s['org_name'] or 'Tedarikçi Denetim'}] {subject}",
                              text, f"<p style='white-space:pre-wrap'>{_h.escape(text)}</p>")
    except Exception:
        pass
