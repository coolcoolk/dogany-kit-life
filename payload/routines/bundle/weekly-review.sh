#!/bin/bash
# weekly-review.sh -- kit_life bundle routine: the weekly retro (2.38.0).
#
# PROMOTED from the life agent's instance-owned weekly (routines/
# weekly-review.sh + routines/lib/life_dashboard.py) and generalised, so every
# crew member closes its week the same way:
#
#   role=main    MAIN AREAS. The life agent's weekly as a conversation, the
#                same shape as the daily retro (owner 2026-10-04): the cron
#                push is a START CARD (title, week range, one count line,
#                plan line, the start question + the start button). The
#                areas (plans & people / work / life log / goals / next
#                week) are rendered here too, one message per question, and
#                ride the pending marker as workspace.steps; brief-session
#                relays them after the yes. A question either looks back on
#                the week or decides something for the next one; nothing is
#                asked only to fill data (owner 2026-10-05).
#   role=domain  PROVIDER AREAS. A domain agent's weekly: the kit draws the
#                title and the flow grid; the lines come from this instance's
#                retro-sections providers (weekly lane, grid lane). Silent
#                when every provider has nothing.
#
# The role and the owner scope come from `kit_life.sh scope-self` (nothing is
# stored for it). A domain line never appears on the main agent's weekly and
# the other way round: the main areas drop the domains another member owns
# (task scope + title-prefix data filter), the domain path reads only its own
# providers and never the kit's life lines.
#
# NO MODEL IN THE SEND PATH. gather (this script, kit_life.sh verbs only) ->
# render (life_dashboard.py, deterministic) -> push.sh --text --button. A
# pending marker (kind=weekly-reflection, agent=<this agent>) lands in
# files/tmp for the brief-session skill to pick the conversation up. The
# start button works exactly like the daily retro's: the bridge replays the
# tapped label as the owner's next message, which wakes brief-session.
#
# WEEK BOUNDS (both roles, one derivation: life_dashboard.py review_week).
#   A run on the week's last day (Sunday; the week is Mon..Sun) reviews the
#   week ending today: the weekly is sent on Sunday evening (Sun 22:00 by
#   default, owner 2026-10-03), when the week is over in practice. A run on
#   any later day (Monday morning, a late send) reviews the week that already
#   ended. The guard refuses any week that has not reached its last day. The
#   preview / placement week is always the week right after the reviewed one.
#   (Until 2.39.0 the main path took "the last COMPLETED week", designed for
#   a Monday run, so a Sunday run reviewed the week before last.)
#
# Marker week convention (deterministic):
#   PLACE_WEEK_MARKER = ISO week of the week this retro places items into
#                       (the week after the reviewed one) -> '#rp<...>'
#   CLOSE_WEEK_MARKER = ISO week the previous retro placed into (= the
#                       reviewed week). The next retro's CLOSE == this PLACE.
#
# Usage: weekly-review.sh [--dry]
#   WEEKLY_REVIEW_FAKE_TODAY=YYYY-MM-DD pins the run date (honoured ONLY under
#   --dry, so a stray env var can never publish a wrong-week retro).
# Exit: 0 ok (also silence) / 1 config error
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
LIFE_SH="$AGENT_DIR/database/kit_life.sh"
PUSH_SH="$AGENT_DIR/routines/push.sh"
DASH_PY="$SCRIPT_DIR/life_dashboard.py"
DRY=0
[[ "${1:-}" == "--dry" ]] && DRY=1

[[ -f "$LIFE_SH" ]] || { echo "[weekly] kit_life.sh not found: $LIFE_SH" >&2; exit 1; }
[[ "$DRY" -eq 1 || -f "$PUSH_SH" ]] || { echo "[weekly] push.sh not found: $PUSH_SH" >&2; exit 1; }

conf_raw() { # conf_raw <KEY> -> raw text after '=' (first line), '' if absent
  grep -E "^$1=" "$AGENT_DIR/config/agent.conf" 2>/dev/null | head -1 | cut -d= -f2- || true
}
# conf_list <KEY> -> the value trimmed, one matched quote pair stripped (the
# agent.conf convention: BRIEF_EXCLUDE_TITLE_PREFIXES="a,b,c"). Read raw,
# the quotes glued onto the first and last prefix and neither ever matched.
conf_list() {
  conf_raw "$1" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' \
    -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'\$/\1/" || true
}
AGENT_LANG="$(conf_raw AGENT_LANG | tr -d '[:space:]')"
AGENT_LANG="${AGENT_LANG:-en}"

# i18n <key> [name=value ...] -> the formatted text ('' if missing). Same
# resolution as daily-retro.sh: the framework's layered lookup, else the
# kit's installed copy over config/i18n; AGENT_LANG first, then en.
i18n() {
  AGENT_DIR="$AGENT_DIR" AGENT_LANG="$AGENT_LANG" python3 -c '
import importlib.util, json, os, sys
root, lang = os.environ["AGENT_DIR"], os.environ["AGENT_LANG"]
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
    if isinstance(val, str):
        try:
            sys.stdout.write(val.format(**kw) if kw else val)
        except (KeyError, IndexError, ValueError):
            sys.stdout.write(val)
        break
' "$@" 2>/dev/null || true
}

# ---- scope: role, own slug, task scope, domains owned elsewhere ----------
SCOPE="$("$LIFE_SH" scope-self 2>/dev/null || true)"
sget() { printf '%s\n' "$SCOPE" | sed -n "s/^$1=//p" | head -1; }
ROLE="$(sget role)"; ROLE="${ROLE:-main}"
SELF="$(sget agent)"; [[ "$SELF" == "-" ]] && SELF=""
TASK_SCOPE="$(sget task_scope)"
OWNS="$(sget owns)"
OTHERS_OWN="$(sget others_own)"
SKIP_MODULES="$(sget skip_modules)"
OWNER_ARGS=()
[[ -n "$TASK_SCOPE" ]] && OWNER_ARGS=(--owning-agent "$TASK_SCOPE")
GOAL_ARGS=()
[[ -n "$SELF" ]] && GOAL_ARGS=(--owning-agent "$SELF")

# ---- timezone offset (framework resolver, then agent.conf, then 0) -------
TZ_OFFSET_H=""
TZ_LIB="$AGENT_DIR/routines/lib/tz_offset.sh"
if [[ -f "$TZ_LIB" ]]; then
  TZ_OFFSET_H="$(bash "$TZ_LIB" resolve "$AGENT_DIR" 2>/dev/null || true)"
  [[ "$TZ_OFFSET_H" =~ ^-?[0-9]+$ ]] || TZ_OFFSET_H=""
fi
if [[ -z "$TZ_OFFSET_H" ]]; then
  TZ_OFFSET_H="$(conf_raw AGENT_TZ_OFFSET_HOURS | tr -d '[:space:]')"
  [[ "$TZ_OFFSET_H" =~ ^-?[0-9]+$ ]] || TZ_OFFSET_H=0
fi

# ---- run date ---------------------------------------------------------------
TODAY="$(date +%F)"
if [[ -n "${WEEKLY_REVIEW_FAKE_TODAY:-}" ]]; then
  if [[ "$DRY" -ne 1 ]]; then
    echo "weekly-review: FATAL WEEKLY_REVIEW_FAKE_TODAY is set outside --dry -- refusing to emit" >&2
    exit 1
  fi
  if ! python3 -c "import datetime,sys;datetime.date.fromisoformat('${WEEKLY_REVIEW_FAKE_TODAY}')" 2>/dev/null; then
    echo "weekly-review: FATAL bad WEEKLY_REVIEW_FAKE_TODAY (want YYYY-MM-DD): ${WEEKLY_REVIEW_FAKE_TODAY}" >&2
    exit 1
  fi
  TODAY="$WEEKLY_REVIEW_FAKE_TODAY"
fi
# All week arithmetic derives from TODAY (one anchor, one derivation).
read -r WEEK_START WEEK_END WEEK_END_EXCL THIS_MON PREVIEW_MON WEEK_IS_CURRENT \
  < <(python3 "$DASH_PY" week "$TODAY")
[[ -n "${WEEK_IS_CURRENT:-}" ]] || { echo "weekly-review: FATAL could not derive the week bounds" >&2; exit 1; }
# The reviewed week must have reached its last day (never an in-progress week).
if ! _why="$(python3 "$DASH_PY" week-guard "$WEEK_START" "$WEEK_END" "$TODAY" 2>&1)"; then
  echo "weekly-review: FATAL week-bound guard tripped (WEEK_START=$WEEK_START WEEK_END=$WEEK_END TODAY=$TODAY: $_why) -- refusing to review an in-progress week" >&2
  exit 1
fi

MARKER_PATH="$AGENT_DIR/files/tmp/retro-pending-weekly-reflection-${TODAY//-/}.json"

# =========================================================================
# DOMAIN PATH: provider areas
# =========================================================================
if [[ "$ROLE" == "domain" ]]; then
  D_START="$WEEK_START"; D_END="$WEEK_END"
  # Providers read the window from these (additive to the registry contract).
  export KIT_RETRO_TODAY="$TODAY" KIT_RETRO_WEEK_START="$D_START" KIT_RETRO_WEEK_END="$D_END"
  ROLE_ARGS=(--role domain)
  [[ -n "$SKIP_MODULES" ]] && ROLE_ARGS+=(--skip-module "$SKIP_MODULES")
  SECTIONS="$("$LIFE_SH" retro-sections --lane weekly --mode section "${ROLE_ARGS[@]}" 2>/dev/null || true)"
  GRID="$("$LIFE_SH" retro-sections --lane weekly --mode grid "${ROLE_ARGS[@]}" 2>/dev/null || true)"
  DOM="${OWNS%%,*}"
  LABEL="$(i18n "domain.label.${DOM:-x}")"
  BODY="$(
    WEEKLY_AGENT_DIR="$AGENT_DIR" AGENT_LANG="$AGENT_LANG" \
    WEEK_START="$D_START" WEEK_END="$D_END" DOMAIN_LABEL="$LABEL" \
    DOMAIN_SECTIONS="$SECTIONS" DOMAIN_GRID="$GRID" \
    python3 "$DASH_PY" domain
  )"
  write_domain_marker() { # <dry|real>
    RENDERED_TEXT="$BODY" MARKER_DATE="$TODAY" AGENT_SLUG="$SELF" \
    WEEK_START="$D_START" WEEK_END="$D_END" MARKER_PATH="$MARKER_PATH" \
    MARKER_MODE="$1" python3 -c "
import json, os
from datetime import datetime, timezone
obj = {
    'agent': os.environ.get('AGENT_SLUG', ''),
    'kind': 'weekly-reflection',
    'date': os.environ['MARKER_DATE'],
    'week_start': os.environ['WEEK_START'],
    'week_end': os.environ['WEEK_END'],
    # the reviewed week has reached its last day (same rule as the guard)
    'week_ok': os.environ['WEEK_END'] <= os.environ['MARKER_DATE'],
    'dashboard_text': os.environ.get('RENDERED_TEXT', ''),
    'rendered_text': os.environ.get('RENDERED_TEXT', ''),
    'created_utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
}
txt = json.dumps(obj, ensure_ascii=False, indent=2)
if os.environ.get('MARKER_MODE') == 'real':
    os.makedirs(os.path.dirname(os.environ['MARKER_PATH']), exist_ok=True)
    with open(os.environ['MARKER_PATH'], 'w') as f:
        f.write(txt + '\n')
else:
    print(txt)
"
  }
  if [[ "$DRY" -eq 1 ]]; then
    echo "---- SCOPE ----"; echo "role=$ROLE agent=$SELF owns=$OWNS week=$D_START..$D_END"
    echo "---- SECTIONS ----"; echo "${SECTIONS:-(none)}"
    echo "---- GRID ----"; echo "${GRID:-(none)}"
    if [[ -z "$BODY" ]]; then
      echo "---- BODY ----"; echo "(silent: no provider has anything this week)"
      exit 0
    fi
    echo "---- PENDING MARKER (would write to: $MARKER_PATH) ----"
    write_domain_marker dry
    echo "---- BODY (rendered, verbatim -- this is exactly what push.sh sends) ----"
    echo "$BODY"
    exit 0
  fi
  if [[ -z "$(printf '%s' "$BODY" | tr -d '[:space:]')" ]]; then
    echo "[weekly] domain: no provider has anything this week -- silent"
    exit 0
  fi
  write_domain_marker real
  exec "$PUSH_SH" --text "$BODY"
fi

# =========================================================================
# MAIN PATH: main areas (byte-identical to the instance weekly it replaces)
# =========================================================================

# ---- schedule title-prefix exclusion (display axis) ----
# BRIEF_EXCLUDE_TITLE_PREFIXES (quotes stripped; the instance weekly read it
# raw, so a quoted value never matched its first and last prefix), plus the
# owner-language prefixes that are ALWAYS routine on this surface
# (retro.weekly.exclude_always), appended when not already present.
EXCLUDE_PREFIXES_RAW="$(conf_list BRIEF_EXCLUDE_TITLE_PREFIXES)"
EXCLUDE_PREFIXES_NEXT="$EXCLUDE_PREFIXES_RAW"
_ALWAYS="$(i18n retro.weekly.exclude_always)"
IFS=',' read -r -a _ALWAYS_ARR <<< "$_ALWAYS"
for _p in ${_ALWAYS_ARR[@]+"${_ALWAYS_ARR[@]}"}; do
  [[ -n "$_p" ]] || continue
  if ! printf '%s' "$EXCLUDE_PREFIXES_NEXT" | grep -qF "$_p"; then
    EXCLUDE_PREFIXES_NEXT="${EXCLUDE_PREFIXES_NEXT:+${EXCLUDE_PREFIXES_NEXT},}${_p}"
  fi
done

# ---- other-domain title prefixes (data-layer boundary) ----
# A domain another crew member owns is never even mentioned here. The rows
# are already cut by owner (task scope); this prefix list is the second layer
# for rows whose owner column cannot tell (appointments, projects, anchors).
# RETRO_ANCHOR_EXCLUDE_PREFIXES overrides; default = the owner-language
# prefixes of every domain in scope-self others_own.
ANCHOR_EXCL_RAW="$(conf_list RETRO_ANCHOR_EXCLUDE_PREFIXES)"
ANCHOR_EXCL="$ANCHOR_EXCL_RAW"
if [[ -z "$ANCHOR_EXCL" ]]; then
  IFS=',' read -r -a _OO <<< "$OTHERS_OWN"
  for _d in ${_OO[@]+"${_OO[@]}"}; do
    [[ -n "$_d" ]] || continue
    _px="$(i18n "retro.weekly.domain_prefixes.$_d")"
    [[ -n "$_px" ]] && ANCHOR_EXCL="${ANCHOR_EXCL:+${ANCHOR_EXCL},}${_px}"
  done
fi

MON="$WEEK_START"
SUN="$WEEK_END"
# Week words for the data block: the reviewed week is "this week" on a
# Sunday run and "last week" after it; the preview week follows suit.
if [[ "$WEEK_IS_CURRENT" == "1" ]]; then
  W_WORD="$(i18n retro.weekly.word.this)"; N_WORD="$(i18n retro.weekly.word.next)"
else
  W_WORD="$(i18n retro.weekly.word.last)"; N_WORD="$(i18n retro.weekly.word.this)"
fi

# ---- (1) tasks done in the reviewed week (owner-scoped) ----
DONE_ROWS="$("$LIFE_SH" task-done-between "$WEEK_START" "$WEEK_END_EXCL" ${OWNER_ARGS[@]+"${OWNER_ARGS[@]}"} 2>/dev/null || true)"
DONE_CNT="$(printf '%s' "$DONE_ROWS" | grep -c . || true)"
DONE_TITLES="$(printf '%s' "$DONE_ROWS" | cut -f2 | grep -v '^$' || true)"

# ---- (2) open backlog before today (owner-scoped) ----
# The owner scope cuts the rows in the QUERY; the prefix filter below stays
# as a second layer and also drops '#rp' loop items (the work area reports
# those through its loop line, so they must not count twice).
OVERDUE_ROWS="$("$LIFE_SH" task-overdue ${OWNER_ARGS[@]+"${OWNER_ARGS[@]}"} 2>/dev/null || true)"
OVERDUE_CNT="$(printf '%s' "$OVERDUE_ROWS" | grep -c . || true)"
OVERDUE_AG_TSV="$(printf '%s' "$OVERDUE_ROWS" | ANCHOR_EXCL="$ANCHOR_EXCL" python3 -c "
import os, sys
dom = [p.strip() for p in os.environ.get('ANCHOR_EXCL','').split(',') if p.strip()]
for line in sys.stdin:
    c = line.rstrip('\n').split('\t')
    if len(c) < 3 or not c[1].strip():
        continue
    t = c[1].strip()
    if any(t.startswith(p) for p in dom):
        continue
    if '#rp' in t:
        continue
    print('%s\t%s\t%s' % (c[0], t, c[2]))
" 2>/dev/null || true)"

# ---- (2b) set aside for later (the daily retro's 'later' button, owner-scoped) ----
# Dateless open tasks. The weekly is where they come back (owner 2026-09-21
# D26): the work area asks when to do one, with a free slot proposed.
# task-untimed-open is read without --suggest (that flag writes a project
# link). Most recently set aside first.
LATER_TSV="$("$LIFE_SH" task-untimed-open ${OWNER_ARGS[@]+"${OWNER_ARGS[@]}"} 2>/dev/null \
  | ANCHOR_EXCL="$ANCHOR_EXCL" python3 -c "
import os, sys
dom = [p.strip() for p in os.environ.get('ANCHOR_EXCL','').split(',') if p.strip()]
rows = []
for line in sys.stdin:
    c = line.rstrip('\n').split('\t')
    if len(c) < 2 or not c[1].strip():
        continue
    t = c[1].strip()
    if any(t.startswith(p) for p in dom) or '#rp' in t:
        continue
    rows.append((c[2] if len(c) > 2 else '', c[0], t))
for upd, tid, t in sorted(rows, key=lambda r: r[0], reverse=True):
    print('%s\t%s\t%s' % (tid, t, upd))
" 2>/dev/null || true)"

# ---- (3) appointments in the reviewed week (appt-find, both ends inclusive) ----
PROM_ROWS="$("$LIFE_SH" appt-find "$MON" "$SUN" 2>/dev/null || true)"
# Render input: the week's live appointments as time<TAB>title, other-domain
# and display-excluded prefixes dropped (appt-find already drops abandoned).
APPT_WEEK_TSV="$(printf '%s' "$PROM_ROWS" \
  | ANCHOR_EXCL="$ANCHOR_EXCL" BRIEF_EXCLUDE_TITLE_PREFIXES="$EXCLUDE_PREFIXES_NEXT" python3 -c "
import os, sys
def _sp(k):
    return [p.strip() for p in os.environ.get(k,'').split(',') if p.strip()]
skip = _sp('ANCHOR_EXCL') + _sp('BRIEF_EXCLUDE_TITLE_PREFIXES')
for line in sys.stdin:
    c = line.rstrip('\n').split('\t')
    if len(c) < 3 or not c[2].strip():
        continue
    t = c[2].strip()
    if any(t.startswith(p) for p in skip):
        continue
    print('%s\t%s' % (c[1], t))
" 2>/dev/null || true)"

# Data block count + list from the same filtered rows the steps use, so the
# skill never reads an appointment the owner excluded (a buffer block) or one
# another member owns.
PROM_CNT="$(printf '%s' "$APPT_WEEK_TSV" | grep -c . || true)"
PROM_TXT="$(printf '%s' "$APPT_WEEK_TSV" | cut -f2 | grep -v '^$' | paste -sd ',' - | sed 's/,/, /g' || true)"

# ---- (4) finished work count (other-domain titles dropped) ----
# No project rows are read: the weekly never asks which project a task
# belonged to, nor proposes one (owner 2026-10-05).
DONE_AG_CNT="$(printf '%s' "$DONE_TITLES" | ANCHOR_EXCL="$ANCHOR_EXCL" python3 -c "
import os, sys
dom = [p.strip() for p in os.environ.get('ANCHOR_EXCL','').split(',') if p.strip()]
n = 0
for line in sys.stdin:
    t = line.strip()
    if t and not any(t.startswith(p) for p in dom):
        n += 1
print(n)
" 2>/dev/null || echo 0)"

# ---- (4b) life log (spend): the flow grid's daily spine ----
# Spend is the one life stream with real daily resolution (task settle times
# bunch up on the day the owner settles them), so the bar is daily spend and
# the note column is that day's appointments.
SPEND_ROW_TSV=""
DAY_SPEND_TSV=""
for _i in 0 1 2 3 4 5 6; do
  _d="$(python3 -c "
import datetime
print((datetime.date.fromisoformat('$WEEK_START') + datetime.timedelta(days=$_i)).isoformat())")"
  _rows="$("$LIFE_SH" spend-find "$_d" 2>/dev/null || true)"
  # spend-find: id<TAB>name<TAB>amount<TAB>category<TAB>method<TAB>scope
  _sum="$(printf '%s' "$_rows" | awk -F'\t' 'NF>=3 && $3 ~ /^[0-9]+$/ {s+=$3} END {printf "%d", s+0}')"
  DAY_SPEND_TSV+="${_d}	${_sum}"$'\n'
  if [[ -n "$_rows" ]]; then
    SPEND_ROW_TSV+="$(printf '%s' "$_rows" | awk -F'\t' -v d="$_d" 'NF>=4 && $3 ~ /^[0-9]+$/ {printf "%s\t%s\t%s\t%s\n", d, $2, $3, $4}')"$'\n'
  fi
done
SPEND_TOTAL="$(printf '%s' "$SPEND_ROW_TSV" | awk -F'\t' 'NF>=3 {s+=$3} END {printf "%d", s+0}')"
SPEND_CNT="$(printf '%s' "$SPEND_ROW_TSV" | grep -c . || true)"
SPEND_CAT_TSV="$(printf '%s' "$SPEND_ROW_TSV" | awk -F'\t' 'NF>=4 {a[$4]+=$3; c[$4]++} END {for (k in a) printf "%s\t%d\t%d\n", k, a[k], c[k]}' | sort -t$'\t' -k2 -nr)"
# Week-over-week comes from spend-week as computed there (never recomputed):
# 'prev_total=2,893,417원 (45건)' -> take the digits before the currency
# mark only (scanning the whole line glues the count onto the amount).
SPEND_PREV="$("$LIFE_SH" spend-week "$WEEK_START" 2>/dev/null \
  | sed -n 's/^prev_total=\([0-9,]*\)원.*/\1/p' | head -1 | tr -d ',' || true)"
SPEND_PREV="${SPEND_PREV:-0}"
DAY_LABEL_TSV="$(printf '%s' "$APPT_WEEK_TSV" | python3 -c "
import sys
from collections import OrderedDict
acc = OrderedDict()
for line in sys.stdin:
    c = line.rstrip('\n').split('\t')
    if len(c) < 2 or not c[0]:
        continue
    acc.setdefault(str(c[0])[:10], []).append(c[1])
for d, ts in acc.items():
    print('%s\t%s' % (d, ' · '.join(ts)))
" 2>/dev/null || true)"

# ---- next-week bounds (local Mon 00:00 .. next Mon 00:00, as UTC) ----
read -r NEXT_MON_UTC NEXT_SUN_UTC < <(python3 -c "
from datetime import datetime, timezone, timedelta
off = int('$TZ_OFFSET_H')
tz = timezone(timedelta(hours=off))
next_mon = datetime.strptime('$PREVIEW_MON', '%Y-%m-%d').replace(hour=0, minute=0, second=0, tzinfo=tz)
next_mon_after = next_mon + timedelta(days=7)
print(next_mon.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ') + ' ' +
      next_mon_after.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))
")
NEXT_SUN="$(python3 -c "
import datetime
print((datetime.date.fromisoformat('$PREVIEW_MON') + datetime.timedelta(days=6)).isoformat())
")"

# (a) next-week appointments (data block), display-excluded prefixes dropped.
APPT_TXT="$("$LIFE_SH" event-window "$NEXT_MON_UTC" "$NEXT_SUN_UTC" appointment 2>/dev/null \
  | BRIEF_EXCLUDE_TITLE_PREFIXES="$EXCLUDE_PREFIXES_NEXT" python3 -c "
import sys, os
from datetime import datetime, timezone, timedelta
LOCAL_TZ = timezone(timedelta(hours=int('$TZ_OFFSET_H')))
_raw = os.environ.get('BRIEF_EXCLUDE_TITLE_PREFIXES', '')
EXCL = [p.strip() for p in _raw.split(',') if p.strip()]
for line in sys.stdin:
    cols = line.rstrip('\n').split('\t')
    if len(cols) < 3:
        continue
    title = cols[2]; sa = cols[3] if len(cols) > 3 else ''
    if any(title.lstrip().startswith(p) for p in EXCL):
        continue
    try:
        dt = datetime.strptime(sa, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc).astimezone(LOCAL_TZ)
        date_str = dt.strftime('%m/%d(%a)')
    except Exception:
        date_str = ''
    print('- ' + (date_str + ' ' if date_str else '') + title)
" 2>/dev/null || true)"

# (a2) render input: ISO date<TAB>title, same filters plus other-domain
# prefixes. The placement's free-day rule reads this list.
NEXT_APPT_TSV="$("$LIFE_SH" event-window "$NEXT_MON_UTC" "$NEXT_SUN_UTC" appointment 2>/dev/null \
  | BRIEF_EXCLUDE_TITLE_PREFIXES="$EXCLUDE_PREFIXES_NEXT" ANCHOR_EXCL="$ANCHOR_EXCL" python3 -c "
import sys, os
from datetime import datetime, timezone, timedelta
LOCAL_TZ = timezone(timedelta(hours=int('$TZ_OFFSET_H')))
def _sp(k):
    return [p.strip() for p in os.environ.get(k,'').split(',') if p.strip()]
EXCL = _sp('BRIEF_EXCLUDE_TITLE_PREFIXES') + _sp('ANCHOR_EXCL')
for line in sys.stdin:
    cols = line.rstrip('\n').split('\t')
    if len(cols) < 4:
        continue
    title, sa = cols[2], cols[3]
    if any(title.lstrip().startswith(p) for p in EXCL):
        continue
    try:
        dt = datetime.strptime(sa, '%Y-%m-%dT%H:%M:%SZ').replace(
            tzinfo=timezone.utc).astimezone(LOCAL_TZ)
    except Exception:
        continue
    print('%s\t%s' % (dt.date().isoformat(), title))
" 2>/dev/null || true)"

# (b) next-week public holidays. The instance table had wrong 설날 (01-28..30)
# and 부처님오신날 (05-25) dates and no substitute days; corrected to the dates
# in database/holiday_source.py _STATIC_KR (owner OK 2026-10-02). Names stay
# here because holiday_source carries dates only.
HOLIDAY_TXT="$(python3 -c "
from datetime import date, timedelta
KR_HOLIDAYS_2026 = {
    '2026-01-01': '신정',
    '2026-02-16': '설날 연휴',
    '2026-02-17': '설날',
    '2026-02-18': '설날 연휴',
    '2026-03-01': '삼일절',
    '2026-03-02': '삼일절 대체공휴일',
    '2026-05-01': '노동절',
    '2026-05-05': '어린이날',
    '2026-05-24': '부처님오신날',
    '2026-05-25': '부처님오신날 대체공휴일',
    '2026-06-03': '지방선거',
    '2026-06-06': '현충일',
    '2026-07-17': '제헌절',
    '2026-08-15': '광복절',
    '2026-08-17': '광복절 대체공휴일',
    '2026-09-24': '추석 연휴',
    '2026-09-25': '추석',
    '2026-09-26': '추석 연휴',
    '2026-10-03': '개천절',
    '2026-10-05': '개천절 대체공휴일',
    '2026-10-09': '한글날',
    '2026-12-25': '성탄절',
}
next_mon = date.fromisoformat('$PREVIEW_MON')
next_sun = next_mon + timedelta(days=6)
hits = [(d, n) for d, n in sorted(KR_HOLIDAYS_2026.items())
        if next_mon <= date.fromisoformat(d) <= next_sun]
for d, n in hits:
    dt = date.fromisoformat(d)
    print('- ' + dt.strftime('%m/%d') + ' ' + n)
" 2>/dev/null || true)"

# ---- ISO week markers (loop closure + placement carrier) ----
read -r PLACE_WEEK_MARKER CLOSE_WEEK_MARKER < <(python3 -c "
from datetime import date
def iso(d):
    y, w, _ = date.fromisoformat(d).isocalendar()
    return '%dW%02d' % (y, w)
print(iso('$PREVIEW_MON'), iso('$WEEK_START'))
")

# ---- (5) loop closure: did last retro's placements happen (real data) ----
# task-find <keyword> = title LIKE %keyword%; id\ttitle\tdue\tdone|todo\tplace.
L_DONE="$(i18n retro.weekly.data.loop_done)"
L_TODO="$(i18n retro.weekly.data.loop_todo)"
LOOP_ROWS="$("$LIFE_SH" task-find "#rp${CLOSE_WEEK_MARKER}" 2>/dev/null || true)"
LOOP_CNT="$(printf '%s' "$LOOP_ROWS" | grep -c . || true)"
LOOP_DONE="$(printf '%s' "$LOOP_ROWS" | awk -F'\t' '$4=="done"' | grep -c . || true)"
LOOP_TXT="$(printf '%s' "$LOOP_ROWS" | awk -F'\t' -v ld="$L_DONE" -v lt="$L_TODO" 'NF>=4 {
  t=$2; gsub(/ *#rp[0-9]+W[0-9]+ */, "", t);
  printf "- %s [%s]\n", t, ($4=="done" ? ld : lt)
}' || true)"
# Render input: title<TAB>done|todo, the '#rp' marker stripped (it rides the
# title only so task-find can query it; it is not for the owner to read).
LOOP_MACHINE_TSV="$(printf '%s' "$LOOP_ROWS" | awk -F'\t' 'NF>=4 {
  t=$2; gsub(/ *#rp[0-9]+W[0-9]+ */, "", t);
  printf "%s\t%s\n", t, $4
}' || true)"

# ---- (6) goals + per-layer review cadence ----
# short / event_overlay = judged weekly; mid = shown only (judged on the last
# Sunday of the month); long = not touched (quarter end). dormant goals are
# never contrasted or mentioned. Only this agent's goals.
GOAL_ROWS="$("$LIFE_SH" goal-list ${GOAL_ARGS[@]+"${GOAL_ARGS[@]}"} --status active 2>/dev/null || true)"
# Keyed off the reviewed week's Sunday (not the run date), so a Monday run
# judges the same month end a Sunday run would.
read -r MID_DUE QTR_DUE < <(python3 -c "
from datetime import date, timedelta
d = date.fromisoformat('$WEEK_END')
last_sunday_of_month = (d + timedelta(days=7)).month != d.month
print(int(last_sunday_of_month),
      int(last_sunday_of_month and d.month in (3, 6, 9, 12)))
")
SELF_NAME="$(conf_raw AGENT_SELF_NAME | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'\$/\1/")"
G_NONE="$(i18n retro.weekly.data.goal_none self="${SELF_NAME:-$SELF}")"
GOAL_BLOCK="$(GOAL_ROWS="$GOAL_ROWS" MID_DUE="$MID_DUE" QTR_DUE="$QTR_DUE" \
  L_JUDGE="$(i18n retro.weekly.data.goal_judge)" \
  L_SHOW="$(i18n retro.weekly.data.goal_show)" \
  L_BG="$(i18n retro.weekly.data.goal_bg)" \
  L_NONE="$G_NONE" L_NONE_DUE="$(i18n retro.weekly.data.goal_none_due)" python3 -c "
import os
rows = [r.split('\t') for r in os.environ.get('GOAL_ROWS','').split('\n') if r.strip()]
mid_due = os.environ.get('MID_DUE') == '1'
qtr_due = os.environ.get('QTR_DUE') == '1'
judge, show, background, dormant = [], [], [], 0
for c in rows:
    if len(c) < 12:
        continue
    layer, focus, title = c[2], c[4], c[11]
    if focus == 'dormant':
        dormant += 1
        continue
    if layer in ('short', 'event_overlay'):
        judge.append(title)
    elif layer == 'mid':
        (judge if mid_due else show).append(title)
    elif layer == 'long':
        (judge if qtr_due else background).append(title)
out = []
if judge:
    out.append(os.environ['L_JUDGE'])
    out += ['- ' + t for t in judge]
if show:
    out.append(os.environ['L_SHOW'])
    out += ['- ' + t for t in show]
if background:
    out.append(os.environ['L_BG'])
    out += ['- ' + t for t in background]
if not rows:
    out.append(os.environ['L_NONE'])
elif not (judge or show or background):
    out.append(os.environ['L_NONE_DUE'])
print('\n'.join(out))
" 2>/dev/null || true)"
GOAL_JUDGE_CNT="$(printf '%s' "$GOAL_BLOCK" | grep -c '^- ' || true)"
# Render input from the SAME gate and inputs: JUDGE / SHOW / BG + any rows.
GOAL_SPLIT="$(GOAL_ROWS="$GOAL_ROWS" MID_DUE="$MID_DUE" QTR_DUE="$QTR_DUE" python3 -c "
import os
rows = [r.split('\t') for r in os.environ.get('GOAL_ROWS','').split('\n') if r.strip()]
mid_due = os.environ.get('MID_DUE') == '1'
qtr_due = os.environ.get('QTR_DUE') == '1'
judge, show, bg = [], [], []
for c in rows:
    if len(c) < 12:
        continue
    layer, focus, title = c[2], c[4], c[11]
    if focus == 'dormant':
        continue
    if layer in ('short', 'event_overlay'):
        judge.append(title)
    elif layer == 'mid':
        (judge if mid_due else show).append(title)
    elif layer == 'long':
        (judge if qtr_due else bg).append(title)
print('JUDGE')
for t in judge: print(t)
print('SHOW')
for t in show: print(t)
print('BG')
for t in bg: print(t)
print('ANY')
print('1' if rows else '0')
" 2>/dev/null || true)"
GOAL_JUDGE_TXT="$(printf '%s' "$GOAL_SPLIT" | sed -n '/^JUDGE$/,/^SHOW$/p' | sed '1d;$d')"
GOAL_SHOW_TXT="$(printf '%s' "$GOAL_SPLIT" | sed -n '/^SHOW$/,/^BG$/p' | sed '1d;$d')"
GOAL_BG_TXT="$(printf '%s' "$GOAL_SPLIT" | sed -n '/^BG$/,/^ANY$/p' | sed '1d;$d')"
GOAL_ROWS_ANY="$(printf '%s' "$GOAL_SPLIT" | sed -n '/^ANY$/,$p' | sed '1d' | head -1)"

# ---- data block (material for the retro conversation, not the push) ----
DATA="$(i18n retro.weekly.data.week week="$W_WORD" start="$MON" end="$SUN")"$'\n'
DATA+="$(i18n retro.weekly.data.done done="$DONE_CNT" overdue="$OVERDUE_CNT")"$'\n'
DATA+="$(i18n retro.weekly.data.appts n="$PROM_CNT" list="${PROM_TXT:+ ($PROM_TXT)}")"

# Anchor material (react-not-recall): finished titles minus routines, so the
# message opens on a concrete fragment rather than a number.
#   (a) a title repeated 3+ times in the week is a routine by the owner's own
#       frequency -> context block, not an anchor
#   (b) BRIEF_EXCLUDE_TITLE_PREFIXES (+ always-routine prefixes) -> context
#   (c) other-domain prefixes -> dropped entirely
#   (d) de-duplicated, capped at 15
ANCHOR_TXT="$(DONE_TITLES="$DONE_TITLES" \
  BRIEF_EXCLUDE_TITLE_PREFIXES="$EXCLUDE_PREFIXES_NEXT" \
  ANCHOR_EXCL="$ANCHOR_EXCL" python3 -c "
import os
from collections import Counter
titles = [t.strip() for t in os.environ.get('DONE_TITLES','').split('\n') if t.strip()]
def _split(k):
    return [p.strip() for p in os.environ.get(k,'').split(',') if p.strip()]
excl = _split('BRIEF_EXCLUDE_TITLE_PREFIXES')
domain = _split('ANCHOR_EXCL')
freq = Counter(titles)
seen, cand, routine = set(), [], []
for t in titles:
    if t in seen:
        continue
    seen.add(t)
    if any(t.lstrip().startswith(p) for p in domain):
        continue
    if any(t.lstrip().startswith(p) for p in excl) or freq[t] >= 3:
        routine.append((t, freq[t])); continue
    cand.append(t)
print('CAND')
for t in cand[:15]:
    print('- ' + t)
print('ROUTINE')
for t, n in routine:
    print('- %s x%d' % (t, n))
" 2>/dev/null || true)"
ANCHOR_CAND="$(printf '%s' "$ANCHOR_TXT" | sed -n '/^CAND$/,/^ROUTINE$/p' | sed '1d;$d')"
ANCHOR_ROUTINE="$(printf '%s' "$ANCHOR_TXT" | sed -n '/^ROUTINE$/,$p' | sed '1d')"

DATA+=$'\n\n'"$(i18n retro.weekly.data.anchor_head week="$W_WORD")"$'\n'
if [[ -n "$ANCHOR_CAND" ]]; then
  DATA+="${ANCHOR_CAND}"$'\n'
else
  DATA+="$(i18n retro.weekly.data.anchor_none week="$W_WORD")"$'\n'
fi
if [[ -n "$ANCHOR_ROUTINE" ]]; then
  DATA+=$'\n'"$(i18n retro.weekly.data.routine_head)"$'\n'"${ANCHOR_ROUTINE}"$'\n'
fi

DATA+=$'\n'"$(i18n retro.weekly.data.loop_head marker="#rp${CLOSE_WEEK_MARKER}")"$'\n'
if [[ -n "$LOOP_TXT" ]]; then
  DATA+="${LOOP_TXT}"$'\n'"$(i18n retro.weekly.data.loop_count done="$LOOP_DONE" total="$LOOP_CNT")"$'\n'
else
  DATA+="$(i18n retro.weekly.data.loop_none)"$'\n'
fi

DATA+=$'\n'"$(i18n retro.weekly.data.goal_head)"$'\n'
DATA+="${GOAL_BLOCK:-$G_NONE}"$'\n'

DATA+=$'\n\n'"$(i18n retro.weekly.data.next week="$N_WORD" start="$PREVIEW_MON" end="$NEXT_SUN")"$'\n'
DATA+="$(i18n retro.weekly.data.next_appts)"$'\n'
if [[ -n "$APPT_TXT" ]]; then DATA+="${APPT_TXT}"$'\n'; else DATA+="$(i18n retro.weekly.data.none)"$'\n'; fi
if [[ -n "$HOLIDAY_TXT" ]]; then
  DATA+="$(i18n retro.weekly.data.holidays)"$'\n'"${HOLIDAY_TXT}"$'\n'
fi

# Low-data guard: with almost nothing tracked, say so lightly, never inflate.
LOW_DATA=""
if [[ "$DONE_CNT" -eq 0 && "$OVERDUE_CNT" -eq 0 && "$PROM_CNT" -eq 0 ]]; then
  LOW_DATA="$(i18n retro.weekly.data.low)"
fi

# ---- agent recommendation (evidence-gated, at most one per retro) ----
# The framework's mint-agent module measures repeated signals; with none it
# prints nothing, which is the correct result. Declines are remembered there.
# This is the ONLY recommendation path of the weekly; the renderer appends
# the block verbatim and never originates one. Module absent -> skipped.
MINT_EVAL="$AGENT_DIR/.claude/skills/mint-agent/scripts/weekly_eval.py"
MINT_BLOCK=""
if [[ -f "$MINT_EVAL" ]]; then
  MINT_ARGS=()
  [[ "$DRY" -eq 1 ]] && MINT_ARGS+=(--dry-run)
  # The homebrew interpreter first: the system python's sqlite cannot open a
  # WAL database mode=ro (it needs to create -shm), which zeroed the
  # evaluator's record counts.
  if [[ -x /opt/homebrew/bin/python3 ]]; then MINT_PY=/opt/homebrew/bin/python3; else MINT_PY=/usr/bin/python3; fi
  MINT_BLOCK="$("$MINT_PY" "$MINT_EVAL" ${MINT_ARGS[@]+"${MINT_ARGS[@]}"} \
    2>>"$AGENT_DIR/.telegram_bot/logs/mint-agent.log" || true)"
  [[ "$DRY" -eq 1 ]] && MINT_BLOCK=""
fi

render() { # render <opener|steps|weekly|width>
  WEEKLY_AGENT_DIR="$AGENT_DIR" AGENT_LANG="$AGENT_LANG" \
  WEEKLY_ADDRESS="$(conf_raw AGENT_ADDRESS | tr -d '[:space:]')" \
  WEEK_START="$WEEK_START" WEEK_END="$WEEK_END" WEEK_IS_CURRENT="$WEEK_IS_CURRENT" \
  PREVIEW_MON="$PREVIEW_MON" NEXT_SUN="$NEXT_SUN" \
  DAY_SPEND_TSV="$DAY_SPEND_TSV" DAY_LABEL_TSV="$DAY_LABEL_TSV" \
  APPT_WEEK_TSV="$APPT_WEEK_TSV" \
  DONE_AG_CNT="$DONE_AG_CNT" OVERDUE_AG_TSV="$OVERDUE_AG_TSV" \
  LATER_TSV="$LATER_TSV" \
  LOOP_TSV="$LOOP_MACHINE_TSV" LOOP_DONE="$LOOP_DONE" LOOP_CNT="$LOOP_CNT" \
  SPEND_TOTAL="$SPEND_TOTAL" SPEND_CNT="$SPEND_CNT" SPEND_PREV="$SPEND_PREV" \
  SPEND_CAT_TSV="$SPEND_CAT_TSV" \
  GOAL_JUDGE_TXT="$GOAL_JUDGE_TXT" GOAL_SHOW_TXT="$GOAL_SHOW_TXT" \
  GOAL_BG_TXT="$GOAL_BG_TXT" GOAL_ROWS_ANY="$GOAL_ROWS_ANY" \
  NEXT_APPT_TSV="$NEXT_APPT_TSV" HOLIDAY_TXT="$HOLIDAY_TXT" \
  ANCHOR_CAND="$ANCHOR_CAND" MINT_BLOCK="$MINT_BLOCK" \
  python3 "$DASH_PY" "$1"
}
# The push is the start card only; the areas wait for the owner's yes.
BODY="$(render opener)"
STEPS_JSON="$(render steps)"
START_LABEL="$(i18n retro.weekly.start_button)"
START_LABEL="${START_LABEL:-Start weekly retro}"

# ---- pending marker (kind=weekly-reflection), consumed by brief-session ----
# dashboard_text = the data block; rendered_text = the message as sent (the
# start card, so the skill never re-asks what it asked); workspace.steps =
# the area messages, relayed one per turn after the yes. A re-run on the
# same day overwrites (upsert, never a blind insert). --dry prints the JSON.
MARKER_DATE="$TODAY"
write_marker() {  # write_marker <mode: dry|real>
  local _mode="$1"
  DASHBOARD_TEXT="$DATA" \
  RENDERED_TEXT="$BODY" STEPS_JSON="$STEPS_JSON" START_LABEL="$START_LABEL" \
  MARKER_DATE="$MARKER_DATE" AGENT_SLUG="$SELF" \
  WEEK_START="$WEEK_START" WEEK_END="$WEEK_END" WEEK_IS_CURRENT="$WEEK_IS_CURRENT" \
  DONE_CNT="$DONE_CNT" OVERDUE_CNT="$OVERDUE_CNT" PROM_CNT="$PROM_CNT" \
  DONE_TITLES="$DONE_TITLES" \
  PLACE_WEEK_MARKER="$PLACE_WEEK_MARKER" CLOSE_WEEK_MARKER="$CLOSE_WEEK_MARKER" \
  PREVIEW_MON="$PREVIEW_MON" NEXT_SUN="$NEXT_SUN" \
  LOOP_ROWS="$LOOP_ROWS" LOOP_CNT="$LOOP_CNT" LOOP_DONE="$LOOP_DONE" \
  GOAL_ROWS="$GOAL_ROWS" MID_DUE="$MID_DUE" QTR_DUE="$QTR_DUE" \
  MARKER_PATH="$MARKER_PATH" MARKER_MODE="$_mode" \
  python3 -c "
import json, os, sys
from datetime import datetime, timezone
def _int(k):
    try: return int(os.environ.get(k, '0') or '0')
    except Exception: return 0
ws = os.environ.get('WEEK_START', '')
we = os.environ.get('WEEK_END', '')
# Consumer-side guard: the reviewed week must have reached its last day.
week_ok = bool(ws) and bool(we) and (we <= os.environ.get('MARKER_DATE', ''))
obj = {
    'agent': os.environ.get('AGENT_SLUG', ''),
    'kind': 'weekly-reflection',
    'date': os.environ['MARKER_DATE'],
    'week_start': ws,
    'week_end': we,
    'week_ok': week_ok,
    'week_is_current': os.environ.get('WEEK_IS_CURRENT') == '1',
    'dashboard_text': os.environ.get('DASHBOARD_TEXT', ''),
    'rendered_text': os.environ.get('RENDERED_TEXT', ''),
    'metrics': {
        'done': _int('DONE_CNT'),
        'overdue': _int('OVERDUE_CNT'),
        'appointments': _int('PROM_CNT'),
    },
    'done_titles': [t for t in os.environ.get('DONE_TITLES', '').split('\n') if t.strip()][:40],
    'created_utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
}
# review->plan workspace state: deterministic data the skill must not
# recompute or guess.
_loop = []
for _line in os.environ.get('LOOP_ROWS', '').split('\n'):
    _c = _line.split('\t')
    if len(_c) >= 4 and _c[0].strip():
        _loop.append({'id': _c[0], 'title': _c[1], 'due': _c[2],
                      'state': _c[3]})
_goals = {'judge': [], 'show': [], 'background': [], 'dormant_skipped': 0}
_mid_due = os.environ.get('MID_DUE') == '1'
_qtr_due = os.environ.get('QTR_DUE') == '1'
for _line in os.environ.get('GOAL_ROWS', '').split('\n'):
    _c = _line.split('\t')
    if len(_c) < 12 or not _c[0].strip():
        continue
    _g = {'id': _c[0], 'ulid': _c[1], 'layer': _c[2], 'title': _c[11]}
    if _c[4] == 'dormant':
        _goals['dormant_skipped'] += 1
    elif _c[2] in ('short', 'event_overlay'):
        _goals['judge'].append(_g)
    elif _c[2] == 'mid':
        (_goals['judge'] if _mid_due else _goals['show']).append(_g)
    elif _c[2] == 'long':
        (_goals['judge'] if _qtr_due else _goals['background']).append(_g)
try:
    _steps = json.loads(os.environ.get('STEPS_JSON') or '[]')
except ValueError:
    _steps = []
obj['workspace'] = {
    # the start card's button label (the bridge replays it as the owner's
    # message) and the area messages to relay after it, in order: send
    # text verbatim, attach options as buttons, one step per turn
    'start_label': os.environ.get('START_LABEL', ''),
    'steps': _steps,
    # placements go straight into task/timeblock with --at, stamped with
    # this marker so next week's retro can close the loop
    'place_week_marker': '#rp' + os.environ.get('PLACE_WEEK_MARKER', ''),
    'place_week_start': os.environ.get('PREVIEW_MON', ''),
    'place_week_end': os.environ.get('NEXT_SUN', ''),
    # loop closure runs off the title marker (the note field is not
    # queryable). Backticks are forbidden in this python block: it lives in
    # a double-quoted shell string.
    'close_week_marker': '#rp' + os.environ.get('CLOSE_WEEK_MARKER', ''),
    'loop_items': _loop,
    'loop_done': int(os.environ.get('LOOP_DONE', '0') or '0'),
    'loop_total': int(os.environ.get('LOOP_CNT', '0') or '0'),
    # horizon-keyed cadence off ledger_goal.layer
    'goals': _goals,
    'mid_judge_due': _mid_due,
    'quarter_renegotiate_due': _qtr_due,
    'mode': ('reflection' if (_goals['judge'] or _goals['show'])
             else 'weaving'),
}
txt = json.dumps(obj, ensure_ascii=False, indent=2)
if os.environ.get('MARKER_MODE') == 'real':
    os.makedirs(os.path.dirname(os.environ['MARKER_PATH']), exist_ok=True)
    with open(os.environ['MARKER_PATH'], 'w') as f:
        f.write(txt + '\n')
else:
    print(txt)
"
}

if [[ "$DRY" -eq 1 ]]; then
  echo "---- REVIEWED WEEK (Mon..Sun ending today on Sunday, else the week that ended) ----"
  echo "WEEK_START=$WEEK_START  WEEK_END=$WEEK_END  WEEK_END_EXCL=$WEEK_END_EXCL  TODAY=$TODAY  CURRENT=$WEEK_IS_CURRENT  PREVIEW_MON=$PREVIEW_MON"
  echo "---- DATA ----"; echo "$DATA"
  echo "---- LOW_DATA ----"; echo "${LOW_DATA:-$(i18n retro.weekly.data.none)}"
  echo "---- NEXT_MON_UTC ----"; echo "$NEXT_MON_UTC"
  echo "---- NEXT_SUN_UTC ----"; echo "$NEXT_SUN_UTC"
  echo "---- APPT_TXT ----"; echo "${APPT_TXT:-$(i18n retro.weekly.data.none)}"
  echo "---- HOLIDAY_TXT ----"; echo "${HOLIDAY_TXT:-$(i18n retro.weekly.data.none)}"
  echo "---- WEEK MARKERS ----"
  echo "PLACE=#rp${PLACE_WEEK_MARKER}  CLOSE=#rp${CLOSE_WEEK_MARKER}"
  echo "---- LOOP CLOSURE ----"; echo "${LOOP_TXT:-$(i18n retro.weekly.data.none)}"; echo "(${LOOP_DONE}/${LOOP_CNT})"
  echo "---- GOAL CADENCE GATE ----"
  echo "MID_JUDGE_DUE=$MID_DUE  QUARTER_RENEG_DUE=$QTR_DUE"
  echo "${GOAL_BLOCK:-$(i18n retro.weekly.data.none)}"
  echo "---- SPEND (flow spine) ----"
  echo "total=${SPEND_TOTAL} cnt=${SPEND_CNT} prev=${SPEND_PREV}"
  echo "$DAY_SPEND_TSV"
  echo "---- PENDING MARKER (would write to: $MARKER_PATH) ----"
  write_marker dry
  echo "---- BUTTON ----"; echo "${START_LABEL}::opt:${START_LABEL}"
  echo "---- BODY (rendered, verbatim -- this is exactly what push.sh sends) ----"
  echo "$BODY"
  echo "---- STEPS (after the yes, one per turn) ----"
  render weekly | awk 'f || /^---- 1\//{f=1; print}'
  echo "---- WIDTH CHECK (code-block width budget) ----"
  render width
  exit 0
fi

# An empty render sends nothing (never an empty bubble).
if [[ -z "$(printf '%s' "$BODY" | tr -d '[:space:]')" ]]; then
  echo "weekly-review: empty render -- nothing to send" >&2
  exit 0
fi

# Real emit: marker first (the skill can ground an answer immediately), then
# the start card with its one button (same shape as daily-retro.sh).
write_marker real
exec "$PUSH_SH" --text "$BODY" --button "${START_LABEL}::opt:${START_LABEL}"
