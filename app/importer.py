"""Tedarikçi listesini CSV / Excel (.xlsx) dosyasından toplu içe aktarma (önizleme + kaydet)."""
from __future__ import annotations

import csv
import io
import json
import re
from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from . import auth
from .db import get_db
from .intel.scanner import normalize_domain

router = APIRouter()
MAX_ROWS = 5000

# Sütun adı eşleştirme (küçük harf, Türkçe/İngilizce)
ALIASES = {
    "name": ["ad", "adı", "firma", "firma adı", "tedarikçi", "tedarikçi adı", "unvan", "name", "supplier", "vendor", "company"],
    "category": ["kategori", "category", "tür", "sektör", "sector"],
    "country": ["ülke", "country"],
    "criticality": ["kritiklik", "criticality", "önem"],
    "tier": ["tier", "seviye", "katman"],
    "contact_name": ["yetkili", "iletişim", "contact", "contact name", "ilgili kişi"],
    "email": ["e-posta", "eposta", "email", "e-mail", "mail"],
    "phone": ["telefon", "tel", "phone"],
    "domains": ["domain", "domainler", "domain'ler", "alan adı", "web", "website", "web sitesi", "domains"],
    "notify_emails": ["bildirim e-postası", "bildirim", "notify emails"],
    "notes": ["not", "notlar", "açıklama", "notes"],
}
CRIT = {"yüksek": "yüksek", "high": "yüksek", "orta": "orta", "medium": "orta", "düşük": "düşük", "low": "düşük",
        "kritik": "yüksek", "critical": "yüksek", "1": "yüksek", "2": "orta", "3": "düşük"}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _key(h: str) -> str:
    return re.sub(r"\s+", " ", (h or "").replace("İ", "i").lower().strip().strip("*"))


def map_headers(headers: list[str]) -> dict[int, str]:
    """Saf: sütun indeksi -> alan adı."""
    lookup = {a: f for f, al in ALIASES.items() for a in al}
    return {i: lookup[_key(h)] for i, h in enumerate(headers) if _key(h) in lookup}


def read_table(filename: str, content: bytes) -> list[list[str]]:
    name = (filename or "").lower()
    if name.endswith(".xlsx"):
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        ws = wb.worksheets[0]
        return [["" if c is None else str(c).strip() for c in r] for r in ws.iter_rows(values_only=True)]
    if name.endswith((".csv", ".txt")):
        text = None
        for enc in ("utf-8-sig", "cp1254", "latin-1"):
            try:
                text = content.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=";,\t") if text.strip() else csv.excel
        return [[c.strip() for c in r] for r in csv.reader(io.StringIO(text), dialect)]
    raise HTTPException(415, "Yalnızca .csv veya .xlsx dosyası yükleyin.")


def parse_rows(table: list[list[str]], tiers: dict[str, int], existing: set[str]) -> list[dict]:
    """Saf: tabloyu doğrulanmış kayıtlara çevirir. tiers: {'1'|'tier 1 — kritik' (küçük harf): id}"""
    table = [r for r in table if any(c for c in r)]
    if not table:
        raise HTTPException(422, "Dosya boş.")
    cols = map_headers(table[0])
    if "name" not in cols.values():
        raise HTTPException(422, "Başlık satırında firma adı sütunu bulunamadı (ör. 'Firma adı', 'Tedarikçi', 'Name').")
    if len(table) - 1 > MAX_ROWS:
        raise HTTPException(413, f"En fazla {MAX_ROWS} satır içe aktarılabilir.")
    out, seen = [], set()
    for n, r in enumerate(table[1:], start=2):
        d = {f: (r[i] if i < len(r) else "") for i, f in cols.items()}
        errors, warnings = [], []
        rec = {"row": n, "name": d.get("name", "").strip()}
        if not rec["name"]:
            errors.append("Firma adı boş")
        key = rec["name"].lower()
        if key in existing:
            errors.append("Bu adla tedarikçi zaten var")
        elif key in seen:
            errors.append("Dosyada tekrar eden ad")
        seen.add(key)
        for f in ("category", "country", "contact_name", "phone", "notes"):
            rec[f] = d.get(f, "").strip() or None
        c = d.get("criticality", "").strip().lower()
        rec["criticality"] = CRIT.get(c, "orta")
        if c and c not in CRIT:
            warnings.append(f"Kritiklik '{c}' anlaşılmadı, 'orta' kullanıldı")
        t = d.get("tier", "").strip().lower()
        t_key = re.sub(r"^(tier|t|seviye)\s*", "", t)
        rec["tier_id"] = tiers.get(t) or tiers.get(t_key)
        if t and not rec["tier_id"]:
            warnings.append(f"Tier '{t}' bulunamadı")
        e = d.get("email", "").strip()
        rec["email"] = e or None
        if e and not EMAIL_RE.match(e):
            errors.append(f"Geçersiz e-posta: {e}")
        ne = [x.strip() for x in re.split(r"[;,\s]+", d.get("notify_emails", "")) if x.strip()]
        bad = [x for x in ne if not EMAIL_RE.match(x)]
        if bad:
            errors.append(f"Geçersiz bildirim e-postası: {', '.join(bad)}")
        rec["notify_emails"] = ", ".join(ne) or None
        doms = []
        for x in re.split(r"[;,\s]+", d.get("domains", "")):
            if x.strip():
                try:
                    doms.append(normalize_domain(x))
                except ValueError:
                    warnings.append(f"Geçersiz domain atlandı: {x}")
        rec["domains"] = list(dict.fromkeys(doms))
        rec["errors"], rec["warnings"] = errors, warnings
        out.append(rec)
    return out


@router.post("/api/import/suppliers")
async def import_suppliers(file: UploadFile = File(...), commit: bool = Form(False)):
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(413, "Dosya 10 MB sınırını aşıyor.")
    table = read_table(file.filename, content)
    with get_db() as db:
        tiers = {}
        for t in db.execute("SELECT id, name, level FROM supplier_tiers"):
            tiers[t["name"].lower()] = t["id"]
            tiers.setdefault(str(t["level"]), t["id"])
        existing = {r[0].lower() for r in db.execute("SELECT name FROM suppliers")}
        recs = parse_rows(table, tiers, existing)
        valid = [r for r in recs if not r["errors"]]
        created = 0
        if commit:
            for r in valid:
                db.execute("INSERT INTO suppliers (name, category, country, criticality, tier_id, contact_name, email, phone, domains, "
                           "notify_emails, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                           (r["name"], r["category"], r["country"], r["criticality"], r["tier_id"], r["contact_name"], r["email"],
                            r["phone"], json.dumps(r["domains"]), r["notify_emails"], r["notes"]))
                created += 1
            auth.log("supplier.import", "supplier", None, {"file": file.filename, "created": created,
                                                            "skipped": len(recs) - len(valid)}, db=db)
    return {"rows": recs, "valid": len(valid), "invalid": len(recs) - len(valid), "created": created, "committed": commit}


def template_csv() -> bytes:
    head = ["Firma adı", "Kategori", "Ülke", "Kritiklik", "Tier", "Yetkili", "E-posta", "Telefon", "Domain", "Bildirim e-postası", "Notlar"]
    ex = ["Örnek Lojistik A.Ş.", "Lojistik", "Türkiye", "orta", "2", "Ayşe Yılmaz", "ayse@ornek.com.tr", "+90 212 000 00 00",
          "ornek.com.tr", "kalite@ornek.com.tr", ""]
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(head)
    w.writerow(ex)
    return ("﻿" + buf.getvalue()).encode()


@router.get("/api/import/suppliers/template.csv")
def import_template():
    from fastapi.responses import Response
    return Response(template_csv(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="tedarikci-sablon.csv"'})
