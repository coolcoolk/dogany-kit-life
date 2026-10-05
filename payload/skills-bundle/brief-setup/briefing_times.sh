#!/bin/bash
# briefing_times.sh -- brief-setup: briefing-time question + change (kit_life 2.37.0).
#
# The kit OWNS the question, the change-time flow and the owner copy (dec-219,
# framework seam 2026-10-01). The framework OWNS the clock mover:
# <root>/routines/set-briefing-times.sh (DGN-1806). This script CALLS it and
# never re-implements it; its only job is to turn the setter's exit code and
# stderr into an honest result for THIS kit's two slots.
#
# Slots the kit ships: morning (morning-brief), retro (daily-retro) and, from
# 2.38.0, weekly (weekly-review). The weekly slot is reported only once its
# row is on (BUNDLE_WEEKLY_REVIEW=on) or a --weekly time was asked: until
# then a weekly-only "NO EMITTER" from the setter is the expected result and
# stays silent, exactly as before. --slots tells the morning/retro times only
# (the weekly time is told by whoever switched the row on).
#
# Usage:
#   briefing_times.sh --root <root> --slots
#       one line per kit slot, then the TOLD time (owner rehearsal 2026-10-01
#       B4: the time is told, not asked; one keep button):
#       SLOT <tab> <slot> <tab> <routine-id> <tab> on|off <tab> <default HH:MM> <tab> <current HH:MM or ->
#       ASK <tab> <line of the told-time text>  (only when a slot is on;
#                                                i18n life_setup.tell.*; one
#                                                row per text line, a blank
#                                                line is an empty row -- speak
#                                                them joined as ONE message)
#       BUTTON <tab> <keep button label>        (life_setup.keep_button)
#       The tag stays ASK so brief-setup/SKILL.md step 1 reads it unchanged.
#   briefing_times.sh --root <root> [--morning HH:MM] [--retro HH:MM]
#                     [--weekly "<Day> HH:MM"]   (Day = Sun..Sat, any case)
#       no time flag = keep (the keep button): the setter adopts the live
#       times and the B4 confirmation line names them.
#       SET  <tab> <slot> <tab> <HH:MM>   the slot fires at that time
#       DEAD <tab> <slot> <tab> <HH:MM>   a slot the owner has on (or asked
#                                         about) has NO loaded unit: nothing
#                                         fires at that time
#       SAVED <tab> <slot> <tab> <HH:MM> Linux only: the time is saved in
#                                         config, the unit did NOT move
#       SAY  <tab> <finished owner line>  copy from i18n life_setup.*
#       ERROR <tab> <setter rc> <tab> <last setter stderr line>
#   briefing_times.sh --root <root> --closing [--mention calendar|health]
#       SAY <tab> the closing line (life_setup.closing), then with --mention
#       one SAY line naming the OTHER group the owner has not tried yet
#       (life_setup.closing.offer_<group>: after 캘린더 관리 mention health,
#       after 식사·운동·인바디 mention calendar -- never brief/retro talk).
#       Always exit 0; no state is read or written. No goal-interview offer:
#       the kit never asks for a goal top-down (owner 2026-10-01).
#
# Times in owner copy are SPOKEN, not HH:MM (B4: "아침 6시, 밤 10시"): ko
# "6시" / "6시 반" / "6시 15분" (the copy carries 아침/밤), en "6:00 AM".
# DEAD / SAVED / ERROR rows and the dead/saved lines keep HH:MM.
#
# Defaults: the ONE kit source is the schedule column of
# service/kit_life/bundle.conf. No other kit file restates a briefing time.
#
# Linux: the setter moves launchd units only (its is_loaded probes launchctl),
# so an on slot comes back "NO EMITTER" while its systemd timer keeps firing
# at the old time. The setter's config write (BRIEF_TIME_*) still lands, so
# such a slot is reported SAVED with the honest "saved, not moved yet" line
# (life_setup.saved_not_moved) -- never SET, never DEAD. This script runs NO
# second writer: no routine-ctl, no unit is enabled or moved here (framework
# 2026-10-01; `routine-ctl enable` does not set times anyway). The setter's
# own Linux fix is framework DGN-1810; once it moves the timer it stops
# naming the slot and the slot is SET like on macOS, with no change here.
# With no time flag nothing is run on Linux: the setter would write its own
# default into BRIEF_TIME_* while the timer keeps its time.
#
# Exit: 0 ok (incl. weekly-only NO EMITTER and Linux saved-not-moved) | 4 an
# on/asked slot has no emitter (never report it as moved) | 1 the setter
# failed | 2 bad input (re-ask) | 3 the framework setter is not on this
# instance.
#
# Seams (tests): BRIEF_SETUP_OS overrides `uname -s`.
set -uo pipefail

ROOT=""; MODE=run; IN_MORNING=""; IN_RETRO=""; IN_WEEKLY=""; MENTION=""
while [ $# -gt 0 ]; do
  case "$1" in
    --root)    ROOT="${2:-}"; shift 2 ;;
    --slots)   MODE=slots; shift ;;
    --closing) MODE=closing; shift ;;
    --morning) IN_MORNING="${2:-}"; shift 2 ;;
    --retro)   IN_RETRO="${2:-}"; shift 2 ;;
    --weekly)  IN_WEEKLY="${2:-}"; shift 2 ;;
    --mention) MENTION="${2:-}"; shift 2 ;;
    *) echo "briefing_times: unknown arg: $1" >&2; exit 2 ;;
  esac
done
[ -n "$ROOT" ] || ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
[ -d "$ROOT" ] || { echo "briefing_times: no such root: $ROOT" >&2; exit 2; }

AGENT_CONF="$ROOT/config/agent.conf"
SETTER="$ROOT/routines/set-briefing-times.sh"
OS="${BRIEF_SETUP_OS:-$(uname -s)}"

valid_hhmm() { [[ "$1" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]]; }
case "$MENTION" in ""|calendar|health) ;; *)
  echo "briefing_times: bad --mention '$MENTION' (calendar|health)" >&2; exit 2 ;;
esac
for t in "$IN_MORNING" "$IN_RETRO"; do
  [ -z "$t" ] || valid_hhmm "$t" || {
    echo "briefing_times: bad time '$t' (want HH:MM)" >&2; exit 2; }
done
# --weekly "<Day> HH:MM": the setter's own form (Day = Sun..Sat).
if [ -n "$IN_WEEKLY" ]; then
  _wd="${IN_WEEKLY%% *}"; _wt="${IN_WEEKLY#* }"
  case "$(printf '%s' "$_wd" | tr 'A-Z' 'a-z')" in
    sun|mon|tue|wed|thu|fri|sat) ;;
    *) echo "briefing_times: bad weekly day '$IN_WEEKLY' (want \"Sun HH:MM\")" >&2; exit 2 ;;
  esac
  valid_hhmm "$_wt" || {
    echo "briefing_times: bad weekly time '$IN_WEEKLY' (want \"Sun HH:MM\")" >&2; exit 2; }
  _wd="$(printf '%s' "$_wd" | cut -c1 | tr 'a-z' 'A-Z')$(printf '%s' "$_wd" | cut -c2-3 | tr 'A-Z' 'a-z')"
  IN_WEEKLY="$_wd $_wt"
fi

# Kit conf: resolve through the framework resolver when present; otherwise the
# two known names. Read only -- this script never writes the kit conf.
kit_conf() {
  local c=""
  if [ -f "$ROOT/routines/lib/life_engine.py" ]; then
    c="$(python3 "$ROOT/routines/lib/life_engine.py" "$ROOT" 2>/dev/null \
         | sed -n 's/^conf=//p' | head -1)"
  fi
  [ -n "$c" ] && [ -f "$c" ] && { printf '%s\n' "$c"; return; }
  for c in "$ROOT/config/kit_life.conf" "$ROOT/config/lifekit.conf"; do
    [ -f "$c" ] && { printf '%s\n' "$c"; return; }
  done
}
KCONF="$(kit_conf)"
kget() { [ -n "$KCONF" ] && grep -E "^$1=" "$KCONF" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '[:space:]'; }
aget() { grep -E "^$1=" "$AGENT_CONF" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '[:space:]"'"'"; }

# spoken <HH:MM> -> the time as the owner hears it (AGENT_LANG). ko: the copy
# already says 아침/밤, so only the 12h clock: 06:00 -> 6시, 22:30 -> 10시 반,
# 07:15 -> 7시 15분. en: 6:00 AM. Anything unparsable passes through.
spoken() {
  local t="$1" h m h12 lang
  [[ "$t" =~ ^([0-9]{1,2}):([0-9]{2})$ ]] || { printf '%s' "$t"; return; }
  h=$((10#${BASH_REMATCH[1]})); m=$((10#${BASH_REMATCH[2]}))
  h12=$((h % 12)); [ "$h12" = 0 ] && h12=12
  lang="$(aget AGENT_LANG)"
  if [ "${lang:-en}" = "ko" ]; then
    if [ "$m" = 0 ]; then printf '%s시' "$h12"
    elif [ "$m" = 30 ]; then printf '%s시 반' "$h12"
    else printf '%s시 %s분' "$h12" "$m"; fi
  else
    if [ "$h" -lt 12 ]; then printf '%s:%02d AM' "$h12" "$m"
    else printf '%s:%02d PM' "$h12" "$m"; fi
  fi
}

slot_id()  { # the routine owning <slot>: the bundle.conf slot column (one source)
  awk -F'\t' -v s="$1" '!/^[#@]/ && NF >= 6 { v = $6; sub(/\r$/, "", v); if (v == s) { print $1; exit } }' \
    "$ROOT/service/kit_life/bundle.conf" 2>/dev/null
}
slot_key() { case "$1" in morning) echo BUNDLE_MORNING_BRIEF ;; retro) echo BUNDLE_DAILY_RETRO ;; weekly) echo BUNDLE_WEEKLY_REVIEW ;; esac; }
slot_in()  { case "$1" in morning) echo "$IN_MORNING" ;; retro) echo "$IN_RETRO" ;; weekly) echo "$IN_WEEKLY" ;; esac; }
slot_on()  { [ "$(kget "$(slot_key "$1")")" = "on" ]; }
default_of() { # the ONE default source: bundle.conf schedule column
  local b="$ROOT/service/kit_life/bundle.conf"
  [ -f "$b" ] || return 0
  awk -F'\t' -v id="$(slot_id "$1")" '!/^[#@]/ && $1 == id { print $3; exit }' "$b"
}

# say <key> [name=value ...] -> the finished line in the owner's language.
# Resolution is the kit's usual one: the framework's layered lookup
# (routines/lib/i18n_lookup.py merged_bundle), else the kit's installed copy
# service/kit_life/i18n over config/i18n; AGENT_LANG first, then en.
say() {
  AGENT_DIR="$ROOT" AGENT_LANG="$(aget AGENT_LANG)" python3 -c '
import importlib.util, json, os, sys
root = os.environ["AGENT_DIR"]
lang = os.environ.get("AGENT_LANG") or "en"
LOOKUP = os.path.join(root, "routines", "lib", "i18n_lookup.py")


def bundle(lg):
    if os.path.isfile(LOOKUP):
        try:
            spec = importlib.util.spec_from_file_location("i18n_lookup", LOOKUP)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            got = mod.merged_bundle(root, lg)
            if isinstance(got, dict):
                return got
        except Exception:
            pass
    merged = {}
    for path in (os.path.join(root, "config", "i18n", "%s.json" % lg),
                 os.path.join(root, "service", "kit_life", "i18n", "%s.json" % lg)):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            merged.update(data)
    return merged


key, kw = sys.argv[1], dict(a.split("=", 1) for a in sys.argv[2:])
for lg in (lang, "en"):
    val = bundle(lg).get(key)
    if isinstance(val, str) and val:
        try:
            print(val.format(**kw))
        except (KeyError, IndexError, ValueError):
            print(val)
        break
' "$@" 2>/dev/null || true
}
emit_say() { local l; l="$(say "$@")"; [ -n "$l" ] && printf 'SAY\t%s\n' "$l"; return 0; }

# ---- --closing: the first-use close (B7 / C4) ------------------------------
if [ "$MODE" = "closing" ]; then
  emit_say life_setup.closing
  case "$MENTION" in
    calendar) emit_say life_setup.closing.offer_health ;;
    health)   emit_say life_setup.closing.offer_calendar ;;
  esac
  exit 0
fi

# ---- --slots: what the question covers -------------------------------------
if [ "$MODE" = "slots" ]; then
  ON=""
  for s in morning retro; do
    st=off; slot_on "$s" && { st=on; ON="$ON $s"; }
    key="BRIEF_TIME_$(printf '%s' "$s" | tr 'a-z' 'A-Z')"
    cur="$(aget "$key")"
    printf 'SLOT\t%s\t%s\t%s\t%s\t%s\n' "$s" "$(slot_id "$s")" "$st" \
      "$(default_of "$s")" "${cur:--}"
  done
  if slot_on weekly; then
    cur="$(aget BRIEF_TIME_WEEKLY)"
    printf 'SLOT\t%s\t%s\t%s\t%s\t%s\n' weekly weekly-review on \
      "$(default_of weekly)" "${cur:--}"
  fi
  # The told line names the time each slot fires at now, else the default.
  m="$(aget BRIEF_TIME_MORNING)"; m="${m:-$(default_of morning)}"
  r="$(aget BRIEF_TIME_RETRO)";   r="${r:-$(default_of retro)}"
  case "$ON" in
    " morning retro") l="$(say life_setup.tell.both morning="$(spoken "$m")" retro="$(spoken "$r")")" ;;
    " morning")       l="$(say life_setup.tell.morning morning="$(spoken "$m")")" ;;
    " retro")         l="$(say life_setup.tell.retro retro="$(spoken "$r")")" ;;
    *)                l="" ;;
  esac
  if [ -n "$l" ]; then
    # one ASK row per line of the told text (a blank line is an empty row):
    # speak the rows joined by line breaks, as ONE message.
    printf '%s\n' "$l" | while IFS= read -r row; do printf 'ASK\t%s\n' "$row"; done
    b="$(say life_setup.keep_button)"
    [ -n "$b" ] && printf 'BUTTON\t%s\n' "$b"
  fi
  exit 0
fi

# ---- run: call the framework setter ----------------------------------------
[ -f "$SETTER" ] || {
  echo "briefing_times: framework setter not on this instance: $SETTER" >&2
  printf 'ERROR\t3\t%s\n' "set-briefing-times.sh missing"
  emit_say life_setup.error
  exit 3; }

LINUX=0; [ "$OS" = "Linux" ] && LINUX=1
if [ "$LINUX" = "1" ] && [ -z "$IN_MORNING$IN_RETRO$IN_WEEKLY" ]; then
  # Keep on Linux: nothing to move, and the setter would write its own
  # default into the wording clock while the timer keeps firing at its time.
  # The on units were switched on at the bundle.conf time (or last moved by
  # the setter), so the confirmation names exactly that; no unit is touched.
  m="$(aget BRIEF_TIME_MORNING)"; m="${m:-$(default_of morning)}"
  r="$(aget BRIEF_TIME_RETRO)";   r="${r:-$(default_of retro)}"
  if slot_on morning && slot_on retro; then
    emit_say life_setup.done.both morning="$(spoken "$m")" retro="$(spoken "$r")"
  elif slot_on morning; then
    emit_say life_setup.done.morning morning="$(spoken "$m")"
  elif slot_on retro; then
    emit_say life_setup.done.retro retro="$(spoken "$r")"
  fi
  exit 0
fi

ARGS=(--root "$ROOT")
[ -n "$IN_MORNING" ] && ARGS+=(--morning "$IN_MORNING")
[ -n "$IN_RETRO" ]   && ARGS+=(--retro "$IN_RETRO")
[ -n "$IN_WEEKLY" ]  && ARGS+=(--weekly "$IN_WEEKLY")
ERRF="$(mktemp -t brief-setup-err.XXXXXX)"
OUT="$(bash "$SETTER" "${ARGS[@]}" 2>"$ERRF")"; RC=$?
ERR="$(cat "$ERRF")"; rm -f "$ERRF"

if [ "$RC" != 0 ] && [ "$RC" != 4 ]; then
  last="$(printf '%s\n' "$ERR" | grep -v '^[[:space:]]*$' | tail -1)"
  printf 'ERROR\t%s\t%s\n' "$RC" "$last"
  if [ "$RC" = 2 ] || printf '%s' "$last" | grep -q 'bad --'; then
    emit_say life_setup.reask; exit 2
  fi
  emit_say life_setup.error; exit 1
fi

# The time each slot now carries, from the setter's own summary line
# ("briefing times set: morning=HH:MM retro=HH:MM weekly=Day HH:MM").
set_time() {
  if [ "$1" = weekly ]; then
    printf '%s\n' "$OUT" | sed -n "s/^briefing times set: .*weekly=\([A-Za-z][a-z][a-z] [0-9][0-9]:[0-9][0-9]\).*/\1/p" | head -1
    return
  fi
  printf '%s\n' "$OUT" | sed -n "s/^briefing times set: .*$1=\([0-9][0-9]:[0-9][0-9]\).*/\1/p" | head -1
}
DEAD_ALL=""
[ "$RC" = 4 ] && DEAD_ALL="$(printf '%s\n' "$ERR" \
  | sed -n 's/.*NO EMITTER on this instance for: \(.*\) -- .*/\1/p' | head -1)"

SET_SLOTS=""; DEAD_SLOTS=""; SAVED_SLOTS=""
for s in morning retro weekly; do
  asked="$(slot_in "$s")"
  slot_on "$s" || [ -n "$asked" ] || continue   # off and not asked: not ours to report
  t="$(set_time "$s")"; t="${t:-$asked}"
  case " $DEAD_ALL " in
    *" $s "*)
      if [ "$LINUX" = "1" ] && slot_on "$s"; then
        # on, but the setter could not see/move its systemd timer: the time
        # is saved, the unit still fires at the old time. Asked slots only
        # (no flag on Linux never gets here).
        [ -n "$asked" ] || continue
        printf 'SAVED\t%s\t%s\n' "$s" "$asked"; SAVED_SLOTS="$SAVED_SLOTS $s"
      else
        printf 'DEAD\t%s\t%s\n' "$s" "$t"; DEAD_SLOTS="$DEAD_SLOTS $s"
      fi ;;
    *) printf 'SET\t%s\t%s\n' "$s" "$t"; SET_SLOTS="$SET_SLOTS $s" ;;
  esac
done

# Owner copy. A slot is confirmed only if it moved; a dead slot is never
# reported as moved. Keep (no flag) confirms the live times it adopted -- the
# B4 line "아침 6시 브리핑, 밤 10시 일간회고로 맞춰 뒀어요."
m="$IN_MORNING"; [ -n "$m" ] || m="$(set_time morning)"
r="$IN_RETRO";   [ -n "$r" ] || r="$(set_time retro)"
KEEP=0; [ -z "$IN_MORNING$IN_RETRO$IN_WEEKLY" ] && KEEP=1
MR_SLOTS=""; for s in $SET_SLOTS; do [ "$s" = weekly ] || MR_SLOTS="$MR_SLOTS $s"; done
case "$MR_SLOTS" in
  " morning retro") emit_say life_setup.done.both morning="$(spoken "$m")" retro="$(spoken "$r")" ;;
  " morning")       { [ -n "$IN_MORNING" ] || [ "$KEEP" = 1 ]; } \
                      && emit_say life_setup.done.morning morning="$(spoken "$m")" ;;
  " retro")         { [ -n "$IN_RETRO" ] || [ "$KEEP" = 1 ]; } \
                      && emit_say life_setup.done.retro retro="$(spoken "$r")" ;;
esac
case " $SET_SLOTS " in
  *" weekly "*) w="$IN_WEEKLY"; [ -n "$w" ] || w="$(set_time weekly)"
                { [ -n "$IN_WEEKLY" ] || [ "$KEEP" = 1 ]; } \
                  && emit_say life_setup.done.weekly \
                       day="$(say "brief.standard.weekday.$(printf '%s' "${w%% *}" | tr 'A-Z' 'a-z')")" \
                       time="$(spoken "${w#* }")" ;;
esac
if [ -n "$SAVED_SLOTS" ]; then
  # one line for every saved-not-moved slot (DRAFT copy, owner to confirm)
  st=""; for s in $SAVED_SLOTS; do st="${st:+$st, }$(slot_in "$s")"; done
  emit_say life_setup.saved_not_moved time="$st"
fi
for s in $DEAD_SLOTS; do
  t="$(slot_in "$s")"; [ -n "$t" ] || t="$(set_time "$s")"
  emit_say "life_setup.dead.$s" time="$t"
done

[ -n "$DEAD_SLOTS" ] && exit 4
exit 0
