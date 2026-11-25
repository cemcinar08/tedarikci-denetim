"""Kanıt dosyaları ve sertifikalar.

Bir dosya şunlardan birine bağlanır: denetim maddesi (audit_id + question_id), CAPA (capa_id) veya doğrudan
tedarikçi (sertifika). kind='sertifika' için expires_at zorunludur; yaklaşan bitişler özet ekranında listelenir.
Dosyalar data/evidence/<supplier_id>/ altında rastgele adla saklanır; indirme her zaman ek (attachment) olarak verilir.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re
import uuid
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import auth, db as dbmod
from .auth import current_user, ensure_supplier_access, is_supplier_user, scoped
from .db import get_db, row, rows

router = APIRouter()
MAX_BYTES = 20 * 1024 * 1024
ALLOWED = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
           ".csv", ".txt", ".zip", ".eml", ".msg"}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def storage_dir() -> Path:
    return dbmod.DB_PATH.parent / "evidence"


def safe_name(name: str) -> str:
    name = Path(name or "dosya").name
    return re.sub(r"[^\w.\- ()ğüşöçıİĞÜŞÖÇ]", "_", name)[:150] or "dosya"


def cert_status(expires_at: str | None, today: dt.date | None = None, warn_days: int = 60) -> str | None:
    """Saf: 'süresi doldu' / 'yaklaşıyor' / 'geçerli' / None."""
    if not expires_at:
        return None
    days = (dt.date.fromisoformat(expires_at) - (today or dt.date.today())).days
    return "süresi doldu" if days < 0 else "yaklaşıyor" if days <= warn_days else "geçerli"


def _resolve_owner(db, supplier_id, audit_id, question_id, capa_id) -> int:
    """Dosyanın ait olduğu tedarikçiyi bağlı kayıttan belirler ve tutarlılığı doğrular."""
    sid = None
    if capa_id:
        c = db.execute("SELECT supplier_id, status FROM capa_items WHERE id=?", (capa_id,)).fetchone()
        if not c:
            raise HTTPException(404, "CAPA bulunamadı")
        if c["status"] == "onaylandı":
            raise HTTPException(409, "Onaylanmış CAPA'ya kanıt eklenemez.")
        sid = c["supplier_id"]
    if audit_id:
        a = db.execute("SELECT supplier_id, checklist FROM audits WHERE id=?", (audit_id,)).fetchone()
        if not a:
            raise HTTPException(404, "Denetim bulunamadı")
        if sid and sid != a["supplier_id"]:
            raise HTTPException(422, "Denetim ve CAPA farklı tedarikçilere ait.")
        sid = a["supplier_id"]
        if question_id and a["checklist"] and f'"id": "{question_id}"' not in a["checklist"]:
            raise HTTPException(422, "Soru bu denetimde yok.")
    if supplier_id:
        if sid and sid != supplier_id:
            raise HTTPException(422, "Tedarikçi bağlı kayıtla uyuşmuyor.")
        if not db.execute("SELECT 1 FROM suppliers WHERE id=?", (supplier_id,)).fetchone():
            raise HTTPException(404, "Tedarikçi bulunamadı")
        sid = supplier_id
    if not sid:
        raise HTTPException(422, "Dosyayı bir tedarikçiye, denetime veya CAPA'ya bağlayın.")
    ensure_supplier_access(sid)
    return sid


@router.post("/api/evidence", status_code=201)
async def upload(file: UploadFile = File(...), kind: Literal["kanıt", "sertifika", "sözleşme"] = Form("kanıt"),
                 title: Optional[str] = Form(None), supplier_id: Optional[int] = Form(None),
                 audit_id: Optional[int] = Form(None), question_id: Optional[str] = Form(None),
                 capa_id: Optional[int] = Form(None), expires_at: Optional[str] = Form(None),
                 contract_id: Optional[int] = Form(None)):
    if expires_at and not DATE_RE.match(expires_at):
        raise HTTPException(422, "Bitiş tarihi YYYY-AA-GG biçiminde olmalı.")
    if kind == "sertifika" and not expires_at:
        raise HTTPException(422, "Sertifika için geçerlilik bitiş tarihi (expires_at) gerekli.")
    if question_id and not audit_id:
        raise HTTPException(422, "Soru kanıtı için audit_id gerekli.")
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED:
        raise HTTPException(415, f"Bu dosya türüne izin verilmiyor ({ext or 'uzantısız'}). İzinli: {', '.join(sorted(ALLOWED))}")
    if is_supplier_user() and audit_id and not capa_id:
        with get_db() as db:
            st = db.execute("SELECT status FROM audits WHERE id=?", (audit_id,)).fetchone()
        if st and st[0] == "tamamlandı":
            raise HTTPException(409, "Tamamlanmış denetime tedarikçi kanıt ekleyemez; ilgili CAPA üzerinden yükleyin.")
    with get_db() as db:
        if contract_id:
            c = db.execute("SELECT supplier_id FROM contracts WHERE id=?", (contract_id,)).fetchone()
            if not c:
                raise HTTPException(404, "Sözleşme bulunamadı")
            if is_supplier_user():
                raise HTTPException(403, "Sözleşme dosyasını yalnızca kurum kullanıcıları yükleyebilir.")
            supplier_id = supplier_id or c[0]
            if supplier_id != c[0]:
                raise HTTPException(422, "Tedarikçi sözleşmeyle uyuşmuyor.")
            kind = "sözleşme"
        sid = _resolve_owner(db, supplier_id, audit_id, question_id, capa_id)
    # Diske yaz (boyut sınırıyla, akış halinde)
    d = storage_dir() / str(sid)
    d.mkdir(parents=True, exist_ok=True)
    stored = f"{uuid.uuid4().hex}{ext}"
    h, size = hashlib.sha256(), 0
    path = d / stored
    try:
        with open(path, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise HTTPException(413, f"Dosya {MAX_BYTES // (1024 * 1024)} MB sınırını aşıyor.")
                h.update(chunk)
                out.write(chunk)
        if size == 0:
            raise HTTPException(422, "Dosya boş.")
    except HTTPException:
        path.unlink(missing_ok=True)
        raise
    u = current_user()
    with get_db() as db:
        eid = db.execute(
            "INSERT INTO evidence_files (supplier_id, audit_id, question_id, capa_id, contract_id, kind, title, original_name, stored_name, "
            "mime, size, sha256, expires_at, uploaded_by, uploaded_by_name) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sid, audit_id, question_id, capa_id, contract_id, kind, title or safe_name(file.filename), safe_name(file.filename), stored,
             file.content_type, size, h.hexdigest(), expires_at, u.get("id"), u.get("name"))).lastrowid
        if capa_id:
            db.execute("INSERT INTO capa_events (capa_id, user_id, actor, action, comment) VALUES (?,?,?,?,?)",
                       (capa_id, u.get("id"), u.get("name"), "kanıt eklendi", safe_name(file.filename)))
        auth.log("evidence.upload", "evidence", eid, {"supplier_id": sid, "kind": kind, "size": size}, db=db)
        return _get(db, eid)


def _get(db, eid: int) -> dict:
    e = row(db.execute("SELECT e.*, s.name AS supplier_name FROM evidence_files e JOIN suppliers s ON s.id=e.supplier_id "
                       "WHERE e.id=?", (eid,)).fetchone())
    if not e or (e["kind"] == "sözleşme" and is_supplier_user()):
        raise HTTPException(404, "Dosya bulunamadı")
    ensure_supplier_access(e["supplier_id"])
    e["cert_status"] = cert_status(e["expires_at"]) if e["kind"] == "sertifika" else None
    e.pop("stored_name", None)
    return e


@router.get("/api/evidence")
def list_evidence(supplier_id: Optional[int] = None, audit_id: Optional[int] = None, capa_id: Optional[int] = None,
                  kind: Optional[str] = None, question_id: Optional[str] = None, contract_id: Optional[int] = None):
    q = "SELECT e.*, s.name AS supplier_name FROM evidence_files e JOIN suppliers s ON s.id=e.supplier_id WHERE 1=1"
    p: list = []
    for col, v in (("e.supplier_id", supplier_id), ("e.audit_id", audit_id), ("e.capa_id", capa_id), ("e.kind", kind),
                   ("e.question_id", question_id), ("e.contract_id", contract_id)):
        if v is not None:
            q += f" AND {col}=?"; p.append(v)
    w, wp = scoped("e.supplier_id")
    if is_supplier_user():
        w += " AND e.kind != 'sözleşme'"
    with get_db() as db:
        out = rows(db.execute(q + w + " ORDER BY e.id DESC", p + wp).fetchall())
    for e in out:
        e.pop("stored_name", None)
        e["cert_status"] = cert_status(e["expires_at"]) if e["kind"] == "sertifika" else None
    return out


@router.get("/api/evidence/{eid}")
def get_evidence(eid: int):
    with get_db() as db:
        return _get(db, eid)


@router.get("/api/evidence/{eid}/download")
def download(eid: int):
    with get_db() as db:
        e = row(db.execute("SELECT * FROM evidence_files WHERE id=?", (eid,)).fetchone())
    if not e or (e["kind"] == "sözleşme" and is_supplier_user()):
        raise HTTPException(404, "Dosya bulunamadı")
    ensure_supplier_access(e["supplier_id"])
    path = storage_dir() / str(e["supplier_id"]) / e["stored_name"]
    if not path.is_file():
        raise HTTPException(410, "Dosya diskte bulunamadı.")
    return FileResponse(path, filename=e["original_name"], media_type="application/octet-stream",
                        headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"})


class EvidenceUpdate(BaseModel):
    title: Optional[str] = None
    expires_at: Optional[str] = None


def _can_modify(e: dict) -> None:
    """Tedarikçi yalnızca kendi yüklediği ve onaylanmamış CAPA'ya ait olmayan dosyayı değiştirebilir."""
    if is_supplier_user() and e["uploaded_by"] != current_user().get("id"):
        raise HTTPException(403, "Yalnızca kendi yüklediğiniz dosyaları değiştirebilirsiniz.")


@router.put("/api/evidence/{eid}")
def update(eid: int, body: EvidenceUpdate):
    if body.expires_at and not DATE_RE.match(body.expires_at):
        raise HTTPException(422, "Bitiş tarihi YYYY-AA-GG biçiminde olmalı.")
    with get_db() as db:
        e = _get(db, eid)
        _can_modify(e)
        if e["kind"] == "sertifika" and body.expires_at == "":
            raise HTTPException(422, "Sertifika bitiş tarihi boş olamaz.")
        d = body.model_dump(exclude_unset=True)
        if d:
            db.execute(f"UPDATE evidence_files SET {', '.join(k + '=?' for k in d)} WHERE id=?", [*d.values(), eid])
        return _get(db, eid)


@router.delete("/api/evidence/{eid}", status_code=204)
def delete(eid: int):
    with get_db() as db:
        e = _get(db, eid)
        _can_modify(e)
        if e["capa_id"] and db.execute("SELECT 1 FROM capa_items WHERE id=? AND status IN ('onay bekliyor','onaylandı')",
                                       (e["capa_id"],)).fetchone() and is_supplier_user():
            raise HTTPException(409, "Onaya gönderilmiş CAPA'nın kanıtı silinemez.")
        stored = db.execute("SELECT stored_name FROM evidence_files WHERE id=?", (eid,)).fetchone()[0]
        db.execute("DELETE FROM evidence_files WHERE id=?", (eid,))
        auth.log("evidence.delete", "evidence", eid, {"name": e["original_name"]}, db=db)
    (storage_dir() / str(e["supplier_id"]) / stored).unlink(missing_ok=True)
