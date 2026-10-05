---
name: core-setup
display_name: 온보딩 인터뷰
description: The kit-owned onboarding interview that turns a stated goal ("10km 뛰고 싶다") into {goal, routine, weekly homework, calendar block} with ZERO domain knowledge. Fires on a fresh instance's first goal conversation, on "온보딩", "온보딩 하자", "처음부터 세팅하자", "목표부터 루틴까지 잡아줘", "운동 시작하고 싶어" (or any domain's equivalent) when no routine exists for that goal yet, and when a domain pack's onboarding hands over the generic half. Runs the machine-gated interview through kit_life onboard-* verbs: pre-interview sweep of the shared kit first (nothing already known is ever asked), then a four-way classification gate before every elicitation (ask / ask / read / tell), one field per message, ending exactly when the five output lines (aspiration, goal, routine, homework, block) can be written. Prescription numbers (start distance, weekly increments, splits, sets/loads) are NOT this skill's business -- it degrades to conservative generic defaults and says so; a domain pack sharpens them later. Side effects: fills life_skeleton_* config keys (config-set path), writes onboarding_state, reads MIRROR_MODULE for the card.
---

# core-setup -- the interview is a machine; you narrate it

Engine (`kit_life.sh onboard-*`) decides WHAT happens each turn; you only
say HOW to speak the payload (rules moved to code after the 2026-09-17/18
rehearsal). never improvise, reorder, or add a question: no `question` key
= no question this turn.

## classification gate (engine-enforced)

```
a  fact only the owner knows      -> ASK
b  owner judgment / preference    -> ASK
c  stored or derivable            -> READ  (never a question)
d  expert judgment                -> TELL  (proposal + one-line reason + one confirm)
```
test for d: "이 답을 몰라도 되는 게 정상인가?" yes -> d. `read`/`tell`
payloads carry no question; `onboard-set` refuses c-only fields (travel
minutes). your half: never phrase a read or tell as a question.

## steps

0. sweep FIRST: `database/kit_life.sh onboard-sweep --capture --json`.
   fills empty `life_skeleton_work_*` from presence routines. anything found
   is KNOWN: never asked again by anyone; surfaces as `read_confirms`.
1. goal: `onboard-next` with no goal -> `delegate` to goal-session's move
   loop, then bind with `onboard-next --goal <id> --domain <D>`. read
   reference/goal-and-card.md when you get `delegate`, `ladder`, or
   `why_label_ask`.
2. loop, one payload = one message:
   `database/kit_life.sh onboard-next --json` /
   `database/kit_life.sh onboard-set <field> <value> [--by ...]`
   until `done: true`.
   - `ask` (a/b): speak `purpose` (one line), then `question` verbatim;
     choices as vertical `- ` bullets as they arrive. store `--by owner_stated`.
   - `tell` (d): `proposal` as your decision, `reason`, one `confirm`;
     `degraded` -> also `degrade_note`. agree -> `--by owner_approved`;
     override -> `--by owner_stated` + their value.
   - `delegate`: run the named command/skill (goal move loop; health-log
     on a probe answer of photo).
   - `read_confirms`: ONE readback line each, before the step's own lines.
   - `prior` (F8): answer from a FOLDED run -> offer for re-confirm
     ("지난번엔 수·금이라 하셨는데 이번에도 그대로 갈까요?"); write only after yes.
   - after `onboard-set`, the `receipt` line is the whole reply.
3. stop exactly at `done: true` (engine's `lines`: goal / aspiration /
   routine / homework / block, plus any pack `need`). not earlier, not later.
4. card: `onboard-card --json`, speak `card_lines` as given. read
   reference/goal-and-card.md before speaking it.
5. materialize with existing verbs only: routine via `routine` /
   schedule-log, place address via place-manage (`place-address`), homework
   as the week's tasks. mirror picks blocks up itself.

Deferral: "나중에요" -> ask "언제쯤 다시 볼까요?" once ->
`database/kit_life.sh onboard-defer --until YYYY-MM-DD` (verb refuses dateless).
the date is kept by the morning brief (`onboard-defer-nudge`: one line on
that morning, once). morning-brief off -> no reminder: say you'll pick it up
when they ask, never promise a nudge.
Restart (F8): `database/kit_life.sh onboard-restart` (binding another goal
does the same). folded answers survive only as `prior` offers.

## pack seam

Pack extends by declaring fields (`onboard-next --need height_cm,weight_kg`);
kit runs the discipline (probe-first, one at a time, read-before-ask), pack
owns the numbers. body fields: InBody probe is always the first body
question; `photo` -> health-log, then only still-missing fields.

No pack (degrade): numbers the kit cannot compute -> conservative generic
value + `degrade_note` (the `tell` rule), handed to the pack later. where
the pack declares the moment, point, never restate:

- 분할 전환 (split transition): declared home = pack_health `workout-program/SKILL.md` section 「분할 전환」 -- kit copies neither the rule nor its numbers.

팩 소유 축 (2.23.0 ruling): training experience / continuity is pack-owned.
pack_health declares it in `workout-program/SKILL.md` W3b, stores it as
`training_experience`, and its records already answer it. the kit engine has
NO such axis, and adding one (a "returner" option, any renamed question)
creates a second home for one question. held by
`database/tests/test_kit2230_pack_seam.py` (scans every registered template).
