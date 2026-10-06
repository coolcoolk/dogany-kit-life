#!/usr/bin/env bash
# route.sh -- Naver map directions puller (walk + transit default).
# Drives map.naver.com via agent-browser, prints raw route lines.
# Agent reads the lines and summarizes for the user.
#
# usage: route.sh "<origin>" "<destination>" [modes]
#   origin  : empty -> defaults to the user's home address (see ROUTE_HOME_ADDR)
#   dest    : required
#   modes   : space/comma list of {transit,walk,car}. default "transit walk"
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
  [ -n "$CONF" ] && HOME_ADDR="$(grep -E '^ROUTE_HOME_ADDR=' "$CONF" 2>/dev/null \
    | head -1 | cut -d= -f2- | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
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
  echo "ERR: agent-browser not found (PATH + known install dirs) -- cannot drive the map" >&2
  exit 4
fi
W(){ $AB wait "$1" >/dev/null 2>&1; }

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

# Fill one POI input with the ladder; sets RESOLVED_Q to the accepted candidate.
# $1 = "first" (origin box) | "second" (destination box); $2 = raw query.
fill_poi(){
  local which="$1" raw="$2" c
  while IFS= read -r c; do
    [ -z "$c" ] && continue
    if [ "$which" = "first" ]; then
      $AB find first ".input_search" fill "$c" >/dev/null 2>&1
    else
      $AB find nth 1 ".input_search" fill "$c" >/dev/null 2>&1
    fi
    W 1600
    if $AB find first "[role=option]" click >/dev/null 2>&1; then
      RESOLVED_Q="$c"; return 0
    fi
  done < <(candidates "$raw")
  return 1
}

# --- open directions page (transit base; tabs switch mode, POIs persist) ---
$AB open "https://map.naver.com/p/directions/-/-/-/transit" >/dev/null 2>&1
$AB wait --load networkidle >/dev/null 2>&1
W 800

# --- fill origin ---
if ! fill_poi first "$ORIGIN"; then
  echo "ERR: origin not found in autocomplete: $ORIGIN" >&2; $AB close >/dev/null 2>&1; exit 3
fi
ORIGIN_Q="$RESOLVED_Q"
W 1000

# --- fill destination ---
if ! fill_poi second "$DEST"; then
  echo "ERR: destination not found in autocomplete: $DEST" >&2; $AB close >/dev/null 2>&1; exit 3
fi
DEST_Q="$RESOLVED_Q"
W 1000

# --- submit ---
$AB click "button.btn_direction.search" >/dev/null 2>&1
$AB wait --load networkidle >/dev/null 2>&1
W 3000

# ORIGIN/DEST report what was actually routed (the accepted ladder candidate).
# MATCHED-* appears only when degradation kicked in, so a caller that wants to
# register the place under a clean name can read it without parsing the ladder.
echo "ORIGIN: $ORIGIN_Q"
echo "DEST:   $DEST_Q"
[ "$ORIGIN_Q" != "$ORIGIN" ] && echo "MATCHED-ORIGIN: $ORIGIN -> $ORIGIN_Q"
[ "$DEST_Q" != "$DEST" ] && echo "MATCHED-DEST: $DEST -> $DEST_Q"

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
  $AB snapshot -c 2>&1 | grep -oE '"[0-9][^"]*km[^"]*원[^"]*"' \
    | grep -E "분" | sed 's/^"//; s/"$//' | head -4
}

for m in $MODES; do
  case "$m" in
    transit) read_transit ;;
    walk)    read_walk ;;
    car)     read_car ;;
    *) echo "WARN: unknown mode $m" >&2 ;;
  esac
done

$AB close >/dev/null 2>&1
