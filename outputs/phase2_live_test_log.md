# Phase 2 live test log (n8n Cloud + HubSpot)

| Test | Input | Expected | Actual | n8n exec |
|---|---|---|---|---|
| Core engine in n8n sandbox | normalize + apply_ai (invented price) + bad op | Same output as Python; price reply blocked; error item for bad op | Pass. key `9663bbf64e85c69c2607` identical to Python; reply replaced by template; `unknown_op:bogus` | 72 |
| HubSpot setup | run FANA 00 | 16 properties + 8 pipeline stages | 16 created, 0 errors; stages created | 73 |
| Scenario 1 (hot buyer, landing page) | خالد العتيبي, 0551234567, العمارة التجارية بالتحلية, 8 مليون, كاش, هذا المزاد | P1, new HubSpot contact | P1 85/100, contact 877071887585 created | 74 |
| Webhook replay | same event_id lp-s1-001 | duplicate_ignored, no CRM call | duplicate_ignored | 77 |
| Scenario 5 (duplicate via Meta) | same mobile as "+966 55 123 4567" | same contact updated, sources merged | contact 877071887585 updated, preferred contact = WhatsApp | 79 |

Bug found by the test and fixed: merged leads recorded the first event's key for later events (fixed in Collect Result).
