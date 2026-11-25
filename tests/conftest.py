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
