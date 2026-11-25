"""Sprint 1: kimlik doğrulama, RBAC ve tedarikçi izolasyonu."""
import re

from fastapi.testclient import TestClient

from app import auth
from conftest import PW


def _users(admin):
    a = admin.post("/api/suppliers", json={"name": "A Tedarik", "notes": "iç not", "webhook_secret": "gizli",
                                           "notify_emails": "a@a.com"}).json()
    b = admin.post("/api/suppliers", json={"name": "B Tedarik"}).json()
    for email, role, sid in [("denetci@x.com", "auditor", None), ("izleyici@x.com", "viewer", None),
                             ("a@x.com", "supplier", a["id"]), ("b@x.com", "supplier", b["id"])]:
        r = admin.post("/api/users", json={"name": email, "email": email, "role": role, "supplier_id": sid, "password": PW})
        assert r.status_code == 201, r.text
    admin.post("/api/findings", json={"supplier_id": a["id"], "title": "A bulgusu"})
    admin.post("/api/findings", json={"supplier_id": b["id"], "title": "B bulgusu"})
    return a, b


def test_every_api_route_has_permission_rule(app_db):
    """Yeni bir uç PERMS'e eklenmeden yayına çıkamaz (eşleşmeyen uç = yalnız admin)."""
    missing = []
    for r in app_db.routes:
        path = getattr(r, "path", "")
        if not path.startswith("/api/") or auth.PUBLIC.match(path.replace("{", "").replace("}", "")):
            continue
        concrete = re.sub(r"\{[^}]+\}", "1", path)
        for m in r.methods - {"HEAD", "OPTIONS"}:
            matched, _ = auth.required_perm(m, concrete)
            if not matched:
                missing.append(f"{m} {path}")
    assert not missing, missing


def test_setup_login_logout(app_db):
    with TestClient(app_db) as c:
        assert c.get("/api/suppliers").status_code == 401
        assert c.get("/api/auth/status").json()["setup_required"] is True
        assert c.post("/api/auth/setup", json={"name": "A", "email": "x@y.com", "password": "kisa"}).status_code == 422
        assert c.post("/api/auth/setup", json={"name": "A", "email": "x@y.com", "password": PW}).status_code == 200
        assert c.post("/api/auth/setup", json={"name": "B", "email": "z@y.com", "password": PW}).status_code == 409
        assert c.get("/api/me").json()["role"] == "admin"
        c.post("/api/auth/logout")
        assert c.get("/api/me").status_code == 401
        assert c.post("/api/auth/login", json={"email": "x@y.com", "password": "yanlis"}).status_code == 401
        tok = c.post("/api/auth/login", json={"email": "X@y.com ", "password": PW}).json()["token"]
    with TestClient(app_db) as c2:  # Bearer token ile API erişimi
        assert c2.get("/api/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 200


def test_login_rate_limit(admin):
    for _ in range(5):
        admin.post("/api/auth/login", json={"email": "admin@test.com", "password": "yanlis"})
    assert admin.post("/api/auth/login", json={"email": "admin@test.com", "password": PW}).status_code == 429
    auth._FAILS.clear()


def test_role_matrix(admin, login):
    a, _ = _users(admin)
    cases = [  # (yöntem, yol, gövde, izin verilen roller)
        ("GET", "/api/suppliers", None, {"auditor", "viewer", "supplier"}),
        ("POST", "/api/suppliers", {"name": "Yeni"}, {"auditor"}),
        ("GET", "/api/question-sets", None, {"auditor", "viewer"}),
        ("POST", "/api/findings", {"supplier_id": a["id"], "title": "x"}, {"auditor"}),
        ("GET", "/api/users", None, set()),
        ("PUT", "/api/settings", {"org_name": "x"}, set()),
        ("GET", "/api/audit-logs", None, set()),
        ("GET", "/api/notifications", None, {"auditor"}),
        ("POST", "/api/supplier-tiers", {"name": "T9", "level": 9}, set()),
    ]
    users = {"auditor": "denetci@x.com", "viewer": "izleyici@x.com", "supplier": "a@x.com"}
    for role, email in users.items():
        c = login(email)
        for m, path, body, allowed in cases:
            r = c.request(m, path, json=body)
            if role in allowed:
                assert r.status_code < 400, (role, m, path, r.status_code, r.text)
            else:
                assert r.status_code == 403, (role, m, path, r.status_code)
    # admin her şeye erişir
    assert admin.get("/api/users").status_code == 200 and admin.get("/api/audit-logs").status_code == 200


def test_supplier_isolation(admin, login):
    a, b = _users(admin)
    gs = next(x for x in admin.get("/api/question-sets").json() if x["standard"] == "Genel")
    aud_b = admin.post(f"/api/suppliers/{b['id']}/audits", json={"audit_date": "2026-01-01", "question_set_ids": [gs["id"]]}).json()
    aud_a = admin.post(f"/api/suppliers/{a['id']}/audits", json={"audit_date": "2026-01-01", "question_set_ids": [gs["id"]]}).json()

    c = login("a@x.com")
    sups = c.get("/api/suppliers").json()
    assert [s["name"] for s in sups] == ["A Tedarik"]
    assert "notes" not in sups[0] and "webhook_secret" not in sups[0]
    detail = c.get(f"/api/suppliers/{a['id']}").json()
    assert detail["name"] == "A Tedarik" and "notes" not in detail
    assert c.get(f"/api/suppliers/{b['id']}").status_code == 404           # varlığı bile sızmaz
    assert [f["title"] for f in c.get("/api/findings").json()] == ["A bulgusu"]
    assert [f["title"] for f in c.get(f"/api/findings?supplier_id={b['id']}").json()] == []
    assert [x["id"] for x in c.get("/api/audits").json()] == [aud_a["id"]]
    assert c.get(f"/api/audits/{aud_b['id']}").status_code == 404
    assert c.get(f"/api/audits/{aud_a['id']}").status_code == 200
    d = c.get("/api/dashboard").json()
    assert d["totals"]["suppliers"] == 1 and [r["name"] for r in d["ranking"]] == ["A Tedarik"]
    # Denetçi her şeyi görür
    assert len(login("denetci@x.com").get("/api/suppliers").json()) == 2


def test_user_management_rules(admin, login):
    a, _ = _users(admin)
    assert admin.post("/api/users", json={"name": "x", "email": "s@x.com", "role": "supplier", "password": PW}).status_code == 422
    assert admin.post("/api/users", json={"name": "x", "email": "v@x.com", "role": "viewer", "supplier_id": a["id"], "password": PW}).status_code == 422
    assert admin.post("/api/users", json={"name": "x", "email": "a@x.com", "role": "viewer", "password": PW}).status_code == 409
    me = admin.get("/api/me").json()
    assert admin.put(f"/api/users/{me['id']}", json={"name": "A", "email": "admin@test.com", "role": "viewer"}).status_code == 422
    # pasifleştirilen kullanıcının oturumu düşer
    c = login("izleyici@x.com")
    uid = next(u["id"] for u in admin.get("/api/users").json() if u["email"] == "izleyici@x.com")
    assert admin.delete(f"/api/users/{uid}").status_code == 204
    assert c.get("/api/suppliers").status_code == 401


def test_audit_logs_and_tiers(admin):
    tiers = admin.get("/api/supplier-tiers").json()
    assert [t["level"] for t in tiers] == [1, 2, 3]
    s = admin.post("/api/suppliers", json={"name": "Seviyeli", "tier_id": tiers[0]["id"]}).json()
    assert s["tier_name"].startswith("Tier 1")
    assert admin.post("/api/suppliers", json={"name": "X", "tier_id": 999}).status_code == 422
    assert admin.delete(f"/api/supplier-tiers/{tiers[0]['id']}").status_code == 409
    logs = admin.get("/api/audit-logs").json()
    actions = [x["action"] for x in logs]
    assert "POST /api/suppliers" in actions and "auth.setup" in actions
    assert logs[0]["actor"] == "admin@test.com"


def test_password_hashing():
    h = auth.hash_password("gizli-parola")
    assert h.startswith("scrypt$") and "gizli" not in h
    assert auth.verify_password("gizli-parola", h) and not auth.verify_password("x", h)
    assert not auth.verify_password("x", "bozuk")
