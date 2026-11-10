# Completion reported in chat

For a matching planned completion, call task-done/task-done-today first:
the kit ensures its experience atomically, preserving the completion reply.
Capture-first for an unplanned narrated lived action: save `experience-capture
"<owner statement, verbatim>" --title "<activity>" [--on <stated day>]
[--at <stated ISO time with timezone>]` before asking about portions, amounts,
payment or sources. Unknown occurrence time stays unknown. Keep its event id
for enrichment; retry the same day/title without creating a task. Successful
specialized writes silently attach the experience; retain their existing reply
shape, with no new menus or questions from the experience layer.

Read when the owner says a task/routine is done, or sends any evidence of today's completion.
A bare button text from a start reminder (미루기 / 도움받기 / 완료 with no token) is a start-card tap: reference/start-card.md (`start-tap` first), not this file.

Capture-turn precedence: finish the semantic completion check, including the
no-open-match handover to the episode adapter, before replying. Pending onboarding, goal
consultations, setup gates and post-tool followups are deferred for this turn
unless the owner explicitly requested them. Do not run a pending consult or
let its question replace the completion line. This rule overrides capture
report/card and automatic consult instructions.

## Completion reported in chat (mandatory write)

When the owner says a task/routine is done (for example "말해보카 했어"),
call `kit_life.sh task-done-today "<exact title or registered alias>"`
BEFORE acknowledging that it is recorded. A photo/app screen goes through
"Evidence of today's completion" below. Do not stop at congratulating them or
keep completion only in chat/memory. An early completion still settles
that day's future block.

Late completion (done after start + duration + 30 min): the verb records it
done now at the scheduled time and the output ends with an `[[OPTIONS: ...]]`
line (the existing [around HH:MM] [just now HH:MM] choice) after the card --
relay card then that line, nothing else; the owner's tap comes back as
`start-tap`. If the owner already said when in the same message ("방금 했어",
"아까 11시에 했어"), pass `--when now` or `--when HH:MM` (24h local): it is
recorded directly and no choice row is printed. `task-done <id>` takes the
same `--when`.

Read the JSON status: DONE = recorded; ALREADY_DONE = already recorded,
do not write again; ASK = two or more open matches, present their local
start times and ask which one (nearest is listed first, never auto-pick).
After the answer, call the same verb with `--id <selected id or ulid>`.
NOT_FOUND = no matching block today: `task-find "<keyword>"` for another day's
open instance the owner means; never quietly settle yesterday/tomorrow. Still
no open task/routine matches and the owner reports an unplanned experience
(such as a vaccination, a movie or dinner) -> it is an experience, not a
task completion: NEVER `task-add` + `task-done-today`, no "완료" wording. Hand
over to core-log/reference/episode.md (curious follow-up); the episode adapter
owns the reply. RETRY = re-read by
calling the same verb; never claim completion before DONE/ALREADY_DONE.

Every successful completion, including `--photo-evidence` and
`--record-evidence`: a new DONE
prints the completed task's card after the JSON line (CARD_BEGIN ... CARD_END,
state line `✅ 완료`, then the date header and the row). Your whole reply is
that returned card: relay the returned card exactly once, verbatim, with no
success line, sentence, button or second copy of your own, and never print
the CARD_* marker lines. No card block (an undated task, ALREADY_DONE, ASK,
NOT_FOUND, CONFIRM, RETRY) = nothing to relay; follow the status rules above.

Use an exact title or an owner-confirmed alias. To remember an explicitly
confirmed alias, call `kit_life.sh task-alias "<exact title>" "<alias>"`.
Do not infer aliases from a similar name. Non-today completion uses the
existing date lookup and task-done for the owner-confirmed instance.

## Any evidence of today's completion

Activity priority: every input during an activity belongs to that activity
first. On a photo turn `task-open-today --photo` also returns
`ACTIVITY_IN_PROGRESS` / `route: task-activity` for a started task inside its
window (through end + 15 minutes). Inspect and answer the photo, including
an implicit question without a caption. Cooking prep is not an eaten meal.
Do not offer completion buttons, create another activity, or announce done.
`task-done-today --photo-evidence` refuses this with the same status and a
machine `next` instruction. Only a clearly visible finished end-state of the
whole matching task permits `--photo-evidence "<observed finished end-state>"
--photo-end-state`; ingredients and intermediate steps never qualify.
An end-state assertion cannot override an open workout session.
`ALREADY_DONE` has an empty `reply`: answer the photo about the activity,
without an already-done or completion announcement.

Open workout session takes precedence on a photo turn. Run
`task-open-today --photo` before applying completion: `SESSION_OPEN` /
`route: workout-session` means inspect the image in the current session
(machine/stack registration, food, form), keep the task open and answer the
session request. Do not offer done buttons, create a done activity block or
send a completion line. This also applies when the image resembles a done
screen. After session close the ordinary photo rules below resume.
`task-done-today` / `task-done` / start-card Done also refuse to complete a
workout task while its owning agent has an open session. `SESSION_OPEN`
means continue that session; it is not a request for completion proof.

After any capture or statement read `OPEN_TODAY`; if absent run
`kit_life.sh task-open-today`. Capture all facts before settling once.
This works in every kit agent even without hooks. On a photo turn use
`task-open-today --photo` (hook re-arming is harmless). Its JSON has both
`open` and `done`; compare the actual evidence to titles and aliases by
meaning, never by a task/app/metric keyword list.

- Exactly one fulfilled row -> settle with `task-done-today "<stored title>"
  --record-evidence "<verb>:<row id>"` for a just-written dated activity,
  `--photo-evidence "<observed evidence of today's activity; visible text
  verbatim>"` for any image, or `--said "<owner words, verbatim>"`.
  A sheet, food, receipt, or other activity evidence need not display a
  completion banner. The photo assertion must be nonempty and more than
  the task title alone. The agent must judge the activity and date from
  the evidence, never manufacture proof. DONE -> relay the returned card exactly once, verbatim, as above.
  No CARD block -> use the JSON `reply` line.
- Two or more fulfilled candidates -> `task-open-today --photo --offer
  <id>[,<id>]`, all candidate IDs, even with different titles. The kit posts
  one question with done buttons. `offer.posted` true -> do not repeat it;
  false -> present one question with the candidate done buttons yourself.
- None fulfilled -> do not mention unrelated tasks. Something the owner did
  or underwent is an unplanned activity even when captured as several facts.
  Check the `done` rows first (already settled -> ALREADY_DONE path). No open
  task/routine matches -> NEVER `task-add` + `task-done-today`, no completion
  line: hand over to core-log/reference/episode.md (experience + one curious
  follow-up question). When the time is known the episode adapter attaches a
  past timeblock only from owner-stated clock time or explicit activity-time
  document/EXIF evidence, following episode.md. Message time, "today" or a
  meal slot never supplies a start/duration. Non-activity facts (birthday,
  price, preference), plans and historical evidence create nothing here. This
  same rule applies to statements and every capture.

Record proof requires a kit activity row written in this turn by this
agent instance. Yesterday's activity, a previous turn, deleted/changed
rows and missing turn identity return CONFIRM; never retry with invented
proof. Corrections, undated containers, plans, catalog size measurements,
and relationship-sidecar rows list candidates but issue no activity receipt;
use the actual photo or owner statement. EXISTS is not a new capture but
still requires checking completion. task-add itself is never proof.

After an offer, settle only on the owner's next message:
- a tap arrives as the button's text (e.g. `완료`; the confirm code rides
  the button's hidden data) -> `kit_life.sh task-done-today "<title>" --tap
  "<that text, verbatim>"` (the tap picks the instance; no `--id`).
- the owner types it (`응 했어`, `완료`) -> `--said "<their words, verbatim>"`.
  In the photo turn itself `--said` passes only when it is a verbatim piece
  of the owner's own caption of that photo naming the task (`헬스 끝냈어`);
  anything else returns `CONFIRM` (`SAID_IN_PHOTO_TURN`).
The gate holds only in the photo turn: a later owner message (a typed
report, a workout log's auto-close) settles on the plain chat path.
Handle the JSON as in "Completion reported in chat"; never say it is
recorded before DONE/ALREADY_DONE. `ACTIVITY_IN_PROGRESS` = answer the activity photo, never ask for completion.
`ALREADY_DONE` = answer the activity, no completion message.
`CONFIRM` = no proof yet: ask, never
retry with invented words. Never invent `--tap`/`--said` words or a record reference. Photo assertions
must be grounded in the image, with visible text verbatim where present. Never register an alias from a photo;
aliases come only from the owner's explicit confirmation through `task-alias`.

Keep capture and verification tool work silent: no intermediate progress
messages: every text block reaches the owner as its own message, so write
no text before or between tool calls (no start, progress or reasoning line).
Send the returned completion card only after all tool work is done.

Reopening (`task-undone`), adding (`task-add`), changing (`task-reschedule`,
`task-postpone`, `task-retitle`) and removing (`task-archive`) a scheduled
task also return the state + time-block card. Relay the CARD block exactly
once; the machine TSV/JSON line is not the confirmation.

Routine definition add/update/pause/resume/retire and routine exception/insist
use the same returned state + time-block cards for their scheduled instances.
A successful pause/retire shows the cancelled instances before their removal.
