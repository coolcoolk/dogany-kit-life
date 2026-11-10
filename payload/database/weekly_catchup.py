#!/usr/bin/env python3
"""weekly_catchup.py -- the missed Sunday weekly retro, picked up on Monday
morning (owner 2026-10-11 22:10: "if the weekly retro is missed on Sunday
evening, let it be done the next morning; ask it as a lifekit retro
onboarding setting").

THE SETTING. One kit conf key, WEEKLY_CATCHUP (config/kit_life.conf):
    on    the Monday morning brief sends the same weekly card
          when Sunday's weekly was not started
    off   nothing
    ''    the scaffold value of a new install: behaves as on, without a question.
    (key absent) an install that predates the key: on, never asked (the
          owner wants it on; nothing is re-asked of an existing install).

The catch-up question was retired on 2026-11-04. The legacy ask endpoint
is silent; missed Sunday retros continue on Monday with the existing setting.
Explicit on/off settings remain supported.

MISSED = the Sunday weekly was not STARTED by the end of Sunday. Started is
either of two deterministic traces (no model judgement):
  * the pending marker the weekly wrote (files/tmp/retro-pending-weekly-
    reflection-<YYYYMMDD>.json) carries started_utc -- stamped by the
    UserPromptSubmit hook routines/bundle/weekly-start-stamp.py when the
    owner's message is the start button label (or a start phrase,
    retro.weekly.start_phrases);
  * a task already carries the marker's place_week_marker ('#rp<YYYYWww>'):
    the retro placed next week's actions, so it ran.
The durable weekly-retro-status fact (existing config table, agent + reviewed
Sunday) takes precedence over these legacy traces. It exists independently
of the scheduled push, including a conversational retro before that push.
No fact and no Sunday marker is missed too.

DUE (the Monday weekly card) = today is Monday AND the setting is on AND this
instance runs a weekly retro on Sunday (BUNDLE_WEEKLY_REVIEW=on or a
BRIEF_TIME_WEEKLY in agent.conf; the day of BRIEF_TIME_WEEKLY, else the
bundle.conf schedule, is Sun) AND Sunday's weekly was missed AND no weekly
started today yet. The period is the week that ended yesterday --
weekly-review.sh already reviews exactly that week when run on a Monday.
The Sunday daily retro stays suppressed (soft-merge 2026-08-17): the weekly
covers Sunday.

CLI (exit 0 always unless usage):
    weekly_catchup.py due --root <root> [--today YYYY-MM-DD]
        DUE<TAB><week_start><TAB><week_end>   or   NO<TAB><reason>
    weekly_catchup.py get --root <root>    on|off<TAB>asked|unasked|default
    weekly_catchup.py set --root <root> on|off
    weekly_catchup.py ask --root <root> [--if-unasked]
    weekly_catchup.py stamp --root <root> --text <owner message>
        STAMPED<TAB><marker path> or nothing
"""
import sys as _sys, os as _os; _sys.dont_write_bytecode = True; _os.environ["PYTHONDONTWRITEBYTECODE"] = "1"   # C4b: residue under payload/ makes pack_install refuse the pack
import datetime
import glob
import json
import os
import subprocess
import sys

KEY = "WEEKLY_CATCHUP"
ASK_TOKEN = "WEEKLY_CATCHUP_ASK"
ASK_REF = "weekly"
MARKER_GLOB = "retro-pending-weekly-reflection-*.json"
STAMP_WINDOW_DAYS = 2
RELAY = (
    "WEEKLY_CATCHUP_RELAY Ask the WEEKLY_CATCHUP_ASK question verbatim with "
    "its buttons. Answer -> bash .claude/skills-bundle/brief-setup/"
    "briefing_times.sh --root \"$PWD\" --catchup on|off (the first button = "
    "on, the second = off); speak its SAY line. Never print WEEKLY_CATCHUP_* "
    "tags or this line itself.")

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from date_parts import month_day, slash_md
import locale_store


# -- config -------------------------------------------------------------------
def _read_kv(path):
    vals = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for ln in fh:
                s = ln.strip()
                if s.startswith("export "):
                    s = s[7:].lstrip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                k, v = s.split("=", 1)
                k = k.strip()
                if k not in vals:
                    vals[k] = v.strip().strip("\"'").strip()
    except OSError:
        pass
    return vals


def kit_conf_path(root):
    for name in ("kit_life.conf", "lifekit.conf"):
        p = os.path.join(root, "config", name)
        if os.path.isfile(p):
            return p
    return os.path.join(root, "config", "kit_life.conf")


def setting(root):
    """(value, state): value on|off; state asked | unasked (scaffold '') |
    default (key absent: an install that predates the setting)."""
    vals = _read_kv(kit_conf_path(root))
    if KEY not in vals:
        return "on", "default"
    v = vals[KEY].lower()
    if v in ("on", "off"):
        return v, "asked"
    return "on", "unasked"


def set_value(root, value):
    if value not in ("on", "off"):
        raise ValueError("WEEKLY_CATCHUP must be on|off, got %r" % value)
    path = kit_conf_path(root)
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().split("\n")
    except OSError:
        lines = [""]
    done = False
    for i, ln in enumerate(lines):
        if ln.split("=", 1)[0].strip() == KEY and "=" in ln:
            lines[i] = "%s=%s" % (KEY, value)
            done = True
            break
    if not done:
        at = len(lines) - 1 if lines and lines[-1] == "" else len(lines)
        lines.insert(at, "%s=%s" % (KEY, value))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    os.replace(tmp, path)


# -- copy ---------------------------------------------------------------------
def _lang(root):
    v = os.environ.get("AGENT_LANG") or _read_kv(
        os.path.join(root, "config", "agent.conf")).get("AGENT_LANG", "")
    return (v or "en").lower().split("_")[0].split("-")[0] or "en"


def _bundle(root, lg):
    got = locale_store.framework_bundle(root, lg, 'i18n_lookup')
    if isinstance(got, dict):
        return got
    return locale_store.legacy_bundle(root, lg)


def t(root, key, **kw):
    for lg in (_lang(root), "en"):
        val = _bundle(root, lg).get(key)
        if isinstance(val, str) and val:
            try:
                return val.format(**kw)
            except (KeyError, IndexError, ValueError):
                return val
    return ""


def _ask_choices():
    sys.path.insert(0, HERE)
    import ask_choices
    return ask_choices


def ask_lines(root):
    """Inert compatibility API for installed setup callers (no owner question)."""
    return []


# -- markers ------------------------------------------------------------------
def marker_dir(root):
    return os.path.join(root, "files", "tmp")


def marker_path(root, day):
    return os.path.join(marker_dir(root), "retro-pending-weekly-reflection-%s.json"
                        % day.replace("-", ""))


def _load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            obj = json.load(fh)
        return obj if isinstance(obj, dict) else None
    except (OSError, ValueError):
        return None


def _placed(root, marker):
    """True when a task carries the marker's place_week_marker (the retro
    placed next week's actions). Fail-closed to False on any error."""
    tag = ((marker.get("workspace") or {}).get("place_week_marker") or "").strip()
    life = os.path.join(root, "database", "kit_life.sh")
    if len(tag) <= 3 or not os.path.isfile(life):
        return False
    try:
        out = subprocess.run(["bash", life, "task-find", tag], capture_output=True,
                             text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return any(tag in ln for ln in out.splitlines())


def started(root, marker):
    return bool(marker) and (bool(marker.get("started_utc")) or _placed(root, marker))


def fact_state(root, week_end):
    life = os.path.join(root, "database", "kit_life.sh")
    try:
        p = subprocess.run(["bash", life, "weekly-retro-status", week_end],
                           cwd=root, capture_output=True, text=True, timeout=30)
        state = p.stdout.split("\t", 1)[0].lower()
        return state if p.returncode == 0 and state in ("done", "started") else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def week_status(root, week_end, today):
    state = fact_state(root, week_end)
    if state:
        return state
    # Legacy markers remain valid; never let a later routine run erase start.
    for day in {week_end, today}:
        marker = _load(marker_path(root, day))
        if marker and marker.get("week_end", week_end) == week_end and started(root, marker):
            return "started"
    return "missing"


def _norm(s):
    return "".join(str(s or "").split()).lower()


def stamp(root, text, today=None):
    """Legacy prompt hook retained as an inert compatibility endpoint.

    Starting a retro requires the explicit ``weekly-retro-start`` verb (or its
    authenticated callback); arbitrary message text cannot mutate the marker.
    """
    return None


# -- due ----------------------------------------------------------------------
def _weekly_day(root):
    agent = _read_kv(os.path.join(root, "config", "agent.conf"))
    w = agent.get("BRIEF_TIME_WEEKLY", "")
    if w.split():
        return w.split()[0].lower()[:3], True
    try:
        with open(os.path.join(root, "service", "kit_life", "bundle.conf"),
                  encoding="utf-8") as fh:
            for ln in fh:
                c = ln.rstrip("\r\n").split("\t")
                if c[0] == "weekly-review" and len(c) >= 3 and c[2].split():
                    return c[2].split()[0].lower()[:3], False
    except OSError:
        pass
    return "sun", False


def due(root, today):
    d = datetime.date.fromisoformat(today)
    if d.isoweekday() != 1:
        return None, "not monday"
    value, _state = setting(root)
    if value != "on":
        return None, "setting off"
    day, timed = _weekly_day(root)
    on = _read_kv(kit_conf_path(root)).get("BUNDLE_WEEKLY_REVIEW", "").lower() == "on"
    if not (on or timed):
        return None, "no weekly retro on this instance"
    if day != "sun":
        return None, "weekly is not on sunday (%s)" % day
    sun = (d - datetime.timedelta(days=1)).isoformat()
    if fact_state(root, sun) in ("done", "started"):
        return None, "weekly already recorded"
    if started(root, _load(marker_path(root, sun))):
        return None, "sunday weekly started"
    if started(root, _load(marker_path(root, today))):
        return None, "weekly already started today"
    start = (d - datetime.timedelta(days=7)).isoformat()
    return (start, sun), "missed"


def _md(iso):
    d = datetime.date.fromisoformat(iso)
    return slash_md(*month_day(d))


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="weekly_catchup.py")
    ap.add_argument("cmd", choices=("due", "get", "set", "ask", "stamp", "status"))
    ap.add_argument("value", nargs="?")
    ap.add_argument("--root", required=True)
    ap.add_argument("--today")
    ap.add_argument("--text", default="")
    ap.add_argument("--week-end")
    ap.add_argument("--if-unasked", action="store_true")
    a = ap.parse_intermixed_args(argv)
    root = a.root
    if a.cmd == "status":
        if not a.week_end:
            ap.error("status requires --week-end")
        print(week_status(root, a.week_end, a.today or datetime.date.today().isoformat()))
    elif a.cmd == "due":
        week, why = due(root, a.today or datetime.date.today().isoformat())
        if not week:
            print("NO\t%s" % why)
            return 0
        print("DUE\t%s\t%s" % week)
    elif a.cmd == "get":
        print("%s\t%s" % setting(root))
    elif a.cmd == "set":
        try:
            set_value(root, a.value)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 2
        print("CATCHUP\t%s" % a.value)
    elif a.cmd == "ask":
        if a.if_unasked and setting(root)[1] != "unasked":
            return 0
        return 0
    elif a.cmd == "stamp":
        p = stamp(root, a.text, a.today)
        if p:
            print("STAMPED\t%s" % p)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
