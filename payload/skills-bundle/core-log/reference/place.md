# core-log -- place adapter (addresses, nicknames, venues)

The record rules for places. place-manage points here for stated facts;
map search, travel minutes, merges and retirement stay there (place
management).

## an address the owner states

1. `place-resolve <name_or_alias>` (home: the owner's home word; the base
   place answers it).
   - RESOLVED -> `place-address <id> add "<address as said>"` (idempotent;
     a second address of the same place is added, the first kept).
   - NEAR_MATCH -> ask its ASK line once (same place?). yes ->
     `near-match-accept place <ref> "<name>"`, then as RESOLVED; no ->
     `near-match-refuse place <ref> "<name>"`, then as ABSENT.
   - ABSENT -> `travel-place-add "<name>" address=<address>`; `EXISTS 1`
     (exit 3) -> `travel-place-upd "<exact stored name>" address=<address>`.
   - home with no base place on file -> `resource-add --kind env --name
     "<home word>" --detail '{"venue":"home"}'` (creates the base), then
     `place-address <id> add`.
2. One line: the address is recorded. Never ask for travel minutes, never
   offer travel setup.

## a nickname for a known place

`travel-place-upd "<stored name>" aliases=<nickname>|<other>`: one row per
venue, never a second row.

## a venue visited or planned

An appointment's or stay's venue rides on the schedule adapter (location
text). A past venue is never registered, never asked about (no address, no
minutes, no "same place?").

## never

no "shall I register this place?"; no geocoding, travel or merge proposals
while recording.
