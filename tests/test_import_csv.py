import pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from import_csv import rows_to_payloads, run_offline          # noqa: E402
from run_scenarios import Pipeline                             # noqa: E402

CSV = ROOT / "fixtures/sample_import.csv"

def by_row(results):
    return {r["csv_row"]: r for r in results}

def test_mixed_arabic_english_headers_map_to_schema():
    p = rows_to_payloads(CSV)
    assert len(p) == 6 and p[0]["campaign"] == "csv_import:sample_import.csv"
    r = by_row(run_offline(p))[2]
    assert r["priority"] == "P1" and r["mobile"] == "+966598887766"
    assert r["unmapped"] == {"Broker": "مكتب الشرقية"}           # unknown column kept, not dropped

def test_invalid_phone_and_email_handling():
    r = by_row(run_offline(rows_to_payloads(CSV)))
    assert r[3]["mobile_status"] == "invalid" and r[3]["lead_key"].endswith("ahmad.test@example.com")
    assert r[3]["priority"] != "Disqualified"                   # reachable by email
    assert r[4]["priority"] == "Disqualified" and "invalid_email:yousef@" in r[4]["issues"]
    assert r[5]["email"] is None and r[5]["mobile_status"] == "valid_sa"

def test_duplicate_inside_file_merges():
    r = by_row(run_offline(rows_to_payloads(CSV)))
    assert r[7]["lead_key"] == r[2]["lead_key"] and r[7]["contact_action"] == "update"

def test_reimport_is_idempotent():
    pl, pipe = rows_to_payloads(CSV), Pipeline()
    run_offline(pl, pipe)
    assert all(x["status"] == "duplicate_ignored" for x in run_offline(pl, pipe))


def test_handoff_ready_rule():
    from run_scenarios import handoff_ready
    assert handoff_ready({"priority": "P1", "flags": {}, "qualification_status": "qualified"})
    assert handoff_ready({"priority": "P2", "flags": {"human_requested": True}, "qualification_status": "handoff_requested"})
    assert handoff_ready({"priority": "P2", "flags": {}, "qualification_status": "qualified"})      # all facts known
    assert not handoff_ready({"priority": "P3", "flags": {}, "qualification_status": "in_qualification"})
    assert not handoff_ready({"priority": "Disqualified", "flags": {"human_requested": True}, "qualification_status": "disqualified"})
