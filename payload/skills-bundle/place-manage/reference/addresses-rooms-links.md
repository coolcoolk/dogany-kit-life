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
- several -> ONE pick question.
- exit 5 -> ask once: "이 링크가 어디예요? 장소 이름만 알려주세요." never store
  the URL as a name, never invent an address.

## second address (050)

Ask ONE address, never a second. user VOLUNTEERS another spelling (지번,
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
```

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
sub-place           <place> [add <name>]
place-dupes         [--strong] [--tolerance N] [<place>]   read only
```

- `aliases` pipe-separated; `address` verbatim (commas, spaces kept).
- `python3 .claude/skills/place-manage/map_search.py "<장소명>"` ->
  `[{name, address, link}]` (naver local search; keys NAVER_CLIENT_ID /
  NAVER_CLIENT_SECRET in `runtime/.env`; missing keys -> exit 1 + stderr).
  link = naver place URL with agent-browser, else coord URL.
