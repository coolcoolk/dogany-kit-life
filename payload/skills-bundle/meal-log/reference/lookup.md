# meal-log -- lookup doors, cache, verbs

## Captured meal time (owner-approved 2026-10-29; overrides older card-first copy)

Stay silent during tool work and send one final reply. The owner-approved
meal continuation also takes precedence over generic completion-only reply
rules for meal capture; settle matching tasks silently and relay this output.
For every chat meal,
first `experience-capture "<owner words verbatim>" --title "<meal> 식사"
--on <day> [--at <explicit ISO time with timezone>]`, then pass its event id
with `meal-add ... --experience <id>` for every food. An explicit clock or
“방금”/“지금” in those owner words supplies the time; never infer a clock from
breakfast/lunch/dinner or from the record timestamp. Bare numeric meal-add
without a capture remains the compatibility ingestion API, not the chat door.

Unknown time: relay the printed `RESOLVED` line (“<items>으로 <meal>을/를
기록했어요.”) and `MEAL_TIME_ASK` question in the SAME reply:
“지금 드시나요, 아니면 언제쯤 드셨나요?” with `[[OPTIONS: 지금 | 직접 입력]]`.
No card yet. After the last item, combine its printed foods and ask only once.
Run the printed `ASK_OPTIONS` answer command. 지금 maps to `now`; 직접 입력
maps to `manual`. Manual keeps the SAME approved question open without buttons;
when the owner types “8시”, pass that exact text to its continuation. A directly
typed clock on the original question is also accepted. Never add another prompt.

The time answer delivers the existing meal card verbatim inside ONE code
block (```), followed by the fullness question and its existing seven buttons
in the SAME message through the kit's strict carrier. `MEAL_TIME_CARD_SENT`
means it was already delivered: reply exactly `NO_PUSH`, with no duplicate.
If delivery is unavailable or fails, follow `MEAL_TIME_CARD_RELAY`: wrap the
printed card between MEAL_CARD_BEGIN/END in one code block, preserving all
spaces and lines, then its printed SATIETY_ASK and options last.
지금/방금 stores basis=current and immediately asks
“<meal> 다 드시고 포만감은 어땠는지 천천히 알려 주세요”. There is no new 30-minute
reminder; an unanswered question remains answerable later through the same
continuation. A stated past clock stores basis=recalled and keeps the question
“<meal> 다 드셨을 때 포만감은 어땠어요?”. Answer using the printed SATIETY_ASK
continuation, retaining its occasion; strict-carrier button
`opt:meal-fullness:<event id>:<token>` runs
`meal-fullness <event id> <token>`. Never re-log the meal.
Management off still asks for the occurrence clock and shows the card, but
prints/schedules no fullness question. Supplements/zero-kcal side drinks and
planned-swap continuations retain their existing behavior.

This exception overrides older meal-log steps that ask for a slot, source,
photo or satiety before writing. A named food plus an eating statement is
already a meal. Use the owner-named slot; when absent use the message time
in the owner timezone: 05:00-10:59 breakfast, 11:00-16:59 lunch,
17:00-22:59 dinner, otherwise snack. Pass the corresponding localized slot
to meal-add. Use an explicit occurrence day, else the message day. This
meal classification does not assert an occurrence clock or create a calendar
block; the captured experience may retain an unknown occurrence time.

Run meal-search and meal-day before writing. Same food already stored stays
with existing duplicate/correction handling; a different food in the same
slot is a new meal item, not a duplicate. Known product/recipe/stock facts
win. Missing source or photo stays unknown: do not start source-plan's
interview. Resolve nutrition with lookup.py, retaining estimate provenance;
use portion-lookup's standard serving when no amount is stated. A cache
miss with no brand uses the documented estimate path, not a source question.
Do not claim the owner ate at a restaurant merely because a standard bowl
is the provisional portion basis.

Pass --experience <captured event_id> to meal-add. This linked eaten-meal
path records an estimate provisionally before the time and satiety answers.
The printed time continuation owns the first reply when the clock is unknown. Do not pass
--no-satiety, forge an exact lookup grade, invent an answer, suppress the
card or initiate onboarding. Relay MEAL_TIME_RELAY first when printed; after its answer relay the actual
MEAL_CARD and any printed SATIETY_RELAY. Never front-run the timed question. Management off,
planned swaps, lookup receipts and duplicate/correction rules still apply.

Capture-first for a narrated lived action: save `experience-capture
"<owner statement, verbatim>" --title "<activity>" [--on <stated day>]
[--at <stated ISO time with timezone>]` before asking about portions, amounts,
payment or sources. Unknown occurrence time stays unknown. Keep its event id
for enrichment; retry the same day/title without creating a task. Pass `--experience <event_id>` on the later meal-add. Successful
specialized writes silently attach the experience; retain their existing reply
shape, with no new menus or questions from the experience layer.

Read when a lookup misses, when writing to the cache (label, web result,
estimate), or when you need a verb's output shape.

## Search before asking about logged food

Before asking which food the owner calls usual, run `$LKIT meal-search
"<food fragment or owner alias>" --json`. This searches eaten history, shared
food-cache names and the SAME meal_alias used by foodcache-alias / food-merge.
`버거` finds the logged burger; `그 햄버거` finds the owner's registered alias.
Use latest date + slot and `count` (eaten occurrences per canonical name).
Ask only if the results leave a real ambiguity or there is no matching record.
`meal-find` accepts one valid YYYY-MM-DD only; a name is a usage error.
TSV meal-search columns: date, slot, name, kcal, carb, protein, fat, fiber,
sugar, alcohol, grams, count, id, source, planned. One row per item, ranked by
`count` then last date (`date`); a lone or dominant top row is the referent:
use it and say so. When logging from the owner's phrase, pass `--ref "<phrase>"`
to meal-add so it is saved as an alias of an existing item (`ALIAS_SAVED`);
never for a new item. Cache-only matches follow with empty date/slot/id and
count zero.
Not-yet-eaten food, targets and gaps belong to the health pack's planning
and status skills when installed. The kit only captures meals and searches food.

## Meals explicitly eaten from pantry stock

If the owner says the food came from their stock, read `$LKIT shop-stock-find
"<ingredient>"` before asking for its pack facts. State the remaining count
before suggesting another purchase. Pass the identified item and the actual
used quantity to the successful meal write: `meal-add ... --from-stock ITEM_ID
--stock-quantity 1 --stock-unit pack`. The meal id deduplicates consumption.
Do not infer that a restaurant/delivery meal came from stock. A meal made by a
completed cooking session has already charged its ingredients; do not charge
them again. An unknown count emits `STOCK_ASK` once; follow the shopping
adapter at `.claude/skills-bundle/core-log/reference/shopping.md`.

## Source first (owner 2026-10-18; overrides the old grams-first miss step)

Before asking about grams or food variants, infer the source from the meal's
schedule/restaurant appointment, same-food history, and photo. Only meal-relevant
schedule rows count (same date and meal/time); an unrelated appointment is not
evidence. An ordinary plated photo does not distinguish restaurant from home.
Conflicting evidence needs one confirmation; do not silently pick a source.

`$LOOKUP source-plan '<json>'` selects the next question without writes. JSON:
`food`, `meal`, `date`, optional explicit `source` (restaurant/product/homemade),
`schedule`/`history` arrays (`food`, `source_kind`, plus date/meal for schedule),
`photo` (observed package/barcode/nutrition_label/restaurant_menu/ingredients),
`source_confirmed`, `skip_photo`, `portion` (stated or calibrated), `lang`.
Read schedule/history through kit verbs; pass the matching evidence, not a guess.

- `ASK_SOURCE`: ask `question`, buttons [식당/배달] [포장 제품] [집에서 만듦].
  Exactly one source question; no grams or water/spicy branch at the same time.
- `CONFIRM_SOURCE`: confirm only the inferred source, never ask all three again.
  On yes retain `source` in the next call; on no ask source once.
- `ASK_PHOTO`: relay `question` EXACTLY, with [사진 없이 기록]. For the owner's
  dinner/noodle example: "저녁 냉면이요. 사진 한 장 보내 주시면 고명까지 보고 더 정확하게 기록할게요."
  No preface or paraphrase. The button sets `skip_photo=true`; continue recording
  in the same flow. A photo already sent is used immediately, never requested again.
- `READY`: resolve nutrition and write. Photo tells WHAT (food, toppings, sides);
  satiety tells HOW MUCH. Never estimate bowl size or grams from the image.

Source resolution: product -> label/barcode/brand values, exact serving on the
label; restaurant/delivery -> named chain menu/public nutrition serving (local
shop estimated); homemade -> ingredients/recipe. Retain source in the cache key
when it changes nutrition. Do not reuse a restaurant estimate for a product.
No photo/history/stated amount: start at ONE standard restaurant bowl/plate,
using the public menu's 1-serving basis, then calibrate with the existing satiety
loop. Do not ask for grams to start. `portion-lookup` returns bowl/plate units;
`reference/portions.json` documents the generic defaults and their provenance.
Public bowl mass and dry package mass are different bases: resolve matching
nutrition before scaling. Explicit pieces/grams and calibrated history win.
The ramen cooked-bowl mass is a provisional estimate, never an exact label value.

Before the final reply, read
`.claude/skills-bundle/schedule-log/reference/completion-chat.md` and apply its
common SETTLE ON EVIDENCE check. A successful meal write is followed by that
check. A PLANNED item settled this turn gets the completion line (overrides
capture/card followups). No open task/routine match: no done block; a plain
meal log, including a narrated eaten-food event, keeps its own meal-card
and satiety reply. Silently link the meal to the captured experience.
Keep tool work silent; do not start an unrequested onboarding or consult.

```
LOOKUP="python3 .claude/skills/meal-log/lookup.py"   # cwd = PROJECT_ROOT
LKIT="$PROJECT_ROOT/database/kit_life.sh"
```

## A. by name

`$LOOKUP name "<food_name exactly as recorded>" <grams>`

Order (lookup.py does 1-2 itself): 1) shared cache (`meal_food`, crew-wide,
0 tokens) -> 2) public DB -> both miss, branch on brand:
- brand present (franchise / maker) -> web search REQUIRED; official or 2+
  agreeing sources = trusted -> `cache-add` `estimated:false`. web fails ->
  LLM estimate `estimated:true`.
- no brand (백미밥, 삼겹살, 사과) -> skip web -> LLM estimate; reliable ->
  cache `estimated:true`.
Brand page found but text has no numbers -> read reference/brand-image-page.md.

## B. by barcode

`$LOOKUP barcode "<digits>" <grams>`. photo of a barcode: read digits, same command.

## C. cache-add

On a narrated meal, choosing a standard serving or writing an estimate is
silent tool work. Do not announce that you are logging, estimating or about
to save. The meal exception allows the final card and satiety question only;
it does not allow interim progress narration.

`$LOOKUP cache-add '<json>'` -- values = absolute amounts for 1 serving at `grams`:

```json
{
  "key_name": "제품명 (표시옵션)",
  "name": "표시용 원래 이름",
  "grams": 347,
  "kcal": 553,
  "carbs": 40, "protein": 18.7, "fat": 18.7,
  "fiber": 5, "sugar": 5, "sodium_mg": 430,
  "source": "출처",
  "estimated": true
}
```

- `key_name` raw; lookup.py normalizes (same function as lookup).
- `grams` = serving basis; lookup scales. `carbs` accepted (DB column `carb`).
- `updated` stamped by DB. same normalized key -> upsert.
- nutrition label photo: read values -> `cache-add` (`estimated:false`,
  `key_name` = product, `grams` = label serving) -> `meal-add`.
- `database/food_cache.json` holds ONLY per-instance `meal_times` /
  `fullness_anchors` / `settings`. never hand-write food entries there.

## alias / portion (shared tables)

```
$LOOKUP alias-add "<alias>" "<canonical>"     # meal_alias; exact name wins over alias
$LOOKUP portion-lookup "<name>"
$LOOKUP portion-add "<name>" <grams> [n]      # e.g. 김밥 -> 110g
```

## kit verbs (inspect only -- write NO lookup receipt)

```
$LKIT foodcache-lookup "<name>" [grams]
      HIT: name grams kcal carb protein fat fiber sugar sodium_mg estimated source
      MISS: NOTFOUND<TAB><key>, exit 3
$LKIT foodcache-upsert '<json>'
$LKIT foodcache-alias "<alias>" "<canonical>"
$LKIT portion-lookup "<name>"     HIT: key grams n ; MISS: NOTFOUND, exit 3
$LKIT portion-upsert "<name>" <grams> [n]
```

## meal-add / meal-upd

```
$LKIT meal-add "<YYYY-MM-DD>" "<아침|점심|저녁|간식|운동>" "<name (Xg)>" \
  <carb> <protein> <fat> <fiber> <sugar> <alt_sugar> <grams> [alcohol]
      -> id<TAB>name<TAB>kcal              (always stdout line 1)
         [AUTO-LINKED<TAB>event_id<TAB>title]
$LKIT meal-upd <id> field=value ...
      fields: date meal name carb protein fat fiber sugar alt_sugar grams alcohol
$LKIT meal-find <date>   /   meal-day <date>   /   agg-day <date>
```

- macros in grams; unknown = 0; grams unknown -> omit last positional.
- kcal DB-generated (protein×4 + fat×9 + (carb−fiber)×4 + alcohol×7). never pass kcal.
- alcohol = pure grams = ml × ABV × 0.789. carb stays actual carb (no folding).
- composite (same date + slot): row 2+ append `--new` (reconcile guard exits 3 otherwise).
- escapes: `--no-lookup` (043, only when lookup cannot run), `--no-satiety`
  (048). never to silence a name mismatch.
- owning_agent stamped by facade; area auto-links to "식습관".
- correct in place with `meal-upd`; do not delete + re-add.

## API keys (optional)

lookup.py reads `FOODSAFETY_KEY` (식품안전나라 C005 barcode) and `DATAGOV_KEY`
(공공데이터포털) from `DIET_ENV_FILE` -> `$PROJECT_ROOT/.env` ->
`.telegram_bot/.env` -> `runtime/.env` -> process env. absent -> public-DB
door returns `{"found": false}` silently; cache + estimate continue.

## Caffeine (same source-first ladder)

Coffee, tea, energy drinks and pre-workout products need caffeine as well as
macros. `name` lookup returns caffeine fields on a complete cache hit, or a
`caffeine` next-step object. A macros-only cache hit is not a caffeine hit.
For a brand/product, search its official menu/label for the exact drink,
country, size, preparation and shots before estimating. Do not apply the
generic table to a branded name. Cache the published serving amount and URL:

```json
{"key_name":"<brand + drink + size>","grams":355,"carbs":0,
 "caffeine_mg":150,"caffeine_origin":"published",
 "caffeine_source":"<official menu URL and serving>","source":"<nutrition source>"}
```

The number above illustrates the payload format; resolve the actual product.
`caffeine_mg` is absolute mg per cached serving, independent of whether the
macros were estimated. Origins: `published`, `label`, `estimated`. Include the
caffeine source even if it differs from the macros' source. A label's explicit
zero is valid; missing caffeine stays unknown, never an invented zero.

No brand: use `caffeine`'s sourced inference fields, or cache the generic drink
with `volume_ml` / `shots`. The kit estimates only complete generic names
(드립커피, 아메리카노, 에스프레소, 콜드브루, 녹차, 홍차). Drink volume in mL
wins; black drink serving grams approximate mL. Defaults when unstated: 237mL
for brewed drinks, two espresso shots for an americano, one for espresso.
Explicit `3샷` or `355ml` in the name wins over defaults. An americano's water
volume does not increase its shots. Cold brew uses an undiluted research proxy;
for a stated dilution, estimate from the actual coffee portion. Save the basis
in `caffeine_source`, origin `estimated`; the shared card adds `[추정]`.
If a brand lookup fails, resolve its generic type/size/shots, then cache that
inference under the full branded identity with origin `estimated`. Never mark
a category estimate as published. Energy/pre-workout formulas have no universal
inference default: resolve their label; otherwise leave caffeine unknown.

`meal-add` automatically reads cached caffeine (including aliases and terminal
`(Xg)` amounts); no extra positional macro is needed. Fractional servings scale
the cached amount. Keep each brand size/shot recipe as its own cache identity;
scaling means a fraction of the same preparation. `foodcache-lookup` preserves
its existing 11-column TSV; lookup.py JSON includes the caffeine provenance.
Both the life agent and the health agent relay the same card from the kit. Only eaten rows count.

Caffeine advice uses kit copy keys `meal.caffeine.*`: never recommend starting
coffee or increasing caffeine for a workout. Advice is emitted only for an
explicit habitual coffee drinker (`habitual_coffee_drinker` config); afternoon
sleepiness suggests a nap before more caffeine. Active pregnancy, heart disease
or medication config/constraints suppress numeric caffeine in the card and use
the medical consultation copy. These conditions never receive a numeric intake
recommendation. The ordinary adult bar is a reference, not a target to reach:
https://www.fda.gov/consumers/consumer-updates/spilling-beans-how-much-caffeine-too-much

Remove a wrong serving definition with `$LKIT portion-del "<name>"`.
This uses the same name/alias resolution as portion-lookup, and removes
only the serving registration. Food and prior meal records remain.


## Brand web-first (2026-11-01, meal-brand-web-first)

Look up published nutrition online first. Never back-calculate macros
from kcal. On a branded name miss (call `MEAL_LOOKUP_BRAND=<maker> lookup.py name ...`),
`found:false` carries `web_lookup_required` (code `WEB_LOOKUP_REQUIRED`). Work its
`steps` in order and stop at the first that yields numbers:

1. maker page (official nutrition table)
2. label image on the maker or a retailer page
3. ask the owner for a back-label photo (existing meal photo ask, no new copy)

`cache-add` for such a food must carry `source` (URL / label description) and
`macro_origin` = `label` | `official` | `db`. A payload with `derived_from_kcal`,
or `macro_origin` of derived/estimate/back_calc, is refused (exit 6, nothing
written). `label` and `official` are package class, weight 1.0; `db` values are
estimates. Payloads that declare none of these fields behave exactly as before;
cache hits are unchanged byte for byte.

## Logged meal queries (plain list, 2.27.0)

"뭘 기록했지" = read own rows:
```
$LKIT meal-day "<YYYY-MM-DD>"   # [id] 끼니 · 음식 (kcal 단 탄 지)
$LKIT agg-day  "<YYYY-MM-DD>"   # meal_cnt intake_kcal protein_g carb_g fat_g + finality
```
- one short line per item ("점심: 김치찌개 1인분, 공깃밥"); total kcal only when asked.
- read live; never re-quote old numbers.
- past day `closed=0` -> append `(잠정, HH:MM 기준)` (HH:MM = `as_of`).
  `meal_cnt=0` = NO RECORDS, never "you ate nothing".
- targets / remaining / judgement -> health pack `diet-status` when installed;
  compare every stored target and gap. A below-target value is never enough/충분.
