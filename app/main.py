"""Tedarikçi Denetim Uygulaması — FastAPI sunucusu."""
from __future__ import annotations

import datetime as dt
import json
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from . import checklist as cl
from .db import get_db, init_db, row, rows
from .intel.scanner import normalize_domain, scan_domain
from . import notify

STATIC = Path(__file__).resolve().parent.parent / "static"

@asynccontextmanager
async def lifespan(_app):
    init_db()
    yield


app = FastAPI(title="Tedarikçi Denetim", version="1.0", lifespan=lifespan)


Severity = Literal["kritik", "yüksek", "orta", "düşük"]
DUE_DAYS = {"kritik": 7, "yüksek": 30, "orta": 60, "düşük": 90}
SEV_ORDER = {"kritik": 0, "yüksek": 1, "orta": 2, "düşük": 3}


def today() -> str:
    return dt.date.today().isoformat()


def due_for(sev: str) -> str:
    return (dt.date.today() + dt.timedelta(days=DUE_DAYS.get(sev, 60))).isoformat()


# ============================================================ Modeller
class SupplierIn(BaseModel):
    name: str = Field(min_length=1)
    category: Optional[str] = None
    country: Optional[str] = None
    contact_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    domains: list[str] = []
    criticality: Literal["düşük", "orta", "yüksek"] = "orta"
    status: Literal["aktif", "askıda", "pasif"] = "aktif"
    notes: Optional[str] = None
    # Bildirim ayarları
    notify_emails: Optional[str] = None     # virgülle ayrılmış
    webhook_url: Optional[str] = None
    webhook_secret: Optional[str] = None
    auto_notify: bool = False

    @field_validator("notify_emails")
    @classmethod
    def _emails(cls, v):
        if not v or not v.strip():
            return None
        try:
            return ", ".join(notify.parse_emails(v))
        except HTTPException as e:
            raise ValueError(e.detail)

    @field_validator("webhook_url")
    @classmethod
    def _webhook(cls, v):
        if not v or not v.strip():
            return None
        v = v.strip()
        if not re.match(r"^https?://[^\s/]+", v):
            raise ValueError("Webhook adresi http:// veya https:// ile başlamalı")
        return v

    @field_validator("domains")
    @classmethod
    def _domains(cls, v):
        out = []
        for d in v:
            if d and d.strip():
                try:
                    n = normalize_domain(d)
                except ValueError as e:
                    raise ValueError(str(e))
                if n not in out:
                    out.append(n)
        return out


class SectionIn(BaseModel):
    title: str = Field(min_length=1)
    weight: float = Field(default=10, gt=0, le=1000)
    questions: list[str]

    @field_validator("questions")
    @classmethod
    def _qs(cls, v):
        v = [q.strip() for q in v if q and q.strip()]
        if not v:
            raise ValueError("Her bölümde en az bir soru olmalı")
        return v


class QuestionSetIn(BaseModel):
    name: str = Field(min_length=1)
    standard: Optional[str] = "Özel"
    description: Optional[str] = None
    sections: list[SectionIn] = Field(min_length=1)


def normalize_sections(sections: list[SectionIn]) -> list[dict]:
    return [{"id": f"s{i}", "title": sec.title.strip(), "weight": sec.weight,
             "questions": [{"id": f"s{i}.{j}", "text": q} for j, q in enumerate(sec.questions, 1)]}
            for i, sec in enumerate(sections, 1)]


class AuditIn(BaseModel):
    question_set_ids: list[int] = []
    custom_sections: list[SectionIn] = []
    save_custom_as: Optional[str] = None   # doluysa özel sorular kütüphaneye set olarak da kaydedilir
    audit_date: str
    auditor: Optional[str] = None
    audit_type: Literal["yerinde", "uzaktan", "doküman"] = "yerinde"
    status: Literal["planlandı", "devam ediyor", "tamamlandı"] = "planlandı"
    notes: Optional[str] = None


class AnswerIn(BaseModel):
    answer: Optional[Literal["uygun", "kısmen", "uygun değil", "na"]] = None
    note: Optional[str] = None


class AuditUpdate(BaseModel):
    audit_date: Optional[str] = None
    auditor: Optional[str] = None
    audit_type: Optional[Literal["yerinde", "uzaktan", "doküman"]] = None
    status: Optional[Literal["planlandı", "devam ediyor", "tamamlandı"]] = None
    notes: Optional[str] = None
    answers: Optional[dict[str, AnswerIn]] = None


class FindingIn(BaseModel):
    supplier_id: int
    audit_id: Optional[int] = None
    title: str = Field(min_length=1)
    severity: Severity = "orta"
    description: Optional[str] = None
    action: Optional[str] = None
    owner: Optional[str] = None
    due_date: Optional[str] = None
    status: Literal["açık", "devam ediyor", "kapandı"] = "açık"


class FindingUpdate(BaseModel):
    title: Optional[str] = None
    severity: Optional[Severity] = None
    description: Optional[str] = None
    action: Optional[str] = None
    owner: Optional[str] = None
    due_date: Optional[str] = None
    status: Optional[Literal["açık", "devam ediyor", "kapandı"]] = None


# ============================================================ Yardımcılar
def _get_supplier(db, sid: int) -> dict:
    s = row(db.execute("SELECT * FROM suppliers WHERE id=?", (sid,)).fetchone())
    if not s:
        raise HTTPException(404, "Tedarikçi bulunamadı")
    return s


def _latest_scans(db, sid: int) -> list[dict]:
    return rows(db.execute(
        """SELECT * FROM intel_scans s WHERE supplier_id=? AND id = (
               SELECT MAX(id) FROM intel_scans WHERE supplier_id=s.supplier_id AND domain=s.domain)
           ORDER BY domain""", (sid,)).fetchall())


def _summary(db, s: dict) -> dict:
    """Tedarikçi için bileşik risk özeti."""
    sid = s["id"]
    last_audit = row(db.execute(
        "SELECT id, audit_date, score FROM audits WHERE supplier_id=? AND status='tamamlandı' AND score IS NOT NULL "
        "ORDER BY audit_date DESC, id DESC LIMIT 1", (sid,)).fetchone())
    next_audit = row(db.execute(
        "SELECT id, audit_date FROM audits WHERE supplier_id=? AND status!='tamamlandı' ORDER BY audit_date LIMIT 1",
        (sid,)).fetchone())
    scans = [x for x in _latest_scans(db, sid) if s["domains"] == [] or x["domain"] in s["domains"]]
    scored = [x["score"] for x in scans if x["score"] is not None]
    intel = round(min(scored), 1) if scored else None  # en zayıf domain belirleyicidir
    f = db.execute(
        """SELECT
             SUM(status!='kapandı') AS open,
             SUM(status!='kapandı' AND severity='kritik') AS critical,
             SUM(status!='kapandı' AND due_date IS NOT NULL AND due_date < ?) AS overdue
           FROM findings WHERE supplier_id=?""", (today(), sid)).fetchone()
    audit = last_audit["score"] if last_audit else None
    parts = [(audit, 0.6), (intel, 0.4)]
    parts = [(v, w) for v, w in parts if v is not None]
    overall = round(sum(v * w for v, w in parts) / sum(w for _, w in parts), 1) if parts else None

    critical = f["critical"] or 0
    if overall is None:
        risk = "bilinmiyor"
    elif critical or overall < 50 or (s["criticality"] == "yüksek" and overall < 70):
        risk = "yüksek"
    elif overall < 75 or (f["overdue"] or 0) > 0:
        risk = "orta"
    else:
        risk = "düşük"
    return {
        "audit_score": audit, "audit_rating": cl.rating(audit),
        "last_audit_date": last_audit["audit_date"] if last_audit else None,
        "next_audit_date": next_audit["audit_date"] if next_audit else None,
        "intel_score": intel,
        "intel_grade": min((x["grade"] for x in scans if x["grade"]), default=None, key=lambda g: -ord(g)),
        "last_scan": max((x["scanned_at"] for x in scans), default=None),
        "open_findings": f["open"] or 0, "critical_findings": critical, "overdue_findings": f["overdue"] or 0,
        "overall_score": overall, "risk": risk,
    }


# ============================================================ Meta
@app.get("/api/meta")
def meta():
    return {"due_days": DUE_DAYS}


# ============================================================ Soru setleri
def _get_set(db, qid: int) -> dict:
    qs = row(db.execute("SELECT * FROM question_sets WHERE id=?", (qid,)).fetchone())
    if not qs:
        raise HTTPException(404, "Soru seti bulunamadı")
    qs["builtin"] = bool(qs["builtin"])
    qs["question_count"] = sum(len(sec["questions"]) for sec in qs["sections"])
    return qs


@app.get("/api/question-sets")
def list_question_sets():
    with get_db() as db:
        ids = [r[0] for r in db.execute("SELECT id FROM question_sets ORDER BY builtin DESC, CASE WHEN builtin=1 THEN id END, name COLLATE NOCASE")]
        out = []
        for i in ids:
            qs = _get_set(db, i)
            qs["used_in"] = db.execute(
                "SELECT COUNT(*) FROM audits WHERE set_names LIKE ?", (f'%"{qs["name"]}"%',)).fetchone()[0]
            out.append(qs)
        return out


@app.get("/api/question-sets/{qid}")
def get_question_set(qid: int):
    with get_db() as db:
        return _get_set(db, qid)


@app.post("/api/question-sets", status_code=201)
def create_question_set(body: QuestionSetIn):
    with get_db() as db:
        _check_name(db, body.name)
        cur = db.execute(
            "INSERT INTO question_sets (name, standard, description, builtin, sections) VALUES (?,?,?,0,?)",
            (body.name.strip(), (body.standard or "Özel").strip(), body.description,
             json.dumps(normalize_sections(body.sections), ensure_ascii=False)))
        return _get_set(db, cur.lastrowid)


@app.put("/api/question-sets/{qid}")
def update_question_set(qid: int, body: QuestionSetIn):
    with get_db() as db:
        if _get_set(db, qid)["builtin"]:
            raise HTTPException(403, "Standart setler değiştirilemez; kopyalayıp düzenleyin.")
        _check_name(db, body.name, qid)
        db.execute(
            "UPDATE question_sets SET name=?, standard=?, description=?, sections=?, updated_at=datetime('now') WHERE id=?",
            (body.name.strip(), (body.standard or "Özel").strip(), body.description,
             json.dumps(normalize_sections(body.sections), ensure_ascii=False), qid))
        return _get_set(db, qid)


@app.delete("/api/question-sets/{qid}", status_code=204)
def delete_question_set(qid: int):
    with get_db() as db:
        if _get_set(db, qid)["builtin"]:
            raise HTTPException(403, "Standart setler silinemez.")
        db.execute("DELETE FROM question_sets WHERE id=?", (qid,))


@app.post("/api/question-sets/{qid}/clone", status_code=201)
def clone_question_set(qid: int):
    with get_db() as db:
        qs = _get_set(db, qid)
        name, n = f"{qs['name']} (kopya)", 2
        while db.execute("SELECT 1 FROM question_sets WHERE lower(name)=lower(?)", (name,)).fetchone():
            name, n = f"{qs['name']} (kopya {n})", n + 1
        cur = db.execute(
            "INSERT INTO question_sets (name, standard, description, builtin, sections) VALUES (?,?,?,0,?)",
            (name, qs["standard"], qs["description"], json.dumps(qs["sections"], ensure_ascii=False)))
        return _get_set(db, cur.lastrowid)


def _check_name(db, name: str, exclude_id: int | None = None):
    r = db.execute("SELECT id FROM question_sets WHERE lower(name)=lower(?) AND id IS NOT ?", (name.strip(), exclude_id)).fetchone()
    if r:
        raise HTTPException(409, f"'{name.strip()}' adında bir soru seti zaten var; farklı bir ad seçin.")


def _build_checklist(db, body: "AuditIn") -> tuple[list[dict], list[str]]:
    """Seçilen setlerin ve özel soruların anlık kopyasını oluşturur. Soru id'leri denetim içinde benzersizdir."""
    sections, names = [], []
    for qid in dict.fromkeys(body.question_set_ids):
        qs = _get_set(db, qid)
        names.append(qs["name"])
        for sec in qs["sections"]:
            sections.append({
                "id": f"q{qid}.{sec['id']}", "title": sec["title"], "weight": sec["weight"], "source": qs["name"],
                "questions": [{"id": f"q{qid}.{q['id']}", "text": q["text"]} for q in sec["questions"]],
            })
    if body.custom_sections:
        label = (body.save_custom_as or "").strip() or "Özel sorular"
        names.append(label)
        for sec in normalize_sections(body.custom_sections):
            sections.append({
                "id": f"c.{sec['id']}", "title": sec["title"], "weight": sec["weight"], "source": label,
                "questions": [{"id": f"c.{q['id']}", "text": q["text"]} for q in sec["questions"]],
            })
        if body.save_custom_as and body.save_custom_as.strip():
            _check_name(db, body.save_custom_as)
            db.execute(
                "INSERT INTO question_sets (name, standard, description, builtin, sections) VALUES (?,?,?,0,?)",
                (label, "Özel", "Denetim planlanırken oluşturuldu.",
                 json.dumps(normalize_sections(body.custom_sections), ensure_ascii=False)))
    if not sections:
        raise HTTPException(422, "En az bir soru seti seçin veya özel soru ekleyin.")
    return sections, names


# ============================================================ Tedarikçiler
@app.get("/api/suppliers")
def list_suppliers():
    with get_db() as db:
        out = []
        for s in rows(db.execute("SELECT * FROM suppliers ORDER BY name COLLATE NOCASE").fetchall()):
            s["summary"] = _summary(db, s)
            out.append(s)
        return out


@app.post("/api/suppliers", status_code=201)
def create_supplier(body: SupplierIn):
    d = body.model_dump()
    d["domains"] = json.dumps(d["domains"])
    with get_db() as db:
        cur = db.execute(
            f"INSERT INTO suppliers ({', '.join(d)}) VALUES ({', '.join('?' * len(d))})", list(d.values()))
        return _get_supplier(db, cur.lastrowid)


@app.get("/api/suppliers/{sid}")
def get_supplier(sid: int):
    with get_db() as db:
        s = _get_supplier(db, sid)
        s["summary"] = _summary(db, s)
        s["audits"] = rows(db.execute(
            "SELECT * FROM audits WHERE supplier_id=? ORDER BY audit_date DESC, id DESC", (sid,)).fetchall())
        s["findings"] = rows(db.execute(
            "SELECT * FROM findings WHERE supplier_id=? ORDER BY status='kapandı', "
            "CASE severity WHEN 'kritik' THEN 0 WHEN 'yüksek' THEN 1 WHEN 'orta' THEN 2 ELSE 3 END, due_date",
            (sid,)).fetchall())
        s["scans"] = _latest_scans(db, sid)
        s["scan_history"] = rows(db.execute(
            "SELECT id, domain, scanned_at, score, grade FROM intel_scans WHERE supplier_id=? ORDER BY id DESC LIMIT 50",
            (sid,)).fetchall())
        return s


@app.put("/api/suppliers/{sid}")
def update_supplier(sid: int, body: SupplierIn):
    d = body.model_dump()
    d["domains"] = json.dumps(d["domains"])
    with get_db() as db:
        _get_supplier(db, sid)
        db.execute(f"UPDATE suppliers SET {', '.join(k + '=?' for k in d)} WHERE id=?", [*d.values(), sid])
        return _get_supplier(db, sid)


@app.delete("/api/suppliers/{sid}", status_code=204)
def delete_supplier(sid: int):
    with get_db() as db:
        _get_supplier(db, sid)
        db.execute("DELETE FROM suppliers WHERE id=?", (sid,))


# ============================================================ Denetimler
@app.get("/api/audits")
def list_audits():
    with get_db() as db:
        return rows(db.execute(
            "SELECT a.*, s.name AS supplier_name FROM audits a JOIN suppliers s ON s.id=a.supplier_id "
            "ORDER BY a.audit_date DESC, a.id DESC").fetchall())


@app.post("/api/suppliers/{sid}/audits", status_code=201)
def create_audit(sid: int, body: AuditIn):
    with get_db() as db:
        _get_supplier(db, sid)
        sections, names = _build_checklist(db, body)
        d = body.model_dump()
        cur = db.execute(
            "INSERT INTO audits (supplier_id, audit_date, auditor, audit_type, status, notes, checklist, set_names) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (sid, d["audit_date"], d["auditor"], d["audit_type"], d["status"], d["notes"],
             json.dumps(sections, ensure_ascii=False), json.dumps(names, ensure_ascii=False)))
        return get_audit(cur.lastrowid, db)


@app.get("/api/audits/{aid}")
def get_audit(aid: int, db=None):
    def _do(db):
        a = row(db.execute(
            "SELECT a.*, s.name AS supplier_name FROM audits a JOIN suppliers s ON s.id=a.supplier_id WHERE a.id=?",
            (aid,)).fetchone())
        if not a:
            raise HTTPException(404, "Denetim bulunamadı")
        ans = db.execute("SELECT question_id, answer, note FROM audit_answers WHERE audit_id=?", (aid,)).fetchall()
        a["answers"] = {r["question_id"]: {"answer": r["answer"], "note": r["note"]} for r in ans}
        if not a.get("checklist"):
            a["checklist"] = cl.legacy_sections()
            a["set_names"] = ["Genel Tedarikçi Denetimi"]
        a["result"] = cl.compute_score({k: v["answer"] for k, v in a["answers"].items()}, a["checklist"])
        a["findings"] = rows(db.execute("SELECT * FROM findings WHERE audit_id=?", (aid,)).fetchall())
        return a
    if db is not None:
        return _do(db)
    with get_db() as db:
        return _do(db)


@app.put("/api/audits/{aid}")
def update_audit(aid: int, body: AuditUpdate):
    with get_db() as db:
        current = get_audit(aid, db)
        fields = body.model_dump(exclude_unset=True, exclude={"answers"})
        if fields:
            db.execute(f"UPDATE audits SET {', '.join(k + '=?' for k in fields)} WHERE id=?", [*fields.values(), aid])
        if body.answers is not None:
            valid = {q["id"] for sec in current["checklist"] for q in sec["questions"]}
            for qid, a in body.answers.items():
                if qid not in valid:
                    raise HTTPException(422, f"Bilinmeyen soru: {qid}")
                db.execute(
                    "INSERT INTO audit_answers (audit_id, question_id, answer, note) VALUES (?,?,?,?) "
                    "ON CONFLICT(audit_id, question_id) DO UPDATE SET answer=excluded.answer, note=excluded.note",
                    (aid, qid, a.answer, a.note))
        a = get_audit(aid, db)
        db.execute("UPDATE audits SET score=? WHERE id=?", (a["result"]["score"], aid))
        a["score"] = a["result"]["score"]
        return a


@app.delete("/api/audits/{aid}", status_code=204)
def delete_audit(aid: int):
    with get_db() as db:
        get_audit(aid, db)
        db.execute("DELETE FROM audits WHERE id=?", (aid,))


@app.post("/api/audits/{aid}/generate-findings")
async def generate_audit_findings(aid: int):
    """'Uygun değil' ve 'kısmen' cevaplarından bulgu (DÖF) oluşturur. Mevcut olanları tekrar oluşturmaz.
    Tedarikçide otomatik bildirim açıksa yeni bulgular tedarikçiye bildirilir."""
    created = 0
    new_ids: list[int] = []
    with get_db() as db:
        a = get_audit(aid, db)
        existing = {f["title"] for f in a["findings"]}
        sec_title = {q["id"]: sec["title"] for sec in a["checklist"] for q in sec["questions"]}
        q_text = {q["id"]: q["text"] for sec in a["checklist"] for q in sec["questions"]}
        for qid, v in a["answers"].items():
            if v["answer"] not in ("uygun değil", "kısmen"):
                continue
            sev = "yüksek" if v["answer"] == "uygun değil" else "orta"
            if qid not in q_text:
                continue
            title = f"[{sec_title[qid]}] {q_text[qid]}"
            if title in existing:
                continue
            new_ids.append(db.execute(
                "INSERT INTO findings (supplier_id, audit_id, source, title, severity, description, due_date) "
                "VALUES (?,?,?,?,?,?,?)",
                (a["supplier_id"], aid, "denetim", title, sev,
                 f"Denetim cevabı: {v['answer']}" + (f"\nNot: {v['note']}" if v.get("note") else ""), due_for(sev))).lastrowid)
            created += 1
        supplier_id = a["supplier_id"]
    sent = await notify.auto_notify(supplier_id, new_ids)
    return {"created": created, "notified": [n["channel"] for n in sent if n["status"] == "gönderildi"]}


# ============================================================ Bulgular / Aksiyonlar
@app.get("/api/findings")
def list_findings(status: Optional[str] = None, supplier_id: Optional[int] = None, source: Optional[str] = None):
    q = "SELECT f.*, s.name AS supplier_name FROM findings f JOIN suppliers s ON s.id=f.supplier_id WHERE 1=1"
    params: list = []
    if status == "açık-tümü":
        q += " AND f.status != 'kapandı'"
    elif status:
        q += " AND f.status=?"; params.append(status)
    if supplier_id:
        q += " AND f.supplier_id=?"; params.append(supplier_id)
    if source:
        q += " AND f.source=?"; params.append(source)
    q += (" ORDER BY f.status='kapandı', CASE f.severity WHEN 'kritik' THEN 0 WHEN 'yüksek' THEN 1 "
          "WHEN 'orta' THEN 2 ELSE 3 END, f.due_date")
    with get_db() as db:
        return rows(db.execute(q, params).fetchall())


@app.post("/api/findings", status_code=201)
def create_finding(body: FindingIn):
    d = body.model_dump()
    d["due_date"] = d["due_date"] or due_for(d["severity"])
    d["source"] = "denetim" if d["audit_id"] else "manuel"
    with get_db() as db:
        _get_supplier(db, d["supplier_id"])
        cur = db.execute(f"INSERT INTO findings ({', '.join(d)}) VALUES ({', '.join('?' * len(d))})", list(d.values()))
        return row(db.execute("SELECT * FROM findings WHERE id=?", (cur.lastrowid,)).fetchone())


@app.put("/api/findings/{fid}")
def update_finding(fid: int, body: FindingUpdate):
    d = body.model_dump(exclude_unset=True)
    with get_db() as db:
        f = row(db.execute("SELECT * FROM findings WHERE id=?", (fid,)).fetchone())
        if not f:
            raise HTTPException(404, "Bulgu bulunamadı")
        if "status" in d:
            d["closed_at"] = dt.datetime.now().isoformat(timespec="seconds") if d["status"] == "kapandı" else None
        if d:
            db.execute(f"UPDATE findings SET {', '.join(k + '=?' for k in d)} WHERE id=?", [*d.values(), fid])
        return row(db.execute("SELECT * FROM findings WHERE id=?", (fid,)).fetchone())


@app.delete("/api/findings/{fid}", status_code=204)
def delete_finding(fid: int):
    with get_db() as db:
        db.execute("DELETE FROM findings WHERE id=?", (fid,))


# ============================================================ Dış istihbarat
def _store_scan(db, sid: int, result: dict, create_findings: bool) -> dict:
    cur = db.execute(
        "INSERT INTO intel_scans (supplier_id, domain, scanned_at, score, grade, result) VALUES (?,?,?,?,?,?)",
        (sid, result["domain"], result["scanned_at"], result["score"], result["grade"],
         json.dumps(result, ensure_ascii=False)))
    scan_id = cur.lastrowid
    created = closed = 0
    new_ids: list[int] = []
    if create_findings:
        prefix = f"[{result['domain']}] "
        open_intel = {f["title"]: f for f in rows(db.execute(
            "SELECT * FROM findings WHERE supplier_id=? AND source='istihbarat' AND status!='kapandı' AND title LIKE ?",
            (sid, prefix + "%")).fetchall())}
        status_by_title = {prefix + c["title"]: c["status"] for c in result["checks"]}
        for c in result["checks"]:
            title = prefix + c["title"]
            if c["status"] == "fail" and title not in open_intel:
                new_ids.append(db.execute(
                    "INSERT INTO findings (supplier_id, scan_id, source, title, severity, description, action, due_date) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (sid, scan_id, "istihbarat", title, c["severity"], c["detail"], c["recommendation"],
                     due_for(c["severity"]))).lastrowid)
                created += 1
        # Önceki taramada başarısız olup artık geçen kontroller -> otomatik kapat
        for title, f in open_intel.items():
            if status_by_title.get(title) == "pass":
                db.execute(
                    "UPDATE findings SET status='kapandı', closed_at=?, description=COALESCE(description,'') || ? WHERE id=?",
                    (dt.datetime.now().isoformat(timespec="seconds"),
                     f"\n[Otomatik] {result['scanned_at'][:10]} taramasında giderildiği doğrulandı.", f["id"]))
                closed += 1
    return {"scan_id": scan_id, "findings_created": created, "findings_closed": closed, "new_finding_ids": new_ids}


@app.post("/api/suppliers/{sid}/scan")
async def scan_supplier(sid: int, create_findings: bool = True, domain: Optional[str] = None):
    with get_db() as db:
        s = _get_supplier(db, sid)
    domains = [normalize_domain(domain)] if domain else s["domains"]
    if not domains:
        raise HTTPException(400, "Bu tedarikçi için tanımlı domain yok.")
    out = []
    for d in domains:
        result = await scan_domain(d)
        with get_db() as db:
            meta = _store_scan(db, sid, result, create_findings)
        sent = await notify.auto_notify(sid, meta.pop("new_finding_ids"), meta["scan_id"])
        meta["notified"] = [n["channel"] for n in sent if n["status"] == "gönderildi"]
        out.append({**meta, "domain": d, "score": result["score"], "grade": result["grade"], "summary": result["summary"]})
    return out


@app.get("/api/scans/{scan_id}")
def get_scan(scan_id: int):
    with get_db() as db:
        s = row(db.execute("SELECT * FROM intel_scans WHERE id=?", (scan_id,)).fetchone())
        if not s:
            raise HTTPException(404, "Tarama bulunamadı")
        return s


@app.get("/api/quick-scan")
async def quick_scan(domain: str):
    """Kaydetmeden tek bir domain'i tarar (ön değerlendirme / aday tedarikçi)."""
    try:
        return await scan_domain(domain)
    except ValueError as e:
        raise HTTPException(422, str(e))


# ============================================================ Pano
@app.get("/api/dashboard")
def dashboard():
    with get_db() as db:
        sups = []
        for s in rows(db.execute("SELECT * FROM suppliers WHERE status!='pasif'").fetchall()):
            s["summary"] = _summary(db, s)
            sups.append(s)
        risk = {k: 0 for k in ("yüksek", "orta", "düşük", "bilinmiyor")}
        grades = {k: 0 for k in "ABCDF"}
        for s in sups:
            risk[s["summary"]["risk"]] += 1
            if s["summary"]["intel_grade"]:
                grades[s["summary"]["intel_grade"]] += 1
        sev = {r["severity"]: r["n"] for r in db.execute(
            "SELECT severity, COUNT(*) n FROM findings WHERE status!='kapandı' GROUP BY severity")}
        overdue = rows(db.execute(
            "SELECT f.*, s.name AS supplier_name FROM findings f JOIN suppliers s ON s.id=f.supplier_id "
            "WHERE f.status!='kapandı' AND f.due_date < ? ORDER BY f.due_date LIMIT 20", (today(),)).fetchall())
        upcoming = rows(db.execute(
            "SELECT a.*, s.name AS supplier_name FROM audits a JOIN suppliers s ON s.id=a.supplier_id "
            "WHERE a.status!='tamamlandı' ORDER BY a.audit_date LIMIT 10").fetchall())
        stale = [
            {"id": s["id"], "name": s["name"], "last_audit_date": s["summary"]["last_audit_date"]}
            for s in sups
            if s["criticality"] != "düşük" and (not s["summary"]["last_audit_date"] or
                s["summary"]["last_audit_date"] < (dt.date.today() - dt.timedelta(days=365)).isoformat())
        ]
        ranking = sorted(
            [{"id": s["id"], "name": s["name"], "criticality": s["criticality"], **s["summary"]} for s in sups],
            key=lambda x: ({"yüksek": 0, "orta": 1, "düşük": 2, "bilinmiyor": 3}[x["risk"]],
                           x["overall_score"] if x["overall_score"] is not None else 999))
        closed_30 = db.execute(
            "SELECT COUNT(*) FROM findings WHERE status='kapandı' AND closed_at >= ?",
            ((dt.date.today() - dt.timedelta(days=30)).isoformat(),)).fetchone()[0]
        return {
            "totals": {
                "suppliers": len(sups),
                "open_findings": sum(sev.values()),
                "overdue_findings": len(overdue),
                "closed_last_30": closed_30,
                "audits_planned": len(upcoming),
            },
            "risk": risk, "grades": grades, "severity": {k: sev.get(k, 0) for k in SEV_ORDER},
            "overdue": overdue, "upcoming": upcoming, "stale": stale, "ranking": ranking,
        }


# ============================================================ Statik arayüz
app.include_router(notify.router)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/yanit/{token}", include_in_schema=False)
def response_page(token: str):
    """Tedarikçinin bildirime yanıt verdiği sayfa (giriş gerektirmez, token ile erişilir)."""
    return FileResponse(STATIC / "yanit.html", headers={"Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex"})


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")
