"""Bring leads from 3 channels into one common schema, then dedupe and merge.

Channels (simulated with test payloads):
  landing_page   - website form behind Google Search ads (Arabic or English field names)
  meta_lead_ads  - Meta Lead Ads webhook after lead retrieval ("field_data" list)
  whatsapp       - WhatsApp Cloud API message webhook (Snapchat click-to-WhatsApp ads)

Rules: unknown stays explicitly unknown ("unknown" for enums, None for values),
invalid inputs are flagged in `issues` and their raw value is kept.
Standard library only, so the same code runs in an n8n Python Code node.
"""
import json
import re
from datetime import datetime, timezone

from .catalog import AUCTION_ID, match_asset, normalize_ar
from .budget_parser import parse_budget

AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
EMAIL_RE = re.compile(r"^[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}$")

ENUM_FIELDS = ["financing", "timeline", "buyer_type", "investment_purpose",
               "auction_experience", "preferred_contact", "consent", "intent"]
VALUE_FIELDS = ["name", "mobile", "email", "city", "interested_asset", "property_type",
                "budget_min", "budget_max"]
MERGE_FIELDS = VALUE_FIELDS + ["budget_raw", "budget_status", "budget_approx",
                               "budget_flexible_up"] + ENUM_FIELDS


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- field values
def normalize_phone(raw):
    """Return (e164 or None, status) where status is valid_sa | valid_intl | invalid | missing."""
    if raw is None or str(raw).strip() == "":
        return None, "missing"
    s = str(raw).translate(AR_DIGITS).strip()
    had_plus = s.startswith("+") or s.startswith("00")
    d = re.sub(r"\D", "", s)
    if d.startswith("00"):
        d = d[2:]
    if d.startswith("966"):
        d = d[3:]
        had_plus = True
    if d.startswith("0") and len(d) == 10:
        d = d[1:]
    if len(d) == 9 and d.startswith("5"):
        return "+966" + d, "valid_sa"
    if had_plus and 8 <= len(d) <= 15 and not d.startswith("5"):
        return "+" + d, "valid_intl"       # e.g. other GCC numbers
    return None, "invalid"


def normalize_email(raw):
    if raw is None or str(raw).strip() == "":
        return None, "missing"
    e = str(raw).strip().lower()
    return (e, "valid") if EMAIL_RE.match(e) else (None, "invalid")


def _has(t, patterns):
    return any(re.search(p, t) for p in patterns)


def parse_financing(v):
    t = normalize_ar(v)
    if not t:
        return "unknown"
    if _has(t, [r"موافق", r"معتمد", r"approved", r"pre-?approv"]):
        return "financing_approved"
    if _has(t, [r"قيد", r"تحت الاجراء", r"قدمت", r"in progress", r"applied", r"pending"]):
        return "financing_in_progress"
    if _has(t, [r"تمويل", r"قرض", r"بنك", r"financ", r"mortgage", r"loan"]):
        return "financing_needed"
    if _has(t, [r"كاش", r"نقد", r"cash", r"جاهز", r"ready", r"متوفر"]):
        return "cash"
    return "unknown"


def parse_timeline(v):
    t = normalize_ar(v)
    if not t:
        return "unknown"
    if _has(t, [r"هذا المزاد", r"المزاد الحالي", r"فورا", r"الحين", r"الان", r"now",
                r"this auction", r"اشارك", r"ادخل المزاد", r"immediately"]):
        return "this_auction"
    if _has(t, [r"شهر", r"قريب", r"within", r"soon", r"month"]):
        return "within_3_months"
    if _has(t, [r"لاحقا", r"بعدين", r"السنه الجايه", r"later", r"next year", r"مو مستعجل"]):
        return "later"
    return "unknown"


def parse_buyer_type(v):
    t = normalize_ar(v)
    if _has(t, [r"شركه", r"مؤسسه", r"company", r"corporate", r"business entity"]):
        return "company"
    if _has(t, [r"فرد", r"شخصي", r"individual", r"personal"]):
        return "individual"
    return "unknown"


def parse_purpose(v):
    t = normalize_ar(v)
    if _has(t, [r"استثمار", r"تاجير", r"invest", r"rent"]):
        return "investment"
    if _has(t, [r"سكن", r"اسكن", r"residen", r"live", r"family", r"عائله"]):
        return "residence"
    if _has(t, [r"تجاري", r"نشاط", r"business", r"office", r"مكتب"]):
        return "business"
    return "unknown"


def parse_yes_no(v, yes="yes", no="no"):
    t = normalize_ar(v)
    if not t:
        return "unknown"
    if _has(t, [r"^(لا|no|false|0)\b", r"اول مره", r"first time", r"never", r"ما شاركت"]):
        return no
    if _has(t, [r"^(نعم|ايوه|ايه|اي|yes|true|1|اكيد|موافق)\b", r"شاركت", r"agree"]):
        return yes
    return "unknown"


def parse_contact_method(v):
    t = normalize_ar(v)
    if _has(t, [r"واتس", r"whats"]):
        return "whatsapp"
    if _has(t, [r"ايميل", r"بريد", r"email", r"mail"]):
        return "email"
    if _has(t, [r"اتصال", r"مكالمه", r"جوال", r"call", r"phone"]):
        return "call"
    return "unknown"


CITY_MAP = [("Dammam", [r"دمام", r"dammam"]), ("Khobar", [r"خبر", r"khobar"]),
            ("Dhahran", [r"ظهران", r"dhahran"]), ("Qatif", [r"قطيف", r"qatif"]),
            ("Jubail", [r"جبيل", r"jubail"]), ("Riyadh", [r"رياض", r"riyadh"]),
            ("Jeddah", [r"جده", r"jeddah"])]


def parse_city(v):
    t = normalize_ar(v)
    if not t:
        return None
    for city, pats in CITY_MAP:
        if _has(t, pats):
            return city
    return str(v).strip()


# ------------------------------------------------------------- source mapping
# Canonical field -> accepted source keys (normalized: lowercase, Arabic-normalized).
FIELD_ALIASES = {
    "name": ["name", "full_name", "fullname", "الاسم", "الاسم الكامل", "اسم"],
    "first_name": ["first_name", "firstname", "الاسم الاول"],
    "last_name": ["last_name", "lastname", "اسم العائله"],
    "mobile": ["mobile", "phone", "phone_number", "whatsapp", "الجوال", "رقم الجوال", "الهاتف", "جوال"],
    "email": ["email", "e-mail", "البريد", "البريد الالكتروني", "الايميل"],
    "city": ["city", "المدينه"],
    "asset_text": ["interested_asset", "property", "which_property_are_you_interested_in?",
                   "العقار", "العقار المهتم به", "العقار المطلوب"],
    "property_type_text": ["property_type", "نوع العقار"],
    "budget_text": ["budget", "budget_range", "الميزانيه", "الميزانيه المتوقعه"],
    "purpose_text": ["investment_purpose", "purpose", "الغرض", "الغرض من الشراء"],
    "buyer_type_text": ["buyer_type", "individual_or_company", "نوع المشتري", "فرد او شركه"],
    "experience_text": ["auction_experience", "have_you_joined_an_auction_before?", "خبره بالمزادات",
                        "هل شاركت في مزاد سابقا"],
    "financing_text": ["financing", "payment_method", "cash_or_financing", "طريقه الدفع", "التمويل"],
    "timeline_text": ["timeline", "purchase_timeline", "when_do_you_plan_to_buy?", "متي تنوي الشراء",
                      "موعد الشراء"],
    "contact_text": ["preferred_contact", "preferred_contact_method", "طريقه التواصل"],
    "consent_text": ["consent", "marketing_consent", "موافقه التواصل", "اوافق علي التواصل"],
    "notes": ["notes", "message", "comments", "ملاحظات", "رسالتك", "استفسار"],
}
_ALIAS_LOOKUP = {normalize_ar(alias): canon for canon, aliases in FIELD_ALIASES.items() for alias in aliases}


def _map_fields(flat):
    out, unmapped = {}, {}
    for k, v in flat.items():
        canon = _ALIAS_LOOKUP.get(normalize_ar(k))
        if canon:
            out[canon] = v
        else:
            unmapped[k] = v
    return out, unmapped


def adapt_landing_page(p):
    fields, unmapped = _map_fields(p.get("fields", {}))
    return {"source_event_id": p.get("event_id"), "campaign": p.get("campaign"),
            "created_at": p.get("submitted_at"), "fields": fields, "unmapped": unmapped}


def adapt_meta(p):
    flat = {f["name"]: (f.get("values") or [None])[0] for f in p.get("field_data", [])}
    fields, unmapped = _map_fields(flat)
    return {"source_event_id": p.get("id"), "campaign": p.get("campaign_name"),
            "created_at": p.get("created_time"), "fields": fields, "unmapped": unmapped}


def adapt_whatsapp(p):
    value = p["entry"][0]["changes"][0]["value"]
    msg = value["messages"][0]
    contact = (value.get("contacts") or [{}])[0]
    ts = msg.get("timestamp")
    created = datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if ts else None
    return {"source_event_id": msg.get("id"), "campaign": p.get("referral_campaign", "snapchat_ctwa"),
            "created_at": created,
            "fields": {"name": (contact.get("profile") or {}).get("name"),
                       "mobile": msg.get("from") or contact.get("wa_id"),
                       "notes": (msg.get("text") or {}).get("body"),
                       "contact_text": "whatsapp"},
            "unmapped": {}}


ADAPTERS = {"landing_page": adapt_landing_page, "meta_lead_ads": adapt_meta, "whatsapp": adapt_whatsapp}


# -------------------------------------------------------------- main entry
def _fnv(data, seed):
    x = seed
    for byte in data:
        x = ((x ^ byte) * 16777619) & 0xFFFFFFFF
    return f"{x:08x}"


def fnv_key(text):
    """FNV-1a with three seeds -> 20 hex chars. An idempotency key, not a security hash.
    Identical to fnvKey() in n8n/fana_core.js so Python and n8n agree on keys."""
    data = text.encode("utf-8")
    return (_fnv(data, 2166136261) + _fnv(data, 84696351) + _fnv(data, 3735928559))[:20]


def event_key(source, source_event_id, payload):
    basis = f"{source}:{source_event_id}" if source_event_id else \
        f"{source}:{json.dumps(payload, sort_keys=True, ensure_ascii=False)}"
    return fnv_key(basis)


def empty_lead():
    lead = {k: None for k in VALUE_FIELDS}
    lead.update({k: "unknown" for k in ENUM_FIELDS})
    lead.update({"budget_raw": None, "budget_status": "unknown", "budget_approx": False,
                 "budget_flexible_up": False, "mobile_status": "missing", "email_status": "missing",
                 "flags": {"human_requested": False, "price_only": False, "contact_later": False,
                           "opt_out": False, "not_interested": False},
                 "confidence": {}, "history": [], "issues": [], "sources": []})
    return lead


def normalize_lead(source, payload, received_at=None):
    """Convert one raw channel payload into the common lead schema."""
    if source not in ADAPTERS:
        raise ValueError(f"unknown source: {source}")
    a = ADAPTERS[source](payload)
    f = a["fields"]
    ts = received_at or now_iso()
    lead = empty_lead()
    lead.update({"auction_id": AUCTION_ID, "source": source, "first_source": source,
                 "sources": [source], "campaign": a["campaign"],
                 "source_event_id": a["source_event_id"],
                 "event_key": event_key(source, a["source_event_id"], payload),
                 "created_at": a["created_at"] or ts, "last_interaction_at": ts,
                 "message_text": f.get("notes"), "unmapped_fields": a["unmapped"]})

    # Identity
    name = f.get("name") or " ".join(x for x in [f.get("first_name"), f.get("last_name")] if x)
    lead["name"] = re.sub(r"\s+", " ", name).strip() if name else None
    lead["mobile"], lead["mobile_status"] = normalize_phone(f.get("mobile"))
    if lead["mobile_status"] == "invalid":
        lead["issues"].append(f"invalid_mobile:{f.get('mobile')}")
    lead["email"], lead["email_status"] = normalize_email(f.get("email"))
    if lead["email_status"] == "invalid":
        lead["issues"].append(f"invalid_email:{f.get('email')}")
    lead["city"] = parse_city(f.get("city"))

    # Buying needs (structured form answers only; free text is left to the agent)
    asset_src = " ".join(str(x) for x in [f.get("asset_text"), f.get("property_type_text")] if x)
    if asset_src:
        m = match_asset(asset_src)
        lead["interested_asset"], lead["property_type"] = m["asset_id"], m["property_type"]
        if m["property_type"]:
            lead["confidence"]["asset"] = m["confidence"]
        else:
            lead["issues"].append(f"unmatched_asset:{asset_src}")
    if f.get("budget_text"):
        b = parse_budget(f["budget_text"])
        lead.update({"budget_raw": f["budget_text"], "budget_min": b["min"], "budget_max": b["max"],
                     "budget_status": b["status"], "budget_approx": b["approx"],
                     "budget_flexible_up": b["flexible_up"]})
        lead["confidence"]["budget"] = b["confidence"]
    lead["investment_purpose"] = parse_purpose(f.get("purpose_text"))
    lead["buyer_type"] = parse_buyer_type(f.get("buyer_type_text"))
    lead["auction_experience"] = parse_yes_no(f.get("experience_text"))

    # Readiness and contact
    lead["financing"] = parse_financing(f.get("financing_text"))
    lead["timeline"] = parse_timeline(f.get("timeline_text"))
    lead["preferred_contact"] = parse_contact_method(f.get("contact_text"))
    lead["consent"] = parse_yes_no(f.get("consent_text"), yes="granted", no="denied")
    if source == "whatsapp" and lead["consent"] == "unknown":
        lead["consent"] = "granted"          # customer started the conversation themselves
        lead["confidence"]["consent"] = 0.8
    for enum in ["financing", "timeline"]:
        if lead[enum] != "unknown":
            lead["confidence"][enum] = 0.9

    lead["lead_key"] = lead_key(lead)
    return lead


def lead_key(lead):
    """Dedup key: normalized mobile first, then email, else the event itself."""
    if lead.get("mobile"):
        return f"{AUCTION_ID}:{lead['mobile']}"
    if lead.get("email"):
        return f"{AUCTION_ID}:{lead['email']}"
    return f"{AUCTION_ID}:anon:{lead.get('event_key')}"


def _is_known(field, value):
    if field in ENUM_FIELDS or field == "budget_status":
        return value not in (None, "unknown")
    return value not in (None, "", False)


def merge_leads(existing, incoming):
    """Merge a new event into an existing lead with the same lead_key.

    Newer known values win; unknown never overwrites known; every change is kept in history.
    """
    merged = json.loads(json.dumps(existing, ensure_ascii=False))
    changed = []
    for field in MERGE_FIELDS:
        new = incoming.get(field)
        old = merged.get(field)
        if not _is_known(field, new) or new == old:
            continue
        if _is_known(field, old):
            merged["history"].append({"field": field, "old": old, "new": new,
                                      "at": incoming.get("last_interaction_at"),
                                      "source": incoming.get("source")})
            changed.append(field)
        merged[field] = new
    for k, v in incoming.get("confidence", {}).items():
        merged["confidence"][k] = v
    for k, v in incoming.get("flags", {}).items():
        merged["flags"][k] = merged["flags"].get(k, False) or v
    merged["sources"] = list(dict.fromkeys(merged.get("sources", []) + incoming.get("sources", [])))
    merged["source"] = incoming.get("source")                 # latest source
    merged["created_at"] = min(merged["created_at"], incoming["created_at"])
    merged["last_interaction_at"] = max(merged["last_interaction_at"], incoming["last_interaction_at"])
    merged["issues"] = list(dict.fromkeys(merged["issues"] + incoming.get("issues", [])))
    if incoming.get("message_text"):
        merged["message_text"] = incoming["message_text"]
    if merged.get("mobile_status") != "valid_sa" and incoming.get("mobile_status", "").startswith("valid"):
        merged["mobile_status"] = incoming["mobile_status"]
    merged["changed_fields"] = changed
    return merged
