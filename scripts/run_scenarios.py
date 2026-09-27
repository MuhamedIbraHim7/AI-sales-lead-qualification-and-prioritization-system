"""Run the 10 case-study scenarios end to end, offline and reproducibly.

Mirrors the n8n flow (FANA 01 -> 02 -> 90) step for step:
  normalize -> idempotency -> merge with stored lead -> [WhatsApp: validate LLM output] -> score
  -> CRM contact upsert (phone OR email) -> deal upsert (by lead_key) -> save -> mark event processed

Differences from live: HubSpot is an in-memory fake, and GPT-4o is replaced by RECORDED outputs
in fixtures/scenarios.json (the same JSON shape the live model returns). Everything else is the
same tested rules. Outputs: outputs/test_log.md and outputs/sales_queue.csv (UTF-8 BOM for Excel).

Usage:  python scripts/run_scenarios.py
"""
import csv
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fana.normalize import normalize_lead, merge_leads          # noqa: E402
from fana.agent_state import apply_ai_extraction, check_reply, fallback_reply  # noqa: E402
from fana.scoring import score_lead, rank_queue                   # noqa: E402
from fana.catalog import asset_label, TYPE_LABELS                  # noqa: E402
from fana.budget_parser import format_budget                       # noqa: E402

STAGES = {"new": "New Lead", "ai": "AI Qualifying", "p2": "Follow-up (P2)", "p1": "Sales Ready (P1)",
          "lost": "Closed Lost"}


class FakeCRM:
    """In-memory stand-in for HubSpot with the same matching rules as the live workflow."""

    def __init__(self):
        self.contacts, self.deals, self._n = {}, {}, 1000

    def _id(self):
        self._n += 1
        return str(self._n)

    def seed_contact(self, phone, email, name):
        cid = self._id()
        self.contacts[cid] = {"phone": phone, "email": email, "name": name, "fana_lead_key": None}
        return cid

    def upsert_contact(self, lead):
        found = next((cid for cid, c in self.contacts.items()
                      if (lead.get("mobile") and c["phone"] == lead["mobile"])
                      or (lead.get("email") and c["email"] == lead["email"])
                      or c["fana_lead_key"] == lead["lead_key"]), None)
        props = {"phone": lead.get("mobile"), "email": lead.get("email"), "name": lead.get("name"),
                 "fana_lead_key": lead["lead_key"]}
        if found:
            self.contacts[found].update({k: v for k, v in props.items() if v})
            return found, "update"
        cid = self._id()
        self.contacts[cid] = props
        return cid, "create"

    def upsert_deal(self, lead, contact_id):
        if lead["priority"] == "Disqualified":
            stage = STAGES["lost"]
        elif lead["priority"] == "P1":
            stage = STAGES["p1"]
        elif lead["priority"] == "P2":
            stage = STAGES["p2"]
        else:
            stage = STAGES["ai"] if "whatsapp" in lead["sources"] else STAGES["new"]
        found = next((did for did, d in self.deals.items() if d["fana_lead_key"] == lead["lead_key"]), None)
        props = {"fana_lead_key": lead["lead_key"], "stage": stage, "contact_id": contact_id,
                 "priority": lead["priority"], "score": lead["score"]}
        if found:
            self.deals[found].update(props)
            return found, "update", stage
        did = self._id()
        self.deals[did] = props
        return did, "create", stage


class Pipeline:
    def __init__(self):
        self.crm, self.leads, self.processed, self.transcripts = FakeCRM(), {}, set(), {}

    def process(self, source, payload, llm=None, at=None):
        incoming = normalize_lead(source, payload, received_at=at)
        if incoming["event_key"] in self.processed:
            return {"status": "duplicate_ignored", "lead_key": incoming["lead_key"]}
        existing = self.leads.get(incoming["lead_key"])
        lead = merge_leads(existing, incoming) if existing else incoming
        reply, reply_source, report, guard = None, "none", None, None
        if source == "whatsapp" and lead.get("message_text"):
            convo = self.transcripts.setdefault(lead["lead_key"], [])
            all_text = "\n".join([t for r, t in convo if r == "user"] + [lead["message_text"]])
            lead, report = apply_ai_extraction(lead, llm, lead["message_text"], at)
            lead.update(score_lead(lead))
            raw_reply = llm.get("reply_ar") if isinstance(llm, dict) else None
            guard = check_reply(raw_reply, all_text)
            use_template = lead["flags"].get("human_requested") or not guard["ok"] \
                or "malformed_output" in report["rejected"]
            reply = fallback_reply(lead) if use_template else raw_reply
            reply_source = "template" if use_template else "llm"
            convo += [("user", lead["message_text"]), ("assistant", reply)]
        lead.update(score_lead(lead))
        contact_id, contact_action = self.crm.upsert_contact(lead)
        existing_contact = contact_action == "update" and existing is None
        lead["existing_crm_contact"] = lead.get("existing_crm_contact") or existing_contact
        deal_id, deal_action, stage = self.crm.upsert_deal(lead, contact_id)
        lead.update(hubspot_contact_id=contact_id, hubspot_deal_id=deal_id, deal_stage=stage)
        self.leads[lead["lead_key"]] = lead
        self.processed.add(incoming["event_key"])
        return {"status": "processed", "lead": lead, "contact_action": contact_action,
                "existing_crm_contact": existing_contact, "deal_action": deal_action, "stage": stage,
                "reply_ar": reply, "reply_source": reply_source, "report": report, "guard": guard}


def check(result, exp):
    """Compare a result against the expectation dict; return list of failures."""
    fails = []
    lead = result.get("lead", {})
    for key, want in exp.items():
        if key == "status":
            got = result["status"]
        elif key in ("contact_action", "deal_action", "reply_source", "stage", "existing_crm_contact"):
            got = result.get(key)
        elif key == "guard_blocked":
            got = bool(result.get("guard") and not result["guard"]["ok"])
        elif key == "reason_contains":
            got = want if want in lead.get("reason_en", "") else lead.get("reason_en")
        elif key == "sources":
            got = lead.get("sources")
        elif key == "history_field":
            got = want if any(h["field"] == want for h in lead.get("history", [])) else None
        elif key == "flag":
            got = want if lead.get("flags", {}).get(want) else None
        elif key == "not_priority":
            got = want if lead.get("priority") == want else None
            if got is None:
                continue
            fails.append(f"priority must not be {want}")
            continue
        else:
            got = lead.get(key)
        if got != want:
            fails.append(f"{key}: expected {want!r}, got {got!r}")
    return fails


def next_action(lead):
    f = lead.get("flags", {})
    if lead["priority"] == "Disqualified":
        return "No action - " + (lead.get("disqualify_reason") or "disqualified")
    if f.get("human_requested"):
        return "CALL NOW - customer asked for a person"
    if lead["qualification_status"] == "needs_review":
        return "Review conversation and clarify: " + ", ".join(lead.get("uncertain_fields", []))
    if lead["priority"] == "P1":
        return "CALL NOW - guide auction registration"
    if lead["priority"] == "P2" and lead.get("financing") not in ("cash", "financing_approved"):
        return "Follow up - resolve financing"
    if lead["priority"] == "P2":
        return "Follow up - complete: " + (", ".join(lead.get("missing_fields", [])) or "confirm interest")
    if f.get("contact_later"):
        return "Nurture - contact later as requested"
    return "Nurture on WhatsApp - agent collecting: " + (", ".join(lead.get("missing_fields", [])) or "-")


def handoff_ready(lead):
    """Sales can take over: ready now (P1), asked for a person, or all core facts collected."""
    return lead["priority"] != "Disqualified" and (
        lead["priority"] == "P1" or bool(lead.get("flags", {}).get("human_requested"))
        or lead.get("qualification_status") == "qualified")


def asset_text(lead):
    if lead.get("interested_asset"):
        return asset_label(lead["interested_asset"])
    if lead.get("property_type") in TYPE_LABELS:
        return TYPE_LABELS[lead["property_type"]][0] + " (بدون تحديد الحي)"
    return "غير محدد"


def main():
    spec = json.loads((ROOT / "fixtures/scenarios.json").read_text(encoding="utf-8"))
    p = Pipeline()
    for c in spec.get("crm_seed", []):
        p.crm.seed_contact(c["phone"], c.get("email"), c["name"])
    rows, total_fail = [], 0
    for sc in spec["scenarios"]:
        last, all_fails = None, []
        for step in sc["steps"]:
            last = p.process(step["source"], step["payload"], step.get("llm"), step.get("at"))
            if "expect" in step:
                all_fails += check(last, step["expect"])
        lead = last.get("lead") or p.leads.get(last.get("lead_key"), {})
        total_fail += bool(all_fails)
        rows.append({"id": sc["id"], "name": sc["name"], "input": sc["input_summary"], "expected": sc["expected_summary"],
                     "actual": (f"{last['status']}; {lead.get('priority')} ({lead.get('score')}), "
                                f"status={lead.get('qualification_status')}, contact={last.get('contact_action', '-')}, "
                                f"deal={last.get('deal_action', '-')} [{last.get('stage', '-')}]"),
                     "reply": last.get("reply_ar") or "", "reply_source": last.get("reply_source", "none"),
                     "rejected": ", ".join((last.get("report") or {}).get("rejected", [])),
                     "reason": lead.get("reason_ar", ""), "result": "PASS" if not all_fails else "FAIL: " + "; ".join(all_fails)})

    out = ROOT / "outputs"
    out.mkdir(exist_ok=True)
    lines = ["# FANA - 10 scenario test log (offline runner)", "",
             "Generated by `python scripts/run_scenarios.py`. Same rules as the n8n Core Engine; HubSpot simulated,",
             "LLM outputs recorded in `fixtures/scenarios.json`. Live n8n/HubSpot evidence: `outputs/phase2_live_test_log.md`.", "",
             f"**Result: {len(rows) - total_fail}/{len(rows)} passed**", "",
             "| # | Scenario | Input | Expected | Actual | Result |", "|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['id']} | {r['name']} | {r['input']} | {r['expected']} | {r['actual']} | {r['result']} |")
    lines += ["", "## Agent replies and sales reasons", ""]
    for r in rows:
        lines += [f"**{r['id']}. {r['name']}**", "",
                  f"- Agent reply ({r['reply_source']}): {r['reply'] or '-'}",
                  f"- Rejected AI fields: {r['rejected'] or '-'}", f"- Sales reason: {r['reason']}", ""]
    (out / "test_log.md").write_text("\n".join(lines), encoding="utf-8")

    ranked = rank_queue([dict(l) for l in p.leads.values()])
    cols = ["rank", "priority", "score", "handoff_ready", "next_action", "name", "mobile", "email", "city", "interested_asset",
            "budget", "financial_readiness", "timeline", "qualification_status", "human_requested", "reason_ar",
            "reason_en", "sources", "last_interaction_at", "hubspot_contact_id", "hubspot_deal_id"]
    with open(out / "sales_queue.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for l in ranked:
            w.writerow({"rank": l["rank"], "priority": l["priority"], "score": l["score"],
                        "handoff_ready": "YES" if handoff_ready(l) else "no", "next_action": next_action(l),
                        "name": l.get("name") or "", "mobile": l.get("mobile") or "", "email": l.get("email") or "",
                        "city": l.get("city") or "", "interested_asset": asset_text(l),
                        "budget": format_budget({"status": l.get("budget_status"), "min": l.get("budget_min"),
                                                 "max": l.get("budget_max"), "flexible_up": l.get("budget_flexible_up")}),
                        "financial_readiness": l.get("financing"), "timeline": l.get("timeline"),
                        "qualification_status": l.get("qualification_status"),
                        "human_requested": "YES" if l["flags"].get("human_requested") else "",
                        "reason_ar": l.get("reason_ar"), "reason_en": l.get("reason_en"),
                        "sources": "+".join(l.get("sources", [])), "last_interaction_at": l.get("last_interaction_at"),
                        "hubspot_contact_id": l.get("hubspot_contact_id"), "hubspot_deal_id": l.get("hubspot_deal_id")})
    for r in rows:
        print(f"{r['id']:>2} {r['name']:<22} {r['result'][:90]}")
    print(f"\n{len(rows) - total_fail}/{len(rows)} passed -> outputs/test_log.md, outputs/sales_queue.csv")
    return 1 if total_fail else 0


if __name__ == "__main__":
    sys.exit(main())
