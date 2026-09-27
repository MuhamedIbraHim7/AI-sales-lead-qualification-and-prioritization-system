import pytest
from fana.budget_parser import parse_budget

@pytest.mark.parametrize("text,lo,hi,approx", [
    ("حول المليون", 850_000, 1_150_000, True),
    ("في حدود مليون ونص", 1_275_000, 1_725_000, True),
    ("مليونين", 2_000_000, 2_000_000, False),
    ("بين 800 ألف ومليون", 800_000, 1_000_000, False),
    ("بين 1 و 2 مليون", 1_000_000, 2_000_000, False),
    ("ثلاثة ملايين", 3_000_000, 3_000_000, False),
    ("نص مليون", 500_000, 500_000, False),
    ("٢٫٥ مليون", 2_500_000, 2_500_000, False),
    ("2,000,000 SAR", 2_000_000, 2_000_000, False),
    ("budget around 1.5 million", 1_275_000, 1_725_000, True),
])
def test_ranges(text, lo, hi, approx):
    b = parse_budget(text)
    assert b["status"] == "known" and b["min"] == lo and b["max"] == hi and b["approx"] == approx

def test_max_only():
    b = parse_budget("لا، خلها مليون ونص بالكثير")
    assert b["min"] is None and b["max"] == 1_500_000

def test_min_only():
    b = parse_budget("فوق ٣ مليون")
    assert b["min"] == 3_000_000 and b["max"] is None

def test_flexible_up():
    assert parse_budget("حول المليون ويمكن أزيد شوي")["flexible_up"] is True

@pytest.mark.parametrize("text", ["على حسب", "الله يسهل", "ما أدري والله"])
def test_vague_is_not_guessed(text):
    b = parse_budget(text)
    assert b["status"] == "vague" and b["min"] is None and b["max"] is None

def test_ignores_months_and_years():
    assert parse_budget("مليون خلال 3 أشهر")["max"] == 1_000_000
    assert parse_budget("أبغى أشتري في 2026")["status"] == "unknown"

def test_empty():
    assert parse_budget(None)["status"] == "unknown"
