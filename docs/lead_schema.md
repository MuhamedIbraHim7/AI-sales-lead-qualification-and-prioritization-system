# Common lead schema

Every lead, from any channel, becomes **one flat record** with the fields below
(`src/fana/normalize.py` -> `normalize_lead()`, runtime copy in `n8n/fana_core.js`).
It is stored in the n8n Data Table `fana_leads` (`lead_json`) and mapped to HubSpot.

**Unknown rule:** nothing is guessed. Enums that are not known are the string `"unknown"`;
values that are not known are `null`. Invalid inputs become `null`/`invalid` and the raw value is
kept in `issues[]`. Every unknown core field is listed in `missing_fields` and in the sales reason.

## Field mapping (PDF field -> schema field)

### Identity and source
| PDF field | Schema field | Type / allowed values | Normalization |
|---|---|---|---|
| **Lead ID** | `lead_key` | string: `<auction>:<E.164 mobile>` -> else `<auction>:<email>` -> else `<auction>:anon:<event_key>` | Stable dedupe identity; one lead per person per auction |
| (event ID) | `event_key` | 20-hex hash of `source + source event id` (payload hash if no id) | Idempotency key per incoming event |
| Source | `source`, `first_source`, `sources[]` | `landing_page` \| `meta_lead_ads` \| `whatsapp` | Latest, first and full channel history |
| Campaign | `campaign` | string | landing `campaign`, Meta `campaign_name`, WhatsApp `referral_campaign`, CSV `csv_import:<file>` |
| Name | `name` | string \| null | Whitespace collapsed; first + last joined when split |
| Mobile | `mobile`, `mobile_status` | E.164 string \| null; `valid_sa` \| `valid_intl` \| `invalid` \| `missing` | Arabic-Indic digits, `05…`, `5…`, `9665…`, `009665…`, spaces/dashes -> `+9665XXXXXXXX` |
| Email | `email`, `email_status` | string \| null; `valid` \| `invalid` \| `missing` | Trimmed, lower-cased, format-checked |
| City | `city` | `Dammam` \| `Khobar` \| `Dhahran` \| `Qatif` \| `Jubail` \| `Riyadh` \| `Jeddah` \| raw text \| null | الدمام / Dammam -> `Dammam` |

### Buying needs
| PDF field | Schema field | Type / allowed values | Normalization |
|---|---|---|---|
| Interested asset | `interested_asset` | one of 11 catalog ids (e.g. `villa_nakheel_dammam`) \| null | Type + district matching; ambiguous text stays at type level |
| Property type | `property_type` | `villa` \| `apartment` \| `residential_building` \| `commercial_building` \| `commercial_land` \| null | Arabic/English keywords (فيلا/فله/villa...) |
| Budget range | `budget_min`, `budget_max`, `budget_raw`, `budget_status`, `budget_approx`, `budget_flexible_up` | SAR numbers \| null; status `known` \| `vague` \| `unknown` | Deterministic parser: `حول المليون` -> 850K-1.15M, `مليون ونص بالكثير` -> max 1.5M, `بين 800 ألف ومليون`, `3 million`; `على حسب` -> vague (never guessed) |
| Investment purpose | `investment_purpose` | `residence` \| `investment` \| `business` \| `unknown` | سكن/استثمار/تجاري + English |
| Individual or company | `buyer_type` | `individual` \| `company` \| `unknown` | فرد/شركة + English |
| Auction experience | `auction_experience` | `yes` \| `no` \| `unknown` | نعم/لا/أول مرة + English |

### Readiness and contact
| PDF field | Schema field | Type / allowed values | Normalization |
|---|---|---|---|
| **Purchase readiness** | `intent` + P1 gate | `high` \| `medium` \| `low` \| `unknown` (LLM classification with confidence) | "Ready for sales now" = P1 gate: funds ready + this auction + asset known + no uncertain field |
| Cash or financing readiness | `financing` | `cash` \| `financing_approved` \| `financing_in_progress` \| `financing_needed` \| `unknown` | كاش / تمويل معتمد / قيد الإجراء / أحتاج تمويل + English |
| Timeline | `timeline` | `this_auction` \| `within_3_months` \| `later` \| `unknown` | هذا المزاد / خلال شهر / لاحقاً + English |
| Preferred contact method | `preferred_contact` | `call` \| `whatsapp` \| `email` \| `unknown` | WhatsApp leads default to `whatsapp` |
| Consent or contact status | `consent`, `flags.opt_out`, `flags.contact_later`, `flags.human_requested` | consent `granted` \| `denied` \| `unknown`; booleans | A customer who starts a WhatsApp chat = consent granted (confidence 0.8); opt-out -> Disqualified |

### Workflow record
| PDF field | Schema field | Type | Rule |
|---|---|---|---|
| Created date | `created_at` | ISO-8601 UTC | Earliest across merged events |
| Last interaction | `last_interaction_at` | ISO-8601 UTC | Latest across merged events |
| Qualification status | `qualification_status` | `in_qualification` \| `qualified` \| `needs_review` \| `handoff_requested` \| `disqualified` | From `scoring.score_lead()` |
| Score | `score`, `breakdown{}` | 0-100 + points per component | Transparent rules (README) |
| Reason | `reason_ar`, `reason_en`, `priority`, `missing_fields[]`, `uncertain_fields[]` | strings / lists | Built by code from the same facts |

### Audit fields
`confidence{}` (per field, 0-1), `history[]` (every changed value: field, old, new, at, source),
`issues[]` (invalid raw inputs), `unmapped_fields{}` (unknown source columns, kept), `hubspot_contact_id`, `hubspot_deal_id`.

## Source-specific field names

One alias map (`FIELD_ALIASES`) resolves ~60 Arabic and English keys after Arabic normalization, e.g.
`الجوال` / `رقم الجوال` / `phone_number` / `mobile` -> mobile; `متى تنوي الشراء` / `when_do_you_plan_to_buy?` -> timeline;
`طريقة الدفع` / `payment_method` -> financing. Unknown keys are kept in `unmapped_fields`.

| Channel | Payload shape | Adapter |
|---|---|---|
| Landing page / website inquiry | `{event_id, campaign, submitted_at, fields{...}}` (Arabic or English keys; free text in `notes`/`رسالتك`) | `adapt_landing_page` |
| Meta Lead Ads | `{id, campaign_name, created_time, field_data[{name, values[]}]}` | `adapt_meta` |
| WhatsApp Cloud API | `entry[0].changes[0].value.{contacts, messages}` | `adapt_whatsapp` |
| CSV import | any columns, Arabic or English headers -> landing-form payload (`scripts/import_csv.py`) | reuses `adapt_landing_page` |
| Google Lead Form *(planned)* | `user_column_data[{column_id, string_value}]` | one more adapter feeding the same alias map |

## Deduplication

1. **Event level (idempotency):** `event_key` is checked before any work and recorded only after HubSpot succeeds -> a replayed webhook or re-imported CSV row returns `duplicate_ignored`.
2. **Lead level:** same `lead_key` -> `merge_leads()`: newer **known** values win, unknown never overwrites known, every change goes to `history[]`, sources are unioned, created date = earliest, last interaction = latest.
3. **CRM level:** before creating a HubSpot Contact, search by E.164 phone OR `hs_searchable_calculated_phone_number` (matches contacts typed by hand in any format) OR email OR `fana_lead_key`. One Deal per `lead_key`.

## Example record (abridged, live lead)
```json
{
  "lead_key": "al-tilal-al-sharqiya:+966502223344", "source": "whatsapp", "campaign": "snapchat_ctwa",
  "name": "أبو سعود", "mobile": "+966502223344", "mobile_status": "valid_sa", "email": null, "city": null,
  "interested_asset": "villa_nakheel_dammam", "property_type": "villa",
  "budget_min": null, "budget_max": 1500000, "budget_raw": "مليون ونص بالكثير", "budget_status": "known",
  "investment_purpose": "residence", "buyer_type": "individual", "auction_experience": "unknown",
  "intent": "high", "financing": "cash", "timeline": "this_auction", "preferred_contact": "whatsapp", "consent": "granted",
  "qualification_status": "qualified", "score": 100, "priority": "P1",
  "reason_ar": "P1 (100/100) | فيلا سكنية - حي النخيل - الدمام | الميزانية حتى 1.5M ريال (مناسبة) | السيولة جاهزة (كاش) | يرغب بالمشاركة في هذا المزاد | تم تعديل الميزانية",
  "history": [{"field": "budget_max", "old": 2300000, "new": 1500000, "source": "agent"}],
  "confidence": {"asset": 1, "budget": 0.9, "financing": 1, "timeline": 0.9, "intent": 1},
  "issues": [], "unmapped_fields": {}
}
```
