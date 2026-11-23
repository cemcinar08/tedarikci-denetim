"""SQLite veritabanı katmanı."""
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(os.environ.get("TD_DB_PATH", Path(__file__).resolve().parent.parent / "data" / "denetim.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS suppliers (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    category      TEXT,
    country       TEXT,
    contact_name  TEXT,
    email         TEXT,
    phone         TEXT,
    domains       TEXT NOT NULL DEFAULT '[]',   -- JSON listesi
    criticality   TEXT NOT NULL DEFAULT 'orta', -- düşük / orta / yüksek
    status        TEXT NOT NULL DEFAULT 'aktif',-- aktif / askıda / pasif
    notes         TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS audits (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_id  INTEGER NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
    audit_date   TEXT NOT NULL,
    auditor      TEXT,
    audit_type   TEXT NOT NULL DEFAULT 'yerinde', -- yerinde / uzaktan / doküman
    status       TEXT NOT NULL DEFAULT 'planlandı', -- planlandı / devam ediyor / tamamlandı
    score        REAL,
    notes        TEXT,
    checklist    TEXT,   -- denetim oluşturulurken seçilen soru setlerinin anlık kopyası (JSON)
    set_names    TEXT,   -- JSON: kullanılan set adları
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS question_sets (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    key          TEXT UNIQUE,                     -- yerleşik setler için sabit anahtar
    name         TEXT NOT NULL,
    standard     TEXT,                            -- ör. PCI DSS, ISO 27001, Özel
    description  TEXT,
    builtin      INTEGER NOT NULL DEFAULT 0,
    sections     TEXT NOT NULL DEFAULT '[]',      -- JSON
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS audit_answers (
    audit_id     INTEGER NOT NULL REFERENCES audits(id) ON DELETE CASCADE,
    question_id  TEXT NOT NULL,
    answer       TEXT,   -- uygun / kısmen / uygun değil / na
    note         TEXT,
    PRIMARY KEY (audit_id, question_id)
);

CREATE TABLE IF NOT EXISTS findings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_id  INTEGER NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
    audit_id     INTEGER REFERENCES audits(id) ON DELETE SET NULL,
    scan_id      INTEGER REFERENCES intel_scans(id) ON DELETE SET NULL,
    source       TEXT NOT NULL DEFAULT 'denetim', -- denetim / istihbarat / manuel
    title        TEXT NOT NULL,
    severity     TEXT NOT NULL DEFAULT 'orta',    -- kritik / yüksek / orta / düşük
    description  TEXT,
    action       TEXT,
    owner        TEXT,
    due_date     TEXT,
    status       TEXT NOT NULL DEFAULT 'açık',    -- açık / devam ediyor / kapandı
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    closed_at    TEXT
);

CREATE TABLE IF NOT EXISTS intel_scans (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_id  INTEGER NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
    domain       TEXT NOT NULL,
    scanned_at   TEXT NOT NULL DEFAULT (datetime('now')),
    score        REAL,
    grade        TEXT,
    result       TEXT NOT NULL  -- JSON
);

CREATE INDEX IF NOT EXISTS idx_audits_supplier ON audits(supplier_id);
CREATE INDEX IF NOT EXISTS idx_findings_supplier ON findings(supplier_id);
CREATE INDEX IF NOT EXISTS idx_scans_supplier ON intel_scans(supplier_id, domain);
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def get_db():
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    from .question_sets import BUILTIN_SETS
    with get_db() as db:
        db.executescript(SCHEMA)
        # Eski veritabanları için sütun ekleme (geçiş)
        cols = {r["name"] for r in db.execute("PRAGMA table_info(audits)")}
        for col in ("checklist", "set_names"):
            if col not in cols:
                db.execute(f"ALTER TABLE audits ADD COLUMN {col} TEXT")
        # Yerleşik soru setlerini yükle / güncelle
        for qs in BUILTIN_SETS:
            db.execute(
                """INSERT INTO question_sets (key, name, standard, description, builtin, sections)
                   VALUES (?,?,?,?,1,?)
                   ON CONFLICT(key) DO UPDATE SET name=excluded.name, standard=excluded.standard,
                     description=excluded.description, sections=excluded.sections, builtin=1""",
                (qs["key"], qs["name"], qs["standard"], qs["description"], json.dumps(qs["sections"], ensure_ascii=False)))


def row(r):
    if r is None:
        return None
    d = dict(r)
    if "domains" in d and isinstance(d["domains"], str):
        d["domains"] = json.loads(d["domains"] or "[]")
    for k in ("sections", "checklist", "set_names"):
        if k in d and isinstance(d[k], str):
            d[k] = json.loads(d[k])
    if "result" in d and isinstance(d["result"], str):
        d["result"] = json.loads(d["result"])
    return d


def rows(rs):
    return [row(r) for r in rs]
