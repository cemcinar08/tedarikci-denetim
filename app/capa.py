"""CAPA (düzeltici / önleyici faaliyet) servisi.

Otomatik açılış (denetim 'tamamlandı' olduğunda):
  * görünür soruda 'uygun değil'  -> CAPA (kritik soruysa trigger=kill-switch, önem=kritik; değilse yüksek)
  * görünür soruda 'kısmen'       -> CAPA (önem=orta)
  * toplam puan < LOW_SCORE       -> genel iyileştirme CAPA'sı (trigger=düşük puan)
Her CAPA bir bulguya (findings) bağlıdır; aynı soru için ikinci kez açılmaz.

İş akışı (state machine):
  açık ──submit (tedarikçi)──> onay bekliyor ──approve (denetçi)──> onaylandı
                                     └──reject (denetçi, gerekçe zorunlu)──> reddedildi ──submit──> onay bekliyor
"""
from __future__ import annotations

import datetime as dt
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import auth, events
from .auth import current_user, ensure_supplier_access, scoped
from .db import get_db, row, rows

router = APIRouter()
LOW_SCORE = 70
DUE_DAYS = {"kritik": 7, "yüksek": 30, "orta": 60, "düşük": 90}
TRANSITIONS = {
    ("açık", "submit"): "onay bekliyor",
    ("reddedildi", "submit"): "onay bekliyor",
    ("onay bekliyor", "approve"): "onaylandı",
    ("onay bekliyor", "reject"): "reddedildi",
    ("açık", "cancel"): "iptal edildi",
    ("reddedildi", "cancel"): "iptal edildi",
    ("onay bekliyor", "cancel"): "iptal edildi",
}
OPEN_STATUSES = ("açık", "onay bekliyor", "reddedildi")


# ------------------------------------------------------------------ saf fonksiyonlar
def next_status(current: str, action: str) -> str:
    try:
        return TRANSITIONS[(current, action)]
    except KeyError:
        raise ValueError(f"'{current}' durumundaki CAPA için '{action}' işlemi yapılamaz") from None


def capa_candidates(checklist: list[dict], answers: dict[str, str], result: dict, low_score: float = LOW_SCORE) -> list[dict]:
    """Denetim sonucundan açılması gereken CAPA'lar. result = templates_engine.score_audit çıktısı."""
    hidden = set(result.get("hidden_question_ids", []))
    out = []
    for sec in checklist:
        for q in sec["questions"]:
            a = answers.get(q["id"])
            if q["id"] in hidden or a not in ("uygun değil", "kısmen"):
                continue
            critical_fail = bool(q.get("critical")) and a == "uygun değil"
            out.append({
                "question_id": q["id"], "title": f"[{sec['title']}] {q['text']}",
                "trigger": "kill-switch" if critical_fail else "uygunsuzluk",
                "severity": "kritik" if critical_fail else ("yüksek" if a == "uygun değil" else "orta"),
                "description": f"Denetim cevabı: {a}" + (" (kritik madde — denetim reddedildi)" if critical_fail else ""),
            })
    score = result.get("score")
    if score is not None and score < low_score:
        out.append({"question_id": "__score__", "title": f"Genel iyileştirme planı — denetim puanı {score} (eşik {low_score})",
                    "trigger": "düşük puan", "severity": "yüksek",
                    "description": "Toplam denetim puanı eşiğin altında. Zayıf bölümler için kapsamlı iyileştirme planı bekleniyor."})
    return out


def due_for(sev: str, today: dt.date | None = None) -> str:
    return ((today or dt.date.today()) + dt.timedelta(days=DUE_DAYS.get(sev, 60))).isoformat()


# ------------------------------------------------------------------ servis
def _event(db, capa_id: int, action: str, frm: str | None, to: str | None, comment: str | None = None) -> None:
    u = current_user()
    db.execute("INSERT INTO capa_events (capa_id, user_id, actor, action, from_status, to_status, comment) VALUES (?,?,?,?,?,?,?)",
               (capa_id, u.get("id"), u.get("name"), action, frm, to, comment))


def _payload(c: dict) -> dict:
    return {k: c.get(k) for k in ("id", "supplier_id", "audit_id", "finding_id", "title", "severity", "status", "trigger",
                                  "due_date", "planned_date", "review_comment")}


def create_capa(db, *, supplier_id: int, title: str, severity: str, trigger: str = "manuel", audit_id=None,
                finding_id=None, question_id=None, description=None, due_date=None) -> int:
    cid = db.execute(
        "INSERT INTO capa_items (supplier_id, audit_id, finding_id, question_id, trigger, title, description, severity, due_date) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (supplier_id, audit_id, finding_id, question_id, trigger, title, description, severity, due_date or due_for(severity))).lastrowid
    _event(db, cid, "oluşturuldu", None, "açık", trigger)
    c = row(db.execute("SELECT * FROM capa_items WHERE id=?", (cid,)).fetchone())
    events.emit("capa.created", supplier_id, "capa", cid, _payload(c), db)
    auth.log("capa.create", "capa", cid, {"trigger": trigger, "severity": severity}, db=db)
    return cid


def auto_open_for_audit(db, audit: dict) -> list[int]:
    """Tamamlanan denetim için eksik CAPA'ları (ve bağlı bulguları) açar. Tekrar çağrılabilir (idempotent)."""
    answers = {k: v["answer"] for k, v in audit["answers"].items() if v.get("answer")}
    existing = {r[0] for r in db.execute("SELECT question_id FROM capa_items WHERE audit_id=?", (audit["id"],))}
    created = []
    for c in capa_candidates(audit["checklist"], answers, audit["result"]):
        if c["question_id"] in existing:
            continue
        f = db.execute("SELECT id FROM findings WHERE audit_id=? AND question_id=?", (audit["id"], c["question_id"])).fetchone()
        if f:
            fid = f[0]
        else:
            fid = db.execute(
                "INSERT INTO findings (supplier_id, audit_id, question_id, source, title, severity, description, due_date) "
                "VALUES (?,?,?,?,?,?,?,?)", (audit["supplier_id"], audit["id"], c["question_id"], "denetim", c["title"],
                                             c["severity"], c["description"], due_for(c["severity"]))).lastrowid
            events.emit("finding.created", audit["supplier_id"], "finding", fid,
                        {"id": fid, "title": c["title"], "severity": c["severity"], "source": "denetim", "audit_id": audit["id"]}, db)
        created.append(create_capa(db, supplier_id=audit["supplier_id"], audit_id=audit["id"], finding_id=fid,
                                   question_id=c["question_id"], title=c["title"], severity=c["severity"],
                                   trigger=c["trigger"], description=c["description"]))
    return created


def get_capa(db, cid: int) -> dict:
    c = row(db.execute(
        "SELECT c.*, s.name AS supplier_name, su.name AS submitted_by_name, ru.name AS reviewed_by_name, a.audit_date "
        "FROM capa_items c JOIN suppliers s ON s.id=c.supplier_id LEFT JOIN users su ON su.id=c.submitted_by "
        "LEFT JOIN users ru ON ru.id=c.reviewed_by LEFT JOIN audits a ON a.id=c.audit_id WHERE c.id=?", (cid,)).fetchone())
    if not c:
        raise HTTPException(404, "CAPA bulunamadı")
    ensure_supplier_access(c["supplier_id"])
    c["events"] = rows(db.execute("SELECT * FROM capa_events WHERE capa_id=? ORDER BY id", (cid,)).fetchall())
    c["evidence"] = rows(db.execute(
        "SELECT id, title, original_name, size, mime, kind, uploaded_by_name, created_at FROM evidence_files "
        "WHERE capa_id=? ORDER BY id", (cid,)).fetchall())
    return c


def _transition(db, c: dict, action: str) -> str:
    try:
        return next_status(c["status"], action)
    except ValueError as e:
        raise HTTPException(409, str(e))


# ------------------------------------------------------------------ API
class CapaIn(BaseModel):
    supplier_id: Optional[int] = None
    finding_id: Optional[int] = None
    title: Optional[str] = None
    description: Optional[str] = None
    severity: Literal["kritik", "yüksek", "orta", "düşük"] = "orta"
    due_date: Optional[str] = None


class SubmitIn(BaseModel):
    root_cause: str = Field(min_length=3, max_length=5000)
    corrective_action: str = Field(min_length=3, max_length=5000)
    preventive_action: Optional[str] = Field(default=None, max_length=5000)
    planned_date: Optional[str] = None
    comment: Optional[str] = Field(default=None, max_length=2000)


class ReviewIn(BaseModel):
    decision: Literal["approve", "reject"]
    comment: Optional[str] = Field(default=None, max_length=2000)


@router.get("/api/capa")
def list_capa(status: Optional[str] = None, supplier_id: Optional[int] = None, audit_id: Optional[int] = None):
    q = ("SELECT c.*, s.name AS supplier_name, (SELECT COUNT(*) FROM evidence_files e WHERE e.capa_id=c.id) AS evidence_count "
         "FROM capa_items c JOIN suppliers s ON s.id=c.supplier_id WHERE 1=1")
    p: list = []
    if status == "açık-tümü":
        q += f" AND c.status IN ({','.join('?' * len(OPEN_STATUSES))})"; p += OPEN_STATUSES
    elif status:
        q += " AND c.status=?"; p.append(status)
    if supplier_id:
        q += " AND c.supplier_id=?"; p.append(supplier_id)
    if audit_id:
        q += " AND c.audit_id=?"; p.append(audit_id)
    w, wp = scoped("c.supplier_id")
    q += w + (" ORDER BY CASE c.status WHEN 'onay bekliyor' THEN 0 WHEN 'reddedildi' THEN 1 WHEN 'açık' THEN 2 ELSE 3 END, "
              "CASE c.severity WHEN 'kritik' THEN 0 WHEN 'yüksek' THEN 1 WHEN 'orta' THEN 2 ELSE 3 END, c.due_date")
    with get_db() as db:
        return rows(db.execute(q, p + wp).fetchall())


@router.get("/api/capa/{cid}")
def read_capa(cid: int):
    with get_db() as db:
        return get_capa(db, cid)


@router.post("/api/capa", status_code=201)
def create_manual(body: CapaIn):
    with get_db() as db:
        if body.finding_id:
            f = row(db.execute("SELECT * FROM findings WHERE id=?", (body.finding_id,)).fetchone())
            if not f:
                raise HTTPException(404, "Bulgu bulunamadı")
            if db.execute("SELECT 1 FROM capa_items WHERE finding_id=? AND status NOT IN ('onaylandı','iptal edildi')", (f["id"],)).fetchone():
                raise HTTPException(409, "Bu bulgu için açık bir CAPA zaten var.")
            cid = create_capa(db, supplier_id=f["supplier_id"], finding_id=f["id"], audit_id=f["audit_id"],
                              question_id=None, title=body.title or f["title"], severity=f["severity"],
                              description=body.description or f["description"], due_date=body.due_date or f["due_date"])
        else:
            if not body.supplier_id or not body.title:
                raise HTTPException(422, "Tedarikçi ve başlık gerekli (veya bir bulgu seçin).")
            if not db.execute("SELECT 1 FROM suppliers WHERE id=?", (body.supplier_id,)).fetchone():
                raise HTTPException(404, "Tedarikçi bulunamadı")
            cid = create_capa(db, supplier_id=body.supplier_id, title=body.title, severity=body.severity,
                              description=body.description, due_date=body.due_date)
        return get_capa(db, cid)


@router.post("/api/capa/{cid}/submit")
def submit(cid: int, body: SubmitIn):
    """Tedarikçi kök neden + aksiyon + (gerekirse) kanıt girer ve onaya gönderir."""
    with get_db() as db:
        c = get_capa(db, cid)
        to = _transition(db, c, "submit")
        if c["severity"] == "kritik" and not c["evidence"]:
            raise HTTPException(422, "Kritik CAPA için en az bir kanıt dosyası yükleyin.")
        u = current_user()
        db.execute("UPDATE capa_items SET status=?, root_cause=?, corrective_action=?, preventive_action=?, planned_date=?, "
                   "round=round+1, submitted_at=datetime('now'), submitted_by=? WHERE id=?",
                   (to, body.root_cause, body.corrective_action, body.preventive_action, body.planned_date, u.get("id"), cid))
        if c["finding_id"]:
            db.execute("UPDATE findings SET status=CASE WHEN status='açık' THEN 'devam ediyor' ELSE status END, "
                       "supplier_status='aksiyon planlandı', supplier_comment=?, supplier_due_date=COALESCE(?, supplier_due_date), "
                       "supplier_updated_at=datetime('now') WHERE id=?", (body.corrective_action, body.planned_date, c["finding_id"]))
        _event(db, cid, "gönderildi", c["status"], to, body.comment)
        c = get_capa(db, cid)
        events.emit("capa.submitted", c["supplier_id"], "capa", cid, _payload(c), db)
        auth.log("capa.submit", "capa", cid, {"round": c["round"]}, db=db)
        return c


@router.post("/api/capa/{cid}/review")
def review(cid: int, body: ReviewIn):
    """Denetçi onaylar (bulgu kapanır) veya gerekçeyle reddeder (tedarikçi yeniden gönderir)."""
    with get_db() as db:
        c = get_capa(db, cid)
        to = _transition(db, c, body.decision)
        if body.decision == "reject" and not (body.comment or "").strip():
            raise HTTPException(422, "Red için gerekçe yazın.")
        u = current_user()
        db.execute("UPDATE capa_items SET status=?, reviewed_at=datetime('now'), reviewed_by=?, review_comment=?, "
                   "closed_at=CASE WHEN ?='onaylandı' THEN datetime('now') END WHERE id=?",
                   (to, u.get("id"), body.comment, to, cid))
        if c["finding_id"]:
            if to == "onaylandı":
                old = db.execute("SELECT status FROM findings WHERE id=?", (c["finding_id"],)).fetchone()
                db.execute("UPDATE findings SET status='kapandı', closed_at=datetime('now'), supplier_status='giderildi' WHERE id=?",
                           (c["finding_id"],))
                if old and old[0] != "kapandı":
                    events.emit("finding.status_changed", c["supplier_id"], "finding", c["finding_id"],
                                {"id": c["finding_id"], "from": old[0], "to": "kapandı", "reason": "CAPA onaylandı"}, db)
            else:
                db.execute("UPDATE findings SET status='devam ediyor' WHERE id=? AND status='kapandı'", (c["finding_id"],))
        _event(db, cid, "onaylandı" if to == "onaylandı" else "reddedildi", c["status"], to, body.comment)
        c = get_capa(db, cid)
        events.emit("capa.approved" if to == "onaylandı" else "capa.rejected", c["supplier_id"], "capa", cid, _payload(c), db)
        auth.log(f"capa.{body.decision}", "capa", cid, {"comment": body.comment}, db=db)
        return c


# ------------------------------------------------------------------ mesajlaşma
class CommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


@router.get("/api/capa/{cid}/comments")
def list_comments(cid: int):
    with get_db() as db:
        get_capa(db, cid)
        return rows(db.execute("SELECT * FROM capa_comments WHERE capa_id=? ORDER BY id", (cid,)).fetchall())


@router.post("/api/capa/{cid}/comments", status_code=201)
def add_comment(cid: int, body: CommentIn):
    u = current_user()
    with get_db() as db:
        c = get_capa(db, cid)
        if c["status"] in ("onaylandı", "iptal edildi"):
            raise HTTPException(409, "Kapanmış CAPA'ya mesaj yazılamaz.")
        side = "tedarikçi" if u.get("role") == "supplier" else "kurum"
        mid = db.execute("INSERT INTO capa_comments (capa_id, user_id, author, side, body) VALUES (?,?,?,?,?)",
                         (cid, u.get("id"), u.get("name"), side, body.body.strip())).lastrowid
        events.emit("capa.commented", c["supplier_id"], "capa", cid,
                    {"id": cid, "title": c["title"], "author": u.get("name"), "side": side, "message": body.body.strip()[:500]}, db)
        auth.log("capa.comment", "capa", cid, {"comment_id": mid}, db=db)
        sup = row(db.execute("SELECT name, notify_emails FROM suppliers WHERE id=?", (c["supplier_id"],)).fetchone())
        m = row(db.execute("SELECT * FROM capa_comments WHERE id=?", (mid,)).fetchone())
    if side == "kurum" and sup and sup.get("notify_emails"):
        _mail_supplier(sup, c, body.body.strip(), u.get("name"))
    return m


def _mail_supplier(sup: dict, c: dict, text: str, author: str) -> None:
    """Kurumdan gelen mesajı tedarikçinin bildirim adreslerine e-postayla iletir (SMTP ayarlıysa; hata isteği bozmaz)."""
    from . import notify
    try:
        with get_db() as db:
            s = notify.get_settings(db)
        if not notify.smtp_ready(s):
            return
        subj = f"[{s['org_name'] or 'Tedarikçi Denetim'}] CAPA mesajı: {c['title'][:80]}"
        body = f"{author} yazdı:\n\n{text}\n\nYanıtlamak için tedarikçi portalında CAPA kaydını açın."
        import html as _h
        notify.send_email(s, notify.parse_emails(sup["notify_emails"]), subj, body, f"<p><b>{_h.escape(author)}</b> yazdı:</p>"
                          f"<p style='white-space:pre-wrap'>{_h.escape(text)}</p><p>Yanıtlamak için tedarikçi portalında CAPA kaydını açın.</p>")
    except Exception:
        pass
