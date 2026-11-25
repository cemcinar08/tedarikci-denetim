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

-- Dinamik denetim şablonları (eski question_sets tablosunun yerini alır; o tablo yalnızca geçiş için durur)
CREATE TABLE IF NOT EXISTS audit_templates (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    key          TEXT UNIQUE,                 -- yerleşik şablonlar için sabit anahtar
    name         TEXT NOT NULL,
    standard     TEXT,
    description  TEXT,
    builtin      INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS audit_questions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    template_id     INTEGER NOT NULL REFERENCES audit_templates(id) ON DELETE CASCADE,
    section_code    TEXT NOT NULL,
    section_title   TEXT NOT NULL,
    section_weight  REAL NOT NULL DEFAULT 10,
    code            TEXT NOT NULL,            -- şablon içi soru kimliği (koşullar buna başvurur)
    text            TEXT NOT NULL,
    weight          REAL NOT NULL DEFAULT 1,
    is_critical     INTEGER NOT NULL DEFAULT 0, -- kill-switch: "uygun değil" -> doğrudan reddedilir
    condition_json  TEXT,                     -- görünürlük koşulu (templates_engine.py)
    sort_order      INTEGER NOT NULL DEFAULT 0,
    UNIQUE (template_id, code)
);
CREATE INDEX IF NOT EXISTS idx_questions_template ON audit_questions(template_id, sort_order);

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

-- ---------------------------------------------------------------- Erişim (RBAC)
CREATE TABLE IF NOT EXISTS supplier_tiers (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    name                 TEXT NOT NULL UNIQUE,
    level                INTEGER NOT NULL,              -- 1 = en kritik
    audit_interval_days  INTEGER NOT NULL DEFAULT 365,  -- denetim sıklığı
    description          TEXT
);

CREATE TABLE IF NOT EXISTS roles (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL UNIQUE,                  -- admin / auditor / supplier / viewer
    title        TEXT NOT NULL,
    permissions  TEXT NOT NULL DEFAULT '[]'             -- JSON (kaynak: app/auth.py ROLES)
);

CREATE TABLE IF NOT EXISTS users (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL,
    email          TEXT NOT NULL UNIQUE,
    password_hash  TEXT NOT NULL,
    role_id        INTEGER NOT NULL REFERENCES roles(id),
    supplier_id    INTEGER REFERENCES suppliers(id) ON DELETE CASCADE,  -- yalnızca supplier rolü
    active         INTEGER NOT NULL DEFAULT 1,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    last_login_at  TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash  TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at  TEXT NOT NULL,
    ip          TEXT
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    actor       TEXT,                 -- kullanıcı silinse de iz kalsın
    action      TEXT NOT NULL,        -- ör. "POST /api/suppliers", "capa.approve"
    entity      TEXT,
    entity_id   TEXT,
    detail      TEXT,                 -- JSON
    ip          TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_users_supplier ON users(supplier_id);
CREATE INDEX IF NOT EXISTS idx_logs_entity ON audit_logs(entity, entity_id);

-- ---------------------------------------------------------------- Kanıt, CAPA, olaylar
CREATE TABLE IF NOT EXISTS capa_items (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_id        INTEGER NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
    audit_id           INTEGER REFERENCES audits(id) ON DELETE SET NULL,
    finding_id         INTEGER REFERENCES findings(id) ON DELETE SET NULL,
    question_id        TEXT,
    trigger            TEXT NOT NULL DEFAULT 'manuel',   -- uygunsuzluk / kill-switch / düşük puan / manuel
    title              TEXT NOT NULL,
    description        TEXT,
    severity           TEXT NOT NULL DEFAULT 'orta',
    status             TEXT NOT NULL DEFAULT 'açık',     -- açık / onay bekliyor / reddedildi / onaylandı
    due_date           TEXT,
    root_cause         TEXT,
    corrective_action  TEXT,
    preventive_action  TEXT,
    planned_date       TEXT,
    round              INTEGER NOT NULL DEFAULT 0,       -- tedarikçi gönderim sayısı
    submitted_at       TEXT,
    submitted_by       INTEGER REFERENCES users(id) ON DELETE SET NULL,
    reviewed_at        TEXT,
    reviewed_by        INTEGER REFERENCES users(id) ON DELETE SET NULL,
    review_comment     TEXT,
    created_at         TEXT NOT NULL DEFAULT (datetime('now')),
    closed_at          TEXT
);
CREATE INDEX IF NOT EXISTS idx_capa_supplier ON capa_items(supplier_id, status);
CREATE UNIQUE INDEX IF NOT EXISTS uq_capa_audit_question ON capa_items(audit_id, question_id) WHERE audit_id IS NOT NULL AND question_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS capa_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    capa_id     INTEGER NOT NULL REFERENCES capa_items(id) ON DELETE CASCADE,
    user_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    actor       TEXT,
    action      TEXT NOT NULL,          -- oluşturuldu / gönderildi / onaylandı / reddedildi / kanıt eklendi
    from_status TEXT,
    to_status   TEXT,
    comment     TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS evidence_files (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_id    INTEGER NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
    audit_id       INTEGER REFERENCES audits(id) ON DELETE CASCADE,
    question_id    TEXT,                -- denetim maddesi
    capa_id        INTEGER REFERENCES capa_items(id) ON DELETE CASCADE,
    kind           TEXT NOT NULL DEFAULT 'kanıt',   -- kanıt / sertifika
    title          TEXT,
    original_name  TEXT NOT NULL,
    stored_name    TEXT NOT NULL,
    mime           TEXT,
    size           INTEGER NOT NULL,
    sha256         TEXT NOT NULL,
    expires_at     TEXT,                -- sertifika geçerlilik sonu
    uploaded_by    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    uploaded_by_name TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_evidence_supplier ON evidence_files(supplier_id, kind, expires_at);
CREATE INDEX IF NOT EXISTS idx_evidence_audit ON evidence_files(audit_id, question_id);

-- Uygulama olayları (bulgu/CAPA/denetim durum değişimleri) ve dış sistemlere webhook teslimatları
CREATE TABLE IF NOT EXISTS events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    type         TEXT NOT NULL,          -- finding.created, finding.status_changed, capa.*, audit.completed
    supplier_id  INTEGER REFERENCES suppliers(id) ON DELETE CASCADE,
    entity       TEXT,
    entity_id    TEXT,
    payload      TEXT NOT NULL,          -- JSON
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS webhook_subscriptions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    url          TEXT NOT NULL,
    secret       TEXT,
    events       TEXT NOT NULL DEFAULT '["*"]',  -- JSON: olay türleri veya "*"
    supplier_id  INTEGER REFERENCES suppliers(id) ON DELETE CASCADE,  -- boş = tüm tedarikçiler
    active       INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS webhook_deliveries (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    subscription_id  INTEGER REFERENCES webhook_subscriptions(id) ON DELETE CASCADE,
    event_id         INTEGER REFERENCES events(id) ON DELETE CASCADE,
    status           TEXT NOT NULL DEFAULT 'beklemede',  -- beklemede / gönderildi / başarısız
    response_code    INTEGER,
    error            TEXT,
    attempts         INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    delivered_at     TEXT
);

-- ---------------------------------------------------------------- Risk alanları ve yaptırımlar
CREATE TABLE IF NOT EXISTS risk_ratings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_id  INTEGER NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
    domain       TEXT NOT NULL,            -- financial / esg / reputation
    score        REAL NOT NULL,            -- 0-100
    source       TEXT,
    notes        TEXT,
    assessed_at  TEXT NOT NULL,
    assessed_by  TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_ratings_supplier ON risk_ratings(supplier_id, domain, assessed_at);

CREATE TABLE IF NOT EXISTS sanctions_entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    list        TEXT NOT NULL,             -- OFAC / BM / AB
    name        TEXT NOT NULL,
    normalized  TEXT NOT NULL,
    program     TEXT,
    ref         TEXT,
    type        TEXT
);
CREATE INDEX IF NOT EXISTS idx_sanctions_list ON sanctions_entries(list);

CREATE TABLE IF NOT EXISTS sanctions_checks (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_id         INTEGER NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
    name_checked        TEXT NOT NULL,
    match_count         INTEGER NOT NULL,
    matches             TEXT NOT NULL,     -- JSON
    status              TEXT NOT NULL,     -- temiz / olası eşleşme / yanlış pozitif / doğrulandı
    checked_by          TEXT,
    checked_at          TEXT NOT NULL DEFAULT (datetime('now')),
    resolution_comment  TEXT,
    resolved_by         TEXT,
    resolved_at         TEXT
);

-- ---------------------------------------------------------------- Yaşam döngüsü ve sözleşmeler
CREATE TABLE IF NOT EXISTS supplier_intake (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_id  INTEGER NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
    answers      TEXT NOT NULL,      -- JSON (doğal risk soruları)
    regulations  TEXT,               -- JSON
    score        REAL NOT NULL,      -- doğal risk 0-100
    tier_level   INTEGER NOT NULL,   -- önerilen tier
    templates    TEXT,               -- JSON: önerilen soru setleri (key)
    notes        TEXT,
    created_by   TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS offboarding (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_id   INTEGER NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
    reason        TEXT,
    items         TEXT NOT NULL,     -- JSON kontrol listesi
    started_by    TEXT,
    started_at    TEXT NOT NULL DEFAULT (datetime('now')),
    completed_by  TEXT,
    completed_at  TEXT
);

CREATE TABLE IF NOT EXISTS contracts (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_id          INTEGER NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
    title                TEXT NOT NULL,
    contract_no          TEXT,
    start_date           TEXT,
    end_date             TEXT,
    auto_renew           INTEGER NOT NULL DEFAULT 0,
    notice_days          INTEGER NOT NULL DEFAULT 0,   -- fesih ihbar süresi
    value                REAL,
    currency             TEXT,
    sla                  TEXT,
    has_dpa              INTEGER NOT NULL DEFAULT 0,   -- veri işleme sözleşmesi
    has_nda              INTEGER NOT NULL DEFAULT 0,
    has_security_clause  INTEGER NOT NULL DEFAULT 0,
    owner                TEXT,
    notes                TEXT,
    created_at           TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at           TEXT
);
CREATE INDEX IF NOT EXISTS idx_contracts_supplier ON contracts(supplier_id, end_date);

-- ---------------------------------------------------------------- İstisnalar ve mesajlaşma
CREATE TABLE IF NOT EXISTS finding_exceptions (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    finding_id             INTEGER NOT NULL REFERENCES findings(id) ON DELETE CASCADE,
    type                   TEXT NOT NULL,          -- itiraz / risk kabulü
    reason                 TEXT NOT NULL,
    compensating_controls  TEXT,
    expires_at             TEXT,                   -- risk kabulü bitişi
    status                 TEXT NOT NULL DEFAULT 'beklemede',  -- beklemede / onaylandı / reddedildi / süresi doldu
    requested_by           INTEGER REFERENCES users(id) ON DELETE SET NULL,
    requested_by_name      TEXT,
    requested_by_role      TEXT,
    requested_at           TEXT NOT NULL DEFAULT (datetime('now')),
    decided_by             INTEGER REFERENCES users(id) ON DELETE SET NULL,
    decided_by_name        TEXT,
    decided_at             TEXT,
    decision_comment       TEXT
);
CREATE INDEX IF NOT EXISTS idx_exc_finding ON finding_exceptions(finding_id, status);

CREATE TABLE IF NOT EXISTS capa_comments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    capa_id     INTEGER NOT NULL REFERENCES capa_items(id) ON DELETE CASCADE,
    user_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    author      TEXT,
    side        TEXT NOT NULL,            -- kurum / tedarikçi
    body        TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------- Alarmlar ve hatırlatmalar
CREATE TABLE IF NOT EXISTS alerts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_id  INTEGER REFERENCES suppliers(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL,
    severity     TEXT NOT NULL,          -- kritik / yüksek / orta / bilgi
    title        TEXT NOT NULL,
    detail       TEXT,
    entity       TEXT,
    entity_id    TEXT,
    dedupe_key   TEXT UNIQUE,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    read_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_alerts_unread ON alerts(read_at, id);

CREATE TABLE IF NOT EXISTS reminders_sent (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_id  INTEGER NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
    items        INTEGER NOT NULL,
    sent_at      TEXT NOT NULL
);

-- ---------------------------------------------------------------- Entegrasyonlar
CREATE TABLE IF NOT EXISTS external_tickets (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    entity        TEXT NOT NULL,            -- finding / capa
    entity_id     INTEGER NOT NULL,
    supplier_id   INTEGER REFERENCES suppliers(id) ON DELETE CASCADE,
    provider      TEXT NOT NULL,            -- jira / servicenow
    key           TEXT,                     -- ör. SEC-123 / INC0010001
    remote_id     TEXT,
    url           TEXT,
    status        TEXT NOT NULL DEFAULT 'açık',   -- açık / tamamlandı
    remote_status TEXT,
    synced_at     TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_tickets_entity ON external_tickets(entity, entity_id);

CREATE TABLE IF NOT EXISTS sso_states (
    state     TEXT PRIMARY KEY,
    verifier  TEXT NOT NULL,
    nonce     TEXT NOT NULL,
    created   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_runs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_id       INTEGER NOT NULL REFERENCES audits(id) ON DELETE CASCADE,
    model          TEXT,
    questions      INTEGER, documents INTEGER, suggestions INTEGER,
    input_tokens   INTEGER, output_tokens INTEGER,
    created_by     TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS ai_suggestions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_id     INTEGER NOT NULL REFERENCES audits(id) ON DELETE CASCADE,
    run_id       INTEGER REFERENCES ai_runs(id) ON DELETE CASCADE,
    question_id  TEXT NOT NULL,
    answer       TEXT NOT NULL,
    rationale    TEXT,
    citations    TEXT,                 -- JSON [{evidence_id, document, page_start, page_end, quote}]
    status       TEXT NOT NULL DEFAULT 'öneri',   -- öneri / kabul / red / eski
    decided_by   TEXT,
    decided_at   TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
    key    TEXT PRIMARY KEY,
    value  TEXT
);

-- Tedarikçiye gönderilen bildirimler (e-posta / webhook)
CREATE TABLE IF NOT EXISTS notifications (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_id    INTEGER NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
    scan_id        INTEGER REFERENCES intel_scans(id) ON DELETE SET NULL,
    kind           TEXT NOT NULL,              -- bulgu / istihbarat / test
    channel        TEXT NOT NULL,              -- email / webhook
    target         TEXT NOT NULL,              -- e-posta adres(ler)i veya webhook URL
    subject        TEXT,
    payload        TEXT,                       -- gönderilen JSON / e-posta metni
    status         TEXT NOT NULL DEFAULT 'beklemede', -- beklemede / gönderildi / başarısız
    error          TEXT,
    response_code  INTEGER,
    token          TEXT UNIQUE,                -- tedarikçinin yanıt linki / API anahtarı
    auto           INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    sent_at        TEXT,
    attempts       INTEGER NOT NULL DEFAULT 0,
    viewed_at      TEXT
);

CREATE TABLE IF NOT EXISTS notification_findings (
    notification_id INTEGER NOT NULL REFERENCES notifications(id) ON DELETE CASCADE,
    finding_id      INTEGER NOT NULL REFERENCES findings(id) ON DELETE CASCADE,
    PRIMARY KEY (notification_id, finding_id)
);

-- Tedarikçiden gelen yanıtlar (yanıt sayfası veya API üzerinden)
CREATE TABLE IF NOT EXISTS supplier_responses (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    notification_id  INTEGER NOT NULL REFERENCES notifications(id) ON DELETE CASCADE,
    finding_id       INTEGER REFERENCES findings(id) ON DELETE CASCADE,
    status           TEXT NOT NULL,   -- okundu / aksiyon planlandı / giderildi / itiraz
    comment          TEXT,
    planned_date     TEXT,
    responder        TEXT,
    via              TEXT NOT NULL DEFAULT 'sayfa', -- sayfa / api
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_notif_supplier ON notifications(supplier_id);
CREATE INDEX IF NOT EXISTS idx_resp_finding ON supplier_responses(finding_id);
CREATE INDEX IF NOT EXISTS idx_audits_supplier ON audits(supplier_id);
CREATE INDEX IF NOT EXISTS idx_findings_supplier ON findings(supplier_id);
CREATE INDEX IF NOT EXISTS idx_scans_supplier ON intel_scans(supplier_id, domain);
"""


MIGRATIONS = {
    "audits": {"checklist": "TEXT", "set_names": "TEXT", "template_ids": "TEXT",
               "risk_level": "TEXT", "decision": "TEXT", "kill_switches": "TEXT", "completed_at": "TEXT",
               "respondent": "TEXT NOT NULL DEFAULT 'denetçi'",   # denetçi / tedarikçi (öz-değerlendirme)
               "due_date": "TEXT", "sent_at": "TEXT", "submitted_at": "TEXT", "submitted_by": "TEXT", "review_comment": "TEXT"},
    "suppliers": {
        "tier_id": "INTEGER REFERENCES supplier_tiers(id) ON DELETE SET NULL",
        "lei": "TEXT",                           # Legal Entity Identifier (DORA bilgi kaydı)
        "tax_no": "TEXT",                        # vergi / ticaret sicil no
        "notify_emails": "TEXT",                 # virgülle ayrılmış alıcılar
        "webhook_url": "TEXT",
        "webhook_secret": "TEXT",                # HMAC-SHA256 imza anahtarı
        "auto_notify": "INTEGER NOT NULL DEFAULT 0",
    },
    "evidence_files": {"contract_id": "INTEGER REFERENCES contracts(id) ON DELETE CASCADE"},
    "webhook_subscriptions": {"format": "TEXT NOT NULL DEFAULT 'json'"},   # json / slack / teams
    "findings": {
        "question_id": "TEXT",                   # denetimdeki soru (denetim kaynaklı bulgular)
        "supplier_status": "TEXT",               # bildirildi / okundu / aksiyon planlandı / giderildi / itiraz
        "supplier_comment": "TEXT",
        "supplier_due_date": "TEXT",
        "supplier_updated_at": "TEXT",
        "notified_at": "TEXT",
    },
}


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
    with get_db() as db:
        db.executescript(SCHEMA)
        # Eski veritabanları için sütun ekleme (geçiş)
        for table, cols in MIGRATIONS.items():
            have = {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}
            for col, typ in cols.items():
                if col not in have:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
        from .auth import seed_auth
        seed_auth(db)
        from .template_store import sync_templates
        sync_templates(db)


def row(r):
    if r is None:
        return None
    d = dict(r)
    if "domains" in d and isinstance(d["domains"], str):
        d["domains"] = json.loads(d["domains"] or "[]")
    for k in ("sections", "checklist", "set_names", "template_ids", "detail", "permissions", "payload_json", "events_json", "condition_json", "kill_switches"):
        if k in d and isinstance(d[k], str):
            try:
                d[k] = json.loads(d[k])
            except ValueError:
                pass
    if "result" in d and isinstance(d["result"], str):
        d["result"] = json.loads(d["result"])
    return d


def rows(rs):
    return [row(r) for r in rs]
