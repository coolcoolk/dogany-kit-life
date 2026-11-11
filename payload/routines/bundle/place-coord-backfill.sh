#!/usr/bin/env bash
# place-coord-backfill.sh -- one pass that fills the map coordinates 066 left
# empty (kit 2.42.0).
#
# 066 stores where a place is only when a Naver route resolve runs for it
# (travel-resolve.sh -> place-coord-set). Places whose minutes were known
# before 066 never got coordinates, so their alerts can open only a search
# (Kakao) and no Naver directions. This walks every known pair that still
# lacks an end (kit_life.sh place-coord-missing: regions whose local maps need
# coordinates, or no region yet) and re-drives the map for it with
#   travel-resolve.sh --coords-only <origin> <dest>
# Minutes are NOT touched (no edge is written), no place is created.
#
# usage: place-coord-backfill.sh [--dry-run|--lookup] [--max-pairs N]
#   --dry-run   list the pairs, drive nothing.
#   --max-pairs N  drive at most N pairs this run (each drive is ~45s); the
#               rest wait for the next run. The daily pass (morning-brief.sh,
#               after the brief is sent) is the retry: a pair whose end the
#               map could not find stays listed, its place is marked not
#               located (place-coord-set --missing) until a run stores it.
#               A pair whose ends were both stored earlier in the same run
#               is skipped (counted in skipped=), not driven again.
#   --lookup    per PLACE (place-coord-pending), look each up and print
#               COORD_FOUND<TAB><place><TAB><naver end><TAB><id> or
#               COORD_CANDIDATES / COORD_CANDIDATE rows, each suffixed
#               with the place id (the owner still has to pick), or
#               COORD_UNRESOLVED<TAB><id><TAB><name>; stores nothing (owner
#               confirms each, then place-coord-set).
# output: one line per pair (the travel-resolve.sh result line, or
#   PLAN<TAB><origin><TAB><dest> on --dry-run), then once per place whose
#   map end cannot be driven without an address (the base / a stay place
#   with no address, owner 2026-10-08 -- never one failure per pair):
#     NEEDS_ADDRESS<TAB><place id><TAB><name><TAB>new|asked
#   (later pairs with that place are skipped, not driven), then
#   BACKFILL<TAB>pairs=<n><TAB>filled=<n><TAB>failed=<n><TAB>needs_address=<places>
#   <TAB>skipped=<pairs>
#   and, when any is new, NEEDS_ADDRESS_RELAY. A 'new' place is marked asked
#   here (kit_life.sh place-address-ask) -- the owner is asked ONCE; 'asked'
#   ones are listed, not asked again.
# exit: 0 (a failed pair is reported, not fatal -- run again later).
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Installed at <root>/routines/bundle/ -> the root is TWO levels up
# (2026-10-08 live: one level gave <root>/routines and "kit_life.sh not
# found"). An explicit LIFEKIT_SH wins; a bounded walk-up covers any other
# layout.
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
LIFEKIT="${LIFEKIT_SH:-}"
if [ -z "$LIFEKIT" ]; then
  LIFEKIT="$PROJECT_ROOT/database/kit_life.sh"
  _d="$SCRIPT_DIR" _i=0
  while [ ! -x "$LIFEKIT" ] && [ "$_d" != "/" ] && [ $_i -lt 8 ]; do
    [ -x "$_d/database/kit_life.sh" ] && LIFEKIT="$_d/database/kit_life.sh"
    _d="$(dirname "$_d")"; _i=$((_i + 1))
  done
fi
RESOLVE="${TRAVEL_RESOLVE_SH:-$SCRIPT_DIR/travel-resolve.sh}"

DRY=0
LOOKUP=0
MAX_PAIRS=0                # 0 = no bound
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1 ;;
    --lookup) LOOKUP=1 ;;
    --max-pairs)
      shift
      case "${1:-}" in
        ''|*[!0-9]*) printf 'usage: place-coord-backfill.sh [--dry-run|--lookup] [--max-pairs N]\n' >&2; exit 2 ;;
      esac
      MAX_PAIRS="$1" ;;
    *) printf 'usage: place-coord-backfill.sh [--dry-run|--lookup] [--max-pairs N]\n' >&2; exit 2 ;;
  esac
  shift
done

[ -x "$LIFEKIT" ] || { printf 'FAILED\tkit_life.sh not found (%s)\n' "$LIFEKIT"; exit 1; }
# the resolver drives the SAME kit this script listed the pairs from
export LIFEKIT_SH="$LIFEKIT"

# --lookup: the registration rule's backfill (owner 2026-10-07 17:40). Every
# live place with no coordinates (kit_life.sh place-coord-pending) is looked
# up with travel-resolve.sh --coords-lookup; NOTHING is stored -- the agent
# shows the matches to the owner (one confirm per place, the Naver-link
# registration rule) and stores each confirmed one with place-coord-set.
if [ "$LOOKUP" -eq 1 ]; then
  n=0 found=0
  while IFS=$'\t' read -r tag pid name _addr _state; do
    [ "$tag" = "COORD_PENDING" ] || continue
    n=$((n + 1))
    out="$(bash "$RESOLVE" --coords-lookup "$pid" </dev/null 2>/dev/null)"
    case "$out" in
      COORD_FOUND*) found=$((found + 1))
                    while IFS= read -r line; do printf '%s\t%s\n' "$line" "$pid"; done <<< "$out" ;;
      COORD_CANDIDATES*)
                    while IFS= read -r line; do printf '%s\t%s\n' "$line" "$pid"; done <<< "$out" ;;
      *) printf 'COORD_UNRESOLVED\t%s\t%s\n' "$pid" "$name" ;;
    esac
  done < <("$LIFEKIT" place-coord-pending 2>/dev/null)
  printf 'LOOKUP\tplaces=%s\tfound=%s\n' "$n" "$found"
  exit 0
fi

# Directional retry work is independent of missing coordinates/home edges.
if [ "$DRY" -eq 0 ]; then
  "$LIFEKIT" travel-sweep </dev/null
  "$LIFEKIT" travel-retry-run --max "$([ "$MAX_PAIRS" -gt 0 ] && printf %s "$MAX_PAIRS" || printf 2)" </dev/null
  "$LIFEKIT" travel-retry-notices </dev/null
fi

pairs=0 filled=0 failed=0 skipped=0 needs=0 fresh=0 driven=0
NEED_NAMES=$'\n'           # "\n<name>\n" per place already reported
has_coord() {   # has_coord <place>: 0 when the kit already stores its location
  "$LIFEKIT" place-route-end "$1" 2>/dev/null \
    | awk -F'\t' '$1 == "PLACE_END" { ok = ($7 == "1") } END { exit !ok }'
}
# Stage 1 (2026-10-16, a venue with no edge so no pair ever listed it): places an
# appointment in the next 14 days is held at that have no coordinates,
# soonest first, whether or not they have a travel edge (place-coord-upcoming).
# Each is first looked up on the map by name (cheap: address + coordinates +
# naver ref, no browser). One with no measured home edge is then driven with
# travel-resolve.sh (edge both ways + both ends' coordinates), and the
# appointments there are re-attached so the provisional 60 min becomes the
# measured leg. Still no coordinates afterwards -> marked 'not located'
# (retried by the next daily pass). A place WITH an edge is left to stage 2.
ulinked=0 uresolved=0 ufailed=0
while IFS=$'\t' read -r tag pid name _start appts edge; do
  [ "$tag" = "COORD_UNLINKED" ] || continue
  ulinked=$((ulinked + 1))
  if [ "$DRY" -eq 1 ]; then
    printf 'PLAN_UNLINKED\t%s\t%s\t%s\n' "$pid" "$name" "$edge"
    continue
  fi
  "$LIFEKIT" place-coord-autolookup "$pid" </dev/null >/dev/null 2>&1 || true
  [ "$edge" = "1" ] && continue
  if [ "$MAX_PAIRS" -gt 0 ] && [ "$driven" -ge "$MAX_PAIRS" ]; then
    continue                                # next run
  fi
  driven=$((driven + 1))
  out="$(bash "$RESOLVE" "" "$name" </dev/null 2>/dev/null)"
  case "$out" in
    RESOLVED*|NEGLIGIBLE*)
      uresolved=$((uresolved + 1))
      IFS=',' read -r -a _ids <<<"$appts"
      for _a in "${_ids[@]}"; do
        "$LIFEKIT" appt-travel-attach "$_a" </dev/null >/dev/null 2>&1 || true
      done
      printf 'UNLINKED\t%s\t%s\t%s\n' "$pid" "$name" "$(printf '%s' "$out" | cut -f1,4 | tr '\t' ' ')" ;;
    *) ufailed=$((ufailed + 1))
       printf 'UNLINKED\t%s\t%s\tFAILED %s\n' "$pid" "$name" "$(printf '%s' "$out" | head -1 | tr '\t' ' ')" ;;
  esac
  has_coord "$pid" || "$LIFEKIT" place-coord-set "$pid" --missing >/dev/null 2>&1 || true
done < <("$LIFEKIT" place-coord-upcoming 2>/dev/null)
[ "$ulinked" -gt 0 ] && printf 'UNLINKED_SUMMARY\tplaces=%s\tresolved=%s\tfailed=%s\n' \
  "$ulinked" "$uresolved" "$ufailed"

while IFS=$'\t' read -r tag origin dest; do
  [ "$tag" = "COORD_MISSING" ] || continue
  pairs=$((pairs + 1))
  if [ "$DRY" -eq 1 ]; then
    printf 'PLAN\t%s\t%s\n' "$origin" "$dest"
    continue
  fi
  case "$NEED_NAMES" in
    *$'\n'"$origin"$'\n'*|*$'\n'"$dest"$'\n'*) skipped=$((skipped + 1)); continue ;;
  esac
  if has_coord "$origin" && has_coord "$dest"; then
    skipped=$((skipped + 1)); continue      # filled by an earlier pair
  fi
  if [ "$MAX_PAIRS" -gt 0 ] && [ "$driven" -ge "$MAX_PAIRS" ]; then
    skipped=$((skipped + 1)); continue      # next run
  fi
  driven=$((driven + 1))
  out="$(bash "$RESOLVE" --coords-only "$origin" "$dest" </dev/null 2>/dev/null)"
  case "$out" in
    NEEDS_ADDRESS*)
      while IFS=$'\t' read -r _t pid name; do
        [ "$_t" = "NEEDS_ADDRESS" ] || continue
        case "$NEED_NAMES" in *$'\n'"$name"$'\n'*) continue ;; esac
        NEED_NAMES="$NEED_NAMES$name"$'\n'
        needs=$((needs + 1))
        state=asked
        if "$LIFEKIT" place-address-ask "$pid" 2>/dev/null \
            | grep -q "^place-address-ask"$'\t'"applied"; then
          state=new; fresh=$((fresh + 1))
        fi
        printf 'NEEDS_ADDRESS\t%s\t%s\t%s\n' "$pid" "$name" "$state"
      done <<<"$out"
      skipped=$((skipped + 1)) ;;
    COORDS*) printf '%s\n' "$out"; filled=$((filled + 1)) ;;
    *) printf '%s\n' "${out:-FAILED	no output}"; failed=$((failed + 1)) ;;
  esac
done < <("$LIFEKIT" place-coord-missing 2>/dev/null)

printf 'BACKFILL\tpairs=%s\tfilled=%s\tfailed=%s\tneeds_address=%s\tskipped=%s\n' \
  "$pairs" "$filled" "$failed" "$needs" "$skipped"
if [ "$fresh" -gt 0 ]; then
  printf '%s\n' "NEEDS_ADDRESS_RELAY Each 'new' NEEDS_ADDRESS place has no address, and its name is a nickname no map can find (home, the parents' home), so no coordinates can be looked up for it. Ask the owner ONCE, in one message for all new places, for each one's street address; store each answer with kit_life.sh place-address <place id> add '<address>' road, then run this backfill again. No answer -> nothing; 'asked' places are never asked again. Never print these lines."
fi
exit 0
