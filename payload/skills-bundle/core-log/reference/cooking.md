# Cooking: menu choice to kit session

The agent judges cooking intentions from the conversation, including dish
selection and later timing, preparation or scheduling answers. No word list,
phrase or regex decides intent. This is the one cooking procedure; an open
pending-context hook points here on every turn, regardless of wording.
Cooking scheduling belongs here; never file cooking as `task-add`.

## Context before menu or plan

First run `$LKIT day-now`. Use its local clock, logged meal slots, next eating
occasion and remaining blocks before any menu suggestion or plan. Never ask
which meal when this answers it. An afternoon request after logged lunch
is about dinner unless the owner explicitly names another meal. A work block
saying the owner is home does not make lunch the target. No dinner record
means none is logged, not proof the owner has not eaten. Keep historical or
explicitly dated meal requests on their stated date/slot.
Treat block titles and times exactly as returned. Only stored attendees or
groups establish company; empty fields do not prove a solo or group activity.
Do not invent a meeting, travel time, departure deadline or social obligation.
Use today's facts and the current conversation before asking anything.
For each stated ingredient read `$LKIT shop-stock-find "<ingredient>"`,
`$LKIT shop-capture-find "<ingredient>"`, and
`$LKIT shop-owned-find "<ingredient>"`; use purpose/event capture lookup when
known (shopping.md). For equipment read `$LKIT shop-owned-find "<tool>"`
or `$LKIT shop-owned-list`. A selected pack is not remaining stock; unknown
quantity is not missing food. Never claim an unrecorded amount is on hand.

## Menu choice belongs to the owner

Dish not picked: suggest 2-3 feasible menus from the stated ingredients and
recorded stock/equipment, with one recommendation and a brief reason. Retain
the target clock and eaters; do not open a session or write a freehand timeline
before the owner picks. Answer a pick in context without requiring the owner
to repeat ingredients, servings or target. A stated dish skips menu selection.
"가영이랑" means the owner plus Gayoung = 2 eaters, not one serving.
An afternoon "5시 완성" means today 17:00 in the owner's timezone.

Plain "what can I eat" without cooking intent may stay advice-only. A menu
suggestion never logs intake or needs diet-plan. Enter the session after the
owner picks a dish with a stated/retained target time, or explicitly asks to
cook/start ("요리 시작"). A next-meal inference supplies no exact serving time.

## Chosen dish -> typed plan -> session

As soon as the owner picks or states dishes, record the context silently:
`$LKIT cooking pending "<dish 1>" "<dish 2>" [--eaters owner,<companion>]
[--target <dated ISO time with offset>]`. Include every known eater and target;
unknown eaters default to owner, and an unstated time stays absent. This records
selection only; it posts no card and creates no calendar task. Update the same
pending context when later answers supply companions or time. Omitted options
retain the pending values. `cooking template` returns this context as `pending`.
The pointer persists until a successful cooking plan/open consumes the context
or the owner's local day ends. Read the message yourself to choose the action;
code never decides intent from those words. A scheduling request for the chosen
dishes follows this procedure, including after a question about meal time.

Run `$LKIT cooking template` before replying with preparation or timing. Its
`appliance` field reads the stored oven type/modes; use those facts before any
oven question. Retain dishes, target and companions from earlier turns; later
time/companion answers update the same plan. Never replace the kit plan with a
freehand chat timeline. Write PLAN.json in its shape with one recipe
per dish, using stated quantities directly. Set `eaters` to the owner and stated companions so the share question asks each person's amount once. For unknown recipe amounts run `$LKIT cooking portions` first. Size the owner from his own typical meal kcal in history, never a generic Western serving. With no companion history start smaller than the owner and ask once if the amount matters. Set recipe amount,
base_amount and yield consistently for those eaters. Preserve exact stated
amounts; never ask them again.

Every ingredient quantity and every amount in an action/instruction uses g,
ml or 개 (piece). Convert recipe spoon/cup defaults to g/ml. The engine inserts
scaled amounts for each step's ingredient ids; use {ingredient_id} placeholders
rather than hardcoding an amount that could stop matching after scaling.
No "한 줌", "한 컵", "적당히", "조금", "넉넉히". Only an owner's stated
quantity may keep another unit or vague wording: set that ingredient's
owner_amount to the owner's exact amount phrase, never fabricate a quote.
Include all used food/water/seasonings in ingredients and in each step's
ingredient ids. One action per step; actions and done cues use plain everyday
Korean. Name each heat step's vessel in gear.

For every oven step with a specified temperature or mode, fill typed `temp_c`
(Celsius) and `oven_mode` (`top_bottom` or `convection`) using the stored
appliance modes. Keep instructions in `action`, but never leave the oven
settings only there: the plan timeline reads typed fields, never action text.
The timeline adds the typed heat/oven condition after the step duration and
shows a non-empty `done_cue` verbatim on its own `ㄴ` line.

`done_cue` is optional. Fill it at plan time only when the action alone cannot
tell the owner when it is done, for example 고기 표면이 손으로 만질 만큼 식음,
겉면이 노릇함, or 물이 끓음. For self-evident actions such as 꺼내기, 계량하기,
and 담기, omit it or leave it empty; do not repeat the action as a cue. The
card shows 완료 기준 only for a non-empty typed `done_cue`. The automatic
냉장고에서 {meat} 꺼내기 step has an empty cue.

For meat set ingredient `food_type: "meat"` and the heat step's typed
`technique: "roast"` or `"sear"`. The compiler adds an `ingredient_prep`
step before the first such heat, with `tempering_lead_sec` defaulting to 2700
(45 minutes; choose 1800..3600 for the plan). This lead belongs in the typed
plan, never inferred from step names. If the owner says the meat is already
out, set `tempering_lead_sec: 0` on that heat step before opening the plan.

Stated target -> set target_at to the dated timezone-aware completion time,
timezone to the owner's zone, resources to the actual kitchen capacity, and
`target_source: owner`. An owner target is the owner's: never change or drop
target_at on a re-plan, re-open or new plan without asking the owner first.
Lateness moves the ETA only. If the kit answers TARGET_OWNER_LOCKED, ask the
owner; rerun with `--owner-confirmed` only after they agree to the new time.
Never open a second session or plan file to restart a meal that is under way;
re-plan the same meal (a plan without target_at inherits the owner target).
For each prep step set typed `ahead` from cooking constraints, never from a
word list: `required` with `ahead_min_sec` for a minimum resting/marinating/brining
lead, `allowed` with `hold_max_sec` for preparation that can safely wait (including
its storage/quality limit), or `never` for order-bound work immediately before
its cook step. Missing `ahead` means `never`. Do not label a step allowed unless
both doing it ahead and during cooking are feasible. Keep explicit prerequisites;
never move a knife/heat/order-bound step ahead just because it says prep.

For a target plan, `cooking plan PLAN.json` posts only the plan card. Required
prep is placed automatically with its required-lead info line. Only allowed prep
asks once: `미리 해도 되는 프렙 <n>개: <name> · <name> · ...`, with
`모두 미리하기`, `모두 요리 때 같이하기`, `하나씩 고르기`.
The individual path shows `• <name> (<N>분)` with `미리` / `요리 때`.
Wait for scoped button answers. Interpret a typed answer yourself in context,
then use `cooking prep-mode separate|joined|individual PLAN.json` (separate = all
ahead, joined = all during cooking), or `cooking prep-step RECIPE-STEP ahead|cook
PLAN.json` for each individual answer, with the request-bound owner receipt.
Never keyword-match an answer or ask for special words. Zero allowed steps means
no question. The kit rebuilds the plan when all allowed choices are answered,
respecting each hold limit. Do not open a full target session at plan time.

Represent take out, thaw, measure and gather as explicit `phase=prep` steps
with `ingredient_prep=true`, no knife/heat, durations and prerequisites. Knife
work, marinating, dry brining and soaking remain ordinary `phase=prep` steps;
heat/finishing remain cook. Record resting lead time with wait_after_sec or an
unattended prep step. No keyword classifier decides a step's stage.

Required and selected allowed prep run ahead; never and declined allowed prep
stay in the cooking lane. Cooking back-schedules from target_at.
The kit derives each stage clock from those steps; never hand-write alert times.
An empty stage is omitted. The plan is ingredients first, ordered actions in
quote blocks, and the one-line stage timeline last. Each stage alert opens only
its own work through its named button. Separate prep completion supplies the
cooking inputs and stock identity; never manually open the full target plan.
Represent any resting/plating time as an explicit final step, with
serve_after_sec omitted or 0, so the last step itself ends at the target clock.
No stated time -> omit target_at. Run `$LKIT cooking check PLAN.json` until
PLAN_OK, then `$LKIT cooking plan PLAN.json` (code posts the plan messages),
For target plans, planning (or the final allowed answer) arms alerts on the existing
heartbeat; use the stage buttons when they arrive. Without a target, run
`$LKIT cooking open PLAN.json` (or bind <task-id> when scheduled).
The session pin appears at Start. While open, read the owner message and call one explicit verb. Use `cooking check-step '<exact step name>'`, `cooking delay <seconds>`, or `cooking close`; `cooking input` with prose is inert and owner words alone never change session state. Never start a separate
timer/session file or replace code's timeline with prose.

Session corrections are typed too. Use `cooking step-upd STEP_ID STEP.json`
for fields on one pending step, `cooking step-del STEP_ID`, or
`cooking dish-del DISH_ID`; started food is deliberately refused. Set an
absolute accumulated delay with `cooking delay-set SECONDS`. Use
`cooking cancel`, reverse it with `cooking cancel-undo`, or return a terminal
session to active work with `cooking reopen`. After an accidental close use
`cooking close-undo`; it restores the pre-close session, pantry quantities and
made-dish rows in one transaction. Never select one of these verbs by matching
words in the owner's message.

A wrong portion logged from a made dish is corrected through the meal lane:
`meal-del BLOCK_ID` first restores that made dish's remaining fraction, then
rerun the normal made-dish resolve/log flow with the corrected typed portion.
Do not edit the calorie measures or `made_dish_meal` rows directly.

Before Start, edit the same plan file and run `cooking plan` again. The kit
edits the existing plan card; do not relay another full plan or tell the owner
to choose the newest card. Once started, use the live verbs below.

During an open session, add a single requested step with
`$LKIT cooking add-step STEP.json`. STEP.json is one step in the printed
STEP schema plus `dish` (recipe id) and exactly one of `before` (pending local
step id), `after` (existing local step id), or `start_at` (timezone-aware time).
Use `step_type: "ingredient_prep"` for preparation tied to a particular heat
step rather than the whole prep-ahead block. An explicit time adds independent
work unless `prereq` is supplied; before/after inserts dependency edges.
For an already-started/completed anchor, use `after` or independent `start_at`.
The kit keeps completed steps, records, existing buttons and running timers,
replans pending work from now, and updates the pin and dish tables. A new
step's own card and done button appear when it becomes due. Retry failed
delivery with `cooking show`; never replay `add-step`. Never offer cancel/reopen
or a separate reminder for one added step.

## Output

Before a pick: one menu reply, 2-3 candidates and one recommendation.
After a pick with a target: the kit posts the plan card and asks only about
allowed prep, using the approved card above. No shopping/prep alert, progress
board, step card or pin is sent at plan time. Stage alerts arrive on their own
computed clocks with one named Start button each. Without a target, the existing
prep/timeline/session flow applies. Never repeat code-posted messages.

## Side effects

Menu comparison reads only. A chosen plan writes the plan file and kit session
through cooking verbs, posts kit cards, and owns the cooking pin/timers.
Cooking completion records ingredient use and made dishes; a menu request
alone is not an eaten meal and must not create a meal record.

## Live progress and owner evidence

The UserPromptSubmit hook captures the latest owner message as evidence.
Read that message yourself. Use typed step ids from `cooking show`:
`cooking check-step ID` completes that reported physical step;
`cooking start-step ID` starts its timer; `cooking begin-step ID` begins active
prep; `cooking undo-step ID` reopens it and dependent ticks. For several
reported steps ("앞에 거 다 했어", "X부터 바로 시작") make ONE call:
`cooking catch-up --done ID,ID,ID --start ID` (--start optional). The kit
applies every state change first and renders once; never loop check-step.
Never complete an unreported step.
Read a reply to an issued check in that card's step context. If the step's
timer is up, an owner confirmation such as "파스타 면 냄비 확인했어요" is
completion evidence: call `cooking check-step ID` on the first reply. Do not
ask for a second completion phrase. Before expiry, a check only acknowledges
looking; do not finish food solely because the owner checked it early.
`cooking start`, `cancel`, `refresh`, and `edit` are explicit session verbs.
Authenticated button metadata goes to `cooking tap ACTIVITY_TOKEN`; owner prose
never goes through the callback path. `cooking input` with prose is inert.

Use `cooking delay SECONDS` for a reported delay and
`cooking timer-set STEP_ID SECONDS` for a duration correction. Ingredient edits
use `cooking change CHANGES.json`, a JSON list of objects with `recipe` and
`ingredient` ids and one of `grams`, `missing: true`, or `replacement` (an
existing substitution name). Preserve quantities and completed history.
If a reference is ambiguous, ask only for the missing fact; any wording of
the answer is acceptable. Never ask the owner to say a scripted sentence.

The pin stays. Every check updates one `요리별 진행사항` card per dish,
including prep, actual step count and measured amounts. At most three buttons
per card, one per row with full labels. State change always gets the returned
acknowledgement; never NO_PUSH after a change, even when cards were sent.
After a check, relay that acknowledgement once. If guidance is needed, give
only the current action and the next action from `cooking show`. The cards
carry the remaining schedule; do not restate it or repeat another running
dish's finish time on every turn. Keep an unrelated onboarding flow paused
while cooking is active. Apply an explicit reply to its already-issued card,
but do not append another onboarding question to a cooking update.

## Oven and portion facts

Before heat instructions, read the `appliance` field from `cooking template`
or run `cooking appliance`, then read owned gear. If type and modes are stored,
use them without asking again. Only missing facts go through the existing
`cooking check PLAN.json` appliance gate; relay its question once. Read the
owner's answer yourself and store it with
`cooking appliance --modes top_bottom,convection [--type oven|air_fryer]`,
listing only the modes the owner has ("모두 가능", "다 돼" = both). Never ask the
owner to repeat the answer with specific words. plan/check/open refuse unknown
or incompatible modes. Do not infer an
air fryer or fan mode from the existence of an oven. Use the stored mode. Equipment corrections use `kitchen-fact-add OWNED_ID key=JSON_VALUE`;
`kitchen-memory-backfill OWNED_ID --memory FILE` now imports an agent-reviewed
JSON fact object, never phrases from memory.

For ambiguous weights ask once: `80g이 한 장이에요, 전체예요?` Run
`cooking weight PLAN.json RECIPE_ID INGREDIENT_ID` with the owner's latest
message. Read the answer yourself and repeat with typed
`--basis per_piece|total --grams N [--count N]`; the command stores `weight_basis`,
`grams_per_unit`, and `weight_owner_words` on the ingredient. Do not silently
turn a package total into a per-sheet weight.

Use the returned owner/companion meal kcal and main-dish budget after rich sides when setting recipe amounts. Never add a full generic main serving on top of a 440kcal pastry side. If a chosen side fills the companion budget, ask once whether to reduce the side or how much main they want; do not silently increase both meals. Side pastry/bread starts at one per person. Name rich items with kcal when
suggesting a count: an 80g puff pastry sheet is about 440kcal; butter and cream
also need their kcal. Companion portions do not automatically equal the owner.
Recipe amount and individual intake are distinct. Use owner-stated shares,
including uneven shares; `반반` alone authorizes equal splitting.

## Finish, eat, and acknowledge

When the owner has finished cooking, the model calls `cooking close`; that explicit verb closes the session, cancels kit timers, retires cards, records used ingredients through inventory and creates made dishes. No finish or eating word triggers this by itself. Read `made-dish list`, then use
`meal-add DATE SLOT --dish ID --portion 1/2 --companion-portion 1/2` for an
owner-authorized equal split; omit companion consumption unless reported.
For several dishes use `--shares '[{"dish":ID,"portion":"1/2","companion":"1/2"}]'`
with one object per dish, in one atomic meal call.

If the session was cancelled and the owner finished by hand, use
`meal-add DATE SLOT --cooked 'exact owner words'` directly. It resolves that
day's saved plan and the stated owner percentage or equal split using recipe
nutrition, without reopening, closing or posting cooking cards. Never offer
session reopen as a meal-log route. The explicit flag declares cooked food;
only the amount is parsed, never words for action routing. For distinct
per-dish amounts, resolve made-dish ids and use typed `--shares`. With unknown
share, call `meal-add DATE SLOT --dish ID`; relay the existing share question
once and translate the answer into typed portions. Uneven fractions are supported.
Never use `--new --no-lookup` hand macros when a cooking session exists.
Never tell the owner internal reasons such as a card being incompletely closed.

Relay the meal command's `reply` verbatim, once. It contains the existing meal
card (including each dish's fraction), acknowledgement and current day line.
Do not paraphrase, append a parenthetical restatement of dishes or shares, or
turn the owner's first-person words into the agent's first person. Do not
repeat separately printed MEAL_CARD. Append only a printed satiety question.
The code owns any leftover question and satiety acknowledgement.

For any timing question, run `cooking show` and answer only from the returned
`schedule` (step name and local HH:MM start/end). QUESTION results also carry
the current schedule. Never quote times from earlier messages, reminder text,
or a previous plan; recomputation can shift all remaining dishes.
