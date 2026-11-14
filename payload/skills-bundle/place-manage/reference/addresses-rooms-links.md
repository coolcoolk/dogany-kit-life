# place-manage -- extra addresses, rooms, map links, CLI

Read when the user volunteers a second address spelling, names a room /
floor / hall, when a caller hands you a map LINK, or when you need a verb shape.

## map link instead of a name (`link_place` handoff)

Sharing a map link is how the user normally names a venue. read before asking:

```
python3 .claude/skills/place-manage/map_search.py --link "<url>"
  -> [{name, address, link}]
  exit 5 + [] -> could not read (reason on stderr)
```

Expands short link, reads name + road address off the page. no keys, no
agent-browser needed. never guesses: unknown host (`meet.google.com`, kakao,
google map links) or unreadable page -> exit 5. naver only so far.

- one candidate -> `place-resolve` it, register (SKILL step "register"),
  hand the NAME back; caller runs `appt-upd <eid> location=<name>`.
- several -> hinted retry and WebSearch first; if still ambiguous, ONE pick question.
- exit 5 -> WebSearch the link and supplied venue/area first. Only after
  lookup is exhausted ask ONE new identifying detail. Never repeat a supplied
  name/link/area, store the URL as a name, or invent an address.

## Named venue: agent-owned resolution before any question

The owner giving a venue (including a parenthesized area such as
`만경 양육관 (사당역 쪽)`) is enough to start work. Never ask him to run
registration/travel commands or to repeat a supplied name, address or area.
`PLACE_RESOLVE_TASK` overrides older instructions that ask for an address.

1. Run `map_search.py "<venue>"` (the appointment write does this in code).
2. Retry the map using the supplied area, parsed from location itself.
   Registered place: `travel-resolve.sh --coords-lookup <id> --hint "<area>"`.
   Unregistered: `travel-resolve.sh --coords-query "<venue>" --hint "<area>"`
   (read-only: no invented place or region-only route endpoint).
3. If unresolved or ambiguous, use the **WebSearch tool** for
   `"<venue> <area>"`. Follow the sources to verify venue/address/branch;
   a web-discovered address or map link is new lookup input, not a question
   for the owner. `place-resolution <eid>` reads the exact query and saved
   attempts/candidates. Submit WebSearch evidence through
   `place-resolution <eid> --web-result '{"query":"<exact query>","candidates":[{"name":"<venue>","address":"<verified address>","url":"<source URL>"}]}'`.
   Empty candidates record an actual completed web miss. Do not submit an
   empty result when the web tool was unavailable. A unique exact venue
   with verified address is saved and the appointment re-derived.
4. Resolve the travel pair with `travel-resolve.sh`, then
   `appt-travel-attach <eid>`. Continue after a successful web lookup; the
   task is complete only when the place and travel both land.

Record `place-resolution <eid> --question-asked` before the single approved
question; a dependency failure cannot pass this guard. `--retry` redoes a
failed lookup after repair, preserving the ask-once boundary. Web tool
failure is not a completed empty WebSearch.

Only after map, hinted map and web have all been tried may you ask ONE
specific question for new information: choose among found candidates, or
supply the missing identifying detail. No generic address question after a
named venue. Dependency/tool failure stays an agent task. Machine codes and
verbs never go to owner copy. Unapproved new question text must stay under
the UI copy gate; do not invent a new morning-brief sentence.

## area hint after a wrong branch or failed lookup

Owner supplies an area/address ("판교야") -> retry the map before asking
for the exact name:

```
bash routines/bundle/travel-resolve.sh --coords-lookup <place_id> --hint "판교"
```

This saves `판교 <stored name>` as the primary address through
`travel-place-upd` and queries it first, falling back to the bare name on a
miss. An area alone must never be the route endpoint. A repeat lookup uses
the saved query. No coordinates or travel edges are stored by this lookup.

`COORD_CANDIDATES` + up to three `COORD_CANDIDATE` rows (rank, name,
address) -> retain the candidates and verify venue/area with WebSearch.
A bare-name fallback may be outside the owner's area; never assume it fits.
A unique verified match can be saved without a confirmation question:
`travel-place-upd <place> address="<address>"`, retry lookup, then
`place-coord-set <place> '<naver end>' --source naver_lookup`.
If ambiguity remains after the complete chain, offer the found candidates
in ONE pick question. No address on a candidate -> retry with its name and
the supplied hint, then web; never ask for an address already supplied.

No candidates after the hinted retry / all rejected -> run WebSearch for
venue + area first (the chain above). Only after every lookup fails, ask
one new identifying detail, never an already supplied address, and mark `place-coord-set <place> --missing`. Never leave
empty coordinates silently or stop after a single web search.

`AGENT_BROWSER_MISSING` is a dependency failure: install the browser and
retry as directed by the relay; do not ask the owner for an address.

## second address (050)

Ask at most ONE new identifying detail only after the complete lookup
chain; never repeat a supplied address. User VOLUNTEERS another spelling (지번,
map link, building name) -> record it:

```
place-address <place>                      list (primary first)
place-address <place> add <value> [kind]   teach one more
place-address <place> primary <value>      which one the map gets
place-address <place> remove [<value>]     forget one (default: primary)
```

- `kind` = road|lot|building|map_url|legacy. OMIT unless user said it; verb
  classifies (URL -> map_url, 도로명+건물번호 -> road, else legacy = "not yet
  classified"). never guess (지번 and apartment number share a shape).
- new address does NOT take primary unless user says so or map keeps failing.
- `map_url` never goes to the map puller; a link-only place still needs a real address.
- `address=` on add/upd = PRIMARY address; blank clears it.

## rooms / floors / halls (050)

```
sub-place <place>              list rooms on file
sub-place <place> add <name>   declare one
sub-place <place> rename <old> <new>     bookings keep the room
sub-place <place> rm <name> [--into <room>]   IN_USE (exit 4) while a booking names it
```

A room may not carry a group's or a person's name (DB refuses: CROSS_KIND) --
a band named 피로물든방 is a group (`appt-group`), never a room. An unknown room
accrues silently only when it has the shape of its siblings (A2룸 beside
A3룸/D룸); otherwise the kit asks TYPE_ASK once.

Declaring closes the hole: DB then refuses to mint a place under that room
name, and "<장소> <방>" venue text resolves to the parent.
- user says place + room -> register PLACE (with address), then declare
  room. two verbs, no extra question.
- existing address is really "건물 + 층" -> declare floor as sub-place,
  `place-address <place> add "<address without floor>"` then `primary` it.
  cannot tell where address ends -> ASK; never cut on a hunch.
- never ask "몇 층이에요?" unprompted.

## CLI (kit_life.sh)

```
place-resolve       <name_or_alias>   -> RESOLVED|VIA_ANCHOR|ABSENT (read)
travel-place-add    <name> [aliases=a|b address=S note=S] [--new]
                      match -> EXISTS 1 + row, exit 3, nothing written
                      may append DUPE-CANDIDATE lines
travel-place-upd    <place> [name=S aliases=a|b address=S note=S]
travel-place-remove <place>           -> status 'gone'
travel-edge-add     "<from>" "<to>" <minutes>   both directions;
                      both ends must NAME a place ("" is NOT home here)
place-address       <place> [add <v> [kind] | primary <v> | remove [<v>]]
sub-place           <place> [add <name> | rename <old> <new> | rm <name> [--into <room>]]
place-dupes         [--strong] [--tolerance N] [<place>]   read only
```

- `aliases` pipe-separated; `address` verbatim (commas, spaces kept).
- `python3 .claude/skills/place-manage/map_search.py "<장소명>"` ->
  `[{name, address, link}]` (naver local search; keys NAVER_CLIENT_ID /
  NAVER_CLIENT_SECRET in `runtime/.env`; missing keys -> exit 1 + stderr).
  link = naver place URL with agent-browser, else coord URL.

## Correcting saved spatial facts

Use `place-coord-reset <place>` or `place-region-reset <place>` when the
owner retracts those facts. The place stays registered and its facts become
unknown. Use the existing set verbs to restore owner-confirmed values.
`travel-edge-retract <from> <to>` retracts only that ordered pair and refreshes
its future derived travel; the reverse pair and completed history stay intact.
Restore it with `travel-edge-add <from> <to> <minutes> [prep_min]`.
