"""Parity: n8n/fana_core.js must match the Python source of truth on the same inputs."""
import json, pathlib, shutil, subprocess
import pytest
from fana.budget_parser import parse_budget
from fana.catalog import match_asset
from fana.normalize import normalize_phone, normalize_lead, merge_leads
from fana.scoring import score_lead, rank_queue
from fana.agent_state import apply_ai_extraction, next_slots, check_reply, fallback_reply, agent_context
from test_normalize import LP, META, WA
from test_scoring import hot, lead

ROOT = pathlib.Path(__file__).resolve().parents[1]
TS = "2026-09-26T09:00:00Z"

def cases():
    budgets = ["حول المليون", "ميزانيتي حول المليون ويمكن أزيد شوي لو المكان حلو", "مليونين",
               "لا، خلها مليون ونص بالكثير", "بين 800 ألف ومليون", "بين 1 و 2 مليون", "2m",
               "2,000,000 ريال", "على حسب", "مليون خلال 3 أشهر", "نص مليون", "ثلاثة ملايين",
               "فوق ٣ مليون", "800", "budget around 1.5 million", "2 ونص مليون", "في حدود مليون ونص",
               "أبغى أشتري في 2026", "", "ما أدري والله"]
    phones = ["0551234567", "+966 55 123 4567", "٠٥٥١٢٣٤٥٦٧", "12345", "", "+971501234567", "00966501112222"]
    assets = ["العمارة التجارية بالتحلية", "فيلا النخيل", "فلة في طيبة", "طيبة", "أرض المريكبات",
              "شقة", "عمارة في الجلوية", "villa in al fayhaa", "something else"]
    score_leads = [hot(), hot(financing="financing_needed", budget_min=850_000, budget_max=1_150_000,
                              budget_flexible_up=True),
                   lead(name="x"), lead(flags={"human_requested": True}),
                   lead(property_type="apartment", intent="low", flags={"price_only": True}),
                   lead(flags={"opt_out": True})]
    l = hot(); l["confidence"]["budget"] = 0.4; score_leads.append(l)
    rank_rows = []
    for name, kw in [("financing", dict(financing="financing_approved")), ("cash", {}),
                     ("human", dict(flags={"human_requested": True})), ("p3", dict(intent="low", timeline="later"))]:
        r = hot(**kw); r["name"] = name; r.update(score_lead(r)); rank_rows.append(r)
    return {"agent_base": lead(), "budgets": budgets, "phones": phones, "assets": assets,
            "leads": [["landing_page", LP, TS], ["meta_lead_ads", META, TS], ["whatsapp", WA, TS]],
            "merge": [["landing_page", LP, "2026-09-20T10:00:00Z"], ["meta_lead_ads", META, "2026-09-21T09:00:00Z"]],
            "score_leads": score_leads, "rank_rows": rank_rows, "agent": AGENT_CASES,
            "replies": [["سعر الفيلا يبدأ من 2 مليون", "كم سعر الفيلا؟"],
                        ["فهمت إن ميزانيتك ١٫٥ مليون", "ميزانيتي 1.5 مليون"], ["", "x"]]}

AGENT_CASES = [
    ["مهتم بفيلا النخيل وميزانيتي حول المليون، وبشتري كاش",
     {"extracted": {"interested_asset": {"value": "villa_nakheel_dammam", "confidence": 0.9, "evidence": "فيلا النخيل"},
                    "budget": {"raw": "حول المليون", "confidence": 0.9, "evidence": "حول المليون"},
                    "financing": {"value": "cash", "confidence": 0.9, "evidence": "كاش"}},
      "intent": {"value": "high", "confidence": 0.8}, "flags": {}}],
    ["أبغى فيلا", {"extracted": {"financing": {"value": "cash", "confidence": 0.9, "evidence": "عندي كاش"},
                                 "interested_asset": {"value": "villa", "confidence": 0.7, "evidence": "فيلا"}}}],
    ["يمكن، على حسب، الله يسهل", {"extracted": {"budget": {"raw": "على حسب", "evidence": "على حسب", "confidence": 0.3}},
                                  "intent": {"value": "medium", "confidence": 0.3}}],
    ["أبي أكلم موظف لو سمحت", {"extracted": None, "flags": {}}],
    ["كم سعر الشقة؟ بس أبغى أعرف", {"extracted": {"interested_asset": {"value": "apt_nour_dammam", "confidence": 0.8, "evidence": "الشقة"}},
                                   "intent": {"value": "low", "confidence": 0.8}, "flags": {"price_only": True}}],
    ["مرحبا", "not json"],
    ["هلا، مهتمة بفلل المزاد بس مشغولة هالأيام، كلموني الأسبوع الجاي",
     {"extracted": {"interested_asset": {"value": "villa", "confidence": 0.8, "evidence": "فلل المزاد"}},
      "intent": {"value": "medium", "confidence": 0.7}, "flags": {"contact_later": True}}],
]

def run_agent_py(c):
    out = []
    for text, ai in c["agent"]:
        l = lead()
        l, rep = apply_ai_extraction(l, ai, text, "2026-09-26T09:00:00Z")
        out.append({"lead": l, "report": rep, "next": next_slots(l), "fallback": fallback_reply(l),
                    "ctx": agent_context(l), "score": score_lead(l)})
    return out

def py_results(c):
    return {"budgets": [parse_budget(t) for t in c["budgets"]],
            "phones": [list(normalize_phone(p)) for p in c["phones"]],
            "assets": [match_asset(t) for t in c["assets"]],
            "leads": [normalize_lead(*x) for x in c["leads"]],
            "merged": merge_leads(normalize_lead(*c["merge"][0]), normalize_lead(*c["merge"][1])),
            "scores": [score_lead(l) for l in c["score_leads"]],
            "ranked": [r["name"] for r in rank_queue([dict(r) for r in c["rank_rows"]])],
            "agent": run_agent_py(c), "replies": [check_reply(a, b) for a, b in c["replies"]]}

def canon(x):
    """Normalize number types (1.0 vs 1) so JSON from both languages compares cleanly."""
    return json.loads(json.dumps(x, ensure_ascii=False), parse_float=lambda s: float(s),
                      parse_int=lambda s: float(s))

@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_js_port_matches_python():
    c = cases()
    js = json.loads(subprocess.run(["node", str(ROOT / "scripts/js_runner.js")],
                                   input=json.dumps(c, ensure_ascii=False), capture_output=True,
                                   text=True, check=True).stdout)
    py = py_results(c)
    for key in py:
        p, j = canon(py[key]), canon(js[key])
        if isinstance(p, list):
            for i, (a, b) in enumerate(zip(p, j)):
                assert a == b, f"{key}[{i}] differs:\nPY {a}\nJS {b}"
        assert p == j, f"{key} differs"
