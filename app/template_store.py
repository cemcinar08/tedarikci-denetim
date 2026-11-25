"""audit_templates / audit_questions veri erişimi (iş kuralları templates_engine.py'de)."""
from __future__ import annotations

import json
from typing import Optional, Union

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator

from . import templates_engine as eng
from .db import row


class QuestionIn(BaseModel):
    code: Optional[str] = Field(default=None, max_length=40)   # şablon içi kimlik (koşullar buna başvurur)
    text: str = Field(min_length=1)
    weight: float = Field(default=1, gt=0, le=100)
    critical: bool = False                                       # kill-switch
    condition: Optional[dict] = None                            # condition_json


class SectionIn(BaseModel):
    title: str = Field(min_length=1)
    weight: float = Field(default=10, gt=0, le=1000)
    questions: list[Union[QuestionIn, str]]

    @field_validator("questions")
    @classmethod
    def _qs(cls, v):
        out = []
        for q in v:
            if isinstance(q, str):
                t = q.strip()
                if not t:
                    continue
                # Hızlı giriş: satır başında "!" = kritik (kill-switch) soru
                q = QuestionIn(text=t.lstrip("!").strip(), critical=t.startswith("!"))
            elif not q.text.strip():
                continue
            out.append(q)
        if not out:
            raise ValueError("Her bölümde en az bir soru olmalı")
        return out


class TemplateIn(BaseModel):
    name: str = Field(min_length=1)
    standard: Optional[str] = "Özel"
    description: Optional[str] = None
    sections: list[SectionIn] = Field(min_length=1)


def normalize_sections(sections: list[SectionIn] | list[dict]) -> list[dict]:
    """Bölümleri kanonik biçime getirir, eksik kodları üretir, koşulları ve döngüleri doğrular."""
    secs = [s if isinstance(s, SectionIn) else SectionIn(**s) for s in sections]
    used = {q.code for s in secs for q in s.questions if isinstance(q, QuestionIn) and q.code}
    out = []
    for i, sec in enumerate(secs, 1):
        qs = []
        for j, q in enumerate(sec.questions, 1):
            code = q.code
            if not code:
                code, n = f"s{i}.{j}", 1
                while code in used:
                    n += 1
                    code = f"s{i}.{j}-{n}"
                used.add(code)
            qs.append({"id": code, "text": q.text.strip(), "weight": q.weight, "critical": q.critical,
                       "condition": q.condition or None})
        out.append({"id": f"s{i}", "title": sec.title.strip(), "weight": sec.weight, "questions": qs})
    ids = [q["id"] for s in out for q in s["questions"]]
    if len(ids) != len(set(ids)):
        raise HTTPException(422, "Soru kodları şablon içinde benzersiz olmalı.")
    errs = [f"'{q['text'][:40]}': {e}" for s in out for q in s["questions"]
            for e in eng.validate_condition(q["condition"], set(ids))]
    if errs:
        raise HTTPException(422, "Geçersiz koşul — " + "; ".join(errs[:5]))
    cyc = eng.find_cycles(out)
    if cyc:
        raise HTTPException(422, f"Koşullarda döngü var: {', '.join(cyc[:5])}")
    return out


# ------------------------------------------------------------------ okuma / yazma
def get_template(db, tid: int) -> dict:
    t = row(db.execute("SELECT * FROM audit_templates WHERE id=?", (tid,)).fetchone())
    if not t:
        raise HTTPException(404, "Soru seti bulunamadı")
    sections: dict[str, dict] = {}
    for q in db.execute("SELECT * FROM audit_questions WHERE template_id=? ORDER BY sort_order, id", (tid,)):
        sec = sections.setdefault(q["section_code"], {"id": q["section_code"], "title": q["section_title"],
                                                      "weight": q["section_weight"], "questions": []})
        sec["questions"].append({"id": q["code"], "text": q["text"], "weight": q["weight"], "critical": bool(q["is_critical"]),
                                 "condition": json.loads(q["condition_json"]) if q["condition_json"] else None})
    t["sections"] = list(sections.values())
    t["builtin"] = bool(t["builtin"])
    t["question_count"] = sum(len(s["questions"]) for s in t["sections"])
    t["critical_count"] = sum(q["critical"] for s in t["sections"] for q in s["questions"])
    t["conditional_count"] = sum(bool(q["condition"]) for s in t["sections"] for q in s["questions"])
    return t


def write_questions(db, tid: int, sections: list[dict]) -> None:
    db.execute("DELETE FROM audit_questions WHERE template_id=?", (tid,))
    order = 0
    for sec in sections:
        for q in sec["questions"]:
            order += 1
            db.execute(
                "INSERT INTO audit_questions (template_id, section_code, section_title, section_weight, code, text, weight, "
                "is_critical, condition_json, sort_order) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (tid, sec["id"], sec["title"], sec["weight"], q["id"], q["text"], q.get("weight", 1),
                 int(bool(q.get("critical"))), json.dumps(q["condition"], ensure_ascii=False) if q.get("condition") else None,
                 order))


def check_name(db, name: str, exclude_id: int | None = None) -> None:
    if db.execute("SELECT 1 FROM audit_templates WHERE lower(name)=lower(?) AND id IS NOT ?", (name.strip(), exclude_id)).fetchone():
        raise HTTPException(409, f"'{name.strip()}' adında bir soru seti zaten var; farklı bir ad seçin.")


def create_template(db, name: str, standard: str | None, description: str | None, sections: list[dict],
                    builtin: bool = False, key: str | None = None) -> int:
    tid = db.execute("INSERT INTO audit_templates (key, name, standard, description, builtin) VALUES (?,?,?,?,?)",
                     (key, name.strip(), (standard or "Özel").strip(), description, int(builtin))).lastrowid
    write_questions(db, tid, sections)
    return tid


# ------------------------------------------------------------------ başlangıç senkronizasyonu
def sync_templates(db) -> None:
    """Yerleşik setleri yükler/günceller; eski question_sets tablosundaki özel setleri bir kez taşır."""
    from .question_sets import BUILTIN_SETS
    if not db.execute("SELECT COUNT(*) FROM audit_templates").fetchone()[0]:
        for r in db.execute("SELECT * FROM question_sets WHERE builtin=0 ORDER BY id").fetchall():
            secs = json.loads(r["sections"] or "[]")
            try:
                create_template(db, r["name"], r["standard"], r["description"], normalize_sections(secs))
            except HTTPException:
                continue
    for qs in BUILTIN_SETS:
        secs = normalize_sections(qs["sections"])
        ex = db.execute("SELECT id FROM audit_templates WHERE key=?", (qs["key"],)).fetchone()
        if ex:
            db.execute("UPDATE audit_templates SET name=?, standard=?, description=?, builtin=1 WHERE id=?",
                       (qs["name"], qs["standard"], qs["description"], ex[0]))
            write_questions(db, ex[0], secs)
        else:
            create_template(db, qs["name"], qs["standard"], qs["description"], secs, builtin=True, key=qs["key"])
