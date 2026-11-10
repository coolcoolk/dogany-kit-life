# core-log -- shopping adapter (purchases, owned things, sizes, taste, wants)

Capture-first for a narrated lived action: save `experience-capture
"<owner statement, verbatim>" --title "<activity>" [--on <stated day>]
[--at <stated ISO time with timezone>]` before asking about portions, amounts,
payment or sources. Unknown occurrence time stays unknown. Keep its event id
for enrichment; retry the same day/title without creating a task. Successful
specialized writes silently attach the experience; retain their existing reply
shape, with no new menus or questions from the experience layer.

Before replying after capture, apply the common SETTLE ON EVIDENCE contract
from AGENT: saving facts alone does not finish an activity report. Read
`.claude/skills-bundle/schedule-log/reference/completion-chat.md` for the
shared completion check. A PLANNED item settled this turn: its single
completion reply overrides this adapter's capture-report/card instructions.
No open task/routine matches: no completion reply at all, hand over to
reference/episode.md. Tool work stays silent until the final line.

The record rules for shopping. shopping-log points here; recall, the
cooling-off flow and the size draft stay there, the search flow stays in
shopping-session. Every write = `$LKIT shop-*`.

## Acquired supplies with unknown payment

"Got a bag of bean pulp" proves acquisition, not purchase. Capture the full
statement first with experience-capture. Retain the one stated bag with
`resource-add --kind supply --name "<item>" --detail
'{"quantity":1,"unit":"bag","payment":"unknown"}' --json`, then
`experience-attach-record <event_id> ledger_resource <resource_id>`.
Do not invent weight, price, a store or a paid purchase. A later confirmed
purchase may use shop-bought --experience <event_id>; it enriches the same
experience. This path introduces no question or menu. Reply with one short natural
reaction only; no recording announcement, progress narration or internal
routing text. Unknown source/payment details wait for evening retro.

## Contract and warranty capture

For a contract, bill, policy, recurring plan or device warranty, read
`service/kit_life/contracts.md` and use the contract service in the same turn.
Save partial facts immediately and relay its Candidate A card and vertical
buttons. A contract creates no payment. A receipt keeps the ordinary purchase
and ledger review path, then enriches the existing possession as a warranty
contract without another purchase or stock refill. No warranty duration is
inferred from a receipt. Source attachments are retained by the kit proof store;
chat resend remains unsupported and must not be promised.

## selections from screenshots or links (record before advising)

A product the owner selects for a dish, occasion or other purpose is a
shopping fact even before payment. Read the screenshot/link and capture
what it actually shows; a product merely compared or rejected is not a pick.
Do this in the same turn, including follow-ups such as "I put the mushrooms in the cart".
Record while shopping management is off too; never wait for a purchase.

1. Reuse `shop-item-find "<product>"` / `shop-item-upsert --category
   food_pantry --name "<product>" [--brand "<brand>"] [--variant "<pack>"]
   [--url <url>] [--image <ref>]` (choose the actual category for non-food).
   Keep the precise product name and put every shown brand in `--brand`.
   Use the shopping conversation's stated date in `--on` when replaying a
   past selection; a status follow-up updates its existing capture.
   Add an ingredient alias with
   `shop-item-upd <id> --alias "<ingredient>"` when useful for later recall.
   Do not invent a brand, store, amount or weight, or ask for absent fields.
2. `shop-capture <item_id> --purpose "<dish/occasion, owner words>"
   [--event <existing event id|ulid>] [--hunt <existing hunt id>]
   [--size "300g"] [--price 20800] [--store "<shown store>"]
   [--link <url>] [--on YYYY-MM-DD] [--note "<owner words/source>"]
   --status picked|in_cart|ordered|bought`.
   Size is the selected pack's weight/volume/count, not recipe consumption.
   Same item + purpose + event + selection date resumes the capture. Several
   ingredients for one purpose get separate captures. Reuse existing search
   hunts/rounds when present; owner-provided choices do not require fabricating
   a search round, verification claims, or three alternative cards.
3. Owner picked it -> `picked`; "added it to the cart" -> `in_cart`, not a purchase.
   Read `shop-capture-find "<ingredient>" --purpose "<dish>"` (or `--event
   <id>`) and use `shop-capture-upd <capture_id> --status in_cart` for a
   follow-up. Do not create a second capture for a status change next day.
4. "ordered it" -> `ordered`; "bought it" or delivered -> `bought` using
   `shop-capture-upd <id> --status ordered|bought [--on <purchase date>]`.
   These two statuses invoke the existing `shop-bought` path once, filling
   store/link/price/size from the capture: shopping record + ledger under
   the existing stored link policy, with only its unknown-field ASK once.
   Relay its SAY/ASK/NOTICE as in "bought it" below. Delivery after an order
   changes status only; it must not create another payment. A cart never
   writes a ledger row. Repeated updates are safe. `LEDGER_EXISTS` still
   requires the owner's answer; retry with `--link-spend <id>` or
   `--new-spend` only as that answer permits. Corrections use the same
   `shop-capture-upd` fields; bought prices read from their single ledger/
   owned home. `--on` on an already purchased capture corrects both its
   purchase date and linked ledger date; a new purchase before selected_on
   is refused. Returns still use `shop-owned-upd`, as below.

## facts before cooking, prep or event questions

Before asking for an ingredient fact, read `shop-capture-find --purpose
"<dish/occasion>"` or `shop-capture-find --event <id>` (no positional
product filter on that purpose/event pass), then
`shop-capture-find "<ingredient>"` (aliases included). Also use
`shop-owned-find "<ingredient>"` for purchases predating capture support.
Read the returned name, brand, size_label, price, store and status. Use
known facts directly: never ask for a weight/price/store held in these rows.
A row with unknown weight/price is still a recorded selected item, never
an absent ingredient. For cooking/prep, answer the needed recorded facts
first; do not turn unknown fields into claims that an ingredient is missing.
A cart record establishes the product/pack facts, not proof of purchase;
keep that distinction in the reply. Several matching packs -> use the
purpose/event and dates; ask only a remaining real ambiguity. NONE means
no capture matched, not that nothing was bought. Never substitute a memory
search or dashboard spend total for the shopping lookup.

## bought it

1. `shop-bought` with only what the owner said: `[--store "<s>"] [--link
   <url>] [--price N] [--on YYYY-MM-DD] [--size <label>] [--online 0|1]`.
   - out of a find (a picked card) -> no item args; the verb fills store,
     link, price from the card and closes the hunt. AMBIGUOUS_HUNT -> pick
     `--hunt <id>` from its list (by the owner's words).
   - not from a find -> `--name "<product, owner words>" --category <c>`
     (+ `--brand`). NEAR_MATCH -> send its ASK, then near-match-accept or
     `--new-confirmed`. The number after NEAR_MATCH is the candidate COUNT, never an id; the id is the first column of each candidate row below it.
   - receipt / capture / link in the message -> read store and price from
     it; never ask what is shown there.
2. read the output:
   - exit 3 EXISTS -> already logged; say so, nothing else.
   - exit 3 LEDGER_EXISTS -> send its ASK (same payment twice is a real
     ambiguity). same row -> rerun with `--link-spend <spend_id>`; a
     different purchase -> `--new-spend`.
   - MISSING + ASK / SPEND_CATEGORY_ASK appear only while shopping
     management is on (shopping-log owns them). Absent -> ask nothing.
   - SAY -> the confirmation. SAY_NOTICE -> say it once, right after.
   - the money side follows the stored link policy (`LEDGER spend|off`):
     never change it, never ask about it.
3. price + shipping -> `--price <total> --note "<split>"`. vague number ->
   record it as said. "don't know" -> no price.
4. returned -> `shop-owned-upd <id> --status returned --refund` (refund row
   only when a money row is linked).

## after use

`shop-owned-find "<key noun>"` -> `shop-owned-upd <id> --satisfaction
liked|neutral|disliked --verbatim "<owner words>"`, `--fit
small|right|large` (clothes, shoes), `--status used_up|gone`. mixed words ->
neutral + verbatim. record only what was said; no follow-up question.

## sizes

- body size heard (waist, foot length) -> body fact: `log-metric <today>
  waist_cm 82` (tokens chest_cm waist_cm hip_cm shoulder_width_cm inseam_cm
  arm_length_cm foot_length_mm; height -> `config-set height_cm=<n>`).
- garment measured -> find the owned row, then `shop-measure-add <owned_id>
  waist_half=41 total_length=102` (parts: total_length shoulder chest_half
  waist_half hip_half thigh_half rise hem_half sleeve; "cross-section" =
  *_half). MEASURE_DIFFERS -> ask which is right; corrected -> `--replace`.

## taste

A general rule the owner states (a liked scent, a brand they never buy) ->
`shop-taste-add <cat> --axis colour|fit|material|brand|style|scent|
price_band|size --value "<owner words>" --polarity like|avoid`. silent: no
confirmation line. TASTE_OPPOSITE -> ask which holds; then `--replace`. A
reject of one card is NOT a taste rule.

## a want with no need and no search request

`shop-want --ask "<owner words>" [--need runout|broken|event|deadline] [--by D]`:
- WANT_NOTED -> recorded; one plain line from its SAY. No offer.
- GO_FIND -> shopping-session (a need or a date was said).
- OFFER_COOLOFF / COOLOFF_PENDING (shopping management on) -> shopping-log
  "a want without a need".
An explicit search request ("find it", "find them all") always goes straight
to shopping-session's search flow.

## never

shopping facts go to shop-* verbs, never to memory; never ask a category,
a store or a price the owner did not give; never pitch the cooling-off flow
or the search while recording a purchase.

## ledger arrivals and pantry quantity (090)

When a card SMS, pay notification or captured order lands in the ledger, the
writer matches the user's open picked/in_cart captures within seven days.
The same merchant and a unique exact order total buy all matching captures;
there is no second spend and no "did you buy it?" question. `SHOP_PAYMENT_ASK`
is a partial/ambiguous order: relay its ASK once, then resolve using
`shop-payment-match SPEND_ID --captures ID,ID` after the owner's answer.
Do not force a partial sum or guess discounts/shipping. Merchant aliases can
be taught with `config-set shop_merchant_aliases='{"coupang":["\ucfe0\ud321"]}'`.
Refunds/negative payments never promote captures. A later "I bought it" (in the owner's language) about a
matched capture uses its existing id with `shop-capture-upd --status bought`;
that is a no-op, including its remaining stock.

Store actual count separately from pack size on captures and purchases:
`--quantity 6 --unit pack --unit-size 80 --size-unit g --size "80g"`.
`--price` is the full captured line total for those six packs. Do not invent
count from a pack size or an owned row. Purchases add a stock lot once.
`shop-bought` and `shop-owned-add` accept the same quantity options.

Before cooking/prep start, ingredient questions or shopping suggestions,
read `shop-stock-find "<ingredient>"` as well as the capture lookup. Say the
remaining stock first (for example, "you have 5 packs of prosciutto"); use those facts
before suggesting another purchase. Stock does not expire with old captures.
The cooking card reads live stock and shows the quantity remaining itself.
Completing a cooking step deducts each used recipe ingredient once per session;
starting a timer or a photo does not deduct it. When opening a sealed pack for
part of its contents, that pack counts as used. No stock exists -> no guessed
quantity or deduction. `STOCK_ASK` asks once when a known purchase lacks a count
or usable unit. Record the answer using `shop-stock-set OWNED_ID 6 --unit pack
--unit-size 80 --size-unit g`, then retry the original stock-use key.

A meal explicitly eaten from stock uses `meal-add ... --from-stock ITEM_ID
--stock-quantity 1 --stock-unit pack`. A plain meal log never assumes pantry
consumption, and a meal made by an already completed cooking session must not
charge its ingredients a second time. An owner saying an item is used up uses `shop-stock-empty ITEM_ID
--key <unique source message id>`. Other stated consumption uses
`shop-stock-use ITEM_ID QUANTITY --unit pack --key <unique source message id>`.
A repeated source key never deducts twice; shortages leave stock unchanged.
