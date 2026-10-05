---
name: meal-log
display_name: 식사 기록
description: >
  The kit's ONE diet-capture skill. Use when the user says or SHOWS what he
  already ate. Triggers: "밥 먹었어", "점심 기록해줘", "삼겹살 200g 먹었어",
  "견과류 간식으로 먹었어", any food name + amount + meal slot; a barcode
  number or a photo of a barcode; a photo of a nutrition label; a photo of a
  recognizable food or supplement product (e.g. a BCAA tub, a snack package
  front, a plated dish). A recognized product photo is a meal-input event and
  MUST end in a meal-add write, not just a spoken description. Resolves the
  food through lookup.py (shared cache -> public nutrition DB -> brand web ->
  LLM estimate), writes the meal into the shared meals table via kit_life.sh,
  and replies with ONE short Korean confirmation line.
  Also answers the PLAIN record query -- the list of what was logged for a
  day or a slot ("점심에 뭐 먹었다고 기록했지", "어제 기록한 식사 목록",
  "오늘 먹은 거 목록 보여줘", "list what I logged for lunch") -- as text from
  meal-day / agg-day. Not a status, not a card (see "query" below).
  Does NOT handle diet status / remaining calories / intake analysis /
  coaching / card rendering -- that is the health pack's diet-status lane and
  is HANDED OFF (see boundary), never turned into an errand for the user.
  Does NOT handle food the user has NOT eaten yet -- an utterance that frames
  the item as UPCOMING ("이따 먹을 거야", "저녁 예정으로 넣어줘",
  "미리 기록해줘 아직 안 먹었지만", "log this as planned",
  "add as upcoming meal") is the health pack's diet-plan lane. Logging it here would write a planned item as
  already eaten and inflate the day's confirmed intake.
  Output: short Korean text confirm only, no card.
---

# meal-log

Capture-only; the kit's ONE diet-capture skill (per-instance copy,
`sharing_mode: own`; the food cache is what is shared). Owner states or
shows what he ate -> resolve nutrition -> write meal -> one short Korean
line. No card, no analysis.

All DB via `$PROJECT_ROOT/database/kit_life.sh` or bundled `lookup.py` /
`mealstate.py`. No raw SQL. cwd = PROJECT_ROOT; never cd into the skill dir.
```
LOOKUP="python3 .claude/skills/meal-log/lookup.py"
LKIT="${PROJECT_ROOT:-$(pwd)}/database/kit_life.sh"
```
Doors, cache JSON, verb output shapes, API keys: read reference/lookup.md
when a lookup misses, when you write the cache, or when you need a verb shape.

## boundary

- diet card / calorie status / remaining / analysis / coaching / carry-over
  -> `diet-status` (health pack). not-yet-eaten / planned -> `diet-plan`
  (health pack). both absent -> capture still works, say nothing about them.
- peer identity + lane-edge handoff: follow `database/crew-agents.md`
  "lane-edge rule" + "handoff lane status". log first, then hand off
  yourself; never time-claim the peer; always pair with numbers you hold;
  lane down -> give what you have, say which part is outside your lane, stop.
  never close with "<봇>한테 물어보세요".
- food cache (`meal_food` / `meal_alias` / `meal_portion`) = crew-wide shared.

## steps

1. parse: food name, grams (ask if unclear), slot 아침/점심/저녁/간식/운동
   (ambiguous -> ask first). workout supplement (BCAA, creatine, pre/intra)
   -> `운동`; food snack -> `간식`.
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
3. miss: grams unknown -> `$LOOKUP portion-lookup "<name>"`, else ask.
   brand -> web first (required); no brand -> estimate. reliable result ->
   `cache-add`. details: reference/lookup.md section A.
   `cache-add` exit 5 (`near_match`) -> nothing cached; same question. no ->
   re-run with `"new_confirmed": true` in the JSON.
   estimate branch -> satiety answer required before write: read
   reference/satiety.md.
4. write: `$LKIT meal-add "<date>" "<slot>" "<name (Xg)>" <carb> <protein> <fat> <fiber> <sugar> <alt_sugar> <grams> [alcohol]`
   composite same slot: 2nd+ row appends `--new`.
5. reply, one line, owner's language:
   - measured: "삼겹살 200g 기록했어요 (단백질 30g)"
   - estimate: "삼겹살 200g 기록했어요 (단백질 30g, 추정값)"
   - output had `AUTO-LINKED` -> one clause with the event title:
     "흑돼지 200g 기록했어요. '제주 여행'에 넣어뒀어요". never a question.

## hard rules (verb enforces -> relay, do not argue)

- `meal-add` refuses a name with no live lookup receipt (043/DGN-928, 24h
  TTL; `foodcache-lookup` writes none). fix the NAME; `--no-lookup` only when
  the lookup genuinely cannot run.
- `meal-add` refuses an estimated meal with no satiety answer (048).
  ask, store, retry. exact matches never gated.
- kcal is DB-generated: pass macros only. alcohol items MUST fill `alcohol`.
- recognized food/supplement PRODUCT photo = meal-input: MUST end in
  `meal-add`. describing it back without a write = dropped write. label
  photo -> read values -> `cache-add` (`estimated:false`) -> `meal-add`.
  serving unknown -> ask ONCE (label serving if visible).
- brand page found but numbers are in an image -> read
  reference/brand-image-page.md (it owes a browser cleanup).
- auto-link is code's: nothing to run, never ask "묶을까요?".
- never narrate scaling / composite / `--new` / English working text
  (DGN-210, DGN-503). only the final confirmation is user-facing.
- correction = `meal-upd <id> field=value`, not delete + re-add.

## card order without a card

`meal-add` success fires framework `card-followup` hook ("render + send the
status card"), keyed on the CLI. `.claude/skills/diet-status/card.py` or
`.claude/skills-bundle/diet-status/card.py` exists -> follow it this turn.
neither -> confirmation line IS the answer; never fabricate, narrate or
apologise for a missing card.

## query (plain list, 2.27.0)

"뭘 기록했지" = read own rows:
```
$LKIT meal-day "<YYYY-MM-DD>"   # [id] 끼니 · 음식 (kcal 단 탄 지)
$LKIT agg-day  "<YYYY-MM-DD>"   # meal_cnt intake_kcal protein_g carb_g fat_g + finality
```
- one short line per item ("점심: 김치찌개 1인분, 공깃밥"); total kcal only when asked.
- read live; never re-quote old numbers.
- past day `closed=0` -> append `(잠정, HH:MM 기준)` (HH:MM = `as_of`).
  `meal_cnt=0` = NO RECORDS, never "you ate nothing".
- targets / remaining / judgement -> `diet-status`; absent -> say the kit
  keeps records and does not compute targets, stop.

## tier / routing

BEST-EFFORT trigger (missed capture is recoverable). write gates above are
code (GUARANTEED). Haiku default; Sonnet for multi-item disambiguation,
image reads, unusual resolution.
