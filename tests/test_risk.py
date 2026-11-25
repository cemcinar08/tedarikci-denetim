"""Grup A: olgunluk ağırlıkları, finansal/ESG puanları, yaptırım taraması, toplu içe aktarma."""
import pytest

from app import importer, risk
from conftest import PW


# ------------------------------------------------------------------ saf fonksiyonlar
def test_maturity_score_weights_and_missing():
    w = {"audit": 50, "intel": 30, "financial": 10, "esg": 10, "reputation": 0}
    r = risk.maturity_score({"audit": 80, "intel": 60, "financial": None, "esg": 100, "reputation": 10}, w)
    # finansal yok -> ağırlığı diğerlerine dağılır; itibar ağırlığı 0 -> etkisiz
    assert r["score"] == round((80 * 50 + 60 * 30 + 100 * 10) / 90, 1)
    assert r["coverage"] == 90
    b = {x["key"]: x for x in r["breakdown"]}
    assert b["financial"]["effective_weight"] == 0 and b["reputation"]["contribution"] is None
    assert round(sum(x["effective_weight"] for x in r["breakdown"])) == 100
    assert risk.maturity_score({k: None for k in w}, w)["score"] is None
    # siber puanın ağırlığı artınca düşük siber puan olgunluğu düşürür
    lo = risk.maturity_score({"audit": 90, "intel": 40}, {**w, "intel": 70, "audit": 30})["score"]
    hi = risk.maturity_score({"audit": 90, "intel": 40}, w)["score"]
    assert lo < hi


@pytest.mark.parametrize("a,b,ok", [
    ("Anadolu Metal San. ve Tic. A.Ş.", "ANADOLU METAL", True),
    ("Bank Melli Iran", "BANK MELLI IRAN ZAO", True),
    ("Ege Lojistik Ltd. Şti.", "Ege Logistics GmbH", False),
    ("Rosneft Trading S.A.", "ROSNEFT TRADING SA", True),
    ("Marmara Ambalaj", "Marmaris Ambalaj Holding", False),
])
def test_screen_matching(a, b, ok):
    entries = [{"list": "OFAC", "name": b, "normalized": risk.normalize_name(b), "program": "X", "ref": "1"}]
    assert bool(risk.screen(a, entries)) is ok


def test_normalize_name():
    assert risk.normalize_name("Şişecam Sanayi ve Ticaret A.Ş.") == "sisecam"
    assert risk.normalize_name("İSTANBUL Gıda Ltd. Şti.") == "istanbul gida"


def test_list_parsers():
    ofac = '36,"AEROCARIBBEAN AIRLINES",-0- ,"CUBA",-0- ,-0- \r\n306,"BANCO NACIONAL DE CUBA",-0- ,"CUBA",-0-\r\n'
    o = risk.parse_ofac_csv(ofac)
    assert [x["name"] for x in o] == ["AEROCARIBBEAN AIRLINES", "BANCO NACIONAL DE CUBA"] and o[0]["program"] == "CUBA"
    un = """<CONSOLIDATED_LIST><INDIVIDUALS><INDIVIDUAL><FIRST_NAME>AHMAD</FIRST_NAME><SECOND_NAME>ZIA</SECOND_NAME>
      <REFERENCE_NUMBER>TAi.155</REFERENCE_NUMBER><UN_LIST_TYPE>Taliban</UN_LIST_TYPE></INDIVIDUAL></INDIVIDUALS>
      <ENTITIES><ENTITY><FIRST_NAME>AL-AKHTAR TRUST</FIRST_NAME><REFERENCE_NUMBER>QDe.121</REFERENCE_NUMBER>
      <ENTITY_ALIAS><ALIAS_NAME>Al-Akhtar Trust International</ALIAS_NAME></ENTITY_ALIAS></ENTITY></ENTITIES></CONSOLIDATED_LIST>"""
    u = risk.parse_un_xml(un)
    assert {x["name"] for x in u} == {"AHMAD ZIA", "AL-AKHTAR TRUST", "Al-Akhtar Trust International"}
    eu = "Entity_LogicalId;Entity_SubjectType;Entity_Regulation_Programme;NameAlias_WholeName\n13;enterprise;SYR;Syrian Arab Airlines\n13;enterprise;SYR;Syrian Arab Airlines\n"
    assert [x["name"] for x in risk.parse_eu_csv(eu)] == ["Syrian Arab Airlines"]


def test_import_parse():
    table = [["Firma Adı", "Kritiklik", "Tier", "E-posta", "Domain", "Bildirim e-postası"],
             ["Yeni A.Ş.", "Kritik", "1", "a@b.com", "https://www.yeni.com.tr/; yeni.com", "x@y.com; z@y.com"],
             ["", "orta", "", "", "", ""],
             ["Var Olan", "low", "", "", "", ""],
             ["Yeni A.Ş.", "", "tier 9", "bozuk", "", ""],
             ["Üçüncü", "belirsiz", "Tier 2", "", "geçersiz domain", ""]]
    recs = importer.parse_rows(table, {"1": 11, "2": 12}, {"var olan"})
    a, b, c, d, e = recs
    assert a["errors"] == [] and a["criticality"] == "yüksek" and a["tier_id"] == 11 and a["domains"] == ["yeni.com.tr", "yeni.com"]
    assert a["notify_emails"] == "x@y.com, z@y.com"
    assert "Firma adı boş" in b["errors"] and "Bu adla tedarikçi zaten var" in c["errors"]
    assert "Dosyada tekrar eden ad" in d["errors"] and any("e-posta" in x for x in d["errors"]) and any("Tier" in x for x in d["warnings"])
    assert e["errors"] == [] and e["tier_id"] == 12 and e["criticality"] == "orta" and len(e["warnings"]) >= 2
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        importer.parse_rows([["Kolon", "X"], ["a", "b"]], {}, set())


# ------------------------------------------------------------------ API
def test_weights_ratings_and_summary(admin, login):
    s = admin.post("/api/suppliers", json={"name": "Puanlı"}).json()
    assert admin.get(f"/api/suppliers/{s['id']}").json()["summary"]["overall_score"] is None
    r = admin.post(f"/api/suppliers/{s['id']}/risk-ratings", json={"domain": "financial", "score": 40, "source": "Findeks"})
    assert r.status_code == 201
    admin.post(f"/api/suppliers/{s['id']}/risk-ratings", json={"domain": "esg", "score": 80})
    sm = admin.get(f"/api/suppliers/{s['id']}").json()["summary"]
    assert sm["overall_score"] == 60 and sm["ratings"]["financial"]["source"] == "Findeks"
    assert admin.put("/api/score-weights", json={"audit": 0, "intel": 0, "financial": 0, "esg": 0, "reputation": 0}).status_code == 422
    admin.put("/api/score-weights", json={"audit": 50, "intel": 30, "financial": 30, "esg": 10, "reputation": 0})
    assert admin.get(f"/api/suppliers/{s['id']}").json()["summary"]["overall_score"] == 50
    admin.post("/api/users", json={"name": "d", "email": "d@x.com", "role": "auditor", "password": PW})
    assert login("d@x.com").put("/api/score-weights", json={"audit": 1, "intel": 1, "financial": 1, "esg": 1, "reputation": 1}).status_code == 403


def test_sanctions_flow(admin):
    s = admin.post("/api/suppliers", json={"name": "Banco Nacional de Cuba S.A."}).json()
    ok = admin.post("/api/suppliers", json={"name": "Temiz Gıda Ltd."}).json()
    assert admin.post(f"/api/suppliers/{s['id']}/sanctions-screen").status_code == 409  # liste yok
    from app.db import get_db
    with get_db() as db:
        risk.store_entries(db, "OFAC", risk.parse_ofac_csv('306,"BANCO NACIONAL DE CUBA",-0- ,"CUBA",-0-\n'))
    st = {x["list"]: x for x in admin.get("/api/sanctions/status").json()}
    assert st["OFAC"]["entries"] == 1 and st["OFAC"]["updated_at"]
    c = admin.post(f"/api/suppliers/{s['id']}/sanctions-screen").json()
    assert c["status"] == "olası eşleşme" and c["matches"][0]["list"] == "OFAC"
    assert admin.get(f"/api/suppliers/{s['id']}").json()["summary"]["sanctions"] == "olası eşleşme"
    assert admin.post(f"/api/suppliers/{ok['id']}/sanctions-screen").json()["status"] == "temiz"
    r = admin.post(f"/api/sanctions-checks/{c['id']}/resolve", json={"status": "doğrulandı", "comment": "Aynı tüzel kişi"}).json()
    assert r["status"] == "doğrulandı" and r["resolved_by"]
    assert admin.get(f"/api/suppliers/{s['id']}").json()["summary"]["risk"] == "yüksek"
    assert admin.post(f"/api/sanctions-checks/{c['id']}/resolve", json={"status": "yanlış pozitif"}).status_code == 409


def test_import_api(admin, login):
    csv_bytes = "Firma adı;Kritiklik;Tier;Domain\nAlfa A.Ş.;yüksek;1;alfa.com\nBeta Ltd.;düşük;3;\n;orta;;\n".encode("utf-8-sig")
    files = {"file": ("liste.csv", csv_bytes)}
    p = admin.post("/api/import/suppliers", files=files, data={"commit": "false"}).json()
    assert p["valid"] == 2 and p["invalid"] == 1 and p["created"] == 0
    assert admin.get("/api/suppliers").json() == []
    c = admin.post("/api/import/suppliers", files=files, data={"commit": "true"}).json()
    assert c["created"] == 2
    sups = {x["name"]: x for x in admin.get("/api/suppliers").json()}
    assert sups["Alfa A.Ş."]["domains"] == ["alfa.com"] and sups["Alfa A.Ş."]["tier_level"] == 1
    # ikinci kez: hepsi "zaten var"
    assert admin.post("/api/import/suppliers", files=files, data={"commit": "true"}).json()["created"] == 0
    # xlsx
    import io
    from openpyxl import Workbook
    wb = Workbook(); ws = wb.active
    ws.append(["Tedarikçi", "Ülke"]); ws.append(["Gama GmbH", "Almanya"])
    buf = io.BytesIO(); wb.save(buf)
    x = admin.post("/api/import/suppliers", files={"file": ("l.xlsx", buf.getvalue())}, data={"commit": "true"}).json()
    assert x["created"] == 1
    assert admin.post("/api/import/suppliers", files={"file": ("l.pdf", b"x")}).status_code == 415
    t = admin.get("/api/import/suppliers/template.csv")
    assert t.status_code == 200 and "Firma adı" in t.content.decode("utf-8-sig")
    admin.post("/api/users", json={"name": "v", "email": "v@x.com", "role": "viewer", "password": PW})
    assert login("v@x.com").post("/api/import/suppliers", files=files).status_code == 403
