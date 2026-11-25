"""Kimlik doğrulama, rol tabanlı yetki (RBAC), tedarikçi satır izolasyonu ve işlem kaydı (audit_logs).

- Oturum: HttpOnly çerez `td_session` veya `Authorization: Bearer <token>`
- Yetki: `AuthMiddleware` her /api isteğini `PERMS` tablosuyla eşler. Eşleşmeyen uç = yalnızca admin (varsayılan ret).
- İzolasyon: Supplier rolündeki kullanıcı yalnızca kendi `supplier_id` kayıtlarını görür (`scope_supplier`, `scoped`).
"""
from __future__ import annotations

import contextvars
import datetime as dt
import hashlib
import hmac
import json
import re
import secrets
import time
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse

from .db import get_db, row, rows

router = APIRouter()
COOKIE = "td_session"
SESSION_DAYS = 7

# ------------------------------------------------------------------ roller ve yetkiler
ROLES: dict[str, dict] = {
    "admin": {"title": "Yönetici", "perms": ["*"]},
    "auditor": {"title": "Denetçi", "perms": [
        "supplier:read", "supplier:write", "audit:read", "audit:write", "template:read", "template:write",
        "finding:read", "finding:write", "capa:read", "capa:write", "capa:review", "evidence:read", "evidence:write",
        "intel:scan", "notify:send", "report:read", "summary:read", "settings:read", "contract:read", "contract:write",
        "finding:dispute", "capa:comment"]},
    "supplier": {"title": "Tedarikçi", "perms": [
        "supplier:read", "audit:read", "finding:read", "capa:read", "capa:submit", "evidence:read", "evidence:write",
        "report:read", "summary:read", "finding:dispute", "capa:comment"]},
    "viewer": {"title": "İzleyici", "perms": [
        "supplier:read", "audit:read", "template:read", "finding:read", "capa:read", "evidence:read",
        "report:read", "summary:read", "contract:read"]},
}

# (yöntem, yol deseni, gereken yetki). None = giriş yapmış herkes. Sıra önemlidir; ilk eşleşen kullanılır.
_R = r"[^/]+"
PERMS: list[tuple[str, str, Optional[str]]] = [
    ("GET", r"/api/me", None), ("PUT", r"/api/me/password", None), ("GET", r"/api/meta", None),
    ("GET", r"/api/summary", "summary:read"), ("GET", r"/api/dashboard", "summary:read"),
    # tedarikçiler
    ("GET", r"/api/suppliers", "supplier:read"), ("GET", rf"/api/suppliers/{_R}", "supplier:read"),
    ("POST", r"/api/suppliers", "supplier:write"), ("PUT", rf"/api/suppliers/{_R}", "supplier:write"),
    ("DELETE", rf"/api/suppliers/{_R}", "supplier:write"),
    ("GET", r"/api/supplier-tiers", "supplier:read"),
    ("GET", rf"/api/suppliers/{_R}/risk-ratings", "supplier:read"), ("POST", rf"/api/suppliers/{_R}/risk-ratings", "supplier:write"),
    ("GET", rf"/api/suppliers/{_R}/sanctions-checks", "supplier:read"), ("POST", rf"/api/suppliers/{_R}/sanctions-screen", "supplier:write"),
    ("POST", rf"/api/sanctions-checks/{_R}/resolve", "supplier:write"),
    ("GET", r"/api/sanctions/status", "supplier:read"), ("POST", r"/api/sanctions/update", "settings:manage"),
    ("GET", r"/api/score-weights", "supplier:read"), ("PUT", r"/api/score-weights", "settings:manage"),
    ("GET", r"/api/onboarding/questions", "supplier:read"), ("POST", r"/api/onboarding(/preview)?", "supplier:write"),
    ("GET", rf"/api/suppliers/{_R}/intake", "supplier:read"), ("POST", rf"/api/suppliers/{_R}/intake", "supplier:write"),
    ("POST", rf"/api/suppliers/{_R}/activate", "supplier:write"),
    ("GET", rf"/api/suppliers/{_R}/offboarding", "supplier:read"), ("POST", rf"/api/suppliers/{_R}/offboarding(/complete)?", "supplier:write"),
    ("PUT", rf"/api/suppliers/{_R}/offboarding/items/{_R}", "supplier:write"),
    ("GET", r"/api/contracts", "contract:read"), ("GET", rf"/api/suppliers/{_R}/contracts", "contract:read"),
    ("POST", rf"/api/suppliers/{_R}/contracts", "contract:write"), ("PUT", rf"/api/contracts/{_R}", "contract:write"),
    ("DELETE", rf"/api/contracts/{_R}", "contract:write"),
    ("POST", r"/api/import/suppliers", "supplier:write"), ("GET", r"/api/import/suppliers/template\.csv", "supplier:write"),
    ("POST", r"/api/supplier-tiers", "settings:manage"), ("PUT", rf"/api/supplier-tiers/{_R}", "settings:manage"),
    ("DELETE", rf"/api/supplier-tiers/{_R}", "settings:manage"),
    # denetimler ve şablonlar
    ("POST", rf"/api/suppliers/{_R}/audits", "audit:write"),
    ("GET", r"/api/audits", "audit:read"), ("GET", rf"/api/audits/{_R}", "audit:read"),
    ("PUT", rf"/api/audits/{_R}", "audit:write"), ("DELETE", rf"/api/audits/{_R}", "audit:write"),
    ("POST", rf"/api/audits/{_R}/generate-findings", "finding:write"),
    ("GET", r"/api/question-sets(/.*)?", "template:read"),
    ("POST", r"/api/question-sets(/.*)?", "template:write"), ("PUT", rf"/api/question-sets/{_R}", "template:write"),
    ("DELETE", rf"/api/question-sets/{_R}", "template:write"),
    # bulgular, CAPA, kanıt
    ("GET", r"/api/findings", "finding:read"), ("POST", r"/api/findings", "finding:write"),
    ("PUT", rf"/api/findings/{_R}", "finding:write"), ("DELETE", rf"/api/findings/{_R}", "finding:write"),
    ("GET", r"/api/capa", "capa:read"), ("GET", rf"/api/capa/{_R}", "capa:read"),
    ("POST", r"/api/capa", "capa:write"), ("POST", rf"/api/capa/{_R}/submit", "capa:submit"),
    ("POST", rf"/api/capa/{_R}/review", "capa:review"),
    ("GET", rf"/api/capa/{_R}/comments", "capa:read"), ("POST", rf"/api/capa/{_R}/comments", "capa:comment"),
    ("GET", r"/api/exceptions", "finding:read"), ("POST", rf"/api/exceptions/{_R}/decide", "finding:write"),
    ("GET", rf"/api/findings/{_R}/exceptions", "finding:read"), ("POST", rf"/api/findings/{_R}/exceptions", "finding:dispute"),
    ("GET", r"/api/evidence", "evidence:read"), ("GET", rf"/api/evidence/{_R}(/download)?", "evidence:read"),
    ("POST", r"/api/evidence", "evidence:write"), ("PUT", rf"/api/evidence/{_R}", "evidence:write"),
    ("DELETE", rf"/api/evidence/{_R}", "evidence:write"),
    # istihbarat
    ("POST", rf"/api/suppliers/{_R}/scan", "intel:scan"), ("GET", r"/api/quick-scan", "intel:scan"),
    ("GET", rf"/api/scans/{_R}", "supplier:read"),
    # bildirim, rapor, entegrasyon
    ("GET", r"/api/notifications", "notify:send"), ("POST", r"/api/notifications(/.*)?", "notify:send"),
    ("POST", rf"/api/suppliers/{_R}/webhook-test", "notify:send"),
    ("GET", r"/api/reports/.*", "report:read"),
    ("GET", r"/api/settings", "settings:read"), ("PUT", r"/api/settings", "settings:manage"),
    ("POST", r"/api/settings/test-email", "settings:manage"),
    ("GET", r"/api/webhooks(/.*)?", "settings:manage"), ("POST", r"/api/webhooks(/.*)?", "settings:manage"),
    ("PUT", rf"/api/webhooks/{_R}", "settings:manage"), ("DELETE", rf"/api/webhooks/{_R}", "settings:manage"),
    # yönetim
    ("GET", r"/api/users", "user:manage"), ("POST", r"/api/users", "user:manage"),
    ("PUT", rf"/api/users/{_R}", "user:manage"), ("DELETE", rf"/api/users/{_R}", "user:manage"),
    ("GET", r"/api/roles", "user:manage"), ("GET", r"/api/audit-logs", "auditlog:read"),
]
_COMPILED = [(m, re.compile(f"^{p}$"), perm) for m, p, perm in PERMS]
PUBLIC = re.compile(r"^/api/(public/.*|auth/(login|logout|setup|status))$")


def required_perm(method: str, path: str) -> tuple[bool, Optional[str]]:
    """(eşleşti_mi, yetki). Eşleşmeyen uçlar yalnızca admin içindir."""
    m = "GET" if method == "HEAD" else method
    for meth, rx, perm in _COMPILED:
        if meth == m and rx.match(path):
            return True, perm
    return False, "*"


def has_perm(user: dict | None, perm: Optional[str]) -> bool:
    if user is None:
        return False
    perms = ROLES.get(user["role"], {}).get("perms", [])
    return "*" in perms or perm is None or perm in perms


# ------------------------------------------------------------------ geçerli kullanıcı / izolasyon
_CURRENT: contextvars.ContextVar[dict | None] = contextvars.ContextVar("current_user", default=None)
SYSTEM = {"id": None, "role": "admin", "name": "Sistem", "email": None, "supplier_id": None, "system": True}


def current_user() -> dict:
    """İstek içindeyken giriş yapan kullanıcı; doğrudan çağrılarda (seed, servis) sistem kullanıcısı."""
    return _CURRENT.get() or SYSTEM


def scope_supplier() -> Optional[int]:
    """Supplier rolü için erişebileceği tek supplier_id; diğer roller için None (kısıt yok)."""
    u = current_user()
    return u["supplier_id"] if u["role"] == "supplier" else None


def scoped(column: str = "supplier_id") -> tuple[str, list]:
    """SQL'e eklenecek izolasyon koşulu:  q += sql; params += p"""
    sid = scope_supplier()
    if current_user()["role"] == "supplier" and not sid:
        return " AND 1=0", []
    return (f" AND {column}=?", [sid]) if sid else ("", [])


def ensure_supplier_access(supplier_id: int | None) -> None:
    """Kayıt başka bir tedarikçiye aitse 404 (varlığını sızdırmamak için 403 değil)."""
    sid = scope_supplier()
    if current_user()["role"] == "supplier" and (not sid or supplier_id != sid):
        raise HTTPException(404, "Kayıt bulunamadı")


def is_supplier_user() -> bool:
    return current_user()["role"] == "supplier"


# ------------------------------------------------------------------ parola ve oturum
def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${h.hex()}"


def verify_password(pw: str, stored: str | None) -> bool:
    try:
        _, n, r, p, salt, h = (stored or "").split("$")
        calc = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=len(h) // 2)
        return hmac.compare_digest(calc.hex(), h)
    except (ValueError, TypeError):
        return False


def _token_hash(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()


def create_session(user_id: int, ip: str | None) -> str:
    token = secrets.token_urlsafe(32)
    exp = (dt.datetime.now() + dt.timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds")
    with get_db() as db:
        db.execute("INSERT INTO sessions (token_hash, user_id, expires_at, ip) VALUES (?,?,?,?)",
                   (_token_hash(token), user_id, exp, ip))
        db.execute("UPDATE users SET last_login_at=datetime('now') WHERE id=?", (user_id,))
    return token


def user_from_token(token: str | None) -> dict | None:
    if not token:
        return None
    with get_db() as db:
        u = row(db.execute(
            "SELECT u.id, u.name, u.email, u.supplier_id, u.active, r.name AS role FROM sessions s "
            "JOIN users u ON u.id=s.user_id JOIN roles r ON r.id=u.role_id "
            "WHERE s.token_hash=? AND s.expires_at > ?", (_token_hash(token), dt.datetime.now().isoformat())).fetchone())
    if not u or not u["active"]:
        return None
    return u


def _token(request: Request) -> str | None:
    a = request.headers.get("authorization", "")
    if a.lower().startswith("bearer "):
        return a[7:].strip()
    return request.cookies.get(COOKIE)


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def log(action: str, entity: str | None = None, entity_id=None, detail: dict | None = None, *,
        user: dict | None = None, ip: str | None = None, db=None) -> None:
    """audit_logs kaydı. Servisler alan bazlı olayları (CAPA onayı vb.) bununla kaydeder."""
    u = user or current_user()
    args = (u.get("id"), u.get("email") or u.get("name"), action, entity, None if entity_id is None else str(entity_id),
            json.dumps(detail, ensure_ascii=False) if detail else None, ip)
    sql = "INSERT INTO audit_logs (user_id, actor, action, entity, entity_id, detail, ip) VALUES (?,?,?,?,?,?,?)"
    if db is not None:
        db.execute(sql, args)
    else:
        with get_db() as d:
            d.execute(sql, args)


# ------------------------------------------------------------------ middleware
class AuthMiddleware:
    """Saf ASGI middleware: kimlik doğrular, yetkiyi kontrol eder, kullanıcıyı contextvar'a koyar,
    değişiklik yapan API isteklerini audit_logs'a yazar."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            return await self.app(scope, receive, send)
        request = Request(scope)
        path, method = scope["path"], scope["method"]
        if PUBLIC.match(path):
            return await self.app(scope, receive, send)
        user = user_from_token(_token(request))
        if user is None:
            return await JSONResponse({"detail": "Giriş yapmanız gerekiyor."}, status_code=401)(scope, receive, send)
        _, perm = required_perm(method, path)
        if not has_perm(user, perm):
            return await JSONResponse({"detail": "Bu işlem için yetkiniz yok."}, status_code=403)(scope, receive, send)
        token = _CURRENT.set(user)
        status = {"code": 500}

        async def _send(msg):
            if msg["type"] == "http.response.start":
                status["code"] = msg["status"]
            await send(msg)
        try:
            await self.app(scope, receive, _send)
        finally:
            _CURRENT.reset(token)
            if method in ("POST", "PUT", "PATCH", "DELETE") and not path.startswith("/api/me"):
                try:
                    log(f"{method} {path}", detail={"status": status["code"]}, user=user, ip=_ip(request))
                except Exception:  # kayıt hatası isteği bozmasın
                    pass
                # İşlem commit edildikten sonra bekleyen webhook teslimatlarını gönder (yanıt zaten iletildi)
                from . import events
                try:
                    if events.has_pending():
                        await events.deliver_pending()
                except Exception:
                    pass


# ------------------------------------------------------------------ veri tabanı tohumları
DEFAULT_TIERS = [
    ("Tier 1 — Kritik", 1, 365, "Üretimi/hizmeti doğrudan durdurabilecek veya kritik veri işleyen tedarikçiler"),
    ("Tier 2 — Önemli", 2, 730, "Önemli ama alternatifi bulunan tedarikçiler"),
    ("Tier 3 — Standart", 3, 1095, "Düşük riskli, kolay ikame edilebilen tedarikçiler"),
]


def seed_auth(db) -> None:
    for name, r in ROLES.items():
        db.execute("INSERT INTO roles (name, title, permissions) VALUES (?,?,?) ON CONFLICT(name) DO UPDATE SET "
                   "title=excluded.title, permissions=excluded.permissions", (name, r["title"], json.dumps(r["perms"])))
    if not db.execute("SELECT COUNT(*) FROM supplier_tiers").fetchone()[0]:
        db.executemany("INSERT INTO supplier_tiers (name, level, audit_interval_days, description) VALUES (?,?,?,?)",
                       DEFAULT_TIERS)


def role_id(db, name: str) -> int:
    r = db.execute("SELECT id FROM roles WHERE name=?", (name,)).fetchone()
    if not r:
        raise HTTPException(422, "Geçersiz rol")
    return r[0]


# ------------------------------------------------------------------ API: oturum
class LoginIn(BaseModel):
    email: str
    password: str


class SetupIn(BaseModel):
    name: str = Field(min_length=1)
    email: str
    password: str = Field(min_length=8)


_FAILS: dict[str, list[float]] = {}


def _set_cookie(resp: Response, token: str, request: Request):
    resp.set_cookie(COOKIE, token, httponly=True, samesite="lax", secure=request.url.scheme == "https",
                    max_age=SESSION_DAYS * 86400, path="/")


def _email(v: str) -> str:
    v = v.strip().lower()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", v):
        raise HTTPException(422, "Geçersiz e-posta")
    return v


@router.get("/api/auth/status")
def auth_status(request: Request):
    with get_db() as db:
        setup = db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    return {"setup_required": setup, "user": user_from_token(_token(request))}


@router.post("/api/auth/setup")
def auth_setup(body: SetupIn, request: Request, response: Response):
    """İlk kurulum: hiç kullanıcı yokken ilk yöneticiyi oluşturur."""
    with get_db() as db:
        if db.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
            raise HTTPException(409, "Kurulum zaten yapılmış.")
        uid = db.execute("INSERT INTO users (name, email, password_hash, role_id) VALUES (?,?,?,?)",
                         (body.name.strip(), _email(body.email), hash_password(body.password), role_id(db, "admin"))).lastrowid
    _set_cookie(response, create_session(uid, _ip(request)), request)
    log("auth.setup", "user", uid, user={"id": uid, "email": body.email}, ip=_ip(request))
    return {"ok": True}


@router.post("/api/auth/login")
def login(body: LoginIn, request: Request, response: Response):
    email = body.email.strip().lower()
    now = time.time()
    fails = [t for t in _FAILS.get(email, []) if now - t < 900]
    if len(fails) >= 5:
        raise HTTPException(429, "Çok fazla hatalı deneme. 15 dakika sonra tekrar deneyin.")
    with get_db() as db:
        u = row(db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone())
    if not u or not u["active"] or not verify_password(body.password, u["password_hash"]):
        _FAILS[email] = fails + [now]
        log("auth.login_failed", "user", None, {"email": email}, user={"id": None, "email": email}, ip=_ip(request))
        raise HTTPException(401, "E-posta veya parola hatalı.")
    _FAILS.pop(email, None)
    token = create_session(u["id"], _ip(request))
    _set_cookie(response, token, request)
    log("auth.login", "user", u["id"], user=u, ip=_ip(request))
    return {"ok": True, "token": token}


@router.post("/api/auth/logout")
def logout(request: Request, response: Response):
    t = _token(request)
    if t:
        with get_db() as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (_token_hash(t),))
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/api/me")
def me():
    u = current_user()
    out = dict(u)
    out["permissions"] = ROLES[u["role"]]["perms"]
    out["role_title"] = ROLES[u["role"]]["title"]
    if u.get("supplier_id"):
        with get_db() as db:
            s = db.execute("SELECT name FROM suppliers WHERE id=?", (u["supplier_id"],)).fetchone()
        out["supplier_name"] = s[0] if s else None
    return out


class PasswordIn(BaseModel):
    current: str
    new: str = Field(min_length=8)


@router.put("/api/me/password")
def change_password(body: PasswordIn):
    u = current_user()
    with get_db() as db:
        h = db.execute("SELECT password_hash FROM users WHERE id=?", (u["id"],)).fetchone()
        if not h or not verify_password(body.current, h[0]):
            raise HTTPException(400, "Mevcut parola hatalı.")
        db.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(body.new), u["id"]))
        log("user.password_changed", "user", u["id"], db=db)
    return {"ok": True}


# ------------------------------------------------------------------ API: kullanıcı yönetimi
class UserIn(BaseModel):
    name: str = Field(min_length=1)
    email: str
    role: Literal["admin", "auditor", "supplier", "viewer"]
    supplier_id: Optional[int] = None
    password: Optional[str] = Field(default=None, min_length=8)
    active: bool = True


def _check_user(db, body: UserIn):
    if body.role == "supplier":
        if not body.supplier_id or not db.execute("SELECT 1 FROM suppliers WHERE id=?", (body.supplier_id,)).fetchone():
            raise HTTPException(422, "Tedarikçi kullanıcısı için geçerli bir tedarikçi seçin.")
    elif body.supplier_id:
        raise HTTPException(422, "Yalnızca Tedarikçi rolündeki kullanıcılar bir tedarikçiye bağlanabilir.")


USER_SELECT = ("SELECT u.id, u.name, u.email, u.supplier_id, u.active, u.created_at, u.last_login_at, r.name AS role, "
               "s.name AS supplier_name FROM users u JOIN roles r ON r.id=u.role_id LEFT JOIN suppliers s ON s.id=u.supplier_id")


@router.get("/api/users")
def list_users():
    with get_db() as db:
        return rows(db.execute(USER_SELECT + " ORDER BY u.name COLLATE NOCASE").fetchall())


@router.get("/api/roles")
def list_roles():
    return [{"name": k, "title": v["title"], "permissions": v["perms"]} for k, v in ROLES.items()]


@router.post("/api/users", status_code=201)
def create_user(body: UserIn):
    if not body.password:
        raise HTTPException(422, "Parola gerekli (en az 8 karakter).")
    with get_db() as db:
        _check_user(db, body)
        email = _email(body.email)
        if db.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            raise HTTPException(409, "Bu e-posta ile bir kullanıcı zaten var.")
        uid = db.execute("INSERT INTO users (name, email, password_hash, role_id, supplier_id, active) VALUES (?,?,?,?,?,?)",
                         (body.name.strip(), email, hash_password(body.password), role_id(db, body.role),
                          body.supplier_id if body.role == "supplier" else None, int(body.active))).lastrowid
        log("user.create", "user", uid, {"email": email, "role": body.role, "supplier_id": body.supplier_id}, db=db)
        return row(db.execute(USER_SELECT + " WHERE u.id=?", (uid,)).fetchone())


@router.put("/api/users/{uid}")
def update_user(uid: int, body: UserIn):
    me_ = current_user()
    with get_db() as db:
        if not db.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone():
            raise HTTPException(404, "Kullanıcı bulunamadı")
        _check_user(db, body)
        if uid == me_["id"] and (body.role != "admin" or not body.active):
            raise HTTPException(422, "Kendi yönetici yetkinizi kaldıramaz veya hesabınızı pasifleştiremezsiniz.")
        email = _email(body.email)
        if db.execute("SELECT 1 FROM users WHERE email=? AND id!=?", (email, uid)).fetchone():
            raise HTTPException(409, "Bu e-posta başka bir kullanıcıda kayıtlı.")
        db.execute("UPDATE users SET name=?, email=?, role_id=?, supplier_id=?, active=? WHERE id=?",
                   (body.name.strip(), email, role_id(db, body.role), body.supplier_id if body.role == "supplier" else None,
                    int(body.active), uid))
        if body.password:
            db.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(body.password), uid))
        if not body.active or body.password:
            db.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
        log("user.update", "user", uid, {"role": body.role, "active": body.active, "password_reset": bool(body.password)}, db=db)
        return row(db.execute(USER_SELECT + " WHERE u.id=?", (uid,)).fetchone())


@router.delete("/api/users/{uid}", status_code=204)
def delete_user(uid: int):
    if uid == current_user()["id"]:
        raise HTTPException(422, "Kendi hesabınızı silemezsiniz.")
    with get_db() as db:
        db.execute("UPDATE users SET active=0 WHERE id=?", (uid,))
        db.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
        log("user.deactivate", "user", uid, db=db)


@router.get("/api/audit-logs")
def audit_logs(limit: int = 200, entity: Optional[str] = None, user_id: Optional[int] = None):
    q, p = "SELECT * FROM audit_logs WHERE 1=1", []
    if entity:
        q += " AND entity=?"; p.append(entity)
    if user_id:
        q += " AND user_id=?"; p.append(user_id)
    q += " ORDER BY id DESC LIMIT ?"; p.append(min(limit, 2000))
    with get_db() as db:
        return rows(db.execute(q, p).fetchall())


# ------------------------------------------------------------------ API: tedarikçi seviyeleri
class TierIn(BaseModel):
    name: str = Field(min_length=1)
    level: int = Field(ge=1, le=9)
    audit_interval_days: int = Field(default=365, ge=30, le=3650)
    description: Optional[str] = None


@router.get("/api/supplier-tiers")
def list_tiers():
    with get_db() as db:
        return rows(db.execute(
            "SELECT t.*, (SELECT COUNT(*) FROM suppliers s WHERE s.tier_id=t.id) AS supplier_count "
            "FROM supplier_tiers t ORDER BY level, name").fetchall())


@router.post("/api/supplier-tiers", status_code=201)
def create_tier(body: TierIn):
    with get_db() as db:
        tid = db.execute("INSERT INTO supplier_tiers (name, level, audit_interval_days, description) VALUES (?,?,?,?)",
                         (body.name, body.level, body.audit_interval_days, body.description)).lastrowid
        return row(db.execute("SELECT * FROM supplier_tiers WHERE id=?", (tid,)).fetchone())


@router.put("/api/supplier-tiers/{tid}")
def update_tier(tid: int, body: TierIn):
    with get_db() as db:
        db.execute("UPDATE supplier_tiers SET name=?, level=?, audit_interval_days=?, description=? WHERE id=?",
                   (body.name, body.level, body.audit_interval_days, body.description, tid))
        r = row(db.execute("SELECT * FROM supplier_tiers WHERE id=?", (tid,)).fetchone())
    if not r:
        raise HTTPException(404, "Seviye bulunamadı")
    return r


@router.delete("/api/supplier-tiers/{tid}", status_code=204)
def delete_tier(tid: int):
    with get_db() as db:
        if db.execute("SELECT 1 FROM suppliers WHERE tier_id=?", (tid,)).fetchone():
            raise HTTPException(409, "Bu seviyede tedarikçi var; önce tedarikçileri başka seviyeye taşıyın.")
        db.execute("DELETE FROM supplier_tiers WHERE id=?", (tid,))
