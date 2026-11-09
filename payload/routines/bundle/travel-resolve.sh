#!/usr/bin/env bash
# travel-resolve.sh -- deterministic auto travel-time -> kit_life edge registration.
#
# Wires the keyless Naver-map route puller (place-view/route.sh) into automatic
# travel_edge registration, so appointment logging can resolve "편도 몇 분?" without
# asking the owner. No LLM in this path: route.sh drives map.naver.com via
# agent-browser and prints route lines; this script parses the RECOMMENDED
# duration and registers the edge (both directions).
#
# usage: travel-resolve.sh [--coords-only] <origin> <dest> [mode]
#   --coords-only (kit 2.42.0, coordinate backfill): drive the map for two
#            KNOWN places only to store where both ends are (066
#            place-coord-set). No minutes, no edge, no place created; the mode
#            defaults to the recorded means or transit (the ends do not depend
#            on it). Prints COORDS<TAB><from><TAB><to><TAB><ends stored>
#            (exit 0, 1 or 2 ends) or FAILED (exit 1). Driven by
#            place-coord-backfill.sh over `kit_life.sh place-coord-missing`.
#            (2026-10-08) Both ends are resolved by the KIT first
#            (`kit_life.sh place-route-end`, the lookup place-coord-set
#            uses), never by a raw-sqlite probe: an end not on file is
#            FAILED "not on file: <name>" before any map drive. Each end is
#            driven by its address; the base place with none by the
#            instance's ROUTE_HOME_ADDR; a base / stay place with neither is
#            never typed into the map by its nickname -- instead
#              NEEDS_ADDRESS<TAB><place id><TAB><name>   (one line per end,
#            exit 8, nothing driven). Ends are stored by ulid, and a refused
#            store names its result (not_found / bad_ref / ...).
#   --coords-query <venue text> [mode] [--hint "<area/address>"]
#            read-only lookup for a venue not yet registered; saves nothing.
#   --coords-lookup <place id|name> [mode] [--hint "<area/address>"]
#            Saves "<hint> <name>" as the primary address and queries it first,
#            then the bare name on no match. Prints up to 3 COORD_CANDIDATE
#            rows (rank, name, address); COORD_CANDIDATES means the owner
#            must pick. Never auto-selects among multiple map matches.
#            Coordinates/edges wait for confirmation.
#            (coordinates at registration, owner
#            2026-10-07 17:40): drive home -> place and print
#            COORD_FOUND<TAB><place><TAB><naver end> (the end's 3rd field is
#            the name the map matched) or FAILED. Stores no coordinates or
#            edges: the agent
#            shows the match in the owner-confirm step that confirms the
#            place, then `kit_life.sh place-coord-set <place> '<end>'`.
#   origin : origin place name/address (usually 집/home base). required.
#   dest   : destination place name/address. required.
#   mode   : transit | walk | car | fastest   (optional)
#            fastest = min(transit, walk).
#
#            NO CODE DEFAULT (owner correction 2026-09-25): which way the user
#            travels is the user's own resource, not a product constant. The
#            2.27.0 default `fastest` silently asserted a walk; the first 2.27.1
#            draft swapped in `transit`, which only asserted a different mode.
#            Resolution order:
#              1. explicit [mode] argument (per-trip override)
#              2. the user's recorded means: config key `travel_mode`
#                 (written on demand with `kit_life.sh config-set travel_mode=<m>`,
#                 m in transit|walk|car|bike)
#              3. neither -> MODE_UNKNOWN (below). Nothing is guessed, no map is
#                 driven, nothing is registered. The caller asks the user ONCE,
#                 records the answer, and re-runs.
#            `fastest` is accepted as an explicit argument only; it is never a
#            recorded means.
#
# output (stdout, machine-readable, exactly ONE result line except for
# candidate rows in --coords-lookup):
#   RESOLVED   <TAB> <from> <TAB> <to> <TAB> <minutes> <TAB> <route>
#   NEGLIGIBLE <TAB> <from> <TAB> <to> <TAB> 0          <TAB> <route>
#     <minutes> grid-ceiled, >0 for RESOLVED.
#     <route> = "<mode> <raw>분: <leg> > <leg> ..." (added 2026-09-23). May be
#     empty, but the field is always present so tab arity is stable. The caller
#     is expected to SHOW this to the owner, not just the number -- it is what
#     lets him say "no, I'm driving this one".
#   FAILED     <TAB> <reason>                            (fall back to manual ask)
#   AGENT_BROWSER_MISSING <TAB> <install instruction>   (exit 9)
#     route.sh exited 4: agent-browser is not installed. The agent installs
#     it and retries; do not ask the owner for an address or duration.
#     Distinct from this script's MODE_UNKNOWN (exit 4).
#   MODE_UNKNOWN    <TAB> <from> <TAB> <to>              (exit 4)
#     no mode argument and no recorded `travel_mode`. Ask the user once which
#     means they use, `config-set travel_mode=<m>`, re-run.
#   MODE_UNROUTABLE <TAB> <from> <TAB> <to> <TAB> <mode> (exit 5)
#     the recorded means has no map route (e.g. bike: route.sh knows only
#     transit/walk/car). Not an error in the user's answer -- ask the one-way
#     minutes for this pair and `travel-edge-add` it; never re-ask the means.
#   IMPLAUSIBLE <TAB> <from> <TAB> <to> <TAB> <raw minutes> <TAB> <route> (exit 7)
#     (2026-10-05) the map answered, but its total is not a typical
#     door-to-door duration (see PLAUSIBILITY below): nothing registered, no
#     place minted. Callers drop it silently -- never store, never ask.
#   NEAR_MATCH <TAB> <name> <TAB> <existing_id> <TAB> <existing_name> (exit 6)
#     (2026-09-28) <name> is not on file but looks like <existing_name>.
#     Nothing registered. Ask "혹시 <existing_name>랑 같은 곳인가요?" --
#     yes: `kit_life.sh near-match-accept place <existing_id> "<name>"`,
#     no: `kit_life.sh near-match-refuse place <existing_id> "<name>"`;
#     then re-run.
#
# exit: 0 on RESOLVED/NEGLIGIBLE, 1 on FAILED, 4 on MODE_UNKNOWN, 5 on
#   MODE_UNROUTABLE, 6 on NEAR_MATCH, 7 on IMPLAUSIBLE, 8 on NEEDS_ADDRESS
#   (--coords-only). Fail-open + never hang: any route.sh error / parse miss ->
#   FAILED so the caller asks the owner.
#
# side effect: registers BOTH directions in kit_life (travel_edge_add via
#   travel-edge-add), creating dest/origin places (travel-place-add) if unknown.
#   Grid = 15 min (edge minutes are grid-ceiled by kit_life). Walking distance
#   floors at one grid unit, not 0 -- see NEGLIGIBLE_THRESHOLD below.
#
# 035 address routing: when the origin/dest place row carries an `address`,
#   THAT string is driven into the map; the place NAME is still what gets
#   registered in kit_life. A place with no address routes by name exactly as
#   before, and a probe failure of any kind (including a pre-035 DB that has no
#   such column) degrades to name-routing silently. Fail-open is unchanged:
#   every failure path still prints FAILED and exits nonzero, so the caller
#   still asks the user.
set -uo pipefail

# ---- constants / config -----------------------------------------------------
# GRID: the travel grid unit (min).
#
# NEGLIGIBLE_THRESHOLD (owner call 2026-08-22): walking distance is still
# travel. The threshold used to be one full grid unit, so anything under 15 min
# registered as 0 -- which tells the planner the owner TELEPORTS to the place,
# and lets the calendar pack blocks right up against the appointment start. A
# 10-minute walk is not "no travel"; it is travel the grid cannot express more
# finely than one unit. So the floor is now one grid unit, and only a genuinely
# zero-length leg (route parsed 0 -- same place) stays NEGLIGIBLE.
# Both overridable via env; GRID default matches kit_life's LIFEKIT_TRAVEL_GRID_MIN=15.
GRID="${LIFEKIT_TRAVEL_GRID_MIN:-15}"
NEGLIGIBLE_THRESHOLD="${TRAVEL_NEGLIGIBLE_THRESHOLD:-1}"

# PLAUSIBILITY (measured 2026-10-05 22:20 KST): a brewery -> home query came
# back "transit 641 min" whose five legs (walk, village bus, walk, trunk bus,
# walk) sum to 33, and the edge was stored at 645 both ways. The
# map routes a transit query departing NOW, so a late-night query waits for
# the first morning bus and that wait lands in the total. route.sh has no
# departure-time parameter, and a stored edge is a typical door-to-door
# duration, not a now-specific ETA -- so an answer like that is refused:
#   * the total exceeds max(2 x legs, legs + TRAVEL_WAIT_SLACK_MIN) when the
#     line names its legs (transfers and ordinary waits fit inside that), or
#   * the total exceeds TRAVEL_RESOLVE_MAX_MIN in any mode.
# Refused = IMPLAUSIBLE (exit 7): no place, no edge. Minutes are never
# invented from the legs either -- the leg sum leaves out every wait.
WAIT_SLACK="${TRAVEL_WAIT_SLACK_MIN:-60}"
MAX_MIN="${TRAVEL_RESOLVE_MAX_MIN:-360}"

# route.sh caps route driving; give it a hard ceiling so we never hang.
ROUTE_TIMEOUT="${TRAVEL_ROUTE_TIMEOUT:-90}"

# ---- path resolution (robust: env override, else walk up from script) -------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# PROJECT_ROOT = the agent workspace root. This script is installed at
# <root>/routines/bundle/, so the root is TWO levels up (2026-10-08: one level
# resolved <root>/routines and only the walk-up below kept it working).
# DELIBERATELY NOT "${PROJECT_ROOT:-...}": PROJECT_ROOT is exported by every
# agent runtime, so honouring an inherited value made this script resolve
# kit_life.sh -- and therefore the DB it WRITES places and edges into -- against
# whichever agent happened to invoke it. Cross-agent invocation (a crew agent
# running Ag's routine) then silently registered the places in the caller's DB
# and reported "edge-add failed" against a DB the owner never asked about.
# A routine belongs to the workspace it lives in; explicit LIFEKIT_SH / ROUTE_SH
# / TRAVEL_DB remain the supported overrides.
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Resolve kit_life.sh: env override, then the conventional database/ path, then
# a bounded walk-up looking for database/kit_life.sh.
resolve_path() {
  # $1 = env value (may be empty); $2 = relative path under PROJECT_ROOT;
  # $3 = leaf to walk-up search for. echoes the resolved absolute path or "".
  local envval="$1" rel="$2" leaf="$3"
  if [ -n "$envval" ] && [ -e "$envval" ]; then printf '%s' "$envval"; return 0; fi
  if [ -e "$PROJECT_ROOT/$rel" ]; then printf '%s' "$PROJECT_ROOT/$rel"; return 0; fi
  local d="$SCRIPT_DIR"
  local i=0
  while [ "$d" != "/" ] && [ $i -lt 8 ]; do
    if [ -e "$d/$leaf" ]; then printf '%s' "$d/$leaf"; return 0; fi
    d="$(dirname "$d")"; i=$((i + 1))
  done
  printf ''
}

LIFEKIT="$(resolve_path "${LIFEKIT_SH:-}" "database/kit_life.sh" "database/kit_life.sh")"
ROUTE="$(resolve_path "${ROUTE_SH:-}" ".claude/skills/place-view/route.sh" ".claude/skills/place-view/route.sh")"

# The DB kit_life writes to: fixed at <dir-of-kit_life.py>/kit_life.db. We resolve
# it ourselves ONLY to probe place existence read-only (no getter CLI exists).
# Override via TRAVEL_DB (the /tmp-copy test uses this). Default = kit_life.db
# beside the resolved kit_life.sh (symlink followed by sqlite3 at open time).
if [ -n "${TRAVEL_DB:-}" ]; then
  DB="$TRAVEL_DB"
elif [ -n "$LIFEKIT" ]; then
  DB="$(cd "$(dirname "$LIFEKIT")" && pwd)/kit_life.db"
else
  DB=""
fi

# ---- helpers ----------------------------------------------------------------
fail() { printf 'FAILED\t%s\n' "$1"; exit 1; }

# ceil to the grid.
ceil_grid() {
  local m="$1"
  printf '%s' "$(( ( (m + GRID - 1) / GRID) * GRID ))"
}

# Parse the RECOMMENDED duration (minutes) for a mode out of route.sh output.
# Reads route.sh stdout on fd/arg $1 (a file), $2 = mode. echoes an integer, or
# nothing (caller treats empty as parse-miss).
#
# Output shapes (validated against live route.sh, 2026-08-16):
#   transit: section "=== 대중교통 ===", recommended line starts "최적 N분 ...";
#            other lines "최소시간 · 최소환승 N분", "최소도보 N분", or bare "N분 ...".
#   walk   : section "=== 도보 ===", recommended line starts "추천 N 분 ...".
#   car    : section "=== 자동차 ===", recommended line contains "실시간 추천 N 분".
# Strategy: isolate the mode's section, prefer the recommended line, else take
# the first data line (parse_line); parse_minutes turns it into total minutes.
# parse_line is also what route_summary reads, so the reported leg is always
# the line the figure came from.
parse_line() {
  local out="$1" mode="$2"
  local header rec_pat section line
  case "$mode" in
    transit) header='=== 대중교통 ==='; rec_pat='^최적' ;;
    walk)    header='=== 도보 ===';     rec_pat='^추천' ;;
    car)     header='=== 자동차 ===';   rec_pat='실시간 추천' ;;
    *) return 1 ;;
  esac
  section="$(awk -v h="$header" '
    $0==h {grab=1; next}
    grab && /^=== /{grab=0}
    grab {print}
  ' "$out")"
  [ -n "$section" ] || return 1
  line="$(printf '%s\n' "$section" | grep -E "$rec_pat" | head -1)"
  [ -n "$line" ] || line="$(printf '%s\n' "$section" | grep -vE '^\s*$' | head -1)"
  [ -n "$line" ] || return 1
  printf '%s' "$line"
}

parse_minutes() {
  local line
  line="$(parse_line "$1" "$2")" || return 1

  # Total minutes from "N 시간 M 분" / "N시간" / "M 분".
  #
  # MEASURED BUG (2026-09-23, owner-visible): this used to take the first
  # "<int> 분" match only, so naver's walk line "추천 3 시간 11 분 11km"
  # parsed as 11 -- the hour term was silently dropped. With MODE=fastest
  # taking min(transit, walk), a 3h11m walk beat a 55m transit leg and the
  # 집->본가 edge resolved to 15 (one grid unit) instead of 55.
  #
  # The error grows with distance: under an hour the walk line has no 시간
  # term and parses correctly, so only LONG legs -- exactly the ones where
  # travel time matters -- came out short.
  #
  # Strip the hour term before scanning for 분 so a spaced "3 시간" cannot be
  # re-read as the minute figure.
  local hours mins rest total
  hours="$(printf '%s' "$line" | grep -oE '[0-9]+[[:space:]]*시간' | head -1 \
           | grep -oE '[0-9]+' | head -1)"
  rest="$(printf '%s' "$line" | sed -E 's/[0-9]+[[:space:]]*시간//')"
  mins="$(printf '%s' "$rest" | grep -oE '[0-9]+[[:space:]]*분' | head -1 \
          | grep -oE '[0-9]+' | head -1)"
  [ -n "$hours" ] || [ -n "$mins" ] || return 1
  total=$(( ${hours:-0} * 60 + ${mins:-0} ))
  printf '%s' "$total"
}

# Does a place with this EXACT name (or alias) already exist? read-only probe.
# place.name has NO unique constraint, so blind travel-place-add would create a
# duplicate row on every resolve. kit_life exposes no place-get CLI, so we probe
# the DB read-only (same DB kit_life writes to). On any probe uncertainty we err
# toward NOT adding is unsafe (edge-add would then miss) -> we err toward adding
# only when the probe is CONCLUSIVELY absent; a probe failure => treat as
# unknown and let place-add run (worst case: a duplicate, never a hard failure).
# The match predicate both probes share: exact canonical name, OR an element of
# the aliases JSON array.
#
# The alias arm decodes the JSON with json_each instead of pattern-matching the
# stored text. kit_life writes aliases with ensure_ascii=True, so a Korean alias
# is stored ESCAPED ("신논현") and the old raw LIKE on '%"<name>"%'
# could never match one -- place_exists answered "absent" for every place the
# user had taught a Korean nickname to, and the caller below then minted a
# DUPLICATE place row on every resolve. json_each compares decoded values, so
# '신논현' finds the place that carries it.
#
# The LIKE arm is kept for legacy non-JSON alias blobs (older rows hold a
# pipe-joined string); json_each cannot run on those at all, and json_valid
# gates it so a malformed blob is skipped rather than raising -- the same
# tolerance kit_life's own json.loads path has.
place_match_sql() {
  local q; q="$(sql_quote "$1")"
  printf '%s' "name=$q \
 OR (aliases IS NOT NULL AND json_valid(aliases) AND EXISTS ( \
       SELECT 1 FROM json_each(place.aliases) WHERE json_each.value=$q)) \
 OR (aliases IS NOT NULL AND aliases LIKE $(sql_quote "%\"$1\"%"))"
}

place_exists() {
  local name="$1"
  [ -n "$DB" ] && [ -r "$DB" ] || return 1   # can't probe -> not-known-present
  # exact canonical name OR alias JSON contains it. read-only SELECT.
  local hit
  hit="$(sqlite3 "$DB" \
    "SELECT 1 FROM place WHERE $(place_match_sql "$name") LIMIT 1;" \
    2>/dev/null)"
  [ "$hit" = "1" ]
}

# minimal SQL string literal quoting (single-quote wrap, escape embedded quotes).
sql_quote() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/''/g")"; }

# The map-routable string for a place, if it has one (035, extended 050). Same
# read-only probe surface as place_exists(), matched the same way (name OR
# alias), so a decorated location string finds the same row the deriver would
# find.
# Echoes nothing AND returns nonzero when the place is unknown, has no address,
# has a blank address, or the probe fails for ANY reason -- including the
# address column not existing yet. Every one of those means "route the name",
# which is precisely pre-035 behaviour, so this function cannot make routing
# worse than it is today and needs no version gate of its own.
#
# 050: a place may now hold SEVERAL strings (place_address), and one of the
# legal kinds is `map_url`. A URL cannot be typed into map autocomplete -- it
# is a link to be OPENED -- so handing the primary over blindly would hand the
# puller a string it can only fail on. The order below is therefore not a
# preference, it is what the map can eat:
#   1. the primary, unless it is a map_url
#   2. any road-name address
#   3. any other typeable address (lot / building / legacy)
#   4. nothing -> route the NAME, i.e. exactly pre-035 behaviour
# A map_url is never returned by this function at all. Reading that link is the
# skill layer's job (place-manage/map_search.py --link), which has the network;
# this path does not.
# The whole query is guarded: on a pre-050 database place_address does not
# exist, sqlite3 errors, and the `||` fallback reads the single mirror column
# exactly as 035 did.
place_address() {
  local name="$1" addr match
  [ -n "$DB" ] && [ -r "$DB" ] || return 1
  match="$(place_match_sql "$name")"
  addr="$(sqlite3 "$DB" \
    "SELECT pa.value FROM place_address pa JOIN place ON place.id = pa.place_id \
      WHERE ($match) AND pa.kind <> 'map_url' AND trim(pa.value) <> '' \
      ORDER BY pa.is_primary DESC, (pa.kind = 'road') DESC, pa.id \
      LIMIT 1;" 2>/dev/null)"
  if [ -z "$addr" ]; then
    addr="$(sqlite3 "$DB" \
      "SELECT address FROM place \
        WHERE ($match) \
          AND address IS NOT NULL AND trim(address) <> '' \
          AND address NOT LIKE 'http://%' AND address NOT LIKE 'https://%' \
        LIMIT 1;" 2>/dev/null)"
  fi
  [ -n "$addr" ] || return 1
  printf '%s' "$addr"
}

# Ensure a place exists in kit_life; create it only if the probe says it's absent.
# $2 (optional) = pipe-separated aliases to register alongside the canonical name.
# The alias is not decoration: when the caller handed us a decorated location
# string (Google Calendar's "POI, <street address>, <city>, <country>") route.sh
# routes the degraded bare POI, so the place gets the CLEAN name -- and the
# deriver, which matches location text against name-or-alias EXACTLY, would then
# never find it from the appointment's stored location. The raw string must ride
# along as an alias or the whole resolve is cosmetic.
# fails the script (via fail) only on a hard kit_life error.
#
# EXIT 3 IS SUCCESS HERE (kit_life 2.20.0). travel-place-add grew the DGN-231
# dedup gate: an existing name now prints `EXISTS 1` + the row and exits 3
# WITHOUT writing. That is exactly what this function wants -- "the place is
# there" -- so a bare `|| fail` would turn the new guard into a routine
# failure. The probe above still runs first (it is what decides whether the
# ALIASES get registered), but the verb is now the authority: when the probe
# false-negatives -- an unreadable DB, a legacy alias blob json_each cannot
# walk -- the old code minted a DUPLICATE and reported success. Now it does
# not write at all, and this returns 0.
#
# EXIT 5 IS A QUESTION (2026-09-28). travel-place-add refuses a name that looks
# like a place already on file ("유디학동치과" beside "학동유디치과") and prints a
# NEAR_MATCH block. This script has nobody to ask, so it stops with the
# NEAR_MATCH result line (exit 6) and registers NOTHING -- no place, no edge.
# Minting here is exactly how the owner got asked a question he had answered.
ensure_place() {
  local name="$1" aliases="${2:-}" rc=0 out
  if place_exists "$name"; then return 0; fi
  if [ -n "$aliases" ]; then
    out="$("$LIFEKIT" travel-place-add "$name" "aliases=$aliases" 2>/dev/null)" || rc=$?
  else
    out="$("$LIFEKIT" travel-place-add "$name" 2>/dev/null)" || rc=$?
  fi
  if [ "$rc" -eq 5 ]; then
    # 2nd line of the block = the best candidate: ref<TAB>name<TAB>signal<TAB>matched
    local cand
    cand="$(printf '%s\n' "$out" | sed -n '2p')"
    printf 'NEAR_MATCH\t%s\t%s\t%s\n' "$name" \
      "$(printf '%s' "$cand" | cut -f1)" "$(printf '%s' "$cand" | cut -f2)"
    exit 6
  fi
  [ "$rc" -eq 0 ] || [ "$rc" -eq 3 ] || fail "place-add failed for $name (rc=$rc)"
}

# Register a one-way edge from->to with the given minutes (0 => negligible).
register_edge() {
  local from="$1" to="$2" minutes="$3"
  "$LIFEKIT" travel-edge-add "$from" "$to" "$minutes" >/dev/null 2>&1 \
    || fail "edge-add failed $from -> $to ($minutes)"
}

# ---- arg validation ---------------------------------------------------------
COORDS_ONLY=0
LOOKUP=0
LOOKUP_HINT=""
LOOKUP_QUERY=0
DURATION_LOOKUP=0
if [ "${1:-}" = "--lookup-only" ]; then DURATION_LOOKUP=1; shift; fi
if [ "${1:-}" = "--coords-only" ]; then COORDS_ONLY=1; shift; fi
if [ "${1:-}" = "--coords-lookup" ] || [ "${1:-}" = "--coords-query" ]; then
  # Query an unregistered venue without minting a place or saving a hint.
  [ "$1" != "--coords-query" ] || LOOKUP_QUERY=1
  # one place (id or name): drive home -> place, print what the map matched
  # for the place. An explicit hint is saved; coordinates await confirmation.
  COORDS_ONLY=1; LOOKUP=1; shift
  _p="${1:-}"
  [ -n "$_p" ] || fail "coords-lookup: place required"
  shift
  _lookup_mode=""
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --hint)
        [ "$#" -ge 2 ] || fail "coords-lookup: --hint needs an area/address"
        LOOKUP_HINT="$(printf '%s' "$2" | tr '\t\r\n' '   ' | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
        [ -n "$LOOKUP_HINT" ] || fail "coords-lookup: --hint must not be blank"
        shift 2 ;;
      transit|walk|car|fastest)
        [ -z "$_lookup_mode" ] || fail "coords-lookup: duplicate mode"
        _lookup_mode="$1"; shift ;;
      *) fail "coords-lookup: unexpected argument $1" ;;
    esac
  done
  # Duration-only callers must remain read-only, including combined options.
  [ "$DURATION_LOOKUP" -eq 0 ] || [ -z "$LOOKUP_HINT" ] \
    || fail "coords-lookup: --hint cannot be combined with --lookup-only"
  if [ "$LOOKUP_QUERY" -eq 0 ] && [ -n "$DB" ] && [ -r "$DB" ]; then
    case "$_p" in
      *[!0-9]*) ;;
      *) _p="$(sqlite3 "$DB" "SELECT name FROM place WHERE id=$_p;" 2>/dev/null)" ;;
    esac
    _isbase="$(sqlite3 "$DB" "SELECT 1 FROM place WHERE is_base=1 AND ($(place_match_sql "$_p"));" 2>/dev/null)"
    [ -z "$_isbase" ] || fail "coords-lookup: $_p is the base place (look it up from another place: --coords-only)"
  fi
  [ -n "$_p" ] || fail "coords-lookup: no such place"
  if [ -n "$LOOKUP_HINT" ] && [ "$LOOKUP_QUERY" -eq 0 ]; then
    # Save a venue query, never a region-only endpoint. Use the canonical
    # name even when the caller provided an alias.
    place_exists "$_p" || fail "coords-lookup: no such place ($_p)"
    _p="$(sqlite3 "$DB" "SELECT name FROM place WHERE $(place_match_sql "$_p") LIMIT 1;" 2>/dev/null)"
    [ -x "$LIFEKIT" ] || fail "coords-lookup: kit_life.sh not found"
    "$LIFEKIT" travel-place-upd "$_p" "address=$LOOKUP_HINT $_p" >/dev/null 2>&1 \
      || fail "coords-lookup: could not save hint for $_p"
  fi
  set -- "" "$_p" "$_lookup_mode"
fi
ORIGIN="${1:-}"
DEST="${2:-}"
MODE="${3:-}"

[ -n "$DEST" ]   || fail "dest required"

# ---- mode: explicit arg > the user's recorded means > MODE_UNKNOWN ----------
# The recorded means lives in the kit's key-value config table (key
# travel_mode), the same store core-setup fills with life_skeleton_* via
# config-set. Read-only probe, like the base-place lookup below. An unreadable
# DB is FAILED, not MODE_UNKNOWN: asking the user would not help, the answer
# could not be read back and the caller would loop.
if [ -z "$MODE" ] && [ "$COORDS_ONLY" -eq 1 ] && ! { [ -n "$DB" ] && [ -r "$DB" ]; }; then
  MODE="transit"   # the ends do not depend on the mode; no DB read needed
fi
if [ -z "$MODE" ]; then
  [ -n "$DB" ] && [ -r "$DB" ] || fail "travel_mode unreadable (no kit_life db)"
  MODE="$(sqlite3 "$DB" "SELECT TRIM(value) FROM config WHERE key='travel_mode' LIMIT 1;" \
          2>/dev/null | tr '[:upper:]' '[:lower:]')"
  case "$MODE" in
    transit|walk|car) ;;
    *) [ "$COORDS_ONLY" -eq 1 ] && MODE="transit" ;;
  esac
  case "$MODE" in
    transit|walk|car) ;;
    '') printf 'MODE_UNKNOWN\t%s\t%s\n' "$ORIGIN" "$DEST"; exit 4 ;;
    *)  printf 'MODE_UNROUTABLE\t%s\t%s\t%s\n' "$ORIGIN" "$DEST" "$MODE"; exit 5 ;;
  esac
fi

# ---- --coords-only: the kit resolves both ends before anything is driven ----
# (2026-10-08, live backfill 21/21 failed). See the usage block above.
home_addr() {   # the instance's map-searchable home address (route.sh's rule)
  if [ -n "${ROUTE_HOME_ADDR:-}" ]; then printf '%s' "$ROUTE_HOME_ADDR"; return; fi
  local d="$SCRIPT_DIR" i=0
  while [ "$d" != "/" ] && [ $i -lt 8 ]; do
    if [ -r "$d/config/agent.conf" ]; then
      grep -E '^ROUTE_HOME_ADDR=' "$d/config/agent.conf" 2>/dev/null | head -1 \
        | cut -d= -f2- | sed 's/^[[:space:]]*//; s/[[:space:]]*$//'
      return
    fi
    d="$(dirname "$d")"; i=$((i + 1))
  done
}
CO_ENDS=0
if [ "$COORDS_ONLY" -eq 1 ] && [ "$LOOKUP" -eq 0 ]; then
  [ -n "$LIFEKIT" ] && [ -x "$LIFEKIT" ] || fail "kit_life.sh not found/executable"
  [ -n "$ORIGIN" ] || fail "coords-only: origin required"
  _absent="" _needs=""
  for _slot in ORIGIN DEST; do
    if [ "$_slot" = ORIGIN ]; then _tok="$ORIGIN"; else _tok="$DEST"; fi
    _line="$("$LIFEKIT" place-route-end "$_tok" 2>/dev/null \
             | grep "^PLACE_END"$'\t' | head -1)"
    if [ -z "$_line" ]; then
      _absent="${_absent:+$_absent, }$_tok"; continue
    fi
    # a tab IFS collapses empty fields (no address = two tabs in a row), so
    # split on a non-whitespace separator
    IFS=$'\x1f' read -r _t _id _ulid _name _role _addr _coord _asked \
      <<<"${_line//$'\t'/$'\x1f'}"
    _route="$_addr"
    _byname=0
    if [ -z "$_route" ]; then
      case "$_role" in
        base) _route="$(home_addr)" ;;
        stay) _route="" ;;
        *)    _route="$_name"; _byname=1 ;;
      esac
    fi
    printf -v "CO_${_slot}_BYNAME" '%s' "$_byname"
    [ -n "$_route" ] \
      || _needs="$_needs$(printf 'NEEDS_ADDRESS\t%s\t%s' "$_id" "$_name")"$'\n'
    printf -v "CO_${_slot}_ULID" '%s' "$_ulid"
    printf -v "CO_${_slot}_NAME" '%s' "$_name"
    printf -v "CO_${_slot}_ROUTE" '%s' "$_route"
  done
  [ -z "$_absent" ] || fail "coords-only: not on file: $_absent"
  if [ -n "$_needs" ]; then printf '%s' "$_needs"; exit 8; fi
  ORIGIN="$CO_ORIGIN_NAME"; DEST="$CO_DEST_NAME"
  CO_ENDS=1
fi

# Empty origin = home. The appt-add TRAVEL_PLACE_NEEDED signal emits an EMPTY
# origin field for the ordinary home-departure case, and schedule-log's SKILL
# contract says so out loud ("origin empty string -> puller defaults to home") --
# but this script used to reject it outright, so the single most common path
# FAILED before the browser ever opened and the owner got asked "편도 몇 분?"
# manually. route.sh already defaults an empty origin to the home address; what
# kit_life needs is the BASE place's name, since that is the origin the deriver
# resolves to when presence says home. Read it rather than hardcoding "집".
if [ -z "$ORIGIN" ]; then
  if [ -n "$DB" ] && [ -r "$DB" ]; then
    ORIGIN="$(sqlite3 "$DB" "SELECT name FROM place WHERE is_base=1 LIMIT 1;" \
              2>/dev/null)"
  fi
  [ -n "$ORIGIN" ] || fail "origin empty and no base place registered"
  ROUTE_ORIGIN=""            # let route.sh use its own home address
else
  ROUTE_ORIGIN="$ORIGIN"
fi
[ -n "$LIFEKIT" ] && [ -x "$LIFEKIT" ] || fail "kit_life.sh not found/executable"
[ -n "$ROUTE" ]   && [ -x "$ROUTE" ]   || fail "route.sh not found/executable"

# ---- 035: hand the MAP an address, hand LIFEKIT the name --------------------
# Two different strings for two different consumers; conflating them IS the bug
# this wiring exists to fix. route.sh needs something map autocomplete can
# resolve, and a human nickname never can -- the drive failed, this script
# printed FAILED, and the user was asked for the one-way minutes again, and for
# that place forever, because nothing ever learned what the nickname meant.
# kit_life meanwhile needs the row the user actually named, so places and edges
# keep being registered under the NAME below.
#
# ORIGIN is already the resolved place NAME in both branches above (the base
# place's name when the caller sent an empty origin), so one lookup covers both
# cases. When the base row carries an address it now supersedes ROUTE_HOME_ADDR
# -- that conf key is the pre-035 workaround for this exact missing column, and
# route.sh still falls back to it when the base row has no address, so nothing
# regresses for an instance that never fills the column in.
ORIGIN_ADDR=""
DEST_ADDR=""
if [ "$CO_ENDS" -eq 1 ]; then
  ROUTE_ORIGIN="$CO_ORIGIN_ROUTE"   # the kit's own answer (see above)
elif _a="$(place_address "$ORIGIN")"; then
  ORIGIN_ADDR="$_a"
  ROUTE_ORIGIN="$_a"
fi
ROUTE_DEST="$DEST"
if [ "$CO_ENDS" -eq 1 ]; then
  ROUTE_DEST="$CO_DEST_ROUTE"
elif _a="$(place_address "$DEST")"; then
  DEST_ADDR="$_a"
  ROUTE_DEST="$_a"
fi

if [ "$LOOKUP_QUERY" -eq 1 ]; then
  ROUTE_DEST="${LOOKUP_HINT:+$LOOKUP_HINT }$DEST"
fi

case "$MODE" in transit|walk|car|fastest) ;; *) fail "bad mode: $MODE" ;; esac

# route.sh's MAP-URL line -> the two directions ends (used by 4b and by
# --coords-only). Two lines whenever the page is a directions page; an end
# the map did not commit ("-") is an EMPTY line (2026-10-15: the store and
# the missing-mark are per end). No directions URL -> nothing.
map_ends() {   # echoes "<origin seg>\n<dest seg>" decoded, or nothing
  local url
  url="$(sed -n 's/^MAP-URL: //p' "$TMP_OUT" | head -1)"
  [ -n "$url" ] || return 0
  printf '%s' "$url" | python3 -c '
import sys, urllib.parse
u = sys.stdin.read().strip().split("?", 1)[0]
if "/directions/" not in u:
    sys.exit(0)
parts = u.split("/directions/", 1)[1].split("/")
ends = [urllib.parse.unquote(p) for p in parts[:2]]
ends = [e if e != "-" and len(e.split(",")) >= 2 else "" for e in ends]
if len(ends) == 2 and any(ends):
    print(ends[0]); print(ends[1])
' 2>/dev/null
}

# ---- 1. drive route.sh and parse the recommended minutes --------------------
# For fastest we ask route.sh for transit+walk in one drive, then take the min.
route_modes=""
case "$MODE" in
  transit) route_modes="transit" ;;
  walk)    route_modes="walk" ;;
  car)     route_modes="car" ;;
  fastest) route_modes="transit walk" ;;
esac

TMP_OUT="$(mktemp -t travel-resolve.XXXXXX)" || fail "mktemp failed"
TMP_ERR="$(mktemp -t travel-resolve-err.XXXXXX)" || fail "mktemp failed"
trap 'rm -f "$TMP_OUT" "$TMP_ERR"' EXIT

# Hard timeout wrapper: prefer coreutils timeout if present, else a watchdog.
run_route() {
  if command -v timeout >/dev/null 2>&1; then
    timeout "$ROUTE_TIMEOUT" bash "$ROUTE" "$ROUTE_ORIGIN" "$ROUTE_DEST" "$route_modes"
  elif command -v gtimeout >/dev/null 2>&1; then
    gtimeout "$ROUTE_TIMEOUT" bash "$ROUTE" "$ROUTE_ORIGIN" "$ROUTE_DEST" "$route_modes"
  else
    # Foreground timeout: no orphan watchdog or route process.
    python3 - "$ROUTE_TIMEOUT" "$ROUTE" "$ROUTE_ORIGIN" "$ROUTE_DEST" "$route_modes" <<'PYROUTE'
import subprocess, sys
try:
    sys.exit(subprocess.run(['bash'] + sys.argv[2:], timeout=float(sys.argv[1])).returncode)
except subprocess.TimeoutExpired:
    sys.exit(124)
PYROUTE
  fi
}

# --coords-only: an end the map could not find is marked on the place
# (kit_life.sh place-coord-set <place> --missing -- the alert then says the
# place is not located instead of silently dropping the route); the daily
# pass retries it. Only when the drive itself ran: a timeout / a browser
# fault names no end and marks nothing.
mark_missing() {   # mark_missing ORIGIN|DEST
  [ "$COORDS_ONLY" -eq 1 ] && [ "$LOOKUP" -eq 0 ] || return 0
  local u
  if [ "$1" = ORIGIN ]; then u="${CO_ORIGIN_ULID:-$ORIGIN}"
  else u="${CO_DEST_ULID:-$DEST}"; fi
  "$LIFEKIT" place-coord-set "$u" --missing >/dev/null 2>&1 || true
}

# Lookup uses exact destination queries and exposes candidates before a click.
lookup_candidates() {
  python3 - "$TMP_OUT" <<'PY'
import json, sys
rows = []
try:
    with open(sys.argv[1]) as output:
        line = next(l for l in output if l.startswith("MAP-CANDIDATES: "))
    rows = json.loads(line.split(": ", 1)[1])
except (StopIteration, ValueError, OSError):
    pass
if not isinstance(rows, list):
    rows = []
seen = set()
for row in rows:
    if not isinstance(row, dict):
        continue
    def clean(key):
        return " ".join(str(row.get(key) or "").split())
    name, address = clean("name"), clean("address")
    if not name or (name, address) in seen:
        continue
    seen.add((name, address))
    print("COORD_CANDIDATE\t%d\t%s\t%s" % (len(seen), name, address))
    if len(seen) == 3:
        break
PY
}

if [ "$LOOKUP" -eq 1 ]; then
  _lookup_query="$ROUTE_DEST"
  for ROUTE_DEST in "$_lookup_query" "$DEST"; do
    route_status=0
    ROUTE_LOOKUP=1 run_route >"$TMP_OUT" 2>"$TMP_ERR" || route_status=$?
    if [ "$route_status" -eq 4 ]; then
      printf 'AGENT_BROWSER_MISSING\tagent-browser not installed; install agent-browser (npm install -g agent-browser && agent-browser install), then retry travel-resolve.\n'
      exit 9
    fi
    # A browser failure/timeout must not turn a partial URL into a match.
    if [ "$route_status" -eq 0 ]; then
      _candidates="$(lookup_candidates)"
      _ends="$(map_ends)"
      _d="$(printf '%s\n' "$_ends" | sed -n 2p)"
      _count="$(printf '%s\n' "$_candidates" | grep -c '^COORD_CANDIDATE' || true)"
      if [ "$_count" -gt 1 ]; then
        printf 'COORD_CANDIDATES\t%s\n%s\n' "$DEST" "$_candidates"
        exit 0
      fi
      if [ -n "$_d" ]; then
        printf 'COORD_FOUND\t%s\t%s\n' "$DEST" "$_d"
        [ -z "$_candidates" ] || printf '%s\n' "$_candidates"
        exit 0
      fi
      if [ -n "$_candidates" ]; then
        printf 'COORD_CANDIDATES\t%s\n%s\n' "$DEST" "$_candidates"
        exit 0
      fi
    fi
    [ "$_lookup_query" != "$DEST" ] || break
  done
  fail "coords-lookup: no coordinates or candidates for $DEST (tried $_lookup_query; $DEST); agent must WebSearch venue + area before any owner question"
fi

if run_route >"$TMP_OUT" 2>"$TMP_ERR"; then
  :
else
  route_status=$?
  if [ "$route_status" -eq 4 ]; then
    printf 'AGENT_BROWSER_MISSING\tagent-browser not installed; install agent-browser (npm install -g agent-browser && agent-browser install), then retry travel-resolve.\n'
    exit 9
  fi
  if grep -q "^ERR: origin not found" "$TMP_ERR" 2>/dev/null; then
    mark_missing ORIGIN
    fail "map found no match for $ORIGIN ('${ROUTE_ORIGIN:-home}')"
  fi
  if grep -q "^ERR: destination not found" "$TMP_ERR" 2>/dev/null; then
    mark_missing DEST
    fail "map found no match for $DEST ('$ROUTE_DEST')"
  fi
  fail "route.sh error or timeout (origin=${ROUTE_ORIGIN:-home} dest=$ROUTE_DEST mode=$MODE)"
fi
[ -s "$TMP_OUT" ] || fail "route.sh produced no output"

# ---- --coords-only: store both ends, nothing else ----------------------------
if [ "$COORDS_ONLY" -eq 1 ]; then
  _ends="$(map_ends)"
  [ -n "$_ends" ] || fail "no directions URL (coords-only $ORIGIN -> $DEST, routed '${ROUTE_ORIGIN:-home}' -> '$ROUTE_DEST')"
  # store by ulid (the kit resolved it above); a refused store says why --
  # "not on file" is never assumed for a write that failed for another reason
  _n=0 _why=""
  for _slot in ORIGIN DEST; do
    if [ "$_slot" = ORIGIN ]; then _u="$CO_ORIGIN_ULID" _nm="$ORIGIN" _k=1
      _byname="${CO_ORIGIN_BYNAME:-0}" _tag="MATCHED-ORIGIN: "
    else _u="$CO_DEST_ULID" _nm="$DEST" _k=2
      _byname="${CO_DEST_BYNAME:-0}" _tag="MATCHED-DEST: "; fi
    _seg="$(printf '%s\n' "$_ends" | sed -n ${_k}p)"
    if [ -n "$_seg" ] && [ "$_byname" = 1 ] && grep -q "^$_tag" "$TMP_OUT"; then
      # (2026-10-15 sandbox) an end routed by its bare NAME that the map
      # only matched after shortening it (gym "Hakdong Fitness M" -> "Hakdong": the
      # subway station) is another place; never stored as this one
      mark_missing "$_slot"
      _why="${_why:+$_why, }$_nm: map matched a shortened name only"
      continue
    fi
    if [ -z "$_seg" ]; then
      # the drive ran and the map left this end empty
      mark_missing "$_slot"
      _why="${_why:+$_why, }$_nm: not committed by the map"
      continue
    fi
    if _out="$("$LIFEKIT" place-coord-set "${_u:-$_nm}" "$_seg" 2>&1)"; then
      _n=$((_n + 1))
    else
      _r="$(printf '%s\n' "$_out" | grep '^place-coord' | head -1 | cut -f2)"
      _why="${_why:+$_why, }$_nm: ${_r:-error $(printf '%s\n' "$_out" | tail -1)}"
    fi
  done
  [ "$_n" -gt 0 ] || fail "coords-only: no end stored ($_why)"
  printf 'COORDS\t%s\t%s\t%s\n' "$ORIGIN" "$DEST" "$_n"
  exit 0
fi

# Extract minutes per requested sub-mode; for fastest pick the smallest.
minutes=""
chosen_mode=""
chosen_line=""
for m in $route_modes; do
  v="$(parse_minutes "$TMP_OUT" "$m" || true)"
  if [ -n "$v" ]; then
    if [ -z "$minutes" ] || [ "$v" -lt "$minutes" ]; then
      minutes="$v"; chosen_mode="$m"
      chosen_line="$(parse_line "$TMP_OUT" "$m" || true)"
    fi
  fi
done
[ -n "$minutes" ] || fail "could not parse a duration (mode=$MODE)"

# The leg tokens of a route line: "<vehicle> <n>분" pairs before the realtime
# tail (naver appends 도착정보/실시간/상세보기 blocks that are not part of the
# leg). Only genuine leg tokens: a transport word immediately before "<n>분".
# Deliberately NOT a bare "<n>분" scan -- that also swallowed the line's own
# total ("1시간5분") and the fare ("650원 ... 분"), which is what the first
# cut of the route summary shipped. One token per output line.
route_legs() {
  printf '%s' "$1" \
    | sed -E 's/(도착정보|실시간|상세보기|하차).*$//' \
    | grep -oE '(도보|[가-힣0-9]*지하철|[가-힣]*버스)[[:space:]]*[0-9]+[[:space:]]*분'
}

# Field 5 of every result line; documented at step 5 below.
route_summary() {
  local line legs
  [ -n "$chosen_mode" ] || return 0
  line="$chosen_line"
  [ -n "$line" ] || return 0
  legs="$(route_legs "$line" | paste -sd '>' - | sed 's/>/ > /g')"
  # No vehicle legs (a walk line, a car line): still name the mode -- the
  # mode is the part the user needs to see and correct.
  if [ -z "$legs" ]; then
    printf '%s %s분' "$chosen_mode" "$minutes"; return 0
  fi
  printf '%s %s분: %s' "$chosen_mode" "$minutes" "$legs"
}

# ---- 1b. plausibility: a typical duration, not a now-specific ETA -----------
leg_sum=0
while IFS= read -r _leg; do
  _n="$(printf '%s' "$_leg" | grep -oE '[0-9]+' | tail -1)"
  [ -n "$_n" ] && leg_sum=$(( leg_sum + _n ))
done < <(route_legs "$chosen_line")
implausible=""
if [ "$minutes" -gt "$MAX_MIN" ]; then
  implausible=1
elif [ "$leg_sum" -gt 0 ]; then
  allow=$(( leg_sum * 2 ))
  [ $(( leg_sum + WAIT_SLACK )) -gt "$allow" ] && allow=$(( leg_sum + WAIT_SLACK ))
  [ "$minutes" -gt "$allow" ] && implausible=1
fi
if [ -n "$implausible" ]; then
  printf 'IMPLAUSIBLE\t%s\t%s\t%s\t%s\n' "$ORIGIN" "$DEST" "$minutes" \
    "$(route_summary)"
  exit 7
fi

# ---- 2. negligible threshold vs grid-ceil -----------------------------------
if [ "$minutes" -lt "$NEGLIGIBLE_THRESHOLD" ]; then
  edge_min=0
  result_tag="NEGLIGIBLE"
else
  edge_min="$(ceil_grid "$minutes")"
  result_tag="RESOLVED"
fi

# A caller holding its own SQLite connection stores the measured edge itself.
# No place/edge/coordinate writes in this mode, including on route failure.
if [ "$DURATION_LOOKUP" -eq 1 ]; then
  printf '%s\t%s\t%s\t%s\t%s\n' "$result_tag" "$ORIGIN" "$DEST" "$edge_min" "$(route_summary)"
  exit 0
fi

# ---- 3. ensure both places exist --------------------------------------------
# route.sh may have degraded a decorated query down to the bare POI it could
# actually route (it reports that as "MATCHED-<slot>: <raw> -> <clean>"). Register
# the place under the CLEAN name and keep the raw string as an alias, so both the
# human-facing name and the appointment's stored location resolve to one place.
matched_of() {   # $1 = ORIGIN|DEST -> echoes the clean name, or nothing
  sed -n "s/^MATCHED-$1: .* -> //p" "$TMP_OUT" | head -1
}
ORIGIN_CANON="$(matched_of ORIGIN)"; [ -n "$ORIGIN_CANON" ] || ORIGIN_CANON="$ORIGIN"
DEST_CANON="$(matched_of DEST)";     [ -n "$DEST_CANON" ]   || DEST_CANON="$DEST"

# 035: when we routed an ADDRESS, route.sh's MATCHED-* line is reporting
# "<address> -> <the bare POI autocomplete accepted>" -- its degradation ladder
# strips at the first comma, so a street address almost always degrades. That
# bare POI is NOT a new name for this place: the place is ALREADY registered
# under the name we looked the address up by, which is where the address came
# from. Adopting the POI would hand ensure_place an unknown name, insert a
# SECOND place row for one physical location, and split the user's edges
# across the two rows -- leaving the nickname row permanently edge-less and
# still triggering the exact recurring one-way-minutes ask this change removes.
[ -n "$ORIGIN_ADDR" ] && ORIGIN_CANON="$ORIGIN"
[ -n "$DEST_ADDR" ]   && DEST_CANON="$DEST"

if [ "$ORIGIN_CANON" = "$ORIGIN" ]; then ensure_place "$ORIGIN_CANON"
else ensure_place "$ORIGIN_CANON" "$ORIGIN"; fi
if [ "$DEST_CANON" = "$DEST" ]; then ensure_place "$DEST_CANON"
else ensure_place "$DEST_CANON" "$DEST"; fi

ORIGIN="$ORIGIN_CANON"
DEST="$DEST_CANON"

# ---- 4. register BOTH directions --------------------------------------------
# ASSUMPTION (symmetric): we register the SAME resolved value for outbound
# (origin->dest) and return (dest->origin). Re-driving route.sh the other way
# would nearly double the browser cost for a usually-negligible asymmetry; the
# deriver needs both directions present, and symmetric is the safe default.
# Override by calling this script twice with swapped args if asymmetry matters.
register_edge "$ORIGIN" "$DEST" "$edge_min"
register_edge "$DEST" "$ORIGIN" "$edge_min"

# ---- 4b. where both ends are on the map (kit_life 066) ----------------------
# route.sh's MAP-URL line is the Naver directions page it landed on; its two
# path segments are the origin and the destination exactly as Naver resolved
# them ("x,y,name,id,type", percent-encoded). Store each against the place we
# registered, so a prep/travel alert can offer map buttons that open with both
# ends filled. Strictly best-effort: no line, a malformed segment, a pre-066
# kit -> nothing stored, and the resolve result below is unchanged.
# (map_ends is defined above step 1.)
_ends="$(map_ends)"
if [ -n "$_ends" ]; then
  # Non-fatal, but never silent: a refused end prints one line (the encoded
  # form was once rejected here with nothing shown, leaving place_coord empty).
  _i=0
  for _p in "$ORIGIN" "$DEST"; do
    _i=$((_i + 1))
    [ -n "$(printf '%s\n' "$_ends" | sed -n "${_i}p")" ] || continue
    # an end routed by its bare name that the map matched only after
    # shortening it is another place (see --coords-only): not stored
    if [ "$_i" = 1 ]; then
      [ -z "${ORIGIN_ADDR:-}" ] && [ -n "${ROUTE_ORIGIN:-}" ] \
        && grep -q '^MATCHED-ORIGIN: ' "${TMP_OUT:-/dev/null}" && continue
    else
      [ -z "${DEST_ADDR:-}" ] \
        && grep -q '^MATCHED-DEST: ' "${TMP_OUT:-/dev/null}" && continue
    fi
    _cres="$("$LIFEKIT" place-coord-set "$_p" \
      "$(printf '%s\n' "$_ends" | sed -n "${_i}p")" 2>&1)" || \
      echo "place-coord REJECTED for '$_p': $(printf '%s' "$_cres" | tr '\n\t' '  ')" >&2
  done
fi

# ---- 5. machine-readable result ---------------------------------------------
# Field 5 = the leg itself, added 2026-09-23 on owner ruling.
#
# WHY: a bare number hides which mode it asserts. The owner's words: tell me it
# is transit, tell me which subway and which bus, and then I can say "no, I'm
# driving this one." A caller that only prints "15분" gives him nothing to
# disagree with -- and the 15 was wrong for two weeks precisely because nobody
# could see it claimed a WALK.
#
# Shape: "<mode> <총분>분: <leg> > <leg> > ..." e.g.
#   "transit 55분: 도보 2분 > 간선버스 11분 > 3호선지하철 8분 > 4호선지하철 8분 > 도보 14분"
# "<mode> <total>(min unit)" alone when the line has no vehicle legs (walk,
# car); empty
# only when no line was chosen. The field stays present so the tab arity never
# changes for existing parsers.
# (route_summary is defined above, beside route_legs: the IMPLAUSIBLE
# result prints it too.)
ROUTE_SUMMARY="$(route_summary)"

printf '%s\t%s\t%s\t%s\t%s\n' "$result_tag" "$ORIGIN" "$DEST" "$edge_min" "$ROUTE_SUMMARY"
exit 0
