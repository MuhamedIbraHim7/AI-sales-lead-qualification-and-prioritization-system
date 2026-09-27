"""Deterministic budget parser for informal Arabic / English budget phrases.

Used to (1) parse form fields and (2) double-check the LLM's budget extraction.
Returns SAR amounts. Never guesses: vague answers return status="vague".

Examples
  "حول المليون"            -> 850,000 - 1,150,000 (approx, +-15%)
  "مليون ونص بالكثير"      -> max 1,500,000
  "بين 800 ألف ومليون"     -> 800,000 - 1,000,000
  "2m" / "2,000,000"       -> 2,000,000
  "على حسب" / "ما أدري"    -> vague
"""
import re

APPROX_PCT = 0.15
AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫٬", "01234567890123456789.,")

APPROX_CUES = r"(حول|تقريبا|تقريبًا|تقريباً|حدود|بحدود|قرابه|قرابة|يعني|around|about|approx|~)"
MAX_CUES = r"(بالكثير|كحد اقصى|كحد أقصى|حد اقصى|ما يتعدى|ما يتجاوز|لا يزيد|ما يزيد|اقصى|أقصى|max|up to|at most)"
MIN_CUES = r"(فوق|اكثر من|أكثر من|على الاقل|على الأقل|at least|\+)"
FLEX_UP_CUES = r"(ازيد|أزيد|نزيد|يزيد شوي|ممكن ازيد|قابل للزياده|قابل للزيادة|flexible)"
VAGUE_CUES = r"(على حسب|ما ادري|ما أدري|مدري|ماادري|الله يسهل|نشوف|مو متأكد|مو متاكد|not sure|depends)"

NUM_WORDS = {"واحد": 1, "اثنين": 2, "اثنان": 2, "ثلاث": 3, "ثلاثه": 3, "ثلاثة": 3, "اربع": 4, "أربع": 4,
             "اربعه": 4, "أربعة": 4, "خمس": 5, "خمسه": 5, "خمسة": 5, "ست": 6, "سته": 6, "ستة": 6,
             "سبع": 7, "سبعه": 7, "سبعة": 7, "ثمان": 8, "ثمانيه": 8, "ثمانية": 8, "تسع": 9, "تسعه": 9,
             "تسعة": 9, "عشر": 10, "عشره": 10, "عشرة": 10}

MILLION = r"(?:مليون|ملايين|مليونين|million|mil|m)\b"
THOUSAND = r"(?:الف|ألف|آلاف|الاف|k|thousand)\b"


def _prep(text):
    t = str(text).translate(AR_DIGITS).lower()
    t = re.sub(r"[\u064B-\u0652\u0640]", "", t)
    # Compound spoken forms -> numeric tokens (order matters: longest first)
    subs = [
        (r"مليونين\s*و\s*(نص|نصف)", "2.5 مليون"),
        (r"مليون\s*و\s*(نص|نصف)", "1.5 مليون"),
        (r"مليون\s*و\s*ربع", "1.25 مليون"),
        (r"(\d+(?:\.\d+)?)\s*و\s*(نص|نصف)\s*مليون", lambda m: f"{float(m.group(1)) + 0.5} مليون"),
        (r"(نص|نصف)\s*(ال)?مليون", "0.5 مليون"),
        (r"ربع\s*(ال)?مليون", "0.25 مليون"),
        (r"مليونين", "2 مليون"),
        (r"الفين|ألفين", "2 ألف"),
    ]
    for pat, rep in subs:
        t = re.sub(pat, rep, t)
    for word, n in NUM_WORDS.items():
        t = re.sub(rf"\b{word}\s*(ملايين|مليون)", f"{n} مليون", t)
    # Split the conjunction "و" glued to a number/million ("ومليون" -> "و مليون")
    t = re.sub(r"(^|\s)و(?=\d|(ال)?مليون)", r"\1و ", t)
    # A bare "مليون"/"المليون" with no number before it means one million
    out, last = [], 0
    for m in re.finditer(r"(ال)?مليون", t):
        before = t[:m.start()].rstrip()
        out.append(t[last:m.start()])
        out.append(m.group(0) if before[-1:].isdigit() else "1 مليون")
        last = m.end()
    out.append(t[last:])
    return "".join(out)


def _to_number(s):
    s = s.strip()
    if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", s):
        s = s.replace(",", "")
    else:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def extract_amounts(text):
    """Return a list of (value_sar, unit_was_explicit)."""
    t = _prep(text)
    pattern = rf"(\d+(?:[.,]\d+)*)\s*({MILLION}|{THOUSAND})?"
    raw = []
    for m in re.finditer(pattern, t):
        n = _to_number(m.group(1))
        if n is not None:
            raw.append({"n": n, "unit": m.group(2) or "", "start": m.start(), "end": m.end()})
    # Borrow the unit of the NEXT amount only when they form a range: "بين 1 و 2 مليون"
    for i, a in enumerate(raw[:-1]):
        nxt = raw[i + 1]
        joiner = t[a["end"]:nxt["start"]]
        if not a["unit"] and nxt["unit"] and a["n"] < 100 and \
                re.fullmatch(r"\s*(و|الى|إلى|لين|-|to)\s*", joiner):
            a["unit"], a["borrowed"] = nxt["unit"], True
    out = []
    for a in raw:
        n, unit = a["n"], a["unit"]
        explicit = (bool(unit) and not a.get("borrowed")) or (not unit and n >= 10_000)
        if re.match(MILLION, unit):
            v = n * 1_000_000
        elif re.match(THOUSAND, unit):
            v = n * 1_000
        elif 1900 <= n <= 2100 and float(n).is_integer():
            continue               # looks like a year
        elif n >= 10_000:
            v = n
        elif 100 <= n < 10_000:
            v = n * 1_000          # "800" in a property budget context means 800k
        else:
            continue               # small unitless numbers ("3 أشهر") are not budgets
        out.append((v, explicit))
    return out


def parse_budget(text):
    """Parse a budget phrase. Returns dict with min, max, approx, status, confidence, raw."""
    result = {"raw": text, "min": None, "max": None, "approx": False,
              "flexible_up": False, "status": "unknown", "confidence": 0.0}
    if text is None or str(text).strip() == "":
        return result
    t = _prep(text)
    amounts = extract_amounts(text)

    if not amounts:
        if re.search(VAGUE_CUES, t):
            result["status"] = "vague"
        return result

    conf = 0.9 if all(e for _, e in amounts) else 0.7
    values = [v for v, _ in amounts]

    if len(values) >= 2:
        lo, hi = min(values[:2]), max(values[:2])
        result.update(min=lo, max=hi)
    else:
        v = values[0]
        if re.search(MAX_CUES, t):
            result.update(min=None, max=v)
        elif re.search(MIN_CUES, t):
            result.update(min=v, max=None)
        elif re.search(APPROX_CUES, t):
            result.update(min=round(v * (1 - APPROX_PCT)), max=round(v * (1 + APPROX_PCT)), approx=True)
        else:
            result.update(min=v, max=v)

    if re.search(FLEX_UP_CUES, t):
        result["flexible_up"] = True
    if re.search(VAGUE_CUES, t):
        conf = min(conf, 0.5)
    ref = result["max"] or result["min"]
    if ref < 50_000 or ref > 500_000_000:
        conf = 0.3                  # implausible for this auction -> ask again
    result["status"] = "known"
    result["confidence"] = conf
    return result


def budget_mid(b):
    if not b:
        return None
    lo, hi = b.get("min"), b.get("max")
    if lo and hi:
        return (lo + hi) / 2
    return lo or hi


def format_sar(v):
    if v is None:
        return "?"
    return f"{round(v/1_000_000, 2):g}M" if v >= 1_000_000 else f"{v/1000:.0f}K"


def format_budget(b):
    if not b or b.get("status") != "known":
        return "unknown"
    lo, hi = b.get("min"), b.get("max")
    if lo and hi and lo != hi:
        s = f"{format_sar(lo)}-{format_sar(hi)} SAR"
    elif lo and hi:
        s = f"{format_sar(lo)} SAR"
    elif hi:
        s = f"up to {format_sar(hi)} SAR"
    else:
        s = f"from {format_sar(lo)} SAR"
    return s + (" (flexible up)" if b.get("flexible_up") else "")
