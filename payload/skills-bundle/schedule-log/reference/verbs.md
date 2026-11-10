# schedule-log -- verbs, resolve verdicts, errors

Read when you need an argument order, an output shape, or an error's meaning.
All via `kit_life.sh`. `task.sh` in this skill dir = 1:1 delegator to
`task-*` (add find done undone reschedule postpone archive overdue); same thing.

## Cooking intentions

Judge cooking intent from the conversation, including earlier dish selections
and later timing or scheduling replies. Never use a word/phrase list or regex
to choose this route. When the owner picks or states dishes, silently record
`$LKIT cooking pending "<dish>" ["<dish>" ...] [--eaters owner,<companion>]
[--target <dated ISO time with offset>]`. Later time/companion answers update
that context. Follow `core-log/reference/cooking.md`: cooking template ->
PLAN.json -> cooking check -> cooking plan (target time), then the kit stage
Start buttons; without a target use cooking open. Never save cooking as
`task-add`, `appt-add` or `presence-add`, or replace the kit plan with a chat
timeline. The hook supplies state on every turn; the agent decides intent.

## Attendance and physical venues (before every appointment write)

Use your understanding of the owner's sentence to pass
`schedule-add ... --kind appointment|task
--attendance-mode online|in_person|unknown`. An online call
keeps the owner's platform/venue wording in location; never register that
wording as a place or create travel tasks yourself. Pass a meeting link as
`--meeting-url <URL>`; that typed link also marks the event online. Do not
classify by a platform-name list. Unknown with no resolved physical place
means no travel. Apply the appointment end default independently of attendance:
with no stated end or sourced runtime, leave the end argument empty for the
kit's start +3h default (owner 2026-10-01).

A same-plan match is the existing event: persist the newly understood mode
on that id before relaying its card. An EXISTS result is not a reason to
leave stale travel or attendance metadata. Preserve people and stated times.

Edits use `appt-upd <id> attendance_mode=online|in_person|unknown` and
`meeting_url=<URL>` (empty clears the link). Changing to in-person must clear
an old meeting_url and supply the actual physical venue. `location=` clears
the derived place and travel; replacing location re-derives exactly once.
These writes and later appt-show/event-card/day-card reads repair stale
travel through the same kit code. Never hand-delete derived travel.

A new venue must resolve through the map resolver to an address/coordinates
before it becomes a place. An exact saved place or taught alias is reused.
Unresolved free text and URLs do not get provisional travel. For an online
event relay the existing venue line, without departure/arrival lines or any
new label, sentence, or card shape.

## Official titles and verified durations (before every plan save or dedup reply)

Dedup continues lookup: `noun-context` / `appt-find` finding the same plan
selects the existing appointment id, not an early reply. Stored titles,
expansions and end times are unverified until backed by an actual source.
Read this section BEFORE replying even if no new appointment will be saved.
For one clear same-plan match, the owner's stated plan authorizes enrichment
of that row: no merge/update menu, permission question or duplicate insert.
This takes precedence over SKILL section 3's generic existing-match menu.
Multiple plausible appointments still require one disambiguation question.

Lookup-before-save: when a plan names a titled thing (movie, show, exhibition,
performance, game, book, product, restaurant or venue), look up its official
name BEFORE `appt-add`, `task-add`, `presence-add` or a title update. This
also applies to familiar shorthand, not just unknown names. Run noun-context
first to reuse existing facts/aliases, then search/fetch authoritative sources
(distributor, publisher, maker, organizer, venue or ticketing page), using the
owner's shorthand plus the stated venue/date/format to disambiguate. Do not
invent an expansion or save shorthand as the official title. A work title is
not a person/group/room: do not send it into a noun-type question.

Save the verified official name as the activity title; keep format such as
4DX in that title, people in participant links, and venue in location. Retain
the owner's exact shorthand as an alias: `appt-add --kind appointment "<official title> 4DX"
<start> "" "<venue>" --alias "<owner shorthand>" ...` saves both atomically.
For an existing block, `block-alias <id> "<owner shorthand>"` preserves the
alias before `appt-upd <id> title="<official title>"`. For task/presence writes,
call block-alias on the returned id in the same turn. Existing entity aliases
(person/group/place, migration 073) continue to use their existing verbs;
resolve a venue first and keep its echoed registered name as the location.
Do not create a second venue just to prettify it. Block title aliases use the
existing block_attr store, never a new table; noun-context returns matching
EVENT lines with the saved official title on later shorthand mentions. Multiple
matching events remain multiple candidates; never pick one silently.

End-time-from-runtime: for a movie/show, look up that day's showtime at the
named venue AND format (e.g. CGV Gangbyeon, 4DX), including its scheduled end.
Use a matching dated session's end when available. If no matching session is
accessible, look up the runtime for the verified work/version and pass
`--runtime-min <positive minutes>` with the owner's stated start and an empty
end argument. The writer derives end = start + runtime, across midnight too.
Do not add guessed advertising/trailer minutes or change the stated start to
a nearby session. A different session is not evidence for this booking. If
the owner explicitly stated an end, preserve it; an explicit end takes priority
over --runtime-min. Retain the source URL, lookup date, format/version and
whether timing came from a session or runtime in summary. Runtime-derived end
is a duration calculation, never a claim that the venue confirmed a session.

For an existing appointment, read `appt-show <id>` first and retain its
participant links, venue, stated start, existing aliases and summary facts.
After the lookup, run `block-alias <id> "<owner shorthand>"`, then
`appt-upd <id> title="<official title> <format>" end_at="<sourced end>"
summary="<existing summary plus source URL/date/version/timing basis>"`.
For a runtime fallback compute end = existing stated start + verified runtime
and pass that end_at; `appt-upd` does not accept --runtime-min. Preserve an
owner-stated end if supplied. Never keep an unsourced stored/default end just
because dedup found it, and never treat an old invented expansion as proof of
the work's identity. Do not cancel/recreate the appointment. Relay the final
update CARD verbatim, just as for a new save. Keep lookup and update tool work
silent. Do not change venue hours, addresses or other place settings to silence
unrelated gap prompts during title enrichment; leave those settings intact.

Use the start +3h appointment default in SKILL section 3 and the time-shape
rule below when no end or sourced runtime is available (owner 2026-10-01).
For titled movie/show capture, follow the lookup and pending-on-failure flow
above: a sourced end or runtime MUST replace the generic +3h default.
Keep a verified session end or runtime as the timing basis in summary.
Non-titled plans retain their current default and reply behavior.

Only after an actual lookup failure (authoritative page unavailable plus a
completed alternate search, or unresolved conflicting results), name ONE
missing fact: the official identity first, otherwise the runtime/end. Ask
only that fact and keep the titled plan pending rather than saving a guessed
name or end. Never hand checking back with "다르면 알려 주세요" or "if it differs,
let me know". Do not ask the owner to confirm facts you found. Successful
capture keeps the existing CARD reply verbatim with the final title, venue,
people and end time; no lookup narration, extra menus or new reply format.

## context before typing or asking

After `noun-context`, before `noun-tag` / TYPE_ASK, search agent memory for
unresolved spans, including case variants. Kit ABSENT is not a memory search.
Known -> no type menu: propose the remembered kind, name and members with
one-tap confirmation. Only after confirmation, resolve existing records,
`group-add <name>` / `person-add <name>` as needed, then
`group-member <group> <person>...` for members (preserves existing groups);
retry noun-tag and link with `appt-group` / `block-person`.
Kit facts win conflicts. Only a completed 0-hit search permits TYPE_ASK;
unavailable search is not 0 hits.

Any `[[OPTIONS]]` menu owns the reply's only question; defer other questions.
PLACE_ASK and other open questions wait for the next reply without a menu.
Ask PLACE_ASK at most
once per appointment per conversation unless the owner brings it up again.
Remember actually asked ids; deferring is not asking, and repeated tool output
or a menu answer is not a reset. `location=미정` handling is unchanged.

## families by kind

```
kind=task        task-add task-find task-done task-undone task-reschedule
                 task-postpone
                 task-retitle task-archive task-overdue task-untimed-open
                 tasks-by-project task-done-between
kind=appointment appt-add appt-find appt-upd appt-show appt-cancel
                 appt-travel-attach
kind=presence    presence-add presence-move presence-cancel presence-revive
any kind         event-day event-window event-notify
                 block-person block-person-rm (appt-person / appt-person-rm =
                 the same verbs under their old names)
```

## schedule settings: travel calendar visibility

| Config key | Default | Values | Effect |
|---|---|---|---|
| `travel_calendar_enabled` | `1` (ON, including unset) | `0` / `1` | Whether the mirror uses the travel calendar. Moves, prep/buffers and presence banners routed there disappear when OFF; kit blocks, departure alerts, pin move lines and map buttons remain. Appointment/task calendars are unaffected. |

- "이동은 캘린더에 올리지 마", "이동 캘린더 보기 싫어", "이동 그룹캘린더 안 쓸래"
  -> `config-set travel_calendar_enabled=0`.
- "이동 캘린더 다시 켜줘", "이동을 다시 캘린더에 올려줘"
  -> `config-set travel_calendar_enabled=1`.

Run the setting verb once, then confirm in one line. Do not remove travel
rules/blocks or disable alerts. The mirror queues existing travel-calendar
rows immediately and retires them through its ordinary push path in batches
of at most 50 per poll; ON queues them for re-push. A timed daytime presence
still belongs to the task calendar and stays visible. Repeated OFF remains
OFF, including newly derived legs and reconcile/repair pushes.

DRAFT COPY — owner review before cut (do not claim approval):
OFF: "이제 이동은 캘린더에 안 올려요. 출발 알림과 고정핀의 이동 줄은 그대로 와요."
ON: "이동을 다시 캘린더에 올릴게요."

## appointment arrival margin (speech settings)

Do not ask proactively. Map the owner's request to the narrowest stated scope:

- "버퍼 10분으로" -> `buffer-set --scope default --minutes 10`.
- "퇴근하고 바로 가는 약속은 버퍼 없이" ->
  `buffer-set --scope situation --target after-work --minutes 0`.
- "이 사람 약속은 항상 15분" -> resolve the person first, then
  `buffer-set --scope person --target person:<id> --minutes 15 --strict`.
  A group uses the same scope with `--target group:<id>`.
- "이번 약속만 5분" ->
  `buffer-set --scope appointment --target <block id> --minutes 5`.
  Add `--strict` only when the owner says this margin must be kept.
- "아까 그거 취소" after a buffer change -> `buffer-undo`.
- "버퍼 설정 어떻게 돼 있어?" -> `buffer-show`.

Relay the verb's one-line localized confirmation exactly. Zero is valid;
never round the requested minutes to the travel grid. Precedence is this
appointment > person/group > situation > user default > product default 0.
At the person/group level, strict wins, then the larger margin, then stable ID.
Deleting a person/group removes its rule; merging keeps the surviving
record's rule, or carries the source rule when the survivor has none.
The `after-work` situation means the latest-ending earlier block on the local
day is a routine presence (office/WFH); a nested lunch does not hide its end.
An intervening stop breaks that chain. A routine riding the journey does not
block travel or qualify as work. Changes re-derive future appointments,
including calendar imports; never rewrite past appointments.

The kit shrinks or drops only the available margin when the previous block
leaves too little time. Strict appointment/person rules add
"앞 일정 때문에 여유가 N분뿐이에요"; other squeezed margins need no warning.
Relay the day card's `HH:MM 출발 · 여유 N분 · HH:MM 도착` unchanged. The
no-buffer explanation belongs only to the first appointment using that rule;
it stays stable when that same card refreshes. Real late-travel handling is
unchanged. Link people/groups before the final `event-day` verification so
participant rules are reflected in the card.

## signatures

```
person-resolve <name_or_alias>
    RESOLVED<TAB>id<TAB>name<TAB>relation<TAB>aliases
    AMBIGUOUS<TAB>n  + n rows (id name relation aliases)
    ABSENT<TAB><query>                         (every verdict exits 0)
place-resolve <name_or_alias>
    RESOLVED<TAB>id<TAB>name<TAB>has_address(0|1)<TAB>address
    VIA_ANCHOR<TAB>id<TAB>name<TAB>has_address<TAB>address<TAB>anchor
        query was a ROUTINE anchor ('헬스'); field 3 = real place name
    ABSENT<TAB><query>                         (every verdict exits 0)
appt-find    <date_from> [date_to]
    -> id start_local title location end_local people retro_line
       appointment AND presence rows (052); abandoned excluded
schedule-add <title> <start> [end location purpose summary]
             --kind appointment|task|auto [--activity workout] [--new]
appt-add     <title> <start> [end location purpose summary] [--kind appointment|task|auto] [--new]
             [--notify default|silent|start_only|<lead-min>] [--project <id|title>]
             [--booked]   (the owner has a booked time)
appt-upd     <id> field=value ...
             (title start_at end_at location location_url purpose summary room)
    room=<ROOM> (appt-add too): the room of THIS booking. REPLACED<TAB>room<TAB>
    <old><TAB><new> when it overwrote one; ROOM_ADDED = a sibling-shaped room
    (B2룸 beside A2룸) registered silently; TYPE_ASK = kit-unknown name,
    apply the memory gate above before asking;
    exit 8 FIELD_KIND_REFUSED = a group / person / place in the room;
    exit 6 PARENT_ASK = the booking has no place; exit 2 E_ROOM_MULTI = two
    room= values. room= (empty) clears; a location at another place clears
    the room (ROOM_CLEARED).
appt-group   <block_id> <group>... / appt-group-rm <block_id> <group>
             a group (band, club) attends; card shows its display name
    location 미정 (also "장소 미정") = the owner said the place is undecided:
    recorded as-is, card shows 장소 미정, never asked again, no travel.
    output (appt-add / appt-upd / appt-person / event-card) may carry, before the card,
    PLACE_UNSET<TAB><id> + PLACE_RELAY + PLACE_ASK<TAB><id><TAB><question>
    -> apply PLACE_RELAY's menu / conversation gate; answer -> appt-upd.
event-card   <id> [<id> ...] [--removed <id[,id]>]
    READ. the confirmation card of these items, final state, ONE date header
    per date with that date's rows. a turn with more than one write ends with
    ONE event-card of every touched id; relay that block, not the per-write ones.
block-person <block_id> <person_id>... /  block-person-rm <block_id> <person_id>
    appointment, presence or task; all-or-nothing on unknown ids; prints the
    card with the people. derived travel/prep legs and records are refused.
appt-show    <id>                      1 appointment + persons, local time
appt-cancel  <id>                      settle 'abandoned'
appt-travel-attach <eid>               derive prep/travel; overwrites provisional stopgap
place-gap    [--all] [<from> [<to>]]   READ: coming appts with no travel time
                                       (link_only|no_place|no_duration)
travel-link-gap [--all]                READ: place pairs with no travel minutes
                                       (absent|expired); gap lines carry no number
travel-sweep [<from> [<to>]]           summary scanned/attached/exists/needs/
                                       retired/other/provisional (provisional ⊂ needs)
presence-add "<title>" "<place>|-" <start> <end> [--new]
             [--in <block id|event>] [--then <block id>] [--no-place]
    two dates = all-day span; two datetimes = timed span (keeps the stated
    departure/return). a timed span ending on a later date renders as
    "11/27(금) 10:00 ~ 11/29(일) 12:00". --in / place '-' / --then: see
    "a stay after an appointment" below.
    Day off / leave, all-day with no place: use exactly
    `presence-add "<title>" - YYYY-MM-DD YYYY-MM-DD --no-place`
    (same date twice for one day). For "오늘 휴가야", title is the
    owner's leave label. Cancel that date's office presence first with
    `presence-cancel <office id>`, then add the leave. Check both exits;
    a refusal means nothing was recorded. Never invent "-|-" as a place.
    `--no-place` stores NULL, keeps the title alone on cards / where-lines,
    also accepts timed spans, and cannot combine with --in / --then.
    Bare '-' without --no-place still means inherit the --in block's place.
presence-upd <id|ulid> [people=<participants>] [location=]
    empty `location=` clears the place and its location text / URL;
    participants stay unless people= is also supplied. Travel follows the
    cleared place. Nonempty location edits are not supported by this verb.
presence-move <id|ulid> <start> <end>  shape-preserving
    its legs follow at once; the `TRAVEL <ulid> regen= ...` line is an
    audit line, never part of the reply.
presence-cancel <id|ulid>              settle 'abandoned'
task-add     <title> [due] [note] [--place N] [--at HH:MM] [--dur N | --end HH:MM] [--booked]
             [--project <id|title>]
task-find    [date|all|keyword]        -> id title due state place
task-done | task-undone <id>
task-reschedule <id> YYYY-MM-DD [--at HH:MM | --all-day]
task-postpone <id> <owner time text> [--now ISO]
task-archive <id>                      soft delete
task-overdue                           todos due before today
event-day    <date> [--with-derived]   day view, all kinds incl. presence band:
                                       BAND/ANCHORED/DUE id rows (machine data,
                                       never shown), then DAY_CARD_BEGIN / the
                                       day-card --today card for that date /
                                       DAY_CARD_END + DAY_CARD_RELAY. reply =
                                       that card verbatim. travel/prep/buffer
                                       are never rows; --with-derived (travel
                                       maintenance only) adds them + DERIVED.
day-card --today [--date <date>]      THE reply to "오늘 할 일" / "할일 목록" /
                                       "오늘 일정": the workbench card's header,
                                       tasks and today's schedule (stays +
                                       appointments, travel as the line under
                                       the item). relay verbatim, at most one
                                       line of your own on top; never rebuild
                                       it from rows.
place-hours  <place> [YYYY-MM-DD] [--json]   READ: the place's hours that date
    place-hours<TAB>id<TAB>name<TAB>date<TAB>OPEN|CLOSED|UNKNOWN|NO_HOURS
               <TAB>open<TAB>close<TAB>breaks|why<TAB>source<TAB>fetched
    + PLACE_HOURS_DEFAULT<TAB>-<TAB>HH:MM   a no-time visit's start (OPEN)
    + PLACE_HOURS_TEXT<TAB><sentence>       reply copy, names the closing time
    + PLACE_HOURS_NEEDED ... when none on file or stale (see below)
place-hours-set <place> <json|text> [--source <url>] [--fetched YYYY-MM-DD]
    upsert; text form '평일 09:30-18:30 (점심 13:00-14:00), 토 09:30-14:30,
    일/공휴일 휴무'; JSON {"mon".."sun"|"weekdays"|"weekend"|"holiday":
    "HH:MM-HH:MM"|"closed", "break": "HH:MM-HH:MM"}; 'none' = keeps no hours.
    bad input -> place-hours-set<TAB>bad_hours<TAB><why>, exit 3, nothing written.
```

## opening hours (067)

Write verbs that print a card (appt-add / appt-upd / task-add / event-card ...)
may carry, before the card, for a live appointment/task at a place (not home,
not a past day):

```
PLACE_HOURS_NEEDED<TAB><place_id><TAB><name><TAB><address>
    no hours on file, or older than 90 days (config place_hours_stale_days).
    -> look them up yourself NOW, silently (web search / fetch by name +
       address), place-hours-set <place_id> '<hours>' --source <url>, then
       event-card <id> again and relay that card. Nothing found / not a
       business -> place-hours-set <place_id> none.
PLACE_HOURS<TAB><id><TAB><place_id><TAB><name><TAB><date><TAB>OPEN|CLOSED|UNKNOWN
           <TAB><open|-><TAB><close|-><TAB><breaks|why>
    the hours on the item's date -> say the closing time in the reply.
PLACE_HOURS_DEFAULT<TAB><id><TAB>HH:MM
    the APPOINTMENT has no time: a start inside the hours (30-min grid, after
    now on today, clear of the break) -> appt-upd <id> start_at=<date>THH:MM.
    never printed for an errand (a no-time task at a place): it takes no
    visit time -- see "errands" below.
PLACE_HOURS_RELAY <instruction>   follow it; never print these lines.
```

A row starting outside the hours carries a card suffix (`← 영업시간 밖
(09:30~18:30)` / `← 휴무일` / `← 휴게시간 (13:00~14:00)`): say so and offer
the nearest time inside the hours. Never tell the owner to check the hours or
to call the place, and never ask the owner for them.

## schedule kind judgment

The agent judges kind from the conversation before writing. Pass
`--kind appointment` for meeting people, a booking, or a fixed external slot;
pass `--kind task` for a self activity. Use `schedule-add <title> <start>
[end location purpose summary] --kind <judgment>`. A matching task routine
supplies its duration/place/notification policy. For a timed appointment with
no stated end or sourced runtime, leave the end argument empty so the kit
uses start +3h (owner 2026-10-01). For a task/self activity with no known
duration, keep the end unknown. `--kind auto` is only for genuinely unresolved
meaning; exit 9 `SCHEDULE_NEEDED kind` tells YOU which typed judgment to pass.
Resolve that from context and retry silently; do not relay kit flags or a
classification refusal to the owner. Ask only a genuinely missing owner fact.
Kind and attendance mode are independent; preserve attendance when supplied.

Legacy `appt-add` still accepts existing appointment calls and keeps their
existing duration default. New producers pass
their judgment explicitly, including when using that compatibility verb.

## errands: a task with a place and a deadline (owner 2026-10-06/07)

A pickup / return / issue / drop-off at a place with no booked time is a
TASK due before the place closes. Read the owner's meaning and choose
`task-add` or `schedule-add ... --kind appointment`; title keywords never gate either write verb.

```
errand-classify <title> [--verb-class pickup|return|issue|drop_off]
    [--place <name>] [--at HH:MM] [--booked]                     READ
    ERRAND_CLASS<TAB>errand|appointment|ask|task|other<TAB><verb class|-><TAB><why>
    + ERRAND_ASK<TAB><question>        class ask only
    The semantic verb enum comes from the agent; title is display evidence.
errand-plan <id> [<id> ...] [--now <UTC ISO>]                     READ
    ERRAND lines (below) for those ids.
schedule-add --kind appointment|task and task-add respect the explicit chosen kind, even if the title
    includes pickup/return/issue/drop-off words. The agent resolves intent
    and missing facts before selecting a verb.
```

Reply: the deadline only ("오늘 18:30 마감 전에 다녀오시면 돼요"); the latest
departure and the time to spare only when the line ends in tight / late
(the card row then ends in `← 늦어도 17:35 출발 (여유 15분)` /
`(25분 모자라요)`). The card row reads `마감 18:30 <title> / <place>`. The
departure alert comes by itself (`📍 볼일 알림`: `늦어도 17:35 출발` /
`<title> 마감 18:30` / route), from the task's own notify policy.

## map buttons per region (069)

Alerts open Google Maps by default; a region's local maps (KR: Kakao, Naver)
are offered once and used only after the owner picks them, per leg by where
the leg is (a leg abroad, or across two regions, stays Google). The same
card output may carry:

```
PLACE_REGION_NEEDED<TAB><place_id><TAB><name><TAB><address>
    an end of an upcoming move whose country is not on file
    -> look it up silently, place-region-set <place_id> <CC> --source <url>.
MAP_REGION_NEEDED<TAB><CC>
    a country whose local map apps are not on file
    -> look them up silently, map-region-set <CC> <kakao,naver|none>
       --label '<country>' --source <url> (only linkable ones are kept).
MAP_REGION_RELAY <instruction>   follow it; never ask the owner.
MAP_PROVIDER_ASK<TAB><CC><TAB><text>
MAP_PROVIDER_CHOICE<TAB><provider><TAB><label>   (google first, then local)
    first move in a region with local maps and no pick -> end the reply
    with <text>, the labels as buttons; once per region, optional.
    Owner picks -> map-provider-set <CC> <provider[,provider]>.
MAP_PROVIDER_RELAY <instruction>   follow it; never print these lines.
```

`map-provider [--json]` shows each region, its local maps and the pick.
`place-coord-missing` lists pairs whose map coordinates 066 left empty;
`routines/bundle/place-coord-backfill.sh [--dry-run]` re-drives the map for
them (`travel-resolve.sh --coords-only`, minutes untouched). Home / a stay
place with no address prints `NEEDS_ADDRESS<TAB><id><TAB><name><TAB>new|asked`
once per place: ask the owner once (all `new` in one message, per
NEEDS_ADDRESS_RELAY), store `place-address <id> add '<address>' road`, re-run;
`asked` is never asked again.

Empty positional -> "" placeholder: `appt-add --kind appointment "AI Day" "2026-07-05T12:00:00+09:00" "" "venue"`.

## a stay after an appointment ("거기서 자고 간다", "외박", "바로 거기서 간다")

A fact, not a question: write the stay, never ask to confirm it. Travel rows
(`이동: ...`, `약속 버퍼: ...`; `event-day --with-derived` lists their ids on
`DERIVED`) are the kit's: never offer to cancel, move or confirm one -- the
stay re-derives them.

    presence-add "<title>" - <appt end> <leave time> --in <appt id> [--then <next appt id>]

- `--in <appt id>`: the id on the card. The stay joins that appointment's
  outing: no move between them, no trip home after the appointment.
- place `-`: the stay is at the appointment's place. The appointment has no
  place -> exit 6, `STAY_PLACE_NEEDED<TAB><appt id>` + its `PLACE_ASK`:
  ask that one question once, `appt-upd <appt id> location=<answer>`, re-run
  the same `presence-add`. Nothing was written before the answer.
- `--then <id>`: "아침에 바로 이 약속으로" -- that appointment leaves from the
  stay (its origin = the stay's place). Minutes for that hop unknown ->
  `TRAVEL_PLACE_NEEDED edge_missing` once (travel-leg.md). Without `--then`
  the next appointment may print `ORIGIN_ASK` (straight from the stay, or
  home first?).
- `STAY_OVERLAP<TAB><id><TAB><title>`: a block (a workout, a chore at home)
  starts during the stay; the card says `← 겹침: ...`. One line to the
  owner, ask move/skip; never change it yourself.
- `NEXT<TAB>block-person <stay id> <person_id>...`: people named for the
  stay are linked now (next section); a birthday or "C는 A, B의 딸" in the
  same message goes to person-set / person-link in the same turn.

## stay participants and family links

Create a stay with `presence-add`, then pass its returned block ID and resolved
person IDs to `block-person <block_id> <person_id>...` in the same turn. This
works for timed and all-day stays; participants use the existing `event_persons`
links and remain attached after `presence-move`. Repeating an ID adds no duplicate.
Read them with `event-card <block_id>`, `day-card --date <date>`, or
`appt-find <date_from> [date_to]` (which also lists stays). The reverse lookup is
`person-blocks <name_or_alias> [days]`. `appt-show` is appointment-only.
Remove a participant with `block-person-rm <block_id> <person_id>`.

`person-link <a> <b> <type>` means **a is type of b**. Both targets accept a
person ID or an unambiguous name. For example, `person-link Child Parent child`
and `person-link Parent Child parent` describe the same link, regardless of
which person was created first. Two parents require two links. The existing
`persons_relations` table stores the smaller ID first and inverts parent/child
when necessary; no separate kinship table is needed.

`person-show <id|name>` lists the **other person's role**: the child's list
shows the parent as `parent`, and the parent's list shows the child as `child`.
`person-unlink <a> <b> [type]` uses the same a-is-type-of-b direction as the
write. Repeating a link is idempotent; an opposite direction conflicts (exit 4)
and requires `--replace` after confirming the correction. A family link does
not itself change either person's relation to the user (`person-set` owns that).

An unknown verb shaped like a person or stay edit (`person-upd`,
`presence-person`, `person-relation`) prints `DID_YOU_MEAN<TAB><verb><TAB><signature>`
on stderr before the verb list (exit 1, nothing written). Run that verb; an
unknown name is never evidence that the capability is missing.

## time shapes

- date-only `YYYY-MM-DD` -> all-day (two dates = multi-day span).
- datetime `YYYY-MM-DDThh:mm[:ss[.fff]][Z|+hh:mm]` -> timed; naive = Asia/Seoul.
- all-day appt: slot_exclusive=0 (containing context, does not block timed).
- timed appt: slot_exclusive=1. An appointment with no stated end or sourced
  runtime gets start +3h (owner ruling 2026-10-01), including explicit
  --kind appointment and legacy appt-add. Leave the end argument empty so
  the kit applies that default. Task/self-activity captures retain their
  unknown end when no duration is known. Official-title capture above uses
  a sourced session end or --runtime-min, never a generic movie/show duration.
- appt-upd on all-day row with date-only value: start only = shift whole
  range (length kept); end only = keep start; both = each own date.

## Owner clash answers

`CLASH_ASK` requires the owner's answer. Relay its question and the
`[[OPTIONS: ...]]` field of its `ASK_OPTIONS` line verbatim, then wait for
the owner to choose. Only after that answer run
`clash-answer <block> cancel|move|keep` (pass the printed `--ask` token when
supplied). Never choose `keep`, cancel, or move yourself;
adding an appointment does not authorize answering a separate clash question.
The kit stores the proposal as pending and preserves the owner's time until
an explicit answer. `ROUTINE_CLASH` is a separate automatic routine adjustment
notice, not permission to answer `CLASH_ASK`.

## errors (loud, non-zero, zero mutation)

- mixed shapes (date-only + datetime); malformed time; reversed interval.
- slot occupied (timed exclusive conflict).
- shape mismatch on appt-upd / presence-move (schedule_kind transition is an
  SDK verb, not appt-upd).
- wrong kind (task id to appt verb or reverse; block-person takes any of the
  three kinds); unknown person_id; empty title=.
- presence: unbounded span; overlap -> `PRESENCE_CONFLICT <n>` + blocking rows.
- exception: resolve verbs exit 0 on ABSENT (not a failure).

## moved from SKILL.md (verbatim detail)

### mirror routing

Mirror routing is code's: 시각 있음 -> 구글 캘린더, 시각 없음 -> 구글 태스크
(owner 2026-08-15). never tell the user kind decides the surface; moving
surface = changing the time. timed presence mirrors like a timed
appointment, so never file a stay as a task to reach the calendar. 집쉼
(home non-work presence) is hidden, not mirrored, by design; 재택/회사 mirror.

### stay span example

overnight / multi-day -> date-only,
UNLESS times were stated ("27 10시쯤 출발, 29 낮쯤 복귀" -> timed
`2026-11-27T10:00` `2026-11-29T12:00`; 낮 = 12:00, say so). keep them.

a stay
right after an appointment ("거기서 자고", "외박", "바로 거기서 간다")
= a fact: write it, no confirm -> verbs.md "a stay after an
appointment" (`--in <appt id>`, place `-`, `--then`); ask only
its PLACE, once. travel rows are never offered for cancel.

### stay filed as an appointment (migrate)

a note saying a stay cannot carry people is stale (before 2.38.0). a stay
filed as an appointment for that reason -> ask once, note its people, then
`appt-cancel` it, `presence-add` same span + place, `block-person` them.


### Stay participants

`presence-add <title> <place|-> <start> <end> people=person:12,group:3`
creates a stay with attendees in the same transaction. `people=` accepts
comma-separated person IDs, `person:<id>`, `group:<id>`, and unambiguous
registered person/group names. Unknown, ambiguous, retired, and wrong-kind
references refuse the write. Groups remain groups; membership is not expanded.

`presence-upd <id|ulid> people=person:12,group:3` replaces the whole party;
`people=` clears both person and group links. Use `presence-move` for time
changes. Both writes render the stay card and refresh its calendar mirror.

## Owner-chat completion

`task-done-today "<title-or-alias>" [--id <id-or-ulid>] [--tap <tapped text> | --said <owner words> | --photo-evidence <observed evidence> | --record-evidence <verb>:<row id>] [--owning-agent <owner>]`
settles today's matching open task with `settled_by=owner-chat`, including
completion before its scheduled start. JSON status: DONE, ALREADY_DONE,
ASK (multiple open candidates, nearest first; no mutation), NOT_FOUND, RETRY,
CONFIRM (row armed by a photo, in the photo turn: needs the tapped button's
text `--tap` or the owner's own words `--said` -- in the photo turn only a
verbatim piece of its caption naming the task; no mutation; `task-done <id>`
refuses alike). A grounded image assertion or same-turn dated capture receipt
also passes the gate. Record proof is validated even without a photo hook:
missing turn, wrong instance/day, changed/deleted row -> CONFIRM. A later
owner statement still uses the ordinary chat path. See completion-chat.md
for semantic matching, no-match activities, time priority and one-line replies.
On ASK, ask which local start time, then repeat with the selected `--id`.
Never choose by proximity alone or substitute yesterday/tomorrow.
`task-alias "<exact-title>" "<alias>"` registers an owner-confirmed title alias
for future daily instances. No fuzzy completion matching.

`task-open-today [--owning-agent <owner>] [--photo [--offer <id>[,<id>]] [--caption <owner caption>]]` ->
JSON `{"open": [{id, ulid, title, start_local, aliases, routine}]}`: today's
still-open task/routine instances, nearest first, each with its registered
aliases. Without `--photo`: read-only. `--photo` (photo lane) also arms the
confirm gate on those rows for the photo turn (3 h when no owner turn is
known); `--caption` records the owner's own caption of the photo; `--offer`
posts the confirm question and clean-label button(s) (the code rides
callback_data) for the matched rows through push.sh -> `offer {text,
buttons, posted, next}`. Settle after the tap with `task-done-today --tap`.

## defer
For a stated clock use `task-postpone <id> "<owner time text>"` (or
`task.sh postpone`), moving the EXISTING id. A clock without a day means
its next occurrence strictly after the current KST clock, considering AM
and PM for 1..12: at 21:18, `9:30으로미뤄줘` -> today 21:30; at 07:00 ->
today 09:30. At 22:00 it rolls to tomorrow 09:30. `내일 9시반` -> tomorrow
09:30; `오후 9시반` -> next 21:30. An explicit day wins (an unqualified
hour on that day is used literally: 9 -> 09:00, 12 -> 12:00); explicit
오전/오후 fixes AM/PM. Supports H:MM, N시,
N시반, N시M분, native Korean hours, today/tomorrow/모레 or an ISO date.
`--now ISO` is a deterministic replay clock; omit it in owner chat. An
unsupported expression refuses without moving: clarify it, never silently
substitute a date. Read back the resulting local clock and confirm it.
Never task-add a replacement or alter a separate daily occurrence. A day-only
move still uses task-reschedule; a relative duration still uses --by from
its scheduled start, never from now.

task-reschedule keeps the row shape (timed stays timed). Untimed target: the verb
warns on stderr; relay it and offer a time (untimed lands on a surface the user
does not read, 2026-08-23).
workout-defer [--from <day>] [--to <date>] [--at HH:MM] [--with <title>]... [--apply]:
planned workout day plus companions (e.g. 인바디) move together. No --apply ->
DEFER_ASK + options line; --apply -> DEFER_CARD. The DEFER_ASK question is a multi-line
screen (optional reason line, `> ` quote block, `일정을 변경할까요?`) ending at the first
`\tsplit=` field: relay it exactly as printed, then the buttons [변경] [그대로 두기]
(answer values move / keep; keep writes nothing); add no label, line or button. DEFER_REFUSED<TAB>code<TAB>detail ->
nothing moved; say what it names.
task-reschedule flags: `--at` only when the user gave a new time, `--all-day` only
when asked.

## Typed schedule corrections and readback (2026-11-06)

Use the existing IDs; do not cancel/recreate a record to correct it. These
verbs return structured data and existing card blocks. Relay only the existing
card contract; do not turn machine statuses or diagnostics into new owner copy.

| Intent | Typed verb | Inverse |
|---|---|---|
| Restore a cancelled appointment | `appt-revive <id\|ulid>` (also `appt-uncancel`) | `appt-revive-undo <id\|ulid>` |
| Restore an archived task | `task-revive <id\|ulid>` | `task-revive-undo <id\|ulid>` |
| Correct task note/place | `task-upd <id\|ulid> note=<text> place=<name\|id>` (either field) | `task-upd-undo <id\|ulid>` |
| Correct stay title/place | `presence-upd <id\|ulid> title=<text> place=<name\|id>` (either; `location=` is a place synonym; optional `people=...`) | `presence-upd-undo <id\|ulid>` |
| Read task aliases | `task-alias-list [<exact-title>]` | read only |
| Remove a task alias | `task-alias-rm <exact-title> <alias>` | `task-alias-undo <alias>` |
| Correct a task alias | remove with `task-alias-rm`, then register with existing `task-alias <exact-title> <alias>` | remove the new alias, then `task-alias-undo <old-alias>` |
| Read travel rules, including retired rules | `travel-rule-list`; `travel-rule-show <id\|ulid>` | read only |
| Read stored notification policy | `event-notify-show <id\|ulid>` | read only; JSON contains `notify_policy` and `notify_lead_min` (null means unset) |
| Clear an event's retro line | `event-retro-clear <id\|ulid>` | `event-retro-clear-undo <id\|ulid>` |
| Delete a project | `project-del <project-id>` | `project-del-undo <project-id>` |

Empty `note=` clears the note; empty `place=` (or presence `location=`) clears
the place. An empty title is refused. A place change clears a previous room;
undo restores the room and location markers. Task reads now include the note,
place ID and stored notification fields. Routine-instance edits are owner
exceptions, so the roller does not overwrite them. Undo keeps unrelated edits
and refuses when a field it would restore has changed since the correction.

Revival restores abandoned rows only; `task-undone` retains its existing
completion semantics. Cancellation receipts distinguish a travel rule retired
by cancellation from an independent travel opt-out. For cancellations predating
these receipts, inspect `travel-rule-list`/`travel-rule-show`; an explicit
`travel-rule-upd <rule> status=active` remains the typed way to re-arm travel.
Never infer that an old retired rule was an accidental cancellation.

Existing project creation/edit verbs are `project-add --title <title> [--status S] [--start YYYY-MM-DD] [--end YYYY-MM-DD] [--note N] [--new]` and `project-upd --id <id> [--title T] [--status S] [--start YYYY-MM-DD] [--end YYYY-MM-DD] [--note N] [--note-add LINE]`.

Project deletion preserves task rows and removes their project links, detaches
routine definitions, and keeps the deleted project and links in undo history.
Undo refuses reused IDs or changed links. A project referenced by immutable
`project_scope` history is hidden from current project lists and new links by
an internal config tombstone; its historical row/scopes remain intact. Undo
removes the tombstone and restores the links. No status copy or schema changes
are introduced.
