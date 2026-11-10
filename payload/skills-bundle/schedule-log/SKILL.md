---
name: schedule-log
display_name: 일정 기록
description: >-
  Put an actual task, appointment or stay on the owner's time, or look up, edit, complete, undo mistaken completion,
  defer or cancel an existing item, when the owner's intent concerns that temporal item. Entry and verb family follow
  the kind gate: task | appointment | presence; use the actual stated timing and kit state, including people names in
  scheduling context. Work-at-home, office and multi-day stays belong here. Manages kit_life.db blocks through
  kit_life.sh. Person and place are NOT handled here: they resolve as module function calls (person-resolve /
  place-resolve) and hand off to person-manage / place-manage only for a user question. Completion: read
  reference/completion-chat.md first; tools stay silent.
---

Before answering about food, cooking, timing or the rest of today, run
`"${PROJECT_ROOT:-$(pwd)}/database/kit_life.sh" day-now` and use its local clock,
day type, logged meals, next eating occasion and remaining blocks. Never ask
which meal when day-now answers it; an afternoon food request after logged
lunch targets dinner unless the owner explicitly says otherwise. Never infer
attendees from a title; use only stored people/groups. Historical or explicitly
dated requests keep their stated date and meal; day-now supplies current context.


# schedule-log

Execution axis: register, update, query, settle, cancel anything that
occupies the user's time. One `block` table, `kind` column = task |
appointment | presence; this skill decides kind, never asks it as such.
`kind` != `schedule_kind` (timed | all_day = time shape). keep the two apart.

All via `LKIT="${PROJECT_ROOT:-$(pwd)}/database/kit_life.sh"` (cwd =
workspace root). never raw sqlite. times in user's local tz (Asia/Seoul).
Signatures, output shapes, time-shape rules, error list: read
reference/verbs.md when you need an arg order or an error's meaning.

## Context before person or gift answers

First run `kit_life.sh noun-context "<current message + known recipient/occasion from this conversation>"` using `${PROJECT_ROOT:-$(pwd)}/database/kit_life.sh` from the agent workspace. Read PERSON / PERSON_REL / PERSON_EVENT and EVENT before answering or asking who/where/when; no EVENT does not mean no person facts. An EVENT line answers who/where/when: take it, ask nothing; ask only with no EVENT line or two differing EVENTs ("which one").
An unresolved nickname: `person-resolve`, then repeat noun-context with `--span "<resolved canonical name>"`; NEAR / AMBIGUOUS is not a resolved person. For missing details use `person-show <id>`. Unresolved span -> search agent memory BEFORE noun-tag or any typing ask. Memory knows it -> propose remembered kind/name/members for one-tap confirm, then land via group-add / person-add / group-member. Type ask only after a completed 0-hit search; unavailable search != 0 hits. Kit facts win conflicts.
Kit person facts override agent memory. A memory fact phrased as a future expectation whose date has passed must not be asserted: check the kit, or ask once if still unknown. A recorded child birth is past, never an upcoming delivery; do not infer medical/recovery facts.
Product "찾아줘" / "다 찾아줘", gift purchase sources, and packaging searches go straight to shopping-session's search flow and concrete product cards, including follow-ups without a repeated recipient. Never answer those with generic advice or an offer to search later.

## Completion reported in chat

Owner says task/routine done, or sends any evidence of an activity done today (a photo, a sheet, a record just captured) -> read reference/completion-chat.md; `task-done-today` with that evidence (`--record-evidence` / `--photo-evidence` / `--said`) BEFORE any "recorded" reply. Message is exactly a start-card button (미루기 / 도움받기 / 완료 / 취소 / a snooze choice) -> read reference/start-card.md; `start-tap` FIRST.

## 0. reading a day (FIRST)

"오늘 할 일" / "오늘 일정" / any day's list -> `day-card --today [--date <d>]` (`event-day`
ends with the same card). reply = that card VERBATIM, at most one own line on top.
never build a list from rows: travel / buffer are not items.

## 1. kind gate (FIRST, before time-shape parse)

- presence: core content = WHERE the user will BE for a span (stay, lodging,
  overnight/multi-day trip, WFH, family home, office hours, business trip
  with no meeting named).
- appointment: meeting people, a booking, or a fixed external slot. A self
  activity at a venue is still a task; place and clock alone do not decide kind. A social or booked dinner is an appointment; "at <PLACE> all
  day Friday" with no event verb = presence.
- task (default): a commitment to DO; "did I do it" is meaningful. may carry
  a clock time (shape, not kind). keep only what he said: day; clock time ONLY
  if given; "퇴근길"/"저녁" stays a word, never a time. no travel math / "빠듯해요 / 옮길까요" unless asked.
- order: presence cues -> appointment cues -> task.
- errand (pick up / return / drop off at a place, no booked time) = a task
  with a deadline, never an appointment. exit 7 ERRAND_* = not an
  appointment -> run the printed line / ask its one question: reference/errand.md.
- ambiguous presence/appointment -> ONE question, examples on BOTH sides
  ("staying at a place for a while, like <PLACE> / WFH, or attending
  something, like a meeting / dinner?"), never "being somewhere vs doing
  something". For unclear task/appointment meaning, ask only a missing owner
  fact; never ask the owner to choose a kind.
- Pass your judgment: `schedule-add ... --kind appointment|task --attendance-mode online|in_person|unknown`. The kit
  accepts it; matching task routines supply duration/place/notification policy.
  `--kind auto` only for genuinely unresolved meaning; use context to resolve
  its SCHEDULE_NEEDED kind silently. Ask only a missing owner fact, never kind.
  For a self activity with only a day, `task-find <day>` first: reuse its
  matching routine occurrence without inventing a clock. No occurrence ->
  `schedule-add <title> <day> --kind task`.
- shape detection still runs after the gate. Attendance mode is independent;
  pass both judgments in the same call; --meeting-url carries a stated link.

Mirror routing is code's: 시각 있음 -> 구글 캘린더, 시각 없음 -> 구글 태스크.
never tell the user kind decides the surface; never file a stay as a task to
reach the calendar (reference/verbs.md "mirror routing").

## 2. person / place = module calls, not this skill

- `place-resolve <name>`: RESOLVED -> use name as-is, say nothing;
  VIA_ANCHOR -> use field 3 (real place), say nothing; NEAR_MATCH -> ask its
  ASK line; yes -> `near-match-accept place <ref> "<name>"` and use the
  existing name; no -> `near-match-refuse place <ref> "<name>"` then as
  ABSENT; ABSENT -> appointment venue: no hand-off, §3 step 2; presence
  place / `task-add --place`: place-manage (it asks), come back. never ask
  whether to register a place ("등록해도 될까요?" has no source). write
  verbs take the NAME and resolve exactly: pass the echoed string, never a
  prettier one
  (untaught nickname = duplicate place). a write verb answering NEAR_MATCH
  (exit 5, e.g. `task-add --place`) = same question, then re-run it.
- `person-resolve <name>`: RESOLVED -> `block-person <block_id> <id>...`
  (appointment, presence or task; `appt-person` = old alias). NEAR_MATCH ->
  ask its ASK line ("혹시 김영희랑 같은 분인가요?"); The number after NEAR_MATCH is the candidate COUNT, never an id; the id is the first column of each candidate row below it. yes ->
  `near-match-accept person <ref> "<name>"`, link <ref>; no -> person-manage
  skill as ABSENT. AMBIGUOUS / ABSENT -> person-manage skill (it asks /
  registers), come back with an id. never `person-add` here. "영희님이랑
  최근에 뭐 했지" -> `person-blocks`, person-manage skill's question.
- companions named in the request ("엄마아빠랑") are linked in the SAME turn,
  any kind, never as a memo -- whose occasion it is (the birthday grandmother
  visited) included. never re-file a stay as an appointment to carry people.
  a stay filed as an appointment to carry people -> reference/verbs.md
  "stay participants" (migrate it).
- names in the utterance -> `noun-tag "<utterance>" --span <NAME>...
  [--field appt.room|appt.participants|appt.place --place <PLACE>]` first;
  fill fields from typed spans (refs unchanged). TYPE_ASK -> apply
  TYPE_RELAY's memory gate first; ask only after a 0-hit search, answer = its NEXT line. exit 8 FIELD_KIND_REFUSED: your
  own guess -> move it where NEXT says, retry silently; owner's words ->
  relay SAY (+ ROOM_ASK buttons). a group (band, club) -> `appt-group <id>
  <group>`, never a room or a person. never infer a room from a word ending.
- any `*_ASK` with an ASK_OPTIONS line: the question verbatim, then that
  `[[OPTIONS: ...]]` line verbatim; a typed answer still counts.
- resolved path loads no other skill.

## 3. appointment

0. DEDUP GATE first, before any question: `appt-find <date> <date+1>`.
   existing -> show, `[[OPTIONS]]` merge/update vs separate new. write only
   on 0 matches or explicit "new".
1. parse time relative to now (local). start required; timed appointment end
   omitted and no sourced runtime = start +3h (owner 2026-10-01). Leave the
   end argument empty so the kit applies its default; task/self-activity
   captures keep an unknown end when no duration is known.
2. Judge attendance independently and pass it in the same capture call.
   Online: keep the owner's platform wording as location and a stated link as --meeting-url;
   skip physical place resolution/registration and never hand-make travel.
   In-person/unknown physical venue -> `place-resolve` first. ABSENT = pass the venue TEXT as
   location, say nothing about it (no place-manage / address / register
   question). past event: ask only what the verb needs (a start time);
   future: address only via `TRAVEL_PLACE_NEEDED` -> reference/travel-leg.md
   "venue handling".
3. `schedule-add <title> <start> [end location purpose summary] --kind appointment --attendance-mode online|in_person|unknown`.
   output has `TRAVEL_PLACE_NEEDED` -> read reference/travel-leg.md.
   output has `PLACE_UNSET` -> follow PLACE_RELAY: never in a reply carrying an [[OPTIONS]] menu for another question, not repeated on consecutive replies; 미정 = `appt-upd <id> location=미정` (unchanged).
   SCHEDULE_NEEDED kind -> resolve from context and retry with --kind;
   never relay the refusal or flags to the owner. Reservation named -> --booked.
4. persons -> `person-resolve` each -> one `block-person` call.
5. edit: `appt-upd <id> field=value ...`. `location=` re-runs auto-attach
   and may emit `TRAVEL_PLACE_NEEDED` (same handling). the booking's room
   (A2룸 on a booking screen) = `room=<ROOM>` (appt-add too), never in
   location / title. owner evidence for THIS booking (statement /
   screenshot) overwrites the stored value: write it (REPLACED), never ask
   which is right; ask only when two sources in the CURRENT turn disagree.
6. verify `appt-show <id>`, report.
7. owner states an arrival margin/buffer -> `buffer-set|show|undo`, read reference/verbs.md
   "appointment arrival margin"; relay confirmation verbatim; never ask unprompted.

## 4. presence (verb enforces one live presence at a time; no dedup step)

1. span: daytime stay (office / WFH) REQUIRES hours -> ask "몇시부터 몇시?"
   if missing, write timed, same day. overnight / multi-day -> date-only,
   UNLESS times were stated: keep them (verbs.md "stay span example").
2. title: daytime -> activity/context first (place is its own arg);
   overnight/travel -> place first ("<PLACE> <trip-context>").
3. `place-resolve` the place (ABSENT -> place-manage). never ask minutes here.
4. `presence-add "<title>" "<place>" <start> <end> [--new]`. a stay right
   after an appointment = a fact: write it, no confirm -> verbs.md "a stay
   after an appointment"; ask only its PLACE, once.
   same-title/date -> blocked unless `--new`. `PRESENCE_CONFLICT <n>` ->
   show rows, `[[OPTIONS]]`: adjust new / move-cancel existing / abandon.
   never coexist two.
5. companions (§2) -> `block-person <id> <pid>...`; verify `event-day`.
- move: `presence-move <id|ulid> <start> <end>` (same shape family).
- cancel ("안 가게 됐어"): confirm first -> `presence-cancel <id|ulid>`.
- a presence is never "done".

## 5. task

- done ("X 했어", planned/recurring task): `task-done-today "<title-or-alias>" --or-overdue`;
  relay its printed status: ASK -> ask which, re-run with `--id`; NOT_FOUND ->
  not a task, never create one (reference/completion-chat.md NOT_FOUND). undo: `task-undone`.
- add: `task-add <title> [due] [note] [--at HH:MM] [--place N]`; `--place`
  runs travel auto-attach -> `place-resolve` first. report one line.
  an errand: `task-add <title> <date> --place <place>`, no --at; reply
  names the deadline only, never a buffer -> reference/errand.md.
- defer: `task-reschedule <id> <YYYY-MM-DD>` moves DATE only; flags, untimed warn
  -> reference/verbs.md "defer". confirm WITH time: "내일(8/25) 19시로 옮겼습니다".
- relative defer ("N시간/분 미뤄"): new start = the item's ORIGINAL start + N, never now + N;
  `task-reschedule <id> <date> --at HH:MM`; confirm with the resulting clock time; a result earlier than now -> ask, never land on it.
- planned workout day (오늘 헬스 / split "미룰래" "내일로"): NOT task-reschedule ->
  `workout-defer --from <day>`; relay DEFER_ASK + [[OPTIONS]] only; yes -> same
  args + `--apply`, DEFER_CARD verbatim; DEFER_REFUSED never claims a move.
- archive: confirm first -> `task-archive <id>`.
- overdue: `task-overdue` -> simple list "title (due)"; empty -> say nothing
  is overdue. never auto-close.

## 6. query

- "this week's appointments" -> personal appointments only; drop recurring
  work/commute blocks and presence rows (appt-find returns presence too).
- a day's list ("오늘 할 일", "what do I have today") -> §0.
- cancelled (abandoned) never listed.
- tabular -> code block; no bold; address per persona (AGENT_ADDRESS in
  config/agent.conf, else locale default).

## boundaries

- own-data done/undo/add -> no extra confirm; ambiguous -> ask.
- appointment cancel, presence cancel, task archive -> confirm first.
- travel rows (이동/약속 버퍼; `event-day --with-derived` ids) are the
  kit's: never offered to cancel, move or confirm.
- this skill writes the DB; the mirror carries it. never offer a separate
  "add to calendar", never call a row local-only.
- no verb for it -> "아직 저장 못 해요, 업스트림에 보고" -- never offer "직접 수정".
- a duplicate place you created (its name = another place's display name /
  alias) -> place-manage merge (`place_merge.py --keep --said`, its
  merge reference); never say you cannot remove it.
- people -> person-manage. places, addresses, rooms, merges -> place-manage.
  meaning after the fact -> episode skills. this skill owns the time.
