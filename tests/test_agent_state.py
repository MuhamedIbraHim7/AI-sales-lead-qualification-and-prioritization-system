from fana.normalize import empty_lead
from fana.agent_state import apply_ai_extraction, next_slots, check_reply, fallback_reply, agent_context
from fana.scoring import score_lead

def base():
    l = empty_lead(); l.update(mobile="+966551234567", mobile_status="valid_sa", consent="granted")
    return l

def test_accepts_evidenced_values_and_parses_budget():
    msg = "مهتم بفيلا النخيل وميزانيتي حول المليون، وبشتري كاش"
    ai = {"extracted": {
        "interested_asset": {"value": "villa_nakheel_dammam", "confidence": 0.9, "evidence": "فيلا النخيل"},
        "budget": {"raw": "حول المليون", "confidence": 0.9, "evidence": "حول المليون"},
        "financing": {"value": "cash", "confidence": 0.9, "evidence": "كاش"}},
        "intent": {"value": "high", "confidence": 0.8}, "flags": {}}
    l, r = apply_ai_extraction(base(), ai, msg)
    assert l["interested_asset"] == "villa_nakheel_dammam" and l["budget_min"] == 850_000
    assert l["financing"] == "cash" and l["intent"] == "high" and not r["rejected"]
    assert next_slots(l) == ["timeline", "buyer_type"]

def test_rejects_invented_values():
    ai = {"extracted": {"financing": {"value": "cash", "confidence": 0.9, "evidence": "عندي كاش"},
                        "interested_asset": {"value": "villa_riyadh", "confidence": 0.9, "evidence": "فيلا"}}}
    l, r = apply_ai_extraction(base(), ai, "أبغى فيلا")
    assert l["financing"] == "unknown" and "no_evidence:financing" in r["rejected"]
    assert "unknown_asset:villa_riyadh" in r["rejected"]

def test_changed_budget_keeps_history():
    l = base()
    l, _ = apply_ai_extraction(l, {"extracted": {"budget": {"raw": "مليونين", "evidence": "مليونين", "confidence": 0.9}}}, "ميزانيتي مليونين")
    l, r = apply_ai_extraction(l, {"extracted": {"budget": {"raw": "مليون ونص بالكثير", "evidence": "مليون ونص بالكثير", "confidence": 0.9}}}, "لا، خلها مليون ونص بالكثير")
    assert l["budget_max"] == 1_500_000 and "budget" in r["changed"]
    assert any(h["field"] == "budget_max" and h["old"] == 2_000_000 for h in l["history"])
    assert "budget was revised" in score_lead(l)["reason_en"]

def test_uninterpretable_budget_goes_to_review():
    l, r = apply_ai_extraction(base(), {"extracted": {"budget": {"raw": "على حسب", "evidence": "على حسب", "confidence": 0.3}},
                                        "intent": {"value": "medium", "confidence": 0.3}}, "يمكن، على حسب، الله يسهل")
    s = score_lead(l)
    assert s["qualification_status"] == "needs_review" and s["priority"] != "Disqualified"
    assert "budget" in next_slots(l)

def test_human_request_safety_net_without_llm_flag():
    l, _ = apply_ai_extraction(base(), {"extracted": {}, "flags": {}}, "أبي أكلم موظف لو سمحت")
    assert l["flags"]["human_requested"] is True
    assert fallback_reply(l).startswith("أبشر")

def test_malformed_output():
    l, r = apply_ai_extraction(base(), "not json", "مرحبا")
    assert r["rejected"] == ["malformed_output"]

def test_reply_guard():
    assert check_reply("سعر الفيلا يبدأ من 2 مليون", "كم سعر الفيلا؟")["ok"] is False
    assert check_reply("ممتاز، ميزانية مليون ونص مناسبة؟ هل الشراء كاش؟", "ميزانيتي 1.5 مليون")["ok"] is True
    assert check_reply("فهمت إن ميزانيتك 1.5 مليون", "ميزانيتي 1.5 مليون")["ok"] is True

def test_context_does_not_reask_known():
    l = base(); l.update(interested_asset="apt_nour_dammam", property_type="apartment")
    ctx = agent_context(l)
    assert "asset" not in ctx["ask_next"] and "asset" in ctx["known"]


def test_contact_later_is_not_a_human_request():
    """Regression from live test: 'كلموني الأسبوع الجاي' means later, not 'call now'."""
    msg = "هلا، مهتمة بفلل المزاد بس مشغولة هالأيام، كلموني الأسبوع الجاي لو سمحتوا"
    ai = {"extracted": {"interested_asset": {"value": "villa", "confidence": 0.8, "evidence": "فلل المزاد"}},
          "intent": {"value": "medium", "confidence": 0.7}, "flags": {"contact_later": True}}
    l, _ = apply_ai_extraction(base(), ai, msg)
    s = score_lead(l)
    assert l["flags"].get("human_requested") is not True
    assert s["priority"] == "P3" and s["qualification_status"] != "handoff_requested"
    assert "contacted later" in s["reason_en"]
