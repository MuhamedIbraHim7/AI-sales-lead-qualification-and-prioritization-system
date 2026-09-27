# Qualification, priority and ranking - how decisions are made

Source of truth: `src/fana/scoring.py` (runtime copy in the n8n Core Engine, parity-tested).
Principle: **the LLM proposes, the rules decide.** Every point, priority and reason can be traced to a field value.

## 1. Who decides what

| Decision | Owner | Why this owner |
|---|---|---|
| Arabic reply to the customer | LLM (GPT-4o) | Natural language is the LLM's strength |
| Extract asset, budget words, financing, timeline, buyer type, purpose | LLM -> **structured JSON** with an exact evidence quote + confidence | Understands free Gulf Arabic; the quote makes every value verifiable |
| Accept or reject each extracted value | Rules (`apply_ai`) | Catalog / allowed-value lists and "quote must appear in the customer's message" stop invented data |
| Budget numbers (min / max) | Rules (deterministic parser) | Money must be exact and repeatable; the LLM only supplies the phrase |
| Intent class (high / medium / low) + flags (person, prices only, later, opt-out, not interested) | LLM classification | Needs whole-message understanding; a keyword net backs up person/opt-out |
| Points, priority, deal stage, queue order, reason text | Rules (`score_lead`, `rank_queue`) | Must be explainable, auditable, identical on every run |

Consequence: the LLM contributes **at most 15 of 100 points** (intent) and **cannot create a P1 on its own** - P1 needs deterministic facts.

## 2. The score (0-100) and why these weights

| Component | Points | Rule | Rationale |
|---|---|---|---|
| Budget fit | 25 | covers asset-type band 25 · within 30% below 12 · below 0 | Biggest predictor of a real bidder; a budget that cannot win the asset wastes a sales call |
| Asset fit | 20 | specific catalog asset 20 · type only 12 | A named asset means research done and a concrete conversation for sales |
| Financial readiness | 20 | cash / approved financing 20 · in progress 10 · needs financing 5 | Auctions close fast; unresolved financing is the most common reason a keen buyer cannot bid |
| Timeline | 15 | this auction 15 · within 3 months 8 · later 3 | This auction has a fixed date; later buyers are nurture |
| Intent (LLM) | 15 | high 15 · medium 8 · low 2 | Useful signal but the softest one, so capped |
| Contactability | 5 | valid mobile and consent not denied | Small, but a lead you cannot reach is worth little |

Budget bands per asset type are **synthetic assumptions** (the auction page shows no prices) and are never quoted to customers.

## 3. From score to priority (gates, not just thresholds)

| Priority | Rule | Meaning |
|---|---|---|
| **P1** | score >= 70 **and** cash/approved financing **and** timeline = this auction **and** asset known **and** no uncertain core field | Hot, ready for sales now |
| **P2** | score >= 45, or a P1-level score with financing unresolved ("held at P2"), or asked for a person | Qualified, needs follow-up |
| **P3** | everything else; price-only browsers and contact-later leads are capped at P3 | Nurture |
| **Disqualified** | only on **confident** facts: opt-out / consent denied; stated no interest; no valid mobile or email; confirmed budget (confidence >= 0.7) below 50% of the cheapest asset band | Not a lead for this auction - always with a reason |

A score alone is not enough - live example: **سعد scored 53** (knows the asset, pays cash) but is **Disqualified** because his confirmed budget (200K) cannot buy any asset.

### Decision: why a price-only "browser" is P3, not Disqualified
The PDF groups "disqualified or low-intent". We deliberately treat **low intent as P3 (nurture)** and reserve Disqualified for
**confident, explicit** signals (not interested, opt-out, unreachable, budget impossible). Reasons: a browser who asks the price has
still clicked a paid ad and may convert once they learn how the auction works; disqualifying them throws away paid acquisition and
removes them from nurture. Low intent is still visible (intent = low, 2 points, "price questions only" in the reason, capped at P3).

## 4. How incomplete information affects the result
- An unknown field earns **0 points** - never a penalty beyond that, never a disqualification.
- It is listed in `missing_fields` and in the reason ("ناقص: الميزانية، التمويل").
- It blocks P1 (the gate needs asset, funds and timeline) -> the lead cannot jump the queue on partial data.
- Status becomes `in_qualification` and the agent asks for exactly those fields next.
- Example: a form with only name + mobile -> **P3 (5/100), in_qualification**, all four core fields listed as missing.

## 5. The reason sales sees
Built by code from the same facts as the score (Arabic + English), so it can never contain invented information:
`P1 (100/100) | فيلا سكنية - حي النخيل - الدمام | الميزانية حتى 1.5M ريال (مناسبة) | السيولة جاهزة (كاش) | يرغب بالمشاركة في هذا المزاد | تم تعديل الميزانية`
Special notes are appended when they apply: "held at P2: financing unresolved", "raised to P2: asked for a person",
"طلب التواصل لاحقاً", "يحتاج مراجعة: الميزانية". Disqualified leads get a one-line cause.

## 6. Worked examples (live leads)

| Lead | Breakdown (asset / budget / funds / timeline / intent / contact) | Score | Priority and why |
|---|---|---|---|
| أبو سعود, turn 1: Nakheel villa, ~2M, this auction | 20 / 25 / 0 / 15 / 15 / 5 | 80 | **P2** - score is P1-level but funds unknown -> held |
| أبو سعود, turn 2: "كاش… خلها مليون ونص بالكثير" | 20 / 25 / 20 / 15 / 15 / 5 | 100 | **P1** - all gates pass; reason notes "budget revised" |
| ماجد: Al Nour apartment, "حول المليون", needs bank financing | 20 / 25 / 5 / 0 / 15 / 5 | 70 | **P2** - financing unresolved, timeline missing |
| فهد: "أبي أكلم موظف" about Tahlia building | 20 / 0 / 0 / 0 / 8 / 5 | 33 | **P2** - raised from P3 because he asked for a person; top of P2 |
| سعد: Al Nour apartment, "200 ألف بس", cash | 20 / 0 / 20 / 0 / 8 / 5 | 53 | **Disqualified** - confirmed budget far below every asset |

## 7. Ranking the queue (beyond the score)
Sort keys, in order:
1. **Priority bucket** P1 > P2 > P3 > Disqualified
2. **Asked for a person** first (a waiting human request beats a higher score)
3. **Funds readiness**: cash > approved > in progress > needed > unknown (who can actually bid)
4. Score
5. Budget size (larger opportunity first)
6. Most recent interaction (the conversation is warm)
7. Oldest lead (fair tie-break)

Live queue example: two P1s at 85 are ordered cash before approved financing; فهد (P2, score 33, asked for a person) sits
**above** P2 leads scoring 70.
Trade-off considered, not built: `needs_review` leads currently sort inside P3 by the same keys; ranking them above plain nurture
would surface human-review work sooner. Kept simple because they already trigger a Telegram review alert.

## 8. Uncertain vs genuinely unqualified

| | Uncertain interpretation | Genuinely unqualified |
|---|---|---|
| Trigger | answer present but confidence < 0.6, or budget words the parser cannot turn into numbers ("على حسب الوضع") | a confident disqualifying fact (budget confidence >= 0.7, explicit "not interested", opt-out, unreachable) |
| Status / priority | `needs_review`; P1 blocked; **never Disqualified** | `disqualified`; deal -> Closed Lost |
| What happens | agent asks a clarifying question first; Telegram review alert; queue action "Review conversation and clarify" | polite close; no follow-up; reason stored |
| Live pair (same topic: budget) | عمر - "الميزانية فوالله على حسب الوضع" -> needs_review, agent asked for a rough budget (exec 184) | سعد - "ميزانيتي 200 ألف بس" -> Disqualified: budget far below every asset (exec 194) |
