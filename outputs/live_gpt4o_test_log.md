# Live GPT-4o test log (n8n Cloud + Azure OpenAI gpt-4o + HubSpot) - 27 Sep 2026

All runs: WhatsApp webhook -> FANA 01 -> FANA 02 (real gpt-4o, JSON mode) -> Core Engine validation -> HubSpot Contact + Deal.

| # | Customer message (Arabic) | Agent reply (real gpt-4o) | Validation | Result | HubSpot | Exec |
|---|---|---|---|---|---|---|
| 6 Natural Arabic | السلام عليكم، أدور فيلا للعائلة في النخيل وميزانيتي تقريباً مليونين، ولو المكان مناسب نبي ندخل هذا المزاد | وعليكم السلام أبو سعود، حياك الله! فهمت أنك مهتم بفيلا في حي النخيل وميزانيتك حوالي مليونين. هل تخطط للشراء نقداً أم تحتاج إلى تمويل؟ | accepted asset, budget (1.7M-2.3M), timeline, buyer_type, purpose; **rejected preferred_contact (no evidence)** | P2 80, held: financing unknown | Deal 523045601499 created, Follow-up (P2) | 100 |
| 7 Changed info (same customer, turn 2) | الدفع كاش إن شاء الله، بس بعد ما فكرت خلها مليون ونص بالكثير | شكرًا أبو سعود على التوضيح. فهمت أنك تخطط للدفع كاش وأن ميزانيتك الآن مليون ونص بالكثير… | budget changed 2.3M -> max 1.5M (history kept), cash accepted | **P1 100**, reason "تم تعديل الميزانية" | **Same deal updated -> Sales Ready (P1)** | 105 |
| 10 Human request | أبي أكلم موظف لو سمحت، ما عندي وقت للأسئلة | أبشر، سجلت طلبك وبيتواصل معك أحد مستشاري المبيعات في أقرب وقت. (template) | human_requested = true | P2, handoff_requested, "ASKED FOR A PERSON - call now" | Deal 523045542099 | 110 |
| 3 Browser | كم سعر الشقة اللي بالنور؟ بس أبغى أعرف الأسعار | الأسعار تُحدد عن طريق المزايدة في المزاد، وسيتم التواصل معك من قبل مستشار مبيعات… هل لديك ميزانية محددة؟ | no price stated (guard ok); price_only flag; intent low | P3 27 | Deal 523045577922, AI Qualifying | 115 |
| 9 Uncertainty | مهتم بالمزاد، بس الميزانية والله يمكن، على حسب الوضع، الله يسهل | حياك الله سلطان! فهمت أنك مهتم بالمزاد. هل تفضل نوع معين من العقارات مثل الفلل أو العمائر؟ | **budget_uninterpretable -> confidence 0.4** | **needs_review (not disqualified)**, P3 | Deal 523046133989, AI Qualifying | 120 |

Earlier live evidence (fallback path, LLM unreachable): execution 86/95 - template reply sent, lead scored and CRM updated anyway.
Fix applied: the Azure resource is an AI Foundry endpoint (`*.cognitiveservices.azure.com`), which n8n's LangChain Azure node
cannot target (it builds `*.openai.azure.com`). FANA 02 now calls the chat-completions endpoint with an HTTP Request node
authenticated by the existing Azure credential (key never in the workflow or repo).

## Gap-closing live runs (27 Sep, after review against the PDF)

| Test | Input | Result | Exec |
|---|---|---|---|
| **8 Existing contact** | Contact created **by hand in HubSpot UI** (عبدالله الحربي, typed 0533334444, source CRM_UI); Meta lead with `05 3333 4444` | Found via `hs_searchable_calculated_phone_number`, **updated, no duplicate**, owner kept; deal created + linked; P1 85 (approved financing, 5M, this auction) | 125 |
| **4 Missing data** | Landing form: الاسم + الجوال only | P3, in_qualification (not disqualified), missing asset/budget/financing/timeline listed; deal in New Lead | 139 |
| **§3 "around one million" + financing (conversation)** | مهتم بالشقة اللي بالنور، ميزانيتي حول المليون بس أحتاج تمويل من البنك | حول المليون -> 850K-1.15M (fits apartment); financing_needed; **held at P2**; reply asks whether financing is already approved | 129 |
| **§3 Contact later - BUG FOUND** | مشغولة هالأيام، كلموني الأسبوع الجاي | LLM flagged contact_later correctly, but the keyword safety net matched "كلموني" -> false "asked for a person / call now" | 134 |
| **§3 Contact later - after fix** | same message, new customer | contact_later = true, human_requested = false, **P3**, reason "طلب التواصل لاحقاً"; reply: "أكيد بنكلمك الأسبوع الجاي على الواتساب. متى الوقت المناسب لك؟" | 143 |
| **Live sales queue (FANA 03)** | all live leads | 12 leads ranked: P1 (cash before approved financing) > P2 (asked for a person first) > P3, each with next action + reason | 148 |

Fix applied: keyword net limited to unambiguous words; contact-later caps at P3 and is always stated in the reason.
Python + JS updated together; regression + parity tests added (49 tests). Note: the record from exec 134 (هند) predates the fix.

## CSV import + invalid-input handling (live, `fixtures/sample_import.csv` via landing webhook)

| CSV row | Input | Result | Exec |
|---|---|---|---|
| 3 | Ahmad Saleh, mobile `12345` (invalid), valid email, villa, 3 million, cash, within 3 months | `invalid_mobile:12345` in issues; **lead_key falls back to email**; no bad phone sent to HubSpot; P2 65; contact + deal created, campaign `csv_import:sample_import.csv` | 152 |
| 4 | يوسف, mobile `abc`, email `yousef@` (both invalid) | anon key; **Disqualified: "لا يوجد رقم جوال أو بريد صالح للتواصل"**; deal in Closed Lost (kept for audit) | 156 |
| 5 | لطيفة, `+966 50 999 8877`, email `latifa.test@@example.com` (invalid) | phone -> `+966509998877`; **invalid email not written to HubSpot**; P2 55 | 160 |
| 6 | سارة القحطاني (already in HubSpot from Meta), now "تمويل معتمد من البنك" | existing contact found + updated (no duplicate); sources meta + landing; **P2 -> P1 85; same deal moved Follow-up (P2) -> Sales Ready (P1)** | 164 |
| 6 again | identical row re-imported | **duplicate_ignored**, HubSpot not called | 168 |

Offline (`python scripts/import_csv.py fixtures/sample_import.csv`) additionally shows row 7 (same person as row 2 with `+966 59 888 7766`) merging into one lead, and the unknown `Broker` column kept in `unmapped_fields`.

## Section 3 review fixes (27 Sep)

| Test | Input | Result | Exec |
|---|---|---|---|
| **Cross-channel memory** | سارة (asset, budget, financing, timeline known from Meta form + CSV) writes on WhatsApp: "عبيت النموذج عندكم عن فيلا العقربية، وش الخطوة الجاية؟" | `ask_next = [buyer_type]` - asked only "الشراء بيكون باسمك الشخصي ولا باسم شركة؟"; P1, `sales_handoff_ready = true` on the Deal; **no duplicate alert** (was already P1) | 174 |
| FANA 00 re-run | add `sales_handoff_ready` | 1 property created, 16 skipped (409), **pipeline stages untouched** (bug fixed: re-run used to replace stages) | 173 |
| Clarify-first (LLM down) | بدر: same message while Azure returned "Authorization failed" | fallback template sent, lead saved (resilience) | 179 |
| **Clarify-first + review alert** (Azure fixed) | عمر: "مهتم بفيلا العقربية بالخبر، أما الميزانية فوالله على حسب الوضع" | asset accepted; budget `uninterpretable` -> needs_review; **agent clarified budget first**: "هل ممكن تعطيني فكرة تقريبية عن الميزانية المناسبة لك؟"; **Telegram alert #21 delivered** "🔍 يحتاج مراجعة بشرية" with name, mobile, reason, HubSpot link | 184 |
| **Human request alert** | فهد: "أبي أكلم موظف بخصوص العمارة التجارية بالتحلية" | handoff_requested, P2, `sales_handoff_ready = true`; **Telegram alert #22 delivered** "🙋 طلب التحدث مع موظف - اتصل الآن" | 189 |

Note: exec 174 also ran on the fallback path (Azure authorization failure) - the cross-channel result comes from the deterministic `ask_next`, which is what drives both the LLM prompt and the template.

## Section 4 review: confident disqualification vs uncertainty (27 Sep)

| Test | Customer message | Result | Exec |
|---|---|---|---|
| **Clear but impossible budget** | "أبغى شقة النور، بس ميزانيتي 200 ألف بس والدفع كاش" | budget 200K (confidence 0.9) < 50% of cheapest band -> **Disqualified: "الميزانية المؤكدة أقل بكثير من جميع أصول المزاد"** although score = 53 (score alone is not enough); deal Closed Lost | 194 |
| Contrast: vague budget | "الميزانية فوالله على حسب الوضع" | **needs_review, not disqualified**; agent asked for a rough budget | 184 |
| Not interested - **BUG FOUND** | "شكراً لكم، بس مو مهتم بالمزاد ولا أبغى أشتري" | disqualified but with the WRONG reason "طلب العميل عدم التواصل" (LLM also set opt_out) | 199 |
| Not interested - after fix | same message, new customer | not_interested = true, opt_out = false -> **"أفاد العميل بعدم اهتمامه"**; polite close reply | 204 |

Fixes: strict flag definitions in the prompt (opt_out only for "stop / do not contact"; not interested is not opt-out);
deal name keeps the asset for disqualified leads (was "asset unknown").

## Sections 5/6 + reliability fixes (27 Sep)

| Test | Result | Exec |
|---|---|---|
| Back-to-back events, same lead (landing form, then cash + this auction 10 s later) | **same deal updated, no duplicate**; P2 -> P1; 🔥 P1 Telegram alert. HubSpot index had caught up (`deal_id_source = search`); stored-id fallback now guards the indexing window | 209, 213 |
| FANA 03 with Excel output | `.xlsx` (Arabic-safe) + CSV produced | 217 |
| FANA 99 error handler (simulated HubSpot 429 after 3 retries in "Create HubSpot Deal") | row written to `fana_event_log`; **Telegram alert #24** "⚠️ FANA system error ... safe to replay after the fix" | 219 |
| Error workflow attached | FANA 01, 02, 03, 90 settings -> `errorWorkflow = FANA 99` (02 and 90 republished) | - |
