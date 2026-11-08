# Routine ends, clashes, calendar deletes

Read before any `routine add` / `routine update`.

- **Every interactive add passes `ask=1` and has an end decided in this same turn.** Look at the owner's
  own words first:
  - names an event ("공연 전까지", "시험까지", "대회 전까지") ->
    `until=<words of that event's title>` (the routine ends the DAY BEFORE
    the event; `until=day:<words>` only if the owner says to include the
    event day). The link is stored: if the event moves, the end moves.
  - names a date or length ("연말까지", "한 달만", "3주") ->
    `until=YYYY-MM-DD` or `until=+1m` / `+3w`.
  - says nothing about an end -> run the add with `ask=1` WITHOUT until=/end=/open=. The
    kit registers nothing and prints `END_ASK` (+ `ASK_OPTIONS`,
    `END_ASK_RELAY`). Say only the question line, put the buttons marker
    last. A related upcoming event is proposed ("11/14 공연 전날(11/13)까지로
    할까요?" [11/13까지] [계속]); with none the question is "끝나는 날이
    있어요?" [계속] [날짜 정하기]. On the tap re-run the same add:
    [11/13까지] -> `until=<END_ASK 4th field>`, [계속] -> `open=1`,
    [날짜 정하기] -> ask the date, then `end=YYYY-MM-DD`.
  - Never invent an end, never put the event day in `end=` by hand (the
    routine would sit on the gig day), never ask this as a separate
    interview turn.
- `routine update <def> until=...` re-points the end the same way;
  `end=YYYY-MM-DD` unlinks it from any event.
- **Clashes.** A routine occurrence never sits on an appointment (or its
  travel / buffer / prep blocks): the kit moves it to the nearest free slot
  that day, or skips the day, and prints `ROUTINE_CLASH <line>` once (after
  `routine add`, after `schedule-add ... --kind appointment` (legacy `appt-add` too), or `routine clash-notes`; the morning day card also
  shows one line per affected occurrence on that day). Say that line
  as written. The appointment is never moved; do not offer to move it.
- **Calendar deletes.** An occurrence the owner deletes in the calendar is a
  skipped day, not re-created. When none is left the kit queues one
  `RETIRE_ASK` (`routine retire-asks` prints it, once; pending questions also appear on
  the morning day card without consuming them, with Retire/Keep pin buttons): relay it with its
  buttons; [끝내기] -> `routine retire <def>`.

When the routine conversation also adds a one-off schedule item:

The agent judges kind from the conversation before writing. Pass
`--kind appointment` for meeting people, a booking, or a fixed external slot;
pass `--kind task` for a self activity. Use `schedule-add <title> <start>
[end location purpose summary] --kind <judgment>`. A matching task routine
supplies its duration/place/notification policy. An unstated end stays unknown;
never invent three hours. `--kind auto` is only for genuinely unresolved
meaning; exit 9 `SCHEDULE_NEEDED kind` tells YOU which typed judgment to pass.
Resolve that from context and retry silently; do not relay kit flags or a
classification refusal to the owner. Ask only a genuinely missing owner fact.
Kind and attendance mode are independent; preserve attendance when supplied.

## Correct an accidental retirement (2026-11-06)

`routine unretire <def-id|ulid>` reactivates the same retired definition,
restores its future `routine-retire` cancellations, and materializes its current
horizon. Past history, owner-cancelled and completed occurrences stay as recorded;
presence overlaps are refused. An independently retired travel rule stays off.
The inverse is `routine unretire-undo <def-id|ulid>`; it refuses intervening
changes to the definition or the affected occurrences. Undo retires new
occurrences materialized during revival instead of deleting their history.
This differs from `routine resume`, whose paused-only contract is preserved.
Use the existing mutation cards; no new confirmation copy is needed.
