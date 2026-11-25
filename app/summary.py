"""Özet ekranı: risk dağılımı, açık CAPA'lar, yaklaşan sertifika bitişleri."""
from __future__ import annotations

import datetime as dt
from typing import Optional

from fastapi import APIRouter, Query

from .auth import scoped
from .db import get_db, rows
from .evidence import cert_status

router = APIRouter()
RISK_LEVELS = ["high", "medium", "low", "none"]


def summarize(suppliers: list[dict], capas: list[dict], certs: list[dict], today: dt.date, days: int = 60) -> dict:
    """Saf fonksiyon.
    suppliers: [{id, name, risk_level (son tamamlanan denetimin), decision}]
    capas:     [{status, severity, due_date}]
    certs:     [{id, supplier_id, supplier_name, title, expires_at}]
    """
    risk = {k: 0 for k in RISK_LEVELS}
    for s in suppliers:
        risk[s.get("risk_level") or "none"] += 1
    open_caps = [c for c in capas if c["status"] != "onaylandı"]
    by_status = {k: 0 for k in ("açık", "onay bekliyor", "reddedildi")}
    by_sev = {k: 0 for k in ("kritik", "yüksek", "orta", "düşük")}
    for c in open_caps:
        by_status[c["status"]] = by_status.get(c["status"], 0) + 1
        by_sev[c["severity"]] = by_sev.get(c["severity"], 0) + 1
    overdue = sum(1 for c in open_caps if c.get("due_date") and c["due_date"] < today.isoformat())
    horizon = (today + dt.timedelta(days=days)).isoformat()
    upcoming = sorted(
        [{**c, "days_left": (dt.date.fromisoformat(c["expires_at"]) - today).days,
          "status": cert_status(c["expires_at"], today, days)}
         for c in certs if c.get("expires_at") and c["expires_at"] <= horizon],
        key=lambda c: c["expires_at"])
    return {
        "risk_distribution": risk,
        "rejected_suppliers": sum(1 for s in suppliers if s.get("decision") == "rejected"),
        "capa": {"open": len(open_caps), "by_status": by_status, "by_severity": by_sev, "overdue": overdue},
        "certificates": {"window_days": days, "expired": sum(1 for c in upcoming if c["days_left"] < 0),
                         "expiring": sum(1 for c in upcoming if c["days_left"] >= 0), "items": upcoming},
    }


@router.get("/api/summary")
def summary(days: int = Query(60, ge=1, le=365), tier_id: Optional[int] = None):
    w, p = scoped("s.id")
    tw, tp = (" AND s.tier_id=?", [tier_id]) if tier_id else ("", [])
    with get_db() as db:
        sups = rows(db.execute(
            "SELECT s.id, s.name, a.risk_level, a.decision FROM suppliers s LEFT JOIN audits a ON a.id = ("
            "  SELECT id FROM audits WHERE supplier_id=s.id AND status='tamamlandı' ORDER BY audit_date DESC, id DESC LIMIT 1) "
            "WHERE s.status != 'pasif'" + w + tw, p + tp).fetchall())
        ids = [s["id"] for s in sups] or [0]
        ph = ",".join("?" * len(ids))
        capas = rows(db.execute(f"SELECT status, severity, due_date FROM capa_items WHERE supplier_id IN ({ph})", ids).fetchall())
        certs = rows(db.execute(
            f"SELECT e.id, e.supplier_id, s.name AS supplier_name, e.title, e.original_name, e.expires_at FROM evidence_files e "
            f"JOIN suppliers s ON s.id=e.supplier_id WHERE e.kind='sertifika' AND e.supplier_id IN ({ph})", ids).fetchall())
    return summarize(sups, capas, certs, dt.date.today(), days)
