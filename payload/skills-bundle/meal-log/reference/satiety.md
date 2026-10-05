# meal-log -- satiety question (estimate branch only)

Read when a lookup MISSED (macros are your estimate) or `meal-add` refused
with `SatietySkippedError`.

Status (owner 2026-09-11): direction + trigger settled; magnitude + question
wording TBD. v0.1 = ask and store only.

## gate (048, code-owned)

- lookup.py stamps `meal_lookup_receipt.resolved`: `exact` (cache hit,
  barcode, label, public-DB) or `estimate` (miss). only `estimate` gated.
- `meal-add` refuses an estimated name whose slot has no satiety answer
  (`SatietySkippedError`, before any write). escape `--no-satiety` always works.
- fail-open: `resolved IS NULL` (pre-048 receipt, ungraded cache-add) = allowed.
- not retroactive; no receipt at all = 043's refusal, not this one.

## scale (owner's wording, verbatim, lightest -> heaviest)

Code copy of the scale: `mealstate.py` `FULLNESS_LEVELS` (writer refuses any
other token). labels only: no percentage stored,
shown or mapped ("근데 퍼센트말고 그냥 언어 레이블로하자고"). rank internal,
never spoken as a number.

| token (storage) | display (owner's wording, exact) |
|---|---|
| `snack` | 안먹은 수준이다(간식) |
| `too_little` | 너무 부족하다 |
| `slightly_light` | 조금 여유롭게 부족하다 |
| `just_right` | 딱 적당하다 |
| `slightly_heavy` | 조금 부담스럽다 |
| `too_full` | 너무 배부르다 |
| `stuffed` | 터질것같다 |

Store: `python3 .claude/skills/meal-log/mealstate.py fullness-set <date> <meal> <token> [est_kcal]`
-- TOKEN only; writer refuses anything else (no mapping, no rounding).
Then retry `meal-add`.

Also: `mealstate.py time-set <date> <meal> <HH:MM> [source]` records when
eaten. store is local per-instance on purpose (029 §4c: behaviour, not
shared nutrition fact).

## estimate revision -- NOT in v0.1

Settled principle (4-level form): hungrier-than-expected -> revise DOWN,
overate -> UP, middle keeps. On the 7-level ladder the cut lines and step
size are TBD and not yours to pick. So v0.1 writes the first-pass estimate
unchanged; `mealstate.py` reads nothing back ("지금은 과하니까 나중버젼에하자고").

TBD (owner): which levels revise; magnitude; trigger for composite /
partly-estimated meals; exact question wording.
Yours meanwhile: question wording, when to ask in a multi-item meal.
