# schedule-log -- travel leg derivation

Read when `appt-add` / `appt-upd` / `task-add --place` output carries a
`TRAVEL_PLACE_NEEDED` line (future appointments) or `TRAVEL_RETRO_RESOLVE`
lines (past ones, section "logged after the fact"). No line -> skip all of
this. a past/started appointment never carries `TRAVEL_PLACE_NEEDED`:
nothing to register, no address, no minutes, no question at all.

Execution-axis work (prep/travel BLOCKS). Place knowledge (address, alias,
room, new row, merge) = place-manage; hand off the moment a human must answer.

## signal

`TRAVEL_PLACE_NEEDED<TAB><status><TAB><eid><TAB><location><TAB><origin><TAB><dest>`
(origin empty = home; eid = the appointment id)

Since 050 a signal does NOT mean "no travel block": underivable duration ->
provisional rule (owner default 60 min, `before_src='provisional'`). Signal
keeps firing every sweep until real place/duration lands (`travel-sweep`
`provisional=N`). So ask anyway. never say "이동 시간 잡아놨어요" off a
stopgap; if mentioned: "장소를 몰라서 일단 1시간으로 잡아뒀어요". real answer
-> `travel-sweep` / `appt-travel-attach` overwrites it; no cleanup. video
call: no stopgap, no signal.

| status | what exists | what to do |
|---|---|---|
| `no_duration` / `edge_missing` | a place ROW (minted from location text) | owed DURATION only. step 1. never register the place again. |
| `link_place` | nothing; location is a map link | step 0 first. |
| `near_place` | nothing minted; location LOOKS LIKE a place on file (col 5 = that place's name) | ask "혹시 <col 5>랑 같은 곳인가요?" (only this). yes -> `near-match-accept place "<col 5>" "<col 3>"`; no -> `near-match-refuse place "<col 5>" "<col 3>"`. then `appt-travel-attach <eid>`. never `travel-place-add` here. |
| `unknown_place` | nothing; text is not a name (pasted blob) | ask the venue name -> `appt-upd <eid> location=<name>` (re-runs the signal). |

## logged after the fact

The owner adds, fixes or cancels an appointment whose time already passed
("아까 4시 반에 서울브루어리 갔었어"): write it with `appt-add` / `appt-upd` /
`appt-cancel` like any other. The kit keeps the venue as a place row
silently (exact name/alias -> that row; anything else -> a new row, a look-
alike included; nothing asked), and the owner's own write re-chains that
day's travel legs, past ones included (previous stop -> this stop, this
stop -> next stop / home); pinned, skipped and settled legs stay. Never
hand-make, move or delete travel blocks to fix a past day, and never run a
sweep for it -- sweeps stay future-only.

`TRAVEL_RETRO_RESOLVE<TAB><eid><TAB><origin><TAB><dest>` = a pair of that
day with no minutes on file. silently, for each line:
`bash routines/bundle/travel-resolve.sh "<origin>" "<dest>"`. any
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
   `no_duration`) -> step 1. could not read -> ask once: "이 약속 장소 이름이
   뭐예요? 링크만 저장돼 있어서 이동 시간을 못 잡고 있어요." -> same `appt-upd`.
   never invent a venue or duration.
1. `bash routines/bundle/travel-resolve.sh "<origin>" "<dest>"` (~15-25s,
   browser once per new pair, then cached).
   - `RESOLVED` / `NEGLIGIBLE` -> `appt-travel-attach <eid>`; tell briefly
     with mode + key legs from field 5:
     "대중교통 55분(간선버스 > 3호선 > 4호선) 기준으로 준비/이동 블록 넣었어요.
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
   - `FAILED` (exit 1) -> step 2. do NOT ask minutes.
   - `IMPLAUSIBLE` (exit 7) -> nothing stored (the map answered with a
     now-specific ETA, e.g. a late-night wait for the first bus). drop it
     silently: no question, no minutes from field 4; resolve again on a
     later turn.
   `NEAR_MATCH<TAB><name><TAB><id><TAB><existing>` (exit 6) -> nothing
   registered; ask "혹시 <existing>랑 같은 곳인가요?", accept/refuse as in
   the table, re-run.
2. hand `<dest>` (exact token from the signal) to place-manage: it owns the
   one address question, alias teaching, last-resort minutes question.
   the row already exists (the kit minted it): no "register?" question,
   the address question is the only one.
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
  `MODE_UNKNOWN<TAB>from<TAB>to` / `MODE_UNROUTABLE<TAB>from<TAB>to<TAB><mode>` /
  `IMPLAUSIBLE<TAB>from<TAB>to<TAB><raw min><TAB><route>` (total far above
  its legs, or above 360 min: never stored).
- `<route>` = "<mode> <total>분: <leg> > <leg> ..." (may be empty; always present on RESOLVED/NEGLIGIBLE).
- exit 0 / 1 FAILED / 4 / 5 / 7 IMPLAUSIBLE. fails open. uses place address when on file,
  else bare name; skips `map_url` addresses.
