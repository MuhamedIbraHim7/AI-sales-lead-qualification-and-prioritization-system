"""Import leads from a CSV file (e.g. an event list, a broker sheet, an old CRM export).

Each row becomes a landing-form payload, so it goes through exactly the same normalization,
dedupe, scoring and CRM sync as web leads. Column headers may be Arabic or English and in any
order - they are resolved by the same alias map (FIELD_ALIASES in src/fana/normalize.py).
Unknown columns are kept in `unmapped_fields`, never dropped silently.

Traceability: campaign = "csv_import:<file name>"; event_id is a stable hash of file name +
row content, so re-importing the same file is ignored as a duplicate (idempotent).

Usage
  python scripts/import_csv.py fixtures/sample_import.csv            # offline: tested pipeline, prints results
  python scripts/import_csv.py fixtures/sample_import.csv --print    # print the webhook payloads (JSON lines)
  python scripts/import_csv.py fixtures/sample_import.csv --live https://<n8n>/webhook/fana/landing
"""
import argparse
import csv
import json
import pathlib
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from fana.normalize import fnv_key  # noqa: E402


def rows_to_payloads(path, submitted_at="2026-09-27T09:00:00Z"):
    path = pathlib.Path(path)
    with open(path, newline="", encoding="utf-8-sig") as fh:   # utf-8-sig: Excel-saved CSVs with BOM
        reader = csv.DictReader(fh)
        payloads = []
        for i, row in enumerate(reader, start=2):              # row 1 is the header
            fields = {k.strip(): (v.strip() if isinstance(v, str) else v)
                      for k, v in row.items() if k and v not in (None, "")}
            if not fields:
                continue                                        # skip blank lines
            content = json.dumps(fields, sort_keys=True, ensure_ascii=False)
            payloads.append({"event_id": "csv-" + fnv_key(f"{path.name}:{content}"),
                             "campaign": f"csv_import:{path.name}",
                             "submitted_at": submitted_at, "csv_row": i, "fields": fields})
    return payloads


def run_offline(payloads, pipeline=None):
    from run_scenarios import Pipeline                        # same flow as n8n, simulated CRM
    p = pipeline or Pipeline()
    results = []
    for pl in payloads:
        r = p.process("landing_page", pl, at=pl["submitted_at"])
        lead = r.get("lead") or {}
        results.append({"csv_row": pl["csv_row"], "status": r["status"], "lead_key": lead.get("lead_key") or r.get("lead_key"),
                        "mobile": lead.get("mobile"), "mobile_status": lead.get("mobile_status"),
                        "email": lead.get("email"), "email_status": lead.get("email_status"),
                        "issues": lead.get("issues", []), "priority": lead.get("priority"),
                        "score": lead.get("score"), "reason_en": lead.get("reason_en"),
                        "contact_action": r.get("contact_action"), "unmapped": lead.get("unmapped_fields", {})})
    return results


def post_live(payloads, url):
    out = []
    for pl in payloads:
        req = urllib.request.Request(url, data=json.dumps(pl, ensure_ascii=False).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                out.append({"csv_row": pl["csv_row"], "http": resp.status, "body": json.loads(resp.read() or b"{}")})
        except Exception as e:                                  # one bad row must not stop the import
            out.append({"csv_row": pl["csv_row"], "error": str(e)})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_path")
    ap.add_argument("--print", action="store_true", help="print webhook payloads as JSON lines")
    ap.add_argument("--live", metavar="WEBHOOK_URL", help="POST each row to the n8n landing webhook")
    a = ap.parse_args()
    payloads = rows_to_payloads(a.csv_path)
    if a.print:
        for pl in payloads:
            print(json.dumps(pl, ensure_ascii=False))
        return 0
    results = post_live(payloads, a.live) if a.live else run_offline(payloads)
    for r in results:
        print(json.dumps(r, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
