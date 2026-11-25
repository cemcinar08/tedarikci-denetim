"""Madde bazlı CSV (Excel'de Türkçe karakterler bozulmasın diye UTF-8 BOM ve ';' ayırıcı)."""
from __future__ import annotations

import csv
import io

COLUMNS = [("Tedarikçi", None), ("Denetim tarihi", None), ("Bölüm", "section"), ("Kaynak set", "source"),
           ("Soru kodu", "question_id"), ("Soru", "question"), ("Kritik", "critical"), ("Ağırlık", "weight"),
           ("Cevap", "answer_tr"), ("Not", "note"), ("Kanıt sayısı", "evidence")]


def _cell(v):
    if isinstance(v, bool):
        return "evet" if v else "hayır"
    if isinstance(v, float):
        return f"{v:g}".replace(".", ",")
    s = "" if v is None else str(v)
    # CSV/formül enjeksiyonuna karşı (=, +, -, @ ile başlayan hücreler)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def to_csv(report: dict) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    w.writerow([c for c, _ in COLUMNS])
    for it in report["items"]:
        w.writerow([_cell(report["supplier"]["name"]), report["audit"]["audit_date"]] +
                   [_cell(it[k]) for _, k in COLUMNS[2:]])
    r = report["result"]
    w.writerow([])
    w.writerow(["Toplam puan", r["score"] if r["score"] is not None else "", "Karar", r["decision_tr"] or "",
                "Risk", r["risk_tr"] or "", "Kill-switch", len(r["kill_switches"])])
    return ("﻿" + buf.getvalue()).encode("utf-8")
