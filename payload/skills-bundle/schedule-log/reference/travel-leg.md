# schedule-log -- travel leg derivation

Read when `schedule-add` / `appt-add` / `appt-upd` / `task-add --place` output carries a
`TRAVEL_PLACE_NEEDED` line (future appointments) or `TRAVEL_RETRO_RESOLVE`
lines (past ones, section "logged after the fact"). No line -> skip all of
this. a past/started appointment never carries `TRAVEL_PLACE_NEEDED`:
nothing to register, no address, no minutes, no question at all.

Before attachment, pass the attendance judgment as described in
reference/verbs.md "Attendance and physical venues". Online events and
unknown events without a resolved physical place have no travel. A
provisional duration applies only after a physical destination is resolved.
Do not mint places from free text or URLs to make a travel signal disappear.

Execution-axis work (travel BLOCKS). Place knowledge (address, alias,
room, new row, merge) = place-manage; hand off the moment a human must answer.

## Agent-owned venue lookup

`PLACE_RESOLVE_TASK` means work for the agent, not an owner question.
Read place-manage/reference/addresses-rooms-links.md "Named venue" and
complete map -> hinted retry -> WebSearch for `<venue> <area>` before any
question. The write path records map attempts/candidates; `place-resolution
<eid>` reads the continuation. Web evidence can resolve the supplied venue
without asking the owner for its address. Resolve travel and re-attach
before finishing. Never relay commands/task codes as owner to-dos.

## signal

`TRAVEL_PLACE_NEEDED<TAB><status><TAB><eid><TAB><location><TAB><origin><TAB><dest>`
(origin empty = home; eid = the appointment id)

Since 050 a signal does NOT mean "no travel block": underivable duration ->
provisional rule (owner default 60 min, `before_src='provisional'`). Signal
keeps firing every sweep until real place/duration lands (`travel-sweep`
`provisional=N`). Complete agent lookup and route resolution first; the
signal alone never authorizes an owner question. never say "이동 시간 잡아놨어요" off a
stopgap; if mentioned: "장소를 몰라서 일단 1시간으로 잡아뒀어요". real answer
-> `travel-sweep` / `appt-travel-attach` overwrites it; no cleanup. video
call: no stopgap, no signal.

| status | what exists | what to do |
|---|---|---|
| `no_duration` / `edge_missing` | a physically resolved or saved place ROW | owed DURATION only. step 1. never register the place again. |
| `link_place` | nothing; location is a map link | step 0 first. |
| `near_place` | nothing minted; location LOOKS LIKE a place on file (col 5 = that place's name) | complete map/hinted map/WebSearch first; then ask "혹시 <col 5>랑 같은 곳인가요?" (only this). yes -> `near-match-accept place "<col 5>" "<col 3>"`; no -> `near-match-refuse place "<col 5>" "<col 3>"`. then `appt-travel-attach <eid>`. never `travel-place-add` here. |
| `unknown_place` | no physical match yet; may be a perfectly good named venue | follow `PLACE_RESOLVE_TASK`: map, hinted map, then WebSearch. Only after all fail ask ONE new identifying detail; never repeat the supplied name/area/address. |

## logged after the fact

The owner adds, fixes or cancels an appointment whose time already passed
("아까 4시 반에 서울브루어리 갔었어"): write it with
`schedule-add ... --kind appointment|task --attendance-mode online|in_person|unknown`
(your judgments) / `appt-upd` / `appt-cancel` like any other. The kit resolves
physical venues silently (saved name/alias -> that row; a new name must resolve
to an address/coordinates; unresolved text stays text), and the owner's own write re-chains that
day's travel legs, past ones included (previous stop -> this stop, this
stop -> next stop / home); pinned, skipped and settled legs stay. Never
hand-make, move or delete travel blocks to fix a past day, and never run a
sweep for it -- sweeps stay future-only.

`TRAVEL_RETRO_RESOLVE<TAB><eid><TAB><origin><TAB><dest>` = a pair of that
day with no minutes on file. silently, for each line:
`bash routines/bundle/travel-resolve.sh "<origin>" "<dest>"`.
`AGENT_BROWSER_MISSING` -> install agent-browser and retry that pair as
described in step 1; this is a repairable dependency, not an unknown trip.
`RESOLVED` / `NEGLIGIBLE` -> after the last line, `appt-travel-attach <eid>`
once (re-chains the day). anything else (`FAILED`, `IMPLAUSIBLE`,
`MODE_UNKNOWN`, `MODE_UNROUTABLE`, `NEAR_MATCH`) -> drop that pair: no question (not the
means, not the address, not the minutes, not "same place?"). say nothing
about the resolve; relay the card. a pair still unknown = no leg; the day
card shows "이동 시간 미정" in that gap. if asked: "그 구간은 이동 시간을 몰라서
비워 뒀어요". never invent a duration.

## rooms and duplicate rows

- venue names a ROOM ("<PLACE> D룸", "12층"): since 050 resolves to the
  PARENT; DB refuses to mint a place from a declared room. room not on file
  -> hand to place-manage (`sub-place`). never work around the refusal.
- room already its own row (pre-050 / forced): two rows for one building,
  same minutes registered twice = a MERGE. place-manage's, needs the user's
  answer. hand both names over; never repoint or retire by hand.

## steps

0. `link_place`: hand the URL VERBATIM to place-manage (it owns
   `map_search.py --link`). back with a name -> `appt-upd <eid>
   location=<name>` (re-runs auto-attach, fresh signal, usually
   `no_duration`) -> step 1. Could not read -> complete the hinted retry
   when a name/area is known, then WebSearch the link and known context.
   Only after lookup is exhausted ask ONE new identifying detail (subject
   to the UI copy gate), never repeat supplied information. Same `appt-upd`.
   Never invent a venue or duration.
1. `bash routines/bundle/travel-resolve.sh "<origin>" "<dest>"` (~15-25s,
   browser once per new pair, then cached).
   - `RESOLVED` / `NEGLIGIBLE` -> `appt-travel-attach <eid>`; tell briefly
     with mode + key legs from field 5:
     "대중교통 55분(간선버스 > 3호선 > 4호선) 기준으로 이동 블록 넣었어요.
     차로 가시면 말씀해 주세요." / NEGLIGIBLE: "가까운 곳이라 이동 블록 없이
     등록했어요." field 5 empty -> minutes only. never paste the raw line.
     one-trip override ("이번엔 차로 갈 거야") -> rerun with `car|walk`, attach
     again, report. NOT a config change. standing change ("요즘은 차로 다녀")
     -> `config-set travel_mode=<m>`.
   - `MODE_UNKNOWN` (exit 4) -> ask once: "보통 뭐 타고 다니세요? 대중교통 /
     자가용 / 도보 / 자전거" -> transit|car|walk|bike -> `config-set
     travel_mode=<m>` -> rerun. never guess, never ask in advance, never re-ask.
   - `MODE_UNROUTABLE` (exit 5, field 4 = means) -> do not re-ask means. ask
     one-way minutes ("<dest>까지 자전거로 편도 몇 분?") -> `travel-edge-add
     "<home place name>" "<dest>" <minutes>` -> `appt-travel-attach <eid>`.
   - `AGENT_BROWSER_MISSING` (exit 9; route.sh exited 4) -> agent-browser
     is not installed. Install it yourself with `npm install -g agent-browser`
     then `agent-browser install`, and retry step 1. This status is also
     relayed by `appt-origin` when its route lookup needs the dependency.
     Do not tell the owner "can't fix", ask them to install it, or ask for
     an address/minutes as a workaround for this status.
   - `FAILED` (exit 1) -> step 2. do NOT ask minutes.
   - `IMPLAUSIBLE` (exit 7) -> nothing stored (the map answered with a
     now-specific ETA, e.g. a late-night wait for the first bus). drop it
     silently: no question, no minutes from field 4; resolve again on a
     later turn.
   `NEAR_MATCH<TAB><name><TAB><id><TAB><existing>` (exit 6) -> nothing
   registered; ask "혹시 <existing>랑 같은 곳인가요?", accept/refuse as in
   the table, re-run.
2. hand `<dest>` (exact token from the signal) to place-manage for the
   complete lookup chain above. Reuse any saved place. A route failure is
   agent work; do not turn it into an address question.
3. back -> retry step 1 once -> `appt-travel-attach <eid>` -> report.
   place-manage returned an edge instead of an address -> attach directly.
   presence path: missing edge never blocks the presence write.

## travel-resolve.sh contract

`bash routines/bundle/travel-resolve.sh "<origin>" "<dest>" [mode]`
- origin "" -> home. mode: explicit arg > recorded `travel_mode` > none
  (no built-in default -> `MODE_UNKNOWN`, touches nothing). modes transit |
  walk | car | fastest (fastest only explicit). rerun with other mode upserts edge.
- out: `RESOLVED<TAB>from<TAB>to<TAB><min><TAB><route>` /
  `NEGLIGIBLE<TAB>from<TAB>to<TAB>0<TAB><route>` / `FAILED<TAB><reason>` /
  `AGENT_BROWSER_MISSING<TAB><install instruction>` /
  `MODE_UNKNOWN<TAB>from<TAB>to` / `MODE_UNROUTABLE<TAB>from<TAB>to<TAB><mode>` /
  `IMPLAUSIBLE<TAB>from<TAB>to<TAB><raw min><TAB><route>` (total far above
  its legs, or above 360 min: never stored).
- `<route>` = "<mode> <total>분: <leg> > <leg> ..." (may be empty; always present on RESOLVED/NEGLIGIBLE).
- exit 0 / 1 FAILED / 4 MODE_UNKNOWN / 5 / 7 IMPLAUSIBLE /
  9 AGENT_BROWSER_MISSING. A route.sh exit 4 becomes exit 9 here, never
  MODE_UNKNOWN. Missing dependency stores nothing. Uses place address when on file,
  else bare name; skips `map_url` addresses.

## venue handling (moved from SKILL.md section 3 step 2)

Pass attendance mode first (reference/verbs.md). For an in-person venue,
use `place-resolve` first. ABSENT = pass the venue TEXT as location, area kept when said ("성수에서 서울 브루어리" -> "서울 브루어리
(성수)"); say nothing about it: no place-manage, no address, no
register question -- the kit resolves physical evidence before saving a new place. past/started event
(logging what happened): NEAR_MATCH -> do not ask, use the text; ask
only what the verb needs (a start time); `TRAVEL_RETRO_RESOLVE` lines
-> reference/travel-leg.md "logged after the fact" (silent). future:
an address is asked only through `TRAVEL_PLACE_NEEDED` (travel-leg.md
step 2).

Before a new schedule write:

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

## Correct a skipped travel leg (2026-11-06)

- `travel-skip-undo <block-id|ulid>` restores an owner-skipped leg on the same
  live anchor. Its inverse is `travel-skip-redo <block-id|ulid>`.
- `travel-move <block> <start> <end>` and `travel-unpin <block>` retain their
  existing move/release meanings; unpin is not the inverse of skip.
- `travel-rule-list` includes retired rules; `travel-rule-show <id|ulid>`
  returns one rule's full stored policy. Existing write verbs are
  `travel-rule-add <def|event> <anchor-ulid> [key=value ...]`,
  `travel-rule-upd <rule> key=value ...` and `travel-rule-retire <rule>`;
  explicit `travel-rule-upd <rule> status=active` re-arms a retired rule.

Skip undo refuses completed/system-retired legs, a settled anchor, or a live
replacement in the same anchor/role. The restored leg remains visible after
travel refresh. Use existing rendered card blocks; add no owner-facing copy.
