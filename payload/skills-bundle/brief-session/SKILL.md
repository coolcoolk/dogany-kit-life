---
name: brief-session
display_name: 회고
description: >-
  Review and reflect on the owner's day, week or goals when the owner requests a review, accepts the review invitation,
  or continues an active review workspace. Resolve the lane from the invitation and kit state. DAILY: stages 2-4 after
  daily-retro.sh's stage-1 invitation at the 회고 시작 button; dispose of today's tasks, record events individually, read
  tomorrow back and close without resending stage 1. WEEKLY: decide weave or reflect per goal from its active anchor and
  judgment cadence; connect fragments or compare evidence, judge and place actions, one weekly retro routinely mixes
  both and ends with a next-week briefing and real placement. Trigger phrases: examples only, never entry requirements: "회고 시작", "주간 회고". Not loaded every session -- fires on explicit
  review/reflection intent. Reads goal/hypothesis/task/event/project state; writes task disposals including date clears,
  event retro lines, routine retro-skips, placements, project links and hypothesis-evidence via kit_life.sh. NOT a
  dashboard or a replacement for module setup: a request to begin using an unconfigured module goes to its setup entry
  before review.
---

Before answering about food, cooking, timing or the rest of today, run
`"${PROJECT_ROOT:-$(pwd)}/database/kit_life.sh" day-now` and use its local clock,
day type, logged meals, next eating occasion and remaining blocks. Never ask
which meal when day-now answers it; an afternoon food request after logged
lunch targets dinner unless the owner explicitly says otherwise. Never infer
attendees from a title; use only stored people/groups. Historical or explicitly
dated requests keep their stated date and meal; day-now supplies current context.


# brief-session

Retro workspace, two lanes sharing labels, buttons, question discipline (not stages):
- daily = stages 2-4 of nightly retro. dispose today's leftovers, one line
  per event, read tomorrow, close.
- weekly = review -> plan. mode per goal (weave | reflect), place next-week
  actions as real schedule entities.
- training facts (gym, tools, cardio, extra days, care per site, calorie
  target): `kit_life.sh body-brief --json` + `consult-answers --domain
  health --json`; never from memory, never re-asked when held.

NOT a dashboard. All DB via `LKIT="${PROJECT_ROOT:-$(pwd)}/database/kit_life.sh"`
(CWD = workspace root). No raw SQL. Verb signatures + output columns: read
reference/verbs.md when you need a column position or flag.

## Context before person or gift answers

First run `kit_life.sh noun-context "<current message + known recipient/occasion from this conversation>"` using `${PROJECT_ROOT:-$(pwd)}/database/kit_life.sh` from the agent workspace. Read PERSON / PERSON_REL / PERSON_EVENT and EVENT before answering or asking who/where/when; no EVENT does not mean no person facts. An EVENT line answers an event's place / people / occasion: ask nothing.
An unresolved nickname: `person-resolve`, then repeat noun-context with `--span "<resolved canonical name>"`; NEAR / AMBIGUOUS is not a resolved person. For missing details use `person-show <id>`.
Kit person facts override agent memory. A memory fact phrased as a future expectation whose date has passed must not be asserted: check the kit, or ask once if still unknown. A recorded child birth is past, never an upcoming delivery; do not infer medical/recovery facts.
Product "찾아줘" / "다 찾아줘", gift purchase sources, and packaging searches go straight to shopping-session's search flow and concrete product cards, including follow-ups without a repeated recipient. Never answer those with generic advice or an offer to search later.

## daily lane (owner-approved 2026-09-21)

Stage 1 = `routines/bundle/daily-retro.sh` (cron, sends list + `회고 시작`
button, stops). This skill starts at the button label or owner's own words
("오늘 회고", "회고 하자"). State lives in this session. NEVER re-render stage 1.
Stage 1 may carry `날짜 안 잡힌 할 일` (undated to-dos, stalled project step):
rules in reference/daily-form.md "undated block". The morning brief no longer
lists undated tasks or stalled project steps; the daily retro is their only surface.

Read reference/daily-form.md before composing the first daily message
(dashboard, card shapes, copy keys, verbatim examples).

Daily hard rules:
- D1 speak COUNTS of what this session will handle; never name the activity
  ("정리 / 돌아보기 / 추리기"), no 감상 line. no number -> no sentence.
- D7 empty stage = silent: no row, no notice, no "없음", no filler question.
- D19 no escape button ("그대로", "넘기기"). buttons only at disposal/skip.
- D13/D21 every user string + card shape = i18n key (`retro.daily.*`). new
  sentence -> add a key, never inline Korean.
- D16/D22 display axis != question axis: every event is SPOKEN; only the
  RECORD is skipped; skip unit = routine.
- D31 stored line = no ack. new task = exactly one notice line.
- Read `day-record-summary <today>` at interactive start; show known facts
  once, no coaching; never ask for a same-day/slot meal either agent wrote
  (shape: reference/daily-form.md).
- nested event = ONE item (`retro-items`): one line, one question with its
  flow recap, answer via `retro-item-set`. A carried visit has its own lead
  line, never under today's notice.

Steps:
Before asking about a task in daily retro, refresh today's `task-find`
rows and skip every row whose state is done. Never reuse an earlier
open-task list after a chat completion. A done report during retro must
call `task-done-today "<title>"` immediately, with the DONE/ALREADY_DONE /
ASK / NOT_FOUND / RETRY handling above; then refresh the remaining list.
The kit stores these completions with settled_by=owner-chat. Do not ask
complete / tomorrow / later / delete about an already settled block.

1. stage 2 `할 일` (one stage: today's still-todo first, then older). source
   `task-find <today>` + `task-read <today>` (stored clock; `task-find` = legacy
   5-column contract) + `task-overdue`. volume D11: all that went overdue
   today + 2 oldest before; rest = `외 N건` on dashboard count only. 1-2 items
   -> one at a time; 3+ -> list first, then one at a time.
   `[[OPTIONS: 완료 | 내일로 | 날짜 없이 두기 | 지우기]]` (labels from i18n retro.daily.btn.*), `완료` first:
   answer -> verb map: reference/daily-form.md "task answers".
2. stage 3 `오늘 일정`. source `retro-items <today>` (JSON; refresh before
   each question). One item = one outer event with everything inside it: ONE
   dashboard line, ONE turn, ONE question; only returned items may be asked
   about. Read reference/retro-items.md before the first item (routine-for-event
   gate, card verbatim, answers, carried visits, follow-up tasks).
   after events block, before stage 4:
   `kit_life.sh retro-sections --lane daily --mode section` -> relay verbatim;
   empty = add nothing.
3. stage 4 `내일 + 마감` = ONE message: dashboard + tomorrow list
   (`appt-find <tomorrow>` + `task-read <tomorrow>`) + summary chips +
   arrangement line only when the sequence collides + open close. NO buttons
   (D32). Failed reader = incomplete close, not empty list.

Daily -> weekly: goal compare, next-week planning, `나중에` sweep (D26),
multi-day patterns ("사흘째 미루시는데")
belong to the weekly lane.

## weekly lane

Session start, once each (do not re-query per step):
- read pending marker: `rendered_text` (first message was rendered by
  `routines/lib/life_dashboard.py`; never re-write, re-summarize or re-ask
  what it asked) + `workspace.*` cadence gates.
- `goal-list --owning-agent <agent> --status active`
- `goal-graduation`
- `freshness-report`
Read reference/reflection.md at session start (cadence table, staleness
rules, Steps 1-5). Read reference/weekly-render.md before your first weekly
turn. Read reference/weaving.md when zero goals reach cadence or the marker
carries a recommendation block.

Mode per goal row:
1. focus_state `dormant` -> skip entirely, never mention.
2. layer at cadence this week (marker `workspace.goals.judge`) -> reflect.
3. not at cadence -> weave (background mention only; no contrast/verdict).
4. zero rows left -> whole session weaves (normal, not failure).
Shape: Step 1 once (anchor + loop closure) -> Steps 2-3
per reflect goal -> Step 4 once (all placements) -> module sections -> close.
Never ask the owner to classify, attach, label or fill a field (project links
included); every question must serve looking back on the week or planning the
next (owner 2026-10-05).

Module sections (every weekly session), after last goal step, before close:
`kit_life.sh retro-sections --lane weekly --mode section` -> relay verbatim
as one block; empty = say nothing. module's last-line decision spends the
session question budget. never recompute or rephrase module numbers.

Weekly hard rules (verdicts come from tools, not judgment):
1. level-up only when `goal-readiness` says `rung_met=1 levelup_eligible=1
   levelup_blocked=0`.
2. `retire_candidate=1` / `downgrade_candidate=1` -> proposal MUST surface
   in Step 3 at the goal's own cadence. declined -> `goal-focus <id> dormant`.
3. `goal-graduation` graduate/stepdown/drift -> MUST surface in Step 3 with
   the tool's `proposal` sentence. graduate -> `--rung-state maintained`,
   never retire for success. new numbers come from owner.
4. derived next-week action MUST be placed before close (`task-add` with
   `#rp<YYYYWww>` marker + `--at`). only zero-placement exit = steady state.
   weaving mode too.
5. loop closure via `task-find "#rp<marker>"` (done/todo deterministic).
   goal attribution only when unambiguous (KNOWN GAP, reflection.md).
6. behavior >> verbal: only an executed action -> `--kind behavior`; "맞아"
   = at most `verbal_affirm`.
7. observations = `hypothesis-add` weak prior, never assertion. emotion /
   relationship / health -> `--sensitivity sensitive`, verbatim only. show
   "이런 게 묶여요", never ask abstract "묶을까요?".
8. zero-hit gate: before "nothing this week" run `goal-list` + `task-find`
   + `experience-list`.
9. cadence gate: judge from `layer` only (short weekly; mid shown; long
   untouched). never from `horizon_date` or a hardcoded list.
10. dormant goals: never contrasted, never mentioned.
11. recommendations: never originated here; render at most the ONE block
    weekly-review computed. cap ONE active recommendation per retro combined.
12. staleness: `freshness-report` verdict only, max ONE line, `noarrival`
    and undeclared metrics silent.

Budget: anchor 1 fragment + 1 question; max 1 question per step per SESSION
(not per goal); ~3-5 questions total; 1 confirm per placement; zero re-ask;
dormant costs zero.

## pointers

- episode merge / split / enrich / distill -> episode-session.
- auto-cluster / create-event -> episode-log.
- goal CRUD -> goal verbs directly.
- health-agent placement -> health-agent session-slot mechanism; `task-add --at` fallback only.
- not built: queryable retro-placement link (S1 data model); cross-agent
  life agent<->health agent retro (DGN-600 context). do not implement from this skill.
