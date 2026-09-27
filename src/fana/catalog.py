"""Asset catalog for the Al Tilal Al Sharqiya auction.

Asset names, districts and cities come from https://fana.sa/auctions/al-tilal-al-sharqiya/
The public page shows NO prices, so TYPE_BUDGET_BANDS are SYNTHETIC ASSUMPTIONS used
only to test budget fit in this prototype. The agent must never quote them to customers.
"""
import re

AUCTION_ID = "al-tilal-al-sharqiya"
AUCTION_NAME_AR = "مزاد التلال الشرقية"

# ASSUMPTION: indicative entry bands in SAR per asset type (not real prices).
TYPE_BUDGET_BANDS = {
    "apartment":            {"min": 500_000,   "max": 1_200_000},
    "villa":                {"min": 1_500_000, "max": 3_500_000},
    "residential_building": {"min": 3_000_000, "max": 7_000_000},
    "commercial_building":  {"min": 5_000_000, "max": 15_000_000},
    "commercial_land":      {"min": 4_000_000, "max": 20_000_000},
}
LOWEST_BAND_MIN = min(b["min"] for b in TYPE_BUDGET_BANDS.values())

TYPE_LABELS = {
    "apartment": ("شقة سكنية", "Apartment"),
    "villa": ("فيلا سكنية", "Villa"),
    "residential_building": ("عمارة سكنية", "Residential building"),
    "commercial_building": ("عمارة تجارية", "Commercial building"),
    "commercial_land": ("أرض تجارية", "Commercial land"),
}

ASSETS = [
    {"id": "cb_tahlia_khobar",      "type": "commercial_building",  "district_ar": "التحلية",  "district_en": "Al Tahlia",     "city": "Khobar"},
    {"id": "cb_nuzha_dammam",       "type": "commercial_building",  "district_ar": "النزهة",   "district_en": "Al Nuzha",      "city": "Dammam"},
    {"id": "rb_taiba_dammam",       "type": "residential_building", "district_ar": "طيبة",     "district_en": "Taiba",         "city": "Dammam"},
    {"id": "rb_jalawiyah_dammam",   "type": "residential_building", "district_ar": "الجلوية",  "district_en": "Al Jalawiyah",  "city": "Dammam"},
    {"id": "villa_aqrabiyah_khobar","type": "villa",                "district_ar": "العقربية", "district_en": "Al Aqrabiyah",  "city": "Khobar"},
    {"id": "villa_taiba_dammam",    "type": "villa",                "district_ar": "طيبة",     "district_en": "Taiba",         "city": "Dammam"},
    {"id": "villa_fayhaa_dammam",   "type": "villa",                "district_ar": "الفيحاء",  "district_en": "Al Fayhaa",     "city": "Dammam"},
    {"id": "villa_nakheel_dammam",  "type": "villa",                "district_ar": "النخيل",   "district_en": "Al Nakheel",    "city": "Dammam"},
    {"id": "land_muraikabat_1",     "type": "commercial_land",      "district_ar": "المريكبات","district_en": "Al Muraikabat", "city": "Dammam"},
    {"id": "land_muraikabat_2",     "type": "commercial_land",      "district_ar": "المريكبات","district_en": "Al Muraikabat", "city": "Dammam"},
    {"id": "apt_nour_dammam",       "type": "apartment",            "district_ar": "النور",    "district_en": "Al Nour",       "city": "Dammam"},
]
ASSET_BY_ID = {a["id"]: a for a in ASSETS}

# Words that identify an asset type (Arabic normalized + English).
TYPE_KEYWORDS = [
    ("commercial_building",  [r"عماره تجاريه", r"مبني تجاري", r"commercial building"]),
    ("residential_building", [r"عماره سكنيه", r"residential building"]),
    ("commercial_land",      [r"ارض تجاريه", r"ارض", r"اراضي", r"land", r"plot"]),
    ("villa",                [r"فيلا", r"فله", r"فلة", r"villa"]),
    ("apartment",            [r"شقه", r"apartment", r"flat"]),
]
GENERIC_BUILDING = [r"عماره", r"عمائر", r"building"]


def normalize_ar(text):
    """Light Arabic normalization for matching (not for display)."""
    if not text:
        return ""
    t = str(text).lower()
    t = re.sub(r"[\u064B-\u0652\u0640]", "", t)          # tashkeel + tatweel
    t = re.sub(r"[إأآا]", "ا", t)
    t = t.replace("ة", "ه").replace("ى", "ي")
    return re.sub(r"\s+", " ", t).strip()


def asset_label(asset_id, lang="ar"):
    a = ASSET_BY_ID.get(asset_id)
    if not a:
        return None
    ar, en = TYPE_LABELS[a["type"]]
    return f"{ar} - حي {a['district_ar']} - {'الخبر' if a['city']=='Khobar' else 'الدمام'}" if lang == "ar" \
        else f"{en} - {a['district_en']} - {a['city']}"


def match_asset(text):
    """Return {"asset_id", "property_type", "confidence"} from free text.

    A specific asset needs a type + district, or a district that maps to one asset.
    Ambiguous matches (e.g. "Taiba" alone, or the two Muraikabat lands) stay at type level.
    """
    t = normalize_ar(text)
    if not t:
        return {"asset_id": None, "property_type": None, "confidence": 0.0}

    ptype = None
    for type_key, patterns in TYPE_KEYWORDS:
        if any(re.search(p, t) for p in patterns):
            ptype = type_key
            break
    if ptype is None and any(re.search(p, t) for p in GENERIC_BUILDING):
        ptype = "building_unspecified"

    districts = [a for a in ASSETS
                 if normalize_ar(a["district_ar"]).replace("ال", "", 1) in t
                 or a["district_en"].lower().replace("al ", "") in t]
    if ptype and ptype != "building_unspecified":
        districts_typed = [a for a in districts if a["type"] == ptype]
    else:
        districts_typed = districts
        if ptype == "building_unspecified":
            districts_typed = [a for a in districts if "building" in a["type"]]

    if len(districts_typed) == 1:
        a = districts_typed[0]
        return {"asset_id": a["id"], "property_type": a["type"], "confidence": 0.9}
    if len(districts_typed) > 1:
        types = {a["type"] for a in districts_typed}
        return {"asset_id": None,
                "property_type": types.pop() if len(types) == 1 else (ptype if ptype in TYPE_BUDGET_BANDS else None),
                "confidence": 0.6}
    if ptype in TYPE_BUDGET_BANDS:
        return {"asset_id": None, "property_type": ptype, "confidence": 0.8}
    return {"asset_id": None, "property_type": None, "confidence": 0.0}


def band_for(asset_id=None, property_type=None):
    if asset_id and asset_id in ASSET_BY_ID:
        return TYPE_BUDGET_BANDS[ASSET_BY_ID[asset_id]["type"]]
    if property_type in TYPE_BUDGET_BANDS:
        return TYPE_BUDGET_BANDS[property_type]
    return None


def catalog_for_prompt():
    """Compact, fact-only catalog text for the LLM (no prices)."""
    return "\n".join(f"- {a['id']}: {asset_label(a['id'])}" for a in ASSETS)
