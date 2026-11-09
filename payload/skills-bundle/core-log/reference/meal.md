# core-log -- meal adapter (food eaten)

When the owner asks to track a nutrient or supplement, run `meal-track-add <metric> [--goal <owner-stated amount>]` or `meal-track-add "<existing supplement name>" --regimen` (register the owner-stated regimen first if needed), then confirm in one line.

Optional card tracking uses the kit config keys `meal_tracked_metrics` (ordered
`[{"metric":"sodium","goal":2000}]`; `null` means no goal) and
`meal_tracked_regimens` (ordered supplement names). Use the verbs rather than
editing JSON: `meal-track-add sodium --goal 2000`, `meal-track-add fiber`,
`meal-track-del sodium`, `meal-track-add "크레아틴" --regimen`, and
`meal-track-del "크레아틴" --regimen`. `meal-track-list [YYYY-MM-DD]` returns
settings and per-name `taken` flags; intake stays on `regimen take --ids ID` and its
existing occurrence records. Register the stated regimen through `regimen add`
or `regimen add --name NAME --amount N --unit UNIT` if it does not exist. Tracking itself records no intake.
A repeated nutrient add updates its goal in place; omitting `--goal` clears it.
Without a goal the existing bar/value row shows an empty bar and `eaten / ?unit`.
Caffeine keeps its automatic row and target policy. Shared cards keep the
existing regimen count line while the owner chooses a supplement-name layout.

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

Before replying after capture, apply the common SETTLE ON EVIDENCE contract
from AGENT: saving facts alone does not finish an activity report. Read
`.claude/skills-bundle/schedule-log/reference/completion-chat.md` for the
shared completion check. For this meal capture, settlement does not suppress the printed time/card/
fullness continuation. Relay that continuation without a task-completion line. Tool work stays silent until the final reply.

meal-log is the record path: read `.claude/skills-bundle/meal-log/SKILL.md`
and follow its steps 0-5 and hard rules (`meal-day` first, lookup receipt,
`meal-add`, one line). Planned / not-yet-eaten food is not this adapter's.

- Meal management off (`manage-state meal` off): `meal-add` records
  without the satiety precondition and prints no `SATIETY_ASK` and no
  `TARGET_OFFER`; relay only what it prints. Do not ask satiety yourself.
- Portion unknown: the lookup's `portion-lookup` (estimate provenance kept);
  ask grams only when no portion can be found, once.
- Home cooking: an open cooking session first receives the owner words through
  `cooking input`; eating words close it and create made dishes. Follow
  `core-log/reference/cooking.md` for personal portions and the returned close
  day line (this overrides the generic no-coaching-line rule for cooked meals).
  Never bypass an existing session with hand macros. First `made-dish resolve '<owner words>' YYYY-MM-DD`.
  `RESOLVED` uses `meal-add DATE SLOT --cooked '<owner words>'`; or use
  `meal-add DATE SLOT NAME --dish ID --portion 'half|2pieces|1/3'`.
  A completed session already has the ingredients and yield. Never ask its
  finished weight. `NEEDS_PORTION` asks only that dish's supplied line and
  buttons. `NEEDS_NUTRITION` means look up the named ingredient and update its
  food cache, then `made-dish refresh ID` and retry; do not ask dish weight.
  `shop-stock-find` includes cooked leftovers. Use `--portion remaining`
  for the rest on a later day. `--key` identifies a retry when no inbound
  request id is available. Fractions refer to the original batch.
  To describe a leftover as pieces or glasses, multiply its remaining fraction
  by the stored yield; otherwise retain the tool's original portion label.
- A label / barcode / product photo is a meal input: it ends in `meal-add`.
- The meal card (`MEAL_CARD_BEGIN..END`) is the record shown back: relay it
  as meal-log says; never add a target or coaching line.

Interpret preparation and occurrence facts yourself: supply `--shake`,
`--mix-liquid water|milk|none` and `--mix-water-ml N` as applicable. Product
labels may supply directed mixing values; an owner reference never selects a
preparation mode. Pass the actual occurrence instant through capture `--at`
when stated; a free note does not answer the meal-clock question. Otherwise
relay the existing clock question and record its issued time value.
