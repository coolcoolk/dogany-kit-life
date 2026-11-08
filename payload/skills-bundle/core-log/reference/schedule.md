# core-log -- schedule adapter (appointments, stays, tasks, completions)

For a matching planned completion, call task-done/task-done-today first:
the kit ensures its experience atomically, preserving the completion reply.
Capture-first for an unplanned narrated lived action: save `experience-capture
"<owner statement, verbatim>" --title "<activity>" [--on <stated day>]
[--at <stated ISO time with timezone>]` before asking about portions, amounts,
payment or sources. Unknown occurrence time stays unknown. Keep its event id
for enrichment; retry the same day/title without creating a task. Successful
specialized writes silently attach the experience; retain their existing reply
shape, with no new menus or questions from the experience layer.

Before replying after capture, apply the common SETTLE ON EVIDENCE contract
from AGENT: saving facts alone does not finish an activity report. Read
`.claude/skills-bundle/schedule-log/reference/completion-chat.md` for the
shared completion check. A PLANNED item settled this turn: its single
completion reply overrides this adapter's capture-report/card instructions.
No open task/routine matches: no completion reply at all, hand over to
reference/episode.md. Tool work stays silent until the final line.

Travel-calendar visibility requests are settings, with no plan capture:
"이동은 캘린더에 올리지 마" / "이동 캘린더 다시 켜줘" -> read
schedule-log/reference/verbs.md "schedule settings: travel calendar visibility",
then `config-set travel_calendar_enabled=0` / `=1` and its one-line reply.

Schedule management is on by default (`manage-state schedule`): the
schedule skill IS the record path. Read
`.claude/skills-bundle/schedule-log/SKILL.md` and follow it (kind gate
first: task | appointment | presence; dedup gates; people via
`person-resolve` + `block-person`; venue resolution as described there).
Judge meaning from the conversation: appointment for people / a booking / a
fixed external slot, task for self activity. Pass that judgment with
`schedule-add <title> <start> [end location purpose summary] --kind appointment|task
--attendance-mode online|in_person|unknown [--meeting-url <URL>]`.
The kit supplies matching routine structure; place or clock alone never
changes the chosen kind. Keep attendance mode as its own axis.
For EVERY plan naming a movie, show, exhibition, performance, game, book,
product, restaurant or venue, read schedule-log/reference/verbs.md "Official
titles and verified durations" BEFORE saving, even when argument order is
already known, and BEFORE replying to an existing appointment match.
Dedup selects the appointment id; it does not finish title or timing research.
A single clear match for the same plan is authorization to enrich that row:
perform the lookup and update it in the same turn, without a reuse menu.
Keep its id, stated start, venue, participant links and existing summary;
append the lookup evidence to summary. Multiple plausible plans still need
one disambiguation question. Never stop with an "already scheduled" reply.
Lookup-before-save: verify the official name, preserve the
owner's shorthand with --alias / block-alias, and use the official title.
End-time-from-runtime: look up the dated venue/format session or the work's
runtime, then supply its sourced end or --runtime-min. This overrides the
unstated-end rule for typed captures of titled movies/shows. Legacy plain
appointment calls retain their old +3h default; new captures do not invent it. Only a
failed lookup permits asking for one missing fact; never ask the owner to
check later. Keep the existing confirmation card reply.

- A completion of a PLANNED item (an open task/routine matches) the owner
  reports, or evidence photo of one:
  `.claude/skills-bundle/schedule-log/reference/completion-chat.md`
  (`task-done-today` first). NOT_FOUND after `task-find` too = unplanned "I did
  X" ("독감주사 맞았어", "영화 보고 왔어", "가영이랑 저녁 먹었어"): NEVER
  `task-add` + `task-done-today`; it is an experience -> reference/episode.md.
- Image evidence grounded in what the image shows may prove today's activity;
  follow the completion-chat evidence rules.
- A past venue is location text only; never an address or registration
  question.
- Recording never switches a day card, a brief or a routine on.

- Apply SETTLE ON EVIDENCE after every capture. An activity with no open
  match is handed to the episode adapter, never turned into a done block.
  For an unplanned text report, only owner-stated clock time (or explicit
  activity-time document/EXIF evidence) permits a past timeblock; follow
  episode.md. Message time, "today" and a meal slot never supply a start or
  duration. Plans and non-activity facts create none.

- Moving an existing task to a stated clock: use
  `task-postpone <id> "<owner time text>"` (schedule-log/reference/verbs.md,
  "defer"). With no day, choose the next occurrence after now: at 21:18,
  "9:30으로미뤄줘" means today 21:30, at 07:00 today 09:30. An explicit
  day wins: "내일 9시반" is tomorrow 09:30; "오후 9시반" fixes 21:30.
  Keep the same task id, never add a replacement. Day-only moves keep
  task-reschedule; duration offsets keep its --by semantics.

Attendance: in the same schedule-add call, pass --attendance-mode online|in_person|unknown
from your understanding of the sentence; pass a meeting URL as --meeting-url.
Platform wording stays in the venue line, never a saved physical place.
Unknown with no physically resolved location has no travel. Edits use
attendance_mode= and meeting_url=; location= removes derived travel. See
schedule-log/reference/verbs.md, "Attendance and physical venues".
