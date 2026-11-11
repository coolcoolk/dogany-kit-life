---
name: shopping-log
display_name: 쇼핑 기록
description: >-
  Capture actual purchases, returns, owned-item facts, post-use feedback, depleted stock, body/garment measurements,
  stated taste, product selections and cart/order/delivery updates in one turn, and recall previously recorded items or
  next-day ingredients. A desire for a thing with no underlying need is captured here; a search/pick request goes to
  shopping-session. Entry follows the supplied facts and active purchase/selection context, including screenshot/link
  follow-ups, independently of module activation. A bare wish to begin shopping management with no item/measurement
  content hands to core-log ## later setup rather than a generic purchase-field question. Writes through kit_life.sh
  shop-* verbs (body sizes through log-metric); one confirmation in shopping terms plus the verb's one ask. Never pays
  or checks out.
  Examples only, never wording requirements: "샀어", "이거 샀다", "허리 82 나왔어", "이 바지 실측 허리 41 총장 102", "그 샴푸 얼마였지".
---

For start/use intent with no record/search/place content: `database/kit_life.sh manage-state shopping` (MANAGE<TAB>shopping<TAB>on|off<TAB>reason). Off -> core-log `## later setup`; on -> normal use/readback before questions.

# shopping-log -- purchases, owned things, sizes, taste (module_shopping P2)

every write = `$LKIT shop-*`, `LKIT="${PROJECT_ROOT:-$(pwd)}/database/kit_life.sh"`.
Tier: find-first, price home, ledger write, missing-field list, withdrawal
line, one reminder, size draft order = GUARANTEED (verbs); trigger =
BEST-EFFORT.

## Context before person or gift answers

First run `kit_life.sh noun-context "<current message + known recipient/occasion from this conversation>"` using `${PROJECT_ROOT:-$(pwd)}/database/kit_life.sh` from the agent workspace. Read PERSON / PERSON_REL / PERSON_EVENT and EVENT before answering or asking who/where/when; no EVENT does not mean no person facts.
An unresolved nickname: `person-resolve`, then repeat noun-context with `--span "<resolved canonical name>"`; NEAR / AMBIGUOUS is not a resolved person. For missing details use `person-show <id>`.
Kit person facts override agent memory. A memory fact phrased as a future expectation whose date has passed must not be asserted: check the kit, or ask once if still unknown. A recorded child birth is past, never an upcoming delivery; do not infer medical/recovery facts.
Product "찾아줘" / "다 찾아줘", gift purchase sources, and packaging searches go straight to shopping-session's search flow and concrete product cards, including follow-ups without a repeated recipient. Never answer those with generic advice or an offer to search later.

## record

Chosen products from screenshots/links, cart/order/delivery updates,
purchases, returns, after-use words, measurements, stated taste and plain
wants follow `.claude/skills-bundle/core-log/reference/shopping.md`.
`shop-item-upsert` identifies the product; `shop-capture` records its purpose,
pack facts and status; `shop-capture-upd` promotes orders/purchases through
`shop-bought` once. "담았어" is in_cart; it does not write a payment.

## management (only while `$LKIT manage-state shopping` is on)

- `shop-bought` MISSING + ASK -> send the ASK line once, as the trailing
  line. answer -> `shop-owned-upd <id> --store .. / --link .. / --price N`.
  a skip is final: never ask again.
- SPEND_CATEGORY_ASK -> send its ASK with the listed ledger categories in
  owner words; answer -> `shop-cat-upd <cat> --spend-category <name>`
  (remembered for that kind).

## recall

- Before cooking/prep/event ingredient questions, `shop-capture-find
  --purpose "<dish/occasion>"` or `--event <id>` first, then
  `shop-capture-find "<ingredient>"`; also `shop-owned-find "<ingredient>"`
  for older purchases. Use recorded name/brand/size/price/store directly.
  Never ask for a fact these rows hold; unknown fields do not erase a row.
- `shop-owned-find "<key noun>" [--category c]`. FOUND rows answer with
  date, store, price. NEAR -> one question. NONE -> also search memory
  before saying it was never bought.
- `shop-owned-list [--category c] [--status s]` for "what do I have".

## size for a candidate

`shop-size-draft --hunt <id> --chart '<json>'`, chart rows parsed from the
product page: `{"size":"M", "<part>":cm,..., "height":"170-175",
"weight":"60-70", "foot_mm":265}`. SIZE_DRAFT -> say the size and its basis
in owner words. SIZE_NEED + ASK -> that one number only, write it as a body
fact, rerun.

## taste reads

"what do you know about my taste" -> `shop-taste-list`; change ->
`shop-taste-upd` / `shop-taste-retire`.

## a want without a need (cooling-off)

`shop-want` printed OFFER_COOLOFF (management on; off it prints WANT_NOTED
and the adapter is done):
1. send the SAY line once.
2. answer -> `shop-want-answer --ask "<same words>" --choice now|later|
   reflect [--category c]`:
   - now -> straight to shopping-session, no follow-up.
   - later -> one reminder is set; send its SAY.
   - reflect -> ask the QUESTION lines one per turn (ON_HAND rows answer
     question 1 from the record first); then the owner decides now/later.
   - no answer / "no" -> drop it. nothing comes back.
COOLOFF_PENDING -> a reminder is already set; say so in one line.

## hard rules

1. ask only what a verb printed as ASK, once. never re-ask a skipped
   field. recording never waits for an answer.
2. the waiting days are the owner's own rule: never call it research,
   never call a want or a purchase impulsive, no countdowns, no
   "sale ends" wording, never bring a not-bought thing back up.
3. a gift's size -> that person's facts only, never the owner's body.
4. shopping facts go to shop-* verbs, never to memory.
5. user language only; never verb names, flags, codes, tokens, ids.

## siblings

- finding a thing -> shopping-session. body facts -> `body-brief`.
- the ledger's own views -> ledger-view.
