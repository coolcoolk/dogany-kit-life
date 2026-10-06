---
name: schedule-log
display_name: 일정 기록
description: >-
  THE one skill for putting something on the user's time -- a task, an
  appointment, or a stay. Fires when the user registers, looks up, edits,
  completes, defers, or cancels anything time-shaped. Tasks: "add X for
  Friday", "I did my vocab", "finished the gym", "that got checked by
  mistake", "what's left today", "what's overdue", "move X to Friday",
  "drop X". Appointments: "I made plans with X", "I'm meeting so-and-so",
  "what appointments do I have this week", "dinner plans with OO", "change
  the appointment's place/time", "add someone to that appointment".
  Stays: "나 금요일 본가 가", "목요일부터 금요일까지 본가에 있을 거야", "이번 주말
  출장", "재택할게", "회사 다녀올게", "9시부터 6시까지 회사". Also fires on a
  person's name or nickname in a scheduling context. Manages kit_life.db
  blocks via the kit_life.sh CLI. Core rule: the "kind gate" -- every
  utterance is task | appointment | presence, the skill decides which, and
  the kind picks the verb family. Person and place are NOT handled here:
  they resolve as module function calls (person-resolve / place-resolve) and
  only hand off to the person-manage / place-manage skills when the user has
  to be asked.
---

# schedule-log

Execution axis: register, update, query, settle, cancel anything that
occupies the user's time. One `block` table, `kind` column = task |
appointment | presence; this skill decides kind, never asks it as such.
`kind` != `schedule_kind` (timed | all_day = time shape). keep the two apart.

All via `LKIT="${PROJECT_ROOT:-$(pwd)}/database/kit_life.sh"` (cwd =
workspace root). never raw sqlite. times in user's local tz (Asia/Seoul).
Signatures, output shapes, time-shape rules, error list: read
reference/verbs.md when you need an arg order or an error's meaning.

## 1. kind gate (FIRST, before time-shape parse)

- presence: core content = WHERE the user will BE for a span (stay, lodging,
  overnight/multi-day trip, WFH, family home, office hours, business trip
  with no meeting named).
- appointment: an event/meeting/activity attended at a time, usually with
  people or at a venue. "dinner at <PLACE>" = appointment; "at <PLACE> all
  day Friday" with no event verb = presence.
- task (default): a commitment to DO; "did I do it" is meaningful. may carry
  a clock time (shape, not kind).
- order: presence cues -> appointment cues -> task.
- ambiguous presence/appointment -> ONE question, examples on BOTH sides
  ("staying at a place for a while, like <PLACE> / WFH, or attending
  something, like a meeting / dinner?"), never "being somewhere vs doing
  something". ambiguous task/appointment -> "check off or show up to?".
- shape detection still runs after the gate.

Mirror routing is code's: 시각 있음 -> 구글 캘린더, 시각 없음 -> 구글 태스크
(owner 2026-08-15). never tell the user kind decides the surface; moving
surface = changing the time. timed presence mirrors like a timed
appointment, so never file a stay as a task to reach the calendar. 집쉼
(home non-work presence) is hidden, not mirrored, by design; 재택/회사 mirror.

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
  ask its ASK line ("혹시 김영희랑 같은 분인가요?"); yes ->
  `near-match-accept person <ref> "<name>"`, link <ref>; no -> person-manage
  skill as ABSENT. AMBIGUOUS / ABSENT -> person-manage skill (it asks /
  registers), come back with an id. never `person-add` here. "영희님이랑
  최근에 뭐 했지" -> `person-blocks`, person-manage skill's question.
- companions named in the request ("엄마아빠랑") are linked in the SAME turn,
  any kind, never as a memo -- whose occasion it is (the birthday grandmother
  visited) included. never re-file a stay as an appointment to carry people.
- resolved path loads no other skill.

## 3. appointment

0. DEDUP GATE first, before any question: `appt-find <date> <date+1>`.
   existing -> show, `[[OPTIONS]]` merge/update vs separate new. write only
   on 0 matches or explicit "new".
1. parse time relative to now (local). start required; timed end omitted =
   kit +3h default (give end only when the user stated one).
2. venue -> `place-resolve` first. ABSENT = pass the venue TEXT as
   location, area kept when said ("성수에서 서울 브루어리" -> "서울 브루어리
   (성수)"); say nothing about it: no place-manage, no address, no
   register question -- the kit keeps the row itself. past/started event
   (logging what happened): NEAR_MATCH -> do not ask, use the text; ask
   only what the verb needs (a start time); `TRAVEL_RETRO_RESOLVE` lines
   -> reference/travel-leg.md "logged after the fact" (silent). future:
   an address is asked only through `TRAVEL_PLACE_NEEDED` (travel-leg.md
   step 2).
3. `appt-add <title> <start> [end location purpose summary]`.
   output has `TRAVEL_PLACE_NEEDED` -> read reference/travel-leg.md.
   output has `PLACE_UNSET` -> ask `PLACE_ASK` verbatim; 미정 = `appt-upd <id> location=미정`.
4. persons -> `person-resolve` each -> one `block-person` call.
5. edit: `appt-upd <id> field=value ...`. `location=` re-runs auto-attach
   and may emit `TRAVEL_PLACE_NEEDED` (same handling).
6. verify `appt-show <id>`, report.

## 4. presence (verb enforces one live presence at a time; no dedup step)

1. span: daytime stay (office / WFH) REQUIRES hours -> ask "몇시부터 몇시?"
   if missing, write timed, same day. overnight / multi-day -> date-only,
   UNLESS times were stated ("27 10시쯤 출발, 29 낮쯤 복귀" -> timed
   `2026-11-27T10:00` `2026-11-29T12:00`; 낮 = 12:00, say so). keep them.
2. title: daytime -> activity/context first (place is its own arg);
   overnight/travel -> place first ("<PLACE> <trip-context>").
3. `place-resolve` the place (ABSENT -> place-manage). never ask minutes here.
4. `presence-add "<title>" "<place>" <start> <end> [--new]`.
   same-title/date -> blocked unless `--new`. `PRESENCE_CONFLICT <n>` ->
   show rows, `[[OPTIONS]]`: adjust new / move-cancel existing / abandon.
   never coexist two.
5. companions (§2) -> `block-person <id> <pid>...`; verify `event-day`.
- move: `presence-move <id|ulid> <start> <end>` (same shape family).
- cancel ("안 가게 됐어"): confirm first -> `presence-cancel <id|ulid>`.
- a presence is never "done".

## 5. task

- done/undo: `task-find "<keyword>"` -> recurring: today's todo first, else
  most recent overdue -> unique -> `task-done` / `task-undone`, one line.
  2+ -> list, ask. 0 -> say not found, offer to create.
- add: `task-add <title> [due] [note] [--at HH:MM] [--place N]`; `--place`
  runs travel/prep auto-attach -> `place-resolve` first. report one line.
- defer: `task-reschedule <id> <YYYY-MM-DD>` moves DATE only; verb keeps
  shape. `--at` only when user gave a new time, `--all-day` only when asked.
  untimed target -> verb warns on stderr: relay it, offer a time (untimed
  lands on a surface the user does not read, 2026-08-23). confirm WITH the
  time: "내일(8/25) 19시로 옮겼습니다".
- archive: confirm first -> `task-archive <id>`.
- overdue: `task-overdue` -> simple list "title (due)"; empty -> say nothing
  is overdue. never auto-close.

## 6. query

- "this week's appointments" -> personal appointments only; drop recurring
  work/commute blocks and presence rows (appt-find returns presence too).
- "what do I have today" across kinds -> `event-day <date>`.
- cancelled (abandoned) never listed.
- tabular -> code block; no bold; address per persona (AGENT_ADDRESS in
  config/agent.conf, else locale default).

## boundaries

- own-data done/undo/add -> no extra confirm; ambiguous -> ask.
- appointment cancel, presence cancel, task archive -> confirm first.
- this skill writes the DB; the mirror carries it. never offer a separate
  "add to calendar", never call a row local-only.
- no verb for it -> "아직 저장 못 해요, 업스트림에 보고" -- never offer "직접 수정".
- people -> person-manage. places, addresses, rooms, merges -> place-manage.
  meaning after the fact -> episode skills. this skill owns the time.
