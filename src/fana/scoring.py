"""Transparent, rules-based qualification score, priority and sales-queue ranking.

The LLM never decides the priority. It only supplies classified inputs (intent, flags,
extracted fields with confidence). Every point below is traceable to a field value.

Score (0-100)
  asset fit            20   specific asset 20 | asset type 12 | unknown 0
  budget fit           25   covers type band 25 | within 30% below band 12 | below 0 | unknown 0
  financial readiness  20   cash / approved 20 | in progress 10 | needs financing 5 | unknown 0
  timeline             15   this auction 15 | within 3 months 8 | later 3 | unknown 0
  intent (LLM class)   15   high 15 | medium 8 | low 2 | unknown 0
  contactability        5   valid mobile and consent not denied
"""
from .catalog import LOWEST_BAND_MIN, TYPE_LABELS, asset_label, band_for
from .budget_parser import budget_mid, format_budget

CONFIDENCE_THRESHOLD = 0.6
CORE_FIELDS = ["asset", "budget", "financing", "timeline"]
READY_FUNDS = ("cash", "financing_approved")
PRIORITY_ORDER = {"P1": 0, "P2": 1, "P3": 2, "Disqualified": 3}
READINESS_ORDER = {"cash": 0, "financing_approved": 1, "financing_in_progress": 2,
                   "financing_needed": 3, "unknown": 4}

FIN_AR = {"cash": "السيولة جاهزة (كاش)", "financing_approved": "تمويل معتمد",
          "financing_in_progress": "التمويل قيد الإجراء", "financing_needed": "يحتاج تمويل",
          "unknown": "الجاهزية المالية غير معروفة"}
FIN_EN = {"cash": "cash ready", "financing_approved": "financing approved",
          "financing_in_progress": "financing in progress", "financing_needed": "needs financing",
          "unknown": "funding unknown"}
TL_AR = {"this_auction": "يرغب بالمشاركة في هذا المزاد", "within_3_months": "خلال 3 أشهر",
         "later": "لاحقاً", "unknown": "التوقيت غير معروف"}
FIELD_AR = {"asset": "العقار", "budget": "الميزانية", "financing": "التمويل",
            "timeline": "التوقيت", "intent": "الجدية"}
TL_EN = {"this_auction": "wants to bid in this auction", "within_3_months": "within 3 months",
         "later": "later", "unknown": "timeline unknown"}


def _conf(lead, field):
    return lead.get("confidence", {}).get(field, 1.0)


def budget_fit(lead):
    """Return (fit, band) where fit is covers | partial | below | unknown."""
    if lead.get("budget_status") != "known":
        return "unknown", None
    band = band_for(lead.get("interested_asset"), lead.get("property_type"))
    ceiling = lead.get("budget_max") or lead.get("budget_min")
    if lead.get("budget_flexible_up") and ceiling:
        ceiling *= 1.1
    ref_min = band["min"] if band else LOWEST_BAND_MIN
    if ceiling >= ref_min:
        return "covers", band
    if ceiling >= 0.7 * ref_min:
        return "partial", band
    return "below", band


def missing_core(lead):
    missing = []
    if not lead.get("interested_asset") and not lead.get("property_type"):
        missing.append("asset")
    if lead.get("budget_status") != "known":
        missing.append("budget")
    if lead.get("financing", "unknown") == "unknown":
        missing.append("financing")
    if lead.get("timeline", "unknown") == "unknown":
        missing.append("timeline")
    return missing


def low_confidence(lead):
    return [f for f in CORE_FIELDS + ["intent"]
            if f in lead.get("confidence", {}) and lead["confidence"][f] < CONFIDENCE_THRESHOLD]


def score_lead(lead):
    """Return a dict with score, breakdown, status, priority and factual reasons."""
    flags = lead.get("flags", {})
    b = {}

    # 1. Points
    if lead.get("interested_asset"):
        b["asset_fit"] = 20
    elif lead.get("property_type"):
        b["asset_fit"] = 12
    else:
        b["asset_fit"] = 0
    fit, band = budget_fit(lead)
    b["budget_fit"] = {"covers": 25, "partial": 12}.get(fit, 0)
    b["financial_readiness"] = {"cash": 20, "financing_approved": 20, "financing_in_progress": 10,
                                "financing_needed": 5}.get(lead.get("financing"), 0)
    b["timeline"] = {"this_auction": 15, "within_3_months": 8, "later": 3}.get(lead.get("timeline"), 0)
    b["intent"] = {"high": 15, "medium": 8, "low": 2}.get(lead.get("intent"), 0)
    contactable = lead.get("mobile_status", "").startswith("valid") and lead.get("consent") != "denied"
    b["contactability"] = 5 if contactable else 0
    score = sum(b.values())

    missing = missing_core(lead)
    uncertain = low_confidence(lead)

    # 2. Disqualification (only on confident facts)
    dq = None
    if flags.get("opt_out") or lead.get("consent") == "denied":
        dq = ("Customer asked not to be contacted", "طلب العميل عدم التواصل")
    elif flags.get("not_interested"):
        dq = ("Customer stated no interest", "أفاد العميل بعدم اهتمامه")
    elif not lead.get("mobile_status", "").startswith("valid") and not lead.get("email"):
        dq = ("No valid mobile or email to contact", "لا يوجد رقم جوال أو بريد صالح للتواصل")
    elif fit == "below" and _conf(lead, "budget") >= 0.7 and \
            (lead.get("budget_max") or lead.get("budget_min")) < 0.5 * LOWEST_BAND_MIN:
        dq = ("Confirmed budget far below every asset in this auction",
              "الميزانية المؤكدة أقل بكثير من جميع أصول المزاد")

    # 3. Priority gates (score alone is not enough)
    notes = []
    if dq:
        priority, status = "Disqualified", "disqualified"
    else:
        p1_gate = (score >= 70 and lead.get("financing") in READY_FUNDS
                   and lead.get("timeline") == "this_auction"
                   and (lead.get("interested_asset") or lead.get("property_type"))
                   and fit in ("covers", "partial") and not uncertain)
        if p1_gate:
            priority = "P1"
        elif score >= 45:
            priority = "P2"
            if score >= 70 and lead.get("financing") not in READY_FUNDS:
                notes.append(("held at P2: financing unresolved", "بقي P2: التمويل غير محسوم"))
        else:
            priority = "P3"
        if flags.get("price_only") and lead.get("intent") != "high" and priority != "P3":
            priority = "P3"
            notes.append(("capped at P3: price questions only", "P3: يسأل عن الأسعار فقط"))
        if flags.get("contact_later") and priority != "P3":
            priority = "P3"
        if flags.get("human_requested") and priority == "P3":
            priority = "P2"
            notes.append(("raised to P2: asked for a person", "رُفع إلى P2: طلب موظف"))

        if flags.get("human_requested"):
            status = "handoff_requested"
        elif uncertain:
            status = "needs_review"
        elif missing:
            status = "in_qualification"
        else:
            status = "qualified"

    result = {"score": score, "breakdown": b, "budget_fit": fit, "priority": priority,
              "qualification_status": status, "missing_fields": missing,
              "uncertain_fields": uncertain, "disqualify_reason": dq[0] if dq else None}
    result["reason_en"], result["reason_ar"] = build_reason(lead, result, band, dq, notes)
    return result


def _asset_text(lead, lang):
    if lead.get("interested_asset"):
        return asset_label(lead["interested_asset"], lang)
    if lead.get("property_type") in TYPE_LABELS:
        ar, en = TYPE_LABELS[lead["property_type"]]
        return (ar + " (بدون تحديد الحي)") if lang == "ar" else (en + " (district not chosen)")
    return "العقار غير محدد" if lang == "ar" else "asset unknown"


def build_reason(lead, r, band, dq, notes):
    """Short, factual reason built only from lead fields - no LLM, no invented facts."""
    p = r["priority"]
    if dq:
        return f"{p}: {dq[0]}.", f"{p}: {dq[1]}."
    budget_en = format_budget({"status": lead.get("budget_status"), "min": lead.get("budget_min"),
                               "max": lead.get("budget_max"),
                               "flexible_up": lead.get("budget_flexible_up")})
    fit_en = {"covers": "fits", "partial": "slightly below band", "below": "below band",
              "unknown": "not given"}[r["budget_fit"]]
    fit_ar = {"covers": "مناسبة", "partial": "أقل قليلاً من المتوقع", "below": "أقل من المتوقع",
              "unknown": "غير معروفة"}[r["budget_fit"]]
    en = [f"{p} ({r['score']}/100)", _asset_text(lead, "en"),
          f"budget {budget_en} ({fit_en})" if lead.get("budget_status") == "known" else "budget unknown",
          FIN_EN[lead.get("financing", "unknown")], TL_EN[lead.get("timeline", "unknown")]]
    budget_ar_val = budget_en.replace("up to", "حتى").replace("from", "من").replace("SAR", "ريال") \
        .replace(" (flexible up)", " (قابلة للزيادة)")
    ar = [f"{p} ({r['score']}/100)", _asset_text(lead, "ar"),
          f"الميزانية {budget_ar_val} ({fit_ar})" if lead.get("budget_status") == "known" else "الميزانية غير معروفة",
          FIN_AR[lead.get("financing", "unknown")], TL_AR[lead.get("timeline", "unknown")]]
    if lead.get("flags", {}).get("human_requested"):
        en.insert(1, "ASKED FOR A PERSON - call now")
        ar.insert(1, "طلب التحدث مع موظف - اتصل الآن")
    if lead.get("flags", {}).get("contact_later"):
        en.append("asked to be contacted later")
        ar.append("طلب التواصل لاحقاً")
    if r["uncertain_fields"]:
        en.append("needs review: " + ", ".join(r["uncertain_fields"]))
        ar.append("يحتاج مراجعة: " + "، ".join(FIELD_AR[f] for f in r["uncertain_fields"]))
    elif r["missing_fields"]:
        en.append("missing: " + ", ".join(r["missing_fields"]))
        ar.append("ناقص: " + "، ".join(FIELD_AR[f] for f in r["missing_fields"]))
    changes = [h for h in lead.get("history", []) if h["field"] in ("budget_min", "budget_max")]
    if changes:
        en.append("budget was revised")
        ar.append("تم تعديل الميزانية")
    en += [n[0] for n in notes]
    ar += [n[1] for n in notes]
    return " | ".join(en), " | ".join(ar)


# ------------------------------------------------------------------ queue ranking
def queue_sort_key(row):
    """Priority bucket > human request > funds readiness > score > budget > recency > age."""
    last = row.get("last_interaction_at") or ""
    return (PRIORITY_ORDER.get(row.get("priority"), 9),
            0 if row.get("flags", {}).get("human_requested") else 1,
            READINESS_ORDER.get(row.get("financing", "unknown"), 4),
            -(row.get("score") or 0),
            -(budget_mid({"min": row.get("budget_min"), "max": row.get("budget_max")}) or 0),
            "".join(chr(0x10FFFF - ord(c)) for c in last),   # newer interaction first
            row.get("created_at") or "")


def rank_queue(rows):
    ranked = sorted(rows, key=queue_sort_key)
    for i, r in enumerate(ranked, 1):
        r["rank"] = i
    return ranked
