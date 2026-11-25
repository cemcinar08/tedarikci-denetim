"""Grup G: Slack/Teams biçimleri, Jira/ServiceNow, SSO (OIDC)."""
import base64
import json
import time
from urllib.parse import parse_qs, urlparse

import pytest

from app import events, sso, ticketing
from conftest import PW

BODY = {"event": "finding.status_changed", "entity": "finding", "entity_id": 7, "supplier": {"id": 1, "name": "Acme"},
        "data": {"title": "TLS 1.0 açık", "severity": "yüksek", "from": "açık", "to": "kapandı", "reason": "CAPA onaylandı"}}


# ------------------------------------------------------------------ Slack / Teams
def test_message_formats():
    m = events.summarize_event(BODY, "https://denetim.abc.com/")
    assert m["head"].startswith("🟠 Bulgu durumu") and m["main"] == "TLS 1.0 açık"
    assert "Durum: açık → kapandı (CAPA onaylandı)" in m["lines"] and m["link"] == "https://denetim.abc.com/#/bulgular"
    sl = events.slack_body(BODY, "https://x")
    assert sl["blocks"][0]["text"]["type"] == "mrkdwn" and "<https://x/#/bulgular|Uygulamada aç>" in sl["blocks"][0]["text"]["text"]
    tm = events.teams_body(BODY, None)
    card = tm["attachments"][0]["content"]
    assert card["type"] == "AdaptiveCard" and card["body"][0]["text"].endswith("Bulgu durumu değişti") and "actions" not in card


def test_slack_delivery(admin, monkeypatch):
    sent = []

    async def fake(url, body, headers):
        sent.append((url, json.loads(body), headers)); return 200
    monkeypatch.setattr(events, "post", fake)
    admin.post("/api/webhooks", json={"name": "Slack", "url": "https://hooks.slack.com/services/x", "format": "slack", "secret": "s"})
    admin.post("/api/webhooks", json={"name": "Teams", "url": "https://prod.westeurope.logic.azure.com/x", "format": "teams"})
    assert admin.post("/api/webhooks", json={"name": "x", "url": "https://x", "format": "discord"}).status_code == 422
    s = admin.post("/api/suppliers", json={"name": "Acme"}).json()
    admin.post("/api/findings", json={"supplier_id": s["id"], "title": "Yeni sorun", "severity": "kritik"})
    by = {u.split("/")[2]: (b, h) for u, b, h in sent}
    assert "blocks" in by["hooks.slack.com"][0] and "X-TD-Signature" not in by["hooks.slack.com"][1]
    assert by["prod.westeurope.logic.azure.com"][0]["type"] == "message"


# ------------------------------------------------------------------ Jira / ServiceNow
def test_ticket_payloads():
    item = {"id": 5, "kind": "finding", "title": "MFA yok", "supplier_name": "Acme", "severity": "kritik", "description": "Açıklama",
            "action": "MFA aç", "due_date": "2026-12-01"}
    j = ticketing.jira_payload({"jira_project_key": "SEC", "jira_issue_type": "Bug"}, item, "https://app/#/bulgular")
    assert j["fields"]["summary"] == "[Acme] MFA yok" and j["fields"]["project"]["key"] == "SEC" and j["fields"]["description"]["type"] == "doc"
    assert any(c["content"] and "Termin: 2026-12-01" in c["content"][0]["text"] for c in j["fields"]["description"]["content"])
    sn = ticketing.servicenow_payload({"servicenow_assignment_group": "SecOps"}, item, None)
    assert sn["urgency"] == "1" and sn["assignment_group"] == "SecOps"
    assert ticketing.is_done("jira", {"category": "done"}) and not ticketing.is_done("jira", {"category": "indeterminate"})
    assert ticketing.is_done("servicenow", {"state": "6"}) and not ticketing.is_done("servicenow", {"state": "2"})


def test_jira_flow(admin, login, monkeypatch):
    calls = []

    async def fake_http(method, url, headers, body=None):
        calls.append((method, url, headers, body))
        if method == "POST":
            return 201, {"id": "10001", "key": "SEC-42"}
        if "/project/" in url:
            return 200, {"name": "Güvenlik"}
        return 200, {"fields": {"status": {"name": "Done", "statusCategory": {"key": "done"}}}}
    monkeypatch.setattr(ticketing, "http", fake_http)
    s = admin.post("/api/suppliers", json={"name": "Acme"}).json()
    f = admin.post("/api/findings", json={"supplier_id": s["id"], "title": "MFA yok", "severity": "yüksek"}).json()
    assert admin.post("/api/tickets", json={"entity": "finding", "entity_id": f["id"]}).status_code == 400   # yapılandırılmamış
    assert admin.put("/api/integrations/ticketing", json={"provider": "jira", "jira_base_url": "http://x"}).status_code == 422
    c = admin.put("/api/integrations/ticketing", json={"provider": "jira", "jira_base_url": "https://acme.atlassian.net/", "jira_email": "a@acme.com",
                                                       "jira_api_token": "tok", "jira_project_key": "SEC", "auto_close": True}).json()
    assert c["ready"] and c["jira_api_token_set"] and "jira_api_token" not in c
    # gizli alan boş gönderilince korunur
    assert admin.put("/api/integrations/ticketing", json={**{k: v for k, v in c.items() if k in ticketing.DEFAULTS}, "jira_api_token": ""}).json()["ready"]
    assert admin.post("/api/integrations/ticketing/test").json()["message"] == "Proje: Güvenlik"
    t = admin.post("/api/tickets", json={"entity": "finding", "entity_id": f["id"]}).json()
    assert t["key"] == "SEC-42" and t["url"] == "https://acme.atlassian.net/browse/SEC-42"
    assert calls[-1][2]["Authorization"] == "Basic " + base64.b64encode(b"a@acme.com:tok").decode()
    assert admin.post("/api/tickets", json={"entity": "finding", "entity_id": f["id"]}).status_code == 409
    r = admin.post(f"/api/tickets/{t['id']}/sync").json()
    assert r["status"] == "tamamlandı" and r["remote_status"] == "Done"
    assert next(x for x in admin.get("/api/findings").json() if x["id"] == f["id"])["status"] == "kapandı"   # auto_close
    admin.post("/api/users", json={"name": "t", "email": "t@x.com", "role": "supplier", "supplier_id": s["id"], "password": PW})
    t2 = login("t@x.com")
    assert t2.get("/api/tickets").status_code == 403 and t2.get("/api/integrations/ticketing").status_code == 403


def test_servicenow_create(admin, monkeypatch):
    async def fake_http(method, url, headers, body=None):
        assert url == "https://acme.service-now.com/api/now/table/incident"
        return 201, {"result": {"sys_id": "abc123", "number": "INC0010001"}}
    monkeypatch.setattr(ticketing, "http", fake_http)
    admin.put("/api/integrations/ticketing", json={"provider": "servicenow", "servicenow_instance_url": "https://acme.service-now.com",
                                                   "servicenow_username": "u", "servicenow_password": "p"})
    s = admin.post("/api/suppliers", json={"name": "Acme"}).json()
    f = admin.post("/api/findings", json={"supplier_id": s["id"], "title": "x"}).json()
    c = admin.post("/api/capa", json={"finding_id": f["id"]}).json()
    t = admin.post("/api/tickets", json={"entity": "capa", "entity_id": c["id"]}).json()
    assert t["key"] == "INC0010001" and "sys_id=abc123" in t["url"]


# ------------------------------------------------------------------ SSO
def _jwt(claims):
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()  # noqa: E731
    return f"{enc({'alg': 'RS256'})}.{enc(claims)}.sig"


def test_claims_validation():
    now = time.time()
    c = {"iss": "https://accounts.google.com", "aud": "cid", "exp": now + 300, "nonce": "n", "email": "Ali@ABC.com", "email_verified": True}
    assert sso.validate_claims(c, "https://accounts.google.com", "cid", "n", now) == "ali@abc.com"
    for bad in [{"iss": "https://evil"}, {"aud": "x"}, {"exp": now - 600}, {"nonce": "z"}, {"email": "yok"}, {"email_verified": False}]:
        with pytest.raises(ValueError):
            sso.validate_claims({**c, **bad}, "https://accounts.google.com", "cid", "n", now)
    ms = {**c, "iss": "https://login.microsoftonline.com/72f988bf-86f1-41af-91ab-2d7cd011db47/v2.0", "email": None, "preferred_username": "veli@abc.com"}
    assert sso.validate_claims(ms, "https://login.microsoftonline.com/{tenantid}/v2.0", "cid", "n", now) == "veli@abc.com"
    assert sso.domain_allowed("a@abc.com", "abc.com, xyz.com") and not sso.domain_allowed("a@evil.com", "abc.com") and sso.domain_allowed("a@x.com", "")
    assert sso.decode_jwt_claims(_jwt({"a": 1})) == {"a": 1}


def test_sso_flow(app_db, admin, monkeypatch):
    from fastapi.testclient import TestClient
    meta = {"authorization_endpoint": "https://idp/authorize", "token_endpoint": "https://idp/token"}

    async def fake_get(url, headers=None):
        assert url == "https://idp/.well-known/openid-configuration"
        return meta
    captured = {}

    async def fake_post(url, data):
        captured["data"] = data
        return {"id_token": _jwt({"iss": "https://idp", "aud": "cid", "exp": time.time() + 300, "nonce": captured["nonce"],
                                  "email": captured["email"], "name": "Ali Veli"})}
    monkeypatch.setattr(sso, "http_get", fake_get)
    monkeypatch.setattr(sso, "http_post_form", fake_post)
    assert admin.get("/api/auth/sso/config").json()["enabled"] is False
    assert admin.put("/api/integrations/sso", json={"enabled": True, "issuer": "https://idp"}).status_code == 422
    admin.put("/api/integrations/sso", json={"enabled": True, "issuer": "https://idp/", "client_id": "cid", "client_secret": "sec",
                                             "allowed_domains": "abc.com", "auto_create": True, "default_role": "viewer"})
    cfg = admin.get("/api/integrations/sso").json()
    assert cfg["client_secret_set"] and cfg["redirect_uri"].endswith("/api/auth/sso/callback") and "client_secret" not in cfg

    def run(email):
        with TestClient(app_db) as c:
            r = c.get("/api/auth/sso/start", follow_redirects=False)
            q = parse_qs(urlparse(r.headers["location"]).query)
            assert q["code_challenge_method"] == ["S256"] and q["client_id"] == ["cid"]
            captured.update(nonce=q["nonce"][0], email=email)
            r = c.get(f"/api/auth/sso/callback?code=abc&state={q['state'][0]}", follow_redirects=False)
            me = c.get("/api/me")
            # aynı state ikinci kez kullanılamaz
            again = c.get(f"/api/auth/sso/callback?code=abc&state={q['state'][0]}", follow_redirects=False)
            return r, me, again
    r, me, again = run("ali@abc.com")
    assert r.status_code == 302 and r.headers["location"] == "/#/" and me.json()["role"] == "viewer" and me.json()["name"] == "Ali Veli"
    assert "sso_error" in again.headers["location"]
    assert captured["data"]["code_verifier"] and captured["data"]["client_secret"] == "sec"
    r, me, _ = run("kisi@evil.com")
    assert "sso_error" in r.headers["location"] and me.status_code == 401
    # SSO ile açılan hesap parola ile giriş yapamaz
    with TestClient(app_db) as c:
        assert c.post("/api/auth/login", json={"email": "ali@abc.com", "password": "sso-only$"}).status_code == 401
    # otomatik oluşturma kapalıysa tanımsız kullanıcı reddedilir; mevcut kullanıcı girer
    admin.put("/api/integrations/sso", json={"enabled": True, "issuer": "https://idp", "client_id": "cid", "auto_create": False, "allowed_domains": ""})
    r, me, _ = run("yeni@abc.com")
    assert "sso_error" in r.headers["location"]
    admin.post("/api/users", json={"name": "Den", "email": "den@abc.com", "role": "auditor", "password": PW})
    r, me, _ = run("den@abc.com")
    assert me.json()["role"] == "auditor"
