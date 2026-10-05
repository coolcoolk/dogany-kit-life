#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""life_dashboard.py -- the kit's DETERMINISTIC weekly-review renderer.

Promoted into the kit in 2.38.0 from the life agent's instance-owned copy
(routines/lib/life_dashboard.py), generalised into two shapes:

    weekly   MAIN AREAS: the life agent's weekly as a CONVERSATION (2.39.x,
             owner 2026-10-04: start it the way the daily retro starts):
             a start card like the daily retro's (opener), then one message
             per area with one real decision each (steps), relayed by
             brief-session after the owner's yes.
    domain   PROVIDER AREAS: a domain agent's weekly. The kit draws the title
             and the flow grid; every area line comes from the domain's
             retro-sections providers (weekly lane), which the gather side
             already ran (routines/bundle/weekly-review.sh).

WHY DETERMINISTIC. A weekly review written by a model from a prose prompt was
rejected on presentation: no visual structure, no area split, one blurred
question. A prose prompt cannot guarantee a layout, so no model sits in this
send path:

    weekly-review.sh  = gather (kit_life.sh verbs only, no raw SQL)
    life_dashboard.py = render (this file)
    push.sh --text    = send (bridge sanitizer -> Telegram HTML)

MAIN AREAS (derived from what the DB actually holds):
  plans & people   timeblock kind='appointment' (appt-find)
  work             timeblock kind='task' (task-done-between / task-overdue /
                   task-untimed-open) + the retro loop marker
  life log         spend_entry (spend-find / spend-week) -- the only life
                   stream with real daily resolution, so also the flow spine
  goals            ledger_goal via the horizon-cadence gate (goal-list)

WHAT A QUESTION MAY ASK (owner 2026-10-05). Every question has to help the
owner look back on the week or plan the next one. A question that only fills
data (which project a task belonged to, whether a spend recurs, who a meal
was with so the calendar gets a row) is not asked ("why are you filling in
projects", owner).
So an area with something to show but nothing to decide carries no question;
its block rides on top of the next message that does ask (render_steps). An
area with nothing to show is not sent at all: no "nothing recorded" header,
no all-zero chart, no goal line when there is no goal. Never padded.

COPY. Every owner-facing string is an i18n key (retro.weekly.*, kit bundle;
instance override wins), resolved the way daily-retro.sh resolves its keys.
The owner's form of address is AGENT_ADDRESS (config/agent.conf, passed in by
the gather side); it opens the first question of the session only.

VISUAL CONVENTIONS
- At most ONE code block per message: the spend grid sits in a single <pre>
  inside the life-log step, and only when there is spend on file; everything
  else is plain text with bracket section headers.
- Column padding is ASCII spaces; CJK glyphs are 2 cells (_disp_width).
- Every bar is a CONSTANT glyph count (BAR_CELLS) of glyphs sharing one
  East-Asian-Width class, so the note column after it lines up on any font.
- ~40-cell width budget (phone code block). See _flow_rows.
- No HTML escaping here: push.sh routes the body through the bridge
  sanitizer, which escapes bare & < > inside <pre>.

INPUT: environment variables (TSV/newline text off kit_life.sh). See _load().
OUTPUT: the message body on stdout. Empty output = nothing to send.

Usage: life_dashboard.py weekly|opener|steps|width|domain
       life_dashboard.py week <today>                 (reviewed-week bounds)
       life_dashboard.py week-guard <start> <end> <today>   (exit 1 = refuse)
"""

import datetime
import importlib.util
import json
import os
import sys
import unicodedata

# ── layout constants ──────────────────────────────────────────────────────
BAR_CELLS = 6            # bar glyph count (constant; see module docstring)
BAR_FULL = "█"      # U+2588 FULL BLOCK      (East Asian Width 'A')
BAR_EMPTY = "▒"     # U+2592 MEDIUM SHADE    (East Asian Width 'A')
# WHY NOT U+2591 LIGHT SHADE: it is EAW 'N' (1 cell) while U+2588 is EAW 'A'
# (2 cells in a CJK font context). A bar mixing them changes rendered width
# with its fill level, so the note column after it would jag row to row.
FLOW_NOTE_CELLS = 16     # note column budget in display cells
WIDTH_BUDGET = 40        # phone code-block budget
# Korean particle pairs (with / without a final consonant) and the bullet:
# one literal each, so the copy-bearing glyphs stay in one place.
EUN = ("은", "는")
I_GA = ("이", "가")
BULLET = "·"
DOT_SEP = " %s " % BULLET


# ── i18n (same resolution order as daily-retro.sh / kit_life.py._i18n) ───
def _bundle(lang):
    root = os.environ.get("WEEKLY_AGENT_DIR", "")
    lookup = os.path.join(root, "routines", "lib", "i18n_lookup.py")
    if root and os.path.isfile(lookup):
        try:
            spec = importlib.util.spec_from_file_location("i18n_lookup", lookup)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            got = mod.merged_bundle(root, lang)
            if isinstance(got, dict):
                return got
        except Exception:
            pass
    merged = {}
    for path in (os.path.join(root, "config", "i18n", "%s.json" % lang),
                 os.path.join(root, "service", "kit_life", "i18n",
                              "%s.json" % lang)):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            merged.update(data)
    return merged


_BUNDLES = None


def t(key, **kw):
    """The key's text in AGENT_LANG (then en), formatted; '' when missing. A
    malformed instance override degrades to its raw text, never a crash."""
    global _BUNDLES
    if _BUNDLES is None:
        lang = os.environ.get("AGENT_LANG") or "en"
        _BUNDLES = [_bundle(lang)] + ([_bundle("en")] if lang != "en" else [])
    for b in _BUNDLES:
        val = b.get(key)
        if isinstance(val, str):
            if not kw:
                return val
            try:
                return val.format(**kw)
            except (KeyError, IndexError, ValueError):
                return val
    return ""


def _weekdays():
    """Mon..Sun short names from retro.daily.weekdays (stored Sun-first)."""
    names = [w.strip() for w in t("retro.daily.weekdays").split(",")]
    if len(names) != 7:
        return ["", "", "", "", "", "", ""]
    return names[1:] + names[:1]


def _disp_width(s):
    """Monospace display width: East Asian Width W, F and A count as 2,
    because Telegram renders these in a CJK font context."""
    w = 0
    for ch in str(s):
        w += 2 if unicodedata.east_asian_width(ch) in ("W", "F", "A") else 1
    return w


def _pad(s, cells):
    """Right-pad s with ASCII spaces to `cells` display cells."""
    return s + " " * max(0, cells - _disp_width(s))


def _clip(s, cells):
    """Truncate to `cells` display cells without cutting a character in half.
    Adds a single-cell ellipsis marker when anything was dropped."""
    if _disp_width(s) <= cells:
        return s
    out, w = "", 0
    for ch in s:
        cw = _disp_width(ch)
        if w + cw > cells - 1:      # keep 1 cell for the marker
            break
        out += ch
        w += cw
    return out + "…"


def _bar(value, vmax):
    """Constant-width bar: BAR_CELLS glyphs, filled proportionally. A nonzero
    value never renders as an all-empty bar; it floors at one glyph."""
    if vmax <= 0 or value <= 0:
        return BAR_EMPTY * BAR_CELLS
    n = int(round(BAR_CELLS * float(value) / float(vmax)))
    n = max(1, min(BAR_CELLS, n))
    return BAR_FULL * n + BAR_EMPTY * (BAR_CELLS - n)


def _man(won):
    """원 -> 만원, one decimal. The money unit of this surface."""
    return "%.1f" % (float(won) / 10000.0)


def _tsv(name):
    """Env var -> list of field-lists. Blank lines dropped."""
    raw = os.environ.get(name, "") or ""
    return [ln.split("\t") for ln in raw.split("\n") if ln.strip()]


def _lines(name):
    raw = os.environ.get(name, "") or ""
    return [ln.strip() for ln in raw.split("\n") if ln.strip()]


def _s(name, default=""):
    return (os.environ.get(name, "") or default).strip()


def _i(name):
    try:
        return int(_s(name) or "0")
    except ValueError:
        return 0


def _josa(word, with_final, without_final):
    """Korean particle agreement (은/는, 이/가, 을/를), decided on the LAST
    Hangul syllable's final consonant: (code - 0xAC00) % 28 != 0 means it has
    one. A non-Hangul tail falls back to the no-final form. Copy in another
    language does not use the {josa} slot, so this is inert there."""
    for ch in reversed(str(word)):
        o = ord(ch)
        if 0xAC00 <= o <= 0xD7A3:
            return with_final if (o - 0xAC00) % 28 else without_final
        if ch.isalnum():
            break
    return without_final


def _md(iso):
    """YYYY-MM-DD -> M/D (no zero padding)."""
    try:
        y, m, d = iso.split("-")
        return "%d/%d" % (int(m), int(d))
    except Exception:
        return iso


def _lead():
    """The address that opens a question ('<address>, '), or nothing."""
    address = _s("WEEKLY_ADDRESS")
    return t("retro.weekly.lead", address=address) if address else ""


# ── flow grid (the life-log step's daily spend chart) ──────────────────────────────────────
def _flow_rows(days, shown=None):
    """One line per day. Column arithmetic against the 40-cell budget:

        weekday 2 cells   (CJK weekday names are East-Asian Wide)
        space   1
        value   5 cells   right-aligned ASCII, e.g. ' 20.4'
        space   1
        bar     BAR_CELLS glyphs -- 12 cells worst case (Ambiguous = 2)
        space   2         FIXED pad, never computed against the bar
        note    <= FLOW_NOTE_CELLS (16)

        total   2+1+5+1+12+2+16 = 39 cells worst case.

    `shown` formats a day's value column (main areas: 만원 of spend).
    """
    shown = shown or (lambda d: _man(d.get("value", 0)))
    vmax = max([float(d.get("value", 0)) for d in days] or [0])
    out = []
    for d in days:
        note = _clip(d.get("label", ""), FLOW_NOTE_CELLS)
        row = "%s %5s %s  %s" % (d["wd"], shown(d),
                                 _bar(d.get("value", 0), vmax), note)
        out.append(row.rstrip())
    return out


# ── payload assembly ──────────────────────────────────────────────────────
def _load():
    """Read the gather-side env contract into one dict. Every field is raw
    kit_life.sh output the gather side already scoped to this agent."""
    m = {
        "week_start": _s("WEEK_START"),
        "week_end": _s("WEEK_END"),
        # 1 when the reviewed week is the one ending today (a Sunday run),
        # 0 when it is the week that already ended (a Monday-or-later run)
        "current": _s("WEEK_IS_CURRENT") == "1",
        "next_mon": _s("PREVIEW_MON"),
        "next_sun": _s("NEXT_SUN"),
        # flow
        "day_spend": {r[0]: int(r[1]) for r in _tsv("DAY_SPEND_TSV")
                      if len(r) >= 2 and r[1].lstrip("-").isdigit()},
        "day_label": {r[0]: r[1] for r in _tsv("DAY_LABEL_TSV")
                      if len(r) >= 2},
        # plans & people
        "appts": [(r[0], r[1]) for r in _tsv("APPT_WEEK_TSV") if len(r) >= 2],
        # work: carried-over tasks as (id, title, due); `later` = the dateless
        # backlog the daily retro's 'later' button leaves (due '')
        "done_cnt": _i("DONE_AG_CNT"),
        "overdue": _carried("OVERDUE_AG_TSV"),
        "later": _carried("LATER_TSV", dated=False),
        "loop": [(r[0], r[1]) for r in _tsv("LOOP_TSV") if len(r) >= 2],
        "loop_done": _i("LOOP_DONE"),
        "loop_total": _i("LOOP_CNT"),
        # life log
        "spend_total": _i("SPEND_TOTAL"),
        "spend_cnt": _i("SPEND_CNT"),
        "spend_prev": _i("SPEND_PREV"),
        "spend_cats": [(r[0], int(r[1]), int(r[2]))
                       for r in _tsv("SPEND_CAT_TSV")
                       if len(r) >= 3 and r[1].isdigit() and r[2].isdigit()],
        # goals (horizon-cadence gate already applied by the gather side)
        "goal_judge": _lines("GOAL_JUDGE_TXT"),
        "goal_show": _lines("GOAL_SHOW_TXT"),
        "goal_bg": _lines("GOAL_BG_TXT"),
        "goal_rows_any": _s("GOAL_ROWS_ANY") == "1",
        # next week
        "next_appts": [(r[0], r[1]) for r in _tsv("NEXT_APPT_TSV")
                       if len(r) >= 2],
        "next_holidays": _lines("HOLIDAY_TXT"),
        # finished non-routine titles (routine/other-domain titles already
        # removed). The gather side lists them with a '- ' prefix for the
        # data block; here the title itself is quoted, so the prefix comes off.
        "anchor_cand": [x[2:] if x.startswith("- ") else x
                        for x in _lines("ANCHOR_CAND")],
        "mint_block": os.environ.get("MINT_BLOCK", "") or "",
    }
    return m


def _carried(name, dated=True):
    """id<TAB>title<TAB>due (or updated date for the dateless backlog) ->
    [(id, title, due)]. A two-column row (title<TAB>due) has no id."""
    out = []
    for r in _tsv(name):
        if len(r) >= 3:
            tid, title, due = r[0], r[1], r[2]
        else:
            tid, title, due = "", r[0], (r[1] if len(r) > 1 else "")
        if title.strip():
            out.append((tid.strip(), title.strip(), due.strip() if dated else ""))
    return out


def _days(m):
    """Seven day dicts (Mon..Sun) for the flow grid."""
    try:
        mon = datetime.date.fromisoformat(m["week_start"])
    except Exception:
        return []
    wd = _weekdays()
    out = []
    for i in range(7):
        d = mon + datetime.timedelta(days=i)
        iso = d.isoformat()
        out.append({"date": iso, "wd": wd[i],
                    "value": m["day_spend"].get(iso, 0),
                    "label": m["day_label"].get(iso, "")})
    return out


def _wd_of(iso_or_dt):
    s = str(iso_or_dt)[:10]
    try:
        return _weekdays()[datetime.date.fromisoformat(s).weekday()]
    except Exception:
        return ""


def _hm_of(val):
    """'2026-08-15T17:30:00+09:00' -> '17:30'; all-day rows -> ''."""
    s = str(val)
    if "T" in s and len(s) >= 16:
        return s[11:16]
    return ""


# ── when-to-do proposal (carried-over work only) ──────────────────────────
# A slot is proposed ONLY for something the owner already carried over: an
# overdue task, or one the daily retro set aside as 'later' (owner 2026-09-21
# D26: the weekly collects them and asks when to do them, with a proposal,
# rather than forcing them into next week). Nothing is placed on the
# strength of a goal or a project alone (owner 2026-10-05: that is filling
# the calendar for the data's sake). No carried-over task -> no proposal.
# TIME RULE: with no work-day skeleton on file any weekday hour is a guess,
# so the first FREE weekend day of next week at 10:00, else the first free
# day at 20:00. A wrong-but-concrete time costs one edit.
PLACE_WEEKEND_HM = "10:00"
PLACE_WEEKDAY_HM = "20:00"


def _free_slot(m):
    try:
        mon = datetime.date.fromisoformat(m["next_mon"])
    except Exception:
        return None
    booked = {str(d)[:10] for d, _ in m["next_appts"]}
    order = [5, 6] + [0, 1, 2, 3, 4]      # Sat, Sun, then Mon..Fri
    wd = _weekdays()
    for idx in order:
        d = mon + datetime.timedelta(days=idx)
        if d.isoformat() in booked:
            continue
        hm = PLACE_WEEKEND_HM if idx >= 5 else PLACE_WEEKDAY_HM
        return {"date": d.isoformat(), "wd": wd[idx], "hm": hm}
    return None


def _carried_all(m):
    """Overdue first (oldest due first, as task-overdue orders them), then
    the dateless backlog (most recently set aside first)."""
    return m["overdue"] + m["later"]


def _place(m):
    carried = _carried_all(m)
    slot = _free_slot(m)
    if not (carried and slot):
        return None
    tid, title, due = carried[0]
    return dict(slot, task_id=tid, title=title,
                kind="overdue" if due else "later")


# -- week bounds (one derivation for both paths) ------------------------------
# The week runs Monday..Sunday. The weekly retro is scheduled for the week's
# last evening (Sunday 22:00 by default, owner 2026-10-03), so a run ON the
# last day reviews the week that is ending today. A run on any later day
# (Monday morning, or a delayed send) reviews the week that already ended.
# The preview / placement week is always the one right after the reviewed
# week. A week that has not reached its last day is never reviewed.
def review_week(today):
    d = datetime.date.fromisoformat(today)
    iso = d.isoweekday()
    end = d if iso == 7 else d - datetime.timedelta(days=iso)
    start = end - datetime.timedelta(days=6)
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "end_excl": (end + datetime.timedelta(days=1)).isoformat(),
        "this_mon": (d - datetime.timedelta(days=iso - 1)).isoformat(),
        "preview_mon": (start + datetime.timedelta(days=7)).isoformat(),
        "current": end == d,
    }


def week_guard(start, end, today):
    """'' when start..end is a whole Mon..Sun week that has reached its last
    day by `today`; otherwise the reason it must not be reviewed."""
    try:
        s = datetime.date.fromisoformat(start)
        e = datetime.date.fromisoformat(end)
        d = datetime.date.fromisoformat(today)
    except ValueError:
        return "unparseable bounds"
    if s.isoweekday() != 1 or (e - s).days != 6:
        return "not a Mon..Sun week"
    if e > d:
        return "the week has not reached its last day"
    return ""


def _week_word(m):
    return t("retro.weekly.word.this" if m["current"] else "retro.weekly.word.last")


def _next_word(m):
    # the week after the reviewed one: next week on a Sunday run, the week
    # that started today on a Monday run
    return t("retro.weekly.word.next" if m["current"] else "retro.weekly.word.this")


def _date_wd(iso):
    try:
        day = datetime.date.fromisoformat(str(iso)[:10])
    except ValueError:
        return str(iso)
    return t("retro.daily.date", m=day.month, d=day.day,
             wd=_weekdays()[day.weekday()])


def _holiday(line):
    """'- 10/09 <name>' (gather side, zero padded) -> '10/9 <name>'."""
    s = line.lstrip("- ").strip()
    head, _, name = s.partition(" ")
    try:
        mo, da = head.split("/")
        return "%d/%d %s" % (int(mo), int(da), name)
    except ValueError:
        return s


# ── render: main areas ────────────────────────────────────────────────────
# Two shapes from one payload:
#   opener  the start card (the cron push): title, the week range, one count
#           line, the plan line and the start question. It asks ONE thing:
#           whether to start. Same form and button as the daily retro.
#   steps   what follows the owner's yes, ONE message per question, each
#           question a look back on the week or a decision for the next
#           one. An area with facts but no such question has no message of
#           its own: its block opens the next message. An area with nothing
#           to show is not there at all (no "nothing recorded" filler, no
#           empty chart). brief-session relays them in order, one per turn.
def render_opener(m):
    if not m["week_start"]:
        return ""
    parts = []
    if m["appts"]:
        parts.append(t("retro.weekly.count.appts", n=len(m["appts"])))
    if m["done_cnt"]:
        parts.append(t("retro.weekly.count.done", n=m["done_cnt"]))
    if _carried_all(m):
        parts.append(t("retro.weekly.count.overdue", n=len(_carried_all(m))))
    if m["spend_cnt"]:
        parts.append(t("retro.weekly.count.spend", n=m["spend_cnt"]))
    week = _week_word(m)
    if parts:
        count_line = t("retro.weekly.count.line", week=week,
                       items=t("retro.weekly.count.sep").join(parts))
    else:
        count_line = t("retro.weekly.count.empty", week=week)
    return t("retro.weekly.layout.opener",
             title=t("retro.weekly.title"),
             range=t("retro.weekly.range", start=_date_wd(m["week_start"]),
                     end=_date_wd(m["week_end"])),
             count_line=count_line,
             plan_line=t("retro.weekly.plan_line", week=week,
                         next=_next_word(m)),
             start_question=t("retro.weekly.start_question")).rstrip()


def _step(area, facts, question=None, options=(), proposal=None):
    """question=None: a block to show, nothing to ask (it opens the next
    message). proposal: the slot a placement question offers, for the skill
    to act on (task id, date, time)."""
    return {"area": area, "facts": [f for f in facts if f is not None],
            "question": question, "options": [o for o in options if o],
            "proposal": proposal}


def _decide_options():
    return [t("retro.weekly.btn.keep"), t("retro.weekly.btn.adjust")]


def _steps_appt(m):
    if not m["appts"]:
        return []
    facts = [t("retro.weekly.appt.head", n=len(m["appts"]))]
    for when, title in m["appts"][:4]:
        hm = _hm_of(when)
        facts.append(t("retro.weekly.appt.row", wd=_wd_of(when),
                       time=(" " + hm) if hm else "", title=title))
    if len(m["appts"]) > 4:
        facts.append(t("retro.weekly.more", n=len(m["appts"]) - 4))
    # the latest appointment of the reviewed week: the freshest memory
    when, title = m["appts"][-1]
    wd = _wd_of(when)
    q = t("retro.weekly.appt.ask", title=title, josa=_josa(title, *EUN),
          wd_part=t("retro.weekly.anchor.wd_part", wd=wd) if wd else "")
    return [_step("appt", facts, q)]


def _titles(rows, total=None, limit=3):
    """Up to `limit` titles, then the rest as a count. `total` = how many
    there are in all when `rows` is only the named ones (finished routines
    are counted, not named)."""
    shown = DOT_SEP.join(rows[:limit])
    rest = max(len(rows), total or 0) - min(len(rows), limit)
    if rest > 0:
        shown += t("retro.weekly.work.more", n=rest)
    return shown


def _steps_work(m, pl):
    """What got done and what is carried over, in one block. It asks only
    when there is a decision for the next week, highest first:
      1) last retro's placement still undone -> place it again or drop it
      2) a carried-over task (overdue or set aside as 'later') -> when to do
         it, with a free slot proposed
    otherwise the block carries no question. Never asks to classify, attach
    or label anything (owner 2026-10-05)."""
    loop_todo = [x for x, st in m["loop"] if st != "done"]
    carried = _carried_all(m)
    done = m["anchor_cand"]
    n_done = max(m["done_cnt"], len(done))
    if not (n_done or carried or m["loop_total"]):
        return []
    facts = [t("retro.weekly.work.head")]
    if done:
        facts.append(t("retro.weekly.work.done", n=n_done,
                       titles=_titles(done, total=n_done)))
    elif n_done:
        # every finished task was a routine: counted, not named
        facts.append(t("retro.weekly.work.done_count", n=n_done))
    if m["loop_total"]:
        facts.append(t("retro.weekly.work.loop", total=m["loop_total"],
                       done=m["loop_done"]))
        for title, state in m["loop"][:3]:
            facts.append(t("retro.weekly.work.loop_row", title=title,
                           state=t("retro.weekly.work.loop_done")
                           if state == "done"
                           else t("retro.weekly.work.loop_todo")))
    if carried:
        facts.append(t("retro.weekly.work.overdue", n=len(carried),
                       titles=_titles([x[1] for x in carried])))
    if loop_todo:
        x = loop_todo[0]
        return [_step("work", facts,
                      t("retro.weekly.work.ask_loop", title=x,
                        josa=_josa(x, *EUN), next=_next_word(m)),
                      [t("retro.weekly.btn.place_again"),
                       t("retro.weekly.btn.drop")])]
    if pl:
        q = t("retro.weekly.work.ask_when", title=pl["title"],
              josa=_josa(pl["title"], *EUN), date=_date_wd(pl["date"]),
              hm=pl["hm"])
        return [_step("work", facts, q,
                      [t("retro.weekly.btn.place"),
                       t("retro.weekly.btn.retime"),
                       t("retro.weekly.btn.no_place")],
                      proposal={"task_id": pl["task_id"],
                                "title": pl["title"], "date": pl["date"],
                                "hm": pl["hm"], "kind": pl["kind"]})]
    return [_step("work", facts)]


def _steps_spend(m, days):
    """The week's spend at a glance. Shown, never asked about: whether a
    spend recurs or who a meal was with only fills data (owner 2026-10-05)."""
    if not m["spend_cnt"]:
        return []
    head = t("retro.weekly.spend.head", n=m["spend_cnt"],
             total=_man(m["spend_total"]))
    if m["spend_prev"]:
        head += t("retro.weekly.spend.prev", prev=_man(m["spend_prev"]),
                  week=t("retro.weekly.word.prev_this" if m["current"]
                         else "retro.weekly.word.prev_last"))
    facts = [head,
             t("retro.weekly.flow.head", unit=t("retro.weekly.flow.unit_spend")),
             "<pre>%s</pre>" % "\n".join(_flow_rows(days))]
    if m["spend_cats"]:
        facts.append(BULLET + " " + DOT_SEP.join(
            t("retro.weekly.spend.cat", cat=c[0], amount=_man(c[1]))
            for c in m["spend_cats"][:3]))
    return [_step("spend", facts)]


def _steps_goal(m):
    if not m["goal_judge"]:
        return []
    facts = [t("retro.weekly.goal.judge_head", n=len(m["goal_judge"]))]
    facts += [BULLET + " " + x for x in m["goal_judge"][:3]]
    if len(m["goal_judge"]) == 1:
        # the title is on the line above; the question does not repeat it
        q = t("retro.weekly.goal.ask_one", week=_week_word(m))
    else:
        t0 = m["goal_judge"][0]
        q = t("retro.weekly.goal.ask_many", title=t0, josa=_josa(t0, *EUN))
    return [_step("goal", facts, q, _decide_options())]


def _steps_next(m):
    """The week after the reviewed one as it stands, and an open invitation
    to add to it. No slot is proposed here: the only proposal is for
    carried-over work (work step)."""
    nxt = _next_word(m)
    facts = [t("retro.weekly.next.head", week=nxt, start=_md(m["next_mon"]),
               end=_md(m["next_sun"]), n=len(m["next_appts"]))]
    for d, title in m["next_appts"][:4]:
        facts.append("%s %s %s" % (BULLET, _date_wd(d), title))
    if len(m["next_appts"]) > 4:
        facts.append(t("retro.weekly.more", n=len(m["next_appts"]) - 4))
    for h in m["next_holidays"][:2]:
        facts.append("%s %s" % (BULLET, _holiday(h)))
    if not m["goal_judge"] and m["goal_show"]:
        facts.append(t("retro.weekly.goal.show_line",
                       titles=DOT_SEP.join(m["goal_show"][:3])))
    return [_step("next", facts, t("retro.weekly.next.none", next=nxt))]


def render_steps(m):
    days = _days(m)
    if not days:
        return []
    pl = _place(m)
    blocks = (_steps_appt(m) + _steps_work(m, pl) + _steps_spend(m, days)
              + _steps_goal(m) + _steps_next(m))
    # A block with nothing to ask opens the next message that asks (the
    # next step always asks, so nothing is left over in practice).
    steps, carry = [], []
    for st in blocks:
        if st["question"] is None:
            carry.append(st["facts"])
            continue
        st["shown"] = carry + [st["facts"]]
        carry = []
        steps.append(st)
    if carry:
        steps.append(dict(blocks[-1], shown=carry, question=""))
    # the address opens the first question of the session only
    if steps and _lead():
        steps[0]["question"] = _lead() + steps[0]["question"]
    out = []
    for i, st in enumerate(steps):
        text = "\n\n".join("\n".join(f) for f in st["shown"] if f)
        if st["question"]:
            text += "\n\n" + st["question"]
        # The agent-recommendation block (if the gather side produced one)
        # rides the LAST step verbatim, or not at all. This renderer never
        # originates a recommendation.
        if i == len(steps) - 1 and m["mint_block"].strip():
            text += "\n\n" + m["mint_block"].strip()
        item = {"area": st["area"], "text": text, "options": st["options"]}
        if st.get("proposal"):
            item["proposal"] = st["proposal"]
        out.append(item)
    return out


def render_weekly(m):
    """Opener card and every step as one plain-text transcript: the shape a
    test or a --dry reader looks at. The send path never sends this."""
    out = [render_opener(m)]
    for i, st in enumerate(render_steps(m), 1):
        out.append("---- %d/%s ----" % (i, st["area"]))
        out.append(st["text"])
        if st["options"]:
            out.append("[[OPTIONS: %s]]" % " | ".join(st["options"]))
    return "\n".join(out)


# ── render: provider areas (domain agent) ─────────────────────────────────
def render_domain():
    """A domain agent's weekly: title, the flow grid when a provider supplied
    grid rows, then each provider's area block (<= 3 lines, decision last --
    the registry enforces the cap). Empty when no provider has anything: the
    composer then stays silent."""
    sections = _lines_blocks("DOMAIN_SECTIONS")
    grid = _tsv("DOMAIN_GRID")
    if not sections and not grid:
        return ""
    ws, we = _s("WEEK_START"), _s("WEEK_END")
    parts = [t("retro.domain.weekly.title", label=_s("DOMAIN_LABEL"),
               start=_md(ws), end=_md(we))]
    unit = ""
    rows = []
    for r in grid:
        if r and r[0] == "unit" and len(r) >= 2:
            unit = r[1]
            continue
        if len(r) >= 3:
            rows.append(r)
    if rows:
        try:
            mon = datetime.date.fromisoformat(ws)
        except ValueError:
            mon = None
        if mon:
            wd = _weekdays()
            byday = {r[0]: r for r in rows}
            days = []
            for i in range(7):
                iso = (mon + datetime.timedelta(days=i)).isoformat()
                r = byday.get(iso)
                try:
                    val = float(r[1]) if r else 0.0
                except ValueError:
                    val = 0.0
                days.append({"wd": wd[i], "value": val,
                             "shown": (r[2] if r else ""),
                             "label": (r[3] if r and len(r) > 3 else "")})
            parts.append("")
            parts.append(t("retro.weekly.flow.head", start=_md(ws),
                           end=_md(we), unit=unit))
            parts.append("<pre>%s</pre>" % "\n".join(
                _flow_rows(days, shown=lambda d: _clip(d["shown"], 5))))
    for block in sections:
        parts.append("")
        parts.extend(block)
    return "\n".join(parts).rstrip()


def _lines_blocks(name):
    """Env text of blocks separated by one blank line -> [[line, ...], ...]."""
    raw = os.environ.get(name, "") or ""
    blocks, cur = [], []
    for ln in raw.split("\n"):
        if ln.strip():
            cur.append(ln.rstrip())
        elif cur:
            blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)
    return blocks


def width_report(body):
    """Diagnostic for the verification path; never used by the send path.
    Only the <pre> block is budget-bound; plain lines soft-wrap."""
    pre, prose, in_pre = [], [], False
    for ln in body.split("\n"):
        opens = "<pre>" in ln
        closes = "</pre>" in ln
        clean = ln.replace("<pre>", "").replace("</pre>", "")
        if opens or in_pre:
            pre.append(clean)
        else:
            prose.append(clean)
        if opens and not closes:
            in_pre = True
        if closes:
            in_pre = False
    out = ["<pre> block -- budget %d cells (hard)" % WIDTH_BUDGET]
    for ln in pre:
        w = _disp_width(ln)
        out.append("%3d %s |%s|" % (w, "OVER" if w > WIDTH_BUDGET else "ok  ",
                                    ln))
    out.append("")
    widths = sorted((_disp_width(x) for x in prose if x.strip()), reverse=True)
    out.append("plain text -- soft-wrapped, no hard budget. "
               "max=%d cells, lines=%d" % (widths[0] if widths else 0,
                                           len(widths)))
    return "\n".join(out)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "weekly"
    if cmd == "weekly":
        sys.stdout.write(render_weekly(_load()) + "\n")
    elif cmd == "opener":
        body = render_opener(_load())
        if body:
            sys.stdout.write(body + "\n")
    elif cmd == "steps":
        sys.stdout.write(json.dumps(render_steps(_load()), ensure_ascii=False)
                         + "\n")
    elif cmd == "width":
        sys.stdout.write(width_report(render_weekly(_load())) + "\n")
    elif cmd == "domain":
        body = render_domain()
        if body:
            sys.stdout.write(body + "\n")
    elif cmd == "week" and len(sys.argv) == 3:
        w = review_week(sys.argv[2])
        sys.stdout.write("%s %s %s %s %s %d\n" % (
            w["start"], w["end"], w["end_excl"], w["this_mon"],
            w["preview_mon"], 1 if w["current"] else 0))
    elif cmd == "week-guard" and len(sys.argv) == 5:
        why = week_guard(*sys.argv[2:5])
        if why:
            sys.stderr.write(why + "\n")
            sys.exit(1)
    else:
        sys.stderr.write("usage: life_dashboard.py [weekly|opener|steps|width|"
                         "domain] | week <today> | week-guard <start> <end> "
                         "<today>\n")
        sys.exit(2)


if __name__ == "__main__":
    main()
