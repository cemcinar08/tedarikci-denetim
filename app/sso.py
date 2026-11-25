"""OpenID Connect ile tek oturum açma (Microsoft Entra ID, Google Workspace veya OIDC uyumlu herhangi bir sağlayıcı).

Akış: /api/auth/sso/start -> sağlayıcı (authorization code + PKCE) -> /api/auth/sso/callback -> oturum çerezi.
id_token, istemci kimlik doğrulamasıyla doğrudan token uç noktasından TLS üzerinden alınır (OIDC Core 3.1.3.7);
iss, aud, exp ve nonce kontrol edilir.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import time
from typing import Literal, Optional
from urllib.parse import quote, urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from . import auth
from .db import get_db, row

router = APIRouter()
DEFAULTS = {"enabled": False, "issuer": "", "client_id": "", "client_secret": "", "allowed_domains": "", "auto_create": False,
            "default_role": "viewer", "button_label": "Kurumsal hesapla giriş"}
STATE_TTL = 600


# ------------------------------------------------------------------ saf yardımcılar
def b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def pkce_pair() -> tuple[str, str]:
    v = b64url(secrets.token_bytes(32))
    return v, b64url(hashlib.sha256(v.encode()).digest())


def decode_jwt_claims(token: str) -> dict:
    """Saf: JWT gövdesini çözer (imza doğrulaması yapmaz; yalnızca token uç noktasından gelen id_token için)."""
    try:
        payload = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError) as e:
        raise ValueError("id_token çözülemedi") from e


def validate_claims(c: dict, issuer: str, client_id: str, nonce: str, now: Optional[float] = None) -> str:
    """Saf: claim'leri doğrular, kullanıcının e-postasını döner."""
    now = now or time.time()
    iss = c.get("iss", "")
    # Entra 'common/organizations' kullanımında issuer kiracıya göre değişir: {tenantid} kalıbını kabul et
    exp_iss = issuer.rstrip("/")
    if iss.rstrip("/") != exp_iss and not ("{tenantid}" in exp_iss and re.fullmatch(re.escape(exp_iss).replace(r"\{tenantid\}", r"[0-9a-f-]{36}"), iss.rstrip("/"))):
        raise ValueError("Geçersiz yayıncı (iss)")
    aud = c.get("aud")
    if (aud if isinstance(aud, list) else [aud]).count(client_id) == 0:
        raise ValueError("Geçersiz hedef (aud)")
    if float(c.get("exp", 0)) < now - 60:
        raise ValueError("Oturum belirtecinin süresi dolmuş")
    if c.get("nonce") != nonce:
        raise ValueError("Geçersiz nonce")
    email = (c.get("email") or c.get("preferred_username") or c.get("upn") or "").strip().lower()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise ValueError("Sağlayıcı e-posta adresi döndürmedi")
    if c.get("email_verified") is False:
        raise ValueError("E-posta adresi doğrulanmamış")
    return email


def domain_allowed(email: str, allowed: str) -> bool:
    doms = [d.strip().lower().lstrip("@") for d in re.split(r"[,\s;]+", allowed or "") if d.strip()]
    return not doms or email.split("@")[1] in doms


# ------------------------------------------------------------------ ayarlar
def get_cfg(db) -> dict:
    r = db.execute("SELECT value FROM settings WHERE key='sso'").fetchone()
    try:
        c = json.loads(r[0]) if r and r[0] else {}
    except ValueError:
        c = {}
    return {**DEFAULTS, **{k: v for k, v in c.items() if k in DEFAULTS}}


def _base(request: Request, db) -> str:
    r = db.execute("SELECT value FROM settings WHERE key='public_base_url'").fetchone()
    return (r[0] if r and r[0] else str(request.base_url)).rstrip("/")


class CfgIn(BaseModel):
    enabled: bool = False
    issuer: str = ""
    client_id: str = ""
    client_secret: Optional[str] = None
    allowed_domains: str = ""
    auto_create: bool = False
    default_role: Literal["viewer", "auditor"] = "viewer"
    button_label: str = "Kurumsal hesapla giriş"


@router.get("/api/auth/sso/config")
def public_config():
    with get_db() as db:
        c = get_cfg(db)
    return {"enabled": bool(c["enabled"] and c["issuer"] and c["client_id"]), "button_label": c["button_label"]}


@router.get("/api/integrations/sso")
def read_cfg(request: Request):
    with get_db() as db:
        c = get_cfg(db)
        base = _base(request, db)
    out = {k: v for k, v in c.items() if k != "client_secret"}
    return {**out, "client_secret_set": bool(c["client_secret"]), "redirect_uri": f"{base}/api/auth/sso/callback"}


@router.put("/api/integrations/sso")
def write_cfg(body: CfgIn, request: Request):
    d = body.model_dump()
    with get_db() as db:
        cur = get_cfg(db)
        if not d.get("client_secret"):
            d["client_secret"] = cur["client_secret"]
        d["issuer"] = d["issuer"].strip().rstrip("/")
        if d["enabled"] and not (d["issuer"].startswith("https://") and d["client_id"] and d["client_secret"]):
            raise HTTPException(422, "SSO'yu açmak için https:// yayıncı adresi, istemci kimliği ve istemci sırrı gerekli.")
        db.execute("INSERT INTO settings (key, value) VALUES ('sso', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(d),))
        auth.log("settings.sso", "settings", None, {"enabled": d["enabled"], "issuer": d["issuer"]}, db=db)
    return read_cfg(request)


# ------------------------------------------------------------------ HTTP (testlerde değiştirilebilir)
async def http_get(url: str, headers: Optional[dict] = None) -> dict:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(url, headers=headers or {})
    r.raise_for_status()
    return r.json()


async def http_post_form(url: str, data: dict) -> dict:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(url, data=data, headers={"Accept": "application/json"})
    if r.status_code >= 300:
        raise HTTPException(502, f"Token alınamadı (HTTP {r.status_code}): {r.text[:200]}")
    return r.json()


async def discovery(issuer: str) -> dict:
    url = issuer.replace("{tenantid}", "common") + "/.well-known/openid-configuration"
    return await http_get(url)


# ------------------------------------------------------------------ akış
def _fail(msg: str) -> RedirectResponse:
    return RedirectResponse("/#/?sso_error=" + quote(msg), status_code=302)


@router.get("/api/auth/sso/start")
async def start(request: Request):
    with get_db() as db:
        c = get_cfg(db)
        base = _base(request, db)
    if not (c["enabled"] and c["issuer"] and c["client_id"]):
        raise HTTPException(404, "SSO etkin değil")
    meta = await discovery(c["issuer"])
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    verifier, challenge = pkce_pair()
    with get_db() as db:
        db.execute("DELETE FROM sso_states WHERE created < ?", (time.time() - STATE_TTL,))
        db.execute("INSERT INTO sso_states (state, verifier, nonce, created) VALUES (?,?,?,?)", (state, verifier, nonce, time.time()))
    q = {"response_type": "code", "client_id": c["client_id"], "redirect_uri": f"{base}/api/auth/sso/callback",
         "scope": "openid email profile", "state": state, "nonce": nonce, "code_challenge": challenge, "code_challenge_method": "S256"}
    return RedirectResponse(f"{meta['authorization_endpoint']}?{urlencode(q)}", status_code=302)


@router.get("/api/auth/sso/callback")
async def callback(request: Request, code: str = "", state: str = "", error: str = "", error_description: str = ""):
    if error:
        return _fail(f"Sağlayıcı hatası: {error_description or error}")
    with get_db() as db:
        c = get_cfg(db)
        base = _base(request, db)
        st = row(db.execute("SELECT * FROM sso_states WHERE state=?", (state,)).fetchone())
        db.execute("DELETE FROM sso_states WHERE state=?", (state,))
    if not st or time.time() - st["created"] > STATE_TTL or not code:
        return _fail("Oturum açma isteği geçersiz veya süresi doldu. Tekrar deneyin.")
    try:
        meta = await discovery(c["issuer"])
        tok = await http_post_form(meta["token_endpoint"], {
            "grant_type": "authorization_code", "code": code, "redirect_uri": f"{base}/api/auth/sso/callback",
            "client_id": c["client_id"], "client_secret": c["client_secret"], "code_verifier": st["verifier"]})
        claims = decode_jwt_claims(tok.get("id_token", ""))
        email = validate_claims(claims, c["issuer"], c["client_id"], st["nonce"])
    except (ValueError, KeyError, HTTPException, httpx.HTTPError) as e:
        auth.log("auth.sso_failed", "user", None, {"error": str(getattr(e, "detail", e))[:200]}, user={"id": None, "email": None})
        return _fail(f"Kurumsal giriş başarısız: {getattr(e, 'detail', e)}")
    if not domain_allowed(email, c["allowed_domains"]):
        return _fail(f"{email.split('@')[1]} alan adına izin verilmiyor.")
    with get_db() as db:
        u = row(db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone())
        if not u:
            if not c["auto_create"]:
                return _fail(f"{email} için kullanıcı tanımlı değil. Yöneticinizden hesap açmasını isteyin.")
            uid = db.execute("INSERT INTO users (name, email, password_hash, role_id) VALUES (?,?,?,?)",
                             (claims.get("name") or email.split("@")[0], email, "sso-only$", auth.role_id(db, c["default_role"]))).lastrowid
            auth.log("user.sso_provisioned", "user", uid, {"email": email, "role": c["default_role"]}, user={"id": uid, "email": email}, db=db)
            u = row(db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone())
        if not u["active"]:
            return _fail("Hesabınız pasif.")
    token = auth.create_session(u["id"], request.client.host if request.client else None)
    auth.log("auth.sso_login", "user", u["id"], {"email": email}, user=u)
    resp = RedirectResponse("/#/", status_code=302)
    auth._set_cookie(resp, token, request)
    return resp
