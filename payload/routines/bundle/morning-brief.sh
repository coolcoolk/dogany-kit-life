#!/bin/bash
# morning-brief.sh -- kit_life bundle routine: daily morning briefing.
# Data source: local kit_life.db only (via database/kit_life.sh). No external APIs.
#   (1) today's day surface -- rows plus the GAP LINE between them (day-card
#       --plain). Travel is not a row: it is what the gap line explains, and
#       that line also carries the leftover (여유 / 딱 맞습니다 / 모자랍니다).
#   (2) yesterday's diet/workout one-line recap (agg-day)
# day-card takes a local date + the instance's declared AGENT_TZ_OFFSET_HOURS
# and returns FINISHED lines: every minute figure on them is computed in the
# kit, never here and never by the model (2.25.0).
#
# DOMAIN AGENT (2.38.0, brief/retro standard). On an instance whose
# `kit_life.sh scope-self` says role=domain, none of the above runs: the brief
# is a deterministic card -- title + the domain's morning-lane providers
# (service/*/retro-sections.tsv rows declaring `morning`), sent verbatim with
# no model. Silent when no provider has anything today. The main agent keeps
# the whole-day picture; a line a crew member owns never appears on both.
#
# Usage: morning-brief.sh [--dry]   (--dry = print data + prompt, no send)
# Exit:  0 ok / 1 config error
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
LIFE_SH="$AGENT_DIR/database/kit_life.sh"
PUSH_SH="$AGENT_DIR/routines/push.sh"
DRY=0
[[ "${1:-}" == "--dry" ]] && DRY=1

[[ -x "$LIFE_SH" || -f "$LIFE_SH" ]] || { echo "[brief] kit_life.sh not found: $LIFE_SH" >&2; exit 1; }
[[ -f "$PUSH_SH" ]] || { echo "[brief] push.sh not found: $PUSH_SH" >&2; exit 1; }

# ---- portable date math (BSD/macOS -v, GNU/Linux -d) ----
day_shift() { # day_shift <+1|-1> -> YYYY-MM-DD
  if date -v+1d +%F >/dev/null 2>&1; then date -v"${1}d" +%F; else date -d "${1} day" +%F; fi
}
TODAY="$(date +%F)"
YDAY="$(day_shift -1)"
# BRIEF_FAKE_TODAY=YYYY-MM-DD pins the run date (2.38.0; fixtures and dry
# renders on a database copy). Honoured ONLY under --dry: a real send always
# uses the wall clock, so a stray env var can never publish a wrong day.
if [[ -n "${BRIEF_FAKE_TODAY:-}" ]]; then
  if [[ "$DRY" -ne 1 ]]; then
    echo "[brief] FATAL BRIEF_FAKE_TODAY is set outside --dry -- refusing to send" >&2
    exit 1
  fi
  python3 -c "import datetime,sys;datetime.date.fromisoformat(sys.argv[1])" "$BRIEF_FAKE_TODAY" 2>/dev/null \
    || { echo "[brief] FATAL bad BRIEF_FAKE_TODAY (want YYYY-MM-DD)" >&2; exit 1; }
  TODAY="$BRIEF_FAKE_TODAY"
  YDAY="$(python3 -c "import datetime,sys;print(datetime.date.fromisoformat(sys.argv[1])-datetime.timedelta(days=1))" "$TODAY")"
fi
NOW_HM="$(date +%H:%M)"   # actual send time -- keep briefing wording in sync

# ---- title line (date + Korean weekday) ----
# KWEEKDAY = single Korean char for TODAY's weekday. Portable numeric weekday
# (0=Sun..6=Sat via %w): BSD/macOS `date -j -f`, GNU/Linux `date -d`.
_WNUM="$(date -j -f %Y-%m-%d "$TODAY" +%w 2>/dev/null || date -d "$TODAY" +%w)"
_KWEEK=(일 월 화 수 목 금 토)   # index by %w (0=Sun)
KWEEKDAY="${_KWEEK[$_WNUM]}"
TITLE="🌅 ${TODAY} (${KWEEKDAY}) 모닝 브리핑"

# ---- scope (2.38.0): which lines this instance may emit ----
# role / owned domains are derived (kit_life.sh scope-self), never stored.
# An older kit without the verb prints nothing -> main, owns nothing listed,
# which keeps the health lines exactly where they were (OWNS_HEALTH=1 below
# only flips to 0 when another member provably owns health).
SCOPE="$("$LIFE_SH" scope-self 2>/dev/null || true)"
_sget() { printf '%s\n' "$SCOPE" | sed -n "s/^$1=//p" | head -1; }
ROLE="$(_sget role)"; ROLE="${ROLE:-main}"
OWNS="$(_sget owns)"
OTHERS_OWN="$(_sget others_own)"
SKIP_MODULES="$(_sget skip_modules)"
OWNS_HEALTH=1
case ",$OTHERS_OWN," in *,health,*) OWNS_HEALTH=0 ;; esac

# ---- locale (address + tone come from the instance i18n) ----
AGENT_LANG="$(grep -E '^AGENT_LANG=' "$AGENT_DIR/config/agent.conf" 2>/dev/null | head -1 | cut -d= -f2 | tr -d '[:space:]' || true)"
AGENT_LANG="${AGENT_LANG:-en}"
# i18n_get <key> -> value ('' if missing). Resolution order is the one
# kit_life.py._i18n uses, per language (AGENT_LANG, then 'en'), first wins:
#   the framework's layered lookup routines/lib/i18n_lookup.py merged_bundle
#     (DGN-1789 / dec-204): kit layers config/packs/<id>.i18n.<lang>.json in
#     the framework's dependency order, then the framework default
#     config/i18n/<lang>.json. The kit's keys (shared render constants, e.g.
#     the D27 schedule line format) ship in payload/config/i18n/*.bundle.json
#     and reach config/packs/kit_life.i18n.<lang>.json via the installer.
#   OLD FRAMEWORK (no i18n_lookup.py; SUNSET at framework >= 2.6.0 carrying
#     DGN-1789): the kit's installed bundle copy service/kit_life/i18n/<lang>.json
#     over config/i18n/<lang>.json.
# The python helper rather than agentlib i18n(): one call yields the whole
# bundle, and this script does not source agentlib.
i18n_get() {
  AGENT_DIR="$AGENT_DIR" AGENT_LANG="$AGENT_LANG" python3 -c '
import importlib.util, json, os, sys
root, lang, key = os.environ["AGENT_DIR"], os.environ["AGENT_LANG"], sys.argv[1]
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


for lg in (lang, "en"):
    val = bundle(lg).get(key)
    if isinstance(val, str) and val != "":
        print(val)
        break
' "$1" 2>/dev/null || true
}
ADDRESS="$(grep -E '^AGENT_ADDRESS=' "$AGENT_DIR/config/agent.conf" 2>/dev/null | head -1 | cut -d= -f2 | tr -d '[:space:]' || true)"
[[ -z "$ADDRESS" ]] && ADDRESS="$(i18n_get address)"
TONE="$(i18n_get tone_guide)"

# ==== DOMAIN PATH (2.38.0): a deterministic card from the domain's providers ====
# Content is the providers' (the pack knows the domain; the kit gives the
# lane, the composer and the silence rule). Owned-domain kit lines that used
# to ride the main brief come here: the one-time body-measurement reminder
# (goal-diet-nudge) when this instance owns health.
if [[ "$ROLE" == "domain" ]]; then
  export KIT_RETRO_TODAY="$TODAY"
  _ROLE_ARGS=(--role domain)
  [[ -n "$SKIP_MODULES" ]] && _ROLE_ARGS+=(--skip-module "$SKIP_MODULES")
  D_SECTIONS="$("$LIFE_SH" retro-sections --lane morning --mode section "${_ROLE_ARGS[@]}" 2>/dev/null || true)"
  D_NUDGE=""
  if [[ "$OWNS_HEALTH" -eq 1 ]]; then
    if [[ "$DRY" -eq 1 ]]; then
      D_NUDGE="$("$LIFE_SH" goal-diet-nudge --dry 2>/dev/null || true)"
    else
      D_NUDGE="$("$LIFE_SH" goal-diet-nudge 2>/dev/null || true)"
    fi
  fi
  _DOM="${OWNS%%,*}"
  _WDS="$(i18n_get retro.daily.weekdays)"
  _WD="$(printf '%s' "$_WDS" | awk -F, -v n="$((_WNUM + 1))" '{gsub(/^ +| +$/, "", $n); print $n}')"
  _M="$((10#$(date -j -f %Y-%m-%d "$TODAY" +%m 2>/dev/null || date -d "$TODAY" +%m)))"
  _D="$((10#$(date -j -f %Y-%m-%d "$TODAY" +%d 2>/dev/null || date -d "$TODAY" +%d)))"
  _DATE="$(i18n_get retro.daily.date)"; _DATE="${_DATE//\{m\}/$_M}"; _DATE="${_DATE//\{d\}/$_D}"; _DATE="${_DATE//\{wd\}/$_WD}"
  D_TITLE="$(i18n_get brief.domain.title)"
  D_TITLE="${D_TITLE//\{icon\}/$(i18n_get "domain.icon.${_DOM:-x}")}"
  D_TITLE="${D_TITLE//\{label\}/$(i18n_get "domain.label.${_DOM:-x}")}"
  D_TITLE="${D_TITLE//\{date\}/$_DATE}"
  D_BODY=""
  [[ -n "$D_SECTIONS" ]] && D_BODY="$D_SECTIONS"
  [[ -n "$D_NUDGE" ]] && D_BODY="${D_BODY:+$D_BODY$'\n\n'}$D_NUDGE"
  if [[ "$DRY" -eq 1 ]]; then
    echo "---- SCOPE ----"; echo "role=$ROLE owns=$OWNS"
    if [[ -z "$D_BODY" ]]; then
      echo "---- BODY ----"; echo "(silent: no morning provider has anything today)"
      exit 0
    fi
    echo "---- BODY (verbatim, push.sh --text) ----"
    printf '%s\n\n%s\n' "$D_TITLE" "$D_BODY"
    exit 0
  fi
  if [[ -z "$D_BODY" ]]; then
    echo "[brief] domain: no morning provider has anything today -- silent"
    exit 0
  fi
  exec "$PUSH_SH" --text "$D_TITLE"$'\n\n'"$D_BODY"
fi

# ---- timezone offset (integer hours, e.g. 9 for UTC+9) ----
# Resolved through the framework helper (routines/lib/tz_offset.sh, DGN-1704):
# per-agent AGENT_TZ_OFFSET_HOURS override, then crew value, then host. Without
# the helper (older framework) or on a failed/non-integer result, fall back to
# the per-agent value, else 0 -- the pre-2.29 behaviour.
TZ_OFFSET_H=""
TZ_LIB="$AGENT_DIR/routines/lib/tz_offset.sh"
if [[ -f "$TZ_LIB" ]]; then
  TZ_OFFSET_H="$(bash "$TZ_LIB" resolve "$AGENT_DIR" 2>/dev/null || true)"
  [[ "$TZ_OFFSET_H" =~ ^-?[0-9]+$ ]] || TZ_OFFSET_H=""
fi
if [[ -z "$TZ_OFFSET_H" ]]; then
  TZ_OFFSET_H="$(grep -E '^AGENT_TZ_OFFSET_HOURS=' "$AGENT_DIR/config/agent.conf" 2>/dev/null | head -1 | cut -d= -f2 | tr -d '[:space:]' || true)"
  TZ_OFFSET_H="${TZ_OFFSET_H:-0}"
fi

# ---- schedule title-prefix exclusion (config-driven, default off) ----
# Comma-separated list of title prefixes; a schedule event whose title (after
# stripping leading whitespace) starts with any listed prefix is omitted from
# the brief. Empty/missing = no filtering. Passed to python via env so config
# values never appear as code literals. Surrounding whitespace of the raw value
# is trimmed here, then a MATCHED surrounding quote pair; per-item trim + split
# happen inside the python block.
# Trailing `|| true`: key is optional (default off); a no-match grep under
# `set -euo pipefail` would otherwise abort the whole brief.
# Quote-strip (2026-09-23): the framework reader SOURCES agent.conf, so a
# space-bearing value must be quoted there; this reader takes the raw text after
# `=`, where an unstripped quote joins the value and kills prefix matching
# outright. Stripping makes quoted and unquoted values read identically, which
# is what lets the conf value be quoted afterwards. Only a matched pair at both
# ends is removed. Same idiom as day-card.sh conf() / daily-retro.sh.
EXCLUDE_PREFIXES="$(grep -E '^BRIEF_EXCLUDE_TITLE_PREFIXES=' "$AGENT_DIR/config/agent.conf" 2>/dev/null | head -1 | cut -d= -f2- | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'\$/\1/" || true)"

# ---- yesterday diet/workout recap gate (config-driven, DEFAULT on) ----
# BRIEF_DIET_RECAP=off suppresses the "# yesterday recap" block entirely. Used
# on instances where the diet/workout domain was transferred to another agent,
# so local agg-day always returns 0 and the recap would be a false "0" line.
# Unset/empty -> on (instances that still own diet locally are unchanged).
BRIEF_DIET_RECAP="$(grep -E '^BRIEF_DIET_RECAP=' "$AGENT_DIR/config/agent.conf" 2>/dev/null | head -1 | cut -d= -f2 | tr -d '[:space:]' || true)"
# 2.38.0: the unset default follows the owner of the health domain -- off when
# another crew member owns it (that member's own brief carries diet), on
# otherwise. An explicit value still wins.
if [[ -z "$BRIEF_DIET_RECAP" && "$OWNS_HEALTH" -eq 0 ]]; then BRIEF_DIET_RECAP=off; fi
BRIEF_DIET_RECAP="${BRIEF_DIET_RECAP:-on}"

# ---- weather image card gate (config-driven, DEFAULT off) ----
# BRIEF_WEATHER_CARD=on renders the weather+air-quality+quote PNG card and
# sends it as a photo after the text brief, suppressing the text weather block.
# Unset/empty -> off (instances keep the text weather line unless they opt in).
BRIEF_WEATHER_CARD="$(grep -E '^BRIEF_WEATHER_CARD=' "$AGENT_DIR/config/agent.conf" 2>/dev/null | head -1 | cut -d= -f2 | tr -d '[:space:]' || true)"
BRIEF_WEATHER_CARD="${BRIEF_WEATHER_CARD:-off}"

# ---- weather (Open-Meteo, no API key) ----
# Both coords must be set; any error -> WEATHER_TXT="" and brief continues normally.
AGENT_LAT="$(grep -E '^AGENT_LAT=' "$AGENT_DIR/config/agent.conf" 2>/dev/null | head -1 | cut -d= -f2 | tr -d '[:space:]' || true)"
AGENT_LNG="$(grep -E '^AGENT_LNG=' "$AGENT_DIR/config/agent.conf" 2>/dev/null | head -1 | cut -d= -f2 | tr -d '[:space:]' || true)"

# ---- weather card (image, config-gated) ----
# When BRIEF_WEATHER_CARD=on, render the PNG card up front. CARD_PATH set +
# CARD_OK=1 on success; on any failure CARD_OK stays 0 and we fall back to the
# text weather path below (never lose weather silently). The card is SENT later
# in the tail (after the text brief), and only if not --dry.
# Interpreter: matplotlib is not in system python3. Resolve a render venv the
# same way the diet-status card does (RENDER_PYTHON env -> ~/dogany/.venvs/render
# -> PATH python3 only if matplotlib present).
CARD_OK=0
CARD_PATH=""
CARD_SH="$AGENT_DIR/routines/bundle/morning_brief_card.py"
if [[ "$BRIEF_WEATHER_CARD" == "on" ]]; then
  RENDER_PY="${RENDER_PYTHON:-}"
  [[ -z "$RENDER_PY" && -x "$HOME/dogany/.venvs/render/bin/python" ]] && RENDER_PY="$HOME/dogany/.venvs/render/bin/python"
  [[ -z "$RENDER_PY" ]] && command -v python3 >/dev/null && python3 -c 'import matplotlib' 2>/dev/null && RENDER_PY="$(command -v python3)"
  [[ -z "$RENDER_PY" ]] && RENDER_PY="python3"   # last resort (script exits non-zero if matplotlib missing)
  CARD_LAT="${AGENT_LAT:-37.5665}"; CARD_LNG="${AGENT_LNG:-126.9780}"
  # mktemp -d gives a clean dir so the output is a single .png with no orphan
  # base file (appending .png to a mktemp file would leave the base behind).
  CARD_TMPDIR="$(mktemp -d -t morning_brief_card)"
  CARD_PATH="$CARD_TMPDIR/card.png"
  if AGENT_ROOT="$AGENT_DIR" "$RENDER_PY" "$CARD_SH" --output "$CARD_PATH" --lat "$CARD_LAT" --lon "$CARD_LNG" >/dev/null 2>&1 \
     && [[ -s "$CARD_PATH" ]]; then
    CARD_OK=1
  else
    echo "[brief] weather card generation failed; falling back to text weather" >&2
    rm -rf "$CARD_TMPDIR" 2>/dev/null || true
    CARD_PATH=""
  fi
fi

# Text weather is suppressed only when the card is on AND actually generated.
# Otherwise (card off, or card failed) we build the text weather as before.
# Retry + cache fallback mirrors the card path: 3 attempts, then last-good cache.
# Cache file: runtime/weather_cache.json (shared with morning_brief_card.py).
WEATHER_TXT=""
WEATHER_CACHE="$AGENT_DIR/runtime/weather_cache.json"
if [[ "$CARD_OK" -ne 1 && -n "$AGENT_LAT" && -n "$AGENT_LNG" ]]; then
  WEATHER_TXT="$(python3 -c "
import sys, json, time
from datetime import datetime, timezone, timedelta
from urllib.request import urlopen

lat = '$AGENT_LAT'
lng = '$AGENT_LNG'
tz_off = int('$TZ_OFFSET_H')
cache_path = '$WEATHER_CACHE'

url = (
    'https://api.open-meteo.com/v1/forecast'
    '?latitude=' + lat +
    '&longitude=' + lng +
    '&hourly=precipitation_probability,temperature_2m'
    '&timezone=auto&forecast_days=1'
)

# --- retry loop (3 attempts, 2s / 4s backoff) ---
raw = None
backoffs = [0, 2, 4]
for attempt in range(3):
    if backoffs[attempt]:
        time.sleep(backoffs[attempt])
    try:
        with urlopen(url, timeout=8) as r:
            if r.status == 200:
                raw = json.loads(r.read())
                break
    except Exception as e:
        sys.stderr.write('[brief] text weather attempt %d failed: %s\n' % (attempt + 1, e))

# --- format from raw API payload ---
def fmt_from_raw(data, cached_at=None):
    times = data.get('hourly', {}).get('time', [])
    precip = data.get('hourly', {}).get('precipitation_probability', [])
    temps  = data.get('hourly', {}).get('temperature_2m', [])
    valid_temps = [t for t in temps if t is not None]
    if not valid_temps:
        return ''
    t_max = max(valid_temps)
    t_min = min(valid_temps)
    lines = ['최고 ' + str(round(t_max)) + 'C / 최저 ' + str(round(t_min)) + 'C']
    if cached_at:
        try:
            dt_c = datetime.strptime(cached_at, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
            # cached_at is stored UTC; display in the instance's configured local
            # offset (AGENT_TZ_OFFSET_HOURS), never a hardcoded region offset.
            dt_local = dt_c + timedelta(hours=tz_off)
            lines.append('(날씨 조회불가, 마지막 기록: ' + dt_local.strftime('%m/%d %H:%M') + ')')
        except Exception:
            lines.append('(날씨 조회불가)')
    now_local = datetime.now(tz=timezone(timedelta(hours=tz_off)))
    key_hours = [h for h in [9, 12, 15, 18, 21] if h >= now_local.hour]
    for i, ts in enumerate(times):
        dt = datetime.strptime(ts, '%Y-%m-%dT%H:%M')
        if dt.hour in key_hours:
            p = precip[i] if i < len(precip) and precip[i] is not None else 0
            t = temps[i]  if i < len(temps)  and temps[i]  is not None else 0
            lines.append('- ' + str(dt.hour).zfill(2) + '시 ' + str(round(p)) + '% ' + str(round(t)) + 'C')
    return '\n'.join(lines)

if raw is not None:
    # Live fetch succeeded -- cache the weather payload (text path subset).
    try:
        import os
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        # Freshness wins: always overwrite. NOTE the text path fetches a NARROWER
        # payload than the card path (no daily min/max, no weather_code), so this
        # write can downgrade a card-shape cache entry to a text-shape one. The
        # card's cache fallback validates the shape before parsing and degrades
        # gracefully if it finds a text-shape entry -- see morning_brief_card.py.
        tmp = cache_path + '.tmp'
        # Read existing cache to preserve air_quality if card already wrote it.
        existing = {}
        try:
            with open(cache_path) as cf:
                existing = json.load(cf)
        except Exception:
            pass
        import datetime as _dt
        payload = {
            'cached_at': _dt.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
            'weather': raw,
            'air_quality': existing.get('air_quality', {}),
        }
        with open(tmp, 'w') as fh:
            json.dump(payload, fh)
        os.replace(tmp, cache_path)
    except Exception as ce:
        sys.stderr.write('[brief] text weather cache write failed: %s\n' % ce)
    print(fmt_from_raw(raw))
else:
    # All retries failed -- fall back to last-good cache.
    sys.stderr.write('[brief] text weather all retries failed; trying cache\n')
    try:
        with open(cache_path) as cf:
            cached = json.load(cf)
        cached_at = cached.get('cached_at', '')
        w_raw = cached.get('weather', {})
        if not w_raw:
            raise ValueError('empty weather in cache')
        print(fmt_from_raw(w_raw, cached_at))
    except FileNotFoundError:
        sys.stderr.write('[brief] no weather cache; emitting unavailable notice\n')
        print('날씨 일시 조회불가')
    except Exception as e2:
        sys.stderr.write('[brief] cache read error: %s\n' % e2)
        print('날씨 일시 조회불가')
" 2>/dev/null || true)"
fi

# ---- (1) today's day surface: rows + the GAP LINES between them ----------
# 2.25.0. Travel is no longer a schedule row of its own: it is the
# explanation of the gap between two rows, and the gap line carries BOTH the
# minutes the gap spends (이동 / 외출준비 / 준비) and the minutes it has left
# (여유 N분 / 딱 맞습니다 / N분 모자랍니다).
#
# EVERY NUMBER ON THOSE LINES IS COMPUTED IN THE KIT, NOT HERE AND NOT BY THE
# MODEL. `day-card --plain` returns the finished lines; this script does not
# format them and the prompt note below may only say "print verbatim". Slack
# is clock arithmetic, and a model doing clock arithmetic puts a wrong number
# on the owner's screen -- that is the exact failure class this estate spent
# 2026-09-22 repairing, so the render was moved behind a deterministic verb.
#
# Window + tz: --date TODAY with the instance's DECLARED offset, which
# reproduces the [today 00:00 local, tomorrow 00:00 local) window this block
# used to compute inline. The title-prefix exclusion knob still applies (it
# is a per-instance display choice, unrelated to the travel-row change).
# `|| true`: the brief must never die on the schedule lane.
SCHED_EXCL=()
[[ -n "$EXCLUDE_PREFIXES" ]] && SCHED_EXCL=(--exclude-prefix "$EXCLUDE_PREFIXES")
SCHED_TXT="$("$LIFE_SH" day-card --plain --date "$TODAY" \
  --tz-offset "$TZ_OFFSET_H" "${SCHED_EXCL[@]+"${SCHED_EXCL[@]}"}" 2>/dev/null || true)"
# Count ROWS, not lines: a gap line is an annotation on the space between two
# rows, so counting it would report "3 items" for a two-appointment day and
# the header number would stop meaning anything.
# The row prefix is the QUOTE marker plus the bullet ('> - ', owner ruling
# 2026-09-22 12:41): rows are quoted, gap lines are not, so this predicate also
# happens to be the row/gap discriminator. Matching bare '^- ' silently counted
# zero the moment quoting landed, which emptied the whole schedule section.
SCHED_CNT=0; [[ -n "$SCHED_TXT" ]] && SCHED_CNT="$(printf '%s\n' "$SCHED_TXT" | grep -c '^> - ' || true)"

# Cadence leg 1 of 3: the pinned day card is BUILT here, at brief time, from
# the same verb, so the message that scrolls away and the pin that stays can
# never disagree about the same day. Legs 2 and 3 (after a schedule write; on
# a waking-hours timer) live in kit_life.py and the day-card routine.
DAY_CARD_SH="$AGENT_DIR/routines/bundle/day-card.sh"
# ONE schedule surface per morning (owner ruling 2026-09-26 06:53): the day
# card IS the brief's schedule message and the text brief must not repeat it.
#
# THE CARD GOES OUT AS A NEW MESSAGE (owner ruling 2026-09-27). The pin alone
# was not enough: the bridge edits the pinned message IN PLACE and silently,
# so from the second day on the brief said the schedule was in a card and
# nothing new appeared in the chat.
#
# ONE CARD, AND IT IS THE PIN (owner option 2, 2026-09-28; framework DGN-1768
# r2). The 2026-09-27 fix pushed the card text as a separate message while the
# framework's r1 day rotation ALSO sent the board as a new pinned message, so
# at 05:00 the chat got two cards back to back (2026-09-30: an unpinned
# pushed copy, then the rotated pin), and the frozen copy went on disagreeing
# with the live pin as soon as the day changed (회사 17:30 -> 19:00). Now the
# brief asks for the rotation itself: `day-card.sh --fresh` pins the card as a
# NEW silent message, deletes yesterday's, and waits for the bridge to confirm
# it (status-footer.py --pin-set ... --fresh --wait). Order holds because of
# the wait: day card (pinned) -> text brief -> weather photo. The instance sets
# DASHBOARD_ROTATE=explicit so nothing else rotates the day first.
#
# CARD FIRST, ALSO ON A DAY WITH NO ROWS (owner ruling 2026-10-04 11:32).
# Since 2026-10-02 the pin carries open tasks and the week too, so a day with
# nothing in today's window still has a card. The no-rows branch used to
# refresh it in place and move on; the bridge's own day rotation then put the
# new pinned card in the chat AFTER the text brief (2026-10-04, a Sunday).
# The fresh pin + wait now runs whenever there is a card, so every morning is
# day card (pinned) -> text brief -> weather photo. An empty card is cleared
# by the same call (day-card.sh --fresh exit 2, nothing pinned).
#
# DAY_CARD_UP=1 when the card is actually up as a new message:
#   day-card.sh --fresh exit 0  -> the bridge confirmed the new pinned card;
#   exit 3 (no pin surface / framework without --fresh) -> FALLBACK to the
#          2026-09-27 path on a day with rows: push the --print text verbatim
#          (--text, no model), DAY_CARD_UP=1 on a successful send. With no
#          rows there is no card push (unchanged); the pin was refreshed in
#          place by day-card.sh;
#   anything else (timeout, pin-set failure, empty render) -> 0.
# On --dry: would be sent. DAY_CARD_SENT (what the text brief reads) is
# DAY_CARD_UP on a day with rows; every 0 case keeps the schedule inside the
# text brief, so the day is never shown zero times. The text is always the
# one day-card render (same verb, same --exclude-prefix, same tz offset).
DAY_CARD_SENT=0
DAY_CARD_UP=0
DAY_CARD_MSG=""
if [[ -f "$DAY_CARD_SH" ]]; then
  if [[ "$DRY" -eq 1 ]]; then
    # --dry: render only, pin nothing, send nothing.
    DAY_CARD_MSG="$(bash "$DAY_CARD_SH" --dry --print --date "$TODAY" 2>/dev/null || true)"
    [[ -n "$DAY_CARD_MSG" ]] && DAY_CARD_UP=1
  else
    DAY_CARD_RC=0
    DAY_CARD_MSG="$(bash "$DAY_CARD_SH" --print --fresh --date "$TODAY")" || DAY_CARD_RC=$?
    if [[ -z "$DAY_CARD_MSG" ]]; then
      if [[ "$SCHED_CNT" -gt 0 ]]; then
        echo "[brief] day card rendered empty (schedule stays in the text brief)" >&2
      fi
    elif [[ "$DAY_CARD_RC" -eq 0 ]]; then
      DAY_CARD_UP=1
      echo "[brief] day card up as a fresh pinned message"
    elif [[ "$DAY_CARD_RC" -eq 3 ]]; then
      if [[ "$SCHED_CNT" -eq 0 ]]; then
        echo "[brief] day card refreshed in place (no fresh pin on this instance; no rows today, no card message)"
      elif "$PUSH_SH" --text "$DAY_CARD_MSG" >/dev/null; then
        DAY_CARD_UP=1
        echo "[brief] day card pushed as a message (fallback: no fresh pin on this instance)"
      else
        echo "[brief] day card message send failed (schedule stays in the text brief)" >&2
      fi
    else
      echo "[brief] fresh day card pin not confirmed (rc=${DAY_CARD_RC}; schedule stays in the text brief)" >&2
    fi
  fi
fi
[[ "$DAY_CARD_UP" -eq 1 && "$SCHED_CNT" -gt 0 ]] && DAY_CARD_SENT=1

# ---- (1b) unhandled fragments (ag-life-integration R1, config-driven DEFAULT on) ----
# Open untimed/floating task fragments -- the ones the mirror routes to Google
# Tasks (a surface the user does not actually watch), so they silently die as
# 'abandoned'. Surface them proactively in the morning as the "다음 액션" nudge:
# stray to-dos the user can knock out or drop today. BRIEF_UNHANDLED_FRAGMENTS=off
# suppresses the block. Unset/empty -> on. Health-independent.
BRIEF_UNHANDLED_FRAGMENTS="$(grep -E '^BRIEF_UNHANDLED_FRAGMENTS=' "$AGENT_DIR/config/agent.conf" 2>/dev/null | head -1 | cut -d= -f2 | tr -d '[:space:]' || true)"
BRIEF_UNHANDLED_FRAGMENTS="${BRIEF_UNHANDLED_FRAGMENTS:-on}"
FRAG_SHOW=5
FRAG_TXT=""; FRAG_CNT=0
if [[ "$BRIEF_UNHANDLED_FRAGMENTS" == "on" ]]; then
  FRAG_ROWS="$("$LIFE_SH" task-untimed-open --suggest 2>/dev/null || true)"
  FRAG_CNT="$(printf '%s' "$FRAG_ROWS" | grep -c . || true)"
  if [[ "$FRAG_CNT" -gt 0 ]]; then
    # cols: id  title  updated_local  suggest_project. Render title + optional
    # "-> 프로젝트?" auto-suggest tail. Show up to FRAG_SHOW, rest as "외 N건".
    FRAG_TXT="$(printf '%s\n' "$FRAG_ROWS" | head -"$FRAG_SHOW" | awk -F'\t' '{
      title=$2; sug=$4;
      line="- " title;
      if (sug != "") line=line "  -> " sug "?";
      print line }' || true)"
    _frest=$(( FRAG_CNT - FRAG_SHOW ))
    if [[ "$_frest" -gt 0 ]]; then
      FRAG_TXT="${FRAG_TXT}"$'\n'"  외 ${_frest}건"
    fi
  fi
fi

# ---- (1c) appointments with no travel time (2.20.0) ----
# The gap nobody heard. A place-bearing appointment whose venue never became
# a place row derives no 이동/준비 block, and every surface that knew was
# either a write verb nobody schedules (travel-sweep) or a per-cycle log
# written NOT to say it (mirror travel_attach_step). Measured 2026-09-12: the
# owner found out the day before his friend's wedding, by asking.
#
# So it rides the one surface that reaches him before the day arrives. Read
# only -- place-gap mints nothing and stamps nothing, so a brief can call it
# as often as it likes. Horizon is deliberately SHORT (default 7 days): a gap
# four weeks out is not today's business and a daily list of them is a list
# he stops reading. BRIEF_PLACE_GAPS=off suppresses it; unset -> on.
BRIEF_PLACE_GAPS="$(grep -E '^BRIEF_PLACE_GAPS=' "$AGENT_DIR/config/agent.conf" 2>/dev/null | head -1 | cut -d= -f2 | tr -d '[:space:]' || true)"
BRIEF_PLACE_GAPS="${BRIEF_PLACE_GAPS:-on}"
GAP_DAYS="$(grep -E '^BRIEF_PLACE_GAP_DAYS=' "$AGENT_DIR/config/agent.conf" 2>/dev/null | head -1 | cut -d= -f2 | tr -d '[:space:]' || true)"
GAP_DAYS="${GAP_DAYS:-7}"
GAP_TXT=""; GAP_CNT=0
if [[ "$BRIEF_PLACE_GAPS" == "on" ]]; then
  if date -v+1d +%F >/dev/null 2>&1; then
    _GAP_TO="$(date -v"+${GAP_DAYS}d" +%F)"
  else
    _GAP_TO="$(date -d "+${GAP_DAYS} day" +%F)"
  fi
  GAP_ROWS="$("$LIFE_SH" place-gap "$TODAY" "$_GAP_TO" 2>/dev/null | grep '^place-gap	' || true)"
  GAP_CNT="$(printf '%s' "$GAP_ROWS" | grep -c . || true)"
  if [[ "$GAP_CNT" -gt 0 ]]; then
    # cols: place-gap  id  reason  local_start  title  location  action
    # Rendered as "MM-DD HH:MM 제목 | 할 일" -- the row IS the sentence, so
    # the model has nothing to paraphrase and no number to round.
    GAP_TXT="$(printf '%s\n' "$GAP_ROWS" | awk -F'\t' '{
      d=substr($4,6,5); t=substr($4,12,5);
      print "- " d " " t " " $5 " | " $7 }' || true)"
  fi
fi

# ---- (2) yesterday recap (diet/workout, from agg-day KEY=VALUE) ----
# Gated by BRIEF_DIET_RECAP (default on). When off, skip agg-day entirely so a
# transferred diet domain (local agg-day = 0) does not leak a false "0" recap.
# DGN-669 finality: agg-day appends as_of= / last_entry_at= / closed=.
# closed missing (old kit_life) -> default 1 = today's behavior, never a
# spurious provisional marker.
Y_KCAL=0; Y_MEALS=0; Y_WO=0; Y_BURN=0; Y_CLOSED=1; Y_ASOF=""
if [[ "$BRIEF_DIET_RECAP" == "on" ]]; then
  AGG="$("$LIFE_SH" agg-day "$YDAY" 2>/dev/null || true)"
  ag() { printf '%s\n' "$AGG" | grep "^$1=" | head -1 | cut -d= -f2-; }
  Y_KCAL="$(ag intake_kcal)"; Y_KCAL="${Y_KCAL:-0}"
  Y_MEALS="$(ag meal_cnt)";   Y_MEALS="${Y_MEALS:-0}"
  Y_WO="$(ag workout_cnt)";   Y_WO="${Y_WO:-0}"
  Y_BURN="$(ag burn_kcal)";   Y_BURN="${Y_BURN:-0}"
  # `|| true`: keys absent on an old kit_life -> grep exits 1; without the guard
  # set -e would kill the whole brief in a mixed-version deploy.
  Y_CLOSED="$(ag closed || true)"; Y_CLOSED="${Y_CLOSED:-1}"
  Y_ASOF="$(ag as_of || true)"
fi

# ---- (2b) calorie carry-over: today's aiming point, and WHY it moved ----
# The touchpoint pair (diet module v0.1): the BRIEF carries the WHY -- one line,
# only when the number actually moved -- and the diet card carries the RESULT.
# Neither is a new surface: both were already in front of the user every day.
#
# The line is produced by the diet skill's carryover.py, which fails CLOSED on
# every missing input (no week anchor, no calorie rule, an incomplete rule
# detail) and prints nothing when there is no carry. So the default outcome of
# this block is silence, and a kit with no calorie rule is unchanged.
#
# NOT gated on BRIEF_DIET_RECAP, and that is deliberate. Measured on the live
# install 2026-09-10: BRIEF_DIET_RECAP=off on the agent that actually sends the
# brief, so reusing that knob would have shipped a feature that can never fire.
# The two are also different questions -- BRIEF_DIET_RECAP asks "does this agent
# narrate yesterday's diet", a domain-attribution switch, while this line is the
# forward-looking target the user asked for by name. The opt-in here is DATA,
# not a knob: carryover.py speaks only when an active calorie rule with a
# derivable weekly band exists, i.e. only when the user has declared a calorie
# plan. Declaring the plan IS the switch, so there is nothing extra to turn on
# and nothing to leave stale.
# carryover.py ships in pack_health's diet-status skill (the coaching half of
# the 2026-09-09 capture/coaching split), NOT in this kit. The -f probe below
# is therefore the kit-alone case as well as the not-yet-rendered one: no pack,
# no file, no line -- and the brief is unchanged, which is the intended shape.
# 2.38.0: a health line -- only on the instance that owns health (scope-self).
CARRY_LINE=""
CARRY_PY="$AGENT_DIR/.claude/skills/diet-status/carryover.py"
if [[ -f "$CARRY_PY" && "$OWNS_HEALTH" -eq 1 ]]; then
  # `|| true` + 2>/dev/null: the brief must never die on the diet lane.
  CARRY_LINE="$(python3 "$CARRY_PY" 2>/dev/null | grep '^why_line=' | head -1 | cut -d= -f2- || true)"
fi

# ---- (3) (removed 2.38.0) the health agent's morning section ----
# The brief used to read a health section from the handoff inbox. Nothing has
# written one since the health agent started sending its own messages, and
# the domain agent now sends its own morning card (domain path above), so the
# read and its prompt notes are gone.

# ---- (4) contact-gap suggestions (person-session alert-pick) ----
# alert-pick owns all gating (weekly cap, snooze, fade, checkin) and logs
# 'shown' as a side effect -- the morning brief IS the weekly surface. We only
# render its PICK lines. PICK carries meeting-based fields:
#   PICK <pid> <name> <gapdays> <ratio> <hint> <meet_days> <meet_ctx>
# We render each as `name | meet_ctx | meet_days일전`. Persons with an upcoming
# appointment are already dropped inside alert-pick. CAP_REACHED/NONE -> empty.
REL_SH="$AGENT_DIR/database/relmod.sh"
CONTACT_TXT=""
if [[ -f "$REL_SH" ]]; then
  CONTACT_TXT="$("$REL_SH" alert-pick 2>/dev/null | python3 -c "
import sys
rows = []
for line in sys.stdin:
    cols = line.rstrip('\n').split('\t')
    if not cols or cols[0] != 'PICK':
        continue
    # PICK pid name gapdays ratio hint meet_days meet_ctx
    name = cols[2] if len(cols) > 2 else ''
    meet_days = cols[6] if len(cols) > 6 else ''
    meet_ctx  = cols[7] if len(cols) > 7 else ''
    ctx = meet_ctx if meet_ctx else '-'
    when = (meet_days + '일전') if meet_days != '' else '-'
    rows.append(name + ' | ' + ctx + ' | ' + when)
print('\n'.join(rows))
" || true)"
fi

DATA="[today ${TODAY}]"$'\n\n'
DATA+="# title"$'\n'"${TITLE}"$'\n\n'
# Section header: a shared i18n constant (the retro's tomorrow block reads
# the same family, schedule.header.tomorrow).
SCHED_HEADER="$(i18n_get schedule.header.today)"
SCHED_HEADER="${SCHED_HEADER:-📅 오늘 일정}"

DATA+="# today's schedule: ${SCHED_CNT}"$'\n'
if [[ "$DAY_CARD_SENT" -eq 1 ]]; then DATA+="(already sent as the day card message just above this one -- not part of this message)"$'\n'
elif [[ "$SCHED_CNT" -gt 0 ]]; then DATA+="${SCHED_TXT}"$'\n'; else DATA+="(none)"$'\n'; fi
# The schedule block arrives ALREADY RENDERED (day-card --plain). This note is
# a verbatim-render device, the same shape RECAP_NOTE / CONTACT_NOTE use: it
# states WHERE the lines go and FORBIDS arithmetic. It deliberately contains
# no rule for deriving any number -- every figure was computed in the kit
# before the prompt was built, and a computation rule in prose is an
# invitation for the model to recompute and disagree.
SCHED_NOTE=""
SCHED_STRUCT="today's schedule under the ${SCHED_HEADER} header, ONLY if schedule items are present; if there are none, OMIT the schedule section entirely -- no line and no filler commentary (no 'open day' / '여유로운 하루' / etc.)"
SCHED_ICON="schedule section header line is EXACTLY ${SCHED_HEADER}; every line of the schedule data -- the '> - ' quoted rows (leading '> ' kept) AND the indented '  ↓ ' lines between them (never quoted) -- is reproduced VERBATIM, in the given order, one per line (see the schedule note below)"
if [[ "$DAY_CARD_SENT" -eq 0 && "$SCHED_CNT" -gt 0 ]]; then
  SCHED_NOTE="The '# today's schedule' block is ALREADY RENDERED. Reproduce EVERY line of it VERBATIM, in the given order, one per line, under the ${SCHED_HEADER} header -- both the '> - ' schedule rows (including their ' · 장소 · 사람' tail) and the indented '  ↓ ' lines between them. EVERY schedule row arrives with a LEADING '> ' quote marker and it is part of the line: keep it exactly, on every row. Rows are quoted and gap lines are not -- that contrast IS the layout, so a row rendered without its '> ', or a gap line given one, is a broken render. Do NOT substitute a bullet ('•', '-') for the '> '. A '  ↓ ' line is a gap line: it already says what the space between the two rows around it is spent on and what is left of it. Keep its two leading spaces and its arrow. Do NOT compute, recompute, verify, adjust, round, convert, total or comment on any number, duration or clock time on any of these lines; do NOT add, drop, merge, split, reorder or re-word a line; do NOT turn a line into a sentence. Every number there was computed before you saw it -- print it, do not do arithmetic."
elif [[ "$DAY_CARD_SENT" -eq 1 ]]; then
  SCHED_STRUCT="NO schedule section: today's schedule (${SCHED_CNT} item(s)) was ALREADY SENT as the day card message just above this one"
  SCHED_ICON="no ${SCHED_HEADER} header in this message"
  SCHED_NOTE="Today's schedule was ALREADY SENT as the day card message just above this brief; it is not part of this message. Do NOT write a schedule section, do NOT list, restate or summarize any schedule item, time or travel gap. If you refer to the schedule at all, point back to the card above (e.g. '오늘 일정은 위 카드에 있어요'); NEVER promise to send it later or say it will come separately -- it has already arrived. The day is NOT empty (${SCHED_CNT} item(s)) -- never call it free or open."
fi
# Unhandled-fragments block: only when there are open fragments.
FRAG_NOTE=""
if [[ "$FRAG_CNT" -gt 0 ]]; then
  DATA+=$'\n'"# unhandled fragments (${FRAG_CNT})"$'\n'
  DATA+="${FRAG_TXT}"$'\n'
  FRAG_NOTE="An unhandled-fragments block is included in the data below under '# unhandled fragments'. These are stray to-dos that never got a time and would otherwise stay invisible to the user. Render them under a header line that reads EXACTLY '🧩 안 챙긴 조각': list each item on its own line (verbatim title), terse. If an item carries a '-> 프로젝트명?' tail, that is an UNCONFIRMED auto-suggested project link -- you may gently ask if it belongs there, but never state the link as fact. Frame it as a light nudge the user can act on today; no guilt/urgency wording. Omit the section entirely if no fragment data is present."
fi
# Travel-gap block: only when there is a gap. An empty section every morning
# is how a warning surface becomes wallpaper.
GAP_NOTE=""
if [[ "$GAP_CNT" -gt 0 ]]; then
  DATA+=$'\n'"# appointments with no travel time (${GAP_CNT})"$'\n'
  DATA+="${GAP_TXT}"$'\n'
  GAP_NOTE="A travel-gap block is included in the data below under '# appointments with no travel time'. These are coming appointments the system could NOT derive a 이동/준비 block for, because the venue never resolved to a place on file. Render them under a header line that reads EXACTLY '🚕 이동 시간 못 잡은 일정': each data row VERBATIM on its own line, unchanged. Do NOT rewrite a row into a sentence, do NOT merge rows, do NOT add or drop the ' | ' tail (that tail is the one thing the user has to DO), and do NOT state or estimate any travel duration -- the whole point is that none is known. State it as a fact he can act on, not as an error or an apology. Omit the section entirely if no such data is present."
fi
# Yesterday recap block only when BRIEF_DIET_RECAP=on (else omit entirely).
# DGN-669 MAJOR2 forcing point: the provisional marker is attached HERE, where
# the number is formatted; the prompt note (RECAP_NOTE, CONTACT_NOTE-style
# verbatim-render device) then forces the LLM to carry the line unchanged so
# the marker cannot be rewritten away.
RECAP_NOTE=""
if [[ "$BRIEF_DIET_RECAP" == "on" ]]; then
  DATA+=$'\n'"# yesterday recap"$'\n'
  RECAP_LINE="meals logged: ${Y_MEALS} (intake ${Y_KCAL} kcal), workouts: ${Y_WO} (burned ${Y_BURN} kcal)"
  if [[ "$Y_CLOSED" == "0" ]]; then
    # as_of is 'YYYY-MM-DD HH:MM:SS' -> HH:MM; empty as_of degrades to bare 잠정.
    _ASOF_HM="${Y_ASOF:11:5}"
    if [[ -n "$_ASOF_HM" ]]; then
      RECAP_LINE+=" (잠정, ${_ASOF_HM} 기준)"
    else
      RECAP_LINE+=" (잠정)"
    fi
    RECAP_NOTE="The yesterday-recap data line is PROVISIONAL: the day is not closed yet and backfill entries may still change these numbers. Render the '# yesterday recap' data line VERBATIM as the recap line, unchanged, INCLUDING the trailing '(잠정, ...)' marker. Do NOT rewrite it into a sentence, do NOT drop, translate, or move the marker, and do NOT present these numbers as final anywhere in the brief."
  elif [[ "$Y_MEALS" == "0" ]]; then
    # DGN-669 MINOR5: closed day with zero meals = NO RECORDS, not a fast.
    RECAP_NOTE="The yesterday-recap shows zero meals logged. Treat this strictly as 'no records for yesterday', NOT as a confirmed fast or a confirmed 0 kcal intake -- never state that the user ate nothing."
  fi
  DATA+="${RECAP_LINE}"$'\n'
fi

CARRY_NOTE=""
if [[ -n "$CARRY_LINE" ]]; then
  DATA+=$'\n'"# kcal carry-over"$'\n'
  DATA+="${CARRY_LINE}"$'\n'
  CARRY_NOTE="A '# kcal carry-over' line is included in the data below. Render it VERBATIM, unchanged, on its own line inside the diet/health part of the briefing. Do NOT rewrite it into your own sentence, do NOT round or restate the numbers, and do NOT add encouragement, warning or judgement around it. It states what today's intake target is and why it moved; it is not a verdict about the user's eating. It is a FORWARD-LOOKING target line, NOT a recap of yesterday. Omit the line entirely when it is absent -- never write a substitute."
fi

# A consultation without a weight gets one reminder on the next morning.
# 2.38.0: a health line -- only on the instance that owns health; a domain
# owner sends it in its own morning card.
DIET_NUDGE=""
if [[ "$OWNS_HEALTH" -eq 1 ]]; then
  if [[ "$DRY" == "1" ]]; then
    DIET_NUDGE="$("$LIFE_SH" goal-diet-nudge --dry 2>/dev/null || true)"
  else
    DIET_NUDGE="$("$LIFE_SH" goal-diet-nudge 2>/dev/null || true)"
  fi
fi
DIET_NUDGE_NOTE=""
if [[ -n "$DIET_NUDGE" ]]; then
  DATA+=$'\n'"# body measurement reminder"$'\n'
  DATA+="${DIET_NUDGE}"$'\n'
  DIET_NUDGE_NOTE="If a '# body measurement reminder' line is present, render it verbatim once in the diet/health portion of the briefing."
fi

# A goal interview the owner deferred to a date gets one reminder on that
# morning (2.37.0): onboard-defer stores the date, this is the reader.
DEFER_NUDGE=""
if [[ "$DRY" == "1" ]]; then
  DEFER_NUDGE="$("$LIFE_SH" onboard-defer-nudge --dry 2>/dev/null || true)"
else
  DEFER_NUDGE="$("$LIFE_SH" onboard-defer-nudge 2>/dev/null || true)"
fi
DEFER_NUDGE_NOTE=""
if [[ -n "$DEFER_NUDGE" ]]; then
  DATA+=$'\n'"# goal interview reminder"$'\n'
  DATA+="${DEFER_NUDGE}"$'\n'
  DEFER_NUDGE_NOTE="If a '# goal interview reminder' line is present, render it VERBATIM once, on its own line after every other section; it is a data line, not a closing line."
fi

CONTACT_NOTE=""
if [[ -n "$CONTACT_TXT" ]]; then
  DATA+=$'\n'"# contact suggestions"$'\n'
  DATA+="${CONTACT_TXT}"$'\n'
  CONTACT_NOTE="A contact-suggestions block is included in the data below under '# contact suggestions'. Render it as its own short section: a header line that reads EXACTLY '💬 연락해보세요', then each data row VERBATIM on its own line, unchanged (format 'name | context | N일전'). Do NOT rewrite rows into sentences, do NOT add guilt/urgency wording, do NOT reorder or merge columns, do NOT add trailing punctuation. Omit this whole section if no such data is present."
fi

WEATHER_NOTE=""
if [[ -n "$WEATHER_TXT" ]]; then
  DATA+=$'\n'"# weather"$'\n'
  DATA+="${WEATHER_TXT}"$'\n'
  WEATHER_NOTE="A weather section is included in the data below under '# weather'. Render a single weather line near the schedule section; use 🌧️ if the max precipitation probability across the listed hours is >= 40%, otherwise use ☀️. Omit this line entirely when no weather data is present."
fi


PROMPT="You are the user's personal assistant sending the morning briefing over Telegram.
Write the briefing in the user's language (locale: ${AGENT_LANG}). Address the user as: ${ADDRESS:-"(no fixed form of address; write naturally)"}.
Tone rules: ${TONE}

Structure (in this exact order; no closing line):
- first output line is EXACTLY the title provided in the data (the '# title' value), verbatim
- second output line is ONE short greeting sentence in the agent's own voice (address the user per the address/tone rules); it may reflect today's context from the data (weekday, empty or busy schedule) but must never invent facts; exactly one line, no icon, no filler beyond this single sentence
- ${SCHED_STRUCT}
- 🚕 이동 시간 못 잡은 일정 travel-gap list (only if present in the data), directly after the schedule section
- 🧩 안 챙긴 조각 unhandled-fragments list (only if present in the data)
- 💬 연락해보세요 contact list (only if present in the data)

Icon rules (fixed, apply the SAME icons every brief -- do not vary or substitute):
- title line starts with 🌅 (use the provided title verbatim)
- ${SCHED_ICON}
- travel-gap header line is EXACTLY 🚕 이동 시간 못 잡은 일정 (rows below it stay plain, one per line, verbatim)
- unhandled fragments header line is EXACTLY 🧩 안 챙긴 조각 (items below it stay plain, one per line)
- contact suggestions header line is EXACTLY 💬 연락해보세요 (rows below it stay plain, one per line, verbatim)

${SCHED_NOTE}
${RECAP_NOTE}
${CARRY_NOTE}
${DIET_NUDGE_NOTE}
${DEFER_NUDGE_NOTE}
${GAP_NOTE}
${FRAG_NOTE}
${CONTACT_NOTE}
${WEATHER_NOTE}
Use only the data below; never invent schedules or numbers. Output the message body only.

=== DATA ===
${DATA}"

if [[ "$DRY" -eq 1 ]]; then
  echo "---- day card message ----"
  if [[ "$DAY_CARD_UP" -eq 1 ]]; then
    echo "would send FIRST, before the text brief (fresh pinned message via day-card.sh --fresh; verbatim --text push on a framework without --fresh):"
    printf '%s\n' "$DAY_CARD_MSG"
  else
    echo "(none: no card message this run)"
  fi
  echo "---- DATA ----"; echo "$DATA"
  echo "---- PROMPT chars ----"; echo "${#PROMPT}"
  echo "---- weather card ----"
  if [[ "$BRIEF_WEATHER_CARD" == "on" ]]; then
    if [[ "$CARD_OK" -eq 1 ]]; then
      echo "card ON: would send photo AFTER text brief: ${CARD_PATH} ($(wc -c < "$CARD_PATH" | tr -d ' ') bytes)"
    else
      echo "card ON but generation failed: falling back to text weather this run"
    fi
  else
    echo "card OFF: text weather in DATA"
  fi
  [[ -n "${CARD_TMPDIR:-}" ]] && rm -rf "$CARD_TMPDIR" 2>/dev/null || true
  exit 0
fi

# The day card message (if any) already went out above. Now the TEXT brief,
# then the weather card photo AFTER. When a card
# must follow, do NOT exec the text push (exec would replace the process and
# the card push below would never run); run it, then push the card, then exit.
# If no card (off or gen failed), exec the text push as the final step.
if [[ "$CARD_OK" -eq 1 ]]; then
  "$PUSH_SH" --model haiku --prompt "$PROMPT" || echo "[brief] text brief send failed" >&2
  "$PUSH_SH" --photo "$CARD_PATH" || echo "[brief] weather card send failed (text already sent)" >&2
  [[ -n "${CARD_TMPDIR:-}" ]] && rm -rf "$CARD_TMPDIR" 2>/dev/null || true
  exit 0
fi

exec "$PUSH_SH" --model haiku --prompt "$PROMPT"
