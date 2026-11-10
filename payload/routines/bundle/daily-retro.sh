#!/bin/bash
# daily-retro.sh -- kit_life bundle routine: STAGE 1 of the daily retro.
#
# OWNERSHIP SPLIT (owner-approved daily form, 2026-09-21 14:21).
# The approved flow has four stages and only the FIRST one is a cron job:
#
#   stage 1  발송        THIS SCRIPT. One shot: count what tonight's session
#                        will actually handle, list it, send without a start
#                        button. With no opener, send the first step too.
#   stage 2  할 일       brief-session SKILL
#   stage 3  오늘 일정   brief-session SKILL
#   stage 4  내일+마감   brief-session SKILL
#
# Stages 2-4 relay the ordered kit retro-walk cursor. The composer emits
# exactly the shown rows; the kit persists progress, and brief-session relays
# one current item until the list is exhausted or the owner stops.
#
# WHAT THE OPENING MESSAGE SAYS (D-numbers = decisions in the owner ticket
# 2026-09-21 retro overhaul, daily-first):
#   D1   counts still determine silence and session scope. Owner 2026-11-05:
#        omit the visible count line.
#   D4   Owner 2026-11-05: title + date / lists / opener question last.
#        Owner 2026-10-16: one HTML
#        quote per list item, no blank line between adjacent item blocks.
#        Owner 2026-10-24: group the experience list and each course question.
#   D11  task volume: everything that newly went overdue TODAY, plus the 2
#        oldest from before, plus "외 N건" for the rest.
#   D12  zero items including the accumulated backlog -> SEND NOTHING.
#        A retro with nothing to dispose of can only ask a watery question.
#   D28  Owner 2026-11-05: omit the closing stage-entry notice.
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
# next session) last. Rows flagged `attach` add the card's attachments (a
# photo after it, an inline keyboard on it) -- see ATTACHMENTS below.
#
# QUESTIONS (owner 2026-10-30 21:58, pick 1 "card apart, one question at a time"): the domain card
# is the summary only -- no question line, no question button. Every question
# (a provider's `ask` row, a raw `[[OPTIONS: ...]]` line from an older pack, a
# planned meal still open) is saved as an item of the retro walk BEFORE the
# card goes out; right after the card (and its photos) the walk posts the FIRST
# one as its own message with a code-posted keyboard, and each next one only
# after the owner's answer (database/retro_walk.py post / answer / resume).
# Contract: database/retro_sections.py (ATTACH, `ask` row).
#
# Usage: daily-retro.sh [--dry [--json]]
# --dry prints the card and id/kind walk; --dry --json emits the full packet.
# Exit:  0 ok (also when D12 silence applies) / 1 config error
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
LIFE_SH="$AGENT_DIR/database/kit_life.sh"
PUSH_SH="$AGENT_DIR/routines/push.sh"
DRY=0
JSON=0
for arg in "$@"; do
  case "$arg" in
    --dry) DRY=1 ;;
    --json) JSON=1 ;;
    *) echo "[retro] unknown option: $arg" >&2; exit 1 ;;
  esac
done
[[ "$JSON" -eq 0 || "$DRY" -eq 1 ]] || { echo "[retro] --json requires --dry" >&2; exit 1; }

[[ -x "$LIFE_SH" || -f "$LIFE_SH" ]] || { echo "[retro] kit_life.sh not found: $LIFE_SH" >&2; exit 1; }
# Explicit slot-off also protects manual routine invocations.
for _retro_conf in "$AGENT_DIR/config/kit_life.conf" "$AGENT_DIR/config/lifekit.conf"; do
  if [[ -f "$_retro_conf" ]]; then
    _retro_switch="$(sed -n 's/^BUNDLE_DAILY_RETRO=//p' "$_retro_conf" | head -1 | tr -d "[:space:]\"'")"
    [[ "$_retro_switch" != "off" ]] || exit 0
    break
  fi
done

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

# Scheduled retro tasks use the task start-card machinery. The old cron
# lane remains the fallback for instances that have not set a retro clock.
if [[ "$DRY" -eq 0 && -n "$("$LIFE_SH" retro-task daily "$TODAY" 2>/dev/null || true)" ]]; then
  exit 0
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
# the crew leader (role=main) reads EVERY member's rows: scope-self's task_scope
# (kit_life.read_scope) wins over the knob (owner 2026-10-13).
if [[ "$ROLE" == "main" || -z "$TASK_OWNING_AGENT" ]]; then TASK_OWNING_AGENT="$(_sget task_scope)"; fi
OWNER_SCOPE=()
[[ -n "$TASK_OWNING_AGENT" ]] && OWNER_SCOPE=(--owning-agent "$TASK_OWNING_AGENT")
ROLE_ARGS=(--role "$ROLE")
[[ -n "$SKIP_MODULES" ]] && ROLE_ARGS+=(--skip-module "$SKIP_MODULES")

# The evening reprise uses the agent's own day-pin composer. Domain packs
# provide service/<owned-domain>/pin-reprise --date YYYY-MM-DD; all other
# agents use the kit day card. Only explicit off disables the retro reprise;
# cooking's session candidates remain opt-in in activity_session.py.
PIN_SYMMETRY="${LIFEKIT_PIN_SYMMETRY:-retro-pin-first}"
day_pin() {
  [[ "$PIN_SYMMETRY" != "off" ]] || return 0
  local domain provider
  local domains=()
  IFS=',' read -r -a domains <<< "$OWNS"
  for domain in ${domains[@]+"${domains[@]}"}; do
    # Scope names are data, never paths outside service/.
    [[ "$domain" =~ ^[a-zA-Z0-9_-]+$ ]] || continue
    provider="$AGENT_DIR/service/$domain/pin-reprise"
    if [[ -x "$provider" ]]; then
      "$provider" --date "$TODAY"
      return $?
    fi
  done
  local args=(--dry --print --date "$TODAY")
  [[ "$DRY" -eq 1 && -n "${BRIEF_FAKE_NOW:-}" ]] && args+=(--now "$BRIEF_FAKE_NOW")
  "$SCRIPT_DIR/day-card.sh" "${args[@]}"
}

# Retire waits whose linked pickup/delivery already completed before reads.
if [[ "$DRY" -eq 0 ]]; then
  "$LIFE_SH" plan-wait-resolve --reconcile >/dev/null
fi

# ---- data (verbs only) ----
# Read the completed session's explicit task link before collecting open tasks.
# This also repairs old closes; it reuses evidence and creates no record blocks.
"$LIFE_SH" workout-retro-reconcile "$TODAY" >/dev/null || true
# task-find <date>  -> id  title  due  state  place   (state = todo|done)
#   today's tasks; the ones still 'todo' at 22:00 are what newly went overdue.
# task-overdue      -> same columns, ORDER BY start_at = oldest first
#   everything that went overdue BEFORE today (block_class travel excluded).
# appt-find <date>  -> id  start_local  title  location  end_local  people
#   052: appointment AND presence, so a routine day-block (회사) is in the
#   list by default -- D22 ("회사도 그냥 디폴트로 회고에도 껴주자").
TASK_TODAY_ROWS="$("$LIFE_SH" task-find "$TODAY" ${OWNER_SCOPE[@]+"${OWNER_SCOPE[@]}"} 2>/dev/null || true)"
TASK_TODAY_DETAIL="$("$LIFE_SH" task-read "$TODAY" ${OWNER_SCOPE[@]+"${OWNER_SCOPE[@]}"} 2>/dev/null || printf '[]')"
TASK_OVERDUE_ROWS="$("$LIFE_SH" task-overdue --before "$TODAY" ${OWNER_SCOPE[@]+"${OWNER_SCOPE[@]}"} 2>/dev/null || true)"
# Appointments are the main agent's (the domain card never lists them).
EVENT_ROWS=""
# The same read gate as the question walk: started same-day timed
# appointments count now; presence and overnight visits still wait to end.
[[ "$ROLE" == "domain" ]] || EVENT_ROWS="$("$LIFE_SH" appt-find "$TODAY" --retro 2>/dev/null || true)"
# Retro batch (owner 2026-10-09 21:29/21:37): the same gate read as ITEMS --
# a nested event (outing bundle, contained child, attached meal, continuing
# stay) is ONE line, and a visit carried from an earlier day is not listed
# as today's (own section). Empty output (an older kit) keeps the rows above.
EVENT_ITEMS=""
[[ "$ROLE" == "domain" ]] || EVENT_ITEMS="$("$LIFE_SH" retro-items "$TODAY" --no-rating 2>/dev/null || true)"
EXPERIENCES=""
COURSE_PREVIEW=()
if [[ -n "${COURSE_REVIEW_FAKE_NOW:-}" ]]; then
  [[ "$DRY" -eq 1 ]] || { echo "[retro] preview clock requires --dry" >&2; exit 1; }
  COURSE_PREVIEW=(--now "$COURSE_REVIEW_FAKE_NOW")
fi
[[ "$ROLE" == "domain" && "$JSON" -eq 0 ]] || EXPERIENCES="$("$LIFE_SH" experience-retro-list "$TODAY" --quote --daily-opener ${COURSE_PREVIEW[@]+"${COURSE_PREVIEW[@]}"})"
# 2.28.0 retro extension point: one line per registered module provider
# (service/*/retro-sections.tsv, contract in database/retro_sections.py).
# The kit's own row (axis-lapsed) is weekly-only, so with no pack rows this is
# empty and the message is byte-identical.
# Module lines ride inside a retro that is being sent anyway; they never
# make a send on their own (D12 counts tasks and events only -- a pending
# module queue is not a reason to push, A5 anti-nag).
export KIT_RETRO_TODAY="$TODAY"
# Undated open tasks (owner 2026-10-13: they belong here, not in the morning
# brief): loose to-dos with no date + one stalled project step per project
# (> 3 days), offered for a date in the retro conversation. Life agent only;
# rows have no start_at so they never repeat today's/overdue lines. Like
# module lines they ride a retro that is sent anyway, never make one.
UNTIMED_ROWS=""
[[ "$ROLE" == "domain" ]] || UNTIMED_ROWS="$("$LIFE_SH" task-untimed-open --suggest --brief --retro --today "$TODAY" ${OWNER_SCOPE[@]+"${OWNER_SCOPE[@]}"} 2>/dev/null || true)"
export KIT_RETRO_TASK_SCOPE="$TASK_OWNING_AGENT"
MODULE_LINES=""
[[ "$ROLE" == "domain" && "$JSON" -eq 0 ]] || MODULE_LINES="$("$LIFE_SH" retro-sections --lane daily --mode line --json "${ROLE_ARGS[@]}" 2>/dev/null || true)"

# Scheduled domain output remains a glance card. Explicit JSON requests
# compose the interactive walk below, using the same domain task scope.
if [[ "$ROLE" == "domain" && "$JSON" -eq 0 ]]; then
  # This consumer posts asks as their own messages: providers (inherited env)
  # keep their question lines out of the section text and emit `ask` rows.
  export KIT_RETRO_ASK_SPLIT=1
  # Ask row contract v2 (retro-sections): providers may add `recheck`, which
  # the walk runs just before posting that question.
  export KIT_RETRO_ASK_RECHECK=1
  D_SECTIONS="$("$LIFE_SH" retro-sections --lane daily --mode section "${ROLE_ARGS[@]}" 2>/dev/null || true)"
  # QUESTIONS ARE NOT CARD LINES (owner 2026-10-30 21:58): the card is the
  # summary only; every question rides the retro walk and is posted as its own
  # message with its own keyboard, the next one only after the owner answers
  # (database/retro_walk.py post/answer). ASK_LINES holds one validated
  # `ask<TAB>json` row per question (retro-sections ask contract).
  ASK_LINES=""
  # A planned meal still unconfirmed tonight is ASKED here, by the domain that
  # owns meals -- the planned row is the open state, no owner task backs it.
  # One question per row, in the walk after the card.
  PM_ASKS=""
  if [[ ",$OWNS," == *,health,* ]]; then
    PM_ASKS="$("$LIFE_SH" meal-planned-ask "$TODAY" --walk 2>/dev/null | grep -a '^ask'$'\t' || true)"
  fi
  # Safety net: a provider that still prints "question + [[OPTIONS: a | b]]"
  # (a pack older than the ask contract) never leaks the raw marker -- the line
  # before it becomes the question, the labels its keyboard.
  MARK_ASKS=""
  if [[ "$D_SECTIONS" == *'[[OPTIONS:'* ]]; then
    read -r -d '' MARK_PY <<'PY' || true
import hashlib, json, os, re, sys
pat = re.compile(r"^\s*(?:>\s*)?\[\[OPTIONS:\s*(.*?)\s*\]\]\s*$")
out, asks = [], []
for line in os.environ["RETRO_SECTIONS"].split("\n"):
    m = pat.match(line)
    if not m:
        out.append(line)
        continue
    labels = [x.strip() for x in m.group(1).split("|") if x.strip()]
    j = len(out) - 1
    while j >= 0 and not out[j].strip():
        j -= 1
    text = re.sub(r"^\s*>\s*", "", out[j]).strip() if j >= 0 else ""
    if j >= 0:
        del out[j:]
    if (text and 1 <= len(labels) <= 4 and len(set(labels)) == len(labels)
            and all(len(l.encode()) <= 60 and "\n" not in l for l in labels)):
        asks.append("ask\t" + json.dumps(dict(
            id="marker:" + hashlib.sha1(text.encode()).hexdigest()[:8], text=text,
            choices=[dict(label=l, then="talk") for l in labels]), ensure_ascii=False))
    else:
        print("[retro] raw options marker dropped (no usable question)", file=sys.stderr)
print(json.dumps(asks, ensure_ascii=False))
print("\n".join(out), end="")
PY
    _MARK="$(RETRO_SECTIONS="$D_SECTIONS" python3 -c "$MARK_PY")"
    MARK_ASKS="$(printf '%s' "${_MARK%%$'\n'*}" | python3 -c 'import json,sys; print("\n".join(json.load(sys.stdin)))')"
    D_SECTIONS="${_MARK#*$'\n'}"
    [[ "$_MARK" == *$'\n'* ]] || D_SECTIONS=""
  fi
  read -r -d '' ASK_PACKET_PY <<'PY' || true
import json, os
items = []
for ln in os.environ["RETRO_ASKS"].split("\n"):
    if not ln.strip():
        continue
    q = json.loads(ln.split("\t", 1)[1])
    base = q.get("base")
    recheck = q.get("recheck")
    items.append(dict(kind="module", id="ask:%s:%s" % (q.get("module") or "kit", q["id"]), text=q["text"],
                      ask=dict(choices=q["choices"], **({"base": base} if base else {}),
                               **({"recheck": recheck} if recheck else {}))))
print(json.dumps(dict(date=os.environ["RETRO_TODAY"], body=os.environ["RETRO_BODY"], items=items),
                 ensure_ascii=False))
PY
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
out = [t("retro.domain.daily.title.health" if dom == "health" else "retro.domain.daily.title", label=t("domain.label." + dom),
         date=t("retro.daily.date", m=day.month, d=day.day, wd=wd))]
if sections.strip():
    out += ["", sections]
if titles:
    out += ["", t("retro.domain.daily.tasks", n=len(open_rows), titles=" / ".join(titles[:3])
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
  DAY_PIN=""
  [[ -z "$D_BODY" ]] || DAY_PIN="$(day_pin)"
  # ATTACHMENTS (2.46.0-dev.10, owner 2026-10-10 21:14): rows flagged
  # `attach` in the registry (contract: database/retro_sections.py ATTACH)
  # are asked only AFTER the card is composed and only when it goes out --
  # an attachment never makes a send on its own. The reader has already
  # validated every part (existing file, keyboard rows with opt: callbacks)
  # and dropped a bad one with one stderr line; here a part only has to be
  # delivered. stderr is kept: it is the routine log's record of a drop.
  D_PHOTOS=(); D_CAPTIONS=(); D_BUTTONS=""; ATTACH_ASKS=""
  if [[ -n "$D_BODY" ]]; then
    D_ATTACH="$("$LIFE_SH" retro-sections --lane daily --mode attach "${ROLE_ARGS[@]}" || true)"
    while IFS= read -r _ln; do
      _kind="${_ln%%$'\t'*}"; _rest="${_ln#*$'\t'}"
      [[ "$_rest" == "$_ln" ]] && continue
      case "$_kind" in
        photo)   D_PHOTOS+=("${_rest%%$'\t'*}")
                 _cap=""; [[ "$_rest" == *$'\t'* ]] && _cap="${_rest#*$'\t'}"
                 D_CAPTIONS+=("$_cap") ;;
        buttons) D_BUTTONS="$_rest" ;;
        ask)     ATTACH_ASKS="${ATTACH_ASKS:+$ATTACH_ASKS$'\n'}ask"$'\t'"$_rest" ;;
      esac
    done <<< "$D_ATTACH"
  fi
  # Order (owner 2026-10-31 06:13): planned meals first, then provider asks
  # (registry order; read only when a card goes out, like every attachment --
  # the missing-meal question is one, re-checked by its `recheck` after the
  # planned answers), then marker asks. A planned meal still open is a send on
  # its own (the row is the open state): with no card text the first question
  # goes out alone, as before.
  ASK_LINES="$(printf '%s\n%s\n%s\n' "$PM_ASKS" "$ATTACH_ASKS" "$MARK_ASKS" | sed '/^$/d')"
  if [[ "$DRY" -eq 1 ]]; then
    echo "---- META ----"; echo "$D_META"
    if [[ -n "$D_BUTTONS" || ${#D_PHOTOS[@]} -gt 0 ]]; then
      echo "---- ATTACH ----"
      [[ -n "$D_BUTTONS" ]] && echo "buttons (on the card, push.sh --strict --buttons-json): $D_BUTTONS"
      for _i in ${D_PHOTOS[@]+"${!D_PHOTOS[@]}"}; do
        echo "photo (after the card, push.sh --photo): ${D_PHOTOS[$_i]}${D_CAPTIONS[$_i]:+ caption: ${D_CAPTIONS[$_i]}}"
      done
    fi
    if [[ -n "$ASK_LINES" ]]; then
      echo "---- ASKS (each its own message after the card, next one after the answer) ----"
      printf '%s\n' "$ASK_LINES" | python3 -c 'import json,sys
for n, ln in enumerate(sys.stdin, 1):
    q = json.loads(ln.split("\t", 1)[1])
    print("%d. [%s] %s  {%s}" % (n, q["id"], q["text"], " | ".join(c["label"] for c in q["choices"])))'
    fi
    [[ -z "$DAY_PIN" ]] || { echo "---- DAY PIN (same composer) ----"; echo "$DAY_PIN"; }
    echo "---- BODY ----"; echo "${D_BODY:-(silent: no open domain task and no provider line today)}"
    exit 0
  fi
  [[ -n "$D_BODY" || -n "$ASK_LINES" ]] || exit 0
  # The walk is saved BEFORE the card goes out (same order as the main card):
  # the questions are claimed with the card they follow. A refused save (an
  # older kit, or a walk already in progress) is logged and the card still goes.
  WALK_SAVED=0
  if [[ -n "$ASK_LINES" ]]; then
    ASK_PACKET="$(RETRO_TODAY="$TODAY" RETRO_BODY="$D_BODY" RETRO_ASKS="$ASK_LINES" python3 -c "$ASK_PACKET_PY")"
    if printf '%s' "$ASK_PACKET" | "$LIFE_SH" retro-walk "$TODAY" save >/dev/null; then
      WALK_SAVED=1
    else
      echo "[retro] walk not saved; the card goes without its questions" >&2
    fi
  fi
  if [[ -z "$D_BODY" ]]; then
    # Questions only (no card text): nothing to summarise, the first one goes alone.
    [[ "$WALK_SAVED" -eq 1 ]] || exit 0
    exec "$LIFE_SH" retro-walk "$TODAY" post >/dev/null
  fi
  [[ -z "$DAY_PIN" ]] || "$PUSH_SH" --text "$DAY_PIN"
  # No attachment and no question: the exact pre-dev.10 send.
  [[ -n "$D_BUTTONS" || ${#D_PHOTOS[@]} -gt 0 || "$WALK_SAVED" -eq 1 ]] || exec "$PUSH_SH" --text "$D_BODY"
  # 1) the card. A multi-button keyboard rides the framework's strict lane
  #    (push.sh --strict --buttons-json, the code-posted keyboard path; the
  #    start button's --button carries ONE button only). A push.sh without
  #    the strict capability, or a strict refusal, still sends the card --
  #    text only, buttons dropped. rc 6 = delivery uncertain: the card may
  #    be out, so it is NOT re-sent (a duplicate card is worse than a lost
  #    keyboard).
  RC=0
  if [[ -n "$D_BUTTONS" ]] && "$PUSH_SH" --capabilities 2>/dev/null | grep -qw strict; then
    "$PUSH_SH" --strict --text "$D_BODY" --buttons-json "$D_BUTTONS" >/dev/null || RC=$?
    if [[ "$RC" -eq 6 ]]; then
      echo "[retro] card delivery uncertain (strict rc 6); not re-sent" >&2
    elif [[ "$RC" -ne 0 ]]; then
      echo "[retro] strict card send failed (rc $RC); sending text without buttons" >&2
      RC=0; "$PUSH_SH" --text "$D_BODY" || RC=$?
    fi
  else
    [[ -n "$D_BUTTONS" ]] && echo "[retro] push.sh has no strict lane; card buttons dropped" >&2
    "$PUSH_SH" --text "$D_BODY" || RC=$?
  fi
  # 2) the photos, each its own message after the card; a failed one is
  #    logged and never undoes the card.
  for _i in ${D_PHOTOS[@]+"${!D_PHOTOS[@]}"}; do
    _pa=(--photo "${D_PHOTOS[$_i]}")
    [[ -n "${D_CAPTIONS[$_i]}" ]] && _pa+=(--caption "${D_CAPTIONS[$_i]}")
    "$PUSH_SH" "${_pa[@]}" || echo "[retro] photo send failed: ${D_PHOTOS[$_i]} (card already sent)" >&2
  done
  # 3) the FIRST question, its own message with its keyboard; the next ones
  #    follow the owner's answers (retro-walk answer). Skipped when the card
  #    itself failed (rc other than 0 / 6): nothing was claimed from the owner.
  if [[ "$WALK_SAVED" -eq 1 && ( "$RC" -eq 0 || "$RC" -eq 6 ) ]]; then
    "$LIFE_SH" retro-walk "$TODAY" post >/dev/null || echo "[retro] first question not posted" >&2
  fi
  exit "$RC"
fi

# ---- compose: the daily experience opens, then the existing ordered walk ----
# The composer prints one meta line first (items/tasks/events), then the body.
# Composition is python for template substitution only -- no DB access here.

# The composer source is hoisted into a variable (not a heredoc inside
# $(...)): bash 3.2, still the /bin/bash on macOS, mis-parses a nested
# heredoc whose body carries unbalanced parentheses.
read -r -d '' COMPOSE_PY <<'PY' || true
import datetime
import hashlib
import json
import os
import re

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
    # Same sanitizer-input contract as kit_life._day_pin_quote: defuse data
    # tag openers, leave & and comparisons for the bridge to escape once.
    # Only list slots carry HTML; template tags remain trusted layout data.
    if slot.startswith("list."):
        kw = {k: re.sub(r"<(?=[/A-Za-z])", "<\u2060", str(v))
              for k, v in kw.items()}
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
try:
    task_detail = {str(x["id"]): x for x in json.loads(os.environ.get("RETRO_TASK_DETAIL", "[]"))}
except (ValueError, KeyError, TypeError):
    task_detail = {}
# A scheduled retro's own session task is the container, not a disposal row.
# Snack/check markers have action_kind=check and remain ordinary tasks.
today_tasks = [r for r in today_tasks if task_detail.get(r[0], {}).get("action_kind") != "retro"]
overdue_all = [r for r in rows("RETRO_TASKS_OVERDUE")
               if len(r) >= 3 and not excluded(r[1])]
overdue_shown = overdue_all[:OVERDUE_SHOW]
overdue_rest = len(overdue_all) - len(overdue_shown)

# ---- today's events (appointment + presence; derived blocks never appear) ----
events = [r for r in rows("RETRO_EVENTS") if len(r) >= 3 and not excluded(r[2])]
carried = []
# retro-items (JSON) wins when present: one entry per outer event, children
# folded in; a carried item goes to its own section, never under today's.
try:
    _items = json.loads(os.environ.get("RETRO_ITEMS", "") or "null")
except ValueError:
    _items = None
if isinstance(_items, list):
    events, carried = [], []
    for it in _items:
        if not isinstance(it, dict) or excluded(it.get("title") or ""):
            continue
        (carried if it.get("carried") else events).append(it)

experience_packet = json.loads(os.environ.get("RETRO_EXPERIENCES") or "{}")
experiences = experience_packet.get("items", [])
experience_blocks = {str(b) for root in experiences for m in [root] + root.get("children", []) for b in m.get("block_ids", [])}
# Lived experiences own their details; the legacy calendar recap must not ask twice.
events = [r for r in events if str(r["id"] if isinstance(r, dict) else r[0]) not in experience_blocks]
carried = [r for r in carried if str(r["id"]) not in experience_blocks]

# Identity-bearing card rows also constitute the machine walk. Legacy
# providers keep their first-line text and receive a stable provider/text id.
raw_modules = os.environ.get("RETRO_MODULE_LINES", "")
try:
    providers = json.loads(raw_modules)
except ValueError:
    providers = [dict(module="legacy", text=ln) for ln in raw_modules.splitlines() if ln.strip()]
if not isinstance(providers, list):
    providers = []
modules = []
for provider in providers:
    if not isinstance(provider, dict):
        continue
    key = provider.get("module", "legacy")
    for it in provider.get("items", [dict(text=provider.get("text", ""))]):
        if not it.get("text") or excluded(it.get("title") or it["text"]):
            continue
        item = dict(it, module=key)
        item.setdefault("kind", "module")
        item.setdefault("id", key + ":" + hashlib.sha256(it["text"].encode()).hexdigest()[:16])
        modules.append(item)

# Append-only fifth field carries the real block id for project steps.
UNTIMED_SHOW = 5
untimed = []
for r in rows("RETRO_UNTIMED"):
    if r and r[0] == "step" and len(r) >= 4:
        if not excluded(r[2]):
            untimed.append(dict(id=r[4] if len(r) > 4 else "legacy-step:" + hashlib.sha256((r[1] + ":" + r[2]).encode()).hexdigest()[:16],
                                kind="task", age_days=int(r[3]), age_clock="step.updated_at/local-date",
                                text=r[5] if len(r) > 5 else t("retro.daily.wait.finished", subject=r[2])))
    elif len(r) >= 2 and not excluded(r[1]):
        untimed.append(dict(id=r[0], kind="task",
                            text=t("retro.daily.untimed.guess", title=r[1], project=r[3])
                            if len(r) > 3 and r[3] else r[1]))

# Merge signals by (kind, block id), never by title. Only rows actually
# displayed count as duplicates. The row retains its single step-age clock;
# the merged signal is intentionally age-free.
shown_rows = {(it["kind"], str(it["id"])): it for it in untimed[:UNTIMED_SHOW]}
for r in today_tasks + overdue_shown:
    shown_rows[("task", str(r[0]))] = {}
for r in events + carried:
    shown_rows[("event", str(r["id"] if isinstance(r, dict) else r[0]))] = {}
remaining = []
for it in modules:
    identity = (it["kind"], str(it["id"]))
    if identity in shown_rows:
        row = shown_rows[identity]
        if it.get("age_clock") == "step.updated_at/local-date" and it.get("signal") == "":
            row["text"] = it["text"]
            continue
        signals = row.setdefault("signals", [])
        signal = it.get("signal") or it["text"]
        if signal not in signals:
            signals.append(signal)
    else:
        remaining.append(it)
        shown_rows[identity] = it
modules = remaining
walk = []


def walk_row(kind, identity, text, **extra):
    item = dict(id=str(identity), kind=kind, text=text, **extra)
    signals = shown_rows.get((kind, str(identity)), {}).get("signals", [])
    if signals:
        item["signals"] = signals
        item["text"] += " \u00b7 " + " \u00b7 ".join(signals)
    walk.append(item)
    return item["text"]

# D23: disposal scope == displayed scope. The count says what THIS session
# handles, not how deep the backlog is -- the "외 N건" line carries the rest.
task_n = len(today_tasks) + len(overdue_shown)
event_n = len(events) + len(carried)
experience_n = len(experiences)
# Intake checks are health module items, never tasks. An owner-requested
# regimen miss check is itself work for this retro, even on an otherwise empty
# day; unrelated passive module queues keep the existing D12 silence rule.
regimen_n = sum(it.get("module") == "module_health" and it["id"].startswith("regimen:")
                for it in modules)
unchecked_ids = {str(r[0]) for r in today_tasks + overdue_all}
unchecked_ids.update(it["id"] for it in untimed)
unchecked_ids.update(str(it["id"]) for it in modules if it["kind"] == "task")
total = max(task_n, len(unchecked_ids)) + event_n + experience_n + regimen_n

# D12: nothing to dispose of, including the backlog -> stay silent.
print("items=%d\ttasks=%d\tevents=%d" % (total, task_n, event_n))
if total == 0:
    print(json.dumps(dict(date=TODAY, items=[], body=""), ensure_ascii=False))
    raise SystemExit(0)

# ---- grouped experience quotes; other lists quote each item ----
block = []
if task_n:
    block.append(layout("list.section", name=t("retro.daily.section.tasks")))
    for r in today_tasks:
        # D23: no date on today's rows -- "9/21(월)" three times in a row is
        # noise. A date appears only on something that is NOT today.
        detail = task_detail.get(r[0], {})
        title = (t("retro.daily.task_with_time", time=detail["local_span"], title=r[1])
                 if detail.get("local_span") and detail.get("schedule_kind", "timed") == "timed" else r[1])
        block.append(layout("list.task_today", title=walk_row("task", r[0], title)))
    for r in overdue_shown:
        due = r[2]
        late = (today - d(due)).days if due else 0
        block.append(layout("list.task_overdue", date=fmt_date(due) if due else "",
                            title=walk_row("task", r[0], r[1], due=due, overdue_days=late), late=t("retro.daily.overdue", n=late)))
    if overdue_rest > 0:
        block.append(layout("list.more", more=t("retro.daily.more", n=overdue_rest)))
if events:
    if block:
        block.append(layout("list.gap"))
    block.append(layout("list.section", name=t("retro.daily.section.events")))
    for r in events:
        if isinstance(r, dict):
            block.append(layout("list.event", time=r["span"], title=walk_row("event", r["id"], r["title"], span=r.get("span") or ""))
                         if r.get("span") else layout("list.event_all_day", title=walk_row("event", r["id"], r["title"], span=r.get("span") or "")))
            continue
        start = hhmm(r[1])
        end = hhmm(r[4]) if len(r) > 4 else ""
        span = t("retro.daily.time_range", start=start, end=end) if start and end else start
        # an all-day row has no clock face, so it takes its own template
        # rather than a timed line with a hole where the time was.
        if span:
            block.append(layout("list.event", time=span, title=walk_row("event", r[0], r[2], span=span)))
        else:
            block.append(layout("list.event_all_day", title=walk_row("event", r[0], r[2], span=span)))
if carried:
    if block:
        block.append(layout("list.gap"))
    block.append(layout("list.section", name=t("retro.daily.section.carried")))
    for it in carried:
        block.append(layout("list.event", time=it["span"], title=walk_row("event", it["id"], it["title"], carried=True, span=it.get("span") or ""))
                     if it.get("span") else layout("list.event_all_day", title=walk_row("event", it["id"], it["title"], carried=True, span=it.get("span") or "")))
if experiences:
    if block:
        block.append(layout("list.gap"))
    # The kit groups the entire list and each course question in one quote.
    block.append(experience_packet.get("block", ""))
    for root in experiences:
        if root.get("next_question"):
            walk_row("experience", root["id"], root["title"], section="experiences",
                     next_question=root["next_question"], children=root.get("children", []))
if untimed:
    if block:
        block.append(layout("list.gap"))
    block.append(layout("list.section", name=t("retro.daily.section.untimed")))
    for it in untimed[:UNTIMED_SHOW]:
        block.append(layout("list.task_today", title=walk_row(it["kind"], it["id"], it["text"], section="untimed", **{k: v for k, v in it.items() if k not in ("kind", "id", "text", "signals")})))
    if len(untimed) > UNTIMED_SHOW:
        block.append(layout("list.more", more=t("retro.daily.more", n=len(untimed) - UNTIMED_SHOW)))
if modules:
    if block:
        block.append(layout("list.gap"))
    block.append(layout("list.section", name=t("retro.daily.section.modules")))
    for it in modules:
        block.append(layout("list.module", line=walk_row(it["kind"], it["id"], it["text"], **{k: v for k, v in it.items() if k not in ("kind", "id", "text", "signals")})))

# ---- count line: what tonight actually handles (D1) ----
parts = []
if task_n:
    parts.append(t("retro.daily.count.tasks", n=task_n))
if event_n:
    parts.append(t("retro.daily.count.events", n=event_n))
if experience_n:
    parts.append(t("retro.experience.count", n=experience_n))
count_line = t("retro.daily.count.line", items=t("retro.daily.count.sep").join(parts)) if parts else ""

daily_opener = experience_packet.get("opener")
opening_line = (daily_opener["text"] if daily_opener else
                t("retro.daily.opener.unchecked", n=len(unchecked_ids)) if unchecked_ids else
                "")
body = layout("opener",
             title=t("retro.daily.title"),
             opener=opening_line,
             date=fmt_date(TODAY),
             count_line=count_line,
             list="\n".join(block),
             plan_line=t("retro.daily.plan_line"),
             start_question="").rstrip()
entry_mode = "reply" if opening_line else "automatic"
first_step = ""
if entry_mode == "automatic" and walk:
    current = walk[0]
    if current["kind"] == "event":
        source = next((r for r in events + carried if isinstance(r, dict)
                       and str(r["id"]) == current["id"]), {})
        first_step = source.get("card") or (layout("card.title", title=current["text"]) +
                                           "\n\n" + t("retro.daily.ask.event", subject=current["text"]))
    elif current["kind"] == "task":
        first_step = layout("card.title", title=current["text"]) + "\n\n" + t("retro.daily.ask.done")
    else:
        first_step = current.get("next_question", {}).get("text") or current["text"]
print(json.dumps(dict(date=TODAY, items=walk, body=body, opener=daily_opener,
                     entry_mode=entry_mode, first_step=first_step), ensure_ascii=False))
PY

COMPOSED="$(
  RETRO_TODAY="$TODAY" \
  RETRO_LANG="$AGENT_LANG" \
  RETRO_AGENT_DIR="$AGENT_DIR" \
  RETRO_LAYOUT="$RETRO_DAILY_LAYOUT" \
  RETRO_EXCLUDE_PREFIXES="$EXCLUDE_PREFIXES" \
  RETRO_TASKS_TODAY="$TASK_TODAY_ROWS" \
  RETRO_TASK_DETAIL="$TASK_TODAY_DETAIL" \
  RETRO_TASKS_OVERDUE="$TASK_OVERDUE_ROWS" \
  RETRO_EVENTS="$EVENT_ROWS" \
  RETRO_ITEMS="$EVENT_ITEMS" \
  RETRO_EXPERIENCES="$EXPERIENCES" \
  RETRO_MODULE_LINES="$MODULE_LINES" \
  RETRO_UNTIMED="$UNTIMED_ROWS" \
  python3 -c "$COMPOSE_PY")"

# Split on the first newline with parameter expansion, not a pipe: under
# `pipefail` a printf whose reader (head) exits early can fail the whole
# assignment, which would abort the send for a long body.
META="${COMPOSED%%$'\n'*}"
PACKET="${COMPOSED#*$'\n'}"
BODY="$(printf '%s' "$PACKET" | python3 -c 'import json,sys; print(json.load(sys.stdin)["body"])')"
DAY_PIN=""
# The meta line is the composer's contract. A missing one means the composer
# did not run to completion -- that must be a LOUD failure, never mistaken for
# D12 silence (a silent zero would lose a real retro without a trace).
if [[ ! "$META" =~ ^items=[0-9]+ ]]; then
  echo "[retro] composer produced no meta line; nothing sent" >&2
  exit 1
fi
ITEMS="$(printf '%s' "$META" | awk -F'\t' '{print $1}' | cut -d= -f2)"

if [[ "$ITEMS" -gt 0 ]]; then
  DAY_PIN="$(day_pin)"
fi

if [[ "$JSON" -eq 1 ]]; then
  if [[ -n "$DAY_PIN" ]]; then
    printf '%s' "$PACKET" | RETRO_DAY_PIN="$DAY_PIN" python3 -c 'import json,os,sys; p=json.load(sys.stdin); p["day_pin"]=os.environ["RETRO_DAY_PIN"]; print(json.dumps(p,ensure_ascii=False))'
  else
    printf '%s\n' "$PACKET"
  fi
  exit 0
fi

# ---- D12: silence ----
if [[ "$ITEMS" -eq 0 ]]; then
  if [[ "$DRY" -eq 1 ]]; then
    echo "---- META ----"; echo "$META"
    echo "---- BODY ----"; echo "(silent: nothing to dispose of today, D12)"
  fi
  exit 0
fi

if [[ "$DRY" -eq 1 ]]; then
  echo "---- META ----"; echo "$META"
  echo "---- WALK ----"
  printf '%s' "$PACKET" | python3 -c 'import json,sys; print(json.dumps([dict(id=i["id"],kind=i["kind"]) for i in json.load(sys.stdin)["items"]],ensure_ascii=False))'
  echo "---- BUTTON ----"; echo "(none)"
  echo "---- ENTRY ----"
  printf '%s' "$PACKET" | python3 -c 'import json,sys; p=json.load(sys.stdin); print(p["entry_mode"]); print(p["first_step"] or "", end="")'
  echo
  [[ -n "$DAY_PIN" ]] && { echo "---- DAY PIN (same composer) ----"; echo "$DAY_PIN"; }
  echo "---- BODY ----"
  echo "$BODY"
  exit 0
fi

if [[ -n "$DAY_PIN" ]]; then
  "$PUSH_SH" --text "$DAY_PIN"
fi
# Persist exactly this card and deliver without a start button. The same
# writer lock protects course claims and automatic first-step delivery;
# failed delivery rolls state back. Dry modes never write walk state.
printf '%s' "$PACKET" | "$LIFE_SH" retro-walk "$TODAY" send >/dev/null
