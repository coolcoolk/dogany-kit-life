# weekly retro -- rendering contract + reply copy (owner 2026-08-22)

Read when composing any weekly-lane turn after the rendered first message.
Scope = WEEKLY lane only. daily lane uses daily-form.md (bold titles, no
`<pre>` grid) and that wins wherever the two differ.

## start card -> steps (owner 2026-10-04, same shape as the daily retro)

- the cron push (`routines/bundle/weekly-review.sh`) is a START CARD only:
  title, the reviewed week's range, one count line, the plan line,
  "주간회고 시작할까요?" and ONE button `주간회고 시작`. The bridge replays the
  tapped label as the owner's message; a typed yes ("네", "시작") to that
  card means the same. Marker `rendered_text` = that card.
- reviewed week = the week ending on the run day when the run is on its
  Sunday, else the week that already ended (marker `week_start`/`week_end`,
  `week_is_current`). say "이번 주" for it when `week_is_current`, else
  "지난주". never recompute from today.
- on the yes: relay marker `workspace.steps[0].text` VERBATIM; when the
  step has `options`, attach them as `[[OPTIONS: a | b]]`. after the
  owner answers a step, act on the answer (verbs below), then relay the
  next step. ONE step per turn, in order, never merged, never re-worded.
  `workspace.steps` holds one message per question: an area with
  nothing to show is not there, so do not narrate it ("기록 없음" never
  appears); an area with something to show but nothing to ask (the
  spend chart, a work block with no decision) already sits on top of
  the next step's text.
- what a question may ask (2026-10-05; the weekly never makes the owner fill fields):
  a look back on the week, or a decision for the next one. never ask the
  owner to classify, attach, label or fill a field: which project a task
  belonged to, whether a spend recurs, who a meal was with. this holds
  for your own follow-ups too, not only the rendered steps.
- steps, in order (each only when present): `appt` (약속·사람: how an
  appointment of the reviewed week went -> `event-retro-set`), `work`
  (일: what got done and what is carried over; asks only (a) re-place
  last retro's undone placement -> `task-add` with
  `workspace.place_week_marker` and `--at`, or drop it, or (b) when to do
  a carried-over task (overdue, or set aside as 나중에), the step's
  `proposal` = {task_id, date, hm}: 넣어주세요 -> `task-reschedule
  <task_id> <date> --at <hm>`, 시간 바꾸기 -> ask the time, then the same
  verb, 넣지 않기 -> leave it as it is), `goal` (목표: keep or adjust a
  goal at cadence), `next` (the week after the reviewed one as it stands
  + an open invitation; add only what the owner names, `task-add` with
  the place marker and `--at`). no slot is ever proposed for a goal or a
  project on its own. goal-engine work from reflection.md runs on the
  owner's answers, inside these steps.
- the start card already asked its one thing; never ask "시작할까요?" again.

## visual grammar

House conventions (`.claude/skills-bundle/workout-session/reference/progress-tables.md`,
health pack):
- bracket section headers, `·` bullets.
- ASCII-space column padding, CJK = 2 cells, ~40-cell width in code block.
- at most ONE `<pre>` per message (the spend grid, `spend` block only).
  rest plain text.
  no markdown tables, no `**bold**`, max one emoji.
- bars: `█` (U+2588) filled, `▒` (U+2592) empty, constant glyph count.
  never `░` (U+2591) when anything renders right of the bar (EAW width
  mismatch jags the column).
- DB title spliced into sentence -> particle agrees with last syllable
  (`life_dashboard._josa`).
- follow-up turns keep same area headers + one-question discipline.
- no internal words in owner copy ("엮기", "앵커", "weave"), no answer
  instructions in parentheses ("(없으면 '없음')", "(예 / 시간 바꿔 / 안 함)"):
  choices are buttons.

## reply copy

- brief, everyday terms. no DB ids, table names, verb names, ulids.
- placement confirm: "[날짜(요일)] [시각]에 [행동] 넣어둘게요."
- executed: "[행동] 다 하셨네요. [brief observation]."
- not executed: "[행동]은 결국 못 하셨네요. [observation + open question]."
- retire: "못 이루는 목표는 지금 내려놓고 나중에 다시 켤 수 있어요."
- downgrade: "현실적인 선으로 낮춰볼까요? 나중에 다시 올리면 돼요."
- weaving promotion (rare): "이제 제대로 가보실래요?"
