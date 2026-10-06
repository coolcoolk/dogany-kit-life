#!/bin/bash
# brief-standard.sh -- switch an agent's brief / daily retro / weekly retro on
# (or off) in one call (kit_life 2.38.0, domain brief/retro standard).
#
# WHO CALLS IT. A pack, at the end of its goal + program consult (pack_health:
# the program-consult wrap). It is not a setup question and has no menu: the
# consult wrap STATES the times and the owner changes them only if they want
# to (tell, not ask). This verb does the switching and prints the facts the
# pack speaks from.
#
# TIMES. The kit owns no domain time; it takes what it is handed:
#   --morning HH:MM          REQUIRED. A pack fact (pack_health: 30 min before
#                            the earliest weekly training start).
#   --retro HH:MM            default: the leader's daily retro time (same clock).
#   --weekly "<Day> HH:MM"   default: the leader's weekly retro time. Owner
#                            2026-10-02: no leader-relative offsets -- each bot
#                            has its own chat, so the same clock never collides;
#                            the owner's day-close time is one time.
# Leader = DOGANY_LEADER_ROOT (kit_life.sh scope-self leader_root); its times
# are its BRIEF_TIME_RETRO / BRIEF_TIME_WEEKLY, else the bundle.conf defaults.
# An instance with no leader (a main agent) defaults to bundle.conf as is.
#
# WHAT IT DOES (activate), in order; the first failure stops it:
#   0. preflight -- the framework primitives this needs are present AND new
#      enough (framework >=2.6.0: routines/lib/bundle-routine-ctl.sh with the
#      bundle.conf slot column, the briefing setter's --weekly). Missing ->
#      exit 3, NOTHING changed, one ERROR row naming the gap and the owner
#      line brief.standard.framework_too_old.
#   1. bundle-routine-ctl.sh on <id> "<[dow ]HH:MM>" for morning-brief /
#      daily-retro / weekly-review. The helper schedules the routine AND swaps
#      its same-slot generic brief (no double message at one clock); the slot
#      map is the bundle.conf `slot` column, never restated here. A failure
#      turns off what THIS run switched on.
#   3. set-briefing-times.sh --morning --retro --weekly -- the wording clock
#      (BRIEF_TIME_*) and the live clock agree. rc 4 (a slot with no loaded
#      unit, e.g. Linux timers) is reported, not fatal.
#   4. config/kit_life.conf BUNDLE_MORNING_BRIEF / _DAILY_RETRO /
#      _WEEKLY_REVIEW = on.
# deactivate reverses 1 and 4 (bundle-routine-ctl.sh off <id> restores the
# generic slots; data untouched).
#
# Usage:
#   brief-standard.sh activate --morning HH:MM [--retro HH:MM]
#                     [--weekly "<Day> HH:MM"] [--dry] [--root <dir>]
#   brief-standard.sh deactivate [--dry] [--root <dir>]
# Output rows (TAB-separated):
#   PLAN  <slot> <routine-id> <time>        (--dry: what would be switched)
#   SET   <slot> <time>                     (activate: switched on)
#   OFF   <slot>                            (deactivate)
#   WARN  <step> <detail>                   (non-fatal: setter rc 4 etc.)
#   ERROR <step> <detail>
#   SAY   <owner line>                      (brief.standard.tell, spoken times)
# Exit: 0 ok | 1 a step failed (rolled back what this run enabled) | 2 bad
#       input | 3 framework too old / missing (nothing changed)
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT=""
CMD="${1:-}"; [ $# -gt 0 ] && shift
IN_MORNING=""; IN_RETRO=""; IN_WEEKLY=""; DRY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --morning) IN_MORNING="${2:-}"; shift 2 ;;
    --retro)   IN_RETRO="${2:-}"; shift 2 ;;
    --weekly)  IN_WEEKLY="${2:-}"; shift 2 ;;
    --root)    ROOT="${2:-}"; shift 2 ;;
    --dry)     DRY=1; shift ;;
    *) echo "brief-standard: unknown arg: $1" >&2; exit 2 ;;
  esac
done
[ -n "$ROOT" ] || ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
case "$CMD" in activate|deactivate) ;; *)
  echo "usage: brief-standard.sh activate --morning HH:MM [--retro HH:MM] [--weekly \"Sun HH:MM\"] [--dry] | deactivate [--dry]" >&2
  exit 2 ;;
esac

LIFE_SH="$ROOT/database/kit_life.sh"
BCTL="$ROOT/routines/lib/bundle-routine-ctl.sh"
SETTER="$ROOT/routines/set-briefing-times.sh"
BUNDLE="$ROOT/service/kit_life/bundle.conf"
KCONF="$ROOT/config/kit_life.conf"

valid_hhmm() { [[ "$1" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]]; }
aget() { # aget <agent-root> <KEY> -> value (quotes stripped)
  grep -E "^$2=" "$1/config/agent.conf" 2>/dev/null | head -1 | cut -d= -f2- \
    | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'\$/\1/"
}
default_of() { # bundle.conf schedule column of <id>
  [ -f "$BUNDLE" ] || return 0
  awk -F'\t' -v id="$1" '!/^[#@]/ && $1 == id { print $3; exit }' "$BUNDLE"
}
slot_owner() { # slot -> routine id owning it in the bundle.conf slot column ('' = none)
  [ -f "$BUNDLE" ] || return 0
  awk -F'\t' -v s="$1" '!/^[#@]/ && NF >= 6 { v = $6; sub(/\r$/, "", v); if (v == s) { print $1; exit } }' "$BUNDLE"
}
norm_weekly() { # "<day> HH:MM" (any case) -> "Sun HH:MM", '' when malformed
  local d="${1%% *}" t="${1#* }" l
  l="$(printf '%s' "$d" | tr 'A-Z' 'a-z')"
  case "$l" in sun|mon|tue|wed|thu|fri|sat) ;; *) return 0 ;; esac
  valid_hhmm "$t" || return 0
  printf '%s %s' "$(printf '%s' "${l:0:1}" | tr 'a-z' 'A-Z')${l:1}" "$t"
}
shift_time() { # shift_time "<[Day ]HH:MM>" <minutes> -> same shape, shifted
  python3 - "$1" "$2" <<'PY'
import datetime, sys
v, m = sys.argv[1].strip(), int(sys.argv[2])
days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
if " " in v:
    d, hm = v.split(" ", 1)
    base = datetime.datetime(2024, 1, 1 + days.index(d.capitalize()),  # 2024-01-01 = Mon
                             int(hm[:2]), int(hm[3:5]))
    x = base + datetime.timedelta(minutes=m)
    print("%s %02d:%02d" % (days[x.weekday()], x.hour, x.minute))
else:
    x = datetime.datetime(2024, 1, 1, int(v[:2]), int(v[3:5])) + datetime.timedelta(minutes=m)
    print("%02d:%02d" % (x.hour, x.minute))
PY
}

# say <key> [name=value ...] -- the kit's usual i18n resolution.
say() {
  AGENT_DIR="$ROOT" AGENT_LANG="$(aget "$ROOT" AGENT_LANG)" python3 -c '
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
spoken() { # HH:MM -> the time as the owner hears it (same rule as brief-setup)
  local t="$1" h m h12
  [[ "$t" =~ ^([0-9]{1,2}):([0-9]{2})$ ]] || { printf '%s' "$t"; return; }
  h=$((10#${BASH_REMATCH[1]})); m=$((10#${BASH_REMATCH[2]}))
  h12=$((h % 12)); [ "$h12" = 0 ] && h12=12
  if [ "$(aget "$ROOT" AGENT_LANG)" = "ko" ]; then
    if [ "$m" = 0 ]; then printf '%s시' "$h12"
    elif [ "$m" = 30 ]; then printf '%s시 반' "$h12"
    else printf '%s시 %s분' "$h12" "$m"; fi
  else
    if [ "$h" -lt 12 ]; then printf '%s:%02d AM' "$h12" "$m"
    else printf '%s:%02d PM' "$h12" "$m"; fi
  fi
}
kconf_set() { # kconf_set <KEY> <value> -- upsert in config/kit_life.conf
  local tmp
  mkdir -p "$(dirname "$KCONF")"; touch "$KCONF"
  if grep -qE "^$1=" "$KCONF"; then
    tmp="$(mktemp)"
    awk -v k="$1" -v v="$2" 'BEGIN{FS=OFS="="} $1 == k && !done {print k "=" v; done=1; next} {print}' "$KCONF" > "$tmp" \
      && cat "$tmp" > "$KCONF"; rm -f "$tmp"
  else
    printf '%s=%s\n' "$1" "$2" >> "$KCONF"
  fi
}

# ---- 0. preflight: fail closed, visibly, before anything changes ---------
preflight() {
  local miss="" s
  [ -f "$BCTL" ] || miss="routines/lib/bundle-routine-ctl.sh missing"
  [ -z "$miss" ] && ! grep -q 'slot-sync' "$BCTL" && miss="bundle-routine-ctl.sh has no slot column support"
  [ -z "$miss" ] && [ ! -f "$SETTER" ] && miss="routines/set-briefing-times.sh missing"
  [ -z "$miss" ] && ! grep -q -- '--weekly' "$SETTER" && miss="set-briefing-times.sh has no --weekly"
  for s in morning retro weekly; do
    [ -z "$miss" ] && [ -z "$(slot_owner "$s")" ] && miss="bundle.conf has no $s slot row"
  done
  for s in morning retro weekly; do
    id="$(slot_owner "$s")"
    [ -z "$miss" ] && [ ! -f "$ROOT/routines/bundle/$id.sh" ] && miss="routines/bundle/$id.sh missing"
  done
  if [ -n "$miss" ]; then
    printf 'ERROR\tframework\t%s\n' "$miss"
    l="$(say brief.standard.framework_too_old)"; [ -n "$l" ] && printf 'SAY\t%s\n' "$l"
    echo "brief-standard: framework too old or incomplete: $miss -- nothing changed" >&2
    exit 3
  fi
}
preflight

SLOTS="morning retro weekly"
sid() { slot_owner "$1"; }
skey() { local k; k="$(awk -F'\t' -v id="$(sid "$1")" '!/^#/ && $1 == id { print $4; exit }' "$BUNDLE")"
  printf 'BUNDLE_%s' "$(printf '%s' "${k#bundle.}" | tr 'a-z' 'A-Z')"; }

# ---- deactivate -------------------------------------------------------------
if [ "$CMD" = deactivate ]; then
  for s in $SLOTS; do
    if [ "$DRY" = 1 ]; then printf 'PLAN\t%s\t%s\toff\n' "$s" "$(sid "$s")"; continue; fi
    bash "$BCTL" off "$(sid "$s")" >/dev/null || { printf 'ERROR\tbundle-routine-ctl\toff %s\n' "$(sid "$s")"; exit 1; }
    kconf_set "$(skey "$s")" off
    printf 'OFF\t%s\n' "$s"
  done
  exit 0
fi

# ---- activate: resolve the three times -----------------------------------
[ -n "$IN_MORNING" ] || { echo "brief-standard: --morning HH:MM is required (a pack fact)" >&2; exit 2; }
valid_hhmm "$IN_MORNING" || { echo "brief-standard: bad --morning '$IN_MORNING' (HH:MM)" >&2; exit 2; }
[ -z "$IN_RETRO" ] || valid_hhmm "$IN_RETRO" || { echo "brief-standard: bad --retro '$IN_RETRO' (HH:MM)" >&2; exit 2; }
if [ -n "$IN_WEEKLY" ]; then
  W="$(norm_weekly "$IN_WEEKLY")"
  [ -n "$W" ] || { echo "brief-standard: bad --weekly '$IN_WEEKLY' (\"Sun HH:MM\")" >&2; exit 2; }
  IN_WEEKLY="$W"
fi

LEADER="$(bash "$LIFE_SH" scope-self 2>/dev/null | sed -n 's/^leader_root=//p' | head -1)"
[ "$LEADER" = "-" ] && LEADER=""
RETRO="$IN_RETRO"
if [ -z "$RETRO" ]; then
  base=""; [ -n "$LEADER" ] && base="$(aget "$LEADER" BRIEF_TIME_RETRO)"
  valid_hhmm "$base" || base="$(default_of daily-retro)"
  RETRO="$base"
fi
WEEKLY="$IN_WEEKLY"
if [ -z "$WEEKLY" ]; then
  base=""; [ -n "$LEADER" ] && base="$(norm_weekly "$(aget "$LEADER" BRIEF_TIME_WEEKLY)")"
  [ -n "$base" ] || base="$(norm_weekly "$(default_of weekly-review)")"
  WEEKLY="$base"
fi
valid_hhmm "$RETRO" && [ -n "$(norm_weekly "$WEEKLY")" ] || {
  printf 'ERROR\ttimes\tunresolved (retro=%s weekly=%s)\n' "$RETRO" "$WEEKLY"; exit 1; }
WDAY="${WEEKLY%% *}"; WTIME="${WEEKLY#* }"
WDAY_L="$(printf '%s' "$WDAY" | tr 'A-Z' 'a-z')"

tm() { case "$1" in morning) echo "$IN_MORNING" ;; retro) echo "$RETRO" ;; weekly) echo "$WEEKLY" ;; esac; }
tell() {
  l="$(say brief.standard.tell morning="$(spoken "$IN_MORNING")" retro="$(spoken "$RETRO")" \
        weekday="$(say "brief.standard.weekday.$WDAY_L")" weekly="$(spoken "$WTIME")")"
  [ -n "$l" ] && printf 'SAY\t%s\n' "$l"
  return 0
}

if [ "$DRY" = 1 ]; then
  for s in $SLOTS; do printf 'PLAN\t%s\t%s\t%s\n' "$s" "$(sid "$s")" "$(tm "$s")"; done
  tell
  exit 0
fi

# ---- 1. switch the three routines on (the helper swaps the generic brief) ---
ENABLED=""
rollback() {
  for id in $ENABLED; do bash "$BCTL" off "$id" >/dev/null 2>&1 || true; done
}
for s in $SLOTS; do
  id="$(sid "$s")"
  if [ "$s" = weekly ]; then at="$WDAY_L $WTIME"; else at="$(tm "$s")"; fi
  out="$(bash "$BCTL" on "$id" "$at" 2>&1)"; rc=$?
  if [ "$rc" != 0 ]; then
    printf 'ERROR\tbundle-routine-ctl\t%s rc=%s %s\n' "$id" "$rc" "$(printf '%s' "$out" | tail -1)"
    rollback
    exit 1
  fi
  ENABLED="$ENABLED $id"
done

# ---- 3. wording clock = live clock ---------------------------------------
sout="$(bash "$SETTER" --root "$ROOT" --morning "$IN_MORNING" --retro "$RETRO" --weekly "$WEEKLY" 2>&1)"; src=$?
case "$src" in
  0) ;;
  4) printf 'WARN\tset-briefing-times\t%s\n' "$(printf '%s' "$sout" | grep 'NO EMITTER' | tail -1)" ;;
  *) printf 'ERROR\tset-briefing-times\trc=%s %s\n' "$src" "$(printf '%s' "$sout" | tail -1)"
     rollback
     exit 1 ;;
esac

# ---- 4. record --------------------------------------------------------------
for s in $SLOTS; do
  kconf_set "$(skey "$s")" on
  printf 'SET\t%s\t%s\n' "$s" "$(tm "$s")"
done
tell
exit 0
