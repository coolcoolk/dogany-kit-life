#!/usr/bin/env bash
# travel-resolve.sh -- deterministic auto travel-time -> kit_life edge registration.
#
# Wires the keyless Naver-map route puller (place-view/route.sh) into automatic
# travel_edge registration, so appointment logging can resolve "편도 몇 분?" without
# asking the owner. No LLM in this path: route.sh drives map.naver.com via
# agent-browser and prints route lines; this script parses the RECOMMENDED
# duration and registers the edge (both directions).
#
# usage: travel-resolve.sh <origin> <dest> [mode]
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
# output (stdout, machine-readable, exactly ONE result line):
#   RESOLVED   <TAB> <from> <TAB> <to> <TAB> <minutes> <TAB> <route>
#   NEGLIGIBLE <TAB> <from> <TAB> <to> <TAB> 0          <TAB> <route>
#     <minutes> grid-ceiled, >0 for RESOLVED.
#     <route> = "<mode> <raw>분: <leg> > <leg> ..." (added 2026-09-23). May be
#     empty, but the field is always present so tab arity is stable. The caller
#     is expected to SHOW this to the owner, not just the number -- it is what
#     lets him say "no, I'm driving this one".
#   FAILED     <TAB> <reason>                            (fall back to manual ask)
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
#   MODE_UNROUTABLE, 6 on NEAR_MATCH, 7 on IMPLAUSIBLE. Fail-open + never hang: any route.sh error / parse miss ->
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

# PROJECT_ROOT = the agent workspace root. routines/ lives directly under it.
# DELIBERATELY NOT "${PROJECT_ROOT:-...}": PROJECT_ROOT is exported by every
# agent runtime, so honouring an inherited value made this script resolve
# kit_life.sh -- and therefore the DB it WRITES places and edges into -- against
# whichever agent happened to invoke it. Cross-agent invocation (a crew agent
# running Ag's routine) then silently registered the places in the caller's DB
# and reported "edge-add failed" against a DB the owner never asked about.
# A routine belongs to the workspace it lives in; explicit LIFEKIT_SH / ROUTE_SH
# / TRAVEL_DB remain the supported overrides.
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

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
if [ -z "$MODE" ]; then
  [ -n "$DB" ] && [ -r "$DB" ] || fail "travel_mode unreadable (no kit_life db)"
  MODE="$(sqlite3 "$DB" "SELECT TRIM(value) FROM config WHERE key='travel_mode' LIMIT 1;" \
          2>/dev/null | tr '[:upper:]' '[:lower:]')"
  case "$MODE" in
    transit|walk|car) ;;
    '') printf 'MODE_UNKNOWN\t%s\t%s\n' "$ORIGIN" "$DEST"; exit 4 ;;
    *)  printf 'MODE_UNROUTABLE\t%s\t%s\t%s\n' "$ORIGIN" "$DEST" "$MODE"; exit 5 ;;
  esac
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
if _a="$(place_address "$ORIGIN")"; then
  ORIGIN_ADDR="$_a"
  ROUTE_ORIGIN="$_a"
fi
ROUTE_DEST="$DEST"
if _a="$(place_address "$DEST")"; then
  DEST_ADDR="$_a"
  ROUTE_DEST="$_a"
fi

case "$MODE" in transit|walk|car|fastest) ;; *) fail "bad mode: $MODE" ;; esac

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
trap 'rm -f "$TMP_OUT"' EXIT

# Hard timeout wrapper: prefer coreutils timeout if present, else a watchdog.
run_route() {
  if command -v timeout >/dev/null 2>&1; then
    timeout "$ROUTE_TIMEOUT" bash "$ROUTE" "$ROUTE_ORIGIN" "$ROUTE_DEST" "$route_modes"
  elif command -v gtimeout >/dev/null 2>&1; then
    gtimeout "$ROUTE_TIMEOUT" bash "$ROUTE" "$ROUTE_ORIGIN" "$ROUTE_DEST" "$route_modes"
  else
    # portable watchdog: background the route, kill it if it overruns.
    bash "$ROUTE" "$ROUTE_ORIGIN" "$ROUTE_DEST" "$route_modes" & local rpid=$!
    ( sleep "$ROUTE_TIMEOUT"; kill -TERM "$rpid" 2>/dev/null ) & local wpid=$!
    wait "$rpid"; local rc=$?
    kill -TERM "$wpid" 2>/dev/null; wait "$wpid" 2>/dev/null
    return $rc
  fi
}

if ! run_route >"$TMP_OUT" 2>/dev/null; then
  fail "route.sh error or timeout (origin=${ROUTE_ORIGIN:-home} dest=$ROUTE_DEST mode=$MODE)"
fi
[ -s "$TMP_OUT" ] || fail "route.sh produced no output"

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
