#!/usr/bin/env bash
# route.sh -- Naver map directions puller (walk + transit default).
# Drives map.naver.com via agent-browser, prints raw route lines.
# Agent reads the lines and summarizes for the user.
# Exit contract: 2 = missing input/home address; 3 = map end not found;
# 4 = agent-browser not installed (PATH and known install dirs checked).
# On exit 4 the agent installs the dependency and retries this command.
# See reference/dependency.md for the place-view/schedule recovery contract.
#
# usage: route.sh "<origin>" "<destination>" [modes]
#   origin  : empty -> defaults to the user's home address (see ROUTE_HOME_ADDR)
#   dest    : required
#   modes   : space/comma list of {transit,walk,car}. default "transit walk"
#   ROUTE_LOOKUP=1: exact destination query; emit MAP-CANDIDATES JSON
#     ({name,address}, up to 3). Multiple matches stop before a click.
#
# examples:
#   route.sh "" "서울역"                   # home -> dest, transit + walk
#   route.sh "강남역" "홍대입구역" "transit"
#   route.sh "" "김포공항" "car"
set -uo pipefail

# --- home address (config, never hardcoded) ----------------------------------
# The empty-origin default needs a MAP-SEARCHABLE address, which the kit_life
# base place cannot supply -- that row stores a display name ("집"), and no map
# resolves that. So the address is instance config, not code: a hardcoded value
# here is one user's private address shipped inside a product pack.
# Order: ROUTE_HOME_ADDR env > config/agent.conf ROUTE_HOME_ADDR > unset (error).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
find_conf() {   # walk up for config/agent.conf; skills live a few levels down
  local d="$SCRIPT_DIR" i=0
  while [ "$d" != "/" ] && [ $i -lt 8 ]; do
    [ -r "$d/config/agent.conf" ] && { printf '%s' "$d/config/agent.conf"; return; }
    d="$(dirname "$d")"; i=$((i + 1))
  done
  printf ''
}
HOME_ADDR="${ROUTE_HOME_ADDR:-}"
if [ -z "$HOME_ADDR" ]; then
  CONF="$(find_conf)"
  # Read shell-style quotes as data, never source a config or evaluate its
  # substitutions. Last assignment wins, as in the instance config loader.
  [ -n "$CONF" ] && HOME_ADDR="$(python3 - "$CONF" <<'PY'
import shlex, sys
value = ''
for line in open(sys.argv[1], encoding='utf-8'):
    line = line.strip().removeprefix('export ').lstrip()
    if line.startswith('ROUTE_HOME_ADDR='):
        value = ' '.join(shlex.split(line.split('=', 1)[1], comments=True))
print(value)
PY
)"
fi

ORIGIN="${1:-}"
DEST="${2:-}"
MODES_RAW="${3:-transit walk}"

if [ -z "$ORIGIN" ]; then
  if [ -z "$HOME_ADDR" ]; then
    echo "ERR: empty origin needs a home address -- set ROUTE_HOME_ADDR in config/agent.conf" >&2
    exit 2
  fi
  ORIGIN="$HOME_ADDR"
fi
if [ -z "$DEST" ]; then echo "ERR: destination required" >&2; exit 2; fi

# normalize modes (commas -> spaces)
MODES="$(printf '%s' "$MODES_RAW" | tr ',' ' ')"

AB="${ROUTE_AGENT_BROWSER:-agent-browser}"
# Resolution is deliberately NOT PATH-only.  DGN-963 (2026-08-21) reordered PATH
# to stop a stale root-owned npm-global `claude` from winning, and dropped
# $HOME/.npm-global/bin as "dead".  It was not dead: agent-browser lives there
# and nothing else does, so the map driver went dark for a full day and every
# new place silently fell back to asking the owner for a duration.  Binding this
# tool to a PATH policy it does not own was the actual defect; so we look in the
# known install locations too, and any future PATH policy change cannot break us.
# Override with ROUTE_AGENT_BROWSER if the binary moves again.
if ! command -v "$AB" >/dev/null 2>&1; then
  for _cand in "$HOME/.npm-global/bin/agent-browser" \
               "$HOME/.local/bin/agent-browser" \
               "/opt/homebrew/bin/agent-browser" \
               "/usr/local/bin/agent-browser"; do
    if [ -x "$_cand" ]; then AB="$_cand"; break; fi
  done
fi
# Preflight. Every $AB call below is silenced (the CLI is chatty and its noise
# would corrupt the machine-readable output), so a MISSING binary used to
# surface as "origin not found in autocomplete" -- a browser problem reported as
# a map problem. That misdiagnosis cost a full debugging round on 2026-08-22.
if ! command -v "$AB" >/dev/null 2>&1 && [ ! -x "$AB" ]; then
  echo "ERR: agent-browser not found (PATH + known install dirs) -- install agent-browser (npm install -g agent-browser && agent-browser install), then retry" >&2
  exit 4
fi
W(){ $AB wait "$1" >/dev/null 2>&1; }
close_browser(){
  # A multi-leg lookup reuses one browser until its final leg. Closing and
  # immediately reopening the same daemon races socket teardown (ENOENT),
  # which used to break the second leg despite a healthy map.
  [ "${ROUTE_KEEP_BROWSER:-0}" = 1 ] || $AB close >/dev/null 2>&1
}

# --- query degradation ladder ------------------------------------------------
# Callers do not always hand us a clean POI name. Google Calendar's location
# field arrives decorated:
#   "<place name>, <street address>, <district>, <city>, <country>"
# Naver autocomplete matches the bare POI ("<poi>") and returns NOTHING for the
# decorated form -- which used to abort the whole route drive. So we try a ladder
# of progressively shorter candidates and take the first one autocomplete accepts:
#   1. the raw string as given
#   2. the segment before the first comma (drops the address/city/country tail)
#   3. that segment with trailing space-separated tokens dropped one at a time
#      ("<poi> <full business name>" -> "<poi>")
# Deliberately NOT fuzzy: every candidate is a literal prefix of what the caller
# said, so we can never silently route to an unrelated place.
candidates(){
  local raw="$1" head cur seen=""
  emit(){ case "$seen" in *"<$1>"*) return ;; esac; seen="$seen<$1>"; printf '%s\n' "$1"; }
  emit "$raw"
  head="${raw%%,*}"
  head="$(printf '%s' "$head" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
  [ -n "$head" ] && emit "$head"
  cur="$head"
  while [ "${cur%\ *}" != "$cur" ]; do
    cur="${cur% *}"
    [ -n "$cur" ] && emit "$cur"
  done
}

# The autocomplete probe. MEASURED BUG (2026-10-06, the life agent, office -> clinic):
# the probe used to be a bare "[role=option]". When the origin query matched
# a "search term" suggestion, Naver ran a place search and left its RESULT
# list (A..G, ul.list_search_result, also role=option) open under the boxes.
# The destination's full street address then got no autocomplete at all, but
# the stale origin list still satisfied the probe: the ladder "accepted" the
# raw address on its first rung, never tried the shorter rungs that do match,
# the destination was never committed, the search ran with one end and the
# transit section came back empty -> travel-resolve: "could not parse a
# duration". Only the autocomplete dropdown of the box being filled counts.
AC_OPTION='[role=option]:not(.list_search_result [role=option])'
# MEASURED BUG (2026-10-15, home -> office): the dropdown now lists a
# "search term" suggestion first (id instant-search-item-ac-N, "search term" icon)
# above the place / address rows (instant-search-item-poi-N ...). Clicking
# it runs a search instead of committing the end: the URL kept
# /directions/<origin>/-/..., no minutes, no MAP-URL ends -- every
# coordinate lookup since 10-07 failed this way ("no directions URL").
# A committing row (place / address) is preferred; the search-term row is
# the last resort only.
AC_COMMIT="$AC_OPTION"':not([id^="instant-search-item-ac-"])'

# Read committing destination options only; stale origin results, hidden
# options and search-term suggestions cannot establish a venue match.
read_lookup_candidates(){
  $AB eval 'JSON.stringify(Array.from(document.querySelectorAll("[role=option]:not(.list_search_result [role=option]):not([id^=\"instant-search-item-ac-\"])"))
    .filter(e => e.getClientRects().length)
    .map(e => {
      const lines = e.innerText.split(/\n/).map(s => s.trim()).filter(Boolean);
      const text = selector => e.querySelector(selector)?.innerText.trim() || "";
      return {name: text(".title_text, .place_name, .name") || lines[0] || "",
              address: text(".address, .addr") || lines.slice(1).join(" ")};
    }).filter(e => e.name).slice(0, 3))' 2>/dev/null | python3 -c '
import json, sys
try:
    rows = json.load(sys.stdin)
    if isinstance(rows, str):
        rows = json.loads(rows)
    if not isinstance(rows, list):
        rows = []
    print(json.dumps(rows[:3], ensure_ascii=False))
except (ValueError, TypeError):
    print("[]")
'
}

# Fill one POI input with the ladder; sets RESOLVED_Q to the accepted candidate.
# $1 = "first" (origin box) | "second" (destination box); $2 = raw query.
fill_poi(){
  local which="$1" raw="$2" c _lookup_count
  while IFS= read -r c; do
    [ -z "$c" ] && continue
    if [ "$which" = "first" ]; then
      $AB find first ".input_search" fill "$c" >/dev/null 2>&1
    else
      $AB find nth 1 ".input_search" fill "$c" >/dev/null 2>&1
    fi
    W 1600
    if [ "$which" = second ] && [ "${ROUTE_LOOKUP:-0}" = 1 ]; then
      LOOKUP_CANDIDATES="$(read_lookup_candidates)"
      [ -n "$LOOKUP_CANDIDATES" ] && [ "$LOOKUP_CANDIDATES" != '[]' ] || continue
      printf 'MAP-CANDIDATES: %s\n' "$LOOKUP_CANDIDATES"
      _lookup_count="$(printf '%s' "$LOOKUP_CANDIDATES" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))')"
      [ "$_lookup_count" -le 1 ] || return 12
      # Keep the committing-row guard; never fall back to a search term.
      if $AB find first "$AC_COMMIT" click >/dev/null 2>&1; then
        RESOLVED_Q="$c"; return 0
      fi
      return 1
    fi
    if $AB find first "$AC_COMMIT" click >/dev/null 2>&1 \
        || $AB find first "$AC_OPTION" click >/dev/null 2>&1; then
      RESOLVED_Q="$c"; return 0
    fi
  done < <(if [ "$which" = second ] && [ "${ROUTE_LOOKUP:-0}" = 1 ]; then
             printf '%s\n' "$raw"
           else candidates "$raw"; fi)
  return 1
}

# --- open directions page (tabs switch mode, POIs persist) ---
# Navigation errors (e.g. a rehearsal dead proxy) are not POI misses.
if ! OPEN_RESULT="$($AB open "https://map.naver.com/p/directions/-/-/-/transit" 2>&1)"; then
  echo "ERR: map navigation failed: $OPEN_RESULT" >&2
  close_browser
  exit 5
fi
# Naver keeps analytics/map tiles active. Network-idle waited minutes even
# after the inputs were usable; wait for the UI we actually need instead.
if ! $AB wait '.input_search' >/dev/null 2>&1; then
  echo "ERR: map directions inputs unavailable" >&2
  close_browser
  exit 5
fi
W 800

# --- fill origin ---
if ! fill_poi first "$ORIGIN"; then
  echo "ERR: origin not found in autocomplete: $ORIGIN" >&2; close_browser; exit 3
fi
ORIGIN_Q="$RESOLVED_Q"
W 1000

# --- fill destination ---
_dest_rc=0
fill_poi second "$DEST" || _dest_rc=$?
if [ "$_dest_rc" -eq 12 ]; then
  close_browser
  exit 0
elif [ "$_dest_rc" -ne 0 ]; then
  echo "ERR: destination not found in autocomplete: $DEST" >&2; close_browser; exit 3
fi
DEST_Q="$RESOLVED_Q"
W 1000

# --- submit ---
$AB click "button.btn_direction.search" >/dev/null 2>&1
W 3000

# ORIGIN/DEST report what was actually routed (the accepted ladder candidate).
# MATCHED-* appears only when degradation kicked in, so a caller that wants to
# register the place under a clean name can read it without parsing the ladder.
echo "ORIGIN: $ORIGIN_Q"
echo "DEST:   $DEST_Q"
[ "$ORIGIN_Q" != "$ORIGIN" ] && echo "MATCHED-ORIGIN: $ORIGIN -> $ORIGIN_Q"
[ "$DEST_Q" != "$DEST" ] && echo "MATCHED-DEST: $DEST -> $DEST_Q"
# MAP-URL: the directions page we landed on. Naver writes both ends into it
# (/p/directions/<x,y,name,id,type>/<x,y,name,id,type>/-/<mode>), which is
# where travel-resolve.sh learns each place's coordinates for the alert map
# buttons (kit_life 066). Only printed when it really is a filled directions
# URL; a caller that does not know the line ignores it.
MAP_URL="$($AB get url 2>/dev/null | tail -1)"
case "$MAP_URL" in
  *"/directions/-/"*|*"/directions/-") ;;
  *"/directions/"*) echo "MAP-URL: $MAP_URL" ;;
esac

read_transit(){
  echo "=== 대중교통 ==="
  $AB snapshot -c 2>&1 | grep -oE '"[^"]*도착[^"]*원[^"]*"' \
    | grep -E "분" | sed 's/^"//; s/"$//' | head -8
}
read_walk(){
  $AB find role tab click --name "도보" >/dev/null 2>&1
  W 2500
  echo "=== 도보 ==="
  $AB snapshot -c 2>&1 | grep -oE '"[^"]*걸음[^"]*"' \
    | grep -E "분" | sed 's/^"//; s/"$//' | head -3
}
read_car(){
  $AB find role tab click --name "자동차" >/dev/null 2>&1
  W 2500
  echo "=== 자동차 ==="
  $AB snapshot -c 2>&1 | grep -oE '"[0-9][^"]*(km|[0-9]m)[^"]*원[^"]*"' \
    | sed 's/^"//; s/"$//' | head -4
}

for m in $MODES; do
  case "$m" in
    transit) read_transit ;;
    walk)    read_walk ;;
    car)     read_car ;;
    *) echo "WARN: unknown mode $m" >&2 ;;
  esac
done

close_browser
