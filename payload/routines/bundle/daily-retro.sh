#!/bin/bash
# daily-retro.sh -- kit_life bundle routine: STAGE 1 of the daily retro.
#
# OWNERSHIP SPLIT (owner-approved daily form, 2026-09-21 14:21).
# The approved flow has four stages and only the FIRST one is a cron job:
#
#   stage 1  발송        THIS SCRIPT. One shot: count what tonight's session
#                        will actually handle, list it, attach the start
#                        button, send. No conversation state, no model turn.
#   stage 2  할 일       brief-session SKILL
#   stage 3  오늘 일정   brief-session SKILL
#   stage 4  내일+마감   brief-session SKILL
#
# Stages 2-4 need stateful follow-up turns, button dispatch, silent record
# persistence and follow-up capture -- state that lives in the CONVERSATION,
# not in a cron shell. Do not grow a state machine here; the flow past the
# start button belongs to skills-bundle/brief-session/SKILL.md.
#
# WHAT THE OPENING MESSAGE SAYS (D-numbers = decisions in the owner ticket
# 2026-09-21 retro overhaul, daily-first):
#   D1   it COUNTS the things this session will handle. Never an activity
#        name ("정리/돌아보기"), never a mood line (D19 bans 감상).
#   D4   heading title / one-line inline-code date / count line / ONE
#        blockquote holding the lists / plan line / start question + button.
#   D11  task volume: everything that newly went overdue TODAY, plus the 2
#        oldest from before, plus "외 N건" for the rest.
#   D12  zero items including the accumulated backlog -> SEND NOTHING.
#        A retro with nothing to dispose of can only ask a watery question.
#   D28  one-line stage-entry notice ("오늘을 정리하고 내일을 계획합니다.").
#   D29  label is `할 일`, never `안 끝난 일` (a label must name the kind of
#        thing, not its failure state).
#   D13  every user-facing string is an i18n key (kit bundle, instance
#        override wins) -- no Korean literals in this file.
#   D21  the LAYOUT is data too: the templates are i18n keys under
#        retro.daily.layout.<preset>.*, preset chosen by RETRO_DAILY_LAYOUT.
#   D23  the dashboard is NOT sent here: at send time progress is 0 and the
#        list IS the dashboard. It starts in stage 2 (the skill).
#
# Send time is configuration (D21), currently 22:00 via service/kit_life/
# bundle.conf. This script never decides its own hour.
#
# Data path: local kit_life.db via database/kit_life.sh verbs ONLY -- never
# raw sqlite (kit_life.sh access rule). Derived blocks (이동/준비/약속 버퍼)
# are excluded by the verbs themselves (block_class='travel' is filtered by
# task-find/task-overdue, and appt-find reads appointment+presence only);
# BRIEF_EXCLUDE_TITLE_PREFIXES is applied on top as the DISPLAY axis (D16).
#
# DOMAIN AGENT (2.38.0, brief/retro standard). On an instance whose
# `kit_life.sh scope-self` says role=domain the retro is a GLANCE CARD, not a
# conversation (owner ruling 2026-10-02): title, the domain's daily-lane
# provider sections, one line naming this domain's tasks still open TODAY
# (not the accumulated backlog: a card with no conversation cannot dispose
# of one) -- no start
# button, no appointments (the main agent's retro owns those). Silent when
# no domain task is open today and no provider has anything. The provider
# order is the registry order, so the pack places its tail line (e.g. the
# next session) last.
#
# Usage: daily-retro.sh [--dry]   (--dry = print the composed message, no send)
# Exit:  0 ok (also when D12 silence applies) / 1 config error
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
LIFE_SH="$AGENT_DIR/database/kit_life.sh"
PUSH_SH="$AGENT_DIR/routines/push.sh"
DRY=0
[[ "${1:-}" == "--dry" ]] && DRY=1

[[ -x "$LIFE_SH" || -f "$LIFE_SH" ]] || { echo "[retro] kit_life.sh not found: $LIFE_SH" >&2; exit 1; }
[[ -f "$PUSH_SH" ]] || { echo "[retro] push.sh not found: $PUSH_SH" >&2; exit 1; }

TODAY="$(date +%F)"
# BRIEF_FAKE_TODAY=YYYY-MM-DD pins the run date (2.38.0; fixtures and dry
# renders on a database copy). Honoured ONLY under --dry: a real send always
# uses the wall clock, so a stray env var can never publish a wrong day.
if [[ -n "${BRIEF_FAKE_TODAY:-}" ]]; then
  if [[ "$DRY" -ne 1 ]]; then
    echo "[retro] FATAL BRIEF_FAKE_TODAY is set outside --dry -- refusing to send" >&2
    exit 1
  fi
  python3 -c "import datetime,sys;datetime.date.fromisoformat(sys.argv[1])" "$BRIEF_FAKE_TODAY" 2>/dev/null \
    || { echo "[retro] FATAL bad BRIEF_FAKE_TODAY (want YYYY-MM-DD)" >&2; exit 1; }
  TODAY="$BRIEF_FAKE_TODAY"
  :
fi

# Sunday soft-merge (owner 2026-08-17): the Sunday weekly review is the single
# Sunday retro. Suppress the standalone daily retro push on Sunday so the owner
# does not get two retros the same evening. --dry still renders for inspection.
if [[ "$DRY" -eq 0 && "$(date +%u)" == "7" ]]; then
  echo "[retro] Sunday: standalone daily retro suppressed -- weekly review covers today"
  exit 0
fi

# ---- locale ----
AGENT_LANG="$(grep -E '^AGENT_LANG=' "$AGENT_DIR/config/agent.conf" 2>/dev/null | head -1 | cut -d= -f2 | tr -d '[:space:]')"
AGENT_LANG="${AGENT_LANG:-en}"

# ---- layout preset (D21: default form + selectable + custom) ----
# RETRO_DAILY_LAYOUT names a template family under the i18n key prefix
# retro.daily.layout.<preset>.*. Unset/missing keys fall back to 'default',
# so a custom preset only has to override the slots it actually changes.
RETRO_DAILY_LAYOUT="$(grep -E '^RETRO_DAILY_LAYOUT=' "$AGENT_DIR/config/agent.conf" 2>/dev/null | head -1 | cut -d= -f2 | tr -d '[:space:]' || true)"
RETRO_DAILY_LAYOUT="${RETRO_DAILY_LAYOUT:-default}"

# ---- display-axis exclusion (D16: display axis, NOT the question axis) ----
# Same config key the morning brief reads: titles the SYSTEM derived and the
# owner does not want listed. The question axis is a different machine (D22:
# routine_def.retro_skip, registered inside the retro by its own button) and
# is not a prefix list -- see the SKILL.
# Quote-strip (2026-09-23): the framework reader SOURCES agent.conf, so a
# space-bearing value must be quoted there; this reader takes the raw text after
# `=`, where an unstripped quote joins the value and kills prefix matching.
# Only a matched pair at both ends is removed, so quoted and unquoted values
# read identically. Same idiom as day-card.sh conf() / morning-brief.sh.
EXCLUDE_PREFIXES="$(grep -E '^BRIEF_EXCLUDE_TITLE_PREFIXES=' "$AGENT_DIR/config/agent.conf" 2>/dev/null | head -1 | cut -d= -f2- | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'\$/\1/" || true)"

# ---- domain read-scope (unchanged knob) ----
# Where several instances share one kit_life.db, this retro speaks for its OWN
# domain only; ownership is a column on the row, so the scope is decided in the
# QUERY. Unset -> unscoped, byte-identical to single-instance estates.
# Quote-strip runs AFTER `tr` here: tr removes the whitespace, leaving any
# quote pair at the ends for sed to take off. Same reason as EXCLUDE_PREFIXES
# above -- this value is a single token today, but it is read from the same
# file the framework SOURCES, so it can legally arrive quoted.
TASK_OWNING_AGENT="$(grep -E '^TASK_OWNING_AGENT=' "$AGENT_DIR/config/agent.conf" 2>/dev/null | head -1 | cut -d= -f2 | tr -d '[:space:]' | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'\$/\1/" || true)"
# 2.38.0: scope-self decides the role, and the scope when the knob is unset
# (a domain agent reads its own slug plus the legacy aliases of what it owns;
# an unset knob on a main agent stays unscoped, byte-identical).
SCOPE="$("$LIFE_SH" scope-self 2>/dev/null || true)"
_sget() { printf '%s\n' "$SCOPE" | sed -n "s/^$1=//p" | head -1; }
ROLE="$(_sget role)"; ROLE="${ROLE:-main}"
OWNS="$(_sget owns)"
SKIP_MODULES="$(_sget skip_modules)"
[[ -z "$TASK_OWNING_AGENT" ]] && TASK_OWNING_AGENT="$(_sget task_scope)"
OWNER_SCOPE=()
[[ -n "$TASK_OWNING_AGENT" ]] && OWNER_SCOPE=(--owning-agent "$TASK_OWNING_AGENT")
ROLE_ARGS=(--role "$ROLE")
[[ -n "$SKIP_MODULES" ]] && ROLE_ARGS+=(--skip-module "$SKIP_MODULES")

# ---- data (verbs only) ----
# task-find <date>  -> id  title  due  state  place   (state = todo|done)
#   today's tasks; the ones still 'todo' at 22:00 are what newly went overdue.
# task-overdue      -> same columns, ORDER BY start_at = oldest first
#   everything that went overdue BEFORE today (block_class travel excluded).
# appt-find <date>  -> id  start_local  title  location  end_local  people
#   052: appointment AND presence, so a routine day-block (회사) is in the
#   list by default -- D22 ("회사도 그냥 디폴트로 회고에도 껴주자").
TASK_TODAY_ROWS="$("$LIFE_SH" task-find "$TODAY" ${OWNER_SCOPE[@]+"${OWNER_SCOPE[@]}"} 2>/dev/null || true)"
TASK_OVERDUE_ROWS="$("$LIFE_SH" task-overdue ${OWNER_SCOPE[@]+"${OWNER_SCOPE[@]}"} 2>/dev/null || true)"
# Appointments are the main agent's (the domain card never lists them).
EVENT_ROWS=""
[[ "$ROLE" == "domain" ]] || EVENT_ROWS="$("$LIFE_SH" appt-find "$TODAY" 2>/dev/null || true)"
# 2.28.0 retro extension point: one line per registered module provider
# (service/*/retro-sections.tsv, contract in database/retro_sections.py).
# The kit's own row (axis-lapsed) is weekly-only, so with no pack rows this is
# empty and the message is byte-identical.
# Module lines ride inside a retro that is being sent anyway; they never
# make a send on their own (D12 counts tasks and events only -- a pending
# module queue is not a reason to push, A5 anti-nag).
export KIT_RETRO_TODAY="$TODAY"
MODULE_LINES=""
[[ "$ROLE" == "domain" ]] || MODULE_LINES="$("$LIFE_SH" retro-sections --lane daily --mode line "${ROLE_ARGS[@]}" 2>/dev/null || true)"

# ==== DOMAIN PATH (2.38.0): the glance card ====
if [[ "$ROLE" == "domain" ]]; then
  D_SECTIONS="$("$LIFE_SH" retro-sections --lane daily --mode section "${ROLE_ARGS[@]}" 2>/dev/null || true)"
  read -r -d '' DOMAIN_PY <<'PY' || true
import datetime, json, os
AGENT_DIR = os.environ["RETRO_AGENT_DIR"]
LANG = os.environ.get("RETRO_LANG") or "en"
LOOKUP = os.path.join(AGENT_DIR, "routines", "lib", "i18n_lookup.py")


def _bundle(lg):
    if os.path.isfile(LOOKUP):
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("i18n_lookup", LOOKUP)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            got = mod.merged_bundle(AGENT_DIR, lg)
            if isinstance(got, dict):
                return got
        except Exception:
            pass
    merged = {}
    for path in (os.path.join(AGENT_DIR, "config", "i18n", "%s.json" % lg),
                 os.path.join(AGENT_DIR, "service", "kit_life", "i18n", "%s.json" % lg)):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            merged.update(data)
    return merged


B = [_bundle(LANG)] + ([_bundle("en")] if LANG != "en" else [])


def t(key, **kw):
    for b in B:
        v = b.get(key)
        if isinstance(v, str) and v != "":
            try:
                return v.format(**kw) if kw else v
            except (KeyError, IndexError, ValueError):
                return v
    return ""


EXCLUDE = [p.strip() for p in os.environ.get("RETRO_EXCLUDE_PREFIXES", "").split(",") if p.strip()]
rows = lambda k: [l.split("\t") for l in os.environ.get(k, "").split("\n") if l.strip()]
# TODAY's still-open domain tasks only. The card is a glance with no
# conversation, so it cannot dispose of a backlog: listing the accumulated
# overdue rows every night would be a nag nobody can answer.
open_rows = [r[1] for r in rows("RETRO_TASKS_TODAY") if len(r) >= 4 and r[3] == "todo"
             and not any(r[1].lstrip().startswith(p) for p in EXCLUDE)]
titles = list(dict.fromkeys(open_rows))
sections = os.environ.get("RETRO_SECTIONS", "").strip("\n")
print("items=%d\ttasks=%d\tsections=%d" % (len(open_rows) + (1 if sections.strip() else 0),
                                          len(open_rows), 1 if sections.strip() else 0))
if not titles and not sections.strip():
    raise SystemExit(0)
day = datetime.date.fromisoformat(os.environ["RETRO_TODAY"])
names = [w.strip() for w in t("retro.daily.weekdays").split(",")]
wd = names[(day.weekday() + 1) % 7] if len(names) == 7 else ""
dom = os.environ.get("RETRO_DOMAIN", "")
out = [t("retro.domain.daily.title", label=t("domain.label." + dom),
         date=t("retro.daily.date", m=day.month, d=day.day, wd=wd))]
if sections.strip():
    out += ["", sections]
if titles:
    out += ["", t("retro.domain.daily.tasks", n=len(open_rows), titles=" · ".join(titles[:3])
              + ((" " + t("retro.daily.more", n=len(titles) - 3)) if len(titles) > 3 else ""))]
print("\n".join(out))
PY
  D_OUT="$(
    RETRO_TODAY="$TODAY" RETRO_LANG="$AGENT_LANG" RETRO_AGENT_DIR="$AGENT_DIR" \
    RETRO_EXCLUDE_PREFIXES="$EXCLUDE_PREFIXES" RETRO_DOMAIN="${OWNS%%,*}" \
    RETRO_TASKS_TODAY="$TASK_TODAY_ROWS" RETRO_TASKS_OVERDUE="$TASK_OVERDUE_ROWS" \
    RETRO_SECTIONS="$D_SECTIONS" python3 -c "$DOMAIN_PY")"
  D_META="${D_OUT%%$'\n'*}"
  if [[ ! "$D_META" =~ ^items=[0-9]+ ]]; then
    echo "[retro] domain composer produced no meta line; nothing sent" >&2
    exit 1
  fi
  D_BODY=""
  [[ "$D_OUT" == *$'\n'* ]] && D_BODY="${D_OUT#*$'\n'}"
  if [[ "$DRY" -eq 1 ]]; then
    echo "---- META ----"; echo "$D_META"
    echo "---- BODY ----"; echo "${D_BODY:-(silent: no open domain task and no provider line today)}"
    exit 0
  fi
  [[ -n "$D_BODY" ]] || exit 0
  exec "$PUSH_SH" --text "$D_BODY"
fi

# ---- compose (deterministic; the counts are the point, D1) ----
# The composer prints one meta line first (items/tasks/events), then the body.
# Composition is python for template substitution only -- no DB access here.

# The composer source is hoisted into a variable (not a heredoc inside
# $(...)): bash 3.2, still the /bin/bash on macOS, mis-parses a nested
# heredoc whose body carries unbalanced parentheses.
read -r -d '' COMPOSE_PY <<'PY' || true
import datetime
import json
import os

TODAY = os.environ["RETRO_TODAY"]
LANG = os.environ.get("RETRO_LANG") or "en"
AGENT_DIR = os.environ["RETRO_AGENT_DIR"]
PRESET = os.environ.get("RETRO_LAYOUT") or "default"
EXCLUDE = [p.strip() for p in os.environ.get("RETRO_EXCLUDE_PREFIXES", "").split(",") if p.strip()]

# D11 volume rule: every task that newly went overdue today, plus this many of
# the oldest accumulated ones, and "외 N건" for whatever is left.
OVERDUE_SHOW = 2


# --- i18n: per language (then 'en'), the same order kit_life.py._i18n uses:
# the framework's layered lookup routines/lib/i18n_lookup.py merged_bundle
# (DGN-1789 / dec-204: kit layers config/packs/<id>.i18n.<lang>.json in the
# framework's dependency order, then the framework default config/i18n). The
# kit's keys ship in payload/config/i18n/*.bundle.json -> kit_life's layer.
# OLD FRAMEWORK (no i18n_lookup.py; SUNSET at framework >= 2.6.0 carrying
# DGN-1789): the kit's installed bundle copy service/kit_life/i18n over
# config/i18n.
_LOOKUP = os.path.join(AGENT_DIR, "routines", "lib", "i18n_lookup.py")


def _bundle(lang):
    if os.path.isfile(_LOOKUP):
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("i18n_lookup", _LOOKUP)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            got = mod.merged_bundle(AGENT_DIR, lang)
            if isinstance(got, dict):
                return got
        except Exception:
            pass
    merged = {}
    for path in (os.path.join(AGENT_DIR, "config", "i18n", "%s.json" % lang),
                 os.path.join(AGENT_DIR, "service", "kit_life", "i18n", "%s.json" % lang)):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            merged.update(data)
    return merged


BUNDLES = [_bundle(LANG)] + ([_bundle("en")] if LANG != "en" else [])


def _fill(val, kw):
    """Substitute, but never let a malformed instance override kill the send:
    a template with an unknown placeholder degrades to its own raw text."""
    if not kw:
        return val
    try:
        return val.format(**kw)
    except (KeyError, IndexError, ValueError):
        return val


def t(key, **kw):
    for b in BUNDLES:
        val = b.get(key)
        if isinstance(val, str) and val != "":
            return _fill(val, kw)
    return ""


def layout(slot, **kw):
    """Layout template for a slot: the configured preset first, then the
    'default' family. D21 -- the shape is data, not code."""
    for preset in (PRESET, "default"):
        key = "retro.daily.layout.%s.%s" % (preset, slot)
        for b in BUNDLES:
            val = b.get(key)
            if isinstance(val, str) and val != "":
                return _fill(val, kw)
    return ""


def rows(env_key):
    raw = os.environ.get(env_key, "")
    return [ln.split("\t") for ln in raw.split("\n") if ln.strip()]


def excluded(title):
    return any(title.lstrip().startswith(p) for p in EXCLUDE)


def d(date_str):
    return datetime.date(*(int(x) for x in date_str.split("-")))


def fmt_date(date_str):
    """M/D(요일) -- the single date form (D4). Weekday names are i18n data."""
    day = d(date_str)
    names = [w.strip() for w in t("retro.daily.weekdays").split(",")]
    wd = names[(day.weekday() + 1) % 7] if len(names) == 7 else ""
    return t("retro.daily.date", m=day.month, d=day.day, wd=wd)


def hhmm(local_col):
    """appt-find renders the local column as ISO+offset for timed rows and a
    bare date for all-day rows; only a timed row has a clock face."""
    if "T" in local_col:
        return local_col.split("T", 1)[1][:5]
    return ""


today = d(TODAY)

# ---- tasks: today's still-open ones, then the oldest accumulated ones ----
today_tasks = [r for r in rows("RETRO_TASKS_TODAY")
               if len(r) >= 4 and r[3] == "todo" and not excluded(r[1])]
overdue_all = [r for r in rows("RETRO_TASKS_OVERDUE")
               if len(r) >= 3 and not excluded(r[1])]
overdue_shown = overdue_all[:OVERDUE_SHOW]
overdue_rest = len(overdue_all) - len(overdue_shown)

# ---- today's events (appointment + presence; derived blocks never appear) ----
events = [r for r in rows("RETRO_EVENTS") if len(r) >= 3 and not excluded(r[2])]

# ---- module lines (2.28.0 extension point; empty unless a pack registers) ----
modules = [ln.strip() for ln in os.environ.get("RETRO_MODULE_LINES", "").split("\n") if ln.strip()]

# D23: disposal scope == displayed scope. The count says what THIS session
# handles, not how deep the backlog is -- the "외 N건" line carries the rest.
task_n = len(today_tasks) + len(overdue_shown)
event_n = len(events)
total = task_n + event_n

# D12: nothing to dispose of, including the backlog -> stay silent.
print("items=%d\ttasks=%d\tevents=%d\tbutton=%s"
      % (total, task_n, event_n, t("retro.daily.start_button")))
if total == 0:
    raise SystemExit(0)

# ---- the one blockquote that holds the lists (D4: lists only) ----
block = []
if task_n:
    block.append(layout("list.section", name=t("retro.daily.section.tasks")))
    for r in today_tasks:
        # D23: no date on today's rows -- "9/21(월)" three times in a row is
        # noise. A date appears only on something that is NOT today.
        block.append(layout("list.task_today", title=r[1]))
    for r in overdue_shown:
        due = r[2]
        late = (today - d(due)).days if due else 0
        block.append(layout("list.task_overdue", date=fmt_date(due) if due else "",
                            title=r[1], late=t("retro.daily.overdue", n=late)))
    if overdue_rest > 0:
        block.append(layout("list.more", more=t("retro.daily.more", n=overdue_rest)))
if event_n:
    if block:
        block.append(layout("list.gap"))
    block.append(layout("list.section", name=t("retro.daily.section.events")))
    for r in events:
        start = hhmm(r[1])
        end = hhmm(r[4]) if len(r) > 4 else ""
        span = t("retro.daily.time_range", start=start, end=end) if start and end else start
        # an all-day row has no clock face, so it takes its own template
        # rather than a timed line with a hole where the time was.
        if span:
            block.append(layout("list.event", time=span, title=r[2]))
        else:
            block.append(layout("list.event_all_day", title=r[2]))
if modules:
    if block:
        block.append(layout("list.gap"))
    block.append(layout("list.section", name=t("retro.daily.section.modules")))
    for line in modules:
        block.append(layout("list.module", line=line))

# ---- count line: what tonight actually handles (D1) ----
parts = []
if task_n:
    parts.append(t("retro.daily.count.tasks", n=task_n))
if event_n:
    parts.append(t("retro.daily.count.events", n=event_n))
count_line = t("retro.daily.count.line", items=t("retro.daily.count.sep").join(parts))

print(layout("opener",
             title=t("retro.daily.title"),
             opener=t("retro.daily.opener"),
             date=fmt_date(TODAY),
             count_line=count_line,
             list="\n".join(block),
             plan_line=t("retro.daily.plan_line"),
             start_question=t("retro.daily.start_question")))
PY

COMPOSED="$(
  RETRO_TODAY="$TODAY" \
  RETRO_LANG="$AGENT_LANG" \
  RETRO_AGENT_DIR="$AGENT_DIR" \
  RETRO_LAYOUT="$RETRO_DAILY_LAYOUT" \
  RETRO_EXCLUDE_PREFIXES="$EXCLUDE_PREFIXES" \
  RETRO_TASKS_TODAY="$TASK_TODAY_ROWS" \
  RETRO_TASKS_OVERDUE="$TASK_OVERDUE_ROWS" \
  RETRO_EVENTS="$EVENT_ROWS" \
  RETRO_MODULE_LINES="$MODULE_LINES" \
  python3 -c "$COMPOSE_PY")"

# Split on the first newline with parameter expansion, not a pipe: under
# `pipefail` a printf whose reader (head) exits early can fail the whole
# assignment, which would abort the send for a long body.
META="${COMPOSED%%$'\n'*}"
BODY="${COMPOSED#*$'\n'}"
# The meta line is the composer's contract. A missing one means the composer
# did not run to completion -- that must be a LOUD failure, never mistaken for
# D12 silence (a silent zero would lose a real retro without a trace).
if [[ ! "$META" =~ ^items=[0-9]+ ]]; then
  echo "[retro] composer produced no meta line; nothing sent" >&2
  exit 1
fi
ITEMS="$(printf '%s' "$META" | awk -F'\t' '{print $1}' | cut -d= -f2)"

# ---- D12: silence ----
if [[ "$ITEMS" -eq 0 ]]; then
  if [[ "$DRY" -eq 1 ]]; then
    echo "---- META ----"; echo "$META"
    echo "---- BODY ----"; echo "(silent: nothing to dispose of today, D12)"
  fi
  exit 0
fi

# The start button is the only button on this message (D17: a turn whose
# buttons cover the whole answer carries no "select or type" guidance line).
# push.sh --button takes ONE inline button as 라벨::callback_data; the bridge
# replays the tapped label as the owner's next message, which is what wakes
# the brief-session SKILL for stages 2-4 (bridge opt: callback prefix).
START_LABEL="${META##*$'\t'button=}"
[[ "$START_LABEL" == "$META" ]] && START_LABEL=""
START_LABEL="${START_LABEL:-Start retro}"

if [[ "$DRY" -eq 1 ]]; then
  echo "---- META ----"; echo "$META"
  echo "---- BUTTON ----"; echo "${START_LABEL}::opt:${START_LABEL}"
  echo "---- BODY ----"; echo "$BODY"
  exit 0
fi

exec "$PUSH_SH" --text "$BODY" --button "${START_LABEL}::opt:${START_LABEL}"
