# meal-log -- brand page whose nutrition table is an image

Read when WebSearch/WebFetch lands on the right product page but the fetched
text has no 영양/칼로리/단백질 numbers. Korean detail pages bake the
상품정보고시 table into a tall image; WebFetch reads text only.

One more tier before LLM estimate. ONE `agent-browser` session: opened at
step 1, closed at step 7. entering = you owe step 7 (success, failure,
abandonment).

1. OPEN the page with the `agent-browser` skill (not WebFetch).
2. ENUMERATE `<img>`: `agent-browser eval "Array.from(document.querySelectorAll('img'))..."`
   (or snapshot). may need to click "상품정보"/"상품정보고시" tab first (lazy load).
3. CAPTURE the likely detail image (filename hints): `curl`, or
   `agent-browser screenshot ... -f`.
4. SLICE: very tall (1000x10000+px) -> never Read whole. PIL crop into ~8
   horizontal slices under `files/tmp/`.
5. READ slices, BOTTOM first (영양정보 conventionally near the end).
6. WRITE BACK: `cache-add` `estimated:false`, `source` e.g.
   "label (brand site 상세이미지, <date>)". placeholder estimate already
   written -> fix with `meal-upd`.
7. CLEANUP (never skip): `agent-browser close --all`, delete image + slices
   from `files/tmp/`.

Genuine LLM estimate only if agent-browser unavailable, image has no table,
or product not found anywhere. fallback does NOT waive step 7.

Enforcement: model judgement only. Gap: step 7 should be a hook/wrapper that
reaps leaked sessions (rule-lane candidate, see audit doc).
