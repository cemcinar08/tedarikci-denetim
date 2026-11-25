"""Grup H: AI kanıt incelemesi (Claude çağrısı sahte)."""
from app import ai_review

QS = [{"id": "q-mfa", "text": "MFA var mı?"}, {"id": "q-yedek", "text": "Yedek var mı?"}, {"id": "q-log", "text": "Log var mı?"}]
DOCS = [{"evidence_id": 11, "title": "SOC 2 Raporu"}]


def _blocks():
    return [
        {"type": "text", "text": "### Q1\nCevap: uygun\nGerekçe: Tüm kullanıcılar için", "citations": None},
        {"type": "text", "text": "MFA zorunlu", "citations": [
            {"type": "page_location", "document_index": 0, "start_page_number": 4, "end_page_number": 5, "cited_text": "MFA is enforced for all users."}]},
        {"type": "text", "text": ".\n\n### Q2\nCevap: Kısmen.\nGerekçe: Yedek var,\nfakat test edilmiyor.\n\n### Q3\nCevap: belirsiz\nGerekçe: Belgede yok.",
         "citations": None},
    ]


def test_parse_response():
    out = {s["question_id"]: s for s in ai_review.parse_response(_blocks(), QS, DOCS)}
    assert out["q-mfa"]["answer"] == "uygun" and "MFA zorunlu" in out["q-mfa"]["rationale"]
    c = out["q-mfa"]["citations"][0]
    assert c == {"evidence_id": 11, "document": "SOC 2 Raporu", "page_start": 4, "page_end": 5, "quote": "MFA is enforced for all users."}
    assert out["q-yedek"]["answer"] == "kısmen" and out["q-yedek"]["rationale"] == "Yedek var, fakat test edilmiyor."
    assert out["q-yedek"]["citations"] == [] and out["q-log"]["answer"] == "belirsiz"
    # "uygun değil" önekiyle "uygun" karışmamalı; bilinmeyen cevap atlanır
    o2 = ai_review.parse_response([{"type": "text", "text": "### Q1\nCevap: uygun değil\n### Q2\nCevap: evet"}], QS, DOCS)
    assert [(s["question_id"], s["answer"]) for s in o2] == [("q-mfa", "uygun değil")]


def test_note_and_prompt():
    s = {"rationale": "MFA zorunlu.", "citations": [{"document": "SOC 2", "page_start": 4}]}
    assert ai_review.note_for(s) == "AI önerisi: MFA zorunlu. (Kaynak: SOC 2 s.4)"
    assert "Q2: Yedek var mı?" in ai_review.build_prompt(QS)


def test_review_flow(world, monkeypatch):
    den, sa, aid, q = world["den"], world["sa"], world["audit"]["id"], world["q"]
    assert den.post(f"/api/audits/{aid}/ai-review", json={}).status_code == 422          # kanıt yok
    den.post("/api/evidence", data={"audit_id": aid, "question_id": q["MFA"], "title": "SOC 2 Raporu"}, files={"file": ("soc2.pdf", b"%PDF-1.4 x")})
    den.post("/api/evidence", data={"audit_id": aid}, files={"file": ("ekran.png", b"\x89PNG..")})
    sent = {}

    def fake(key, content):
        sent["content"] = content
        cit = [{"document_index": 0, "start_page_number": 2, "end_page_number": 2, "cited_text": "MFA"}]
        return {"blocks": [{"type": "text", "text": "### Q1\nCevap: uygun\nGerekçe: "},
                           {"type": "text", "text": "MFA zorunlu.", "citations": cit},
                           {"type": "text", "text": "\n### Q2\nCevap: kısmen\nGerekçe: Test yok.\n### Q3\nCevap: belirsiz\nGerekçe: Yok."}],
                "stop_reason": "end_turn", "model": "claude-opus-5-5", "usage": {"input_tokens": 100, "output_tokens": 50}}
    monkeypatch.setattr(ai_review, "call_claude", fake)
    # tedarikçi çalıştıramaz / göremez
    assert sa.post(f"/api/audits/{aid}/ai-review", json={}).status_code == 403
    assert sa.get(f"/api/audits/{aid}/ai-suggestions").status_code == 403
    r = den.post(f"/api/audits/{aid}/ai-review", json={})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["documents"] == 1 and j["suggestions"] == 3 and any("ekran.png" in s for s in j["skipped"])
    doc = sent["content"][0]
    assert doc["type"] == "document" and doc["source"]["media_type"] == "application/pdf" and doc["citations"] == {"enabled": True}
    assert sent["content"][-1]["type"] == "text"
    items = {s["question_id"]: s for s in j["items"]}
    mfa = items[q["MFA"]]
    # belirsiz uygulanamaz
    assert den.post(f"/api/ai-suggestions/{items[q['Log']]['id']}/decide", json={"decision": "accept"}).status_code == 422
    assert den.post(f"/api/ai-suggestions/{mfa['id']}/decide", json={"decision": "accept"}).status_code == 200
    assert den.post(f"/api/ai-suggestions/{mfa['id']}/decide", json={"decision": "accept"}).status_code == 409
    assert den.post(f"/api/ai-suggestions/{items[q['Yed']]['id']}/decide", json={"decision": "reject"}).status_code == 200
    a = den.get(f"/api/audits/{aid}").json()
    assert a["answers"][q["MFA"]]["answer"] == "uygun" and "Kaynak: SOC 2 Raporu s.2" in a["answers"][q["MFA"]]["note"]
    assert q["Yed"] not in a["answers"]
    st = {s["question_id"]: s["status"] for s in den.get(f"/api/audits/{aid}/ai-suggestions").json()}
    assert st[q["MFA"]] == "kabul" and st[q["Yed"]] == "red"
    # varsayılan: yalnızca cevapsız sorular yeniden incelenir
    j2 = den.post(f"/api/audits/{aid}/ai-review", json={}).json()
    assert j2["questions"] == 2


def test_refusal_and_key_settings(world, admin, monkeypatch):
    den, aid, q = world["den"], world["audit"]["id"], world["q"]
    den.post("/api/evidence", data={"audit_id": aid, "question_id": q["MFA"]}, files={"file": ("p.pdf", b"%PDF-1.4 x")})
    monkeypatch.setattr(ai_review, "call_claude", lambda k, c: {"blocks": [], "stop_reason": "refusal", "model": "m", "usage": {}})
    assert den.post(f"/api/audits/{aid}/ai-review", json={}).status_code == 422
    assert den.put("/api/integrations/ai", json={"api_key": "sk-ant-x"}).status_code == 403
    assert admin.put("/api/integrations/ai", json={"api_key": "yanlis"}).status_code == 422
    r = admin.put("/api/integrations/ai", json={"api_key": "sk-ant-abc"}).json()
    assert r["key_set"] is True and "sk-ant" not in str(r)
    assert admin.put("/api/integrations/ai", json={"api_key": ""}).json()["key_set"] is False


def test_supplier_isolation(world):
    other = world["sb"]
    assert other.post(f"/api/audits/{world['audit']['id']}/ai-review", json={}).status_code == 403
