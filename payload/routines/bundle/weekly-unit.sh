#!/bin/bash
# weekly-unit.sh -- the weekly retro unit's program path: check, and the
# upgrade migration that points a dead unit at routines/bundle/weekly-review.sh.
#
# WHY (the life agent, Sun 2026-10-11). The life agent's weekly was instance-owned
# (routines/weekly-review.sh) until 2.38.0 promoted it into the kit
# (routines/bundle/weekly-review.sh, kit 09cdfa8). The old unit -- the
# instance template routines/<label>.plist and its LaunchAgents copy -- kept
# naming routines/weekly-review.sh, which no longer exists: launchd ran a
# missing file, the Sunday daily retro is suppressed by design (the weekly is
# the single Sunday retro, soft-merge 2026-08-17), so the owner got no retro.
# Moving the unit's clock (set-briefing-times.sh --weekly) kept the dead path
# and was reported as set.
#
# Usage: weekly-unit.sh check [--root <dir>]
#        weekly-unit.sh heal  [--root <dir>] [--dry]
#
# check: one row per weekly unit file found (label
#        com.telegram-skill-bot.<agent>.weekly-review, <agent> = .instance.conf
#        DOGANY_AGENT_NAME, else the root's dirname -- routine-ctl's own rule):
#          UNIT <tab> live|template <tab> <file> <tab> ok|dead|healable <tab> <program>
#        ok       the program the unit runs exists
#        healable it does not, and routines/bundle/weekly-review.sh does
#        dead     it does not, and there is no bundle twin either
#        No unit file -> no row. Exit 0 = every unit ok (or none), 5 = a unit
#        is dead or healable (nothing fires at its time).
# heal:  every healable unit is re-registered through the kit's ONE routine
#        verb (bundle-routine-ctl.sh on weekly-review "<dow> HH:MM", which
#        renders routines/bundle/routine.plist.tpl via routine-ctl.sh -- never
#        a hand-written plist), keeping the unit's own day and time. The live
#        unit is (re)loaded; the template is re-rendered the same way with
#        ROUTINE_CTL_NO_LOAD=1 into a scratch dir and put in place, so the
#        framework installer's routines/*.plist pass cannot bring the dead
#        path back. BUNDLE_WEEKLY_REVIEW is set on (the routine now runs from
#        the kit). Rows: HEALED <tab> live|template <tab> <file> <tab> <dow HH:MM>
#        (--dry: PLAN instead, nothing changed), then the check rows.
#        Exit 0 = nothing dead is left, 5 = a dead unit with no bundle twin
#        is left, 4 = the routine verb failed (its stderr passed through).
#
# Callers: briefing_times.sh (before and after moving the weekly clock: a
# dead program is reported DEAD, never SET) and morning-brief.sh (the upgrade migration: the kit has no install-time code
# hook, so the next morning brief after an upgrade heals the unit).
#
# Linux: a systemd unit's ExecStart is checked the same way
# (dogany-<agent>-weekly-review.service).
# Seams (tests): ROUTINE_CTL_LA_DIR, ROUTINE_CTL_UNIT_DIR (as routine-ctl.sh),
# BUNDLE_ROUTINE_CTL (the routine verb), WEEKLY_UNIT_OS (uname -s).
set -uo pipefail

CMD="${1:-}"; [ $# -gt 0 ] && shift
ROOT=""; DRY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --root) ROOT="${2:-}"; shift 2 ;;
    --dry)  DRY=1; shift ;;
    *) echo "weekly-unit: unknown arg: $1" >&2; exit 2 ;;
  esac
done
case "$CMD" in check|heal) ;; *)
  echo "usage: weekly-unit.sh check|heal [--root <dir>] [--dry]" >&2; exit 2 ;;
esac
[ -n "$ROOT" ] || ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
[ -d "$ROOT" ] || { echo "weekly-unit: no such root: $ROOT" >&2; exit 2; }

ID=weekly-review
TWIN_REL="routines/bundle/$ID.sh"
TWIN="$ROOT/$TWIN_REL"
BCTL="${BUNDLE_ROUTINE_CTL:-$ROOT/routines/lib/bundle-routine-ctl.sh}"
OS="${WEEKLY_UNIT_OS:-$(uname -s)}"
AGENT_NAME="$(grep -E '^DOGANY_AGENT_NAME=' "$ROOT/.instance.conf" 2>/dev/null | head -1 | cut -d= -f2 | tr -d '[:space:]"' || true)"
AGENT_NAME="${AGENT_NAME:-$(basename "$ROOT")}"
LABEL="com.telegram-skill-bot.${AGENT_NAME}.${ID}"
LA_DIR="${ROUTINE_CTL_LA_DIR:-$HOME/Library/LaunchAgents}"
UNIT_DIR="${ROUTINE_CTL_UNIT_DIR:-$HOME/.config/systemd/user}"

# unit files: <where> <tab> <file>
units() {
  if [ "$OS" = Linux ]; then
    for f in "$UNIT_DIR/dogany-${AGENT_NAME}-${ID}.service" "$ROOT/routines/${LABEL}.service"; do
      [ -f "$f" ] || continue
      case "$f" in "$ROOT"/*) printf 'template\t%s\n' "$f" ;; *) printf 'live\t%s\n' "$f" ;; esac
    done
  else
    [ -f "$LA_DIR/${LABEL}.plist" ] && printf 'live\t%s\n' "$LA_DIR/${LABEL}.plist"
    [ -f "$ROOT/routines/${LABEL}.plist" ] && printf 'template\t%s\n' "$ROOT/routines/${LABEL}.plist"
  fi
  return 0
}

# info <file> -> "<program>\t<dow HH:MM>" (program = the last argument naming
# a .sh / .py file; schedule '' when the unit has none it can carry over)
info() {
  python3 - "$1" <<'PY' 2>/dev/null
import plistlib, re, sys
path = sys.argv[1]
prog, sched = "", ""
days = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]
if path.endswith(".plist"):
    try:
        with open(path, "rb") as fh:
            d = plistlib.load(fh)
    except Exception:
        import subprocess
        raw = subprocess.run(["plutil", "-convert", "xml1", "-o", "-", path],
                             capture_output=True).stdout
        d = plistlib.loads(raw)
    args = d.get("ProgramArguments") or ([d["Program"]] if d.get("Program") else [])
    for a in args:
        if re.search(r"\.(sh|py)$", str(a)):
            prog = str(a)
    sci = d.get("StartCalendarInterval")
    if isinstance(sci, list):
        sci = sci[0] if sci else None
    if isinstance(sci, dict) and "Weekday" in sci:
        sched = "%s %02d:%02d" % (days[int(sci["Weekday"]) % 7],
                                  int(sci.get("Hour", 0)), int(sci.get("Minute", 0)))
else:
    for ln in open(path, encoding="utf-8"):
        if ln.startswith("ExecStart="):
            for a in ln.split("=", 1)[1].split():
                if re.search(r"\.(sh|py)$", a):
                    prog = a
print("%s\t%s" % (prog, sched))
PY
}

state_of() { # state_of <program>
  local p="$1"
  [ -n "$p" ] && [ -f "$p" ] && { echo ok; return; }
  [ -f "$TWIN" ] && { echo healable; return; }
  echo dead
}

check_rows() {
  local rc=0 where file inf prog st
  while IFS=$'\t' read -r where file; do
    [ -n "$file" ] || continue
    inf="$(info "$file")"; prog="${inf%%$'\t'*}"
    st="$(state_of "$prog")"
    printf 'UNIT\t%s\t%s\t%s\t%s\n' "$where" "$file" "$st" "${prog:--}"
    [ "$st" = ok ] || rc=5
  done < <(units)
  return "$rc"
}

if [ "$CMD" = check ]; then
  check_rows; exit $?
fi

# ---- heal ---------------------------------------------------------------------
# The schedule to keep: the live unit's day/time, else the template's, else
# BRIEF_TIME_WEEKLY, else the bundle.conf schedule column.
SCHED=""; NEED=""
while IFS=$'\t' read -r where file; do
  [ -n "$file" ] || continue
  inf="$(info "$file")"; prog="${inf%%$'\t'*}"; sch="${inf#*$'\t'}"
  [ "$(state_of "$prog")" = healable ] || continue
  NEED="$NEED $where"
  [ -z "$SCHED" ] && SCHED="$sch"
done < <(units)

if [ -z "$NEED" ]; then
  check_rows; exit $?
fi
if [ -z "$SCHED" ]; then
  w="$(grep -E '^BRIEF_TIME_WEEKLY=' "$ROOT/config/agent.conf" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"'"'")"
  [ -n "$w" ] || w="$(awk -F'\t' -v id="$ID" '!/^[#@]/ && $1 == id { print $3; exit }' "$ROOT/service/kit_life/bundle.conf" 2>/dev/null)"
  SCHED="$(printf '%s' "$w" | awk '{ print tolower(substr($1, 1, 3)) " " $2 }')"
fi
[[ "$SCHED" =~ ^(sun|mon|tue|wed|thu|fri|sat)\ ([01][0-9]|2[0-3]):[0-5][0-9]$ ]] || {
  echo "weekly-unit: no usable weekly schedule ('$SCHED') -- nothing changed" >&2; check_rows; exit 5; }

if [ "$DRY" = 1 ]; then
  for where in $NEED; do printf 'PLAN\t%s\t%s\t%s\n' "$where" "$TWIN_REL" "$SCHED"; done
  exit 0
fi
[ -f "$BCTL" ] || { echo "weekly-unit: routine verb missing: $BCTL -- nothing changed" >&2; exit 4; }

for where in $NEED; do
  case "$where" in
    live)
      bash "$BCTL" on "$ID" "$SCHED" --root "$ROOT" >/dev/null || {
        echo "weekly-unit: bundle-routine-ctl.sh on $ID failed -- live unit unchanged" >&2; exit 4; }
      f="$(units | awk -F'\t' '$1 == "live" { print $2; exit }')"
      printf 'HEALED\tlive\t%s\t%s\n' "${f:--}" "$SCHED" ;;
    template)
      scratch="$(mktemp -d "${TMPDIR:-/tmp}/weekly-unit.XXXXXX")"
      if [ "$OS" = Linux ]; then
        ROUTINE_CTL_UNIT_DIR="$scratch" ROUTINE_CTL_NO_LOAD=1 \
          bash "$BCTL" on "$ID" "$SCHED" --root "$ROOT" >/dev/null 2>&1
        src="$scratch/dogany-${AGENT_NAME}-${ID}.service"; dst="$ROOT/routines/${LABEL}.service"
      else
        ROUTINE_CTL_LA_DIR="$scratch" ROUTINE_CTL_NO_LOAD=1 \
          bash "$BCTL" on "$ID" "$SCHED" --root "$ROOT" >/dev/null 2>&1
        src="$scratch/${LABEL}.plist"; dst="$ROOT/routines/${LABEL}.plist"
      fi
      if [ -f "$src" ] && grep -q "$TWIN" "$src"; then
        cp "$src" "$dst"; rm -rf "$scratch"
        printf 'HEALED\ttemplate\t%s\t%s\n' "$dst" "$SCHED"
      else
        rm -rf "$scratch"
        echo "weekly-unit: could not render the template through the routine verb -- template unchanged" >&2
        exit 4
      fi ;;
  esac
done

# the routine now runs from the kit: record its bundle row as on
KCONF="$ROOT/config/kit_life.conf"; [ -f "$KCONF" ] || KCONF="$ROOT/config/lifekit.conf"
if [ -f "$KCONF" ]; then
  if grep -qE '^BUNDLE_WEEKLY_REVIEW=' "$KCONF"; then
    tmp="$(mktemp)"
    awk 'BEGIN{FS=OFS="="} $1 == "BUNDLE_WEEKLY_REVIEW" && !d {print "BUNDLE_WEEKLY_REVIEW=on"; d=1; next} {print}' \
      "$KCONF" > "$tmp" && cat "$tmp" > "$KCONF"; rm -f "$tmp"
  else
    printf 'BUNDLE_WEEKLY_REVIEW=on\n' >> "$KCONF"
  fi
fi
check_rows; exit $?
