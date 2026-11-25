import os

import pytest

os.environ["TD_SCHEDULER"] = "0"  # testlerde arka plan zamanlayıcısı çalışmasın
from fastapi.testclient import TestClient

PW = "parola1234"


@pytest.fixture
def app_db(tmp_path, monkeypatch):
    monkeypatch.setattr("app.db.DB_PATH", tmp_path / "t.db")
    from app.main import app
    return app


@pytest.fixture
def admin(app_db):
    with TestClient(app_db) as c:
        assert c.post("/api/auth/setup", json={"name": "Admin", "email": "admin@test.com", "password": PW}).status_code == 200
        yield c


@pytest.fixture
def login(app_db):
    """login(email) -> o kullanıcıyla oturum açmış ayrı bir istemci."""
    clients = []

    def _login(email, password=PW):
        c = TestClient(app_db)
        c.__enter__()
        clients.append(c)
        r = c.post("/api/auth/login", json={"email": email, "password": password})
        assert r.status_code == 200, r.text
        return c
    yield _login
    for c in clients:
        c.__exit__(None, None, None)


@pytest.fixture
def world(admin, login):
    a = admin.post("/api/suppliers", json={"name": "A"}).json()
    b = admin.post("/api/suppliers", json={"name": "B"}).json()
    for email, role, sid in [("den@x.com", "auditor", None), ("a@x.com", "supplier", a["id"]), ("b@x.com", "supplier", b["id"])]:
        assert admin.post("/api/users", json={"name": email.split("@")[0], "email": email, "role": role, "supplier_id": sid,
                                              "password": PW}).status_code == 201
    t = admin.post("/api/question-sets", json={"name": "T", "sections": [{"title": "Güvenlik", "questions": [
        {"code": "mfa", "text": "MFA var mı?", "critical": True}, {"code": "yedek", "text": "Yedek var mı?"},
        {"code": "log", "text": "Log var mı?"}]}]}).json()
    aud = admin.post(f"/api/suppliers/{a['id']}/audits", json={"audit_date": "2026-01-01", "question_set_ids": [t["id"]]}).json()
    q = {x["text"][:3]: x["id"] for x in aud["checklist"][0]["questions"]}
    return {"a": a, "b": b, "audit": aud, "q": q, "den": login("den@x.com"), "sa": login("a@x.com"), "sb": login("b@x.com")}
