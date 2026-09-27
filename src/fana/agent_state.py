"""Conversation state for the Arabic qualification agent.

The LLM proposes; code decides. The LLM returns a JSON extraction per customer turn.
This module validates it (schema, allowed values, evidence quotes), merges accepted values
into the lead with change history, picks the next missing slots to ask about, and guards
the Arabic reply against invented prices or property facts.
"""
import re

from .catalog import ASSET_BY_ID, TYPE_BUDGET_BANDS, normalize_ar, catalog_for_prompt, asset_label
from .budget_parser import parse_budget, AR_DIGITS

ALLOWED = {
    "financing": {"cash", "financing_approved", "financing_in_progress", "financing_needed", "unknown"},
    "timeline": {"this_auction", "within_3_months", "later", "unknown"},
    "buyer_type": {"individual", "company", "unknown"},
    "investment_purpose": {"residence", "investment", "business", "unknown"},
    "auction_experience": {"yes", "no", "unknown"},
    "preferred_contact": {"call", "whatsapp", "email", "unknown"},
    "intent": {"high", "medium", "low", "unknown"},
}
ENUM_SLOTS = ["financing", "timeline", "buyer_type", "investment_purpose", "auction_experience", "preferred_contact"]
FLAG_KEYS = ["human_requested", "price_only", "contact_later", "opt_out", "not_interested"]
SLOT_ORDER = ["asset", "budget", "financing", "timeline", "buyer_type"]

# Deterministic safety nets (OR-ed with the LLM flags)
# Only UNAMBIGUOUS requests for a person. "كلموني / اتصلوا علي / call me" are left to the LLM because
# they often mean "contact me later" (found in live testing: "كلموني الأسبوع الجاي").
HUMAN_PATTERNS = [r"موظف", r"شخص حقيقي", r"احد يكلمني", r"اكلم احد", r"اكلم شخص",
                  r"مندوب", r"خدمه العملاء", r"agent", r"human", r"real person"]
OPTOUT_PATTERNS = [r"لا تتصلوا", r"لا تراسلوني", r"وقفوا الرسائل", r"الغاء الاشتراك", r"stop messaging", r"unsubscribe"]

QUESTIONS_AR = {
    "asset": "أي عقار من عقارات المزاد يهمك أكثر؟ (فلل، عمائر سكنية أو تجارية، أراضٍ تجارية، أو شقة في الدمام والخبر)",
    "budget": "كم الميزانية التقريبية اللي تفكر فيها؟",
    "financing": "بتكون عملية الشراء كاش ولا عن طريق تمويل بنكي؟",
    "timeline": "ناوي تدخل المزاد الحالي، ولا تفكر بالشراء لاحقاً؟",
    "buyer_type": "الشراء بيكون باسمك الشخصي ولا باسم شركة؟",
}
HANDOFF_REPLY_AR = "أبشر، سجلت طلبك وبيتواصل معك أحد مستشاري المبيعات في أقرب وقت. شكراً لتواصلك مع فنا."
PRICE_POLICY_AR = "الأسعار في المزاد تتحدد بالمزايدة، وفريق المبيعات يقدر يزودك بتفاصيل كل عقار وشروط الدخول."


def contains_evidence(evidence, customer_text):
    if not evidence:
        return False
    e = normalize_ar(evidence)
    return len(e) >= 2 and e in normalize_ar(customer_text)


def _set(lead, field, value, at, source="agent"):
    old = lead.get(field)
    if value == old:
        return False
    known_old = old not in (None, "unknown", "", False)
    if known_old:
        lead.setdefault("history", []).append({"field": field, "old": old, "new": value, "at": at, "source": source})
    lead[field] = value
    return known_old


def apply_ai_extraction(lead, ai, customer_text, at=None):
    """Validate one LLM turn and merge it into the lead. Returns (lead, report)."""
    report = {"accepted": [], "rejected": [], "changed": []}
    if not isinstance(ai, dict) or (ai.get("extracted") is not None and not isinstance(ai["extracted"], dict)):
        report["rejected"].append("malformed_output")
        return lead, report
    ex = ai.get("extracted") or {}
    conf = lead.setdefault("confidence", {})

    def accept(slot, item):
        if not isinstance(item, dict) or item.get("value") in (None, "", "unknown"):
            return None
        if not contains_evidence(item.get("evidence"), customer_text):
            report["rejected"].append(f"no_evidence:{slot}")
            return None
        c = item.get("confidence", 0)
        return float(c) if isinstance(c, (int, float)) else 0.0

    # Asset (must exist in the catalog)
    a = ex.get("interested_asset")
    c = accept("asset", a)
    if c is not None:
        val = a["value"]
        if val in ASSET_BY_ID:
            if _set(lead, "interested_asset", val, at):
                report["changed"].append("interested_asset")
            _set(lead, "property_type", ASSET_BY_ID[val]["type"], at)
            conf["asset"] = c
            report["accepted"].append("asset")
        elif val in TYPE_BUDGET_BANDS:
            if lead.get("interested_asset") and ASSET_BY_ID[lead["interested_asset"]]["type"] != val:
                _set(lead, "interested_asset", None, at)
            if _set(lead, "property_type", val, at):
                report["changed"].append("property_type")
            conf["asset"] = c
            report["accepted"].append("asset")
        else:
            report["rejected"].append(f"unknown_asset:{val}")

    # Budget: the deterministic parser has the final word on numbers
    b = ex.get("budget")
    if isinstance(b, dict) and b.get("raw"):
        if contains_evidence(b.get("raw"), customer_text) or contains_evidence(b.get("evidence"), customer_text):
            parsed = parse_budget(b["raw"])
            llm_c = b.get("confidence", 0) if isinstance(b.get("confidence"), (int, float)) else 0
            if parsed["status"] == "known":
                changed = False
                for f, v in [("budget_min", parsed["min"]), ("budget_max", parsed["max"])]:
                    changed = _set(lead, f, v, at) or changed
                lead.update(budget_status="known", budget_raw=b["raw"], budget_approx=parsed["approx"],
                            budget_flexible_up=parsed["flexible_up"])
                conf["budget"] = min(parsed["confidence"], max(llm_c, 0.5))
                report["accepted"].append("budget")
                if changed:
                    report["changed"].append("budget")
            else:
                conf["budget"] = 0.4          # customer answered, but we cannot interpret it
                report["rejected"].append("budget_uninterpretable")
        else:
            report["rejected"].append("no_evidence:budget")

    # Enum slots
    for slot in ENUM_SLOTS:
        item = ex.get(slot)
        c = accept(slot, item)
        if c is None:
            continue
        if item["value"] not in ALLOWED[slot]:
            report["rejected"].append(f"invalid_value:{slot}")
            continue
        if _set(lead, slot, item["value"], at):
            report["changed"].append(slot)
        if slot in ("financing", "timeline"):
            conf[slot] = c
        report["accepted"].append(slot)

    # Intent (classification of the whole conversation, no evidence quote required)
    it = ai.get("intent") or {}
    if isinstance(it, dict) and it.get("value") in ALLOWED["intent"] and it["value"] != "unknown":
        lead["intent"] = it["value"]
        conf["intent"] = float(it.get("confidence", 0)) if isinstance(it.get("confidence"), (int, float)) else 0.0

    # Flags: LLM OR deterministic keyword safety net
    t = normalize_ar(customer_text)
    flags = lead.setdefault("flags", {})
    ai_flags = ai.get("flags") or {}
    for k in FLAG_KEYS:
        if ai_flags.get(k) is True:
            flags[k] = True
    if any(re.search(p, t) for p in HUMAN_PATTERNS):
        flags["human_requested"] = True
    if any(re.search(p, t) for p in OPTOUT_PATTERNS):
        flags["opt_out"] = True
    if report["changed"]:
        lead["changed_fields"] = list(dict.fromkeys(lead.get("changed_fields", []) + report["changed"]))
    return lead, report


def next_slots(lead, limit=2):
    """Slots still missing or uncertain, in priority order. The agent asks only for these."""
    conf = lead.get("confidence", {})
    todo = []
    for s in SLOT_ORDER:
        missing = {
            "asset": not lead.get("interested_asset") and not lead.get("property_type"),
            "budget": lead.get("budget_status") != "known",
            "financing": lead.get("financing", "unknown") == "unknown",
            "timeline": lead.get("timeline", "unknown") == "unknown",
            "buyer_type": lead.get("buyer_type", "unknown") == "unknown",
        }[s]
        uncertain = conf.get(s, 1.0) < 0.6
        if missing or uncertain:
            todo.append(s)
    return todo[:limit]


def known_summary(lead):
    """Facts the agent may rely on (so it never re-asks them)."""
    out = {}
    if lead.get("name"):
        out["name"] = lead["name"]
    if lead.get("interested_asset"):
        out["asset"] = asset_label(lead["interested_asset"])
    elif lead.get("property_type"):
        out["property_type"] = lead["property_type"]
    if lead.get("budget_status") == "known":
        out["budget"] = {"min": lead.get("budget_min"), "max": lead.get("budget_max"), "raw": lead.get("budget_raw")}
    for s in ENUM_SLOTS:
        if lead.get(s, "unknown") != "unknown":
            out[s] = lead[s]
    return out


def agent_context(lead):
    return {"known": known_summary(lead), "ask_next": next_slots(lead),
            "catalog": catalog_for_prompt(), "flags": lead.get("flags", {})}


AMOUNT_RE = r"(\d[\d,\.]*)\s*(مليون|ملايين|الف|الاف|ريال|million|k|sar)"


def check_reply(reply, customer_text):
    """Reject replies that state amounts the customer never said (invented prices)."""
    if not reply or not isinstance(reply, str):
        return {"ok": False, "violations": ["empty_reply"]}
    violations = []
    cust = normalize_ar(str(customer_text).translate(AR_DIGITS))
    for m in re.finditer(AMOUNT_RE, normalize_ar(reply.translate(AR_DIGITS))):
        if m.group(1) not in cust:
            violations.append(f"unsupported_amount:{m.group(0)}")
    if re.search(r"(سعر الافتتاح|السعر المبدئي|يبدا من|يبدأ من|opening price)", reply):
        violations.append("price_claim")
    return {"ok": not violations, "violations": violations}


def fallback_reply(lead):
    """Safe template reply used when the LLM fails or its reply is rejected."""
    flags = lead.get("flags", {})
    if flags.get("human_requested"):
        return HANDOFF_REPLY_AR
    slots = next_slots(lead, limit=1)
    parts = []
    if flags.get("price_only"):
        parts.append(PRICE_POLICY_AR)
    parts.append(QUESTIONS_AR[slots[0]] if slots else "شكراً لك، بيتواصل معك فريق المبيعات قريباً بإذن الله.")
    return " ".join(parts)
