---
name: routine-manage
display_name: 생활 루틴
description: >-
  Register, edit, list, pause/resume, and retire recurring routines in the
  kit_life ledger. Fires when the user says things like "register a
  daily 6am run", "set up a Mon/Wed/Fri morning workout", "remind me to take
  supplements every other day", "add weekday morning stretching", "add my
  weekday office routine", "set up a work-from-home routine", "add/make a
  routine", "show my routines", "what routines do I have", "change the
  routine time", "adjust the frequency", "stop/retire this routine",
  "pause/resume the routine", "when is my routine this week", "show routine
  adherence/health", "회사 루틴 추가", "재택 루틴". Handles register (cadence
  parse + meta inference +
  notification question + question-form confirm), edit (rule change vs
  this-instance-only exception), list/status, health query, retire. Writes
  through kit_life.sh routine verbs only (SoT=sqlite; mirror rides the
  outbox). Final output = registration echo with this week's occurrence
  dates.
---

# routine-manage -- recurring routine register / edit / retire

Own the routine_def lifecycle conversation (DGN-240). every write =
`kit_life.sh routine <verb>`; never write event rows or DB by hand, never
create GCal/GTasks entries (outbox owns the mirror). Tier: BEST-EFFORT.
Cadence strings + notify mapping: read reference/cadence-notify.md when
parsing a cadence or a notify answer.

## register

0. kind, three-way:
   - HEALTH (fitness / workout / gym / diet / meal-plan / nutrition) and the
     estate mounts a health-trainer module -> NOT this skill's. do not write
     it here, do not send the user to that agent. relay the utterance per
     `database/crew-agents.md` "lane-edge rule" (test the relay, check
     "handoff lane status"; slug = health peer's mailbox slug = lowercase
     basename of matching `HANDOFF_PEER_*` root in config/agent.conf), then
     say only that it was handed over, and STOP. send fails -> say the
     handover failed, stop. lane down -> say it is the health peer's and the
     handover path is not available here, stop. never `decision.notice` (it
     only lands in a log). no health module mounted -> proceed normally.
   - STAY (presence): subject names a place the user occupies for the span
     (registered place, or plainly location-shaped: 회사, 집, 사무실, 본가, a
     trip destination) -> presence, NO question ("회사 루틴" = this).
   - ACTION (task): a thing to do at a time (default for activities).
   - genuinely unclear -> ask with examples on BOTH sides ("stay-at-a-place
     routine like 회사 / 재택 / 여행, or thing-to-do routine like 운동 / 약 /
     공부?"), never "being at a place vs doing something".
1. cadence -> reference/cadence-notify.md. unparseable -> ONE clarify
   question with the 2 nearest readings.
2. meta, NO interrogation:
   - area: pass `area=<name>` only when it exactly matches an existing area
     (verb errors "영역 없음" otherwise); unsure -> omit.
   - project: `project=<id|title>` only when confident; else omit.
   - purpose: goal phrase from utterance/context; none -> ride the confirm.
   unresolved after confirm -> register NULL; retro backfills.
3. confirm = ONE question total, notify included: restate what the user
   said, ask what you inferred, always state the notify default (or echo
   theirs) and invite adjustment.
4. write after OK:
   `kit_life.sh routine add "<title>" "<cadence>" time=HH:MM duration=N area=<name> project=<title> purpose="<one line>" [end=YYYY-MM-DD] [notify=...]`
   - `EXISTS n` (exit 3) = same-title active routine -> show it, ONE
     question: modify existing or `--new`.
   - success prints materialized dates -> reply with THIS WEEK's dates.
   - today included for all_day, and for timed only if the time is ahead
     (engine rule).

Presence (STAY) instead of steps 2-4:
- place required (utterance, else ask). `place-resolve` it; NEAR_MATCH ->
  ask its ASK line (yes -> `near-match-accept place <ref> "<name>"`, use that
  place; no -> `near-match-refuse ...`, then as ABSENT); ABSENT ->
  place-manage (address at most once, never minutes first). known place ->
  never re-ask. `routine-add ... place=` / `presence-add` answering
  NEAR_MATCH (exit 5) -> same question, then re-run.
- daytime stay (회사 / 재택, the common case): ASK hours ("몇시부터 몇시?")
  -> timed, `duration` = end - start minutes (09:00-18:00 -> 540).
  overnight (여행 / 본가 across nights) -> all_day dates. recurring
  workplace/home stay defaults to daytime-timed.
- no notify question (presence carries no alerts). confirm = place +
  cadence + hours.
- `kit_life.sh routine add "<title>" "<cadence>" kind=presence place=<name> time=HH:MM duration=<min>`
  or all_day: `... kind=presence place=<name> [start=YYYY-MM-DD] [end=YYYY-MM-DD]`.
- reply with this week's stay dates.
- appointment-shaped routine -> verb refuses; say so, offer a plain
  repeating appointment discussion.

## edit (ask which when ambiguous, 1 question)

- this instance only: find its ulid with `kit_life.sh event-window` ->
  `kit_life.sh routine exception <event_ulid> date=YYYY-MM-DD` (date-only
  move, keeps shape) | `start_at=... end_at=...` | `title=` / `note=`.
- rule change: `kit_life.sh routine update <def_ulid> time=07:00 [effective_from=YYYY-MM-DD]`.
  conversation edit -> omit effective_from (today). retro decision -> next Monday.
- routine alerts: `routine update <def_ulid> notify=silent|start_only|<lead-min>`
  (empty = default). engine re-stamps only roller-made, non-exception future
  instances; hand-moved ones (rec_exception=1): list via `event-window
  <today> <+28d>`, apply `event-notify <event_ulid> <policy>` each.
- one instance's alert: `kit_life.sh event-notify <event_ulid> silent|default|start_only|<lead-min>|''`
  (works on one-off tasks and appointments too).

## lifecycle

- `routine pause <def_ulid>` (future instances withdrawn) /
  `routine resume <def_ulid>` (window refreshed, re-materialized).
- `routine retire <def_ulid>`: cancels ALL future occurrences incl.
  hand-moved. confirm first (undo = re-register).

## query

- `routine list [active|paused|retired]` -> `id ulid title cadence
  schedule[:time] status retro_skip`. `retro_skip=1` = retro never asks;
  NOT paused, not a lifecycle state. the retro owns that flag.
- `routine show <def_ulid>` (KEY=VALUE incl. notify_policy, notify_lead_min, retro_skip).
- `routine health [window=28]` -> JSON done/missed/skipped/rate/anomaly.
  ad-hoc asks get a short human summary, never raw JSON.
