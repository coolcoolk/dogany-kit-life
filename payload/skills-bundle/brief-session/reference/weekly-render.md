# weekly retro -- rendering contract + reply copy (owner 2026-08-22)

The marker may include an `experiences` step and `workspace.experiences`.
Read [experiences.md](experiences.md) for this step before continuing the
weekly walk. It is the Sunday daily-experience block inside this weekly
retro, including the owner-authorized missing-field conversation.

Read when composing any weekly-lane turn after the rendered first message.
Scope = WEEKLY lane only. daily lane uses daily-form.md (bold titles, no
`<pre>` grid) and that wins wherever the two differ.

## weekly card -> steps (owner 2026-11-04)

- Conversation and opener-reply entry share the same close contract. If no pending
  marker exists for the reviewed week (including a conversation before the
  Sunday push), run `bash routines/bundle/weekly-review.sh --marker-only`
  first and read its marker. Do not send or repeat the start card; the owner
  has already asked to begin. Run `bash database/kit_life.sh weekly-retro-start
  <marker.week_end>` after reading that workspace. A start is not completion.
- On every successful weekly close, including a steady week with zero new
  placements, run `bash database/kit_life.sh weekly-retro-done <marker.week_end>`
  and check for `DONE` before saying the retro is finished. Use the reviewed
  week's Sunday, not next week's placement tag or today's date. This writes
  the durable agent/week fact used by the Sunday push and Monday catch-up.
  An interrupted session must not be marked done. Complete the existing
  scheduled retro task too when the session was entered through that task.
- The cron push (`routines/bundle/weekly-review.sh`) sends the weekly card:
  title, reviewed week range, optional opening sentence directly under the
  title, count line, and plan line. There is NO start button. Monday morning
  sends this same card after a missed Sunday; there is no separate reminder
  line. Marker `rendered_text` is exactly the card.
- `workspace.entry_mode=reply` waits for the owner's reply to the opening
  sentence or unchecked-count opener, then enters step 1 directly. Do not
  require a start label or ask whether to start.
- With neither an outing opener nor unchecked tasks,
  `workspace.entry_mode=automatic`: the sender posts `workspace.steps[0]`
  immediately after the card and records the weekly start. When
  `workspace.first_step_sent=true`, that first step is already awaiting its
  answer. Act on the answer, then continue at step 2; never resend step 1.
  `--marker-only` prepares the workspace without sending either message.
- A substantive reply to the opening sentence also enters this weekly
  conversation. Read `workspace.experiences.opener` before recording it:
  `kind=event` uses `retro-item-set <id> <exact reply>`; `kind=experience`
  uses `experience-enrich <id> reaction="<exact reply>"
  verbatim="<exact reply>"`. Keep that source id even when the outing was
  earlier in the reviewed week. A start/yes alone is not an outing reaction.
  Run `weekly-retro-start <marker.week_end>` and then continue the existing
  `workspace.steps` in order, one per turn;
  do not ask whether to start again. A count opener binds no individual
  task: resolve the owner's named task before writing its completion.
  The opening sentence retains the approved "this week" form on Monday;
  the existing range and other week words below keep their usual semantics.
- reviewed week = the week ending on the run day when the run is on its
  Sunday, else the week that already ended (marker `week_start`/`week_end`,
  `week_is_current`). say "이번 주" for it when `week_is_current`, else
  "지난주". never recompute from today.
- on the opener reply: relay marker `workspace.steps[0].text` VERBATIM; when the
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
- steps, in order (each only when present): `appt` (약속/사람: how an
  appointment of the reviewed week went -> `event-retro-set`), `work`
  (일: what got done and what is carried over; asks only (a) re-place
  last retro's undone placement -> `task-add` with
  `workspace.place_week_marker` and `--at`, or drop it, or (b) when to do
  a carried-over task (overdue, or set aside as 나중에), the step's
  `proposal` = {task_id, date, hm}: 넣어주세요 -> `task-reschedule
  <task_id> <date> --at <hm>`, 시간 바꾸기 -> ask the time, then the same
  verb, 넣지 않기 -> leave it as it is), `balance` (통장: only the
  accounts whose balance reading is missing or older than a week, from
  `balance-stale`; the buttons are those account names + 다음에. a tapped
  account -> ask its amount now, once; the owner's amount -> `balance-set
  <account> <amount>` (a hand reading, today). an amount given with the
  account name -> write it straight away. 다음에 -> write nothing. the
  shown 추정 is an estimate, never write it as a reading unless the owner
  says that amount is right), `goal` (목표: keep or adjust a
  goal at cadence), `next` (the week after the reviewed one as it stands
  + an open invitation; add only what the owner names, `task-add` with
  the place marker and `--at`). no slot is ever proposed for a goal or a
  project on its own. goal-engine work from reflection.md runs on the
  owner's answers, inside these steps.
- The opener reply starts the walk directly; never ask "시작할까요?" again.

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

## routine occurrences

Weekly gathering uses `appt-find --weekly` and `event-window --weekly`.
Plain recurring occurrences (including work and gym) are omitted regardless
of title or language. One-offs remain eligible, including work/gym one-offs;
explicit display and other-domain exclusions still apply. Calendar availability
continues to include routine time.

A routine occurrence is notable when it has `rec_exception` (a changed
occurrence) or a nonblank `retro_line` (an explicit account of what happened).
It can appear with a changed/skipped/noted label and its note. A cancelled
routine appears only with that evidence; automatic expiry alone is not a
reported miss. No new question asks the owner to classify a routine. The
start-card, reviewed-week dates, step order and one-question rule above hold.

Interpret the owner request before entry. Run `weekly-retro-start YYYY-MM-DD`
with the reviewed Sunday; the legacy stamp hook is inert for every wording.
Finish with `weekly-retro-done YYYY-MM-DD` and verify DONE. Owner prose never
starts or completes a weekly review on its own.

### Correct weekly completion

`weekly-retro-reopen <Sunday>` restores STARTED after an accidental DONE.
`weekly-retro-clear <Sunday>` removes the current agent/week marker;
`weekly-retro-undo <Sunday>` reverses the latest start/done/reopen/clear.
Re-read `weekly-retro-status <Sunday>`. Re-answer/clear event lines with the
same event/item correction verbs as daily; see verbs.md. The scheduled clock
is readable with `retro-schedule-show weekly`; set/clear is reversible with
`retro-schedule-undo weekly`.
