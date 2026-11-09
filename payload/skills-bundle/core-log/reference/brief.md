# core-log -- brief adapter (finished work, decisions, day evidence)

Before replying after capture, apply the common SETTLE ON EVIDENCE contract
from AGENT: saving facts alone does not finish an activity report. Read
`.claude/skills-bundle/schedule-log/reference/completion-chat.md` for the
shared completion check. A PLANNED item settled this turn: its single
completion reply overrides this adapter's capture-report/card instructions.
No open task/routine matches: no completion reply at all, hand over to
reference/episode.md. Tool work stays silent until the final line.

- Work finished ("finished the report") -> the schedule adapter's
  completion rule when a PLANNED item matches (`task-done-today`). No open
  match -> never `task-add` + done: record the work as a decision/outcome the
  way this adapter records work (below); a lived activity (unplanned "I did
  X") -> reference/episode.md; otherwise one short confirm line.
- A decision taken in a meeting / event that is on today's schedule
  (`event-day <today>` shows it) -> `event-retro-set <event_id> "<the
  decision, owner words>"`. No such event -> `experience-create "<meeting,
  owner words>" other "<the decision, verbatim>"`.
- Several facts in one message -> each one, one confirm line for all.

Never: start a daily / weekly review, a goal judgment, a placement, a
module / pack introduction or a promotion offer from a capture.
