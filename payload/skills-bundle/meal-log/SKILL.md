---
name: meal-log
display_name: 식사 기록
description: >-
  Capture food actually eaten from the owner's report, amount/meal context, barcode, nutrition label or recognized
  food/product photo, and retrieve logged foods by name/alias or a day's meal records. Capture works while meal
  management is off. Photo evidence must end in meal-add; use lookup -> meal-add -> the existing short confirmation/card
  and satiety contract. Also handle cooking, recipe, next-meal/menu and leftover-ingredient advice via
  reference/cooking.md, running day-now before replying. Suggesting food never logs it as eaten. Future planned eating
  goes to health pack diet-plan when installed, since logging here inflates eaten intake; status / targets / gaps ->
  health pack diet-status when installed. A bare request to begin meal/diet management without eaten-food content and
  module management off hands to brief-setup first-install.md ## later group C (C1), never a generic food/amount
  question. Completed setup is not a reason to restart it.
---

Before answering about food, cooking, timing or the rest of today, run
`"${PROJECT_ROOT:-$(pwd)}/database/kit_life.sh" day-now`, then read
reference/cooking.md for clock, meal-context and cooking routing.

Before record fields, only for start/use intent without actual record content: `database/kit_life.sh manage-state meal` (MANAGE<TAB>meal<TAB>on|off<TAB>reason). Off -> brief-setup first-install.md `## later` C1; on -> normal use. Actual records capture either way.

# meal-log

For eaten food, the kit's ONE shared diet-capture machine. Both life and health
agents may drive it; rows retain the resolved health owner. Owner states or
shows what he ate -> resolve nutrition -> write meal -> one short Korean
line. No analysis; card per "card after meal-add".

All DB via `$PROJECT_ROOT/database/kit_life.sh` or bundled `lookup.py` /
`mealstate.py`. No raw SQL. cwd = PROJECT_ROOT; never cd into the skill dir.
```
LOOKUP="python3 .claude/skills/meal-log/lookup.py"
LKIT="${PROJECT_ROOT:-$(pwd)}/database/kit_life.sh"
```
Doors, cache JSON, verb output shapes, API keys: read reference/lookup.md
before asking about food, on lookup misses, cache writes or verb-shape questions.
Branded miss (`web_lookup_required`): search online (maker page -> label image -> ask back-label photo); never derive macros from kcal; cache-add needs source + macro_origin=label|official|db.

## boundary

- diet card / calorie status / remaining / analysis / coaching / carry-over -> health pack `diet-status`.
  not-yet-eaten / planned ONLY if his words say future -> health pack `diet-plan`. Pack absent -> capture only; the kit ships neither.
  food + slot, no future words = eaten -> `meal-add` now. Never infer from clock; never ask eaten-vs-planned. Wrong guess -> fix on his correction.
- peer identity + lane-edge handoff: follow `database/crew-agents.md`
  "lane-edge rule" + "handoff lane status". log first, then hand off
  yourself; never time-claim the peer; always pair with numbers you hold;
  lane down -> give what you have, say which part is outside your lane, stop.
  never close with "<봇>한테 물어보세요".
- food cache (`meal_food` / `meal_alias` / `meal_portion`) = crew-wide shared.

Cancelled cooking finished by hand: read reference/cooking.md before logging.

## steps

0. `meal-search "<food name or owner alias>" --json` before asking about an
   already logged food; then `meal-day <date>` (+ requested slot). Same-day/slot
   fact already written by either agent -> no re-ask, no `meal-add`; go to step 5.
1. parse: food name, grams (ask if unclear; restaurant/delivery food: see
   1a, never grams first), slot 아침/점심/저녁/간식/운동
   (use an explicit slot; for current food advice use day-now; genuinely unresolved
   historical capture -> ask once). workout supplement (BCAA, creatine, pre/intra)
   -> `운동`; food snack -> `간식`.
1a. SOURCE FIRST (before any grams/variant question): brand product /
   restaurant-delivery / homemade. infer from (a) restaurant appointment at
   that meal in today's schedule, (b) past logs of the same food, (c) photo.
   inferred -> confirm in ONE line, no open question. not inferable -> ask
   source ONCE with the three choices. helper: `$LOOKUP source-plan '<json>'`
   (ASK_SOURCE / CONFIRM_SOURCE / ASK_PHOTO / READY). copy: relay
   reference/source-copy.json text exactly (keys source_ask, sources, confirm,
   photo_ask, photo_skip). details: reference/lookup.md "Source first".
   - photo came -> use for composition (type, toppings, sides) only.
   - no photo + restaurant/delivery -> ask for one with source-copy.json
     `photo_ask` + button `photo_skip`. skip -> record with the standard
     serving; never re-ask.
   - amount: standard serving first (reference/portions.json); the satiety
     loop corrects it later. never ask grams first for restaurant food.
2. resolve under the EXACT string you will pass to `meal-add` (convention
   includes amount: "백미밥 (210g)"):
   `$LOOKUP name "<name (Xg)>" <grams>` or `$LOOKUP barcode "<digits>" <grams>`.
   found -> use values. several candidates -> closest by name + maker;
   genuinely ambiguous -> ask.
   output carries `near_match` + `ask` (a cached food he may mean, e.g.
   "바닐라라테" vs 바닐라라떼) -> ask the `ask` line first. yes ->
   `$LKIT near-match-accept food "<near_match[0].ref>" "<name>"`, re-run the
   lookup (now a cache hit). no -> `$LKIT near-match-refuse food "<ref>"
   "<name>"`, go on.
3. miss, by source (1a): brand product -> label/web first (required).
   restaurant/delivery -> chain menu data if a chain, else standard
   restaurant serving from reference/portions.json
   (`$LOOKUP portion-lookup "<name>"`) as the starting amount; no grams
   question. homemade -> ask what went in. reliable result ->
   `cache-add`. details: reference/lookup.md section A.
   `cache-add` exit 5 (`near_match`) -> nothing cached; same question. no ->
   re-run with `"new_confirmed": true` in the JSON.
   satiety: only when `meal-add` printed `SATIETY_ASK` (meal management
   on): ask after the slot's LAST item via printed buttons. exact =
   optional (건너뛰기 ok); estimate = required before write. close with
   `meal-satiety`. details: reference/satiety.md. Nothing printed -> no
   satiety question.
4. write: `$LKIT meal-add "<date>" "<slot>" "<name (Xg)>" <carb> <protein> <fat> <fiber> <sugar> <alt_sugar> <grams> [alcohol]`
   owner gave an amount -> pass `--portion-basis` and `--portion-evidence`; see reference/satiety.md.
   composite same slot: 2nd+ row appends `--new`.
   run `meal-add` / `meal-upd` ALONE: never chained after another verb, never
   piped or truncated (no `| tail`, `| head`); card block + hooks need the
   whole output.
5. output has `PLANNED_SWAP_ASK` -> ask its printed `question`; answer -> the
   command `PLANNED_SWAP_RELAY` prints; then `meal-card <date>`. otherwise
   reply, one line, owner's language. first the SETTLE ON EVIDENCE check
   (schedule-log/reference/completion-chat.md): a task settled this turn ->
   its completion line only, nothing below. otherwise (step 0 hit: say so):
   - measured: "삼겹살 200g 기록했어요 (단백질 30g)"
   - estimate: "삼겹살 200g 기록했어요 (단백질 30g, 추정값)"
   - output had `AUTO-LINKED` -> one clause with the event title:
     "흑돼지 200g 기록했어요. '제주 여행'에 넣어뒀어요". never a question.
   - output had `SATIETY_REPLY` -> say it once, one clause, in the line.

## hard rules (verb enforces -> relay, do not argue)

- `meal-add` refuses a name with no live lookup receipt (043/DGN-928, 24h
  TTL; `foodcache-lookup` writes none). fix the NAME; `--no-lookup` only when
  the lookup genuinely cannot run.
- `meal-add` refuses an estimated meal with no satiety answer (048) while
  meal management is on. ask, store, retry. exact meals never refused; ask
  still shows. management off -> no refusal, no ask.
- kcal is DB-generated: pass macros only. alcohol items MUST fill `alcohol`.
- Product photo in shopping/cart/prep context: follow
  `.claude/skills-bundle/core-log/reference/shopping.md` (pick + status; pack
  facts before asking). Eaten = meal-input, MUST end in `meal-add`: label ->
  `cache-add` (`estimated:false`) -> `meal-add`. Serving unknown -> ask ONCE.
- brand page found but numbers are in an image -> read
  reference/brand-image-page.md (it owes a browser cleanup).
- auto-link is code's: nothing to run, never ask "묶을까요?".
- never narrate scaling / composite / `--new` / English working text
  (DGN-210, DGN-503). only the final confirmation is user-facing.
- correction = `meal-upd <id> field=value`, not delete + re-add.

## card after meal-add

`meal-add` output carries `MEAL_CARD_BEGIN..END` + `MEAL_CARD_RELAY`: relay the
block exactly as `MEAL_CARD_RELAY` says. output / hook context says `CARD_POSTED`
(a pack hook posted the card) -> agent writes only the one short Korean confirm
line, never re-sends or describes the card. never render a card or an image. no
block printed -> confirmation line IS the answer; never fabricate, narrate or
apologise for a missing card. a task settled this turn -> its completion
line is the answer; agent adds no card text.

Meal-record queries: read reference/lookup.md "Logged meal queries".

tier: BEST-EFFORT trigger (missed capture recoverable); write gates = code
(GUARANTEED). Haiku default; Sonnet for multi-item, image reads, odd resolution.
