# meal-log -- satiety question (every meal slot) + calibration

Read when `meal-add` prints `SATIETY_ASK` / `SATIETY_CLOSE`.

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

## recording and calibration

Lookup receipts still authorize the lookup step (043); no receipt at all
can refuse the write. The old satiety precondition (048) is retired: lacking
a fullness answer never prevents an estimated food from being recorded.
`--no-satiety` / SDK `allow_unsated` remain compatible and cannot block writes.
The seven choice tokens and independent weight provenance still control
calibration; neither an answer nor an exact weight may be fabricated.

Pass `meal-add ... --portion-source stated` when the owner gives measured
grams, `--portion-source package` for a known package net weight, and
`--portion-source estimate` when the portion came from a photo or vague
amount. The flag grades mass only: `ESTIMATE` still describes nutrition.
Do not mark a photo guess as stated just because meal-add needs a numeric
grams argument. Without a flag, existing cache/weight evidence is used;
a documented packaged serving stays exact despite estimated nutrition.

### amount basis: MUST pass whenever the owner gave an amount

Whenever the owner states any amount, `meal-add` / `meal-upd` carries the basis you chose
from his words (code never parses them; you decide):

`--portion-basis exact|package|count|part|volume|standard|estimate
 --portion-evidence "<owner words verbatim>" [--count N | --fraction F | --volume-ml N]`

| owner said | basis | extra |
|---|---|---|
| grams said or weighed ("150g", scale) | `exact` | grams arg = that number |
| own-cooking recipe sum with known g/kg ingredients | `exact` | cooked-dish path keeps the frozen ingredient evidence |
| identified package with an official weight (photo/label) | `package` | grams arg = net weight |
| N pieces ("두 개") | `count` | `--count N` |
| half / one third ("반", "1/3") | `part` | `--fraction 0.5` |
| ml ("500ml") | `volume` | `--volume-ml 500` |
| no amount, usual restaurant serving | `standard` | none |
| restaurant whole with unknown mass, photo or vague amount | `estimate` | none |

`exact`/`package` need positive grams and evidence; `count`/`part`/`volume`
need evidence and their number. Nutrition estimated from a similar product
never lowers a basis: a known portion stays `exact`/`package` even when the
`ESTIMATE` line is on. Never downgrade to `estimate` because the nutrition is
a guess, and never skip the flags because the amount was said in passing.
Satiety recalibration pins `exact`/`package` rows and still adjusts
`count`/`part`/`volume`/`standard`/`estimate` rows. Reliability weights are
stored separately: exact/package 1.0, part 0.8, count/volume 0.65,
standard serving 0.2, restaurant whole/photo estimate 0.0. These weights do
not pin inferred grams. The cooked-dish path accepts the same flags (one dish
per declaration). Without flags, a frozen own-cooking recipe sum whose
ingredients are all in g/kg establishes exact mass, even with estimated
nutrition; converted pieces/ml do not. This extends `--portion-source`; if both are given they
must agree (exact=stated).

## scale (owner's wording, verbatim, lightest -> heaviest)

Code copy of the scale: `mealstate.py` `FULLNESS_LEVELS` (writer refuses any
other token). labels only: no percentage stored,
shown or mapped ("근데 퍼센트말고 그냥 언어 레이블로하자고"). rank internal,
never spoken as a number.

| token (storage) | display (owner's wording, exact) |
|---|---|
| `snack` | 안먹은 수준이다(간식) |
| `too_little` | 허기만 달랬다 |
| `slightly_light` | 조금 여유롭게 부족하다 |
| `just_right` | 딱 적당하다 |
| `slightly_heavy` | 조금 부담스럽다 |
| `too_full` | 너무 배부르다 |
| `stuffed` | 터질것같다 |

Store: `$LKIT meal-satiety <date> <slot> <token|skip>` (TOKEN only; the
verb refuses anything else -- no mapping, no rounding). The legacy writer
`python3 .claude/skills/meal-log/mealstate.py fullness-set <date> <meal>
<token> [est_kcal]` still works and routes to the same shared kit writer. The optional legacy kcal is
validated but the stored amount comes from the actual meal rows.

Chat meal clocks use `meal-time <event id> <now|manual|clock>`, writing the
meal event, record blocks and shared occasion together. The legacy
`mealstate.py time-set` cache is not this continuation and must not be used
to answer MEAL_TIME_ASK.

## calibration (kit_life `meal-satiety`, code-owned)

Calibrate fullness against known food weights and volumes, then use those
ratios when estimating a later meal.

- store: the shared kit DB `satiety_answer`, keyed by member + occasion,
  with food IDs, grams, kcal, level, recorded-at and source agent. The health and life agents
  use the same member (`LIFEKIT_MEMBER_ID`, default `owner`). Migration 104
  plus `database/satiety_migrate.py` imports both agents' legacy JSON caches,
  deduplicates occasions and recovers missing amounts from current meal rows.
  The source files remain untouched. Pending requests and scaling factors
  stay with the shared occasion; the legacy JSON is no longer an answer store.
- weight and nutrient grades are separate. The lookup receipt still grades
  nutrients for the post-card question. Calibration uses `satiety_weight_grade`:
  package/label or official serving weight is exact even when macros are
  estimated. Legacy source text explicitly recording a corroborated published
  serving weight also establishes weight; a bare web search or official kcal
  alone does not. Negated/missing sources, LLM-only estimates and estimated
  weight cannot establish exact weight, even with a stale exact receipt.
- sample = one answered slot whose EVERY current row has positive, exact
  weight. Read-time selection joins answers to current meal rows and source
  evidence, including answers with no stored calibration fields. Stored
  `grams`/`resolved` summaries are not evidence; no historical data is written.
  Receipt expiry limits write authorization, not historical calibration reads.
- a whole labelled serving is known; multiples need an explicit unit count.
  An arbitrary gram suffix or a generic nutrition basis does not prove the
  consumed weight. Missing/unknown-weight drinks still disqualify the slot.
- one answer describes the whole slot: there are no per-food samples. Exact
  foods in a mixed slot stay pinned in the existing subtraction formula, but
  cannot independently teach fullness. An answer in another slot is never
  borrowed. Previously scaled estimates cannot teach their own result.
- calibrate on GRAMS (satiety follows weight/volume, not kcal). Volume
  proxy = energy density kcal/g of the slot (volumetrics): bands
  very_low <0.6, low 0.6-1.5, medium 1.5-4.0, high >4.0.
- Basis: `satiety_answer.state` stores current/recalled, eaten_at and
  minutes_since_meal at answer time. Recalled samples receive **0.5×** the
  current-sample weight, multiplied by the existing center and recency weights.
  This conservative fixed discount reflects recall uncertainty without
  inventing an elapsed-time decay; legacy unknown-basis samples keep their
  previous weight. Raw-count readiness remains unchanged.
- time-varying: only samples in the last 90 days, weight 0.5^(age/30d);
  multiplied by closeness to the center (`just_right` 1.0, adjacent 0.5,
  next 0.2, endpoints 0.1). Reference = weighted median grams; readiness
  requires at least 5 eligible answers (raw count). Distant levels also need ready
  near-center evidence. A level whose samples age out becomes not ready.
- lookup: (level, band of THIS slot's own kcal/g) first; not ready -> the
  level across bands. the verb names the scope used.
- estimated-weight slot, ready: factor = (ref - exact rows' grams) / estimated
  rows' grams. At either clamp bound (0.5 or 2.0), including an unclamped
  ratio exactly equal to the bound, `SATIETY_PORTION_ASK` returns proposed
  foods/grams to the agent. Keep the answer and ask for the actual portion
  using existing portion-question copy before any correction; no food or
  scaling provenance is changed. Only interior factors move estimated rows (grams +
  macros via meal-upd; kcal stays DB-generated; a trailing "(Xg)" in the
  name follows). Not ready -> unchanged. `CALIB_NOT_READY` prints weighted
  `effective_n` and `need` as machine diagnostics only. Never report them:
  no calibration/보정/기준 progress line, no weight fraction, no explanation
  of how answers are weighted, in any state (not ready, partial, ready).
- idempotent: each row keeps the factor applied; a re-run or a changed
  answer moves rows from the ORIGINAL estimate, never compounds.
- constants: `kit_life.SATIETY_CALIB_*`, `SATIETY_FACTOR_*`,
  `SATIETY_DENSITY_BANDS`.

Verb output (machine lines, never shown):
```
SATIETY<TAB>date<TAB>slot<TAB>status<TAB>level<TAB>resolved<TAB>grams<TAB>kcal
CALIBRATED<TAB>factor=..<TAB>grams=old->new<TAB>ref=..<TAB>scope=..<TAB>n=..
CALIB_NOT_READY<TAB>effective_n=..<TAB>need=5<TAB>scope=..<TAB>band=..
SATIETY_PORTION_ASK<TAB><proposed food IDs, names and grams as JSON>
```
When food grams change, `SATIETY_REPLY` carries the owner-approved notice
(2026-11-02). Relay the entire multiline text once, verbatim, preserving one
quote-block line per changed food:
```
포만감 답 기준으로 양을 고쳤어요.
> {food} {old_g}g → {new_g}g · {old_kcal} → {new_kcal}kcal
```
Only changed food portions appear; known weights stay pinned and unlisted.
No changed grams means no correction notice. Numbers use thousands separators;
grams retain stored tenths when needed, and kcal rounds as on the meal card.
Never substitute occasion totals or add a factor, reference, band, sample
count, progress sentence or other new visible element. `satiety-calib`'s
`readiness=` field remains a diagnostic, never shown to the owner.

Drift read: `$LKIT satiety-calib [YYYY-MM-DD]` -> per level (and per level x
band with samples): diagnostic n, ready, ref_g, newest, oldest, effective_n
and translated readiness. The evidence fraction always uses effective_n.

## open (owner, not built)

- pre-meal hunger confounds the reading (the same grams feel lighter after
  a skipped meal). Not modelled; a later cut may ask or infer it.
- the reference is per person per level; no cross-person prior.
- the clock and fullness wording above and the seven labels are owner-approved.

## mixing water (owner 2026-10-26 12:58)

Density uses the mass actually swallowed. Log powder grams as its nutrient
basis and add the water mixed into it via `meal-add --shake --mix-water-ml
<stated ml>`. Water on the side (`물 500ml`) is a zero-kcal non-meal and adds
no occasion grams. With milk, use `--mix-liquid milk` and log the milk as its
own food; it already supplies both mass and nutrients.

For an unstated mixing volume, `--shake` uses product directions, marked
estimated in the RESOLVED sentence and persisted with its source. ISO100's
[manufacturer label](https://dsld.od.nih.gov/label/302413) directs 150–180ml
per scoop; the default is its midpoint 165ml per stated scoop, an estimate.
For other products look up the label first and pass `--mix-water-default-ml
<ml> --mix-water-source <label URL>`. Do not invent a label volume. Unknown
water volume stays unknown and cannot teach an exact-mass sample. Dry powder
without a shake/mixing context has no implicit water.

Mixing water is stored beside the meal's nutrient portion. It contributes
zero kcal and never multiplies protein or other macros. Rescaling an estimated
powder moves its dry grams and nutrients while keeping the stated/assumed mix
water fixed. Label-measured powder stays pinned; estimated mixing water does
not turn that powder into an estimated nutrient portion. To correct a stated
water amount use `meal-upd <id> mix_water_ml=<ml>`.

Cooking satiety: acknowledge only the stated feeling and recorded portion. Never
invent a count or say “next time one pie” without checking what the owner
actually ate. An overly full report is evidence to retain, not permission to
manufacture a smaller-portion recommendation from a guess.
