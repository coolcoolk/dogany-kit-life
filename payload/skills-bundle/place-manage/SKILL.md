---
name: place-manage
display_name: 장소 등록
description: >-
  Resolve, register or correct a place when a reusable place identity/address is needed, the owner teaches an alias or
  rename/address/retirement, or another skill hands off an unknown place requiring a user question. Use resolver state
  and address need as the entry condition. Merge duplicate rows only on the owner's identification/approval; surface
  duplicate candidates without merging autonomously. Manage kit_life.db places and travel edges through kit_life.sh. A
  venue with room/floor/hall details splits into a place and SUB-PLACE, the only way a room is recorded; a room is not a
  place. NOT for a one-off venue on an appointment, past or future: schedule-log passes it as location text (the kit
  keeps the row) and only hands it here when a planned trip needs its address (TRAVEL_PLACE_NEEDED). Core rule: ask for
  an ADDRESS at most once per place, and never ask for MINUTES as the first move. A bare wish to start place management
  has no venue to register; hand to core-log ## later setup while place management is off, before any address question.
---

For start/use intent with no record/search/place content: `database/kit_life.sh manage-state place` (MANAGE<TAB>place<TAB>on|off<TAB>reason). Off -> core-log `## later setup`; on -> normal use/readback before questions.

# place-manage

The place registry's conversational door. Load only when the user must be
ASKED about a place. reading a known place = `place-resolve` from any skill,
no dialogue, no load.

All via `$PROJECT_ROOT/database/kit_life.sh` (cwd = workspace root; never
SQL). SoT: `place` rows + `place_address` + `sub_place` + `place_link` edges.

Three layers (050). dividing question for a venue string: can a map find
it, and does a trip end there?
1. PLACE = trip terminus, the name the user says. travel edges attach here only.
2. ADDRESS = map-resolvable string (road / lot / building / map_url); many
   per place, one `primary` goes to the map.
3. SUB-PLACE = room / floor / suite / hall. DB refuses a declared room as a
   place (bare or "<장소> <방>"). refusal = register `sub-place`, point the
   booking at the parent. the room of ONE booking = `appt-upd <id>
   room=<ROOM>` (rooms vary per booking). display_name and aliases resolve:
   never mint a place for a name place-resolve returns.

Extra addresses, rooms, map links, verb shapes: read
reference/addresses-rooms-links.md when one of those comes up.
Two rows that are one place, `DUPE-CANDIDATE`, tidy requests: read
reference/merge.md.

## governing rule

Ask for an ADDRESS at most once per place. never ask MINUTES as the first
move. a place with an address, or once map-resolved, is never re-asked.

## steps

An address, nickname or venue the owner STATES is recorded by the
core-log place adapter (`.claude/skills-bundle/core-log/reference/place.md`),
management on or off. The steps below are for a place that has to be
asked about because a caller needs it.

1. resolve first: `place-resolve <name_or_alias>` (exact name-or-alias; no
   fuzzy; `gone` does not resolve).
   - RESOLVED has_address=1 -> nothing to ask. report, stop.
   - RESOLVED has_address=0 -> address owed only if a caller said a route
     failed. never volunteer.
   - VIA_ANCHOR -> query is a routine anchor ('헬스'); destination already on
     file (fields 1-5 = that row). register NOTHING. repair = alias:
     `travel-place-upd "<field 3>" aliases=<query>`. two live rules naming
     different places -> verb answers ABSENT instead.
   - NEAR_MATCH -> not on file, but looks like a place that is ("유디학동치과"
     vs "학동유디치과"). ask the ASK line, nothing else. yes ->
     `near-match-accept place <ref> "<name>"`, then treat as RESOLVED for
     that row. no -> `near-match-refuse place <ref> "<name>"`, then ABSENT.
   - ABSENT -> unknown; continue.
   caller handed a URL instead of a name -> addresses-rooms-links.md "map link".
2. map first: `python3 .claude/skills/place-manage/map_search.py "<name>"`.
   One obvious match -> use its address; unresolved / ambiguous -> step 3.
   Supplied area -> `bash routines/bundle/travel-resolve.sh --coords-lookup
   <place_id> --hint "<area/address>"`; unregistered -> `--coords-query
   "<venue>" --hint "<area/address>"` (read-only, no invented place).
3. agent lookup before asking: `PLACE_RESOLVE_TASK` -> map, hinted map,
   WebSearch. Read reference/addresses-rooms-links.md "Named venue:
   agent-owned resolution before any question" for evidence and guards.
   Read `place-resolution <eid>`; submit WebSearch via `place-resolution <eid> --web-result '<query,candidates JSON>'`.
   Empty candidates only for a real web miss. Tool failure stays an agent
   task; repair, then `place-resolution <eid> --retry`.
   Only after all lookups fail: `place-resolution <eid> --question-asked`,
   then ONE approved question for a new detail / candidate pick. Never a
   generic address question or repeat of supplied name/link/area.
   Lookup success -> resolve travel, `appt-travel-attach <eid>`, verify both.
4. write; verb picked by step-1 verdict:
   - ABSENT -> `travel-place-add "<name>" address=<address> [aliases=a|b]`
   - RESOLVED / VIA_ANCHOR -> `travel-place-upd "<exact stored name>" address=<address>`
   `travel-place-add` on an existing name/alias -> `EXISTS 1`, exit 3,
   nothing written (2.20.0): switch to upd. `--new` only for a genuinely
   distinct venue sharing a name, never to silence the gate.
   `NEAR_MATCH`, exit 5 -> nothing written; ask the ASK line (step 1's
   yes/no). `--new-confirmed` only after he said it is a different place.
5. report one line. verify `place-resolve`: has_address must read 1; still
   0 -> you wrote a duplicate, say so instead of asking again.

## nickname = alias (stops duplicates)

Venue text carries decoration (street, room, calendar "POI, street, city").
clean name the user says -> ALIAS on the existing row:
`travel-place-upd "<stored name>" aliases=<nickname>|<other>`. never a
second row for one venue (exact resolution -> untaught nickname mints a
duplicate next appointment).

## minutes fallback (last resort)

Only when a caller says the route still fails WITH an address on file, or
the user cannot give an address.
1. ask: "집에서 <dest>까지 편도 몇 분?"
2. `travel-edge-add "<home place NAME>" "<dest>" <minutes>` (both directions).
   empty `<from>` errors here (not home): the signal's empty origin field
   must become the home place's name.
3. tell the caller the edge exists; it re-runs its attach verb.
reached here without trying an address = skipped a step.

## gone

`travel-place-remove "<place>"` -> `gone`, pair measures expire, pinned
travel rules retire, history keeps resolving. confirm first: future
appointments naming it stop deriving travel.

## boundaries

- owns place ROW + addresses + rooms + status. blocks referencing a place
  (travel derivation) = schedule-log.
- merge ONLY via `place_merge.py` with user's `--keep` + `--said`. never
  SQL, never DELETE. noticing = yours; deciding = his; offer once, only
  while `kit_life.sh manage-state place` is on (an owner's own merge
  request always runs).
- never register "just in case"; a real appointment or stay names it.
  registering is for places a trip is planned to (home, office, regulars).
  a venue row the kit keeps for an appointment -- a finished outing's
  included -- is not a registration and is never proposed. never ask
  whether to register ("등록해도 될까요?"). a past venue is never asked
  about (no address, no minutes, no "same place?").
- no bold. address per persona (AGENT_ADDRESS in config/agent.conf, else
  locale default).
