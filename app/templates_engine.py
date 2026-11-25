"""Denetim şablonu iş kuralları — SAF fonksiyonlar (DB / ağ erişimi yok).

Bölüm yapısı:  [{id, title, weight, questions: [{id, text, weight=1, critical=False, condition=None}]}]
Cevaplar:      {question_id: "uygun" | "kısmen" | "uygun değil" | "na"}

Koşul (condition_json) biçimi
  {"q": "<soru id>", "op": "eq",  "value": "uygun değil"}          tek değer
  {"q": "<soru id>", "op": "in",  "value": ["kısmen", "uygun değil"]}
  {"q": "<soru id>", "op": "neq", "value": "uygun"}
  {"q": "<soru id>", "op": "answered"}                              herhangi bir cevap
  {"all": [koşul, ...]}  /  {"any": [koşul, ...]}  /  {"not": koşul}
Koşulu sağlanmayan soru gizlidir: puanlanmaz, kill-switch tetiklemez; gizli sorunun cevabı da
başka koşullarda "cevapsız" sayılır (zincirleme alt sorular birlikte kapanır).
"""
from __future__ import annotations

from typing import Any

ANSWER_POINTS = {"uygun": 1.0, "kısmen": 0.5, "uygun değil": 0.0}
VALID_ANSWERS = {"uygun", "kısmen", "uygun değil", "na"}
OPS = {"eq", "neq", "in", "answered"}
KILL_ANSWER = "uygun değil"

# Puan eşikleri: (alt sınır, risk seviyesi, karar, Türkçe değerlendirme)
THRESHOLDS = [
    (85, "low", "approved", "Onaylı"),
    (70, "medium", "conditional", "Şartlı Onaylı"),
    (50, "high", "improvement_required", "Gelişim Gerekli"),
    (0, "high", "rejected", "Onaysız"),
]
RISK_TR = {"low": "düşük", "medium": "orta", "high": "yüksek"}
DECISION_TR = {"approved": "Onaylı", "conditional": "Şartlı Onaylı", "improvement_required": "Gelişim Gerekli",
               "rejected": "Onaysız"}


# ------------------------------------------------------------------ koşullar
def validate_condition(cond: Any, known_ids: set[str] | None = None, path: str = "koşul") -> list[str]:
    """Hata mesajları listesi (boş = geçerli)."""
    if cond is None:
        return []
    if not isinstance(cond, dict):
        return [f"{path}: nesne olmalı"]
    for k in ("all", "any"):
        if k in cond:
            if not isinstance(cond[k], list) or not cond[k]:
                return [f"{path}.{k}: boş olmayan liste olmalı"]
            return [e for i, c in enumerate(cond[k]) for e in validate_condition(c, known_ids, f"{path}.{k}[{i}]")]
    if "not" in cond:
        return validate_condition(cond["not"], known_ids, f"{path}.not")
    errs = []
    op = cond.get("op", "eq")
    if op not in OPS:
        errs.append(f"{path}: bilinmeyen işlem '{op}'")
    if not cond.get("q"):
        errs.append(f"{path}: 'q' (soru) gerekli")
    elif known_ids is not None and cond["q"] not in known_ids:
        errs.append(f"{path}: '{cond['q']}' sorusu şablonda yok")
    if op in ("eq", "neq") and cond.get("value") not in VALID_ANSWERS:
        errs.append(f"{path}: geçersiz cevap değeri")
    if op == "in" and (not isinstance(cond.get("value"), list) or not set(cond["value"]) <= VALID_ANSWERS):
        errs.append(f"{path}: 'in' için geçerli cevap listesi gerekli")
    return errs


def evaluate_condition(cond: dict | None, answers: dict[str, str]) -> bool:
    if not cond:
        return True
    if "all" in cond:
        return all(evaluate_condition(c, answers) for c in cond["all"])
    if "any" in cond:
        return any(evaluate_condition(c, answers) for c in cond["any"])
    if "not" in cond:
        return not evaluate_condition(cond["not"], answers)
    a = answers.get(cond.get("q"))
    op = cond.get("op", "eq")
    if op == "answered":
        return a is not None
    if a is None:  # cevapsız (veya gizli) soru eşitlik koşulunu sağlamaz
        return False
    if op == "eq":
        return a == cond.get("value")
    if op == "neq":
        return a != cond.get("value")
    if op == "in":
        return a in (cond.get("value") or [])
    return False


def condition_refs(cond: dict | None) -> set[str]:
    if not cond:
        return set()
    out: set[str] = set()
    for k in ("all", "any"):
        for c in cond.get(k, []):
            out |= condition_refs(c)
    if "not" in cond:
        out |= condition_refs(cond["not"])
    if cond.get("q"):
        out.add(cond["q"])
    return out


def prefix_condition(cond: dict | None, prefix: str) -> dict | None:
    """Şablon içi soru kodlarını denetimdeki benzersiz id'lere çevirir."""
    if not cond:
        return cond
    out = dict(cond)
    for k in ("all", "any"):
        if k in out:
            out[k] = [prefix_condition(c, prefix) for c in out[k]]
    if "not" in out:
        out["not"] = prefix_condition(out["not"], prefix)
    if out.get("q"):
        out["q"] = prefix + out["q"]
    return out


def all_questions(sections: list[dict]) -> list[dict]:
    return [q for s in sections for q in s["questions"]]


def visible_question_ids(sections: list[dict], answers: dict[str, str]) -> set[str]:
    """Koşulları sağlanan (görünür) soruların id'leri. Gizli soruların cevapları yok sayılır;
    zincirleme koşullar sabit noktaya kadar yeniden değerlendirilir."""
    qs = all_questions(sections)
    visible = {q["id"] for q in qs}
    for _ in range(len(qs) + 1):
        eff = {k: v for k, v in answers.items() if k in visible and v in VALID_ANSWERS}
        nxt = {q["id"] for q in qs if evaluate_condition(q.get("condition"), eff)}
        if nxt == visible:
            break
        visible = nxt
    return visible


def find_cycles(sections: list[dict]) -> list[str]:
    """Koşul döngüsü olan soru id'leri (A, B'ye; B, A'ya bağlı)."""
    deps = {q["id"]: condition_refs(q.get("condition")) for q in all_questions(sections)}
    bad = []
    for start in deps:
        stack, seen = list(deps[start]), set()
        while stack:
            n = stack.pop()
            if n == start:
                bad.append(start)
                break
            if n not in seen:
                seen.add(n)
                stack.extend(deps.get(n, ()))
    return bad


# ------------------------------------------------------------------ puanlama
def classify(score: float | None) -> dict:
    if score is None:
        return {"risk_level": None, "decision": None, "rating": None}
    for floor, risk, decision, rating in THRESHOLDS:
        if score >= floor:
            return {"risk_level": risk, "decision": decision, "rating": rating}
    raise AssertionError("eşik bulunamadı")


def score_audit(sections: list[dict], answers: dict[str, str]) -> dict:
    """Ağırlıklı puan + kill-switch.

    * Soru puanı: uygun 1, kısmen 0.5, uygun değil 0; 'na', cevapsız ve gizli sorular hesaba katılmaz.
    * Bölüm puanı = Σ(soru puanı × soru ağırlığı) / Σ(soru ağırlığı) × 100
    * Toplam puan  = Σ(bölüm puanı × bölüm ağırlığı) / Σ(bölüm ağırlığı)   (yalnızca cevaplı bölümler)
    * Kill-switch: görünür ve kritik bir soru 'uygun değil' ise puandan bağımsız olarak
      risk_level = high, decision = rejected.
    """
    visible = visible_question_ids(sections, answers)
    out_sections, kills = [], []
    tot, tot_w = 0.0, 0.0
    answered = total_visible = 0
    for sec in sections:
        num = den = 0.0
        n_ans = n_vis = 0
        for q in sec["questions"]:
            if q["id"] not in visible:
                continue
            n_vis += 1
            a = answers.get(q["id"])
            if a in VALID_ANSWERS:
                n_ans += 1
            if q.get("critical") and a == KILL_ANSWER:
                kills.append({"question_id": q["id"], "text": q["text"], "section": sec["title"]})
            if a in ANSWER_POINTS:
                w = float(q.get("weight") or 1)
                num += ANSWER_POINTS[a] * w
                den += w
        pct = 100 * num / den if den else None
        sw = float(sec.get("weight") or 1)
        if pct is not None:
            tot += pct * sw
            tot_w += sw
        answered += n_ans
        total_visible += n_vis
        out_sections.append({"id": sec["id"], "title": sec["title"], "score": pct, "answered": n_ans,
                             "questions": n_vis, "source": sec.get("source")})
    score = round(tot / tot_w, 1) if tot_w else None
    cls = classify(score)
    if kills:
        cls = {"risk_level": "high", "decision": "rejected", "rating": "Onaysız (kritik madde)"}
    return {
        "score": score, **cls,
        "kill_switch": bool(kills), "kill_switches": kills,
        "sections": out_sections,
        "answered": answered, "visible_questions": total_visible,
        "hidden_question_ids": sorted({q["id"] for q in all_questions(sections)} - visible),
    }
