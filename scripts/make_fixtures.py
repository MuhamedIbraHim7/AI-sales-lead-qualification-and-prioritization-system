"""Builds fixtures/scenarios.json (synthetic data only). Re-run after editing."""
import json, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[1]

def wa(phone, name, text, mid, ts):
    return {"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp", "contacts": [{"profile": {"name": name}, "wa_id": phone}],
        "messages": [{"from": phone, "id": mid, "timestamp": str(ts), "type": "text", "text": {"body": text}}]}}]}]}

def lp(eid, fields):
    return {"event_id": eid, "campaign": "google_search_eastern_auction", "submitted_at": "2026-09-26T08:00:00Z", "fields": fields}

def meta(lid, fields):
    return {"id": lid, "campaign_name": "meta_eastern_province", "created_time": "2026-09-26T09:00:00Z",
            "field_data": [{"name": k, "values": [v]} for k, v in fields.items()]}

def item(value, conf, ev):
    return {"value": value, "confidence": conf, "evidence": ev}

NO_FLAGS = {"human_requested": False, "price_only": False, "contact_later": False, "opt_out": False, "not_interested": False}
T = lambda h: f"2026-09-26T{h}:00Z"

scenarios = [
 {"id": 1, "name": "Hot buyer", "input_summary": "Landing form: العمارة التجارية بالتحلية، 8 مليون، كاش، في هذا المزاد",
  "expected_summary": "P1, new contact + deal in Sales Ready (P1)",
  "steps": [{"source": "landing_page", "at": T("08:00"), "payload": lp("lp-s1", {"الاسم": "خالد العتيبي", "الجوال": "0551234567",
     "البريد الإلكتروني": "khalid.test@example.com", "المدينة": "الخبر", "العقار المهتم به": "العمارة التجارية بالتحلية",
     "الميزانية": "8 مليون", "طريقة الدفع": "كاش", "متى تنوي الشراء": "في هذا المزاد", "نوع المشتري": "فرد", "موافقة التواصل": "نعم"}),
     "expect": {"priority": "P1", "contact_action": "create", "deal_action": "create", "stage": "Sales Ready (P1)"}},
    {"source": "landing_page", "at": T("08:05"), "payload": lp("lp-s1", {"الاسم": "خالد العتيبي", "الجوال": "0551234567"}),
     "expect": {"status": "duplicate_ignored"}}]},
 {"id": 2, "name": "Financing needed", "input_summary": "Meta form: فيلا العقربية، بين 2 و 3 مليون، أحتاج تمويل بنكي، هذا المزاد",
  "expected_summary": "P2 held (financing unresolved), deal in Follow-up (P2)",
  "steps": [{"source": "meta_lead_ads", "at": T("09:00"), "payload": meta("meta-s2", {"full_name": "سارة القحطاني", "phone_number": "0562223333",
     "email": "sara.test@example.com", "which_property_are_you_interested_in?": "فيلا العقربية بالخبر", "budget": "بين 2 و 3 مليون",
     "payment_method": "أحتاج تمويل بنكي", "when_do_you_plan_to_buy?": "في هذا المزاد"}),
     "expect": {"priority": "P2", "financing": "financing_needed", "reason_contains": "held at P2: financing unresolved", "stage": "Follow-up (P2)"}}]},
 {"id": 3, "name": "Browser (prices only)", "input_summary": "WhatsApp: كم سعر الشقة؟ بس أبغى أعرف الأسعار",
  "expected_summary": "P3; LLM reply that invents a price is blocked; safe price-policy reply",
  "steps": [{"source": "whatsapp", "at": T("10:00"), "payload": wa("966577778888", "ريم", "كم سعر الشقة؟ بس أبغى أعرف الأسعار", "wamid.s3", 1790400000),
     "llm": {"reply_ar": "سعر الشقة يبدأ من 900 ألف ريال تقريباً", "extracted": {"interested_asset": item("apt_nour_dammam", 0.8, "الشقة")},
             "intent": {"value": "low", "confidence": 0.85}, "flags": {**NO_FLAGS, "price_only": True}},
     "expect": {"priority": "P3", "guard_blocked": True, "reply_source": "template", "flag": "price_only"}}]},
 {"id": 4, "name": "Missing data", "input_summary": "Landing form with only الاسم + الجوال",
  "expected_summary": "P3 in_qualification (not disqualified); missing fields listed; deal in New Lead",
  "steps": [{"source": "landing_page", "at": T("11:00"), "payload": lp("lp-s4", {"الاسم": "محمد الدوسري", "الجوال": "0544445555"}),
     "expect": {"priority": "P3", "qualification_status": "in_qualification", "stage": "New Lead",
                "missing_fields": ["asset", "budget", "financing", "timeline"]}}]},
 {"id": 5, "name": "Duplicate across channels", "input_summary": "Landing 0551112233 then WhatsApp 966551112233",
  "expected_summary": "One contact + one deal, both sources kept",
  "steps": [{"source": "landing_page", "at": T("12:00"), "payload": lp("lp-s5", {"الاسم": "فيصل الشمري", "الجوال": "055 111 2233", "العقار المهتم به": "أرض تجارية"}),
     "expect": {"contact_action": "create"}},
    {"source": "whatsapp", "at": T("12:30"), "payload": wa("966551112233", "Faisal", "مرحبا، عبيت النموذج قبل شوي عن الأرض", "wamid.s5", 1790401800),
     "llm": {"reply_ar": "حياك الله فيصل، كم الميزانية التقريبية اللي تفكر فيها؟ وبتكون كاش ولا تمويل؟",
             "extracted": {}, "intent": {"value": "medium", "confidence": 0.7}, "flags": NO_FLAGS},
     "expect": {"contact_action": "update", "deal_action": "update", "sources": ["landing_page", "whatsapp"]}}]},
 {"id": 6, "name": "Natural Arabic", "input_summary": "WhatsApp: أدور فيلا للعائلة في النخيل وميزانيتي تقريباً مليونين، ونبي ندخل هذا المزاد",
  "expected_summary": "Asset, approx budget (1.7M-2.3M), timeline and intent extracted with evidence; P2 until financing known",
  "steps": [{"source": "whatsapp", "at": T("13:00"), "payload": wa("966501112222", "أبو فهد",
      "السلام عليكم، أدور فيلا للعائلة في النخيل وميزانيتي تقريباً مليونين، ولو المكان مناسب نبي ندخل هذا المزاد", "wamid.s6", 1790402400),
     "llm": {"reply_ar": "وعليكم السلام أبو فهد، حياك الله! فيلا النخيل من ضمن عقارات المزاد. بتكون عملية الشراء كاش ولا عن طريق تمويل؟ والشراء باسمك الشخصي ولا باسم شركة؟",
             "extracted": {"interested_asset": item("villa_nakheel_dammam", 0.9, "فيلا للعائلة في النخيل"),
                           "budget": {"raw": "تقريباً مليونين", "confidence": 0.85, "evidence": "تقريباً مليونين"},
                           "timeline": item("this_auction", 0.8, "نبي ندخل هذا المزاد"),
                           "investment_purpose": item("residence", 0.8, "للعائلة")},
             "intent": {"value": "high", "confidence": 0.8}, "flags": NO_FLAGS},
     "expect": {"interested_asset": "villa_nakheel_dammam", "budget_min": 1700000, "budget_max": 2300000,
                "timeline": "this_auction", "priority": "P2", "reply_source": "llm"}}]},
 {"id": 7, "name": "Changed information", "input_summary": "WhatsApp: ميزانيتي مليونين… then: لا، خلها مليون ونص بالكثير",
  "expected_summary": "Budget updated to max 1.5M, old value kept in history, reason says revised",
  "steps": [{"source": "whatsapp", "at": T("14:00"), "payload": wa("966588889999", "نورة", "أبغى فيلا الفيحاء وميزانيتي مليونين، والدفع كاش", "wamid.s7a", 1790406000),
     "llm": {"reply_ar": "ممتاز يا نورة، ناوية تدخلين المزاد الحالي ولا تفكرين بالشراء لاحقاً؟",
             "extracted": {"interested_asset": item("villa_fayhaa_dammam", 0.9, "فيلا الفيحاء"),
                           "budget": {"raw": "مليونين", "confidence": 0.9, "evidence": "ميزانيتي مليونين"},
                           "financing": item("cash", 0.9, "كاش")},
             "intent": {"value": "high", "confidence": 0.8}, "flags": NO_FLAGS},
     "expect": {"budget_max": 2000000}},
    {"source": "whatsapp", "at": T("14:05"), "payload": wa("966588889999", "نورة", "لا، خلها مليون ونص بالكثير", "wamid.s7b", 1790406300),
     "llm": {"reply_ar": "تمام، سجلت الميزانية بحد أقصى مليون ونص. ناوية تدخلين المزاد الحالي؟",
             "extracted": {"budget": {"raw": "مليون ونص بالكثير", "confidence": 0.9, "evidence": "مليون ونص بالكثير"}},
             "intent": {"value": "high", "confidence": 0.8}, "flags": NO_FLAGS},
     "expect": {"budget_max": 1500000, "history_field": "budget_max", "reason_contains": "budget was revised", "reply_source": "llm"}}]},
 {"id": 8, "name": "Existing HubSpot contact", "input_summary": "Meta lead 0533334444; contact already in CRM as +966533334444",
  "expected_summary": "Existing contact found and updated (no duplicate), deal created and linked",
  "steps": [{"source": "meta_lead_ads", "at": T("15:00"), "payload": meta("meta-s8", {"full_name": "عبدالله الحربي", "phone_number": "0533334444",
     "which_property_are_you_interested_in?": "عمارة سكنية في الجلوية", "budget": "5 مليون", "payment_method": "تمويل معتمد من البنك"}),
     "expect": {"contact_action": "update", "existing_crm_contact": True, "deal_action": "create"}}]},
 {"id": 9, "name": "Agent uncertainty", "input_summary": "WhatsApp: والله يمكن، على حسب الوضع، الله يسهل",
  "expected_summary": "needs_review (uncertain, NOT disqualified); agent asks budget again",
  "steps": [{"source": "whatsapp", "at": T("16:00"), "payload": wa("966599990000", "سلطان", "والله يمكن، على حسب الوضع، الله يسهل", "wamid.s9", 1790409600),
     "llm": {"reply_ar": "الله يسهل أمرك يا سلطان. عشان أساعدك صح، كم الميزانية التقريبية اللي تفكر فيها؟",
             "extracted": {"budget": {"raw": "على حسب الوضع", "confidence": 0.3, "evidence": "على حسب الوضع"}},
             "intent": {"value": "medium", "confidence": 0.3}, "flags": NO_FLAGS},
     "expect": {"qualification_status": "needs_review", "not_priority": "Disqualified"}}]},
 {"id": 10, "name": "Human request", "input_summary": "WhatsApp: أبي أكلم موظف لو سمحت، ما عندي وقت للأسئلة",
  "expected_summary": "Handoff flagged even though the model missed it; P2+; handoff template reply",
  "steps": [{"source": "whatsapp", "at": T("17:00"), "payload": wa("966511223344", "تركي", "أبي أكلم موظف لو سمحت، ما عندي وقت للأسئلة", "wamid.s10", 1790413200),
     "llm": {"reply_ar": "أكيد! بس قبل، أي عقار يهمك؟", "extracted": {}, "intent": {"value": "medium", "confidence": 0.6}, "flags": NO_FLAGS},
     "expect": {"flag": "human_requested", "qualification_status": "handoff_requested", "priority": "P2", "reply_source": "template"}}]},
]
spec = {"note": "Synthetic data only. llm = recorded GPT-4o output in the live JSON shape.",
        "crm_seed": [{"phone": "+966533334444", "email": "abdullah.test@example.com", "name": "Abdullah Al-Harbi (created manually in CRM)"}],
        "scenarios": scenarios}
(ROOT / "fixtures").mkdir(exist_ok=True)
(ROOT / "fixtures/scenarios.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
print("wrote fixtures/scenarios.json")
