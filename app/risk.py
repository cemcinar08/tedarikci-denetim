"""Tedarikçi olgunluk puanı (ağırlıklı), finansal/ESG/itibar puanları ve yaptırım listesi taraması."""
from __future__ import annotations

import csv
import difflib
import io
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from typing import Literal, Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import auth
from .auth import ensure_supplier_access
from .db import get_db, row, rows

router = APIRouter()

# ------------------------------------------------------------------ olgunluk puanı (saf)
DIMENSIONS = {
    "audit": "Denetim puanı",
    "intel": "Dış istihbarat (siber)",
    "financial": "Finansal",
    "esg": "ESG / sürdürülebilirlik",
    "reputation": "İtibar",
}
DEFAULT_WEIGHTS = {"audit": 50, "intel": 30, "financial": 10, "esg": 10, "reputation": 0}


def maturity_score(values: dict[str, Optional[float]], weights: dict[str, float]) -> dict:
    """Ağırlıklı olgunluk puanı. Değeri olmayan boyutun ağırlığı diğerlerine oransal dağıtılır.

    Dönüş: {score, coverage (kullanılan ağırlık / toplam ağırlık), breakdown: [...]}
    """
    total_w = sum(max(0.0, float(weights.get(k, 0))) for k in DIMENSIONS)
    used = {k: float(weights.get(k, 0)) for k in DIMENSIONS if values.get(k) is not None and float(weights.get(k, 0)) > 0}
    used_w = sum(used.values())
    breakdown = []
    for k, label in DIMENSIONS.items():
        w = max(0.0, float(weights.get(k, 0)))
        v = values.get(k)
        eff = (w / used_w * 100) if (k in used and used_w) else 0.0
        breakdown.append({"key": k, "label": label, "value": v, "weight": w, "effective_weight": round(eff, 1),
                          "contribution": round(v * eff / 100, 1) if (v is not None and eff) else None})
    score = round(sum(values[k] * w for k, w in used.items()) / used_w, 1) if used_w else None
    return {"score": score, "coverage": round(used_w / total_w * 100) if total_w else 0, "breakdown": breakdown}


def get_weights(db) -> dict:
    r = db.execute("SELECT value FROM settings WHERE key='score_weights'").fetchone()
    try:
        w = json.loads(r[0]) if r and r[0] else {}
    except ValueError:
        w = {}
    return {k: float(w.get(k, DEFAULT_WEIGHTS[k])) for k in DIMENSIONS}


class WeightsIn(BaseModel):
    audit: float = Field(ge=0, le=100)
    intel: float = Field(ge=0, le=100)
    financial: float = Field(ge=0, le=100)
    esg: float = Field(ge=0, le=100)
    reputation: float = Field(ge=0, le=100)


@router.get("/api/score-weights")
def read_weights():
    with get_db() as db:
        return {"weights": get_weights(db), "labels": DIMENSIONS, "defaults": DEFAULT_WEIGHTS}


@router.put("/api/score-weights")
def write_weights(body: WeightsIn):
    w = body.model_dump()
    if sum(w.values()) <= 0:
        raise HTTPException(422, "En az bir boyutun ağırlığı sıfırdan büyük olmalı.")
    with get_db() as db:
        db.execute("INSERT INTO settings (key, value) VALUES ('score_weights', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (json.dumps(w),))
        auth.log("settings.score_weights", "settings", None, w, db=db)
    return read_weights()


# ------------------------------------------------------------------ finansal / ESG / itibar puanları
RATING_DOMAINS = {"financial": "Finansal", "esg": "ESG", "reputation": "İtibar"}


class RatingIn(BaseModel):
    domain: Literal["financial", "esg", "reputation"]
    score: float = Field(ge=0, le=100)
    source: Optional[str] = Field(default=None, max_length=200)   # ör. "Findeks", "EcoVadis", "iç değerlendirme"
    notes: Optional[str] = Field(default=None, max_length=2000)
    assessed_at: Optional[str] = None


def latest_ratings(db, supplier_id: int) -> dict[str, dict]:
    out = {}
    for r in db.execute("SELECT * FROM risk_ratings WHERE supplier_id=? ORDER BY assessed_at DESC, id DESC", (supplier_id,)):
        out.setdefault(r["domain"], dict(r))
    return out


@router.get("/api/suppliers/{sid}/risk-ratings")
def list_ratings(sid: int):
    ensure_supplier_access(sid)
    with get_db() as db:
        return rows(db.execute("SELECT * FROM risk_ratings WHERE supplier_id=? ORDER BY assessed_at DESC, id DESC", (sid,)).fetchall())


@router.post("/api/suppliers/{sid}/risk-ratings", status_code=201)
def add_rating(sid: int, body: RatingIn):
    if body.assessed_at and not re.match(r"^\d{4}-\d{2}-\d{2}$", body.assessed_at):
        raise HTTPException(422, "Tarih YYYY-AA-GG biçiminde olmalı.")
    u = auth.current_user()
    with get_db() as db:
        if not db.execute("SELECT 1 FROM suppliers WHERE id=?", (sid,)).fetchone():
            raise HTTPException(404, "Tedarikçi bulunamadı")
        rid = db.execute(
            "INSERT INTO risk_ratings (supplier_id, domain, score, source, notes, assessed_at, assessed_by) "
            "VALUES (?,?,?,?,?,COALESCE(?, date('now')),?)",
            (sid, body.domain, body.score, body.source, body.notes, body.assessed_at, u.get("name"))).lastrowid
        auth.log("risk_rating.add", "supplier", sid, {"domain": body.domain, "score": body.score}, db=db)
        return row(db.execute("SELECT * FROM risk_ratings WHERE id=?", (rid,)).fetchone())


# ------------------------------------------------------------------ yaptırım taraması
SANCTION_SOURCES = {
    "OFAC": "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.CSV",
    "BM": "https://scsanctions.un.org/resources/xml/en/consolidated.xml",
    "AB": "https://webgate.ec.europa.eu/fsd/fsf/public/files/csvFullSanctionsList_1_1/content?token=dG9rZW4tMjAxNw",
}
LEGAL_SUFFIXES = {
    "as", "aş", "a", "s", "ltd", "şti", "sti", "sirketi", "şirketi", "san", "tic", "ve", "sanayi", "ticaret", "sanayii", "dis", "dış", "anonim", "limited", "inc", "llc", "llp",
    "plc", "gmbh", "ag", "sa", "sarl", "bv", "nv", "spa", "srl", "co", "corp", "corporation", "company", "ltda", "oy", "ab",
    "jsc", "ojsc", "pjsc", "ooo", "zao", "fze", "fzco", "the", "holding", "group", "grup",
}


_SUFFIXES = {unicodedata.normalize("NFKD", x).encode("ascii", "ignore").decode() for x in LEGAL_SUFFIXES}


def normalize_name(name: str) -> str:
    """Saf: küçük harf, aksan/Türkçe karakter sadeleştirme, noktalama ve şirket eklerini (A.Ş., Ltd., GmbH…) atma."""
    s = (name or "").replace("İ", "i").replace("I", "ı").lower()
    s = s.replace("ı", "i").replace("ş", "s").replace("ğ", "g").replace("ü", "u").replace("ö", "o").replace("ç", "c")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(t for t in s.split() if t not in _SUFFIXES)


def similarity(a: str, b: str) -> float:
    """Saf: sıralamadan bağımsız token benzerliği (0-1)."""
    if not a or not b:
        return 0.0
    ta, tb = " ".join(sorted(a.split())), " ".join(sorted(b.split()))
    r = difflib.SequenceMatcher(None, ta, tb).ratio()
    sa, sb = set(a.split()), set(b.split())
    if sa and sb and (sa <= sb or sb <= sa) and min(len(sa), len(sb)) >= 2:
        r = max(r, 0.92)  # biri diğerinin tüm kelimelerini içeriyor
    return r


def screen(name: str, entries: list[dict], threshold: float = 0.92, limit: int = 10) -> list[dict]:
    """Saf: firma adını liste kayıtlarıyla karşılaştırır. entries: [{list, name, normalized, program, ref}]"""
    n = normalize_name(name)
    if len(n) < 3:
        return []
    hits = []
    for e in entries:
        sc = similarity(n, e["normalized"])
        if sc >= threshold:
            hits.append({**{k: e.get(k) for k in ("list", "name", "program", "ref")}, "score": round(sc, 3)})
    return sorted(hits, key=lambda h: -h["score"])[:limit]


def parse_ofac_csv(text: str) -> list[dict]:
    out = []
    for r in csv.reader(io.StringIO(text)):
        if len(r) >= 4 and r[0].strip().isdigit() and r[1].strip() and r[1].strip() != "-0-":
            out.append({"list": "OFAC", "name": r[1].strip(), "program": r[3].replace("-0-", "").strip() or None, "ref": r[0].strip(),
                        "type": (r[2].strip() or "entity").replace("-0-", "entity")})
    return out


def parse_un_xml(text: str) -> list[dict]:
    root = ET.fromstring(text)
    out = []
    for tag, typ in (("INDIVIDUAL", "individual"), ("ENTITY", "entity")):
        for el in root.iter(tag):
            parts = [(el.findtext(f) or "").strip() for f in ("FIRST_NAME", "SECOND_NAME", "THIRD_NAME", "FOURTH_NAME")]
            name = " ".join(p for p in parts if p)
            ref = (el.findtext("REFERENCE_NUMBER") or "").strip()
            prog = (el.findtext("UN_LIST_TYPE") or "").strip() or None
            names = [name] + [(a.findtext("ALIAS_NAME") or "").strip() for a in el.iter(f"{tag}_ALIAS")]
            for nm in dict.fromkeys(n for n in names if n):
                out.append({"list": "BM", "name": nm, "program": prog, "ref": ref, "type": typ})
    return out


def parse_eu_csv(text: str) -> list[dict]:
    rd = csv.DictReader(io.StringIO(text), delimiter=";")
    out, seen = [], set()
    for r in rd:
        nm = (r.get("NameAlias_WholeName") or "").strip()
        ref = (r.get("Entity_LogicalId") or "").strip()
        if nm and (nm, ref) not in seen:
            seen.add((nm, ref))
            out.append({"list": "AB", "name": nm, "program": (r.get("Entity_Regulation_Programme") or "").strip() or None, "ref": ref,
                        "type": "individual" if (r.get("Entity_SubjectType") or "").startswith("person") else "entity"})
    return out


PARSERS = {"OFAC": parse_ofac_csv, "BM": parse_un_xml, "AB": parse_eu_csv}


def store_entries(db, lst: str, entries: list[dict]) -> int:
    db.execute("DELETE FROM sanctions_entries WHERE list=?", (lst,))
    db.executemany("INSERT INTO sanctions_entries (list, name, normalized, program, ref, type) VALUES (?,?,?,?,?,?)",
                   [(lst, e["name"], normalize_name(e["name"]), e.get("program"), e.get("ref"), e.get("type")) for e in entries])
    db.execute("INSERT INTO settings (key, value) VALUES (?, datetime('now')) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (f"sanctions_updated_{lst}",))
    return len(entries)


@router.get("/api/sanctions/status")
def sanctions_status():
    with get_db() as db:
        counts = {r["list"]: r["n"] for r in db.execute("SELECT list, COUNT(*) n FROM sanctions_entries GROUP BY list")}
        upd = {k: (db.execute("SELECT value FROM settings WHERE key=?", (f"sanctions_updated_{k}",)).fetchone() or [None])[0]
               for k in SANCTION_SOURCES}
    return [{"list": k, "url": u, "entries": counts.get(k, 0), "updated_at": upd[k]} for k, u in SANCTION_SOURCES.items()]


@router.post("/api/sanctions/update")
async def sanctions_update():
    """Resmi listeleri indirip yerel veritabanına yükler (OFAC SDN, BM Konsolide, AB Konsolide)."""
    result = []
    async with httpx.AsyncClient(timeout=120, follow_redirects=True, headers={"User-Agent": "TedarikciDenetim/1.0"}) as c:
        for lst, url in SANCTION_SOURCES.items():
            try:
                r = await c.get(url)
                r.raise_for_status()
                entries = PARSERS[lst](r.content.decode("utf-8-sig", errors="replace"))
                if not entries:
                    raise ValueError("liste boş veya biçimi değişmiş")
                with get_db() as db:
                    n = store_entries(db, lst, entries)
                result.append({"list": lst, "ok": True, "entries": n})
            except Exception as e:
                result.append({"list": lst, "ok": False, "error": f"{type(e).__name__}: {e}"[:200]})
    auth.log("sanctions.update", "sanctions", None, {"result": result})
    return result


def run_screen(db, sid: int) -> dict:
    s = db.execute("SELECT name FROM suppliers WHERE id=?", (sid,)).fetchone()
    if not s:
        raise HTTPException(404, "Tedarikçi bulunamadı")
    entries = rows(db.execute("SELECT list, name, normalized, program, ref FROM sanctions_entries").fetchall())
    if not entries:
        raise HTTPException(409, "Yaptırım listeleri henüz indirilmedi. Ayarlar → Yaptırım listeleri → Güncelle.")
    hits = screen(s["name"], entries)
    cid = db.execute("INSERT INTO sanctions_checks (supplier_id, name_checked, match_count, matches, status, checked_by) "
                     "VALUES (?,?,?,?,?,?)", (sid, s["name"], len(hits), json.dumps(hits, ensure_ascii=False),
                                              "olası eşleşme" if hits else "temiz", auth.current_user().get("name"))).lastrowid
    auth.log("sanctions.screen", "supplier", sid, {"matches": len(hits)}, db=db)
    return _check(db, cid)


def _check(db, cid: int) -> dict:
    c = row(db.execute("SELECT * FROM sanctions_checks WHERE id=?", (cid,)).fetchone())
    c["matches"] = json.loads(c["matches"] or "[]")
    return c


@router.post("/api/suppliers/{sid}/sanctions-screen")
def screen_supplier(sid: int):
    with get_db() as db:
        return run_screen(db, sid)


@router.get("/api/suppliers/{sid}/sanctions-checks")
def list_checks(sid: int):
    ensure_supplier_access(sid)
    with get_db() as db:
        return [_check(db, r[0]) for r in db.execute("SELECT id FROM sanctions_checks WHERE supplier_id=? ORDER BY id DESC LIMIT 20", (sid,))]


class ResolveIn(BaseModel):
    status: Literal["yanlış pozitif", "doğrulandı"]
    comment: Optional[str] = Field(default=None, max_length=1000)


@router.post("/api/sanctions-checks/{cid}/resolve")
def resolve_check(cid: int, body: ResolveIn):
    with get_db() as db:
        c = _check(db, cid)
        if c["status"] != "olası eşleşme":
            raise HTTPException(409, "Yalnızca olası eşleşmeler değerlendirilebilir.")
        u = auth.current_user()
        db.execute("UPDATE sanctions_checks SET status=?, resolution_comment=?, resolved_by=?, resolved_at=datetime('now') WHERE id=?",
                   (body.status, body.comment, u.get("name"), cid))
        auth.log("sanctions.resolve", "supplier", c["supplier_id"], {"status": body.status}, db=db)
        return _check(db, cid)


def sanctions_state(db, sid: int) -> Optional[str]:
    """Son taramanın durumu: None / temiz / olası eşleşme / yanlış pozitif / doğrulandı"""
    r = db.execute("SELECT status FROM sanctions_checks WHERE supplier_id=? ORDER BY id DESC LIMIT 1", (sid,)).fetchone()
    return r[0] if r else None
