"""Sprint 2: koşullu sorular, ağırlıklı puanlama, kill-switch (saf fonksiyonlar) ve şablon API'si."""
import pytest

from app import templates_engine as eng

SECS = [
    {"id": "a", "title": "Kalite", "weight": 30, "questions": [
        {"id": "a1", "text": "ISO 9001 var mı?"},
        {"id": "a1x", "text": "Yoksa el kitabı var mı?", "condition": {"q": "a1", "op": "in", "value": ["kısmen", "uygun değil"]}},
        {"id": "a1y", "text": "El kitabı da yoksa plan var mı?", "condition": {"q": "a1x", "op": "eq", "value": "uygun değil"}},
        {"id": "a2", "text": "Kalibrasyon", "weight": 3},
    ]},
    {"id": "b", "title": "Etik", "weight": 10, "questions": [
        {"id": "b1", "text": "Çocuk işçi yok mu?", "critical": True},
        {"id": "b2", "text": "Rüşvet politikası"},
    ]},
]


# ------------------------------------------------------------------ koşullar
@pytest.mark.parametrize("cond,answers,expected", [
    (None, {}, True),
    ({"q": "x", "op": "eq", "value": "uygun"}, {"x": "uygun"}, True),
    ({"q": "x", "op": "eq", "value": "uygun"}, {}, False),
    ({"q": "x", "op": "neq", "value": "uygun"}, {"x": "kısmen"}, True),
    ({"q": "x", "op": "neq", "value": "uygun"}, {}, False),           # cevapsız soru koşulu sağlamaz
    ({"q": "x", "op": "in", "value": ["kısmen", "na"]}, {"x": "na"}, True),
    ({"q": "x", "op": "answered"}, {"x": "uygun değil"}, True),
    ({"q": "x", "op": "answered"}, {}, False),
    ({"all": [{"q": "x", "op": "answered"}, {"q": "y", "op": "eq", "value": "uygun"}]}, {"x": "na", "y": "uygun"}, True),
    ({"any": [{"q": "x", "op": "answered"}, {"q": "y", "op": "eq", "value": "uygun"}]}, {"y": "kısmen"}, False),
    ({"not": {"q": "x", "op": "eq", "value": "uygun"}}, {"x": "kısmen"}, True),
])
def test_evaluate_condition(cond, answers, expected):
    assert eng.evaluate_condition(cond, answers) is expected


def test_child_question_opens_and_closes():
    assert eng.visible_question_ids(SECS, {}) == {"a1", "a2", "b1", "b2"}
    assert "a1x" in eng.visible_question_ids(SECS, {"a1": "uygun değil"})
    # zincirleme: a1x 'uygun değil' -> a1y açılır
    assert {"a1x", "a1y"} <= eng.visible_question_ids(SECS, {"a1": "kısmen", "a1x": "uygun değil"})
    # üst soru 'uygun' olunca alt soru ve onun alt sorusu kapanır (eski cevaplar yok sayılır)
    v = eng.visible_question_ids(SECS, {"a1": "uygun", "a1x": "uygun değil", "a1y": "uygun"})
    assert "a1x" not in v and "a1y" not in v


def test_validate_condition_and_cycles():
    assert eng.validate_condition({"q": "a1", "op": "eq", "value": "uygun"}, {"a1"}) == []
    assert eng.validate_condition({"q": "zz", "op": "eq", "value": "uygun"}, {"a1"})
    assert eng.validate_condition({"q": "a1", "op": "eq", "value": "evet"}, {"a1"})
    assert eng.validate_condition({"q": "a1", "op": "gt"}, {"a1"})
    assert eng.validate_condition({"all": []}, {"a1"})
    cyc = [{"id": "s", "title": "S", "questions": [
        {"id": "x", "text": "x", "condition": {"q": "y", "op": "answered"}},
        {"id": "y", "text": "y", "condition": {"q": "x", "op": "answered"}}]}]
    assert set(eng.find_cycles(cyc)) == {"x", "y"} and eng.find_cycles(SECS) == []


def test_prefix_condition():
    c = {"all": [{"q": "a1", "op": "answered"}, {"not": {"q": "b1", "op": "eq", "value": "uygun"}}]}
    p = eng.prefix_condition(c, "q7.")
    assert p["all"][0]["q"] == "q7.a1" and p["all"][1]["not"]["q"] == "q7.b1" and c["all"][0]["q"] == "a1"


# ------------------------------------------------------------------ puanlama
def test_weighted_score():
    # Kalite: a1 uygun (w1) + a2 kısmen (w3) = (1 + 1.5)/4 = 62.5 ; Etik: 100 ; toplam = (62.5*30 + 100*10)/40 = 71.875
    r = eng.score_audit(SECS, {"a1": "uygun", "a2": "kısmen", "b1": "uygun", "b2": "uygun"})
    assert r["sections"][0]["score"] == 62.5 and r["sections"][1]["score"] == 100
    assert r["score"] == 71.9 and r["decision"] == "conditional" and r["risk_level"] == "medium"
    assert not r["kill_switch"]


def test_na_hidden_and_unanswered_excluded():
    r = eng.score_audit(SECS, {"a1": "uygun", "a1x": "uygun değil", "a2": "na", "b1": "uygun"})
    # a1x gizli (a1 uygun) -> puanlanmaz; a2 na; b2 cevapsız
    assert r["score"] == 100 and "a1x" in r["hidden_question_ids"]
    assert r["answered"] == 3 and r["visible_questions"] == 4
    assert eng.score_audit(SECS, {})["score"] is None


def test_kill_switch_overrides_score():
    ans = {"a1": "uygun", "a2": "uygun", "b1": "uygun değil", "b2": "uygun"}
    r = eng.score_audit(SECS, ans)
    assert r["score"] > 80  # puan yüksek olsa da
    assert r["kill_switch"] and r["decision"] == "rejected" and r["risk_level"] == "high"
    assert r["kill_switches"][0]["question_id"] == "b1"
    # kısmen kill-switch tetiklemez
    assert not eng.score_audit(SECS, {**ans, "b1": "kısmen"})["kill_switch"]


def test_hidden_critical_question_does_not_kill():
    secs = [{"id": "s", "title": "S", "questions": [
        {"id": "p", "text": "Kart verisi saklanıyor mu?"},
        {"id": "c", "text": "CVV saklanmıyor mu?", "critical": True, "condition": {"q": "p", "op": "eq", "value": "uygun"}}]}]
    assert not eng.score_audit(secs, {"p": "na", "c": "uygun değil"})["kill_switch"]
    assert eng.score_audit(secs, {"p": "uygun", "c": "uygun değil"})["kill_switch"]


@pytest.mark.parametrize("score,decision,risk", [
    (85, "approved", "low"), (84.9, "conditional", "medium"), (70, "conditional", "medium"),
    (69.9, "improvement_required", "high"), (50, "improvement_required", "high"), (49.9, "rejected", "high")])
def test_thresholds(score, decision, risk):
    c = eng.classify(score)
    assert c["decision"] == decision and c["risk_level"] == risk


# ------------------------------------------------------------------ API
def test_template_api_conditions_and_audit(admin):
    body = {"name": "Koşullu Set", "sections": [{"title": "Belgeler", "weight": 10, "questions": [
        {"code": "iso", "text": "ISO 27001 var mı?"},
        {"code": "soc", "text": "Yoksa SOC 2 raporu var mı?", "condition": {"q": "iso", "op": "eq", "value": "uygun değil"}},
        "! Veri sızıntısı bildirimi 72 saat içinde yapılıyor mu?",
    ]}]}
    t = admin.post("/api/question-sets", json=body)
    assert t.status_code == 201, t.text
    t = t.json()
    qs = t["sections"][0]["questions"]
    assert qs[1]["condition"]["q"] == "iso" and qs[2]["critical"] and t["critical_count"] == 1 and t["conditional_count"] == 1
    bad = {**body, "name": "Bozuk", "sections": [{"title": "B", "questions": [
        {"code": "a", "text": "a", "condition": {"q": "yok", "op": "answered"}}]}]}
    assert admin.post("/api/question-sets", json=bad).status_code == 422
    cyc = {**body, "name": "Döngü", "sections": [{"title": "B", "questions": [
        {"code": "a", "text": "a", "condition": {"q": "b", "op": "answered"}}, {"code": "b", "text": "b", "condition": {"q": "a", "op": "answered"}}]}]}
    assert admin.post("/api/question-sets", json=cyc).status_code == 422

    sid = admin.post("/api/suppliers", json={"name": "T", "criticality": "orta"}).json()["id"]
    a = admin.post(f"/api/suppliers/{sid}/audits", json={"audit_date": "2026-01-01", "question_set_ids": [t["id"]]}).json()
    q = {x["text"][:5]: x for x in a["checklist"][0]["questions"]}
    assert q["Yoksa"]["condition"]["q"] == q["ISO 2"]["id"]           # koşul denetim id'sine çevrildi
    assert a["result"]["hidden_question_ids"] == [q["Yoksa"]["id"]]
    ans = {q["ISO 2"]["id"]: {"answer": "uygun"}, q["Veri "]["id"]: {"answer": "uygun değil"}}
    r = admin.put(f"/api/audits/{a['id']}", json={"answers": ans, "status": "tamamlandı"}).json()
    assert r["decision"] == "rejected" and r["risk_level"] == "high" and r["kill_switches"]
    stored = admin.get(f"/api/audits/{a['id']}").json()
    assert stored["decision"] == "rejected" and stored["completed_at"]
    assert admin.get(f"/api/suppliers/{sid}").json()["summary"]["risk"] == "yüksek"
    assert r["capa_created"] == 2  # kritik madde + düşük puan (50 < 70)
    f = admin.get(f"/api/findings?supplier_id={sid}").json()
    assert f[0]["severity"] == "kritik" and f[0]["question_id"] == q["Veri "]["id"]
    # şablonda kullanıldığı görülür
    assert next(x for x in admin.get("/api/question-sets").json() if x["id"] == t["id"])["used_in"] == 1


def test_builtin_templates_have_rules(admin):
    sets = {x["standard"]: x for x in admin.get("/api/question-sets").json()}
    assert sets["PCI DSS"]["critical_count"] >= 2 and sets["PCI DSS"]["conditional_count"] >= 1
    assert sets["Genel"]["critical_count"] == 1
