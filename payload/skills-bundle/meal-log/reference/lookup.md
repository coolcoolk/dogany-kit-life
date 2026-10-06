# meal-log -- lookup doors, cache, verbs

Read when a lookup misses, when writing to the cache (label, web result,
estimate), or when you need a verb's output shape.

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
      -> [AUTO-LINKED<TAB>event_id<TAB>title]
         id<TAB>name<TAB>kcal
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
