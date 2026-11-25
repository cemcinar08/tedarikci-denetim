"""Rapor verisini veritabanından toplar (tek okuma noktası)."""
from __future__ import annotations

import datetime as dt
import re
import unicodedata

from fastapi import HTTPException

from .. import templates_engine as eng
from ..auth import ensure_supplier_access
from ..db import get_db, row, rows

ANSWER_TR = {"uygun": "Uygun", "kısmen": "Kısmen", "uygun değil": "Uygun değil", "na": "N/A", None: "—"}


def build_audit_report(audit_id: int) -> dict:
    with get_db() as db:
        a = row(db.execute(
            "SELECT a.*, s.name AS supplier_name, s.category, s.country, s.criticality, t.name AS tier_name "
            "FROM audits a JOIN suppliers s ON s.id=a.supplier_id LEFT JOIN supplier_tiers t ON t.id=s.tier_id WHERE a.id=?",
            (audit_id,)).fetchone())
        if not a:
            raise HTTPException(404, "Denetim bulunamadı")
        ensure_supplier_access(a["supplier_id"])
        if not a.get("checklist"):
            from ..checklist import legacy_sections
            a["checklist"] = legacy_sections()
        ans = {r["question_id"]: dict(r) for r in db.execute(
            "SELECT question_id, answer, note FROM audit_answers WHERE audit_id=?", (audit_id,))}
        ev = {}
        for r in db.execute("SELECT question_id, COUNT(*) n FROM evidence_files WHERE audit_id=? AND question_id IS NOT NULL "
                            "GROUP BY question_id", (audit_id,)):
            ev[r["question_id"]] = r["n"]
        capas = rows(db.execute(
            "SELECT id, title, severity, status, trigger, due_date, planned_date FROM capa_items WHERE audit_id=? ORDER BY "
            "CASE severity WHEN 'kritik' THEN 0 WHEN 'yüksek' THEN 1 WHEN 'orta' THEN 2 ELSE 3 END, id", (audit_id,)).fetchall())
        org = db.execute("SELECT value FROM settings WHERE key='org_name'").fetchone()
    answers = {k: v["answer"] for k, v in ans.items() if v["answer"]}
    res = eng.score_audit(a["checklist"], answers)
    hidden = set(res["hidden_question_ids"])
    items = []
    for sec in a["checklist"]:
        for q in sec["questions"]:
            if q["id"] in hidden:
                continue
            items.append({"section": sec["title"], "source": sec.get("source"), "question_id": q["id"], "question": q["text"],
                          "critical": bool(q.get("critical")), "weight": q.get("weight", 1),
                          "answer": answers.get(q["id"]), "answer_tr": ANSWER_TR.get(answers.get(q["id"]), answers.get(q["id"])),
                          "note": (ans.get(q["id"]) or {}).get("note") or "", "evidence": ev.get(q["id"], 0)})
    return {
        "generated_at": dt.datetime.now().isoformat(timespec="minutes"),
        "org_name": org[0] if org and org[0] else None,
        "audit": {k: a.get(k) for k in ("id", "audit_date", "auditor", "audit_type", "status", "notes", "completed_at")},
        "supplier": {"id": a["supplier_id"], "name": a["supplier_name"], "category": a["category"], "country": a["country"],
                     "criticality": a["criticality"], "tier": a["tier_name"]},
        "template_names": a.get("set_names") or [],
        "result": {"score": res["score"], "risk_level": res["risk_level"], "risk_tr": eng.RISK_TR.get(res["risk_level"]),
                   "decision": res["decision"], "decision_tr": eng.DECISION_TR.get(res["decision"]),
                   "kill_switches": res["kill_switches"], "answered": res["answered"], "visible": res["visible_questions"],
                   "sections": res["sections"]},
        "items": items,
        "capas": capas,
    }


def report_filename(report: dict, ext: str) -> str:
    name = unicodedata.normalize("NFKD", report["supplier"]["name"]).encode("ascii", "ignore").decode()
    name = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower() or "tedarikci"
    return f"denetim-{report['audit']['id']}-{name}-{report['audit']['audit_date']}.{ext}"
