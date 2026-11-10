---
name: shopping-session
display_name: 상품 찾기
description: >-
  Find and compare products, gifts, ingredients, purchase sources or packaging when the owner asks for choices, or
  continues, narrows, selects, resumes or stops an active search. Use the current hunt, recipient/occasion and stated
  constraints to interpret follow-ups without requiring the owner to repeat their wording or recipient. A bare request
  to begin shopping management while off hands to core-log ## later setup before any search or purchase question. Open
  with 3-4 image-verified cards in different style directions; use picks/rejections to narrow the next round, check
  several shops and report search coverage/gaps. Runs in the main session (browser). Writes only through kit_life.sh
  shop-* verbs; final output is the verb's round card block (images, price, arrival, coverage line, one question). Never
  pays or checks out.
---

Before answering about food, cooking, timing or the rest of today, run
`"${PROJECT_ROOT:-$(pwd)}/database/kit_life.sh" day-now` and use its local clock,
day type, logged meals, next eating occasion and remaining blocks. Never ask
which meal when day-now answers it; an afternoon food request after logged
lunch targets dinner unless the owner explicitly says otherwise. Never infer
attendees from a title; use only stored people/groups. Historical or explicitly
dated requests keep their stated date and meal; day-now supplies current context.


For start/use intent with no record/search/place content: `database/kit_life.sh manage-state shopping` (MANAGE<TAB>shopping<TAB>on|off<TAB>reason). Off -> core-log `## later setup`; on -> normal use/readback before questions.

# shopping-session -- find a thing to buy (module_shopping P1)

Owner asks for a thing -> rounds of 3-4 verified cards -> pick -> close.
every write = `$LKIT shop-*`, `LKIT="${PROJECT_ROOT:-$(pwd)}/database/kit_life.sh"`.
the hunt is a ledger: state lives in rows, not in memory. Tier: card
verification, round size, distinct cards, coverage line, budget overrun,
re-aim = GUARANTEED (shop-round-add / shop-hunt-close refuse); trigger =
BEST-EFFORT.

## Context before person or gift answers

First run `kit_life.sh noun-context "<current message + known recipient/occasion from this conversation>"` using `${PROJECT_ROOT:-$(pwd)}/database/kit_life.sh` from the agent workspace. Read PERSON / PERSON_REL / PERSON_EVENT and EVENT before answering or asking who/where/when; no EVENT does not mean no person facts.
An unresolved nickname: `person-resolve`, then repeat noun-context with `--span "<resolved canonical name>"`; NEAR / AMBIGUOUS is not a resolved person. For missing details use `person-show <id>`.
Kit person facts override agent memory. A memory fact phrased as a future expectation whose date has passed must not be asserted: check the kit, or ask once if still unknown. A recorded child birth is past, never an upcoming delivery; do not infer medical/recovery facts.
Product "찾아줘" / "다 찾아줘", gift purchase sources, and packaging searches go straight to shopping-session's search flow and concrete product cards, including follow-ups without a repeated recipient. Never answer those with generic advice or an offer to search later.

## where it runs

- main session only: browser work (agent-browser) does not exist in a
  detached run. detached -> do not start; say it runs in the conversation.
- NEVER pay, check out, enter card data, add to cart for purchase, or
  confirm an order. stop at the product page; the owner pays.

## owner-supplied products and picks

For a screenshot/link product the owner chooses, or a pick/cart follow-up,
read `.claude/skills-bundle/core-log/reference/shopping.md` and capture each
chosen item in this turn. Reuse an existing hunt/round/item when present.
Owner-supplied choices do not require a fabricated search round or extra
alternatives. Record the owner's cart status; the owner still checks out.

## flow

1. After noun-context, `shop-hunt-cursor` every turn about shopping. Follow
   its EXPECT; read `person-show <recipient id>` when a resumed hunt supplies
   the recipient. Reuse that recipient on follow-ups, never guess from memory.
2. open: `shop-hunt-open <category> --ask "<owner words, verbatim>"
   [--for <person>] [--budget N] [--by YYYY-MM-DD]`.
   - category: token or label from `shop-cat-list`. CAT_UNKNOWN -> choose
     from the printed list, or `shop-cat-add <ascii_token> --display
     "<owner word>"` then reopen. NEAR_MATCH -> send its ASK line, then
     near-match-accept / --new-confirmed. The number after NEAR_MATCH is the candidate COUNT, never an id; the id is the first column of each candidate row below it.
   - budget / date ONLY when the owner said them. never ask up front.
   - RESUMED -> continue from the cursor. EXPIRED -> send the ASK line;
     yes -> `shop-hunt-upd <id> --reopen`; no -> reopen with `--new`.
   - gift (`--for`) -> that person's facts only, never the owner's body.
3. search (browser), including each requested ingredient and packaging item.
   A shopping list with several categories gets a hunt/round per category;
   include concrete product URLs, verified prices and images. A search snippet
   alone is not verification: open product pages. Record blocked shops and
   real shortfalls with `--partial`; continue other sources before stopping.
   Sources = the cursor's SOURCES line (owner's list or
   the default). open each candidate's product page and LOOK at
   its image. fill `checks` from what you saw:
   - wearer: made for the person it is for (gender / target).
   - adult: adult size class, content safe.
   - form: the true product form the owner asked for (a jumpsuit is not a
     bib overall, not a racing suit).
   - colour: matches the ask (true when the ask names none).
   any doubt -> false. a false or missing check = card refused: replace
   the card, never argue with the gate.
   - round 1 = scatter: 3-4 clearly different style directions (contrasting
     archetypes). no taste questions before it. the cursor's TASTE rows
     (owner-stated like / avoid) and OWNED_RECENT rows (last bought: brand,
     size, fit, liked?) shape it; a liked past item may be one slot. a card
     of an avoided brand is refused (AVOID_HIT).
   - next rounds: never reuse DROPPED_DIRECTION / REJECTED_ITEM; keep the
     picked direction, vary the part the owner disliked; a `closest` card =
     search near it.
4. `shop-round-add <hunt> --seen <shops actually searched> [--blocked
   <shops tried, not reachable>] [--assume "<basis>"] --card '<json>' x3-4`.
   card json: name, brand, variant, url, image, direction (short label,
   owner language), source, price (number), ship_by (YYYY-MM-DD; required
   when the hunt has a date), checks {...}, reasons [<= 3 short], reviews
   (count, if shown). pass cards best first; the engine sets slot order.
   `--assume` when wearer / size basis is not on record (shown in the
   coverage line, owner can correct). fewer than 3 verified after a real
   search -> `--partial "<why>"` with what passed; never pad.
5. send the block the verb printed, nothing else as candidates:
   image per card (or one collage, slot-numbered), then per card: slot,
   direction, name, price, arrival, flag labels; SAY_COVERAGE line as is;
   SAY_SHORTFALL if printed; the ASK line.
6. answer -> `shop-verdict <hunt> ... --verbatim "<owner words>"`:

   | owner says | verb |
   |---|---|
   | "2번" | `--pick 2` |
   | "2번 색은 좋은데 핏이 별로" | `--pick 2` (next round keeps colour, varies fit) |
   | "1이랑 3" | `--pick 1,3`; compare the two on the same 1-2 axes |
   | "음 글쎄" / "생각해볼게" | no verdict; next round narrower (3 cards, more alike) |
   | "다 별로" | `--reject-all`; send its ASK once; reply "2번이 그나마" -> `--closest 2` |
   | rejects the thing itself (brand, that product) | `--reject <slot> --scope item` |
   | "이 느낌 아님" | `--reject <slot>` (this hunt only) |
   | "아무거나" / "골라줘" | offer ONE card + one-line reason, rest collapsed; yes -> `--default <slot>` |
   | answer + question | record the verdict first, then answer from the product page |
   | slot that does not exist | BAD_SLOT lists valid slots; re-ask once |

7. after a pick, on that card only: size -> `shop-size-draft --hunt <id>
   --chart '<json>'` (SIZE_NEED -> that one number, as a body fact);
   2-4 star review text on the owner's axes, sponsored /
   advertising / provided-marked reviews excluded; return policy.
8. EXPECT budget_decision -> send the ASK the verdict printed.
   go with it -> `shop-hunt-upd <id> --budget <price>`; within budget ->
   `shop-hunt-upd <id> --budget-hard 1`, next round; new number ->
   `--budget N`. SHORTFALL -> one decision, this order: raise budget /
   drop a must-have (name what changes) / stop.
9. close: owner buys or says done -> `shop-hunt-close <id> --picked
   <shown_id>` (refuses an undecided overrun). stop -> `--abandon`.
   later -> leave open (expires after 14 idle days); promise no reminder.
10. EXPECT reaim (4 rounds, no pick) -> send its ASK. narrow ->
    next `shop-round-add ... --reaimed`; stop -> `--abandon`.

## hard rules

1. "find it" = search to the end. no permission asks ("shall I look?").
   questions allowed: category truly unclear (max 1 before round 1),
   budget overrun, size when no body fact. nothing else.
2. no taste questionnaire. a card not picked is not a dislike.
3. a card reaches the owner only through the verb's render.
4. coverage always: name where you looked and where you could not. one
   shop only -> say so. owner's logged-in Chrome for a blocked shop: ask
   once per session (it shows the shop who they are).
5. seller urgency ("today only") = the seller's claim, never your advice.
   no product-efficacy coaching.
6. shopping facts go to shop-* verbs, never to memory. body facts heard
   here -> body homes (`config-set height_cm`, `log-metric`), not shopping.
7. user language only; never verb names, flags, codes, tokens, ids.

## verbs

```
shop-hunt-open <cat> --ask "<w>" [--for p] [--budget N] [--by D] [--new]
shop-hunt-cursor [<id>]          EXPECT round|verdict|direction|budget_decision|close|reaim|none
shop-hunt-list [--all|--state s] / shop-hunt-upd <id> [--ask] [--budget N|none]
      [--by D|none] [--for p|self] [--budget-hard 0|1] [--reopen]
shop-hunt-close <id> --picked <shown_id> | --abandon     shop-hunt-del <id>
shop-round-add <hunt> --seen .. [--blocked ..] [--assume ..] [--partial ..]
      [--reaimed] --card '<json>' x3-4
shop-round-show <hunt> [<n>] / shop-round-upd <round_id> [--seen] [--blocked] [--assume]
shop-verdict <hunt> --pick s[,s] | --reject s[,s] [--scope round|item] |
      --reject-all | --default s | --closest s   [--verbatim ".."] [--round n]
shop-shown-list <hunt> / shop-shown-void <shown_id>
shop-cat-list [--all] / shop-cat-add <token> [--display ..] [--consumable 0|1]
      [--checks a,b] / shop-cat-upd / shop-cat-retire / shop-cat-del
shop-item-upsert --category c --name .. [--brand] [--variant] [--url] [--image]
shop-item-find "<w>" / shop-item-upd <id> [--alias ..] / shop-item-merge <a> <b> / shop-item-del <id>
```
exit 3 = already there (EXISTS / EXPIRED), 4 = REFUSED (nothing written,
fix and rerun), 5 = NEAR_MATCH (send its ASK).

## siblings

- body facts -> `body-brief`; people (`--for`) -> person-manage.
- "bought it" -> shopping-log (`shop-bought` fills from the picked card and
  closes the hunt). sizes for a picked card -> `shop-size-draft` there.
- an explicit product search ("찾아줘" / "다 찾아줘") goes directly to this
  flow; no cooling-off detour or permission question.
- a want with no search request or need (nothing ran out / broke / no date) ->
  `shop-want` via the core-log shopping adapter: recorded quietly while
  shopping management is off, shopping-log's cooling-off offer while it is
  on. wishlist, rebuy: not built yet (module_shopping P3).
