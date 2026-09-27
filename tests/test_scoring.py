from fana.normalize import empty_lead
from fana.scoring import score_lead, rank_queue

def lead(**kw):
    l = empty_lead()
    l.update({"mobile": "+966551234567", "mobile_status": "valid_sa", "consent": "granted",
              "created_at": "2026-09-20T10:00:00", "last_interaction_at": "2026-09-20T10:00:00"})
    flags = kw.pop("flags", {})
    l.update(kw); l["flags"].update(flags)
    return l

def hot(**kw):
    base = dict(interested_asset="villa_nakheel_dammam", property_type="villa", budget_status="known",
                budget_min=1_800_000, budget_max=2_200_000, financing="cash",
                timeline="this_auction", intent="high")
    base.update(kw)
    return lead(**base)

def test_hot_buyer_is_p1():
    r = score_lead(hot())
    assert r["score"] == 100 and r["priority"] == "P1" and r["qualification_status"] == "qualified"

def test_financing_needed_held_at_p2():
    r = score_lead(hot(financing="financing_needed"))
    assert r["priority"] == "P2" and "financing" in r["reason_en"]

def test_browser_capped_p3():
    r = score_lead(lead(property_type="apartment", intent="low", flags={"price_only": True}))
    assert r["priority"] == "P3"

def test_missing_data_not_disqualified():
    r = score_lead(lead(name="محمد"))
    assert r["priority"] == "P3" and r["qualification_status"] == "in_qualification"
    assert set(r["missing_fields"]) == {"asset", "budget", "financing", "timeline"}

def test_uncertain_is_review_not_disqualified():
    l = hot(); l["confidence"]["budget"] = 0.4
    r = score_lead(l)
    assert r["qualification_status"] == "needs_review" and r["priority"] == "P2"

def test_human_request_raised_and_flagged():
    r = score_lead(lead(flags={"human_requested": True}))
    assert r["priority"] == "P2" and r["qualification_status"] == "handoff_requested"

def test_disqualify_opt_out_and_low_budget():
    assert score_lead(lead(flags={"opt_out": True}))["priority"] == "Disqualified"
    l = lead(budget_status="known", budget_min=100_000, budget_max=100_000)
    l["confidence"]["budget"] = 0.9
    assert score_lead(l)["priority"] == "Disqualified"

def test_queue_tie_breaks():
    rows = []
    for name, kw in [("financing", dict(financing="financing_approved")), ("cash", {}),
                     ("human", dict(flags={"human_requested": True}))]:
        l = hot(**kw); l["name"] = name
        l.update(score_lead(l)); rows.append(l)
    assert [r["name"] for r in rank_queue(rows)] == ["human", "cash", "financing"]
