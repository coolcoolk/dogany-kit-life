---
name: brief-session
display_name: 회고
description: >-
  Retro workspace, two lanes. DAILY (일간회고): stages 2-4 of the nightly
  retro -- dispose of today's tasks, record today's events one at a time,
  read tomorrow back and close. Stage 1 is sent by routines/bundle/
  daily-retro.sh; this SKILL takes over at the 회고 시작 button.
  WEEKLY: review->plan workspace whose mode is decided PER GOAL, not per
  session -- a goal with no active anchor is woven (surface and connect week
  fragments), a goal at its judgment cadence is reflected on (anchor ->
  compare -> judge -> place -> close loop). One weekly retro routinely mixes
  both and ends with at least a next-week briefing and a real placement.
  Trigger phrases: "회고 시작", "오늘 회고", "일간 회고", "하루 정리",
  "주간 회고", "이번 주 돌아보기", "한 주 정리", "회고 해줘", "weekly
  review", "지난 주 어떻게 됐어", "목표 점검". Not loaded every session --
  fires on explicit review/reflection intent. Side effects: reads
  goal/hypothesis/task/event/prep/project state; writes task disposals
  (incl. date clears), event retro lines, routine retro-skips, task
  placements, project links and hypothesis-evidence via kit_life.sh.
---

# brief-session

Retro workspace, two lanes sharing labels, buttons, question discipline (not stages):
- daily = stages 2-4 of nightly retro. dispose today's leftovers, one line
  per event, read tomorrow, close.
- weekly = review -> plan. mode per goal (weave | reflect), place next-week
  actions as real schedule entities.

NOT a dashboard. All DB via `LKIT="${PROJECT_ROOT:-$(pwd)}/database/kit_life.sh"`
(CWD = workspace root). No raw SQL. Verb signatures + output columns: read
reference/verbs.md when you need a column position or flag.

## daily lane (owner-approved 2026-09-21)

Stage 1 = `routines/bundle/daily-retro.sh` (cron, sends list + `회고 시작`
button, stops). This skill starts at the button label or owner's own words
("오늘 회고", "회고 하자"). State lives in this session. NEVER re-render stage 1.

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

Steps:
1. stage 2 `할 일` (one stage: today's still-todo first, then older). source
   `task-find <today>` + `task-overdue`. volume D11: all that went overdue
   today + 2 oldest before; rest = `외 N건` on dashboard count only. 1-2 items
   -> one at a time; 3+ -> list first, then one at a time.
   `[[OPTIONS: 완료 | 내일로 | 나중에 | 지우기]]`, `완료` first:
   - 완료 -> `task-done <id>`
   - 내일로 -> `task-reschedule <id> <tomorrow>`. no time question (D5).
   - 나중에 -> `task-undate <id>` (dateless backlog; weekly D26 places it).
     never fake with a far date. routine occurrence refuses -> treat as 내일로 or 지우기.
   - 지우기 -> `task-archive <id>`
   - worded answer beats nearest button.
2. stage 3 `오늘 일정`. source `appt-find <today>` (appointment + presence).
   per event, BEFORE building its turn: `routine-for-event <event>`.
   - verb errors -> one-off: open question naming the subject (D30), no buttons.
   - `retro_skip=1` -> no question, no buttons, no turn; still spoken,
     dashboard child `· 스킵`. checked per event, so a skip set tonight holds.
   - `retro_skip=0` -> routine turn with
     `[[OPTIONS: 직접 입력 | 이번엔 스킵 | 이 루틴 항상 스킵]]`;
     항상 스킵 -> `routine-retro-skip <routine> on` (reversible `off`; this is
     the only exclusion registry, D8).
   answer -> `event-retro-set <event> "<his line>"`, say nothing.
   line holds a follow-up action -> `task-add` (no time, D5), one notice line.
   owner NAMED the project -> `task-add ... --project <that project>`; not
   named -> omit flag, never guess, never ask.
   after events block, before stage 4:
   `kit_life.sh retro-sections --lane daily --mode section` -> relay verbatim;
   empty = add nothing.
3. stage 4 `내일 + 마감` = ONE message: dashboard + tomorrow list
   (`appt-find <tomorrow>`) + summary chips + arrangement line only when the
   sequence collides + open close. NO buttons (D32).

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
