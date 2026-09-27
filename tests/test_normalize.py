import pytest
from fana.normalize import normalize_phone, normalize_email, normalize_lead, merge_leads

@pytest.mark.parametrize("raw", ["0551234567", "551234567", "+966551234567", "00966551234567",
                                 "966 55 123 4567", "٠٥٥١٢٣٤٥٦٧"])
def test_saudi_phone_formats(raw):
    assert normalize_phone(raw) == ("+966551234567", "valid_sa")

def test_invalid_and_missing_phone():
    assert normalize_phone("12345")[1] == "invalid"
    assert normalize_phone("")[1] == "missing"

def test_gcc_phone():
    assert normalize_phone("+971501234567") == ("+971501234567", "valid_intl")

def test_email():
    assert normalize_email(" Ahmed@Example.COM ") == ("ahmed@example.com", "valid")
    assert normalize_email("ahmed@") == (None, "invalid")

LP = {"event_id": "lp-1", "campaign": "google_search", "submitted_at": "2026-09-20T10:00:00+00:00",
      "fields": {"الاسم": "خالد العتيبي", "الجوال": "0551234567", "البريد الإلكتروني": "k@x.com",
                 "المدينة": "الخبر", "العقار المهتم به": "العمارة التجارية بالتحلية",
                 "الميزانية": "8 مليون", "طريقة الدفع": "كاش", "متى تنوي الشراء": "في هذا المزاد",
                 "نوع المشتري": "فرد"}}

def test_landing_page_arabic_fields():
    l = normalize_lead("landing_page", LP)
    assert l["name"] == "خالد العتيبي" and l["mobile"] == "+966551234567"
    assert l["city"] == "Khobar" and l["interested_asset"] == "cb_tahlia_khobar"
    assert l["budget_min"] == 8_000_000 and l["financing"] == "cash"
    assert l["timeline"] == "this_auction" and l["buyer_type"] == "individual"
    assert l["investment_purpose"] == "unknown"

META = {"id": "meta-9", "created_time": "2026-09-21T09:00:00+00:00", "campaign_name": "meta_eastern",
        "field_data": [{"name": "full_name", "values": ["Sara Al-Qahtani"]},
                       {"name": "phone_number", "values": ["+966 55 123 4567"]},
                       {"name": "budget", "values": ["around 1 million"]}]}

def test_meta_and_dedupe_merge():
    a = normalize_lead("landing_page", LP, received_at="2026-09-20T10:00:00+00:00")
    b = normalize_lead("meta_lead_ads", META, received_at="2026-09-21T09:00:00+00:00")
    assert a["lead_key"] == b["lead_key"]
    m = merge_leads(a, b)
    assert m["sources"] == ["landing_page", "meta_lead_ads"] and m["first_source"] == "landing_page"
    assert m["budget_max"] == 1_150_000 and "budget_max" in m["changed_fields"]
    assert any(h["field"] == "budget_min" and h["old"] == 8_000_000 for h in m["history"])
    assert m["financing"] == "cash"

WA = {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {
    "contacts": [{"profile": {"name": "أبو فهد"}, "wa_id": "966501112222"}],
    "messages": [{"from": "966501112222", "id": "wamid.1", "timestamp": "1790400000",
                  "type": "text", "text": {"body": "السلام عليكم، كم سعر الفيلا؟"}}]}}]}]}

def test_whatsapp():
    l = normalize_lead("whatsapp", WA)
    assert l["mobile"] == "+966501112222" and l["message_text"].startswith("السلام")
    assert l["consent"] == "granted" and l["budget_status"] == "unknown"

def test_same_event_same_key():
    assert normalize_lead("whatsapp", WA)["event_key"] == normalize_lead("whatsapp", WA)["event_key"]
