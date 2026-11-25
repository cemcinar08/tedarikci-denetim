"""Tedarikçi yaşam döngüsü: kabul (onboarding) + doğal risk, çıkarma (offboarding) kontrol listesi, sözleşmeler."""
from __future__ import annotations

import datetime as dt
import json
import re
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import auth
from .auth import ensure_supplier_access, scoped
from .db import get_db, row, rows

router = APIRouter()
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# ------------------------------------------------------------------ doğal risk (inherent risk) — saf
INTAKE_QUESTIONS = [
    {"key": "data", "text": "Tedarikçi hangi verilere erişecek?", "options": [
        ("yok", "Veri erişimi yok", 0), ("dahili", "Dahili / gizli olmayan iş verisi", 10),
        ("kisisel", "Kişisel veri (KVKK)", 25), ("hassas", "Özel nitelikli kişisel veri, kart verisi veya ticari sır", 35)]},
    {"key": "access", "text": "Sistemlerimize erişimi ne düzeyde?", "options": [
        ("yok", "Erişim yok", 0), ("uzak", "Sınırlı / uzaktan kullanıcı erişimi", 10),
        ("ag", "Ağımıza bağlantı veya entegrasyon (API)", 18), ("yetkili", "Yönetici / ayrıcalıklı erişim", 25)]},
    {"key": "criticality", "text": "Durursa operasyonumuz nasıl etkilenir?", "options": [
        ("dusuk", "Etkilenmez", 0), ("orta", "Kısa süreli aksama", 8), ("yuksek", "Üretim / hizmet durur", 18)]},
    {"key": "substitute", "text": "Yerine başka tedarikçi bulmak ne kadar kolay?", "options": [
        ("kolay", "Kolay (günler)", 0), ("orta", "Orta (haftalar)", 4), ("zor", "Zor / tek kaynak (aylar)", 8)]},
    {"key": "spend", "text": "Yıllık harcama büyüklüğü?", "options": [
        ("dusuk", "< 1 milyon TL", 0), ("orta", "1–10 milyon TL", 3), ("yuksek", "> 10 milyon TL", 6)]},
    {"key": "location", "text": "Hizmet / veri nerede?", "options": [
        ("yurtici", "Yurt içi", 0), ("yurtdisi", "Yurt dışı (veri aktarımı olabilir)", 4)]},
]
REGULATIONS = {"kvkk": "KVKK", "pci": "PCI DSS (kart verisi)", "dora": "DORA (AB finans)", "nis2": "NIS2", "iso9001": "Kalite (ISO 9001)",
               "cevre": "Çevre (ISO 14001)", "isg": "İSG (ISO 45001)"}
TEMPLATE_FOR = {"kvkk": "kvkk", "pci": "pci-dss-4", "iso9001": "iso-9001-2015", "cevre": "iso-14001-2015", "isg": "iso-45001-2018",
                "dora": "dora", "nis2": "nis2"}


def inherent_risk(answers: dict[str, str], regulations: list[str] | None = None) -> dict:
    """Saf: kabul formu cevaplarından doğal risk puanı (0-100), önerilen tier ve soru setleri."""
    score, reasons = 0, []
    for q in INTAKE_QUESTIONS:
        opt = next((o for o in q["options"] if o[0] == answers.get(q["key"])), None)
        if opt is None:
            raise ValueError(f"'{q['text']}' sorusu cevaplanmadı")
        score += opt[2]
        if opt[2] >= 15:
            reasons.append(opt[1])
    regs = [r for r in (regulations or []) if r in REGULATIONS]
    if {"pci", "dora", "nis2"} & set(regs):
        score += 5
        reasons.append("Düzenlemeye tabi hizmet (" + ", ".join(REGULATIONS[r] for r in regs if r in ("pci", "dora", "nis2")) + ")")
    score = min(score, 100)
    level = 1 if score >= 50 else 2 if score >= 25 else 3
    # Hassas veri veya ayrıcalıklı erişim tek başına Tier 1 sebebidir
    if answers.get("data") == "hassas" or answers.get("access") == "yetkili":
        level = 1
    templates = ["genel"]
    if answers.get("data") in ("kisisel", "hassas") or answers.get("access") in ("ag", "yetkili"):
        templates.append("iso-27001-2022")
    if answers.get("data") in ("kisisel", "hassas") and "kvkk" not in regs:
        regs = regs + ["kvkk"]
    templates += [TEMPLATE_FOR[r] for r in regs if r in TEMPLATE_FOR]
    criticality = {1: "yüksek", 2: "orta", 3: "düşük"}[level]
    return {"score": score, "tier_level": level, "criticality": criticality, "reasons": reasons,
            "templates": list(dict.fromkeys(templates)), "regulations": regs}


# ------------------------------------------------------------------ çıkarma kontrol listesi — saf
OFFBOARDING_ITEMS = [
    ("access", "Sistem, VPN, hesap ve API anahtarı erişimleri kaldırıldı"),
    ("data", "Verilerimiz iade edildi veya imha edildi (imha tutanağı alındı)"),
    ("assets", "Ekipman, kart, belge ve fiziksel erişimler iade alındı"),
    ("contract", "Sözleşme feshedildi / yenilenmeyecek olarak işaretlendi"),
    ("finance", "Açık fatura ve ödemeler kapatıldı"),
    ("capa", "Açık CAPA ve bulgular kapatıldı veya risk kabulüne alındı"),
    ("confidentiality", "Süren gizlilik yükümlülükleri tedarikçiye yazılı hatırlatıldı"),
]


def new_checklist() -> list[dict]:
    return [{"key": k, "label": label, "done": False, "done_by": None, "done_at": None, "note": None} for k, label in OFFBOARDING_ITEMS]


# ------------------------------------------------------------------ sözleşme durumu — saf
def contract_status(c: dict, today: dt.date, warn_days: int = 30) -> dict:
    """Saf: {status, days_left, notice_deadline}. Fesih ihbar süresi varsa uyarı ihbar tarihinden önce başlar."""
    if not c.get("end_date"):
        return {"status": "süresiz", "days_left": None, "notice_deadline": None}
    end = dt.date.fromisoformat(c["end_date"])
    notice = end - dt.timedelta(days=int(c.get("notice_days") or 0))
    days = (end - today).days
    if c.get("start_date") and dt.date.fromisoformat(c["start_date"]) > today:
        st = "başlamadı"
    elif days < 0:
        st = "süresi doldu"
    elif (notice - today).days <= warn_days:
        st = "otomatik yenilenecek" if c.get("auto_renew") else "bitiyor"
    else:
        st = "aktif"
    return {"status": st, "days_left": days, "notice_deadline": notice.isoformat() if c.get("notice_days") else None}


# ------------------------------------------------------------------ API: kabul
class IntakeIn(BaseModel):
    answers: dict[str, str]
    regulations: list[str] = []
    notes: Optional[str] = Field(default=None, max_length=2000)


class OnboardIn(IntakeIn):
    name: str = Field(min_length=1)
    category: Optional[str] = None
    country: Optional[str] = None
    contact_name: Optional[str] = None
    email: Optional[str] = None
    domains: list[str] = []
    apply_suggestion: bool = True


def _eval(body: IntakeIn) -> dict:
    try:
        return inherent_risk(body.answers, body.regulations)
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.get("/api/onboarding/questions")
def intake_questions():
    return {"questions": [{"key": q["key"], "text": q["text"], "options": [{"value": v, "label": lb} for v, lb, _ in q["options"]]}
                          for q in INTAKE_QUESTIONS], "regulations": REGULATIONS}


@router.post("/api/onboarding/preview")
def intake_preview(body: IntakeIn):
    r = _eval(body)
    with get_db() as db:
        r["tier"] = row(db.execute("SELECT id, name FROM supplier_tiers WHERE level=? ORDER BY id LIMIT 1", (r["tier_level"],)).fetchone())
        r["template_ids"] = [x[0] for x in db.execute(
            f"SELECT id FROM audit_templates WHERE key IN ({','.join('?' * len(r['templates']))})", r["templates"])]
    return r


def _save_intake(db, sid: int, body: IntakeIn, r: dict) -> None:
    db.execute("INSERT INTO supplier_intake (supplier_id, answers, regulations, score, tier_level, templates, notes, created_by) "
               "VALUES (?,?,?,?,?,?,?,?)", (sid, json.dumps(body.answers), json.dumps(r["regulations"]), r["score"], r["tier_level"],
                                            json.dumps(r["templates"]), body.notes, auth.current_user().get("name")))


@router.post("/api/onboarding", status_code=201)
def onboard(body: OnboardIn):
    """Yeni tedarikçiyi 'aday' durumunda oluşturur, doğal risk değerlendirmesini kaydeder ve tier/kritiklik önerisini uygular."""
    from .intel.scanner import normalize_domain
    r = _eval(body)
    doms = []
    for d in body.domains:
        if d.strip():
            try:
                doms.append(normalize_domain(d))
            except ValueError as e:
                raise HTTPException(422, str(e))
    with get_db() as db:
        if db.execute("SELECT 1 FROM suppliers WHERE lower(name)=lower(?)", (body.name.strip(),)).fetchone():
            raise HTTPException(409, "Bu adla bir tedarikçi zaten var.")
        tier = db.execute("SELECT id FROM supplier_tiers WHERE level=? ORDER BY id LIMIT 1", (r["tier_level"],)).fetchone()
        sid = db.execute(
            "INSERT INTO suppliers (name, category, country, contact_name, email, domains, criticality, tier_id, status) "
            "VALUES (?,?,?,?,?,?,?,?, 'aday')",
            (body.name.strip(), body.category, body.country, body.contact_name, body.email, json.dumps(list(dict.fromkeys(doms))),
             r["criticality"] if body.apply_suggestion else "orta", tier[0] if (tier and body.apply_suggestion) else None)).lastrowid
        _save_intake(db, sid, body, r)
        auth.log("supplier.onboard", "supplier", sid, {"score": r["score"], "tier_level": r["tier_level"]}, db=db)
    return {"supplier_id": sid, **r}


@router.get("/api/suppliers/{sid}/intake")
def get_intake(sid: int):
    ensure_supplier_access(sid)
    with get_db() as db:
        r = row(db.execute("SELECT * FROM supplier_intake WHERE supplier_id=? ORDER BY id DESC LIMIT 1", (sid,)).fetchone())
    if r:
        for k in ("answers", "regulations", "templates"):
            r[k] = json.loads(r[k] or "[]")
    return r


@router.post("/api/suppliers/{sid}/intake", status_code=201)
def redo_intake(sid: int, body: IntakeIn):
    r = _eval(body)
    with get_db() as db:
        if not db.execute("SELECT 1 FROM suppliers WHERE id=?", (sid,)).fetchone():
            raise HTTPException(404, "Tedarikçi bulunamadı")
        _save_intake(db, sid, body, r)
    return {**get_intake(sid), "suggestion": r}


@router.post("/api/suppliers/{sid}/activate")
def activate(sid: int):
    with get_db() as db:
        s = db.execute("SELECT status FROM suppliers WHERE id=?", (sid,)).fetchone()
        if not s:
            raise HTTPException(404, "Tedarikçi bulunamadı")
        if s[0] != "aday":
            raise HTTPException(409, "Yalnızca aday tedarikçi onaylanabilir.")
        if not db.execute("SELECT 1 FROM supplier_intake WHERE supplier_id=?", (sid,)).fetchone():
            raise HTTPException(422, "Önce doğal risk değerlendirmesini (kabul formu) tamamlayın.")
        db.execute("UPDATE suppliers SET status='aktif' WHERE id=?", (sid,))
        auth.log("supplier.activate", "supplier", sid, db=db)
    return {"ok": True}


# ------------------------------------------------------------------ API: çıkarma
class OffboardIn(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


class ItemIn(BaseModel):
    done: bool
    note: Optional[str] = Field(default=None, max_length=1000)


def _offb(db, sid: int) -> Optional[dict]:
    o = row(db.execute("SELECT * FROM offboarding WHERE supplier_id=? ORDER BY id DESC LIMIT 1", (sid,)).fetchone())
    if o:
        o["items"] = json.loads(o["items"])
        o["progress"] = sum(i["done"] for i in o["items"])
    return o


@router.get("/api/suppliers/{sid}/offboarding")
def get_offboarding(sid: int):
    ensure_supplier_access(sid)
    with get_db() as db:
        return _offb(db, sid)


@router.post("/api/suppliers/{sid}/offboarding", status_code=201)
def start_offboarding(sid: int, body: OffboardIn):
    with get_db() as db:
        s = db.execute("SELECT status FROM suppliers WHERE id=?", (sid,)).fetchone()
        if not s:
            raise HTTPException(404, "Tedarikçi bulunamadı")
        cur = _offb(db, sid)
        if cur and not cur["completed_at"]:
            raise HTTPException(409, "Çıkarma süreci zaten devam ediyor.")
        db.execute("INSERT INTO offboarding (supplier_id, reason, items, started_by) VALUES (?,?,?,?)",
                   (sid, body.reason, json.dumps(new_checklist(), ensure_ascii=False), auth.current_user().get("name")))
        db.execute("UPDATE suppliers SET status='çıkış sürecinde' WHERE id=?", (sid,))
        auth.log("supplier.offboarding_start", "supplier", sid, {"reason": body.reason}, db=db)
        return _offb(db, sid)


@router.put("/api/suppliers/{sid}/offboarding/items/{key}")
def update_item(sid: int, key: str, body: ItemIn):
    with get_db() as db:
        o = _offb(db, sid)
        if not o or o["completed_at"]:
            raise HTTPException(409, "Devam eden çıkarma süreci yok.")
        it = next((i for i in o["items"] if i["key"] == key), None)
        if not it:
            raise HTTPException(404, "Madde bulunamadı")
        it.update(done=body.done, note=body.note, done_by=auth.current_user().get("name") if body.done else None,
                  done_at=dt.datetime.now().isoformat(timespec="seconds") if body.done else None)
        db.execute("UPDATE offboarding SET items=? WHERE id=?", (json.dumps(o["items"], ensure_ascii=False), o["id"]))
        return _offb(db, sid)


@router.post("/api/suppliers/{sid}/offboarding/complete")
def complete_offboarding(sid: int):
    """Tüm maddeler tamamsa tedarikçiyi pasife alır ve tedarikçi kullanıcı hesaplarını kapatır."""
    with get_db() as db:
        o = _offb(db, sid)
        if not o or o["completed_at"]:
            raise HTTPException(409, "Devam eden çıkarma süreci yok.")
        missing = [i["label"] for i in o["items"] if not i["done"]]
        if missing:
            raise HTTPException(422, f"Tamamlanmamış maddeler var: {len(missing)}")
        db.execute("UPDATE offboarding SET completed_at=datetime('now'), completed_by=? WHERE id=?", (auth.current_user().get("name"), o["id"]))
        db.execute("UPDATE suppliers SET status='pasif' WHERE id=?", (sid,))
        n = db.execute("UPDATE users SET active=0 WHERE supplier_id=? AND active=1", (sid,)).rowcount
        db.execute("DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE supplier_id=?)", (sid,))
        db.execute("UPDATE webhook_subscriptions SET active=0 WHERE supplier_id=?", (sid,))
        auth.log("supplier.offboarding_complete", "supplier", sid, {"users_deactivated": n}, db=db)
        return _offb(db, sid)


# ------------------------------------------------------------------ API: sözleşmeler
class ContractIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    contract_no: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    auto_renew: bool = False
    notice_days: int = Field(default=0, ge=0, le=730)
    value: Optional[float] = Field(default=None, ge=0)
    currency: Optional[str] = Field(default="TRY", max_length=3)
    sla: Optional[str] = Field(default=None, max_length=2000)
    has_dpa: bool = False         # veri işleme sözleşmesi (KVKK/GDPR)
    has_nda: bool = False         # gizlilik sözleşmesi
    has_security_clause: bool = False
    owner: Optional[str] = None
    notes: Optional[str] = Field(default=None, max_length=4000)


def _check_dates(b: ContractIn):
    for d in (b.start_date, b.end_date):
        if d and not DATE_RE.match(d):
            raise HTTPException(422, "Tarihler YYYY-AA-GG biçiminde olmalı.")
    if b.start_date and b.end_date and b.end_date < b.start_date:
        raise HTTPException(422, "Bitiş tarihi başlangıçtan önce olamaz.")


def _decorate(c: dict) -> dict:
    c.update(contract_status(c, dt.date.today()))
    for k in ("auto_renew", "has_dpa", "has_nda", "has_security_clause"):
        c[k] = bool(c[k])
    return c


FIELDS = list(ContractIn.model_fields)


@router.get("/api/contracts")
def all_contracts(status: Optional[str] = None):
    w, p = scoped("c.supplier_id")
    with get_db() as db:
        out = [_decorate(c) for c in rows(db.execute(
            "SELECT c.*, s.name AS supplier_name FROM contracts c JOIN suppliers s ON s.id=c.supplier_id WHERE 1=1" + w +
            " ORDER BY c.end_date IS NULL, c.end_date", p).fetchall())]
    return [c for c in out if not status or c["status"] == status]


@router.get("/api/suppliers/{sid}/contracts")
def supplier_contracts(sid: int):
    ensure_supplier_access(sid)
    with get_db() as db:
        return [_decorate(c) for c in rows(db.execute(
            "SELECT * FROM contracts WHERE supplier_id=? ORDER BY end_date IS NULL, end_date DESC", (sid,)).fetchall())]


@router.post("/api/suppliers/{sid}/contracts", status_code=201)
def create_contract(sid: int, body: ContractIn):
    _check_dates(body)
    with get_db() as db:
        if not db.execute("SELECT 1 FROM suppliers WHERE id=?", (sid,)).fetchone():
            raise HTTPException(404, "Tedarikçi bulunamadı")
        d = body.model_dump()
        cid = db.execute(f"INSERT INTO contracts (supplier_id, {', '.join(FIELDS)}) VALUES (?{', ?' * len(FIELDS)})",
                         [sid, *[d[k] for k in FIELDS]]).lastrowid
        auth.log("contract.create", "contract", cid, {"supplier_id": sid}, db=db)
        return _decorate(row(db.execute("SELECT * FROM contracts WHERE id=?", (cid,)).fetchone()))


@router.put("/api/contracts/{cid}")
def update_contract(cid: int, body: ContractIn):
    _check_dates(body)
    with get_db() as db:
        c = db.execute("SELECT supplier_id FROM contracts WHERE id=?", (cid,)).fetchone()
        if not c:
            raise HTTPException(404, "Sözleşme bulunamadı")
        ensure_supplier_access(c[0])
        d = body.model_dump()
        db.execute(f"UPDATE contracts SET {', '.join(k + '=?' for k in FIELDS)}, updated_at=datetime('now') WHERE id=?",
                   [*[d[k] for k in FIELDS], cid])
        auth.log("contract.update", "contract", cid, db=db)
        return _decorate(row(db.execute("SELECT * FROM contracts WHERE id=?", (cid,)).fetchone()))


@router.delete("/api/contracts/{cid}", status_code=204)
def delete_contract(cid: int):
    with get_db() as db:
        db.execute("DELETE FROM contracts WHERE id=?", (cid,))
        auth.log("contract.delete", "contract", cid, db=db)


StatusLiteral = Literal["aday", "aktif", "askıda", "çıkış sürecinde", "pasif"]
