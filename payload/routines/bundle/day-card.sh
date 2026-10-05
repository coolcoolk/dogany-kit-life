#!/bin/bash
# day-card.sh -- kit_life bundle routine: publish TODAY's day card into the
# pinned dashboard.
#
# WHAT IT IS. The day surface kept alive on the ONE pinned message instead of
# scrolling away with the 06:00 brief. Since 2026-10-02 (owner 13:01) the pin
# has its own layout (kit_life.sh day-card --pin): today's open tasks, today's
# appointments (time, title, place, people; ' <- now' on the running one),
# and the week calendar -- appointment count per day, today marked, then
# Monday..Sunday one line per appointment. Since 2026-10-04 12:15 every item
# is its own <blockquote> under an emoji heading: the text is input to the
# framework's bridge sanitizer (parse_mode HTML), which every pin and push
# path runs. Gap lines stay on the brief's --plain block.
#
# WHICH PIN MECHANISM. The framework's, unchanged: routines/status-footer.py
# owns dashboard.md and composes it from a SECTION STORE keyed by owner slug
# (DGN-967). This script is the kit-side PRODUCER and nothing else -- it
# builds text and hands it to that API under one slug. There is deliberately
# no second pin path here: a domain agent writing dashboard.md directly gets
# adopted as the "legacy" section with a 24h TTL, which is the framework
# telling us to use the door.
#
# WHERE THE NUMBERS COME FROM. kit_life.sh day-card, in Python, from stored
# block spans. This script formats nothing and computes nothing; no model is
# involved at any point in this lane.
#
# CADENCE (3 legs, by design -- a pin that is only correct at 06:00 is worse
# than no pin, because it looks live):
#   1. morning-brief.sh calls this at brief time with --fresh, so the card
#      is up with the brief and the two never disagree about the same day.
#      --fresh asks the framework for a NEW pinned message (DGN-1768 r2,
#      owner option 2 of 2026-09-28): the bridge sends the board as a fresh
#      silent message, pins it and deletes yesterday's. That one pinned
#      message IS the brief's card; the brief no longer pushes a second copy
#      (2026-09-30 05:00: the pushed copy and the rotated pin arrived back to
#      back, and the frozen copy then disagreed with the live pin once the
#      day's end time moved). On a framework without --fresh this exits 3 and
#      the brief falls back to pushing the --print text itself.
#   2. kit_life.py refreshes it after a schedule WRITE (see _day_card_refresh
#      in kit_life.py) -- the moment the day actually changes.
#   3. the day-card bundle routine re-runs it through waking hours, so the
#      past / now / ahead split keeps moving even on a day nobody edits.
#
# TTL. The section carries an expiry a little over one refresh interval, so a
# producer that stops (laptop asleep, routine unloaded) takes its own card
# down instead of pinning a stale day forever. LIFEKIT_DAY_CARD_TTL overrides.
#
# Usage: day-card.sh [--dry] [--print] [--fresh [--wait SECS]] [--date YYYY-MM-DD]
#   --dry    print the card, touch no pin (--fresh is ignored)
#   --print  ALSO write the card text to stdout, exactly as pinned (empty on a
#            day with no rows). The morning brief keeps this text for its
#            fallback push (framework without --fresh) and for --dry.
#            With --dry: the bare card only, no "(no rows ...)" note.
#   --fresh  pin as a NEW message (status-footer.py --pin-set ... --fresh
#            --wait SECS) instead of editing the current pin, and wait for the
#            bridge to confirm it. --wait defaults to LIFEKIT_DAY_CARD_FRESH_WAIT
#            (90s): the bridge ticks every ~3s, the slack covers a send that
#            failed once and retries (Ag 2026-09-30 05:00:13 ConnectError).
# Exit: 0 ok (INCLUDING "nothing to show" and "no pin surface on this
#       instance") / 1 config error. This routine must never be the reason a
#       brief or a write verb fails.
#       With --fresh the exit code is the answer to "is the card up as a new
#       pinned message?": 0 yes (confirmed by the bridge) / 2 no (timeout,
#       pin-set failed, or no rows -- the section is still written or cleared
#       as usual) / 3 cannot ask (no pin surface, or a framework without
#       --fresh; the pin was refreshed the old way) / 1 config error.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
LIFE_SH="$AGENT_DIR/database/kit_life.sh"
FOOTER_PY="$AGENT_DIR/routines/status-footer.py"
PIN_OWNER="lifekit-day"

DRY=0
PRINT=0
FRESH=0
WAIT=""
DATE=""
USAGE="usage: day-card.sh [--dry] [--print] [--fresh [--wait SECS]] [--date YYYY-MM-DD]"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry) DRY=1; shift ;;
    --print) PRINT=1; shift ;;
    --fresh) FRESH=1; shift ;;
    --wait) WAIT="${2:-}"; shift 2 ;;
    --date) DATE="${2:-}"; shift 2 ;;
    *) echo "$USAGE" >&2; exit 1 ;;
  esac
done
if [[ -n "$WAIT" ]]; then
  [[ "$FRESH" -eq 1 ]] || { echo "[day-card] --wait requires --fresh" >&2; exit 1; }
  [[ "$WAIT" =~ ^[0-9]+([.][0-9]+)?$ ]] || { echo "[day-card] --wait expects seconds" >&2; exit 1; }
fi

[[ -f "$LIFE_SH" ]] || { echo "[day-card] kit_life.sh not found: $LIFE_SH" >&2; exit 1; }

conf() {  # conf <KEY> <default>
  # Trailing quote-strip (KIT, 2026-09-23): agent.conf has TWO reader
  # conventions. The framework side SOURCES the file, so a value containing a
  # space MUST be quoted there or the shell runs its second token as a command
  # ("agent.conf: line 21: 버퍼: command not found", once per cycle). This
  # reader takes the RAW text after `=`, so an unstripped quote would become
  # part of the value and break prefix matching outright. Stripping here is the
  # compatible direction: quoted and unquoted values both read the same, which
  # is what lets the conf value be quoted afterwards. Only a MATCHED pair at
  # both ends is removed, so a value that legitimately contains a quote is
  # untouched. Order matters: whitespace first, then quotes.
  local v
  v="$(grep -E "^$1=" "$AGENT_DIR/config/agent.conf" 2>/dev/null | head -1 | cut -d= -f2- | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'\$/\1/" || true)"
  [[ -n "$v" ]] && printf '%s' "$v" || printf '%s' "$2"
}

# Offset: the framework resolver (routines/lib/tz_offset.sh, DGN-1704) owns the
# order -- per-agent AGENT_TZ_OFFSET_HOURS override, then the crew value, then
# the host. On an older framework without the helper (or if it fails / prints
# a non-integer) keep the pre-2.29 behaviour: the per-agent value, else 0.
TZ_OFFSET_H=""
TZ_LIB="$AGENT_DIR/routines/lib/tz_offset.sh"
if [[ -f "$TZ_LIB" ]]; then
  TZ_OFFSET_H="$(bash "$TZ_LIB" resolve "$AGENT_DIR" 2>/dev/null || true)"
  [[ "$TZ_OFFSET_H" =~ ^-?[0-9]+$ ]] || TZ_OFFSET_H=""
fi
[[ -n "$TZ_OFFSET_H" ]] || TZ_OFFSET_H="$(conf AGENT_TZ_OFFSET_HOURS 0)"
# Same per-instance display knob the morning brief honours, so the pinned card
# and the brief cannot show a different set of rows for the same day.
EXCLUDE_PREFIXES="$(conf BRIEF_EXCLUDE_TITLE_PREFIXES '')"
TTL="$(conf LIFEKIT_DAY_CARD_TTL 7200)"
if [[ "$FRESH" -eq 1 && -z "$WAIT" ]]; then
  WAIT="$(conf LIFEKIT_DAY_CARD_FRESH_WAIT 90)"
  [[ "$WAIT" =~ ^[0-9]+([.][0-9]+)?$ ]] || WAIT=90
fi
[[ -z "$DATE" ]] && DATE="$(date +%F)"

# --pin (2026-10-02): the pin's own layout -- today's tasks, today's
# appointments, the week calendar. The brief's --plain block is unchanged.
ARGS=(day-card --pin --date "$DATE" --tz-offset "$TZ_OFFSET_H")
[[ -n "$EXCLUDE_PREFIXES" ]] && ARGS+=(--exclude-prefix "$EXCLUDE_PREFIXES")
CARD="$("$LIFE_SH" "${ARGS[@]}" 2>/dev/null || true)"

# --print goes out BEFORE any pin-surface early exit: an instance without a
# dashboard still gets the brief's card message.
[[ "$PRINT" -eq 1 && -n "$CARD" ]] && printf '%s\n' "$CARD"

if [[ "$DRY" -eq 1 ]]; then
  [[ "$PRINT" -eq 1 ]] && exit 0
  if [[ -n "$CARD" ]]; then printf '%s\n' "$CARD"; else echo "(no rows today -- the pin would be cleared)"; fi
  exit 0
fi

# No pin surface on this instance (kit installed without the framework's
# dashboard, or a bare checkout): say so once on stderr and leave. Not an
# error -- the kit half is complete and the card simply has nowhere to go.
if [[ ! -f "$FOOTER_PY" ]]; then
  echo "[day-card] no section store at $FOOTER_PY; card not pinned" >&2
  [[ "$FRESH" -eq 1 ]] && exit 3
  exit 0
fi

# Empty card = no rows today. CLEAR the section rather than pin an empty box:
# with every section empty the composer writes "" and the bridge's debounced
# delete takes the pinned message down, which is the correct end state for a
# day with nothing on it.
if [[ -z "$CARD" ]]; then
  python3 "$FOOTER_PY" --pin-clear "$PIN_OWNER" >/dev/null 2>&1 \
    || echo "[day-card] pin-clear failed (previous content preserved)" >&2
  [[ "$FRESH" -eq 1 ]] && exit 2
  exit 0
fi

# --fresh needs the framework's DGN-1768 r2 door. Capability, not version: the
# flag is either parsed by this status-footer.py or it is not. An older one
# rejects the unknown flag BEFORE writing anything, so asking it anyway would
# leave the pin stale; instead refresh the old way and exit 3 so the caller
# sends the card itself.
if [[ "$FRESH" -eq 1 ]] && ! grep -q -- '"--fresh"' "$FOOTER_PY" 2>/dev/null; then
  echo "[day-card] framework has no --pin-set --fresh; pin refreshed in place" >&2
  printf '%s\n' "$CARD" | python3 "$FOOTER_PY" --pin-set "$PIN_OWNER" --ttl "$TTL" >/dev/null 2>&1 \
    || echo "[day-card] pin-set failed (previous content preserved)" >&2
  exit 3
fi

if [[ "$FRESH" -eq 1 ]]; then
  # Exit 0 only when the bridge consumed the rotate request, i.e. the new
  # pinned message is up. On timeout the framework withdraws the request
  # (no late rotation behind the caller's fallback) and the section stays
  # written, so the current pin is edited in place as on any other refresh.
  if printf '%s\n' "$CARD" | python3 "$FOOTER_PY" --pin-set "$PIN_OWNER" --ttl "$TTL" \
       --fresh --wait "$WAIT" >/dev/null 2>&1; then
    exit 0
  fi
  echo "[day-card] fresh pin not confirmed within ${WAIT}s (pin edited in place, if at all)" >&2
  exit 2
fi

printf '%s\n' "$CARD" | python3 "$FOOTER_PY" --pin-set "$PIN_OWNER" --ttl "$TTL" >/dev/null 2>&1 \
  || echo "[day-card] pin-set failed (previous content preserved)" >&2
exit 0
