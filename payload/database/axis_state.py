#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""axis_state.py -- kit_life axis state (축 상태): resolvers + the weekly-retro
lapse section.

Worklog: 2026-09-17-axis-board-pin-spec.md (section 3 signal contract, 3-1
declaration surface, 3-2 three states, section 9 direction change). PURPOSE.md
P3/P4: attrition on an internalized axis is shown, never nagged about.

SURFACE (spec section 9, owner 2026-09-26 "조망판 너무 번잡하다 주간회고 단위로
보는게 좋겠어"). There is no pinned board. The only output is one section in
the WEEKLY retro, through the 2.28.0 retro extension point
(service/kit_life/retro-sections.tsv -> service/kit_life/axis-lapsed ->
weekly_lines() here). It names ONLY axes that are lapsed as of the retro day
-- newly lapsed this week or still lapsed -- one short line each:

    소비 기록이 3주째 멈췄어요

Active axes are omitted. Never-started axes are silent. Nothing lapsed -> no
section at all (no "all good" filler). The lines state; they never ask (the
retro conversation owns any follow-up, spec section 2 mirror discipline).

WHAT LIVES HERE, AND WHY IT IS CODE NOT CONFIG
    service/kit_life/axis.conf DECLARES the rows (axis_id, label_key,
    resolver name, stale_after_days). The QUERY for each axis lives here,
    in RESOLVERS, because the per-axis traps measured on live data (spec
    section 3) are query-logic bugs, not values -- a conf file cannot be
    unit-tested and cannot avoid them:
      (a) person   persons.created_at is when someone was REGISTERED, not
                   when the owner last saw them. last_person_contact reads
                   event_persons -> block instead.
      (b) execution block carries open-ended future rows (a 2099-12-30
                   sentinel among them). last_block_past bounds every read
                   to <= today so a sentinel can never win a MAX().
      (c) meal     block.start_at is NULL for source_module='diet' rows
                   (meal writes use block.date, never start_at -- CHECK
                   (start_at IS NULL OR date IS NULL) enforces the split at
                   the schema level). last_meal_record reads block.date.
                   spend has no block column at all: last_spend_record reads
                   spend_entry.date.
    Each resolver reads its OWN source table; there is no shared "the axis
    column" because one does not exist across modules.

THREE STATES, NOT TWO (spec 3-2)
    'unstarted' (never recorded) must not be reported as elapsed days --
    counting days on a record that never existed manufactures a nag out of
    nothing and violates PURPOSE P1. classify_axis_state() is the one place
    that turns (last_date, today, stale_after_days) into {active, lapsed,
    unstarted}; every resolver defers to it.

LANE (spec section 4): rule lane only. Same (conn, today) in, same lines out
-- no model call anywhere in this file. `today` is always caller-supplied (the
provider defaults it to the process-local date, which is the KST date the
kit's date columns are written in), never computed inside a resolver.

English/ASCII only (repo convention for code comments; axis.conf labels and
the sentence templates are kit i18n keys, shipped in
config/i18n/<lang>.bundle.json and read through the framework's layered
lookup -- see load_i18n).
"""

import datetime
import json
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AGENT_DIR = os.path.dirname(SCRIPT_DIR)
AXIS_CONF_PATH = os.path.join(AGENT_DIR, 'service', 'kit_life', 'axis.conf')
I18N_DIR = os.path.join(AGENT_DIR, 'service', 'kit_life', 'i18n')
I18N_LOOKUP = os.path.join(AGENT_DIR, 'routines', 'lib', 'i18n_lookup.py')

STATE_ACTIVE = 'active'
STATE_LAPSED = 'lapsed'
STATE_UNSTARTED = 'unstarted'

# retro_sections.SECTION_MAX_LINES: a module section is a glance, at most 3
# lines. Self-capped here so the kit never has to cut us mid-list.
MAX_LINES = 3


def _days_between(earlier_iso, later_iso):
    """later - earlier, in whole days. Both args are YYYY-MM-DD strings."""
    a = datetime.date.fromisoformat(earlier_iso)
    b = datetime.date.fromisoformat(later_iso)
    return (b - a).days


def classify_axis_state(last_date, today, stale_after_days):
    """(state, last_date, days_elapsed) -- the single place the 3-state
    contract (spec 3-2) is decided.

    last_date=None (no record has ever existed for this axis) always
    returns days_elapsed=None: 'unstarted' is not a stale reading zeroed
    out, it is the absence of a clock to read at all.
    """
    if last_date is None:
        return (STATE_UNSTARTED, None, None)
    elapsed = _days_between(last_date, today)
    state = STATE_LAPSED if elapsed >= stale_after_days else STATE_ACTIVE
    return (state, last_date, elapsed)


# -- Resolvers -- each reads its own source, bounded to <= today -------------
# Signature: resolver(conn, today, stale_after_days)
#            -> (state, last_date, days_elapsed).

def last_block_past(conn, today, stale_after_days):
    """execution axis: block(source_module='life'), the schedule/task/
    appointment calendar. Trap (b): block carries open-ended future rows
    (a 2099-12-30 sentinel among 130 live future rows measured 2026-09-17).
    Bounding to <= today is not an optimization here, it is the fix -- an
    unbounded MAX() reports the sentinel year as "last activity".
    Cancelled rows (settled_outcome='abandoned') do not count as activity,
    matching appt_find's existing exclusion."""
    row = conn.execute(
        "SELECT MAX(d) FROM ("
        "  SELECT COALESCE(date, substr(start_at, 1, 10)) AS d"
        "  FROM block"
        "  WHERE source_module = 'life'"
        "    AND (settled_outcome IS NULL OR settled_outcome <> 'abandoned')"
        ") WHERE d IS NOT NULL AND d <= ?;",
        (today,)).fetchone()
    return classify_axis_state(row[0] if row else None, today, stale_after_days)


def last_person_contact(conn, today, stale_after_days):
    """person axis: event_persons -> block, NOT persons.created_at.
    Trap (a): persons.created_at is the date someone was REGISTERED (measured
    2026-06-27 on live data), not the date they were last seen; event_persons
    joined to the block they were tagged on (measured 2026-09-09 on the same
    data) is the actual contact signal. Bounded to <= today for the same
    future-sentinel reason as (b)."""
    row = conn.execute(
        "SELECT MAX(d) FROM ("
        "  SELECT COALESCE(b.date, substr(b.start_at, 1, 10)) AS d"
        "  FROM event_persons ep"
        "  JOIN block b ON b.id = ep.event_id"
        ") WHERE d IS NOT NULL AND d <= ?;",
        (today,)).fetchone()
    return classify_axis_state(row[0] if row else None, today, stale_after_days)


def last_meal_record(conn, today, stale_after_days):
    """meal axis: block(source_module='diet'). Trap (c): meal rows never
    populate block.start_at (the diet write path always sets `date`, never
    `start_at`). Reading start_at here would silently see zero rows on every
    live meal. Read block.date directly instead."""
    row = conn.execute(
        "SELECT MAX(date) FROM block"
        " WHERE source_module = 'diet' AND date IS NOT NULL AND date <= ?;",
        (today,)).fetchone()
    return classify_axis_state(row[0] if row else None, today, stale_after_days)


def last_spend_record(conn, today, stale_after_days):
    """spend axis: spend_entry.date (YYYY-MM-DD, KST). Trap (c) again: the
    ledger lane has no block start_at to read -- its own table's date is the
    signal (spec section 3). The axis stays a kit row even though deep spend
    work is a market module (PURPOSE P5 boundary: the row shows, the pack
    digs). Expense rows only: an income row (a salary landing) is not the
    owner keeping a spending record."""
    row = conn.execute(
        "SELECT MAX(date) FROM spend_entry"
        " WHERE kind = 'expense' AND date <= ?;",
        (today,)).fetchone()
    return classify_axis_state(row[0] if row else None, today, stale_after_days)


def last_goal_touch(conn, today, stale_after_days):
    """goal axis: goal_target / commitment valid_from, NOT goal.created_at.
    A goal that is actively managed shows up as REVISIONS (a new goal_target
    or commitment version, each with its own valid_from, GLOB-constrained to
    full UTC-Z timestamps so substr(...,1,10) is always a calendar date)."""
    row = conn.execute(
        "SELECT MAX(d) FROM ("
        "  SELECT substr(valid_from, 1, 10) AS d FROM goal_target"
        "  WHERE valid_from IS NOT NULL"
        "  UNION ALL"
        "  SELECT substr(valid_from, 1, 10) AS d FROM commitment"
        "  WHERE valid_from IS NOT NULL"
        ") WHERE d <= ?;",
        (today,)).fetchone()
    return classify_axis_state(row[0] if row else None, today, stale_after_days)


def last_routine_run(conn, today, stale_after_days):
    """routine axis: block rows MATERIALIZED from a generator (generator_id
    IS NOT NULL, dated by rec_date) that actually SETTLED done. Not a count
    of definitions (spec 3-1: 'whether it ran') -- five definitions and zero
    completions read unstarted, not five."""
    row = conn.execute(
        "SELECT MAX(rec_date) FROM block"
        " WHERE generator_id IS NOT NULL AND settled_outcome = 'done'"
        "   AND rec_date IS NOT NULL AND rec_date <= ?;",
        (today,)).fetchone()
    return classify_axis_state(row[0] if row else None, today, stale_after_days)


def last_episode(conn, today, stale_after_days):
    """episode axis: event.created_at (the experience container). Spec 3-2's
    forcing case: the machinery is wired but `event` measured 0 rows live --
    this resolver must return 'unstarted', not 'lapsed', on that state."""
    row = conn.execute(
        "SELECT MAX(substr(created_at, 1, 10)) FROM event"
        " WHERE substr(created_at, 1, 10) <= ?;",
        (today,)).fetchone()
    return classify_axis_state(row[0] if row else None, today, stale_after_days)


RESOLVERS = {
    'last_block_past': last_block_past,
    'last_person_contact': last_person_contact,
    'last_meal_record': last_meal_record,
    'last_spend_record': last_spend_record,
    'last_goal_touch': last_goal_touch,
    'last_routine_run': last_routine_run,
    'last_episode': last_episode,
}


# -- axis.conf parsing (declaration reader, spec 3-1) ------------------------

class AxisRow(object):
    __slots__ = ('axis_id', 'label_key', 'resolver', 'stale_after_days')

    def __init__(self, axis_id, label_key, resolver, stale_after_days):
        self.axis_id = axis_id
        self.label_key = label_key
        self.resolver = resolver
        self.stale_after_days = stale_after_days


def parse_axis_conf(path=AXIS_CONF_PATH):
    """[AxisRow, ...] in file order. Blank lines and '#'-comment lines are
    skipped -- same convention as bundle.conf's reader."""
    rows = []
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                continue
            parts = line.rstrip('\n').split('\t')
            if len(parts) != 4:
                raise ValueError(
                    "axis.conf: expected 4 tab-separated fields, got %d: %r"
                    % (len(parts), line))
            axis_id, label_key, resolver, stale_raw = parts
            rows.append(AxisRow(axis_id, label_key, resolver,
                                int(stale_raw)))
    return rows


def load_i18n(lang='ko'):
    """The kit's keys for `lang`: the framework's i18n_lookup.merged_bundle
    (DGN-1789: kit layers config/packs/<id>.i18n.<lang>.json over the
    framework default) when that helper is installed; else (OLD FRAMEWORK,
    SUNSET at framework >= 2.6.0 / DGN-1789) the kit's installed bundle copy
    service/kit_life/i18n/<lang>.json."""
    if os.path.isfile(I18N_LOOKUP):
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                'axis_state_fw_i18n_lookup', I18N_LOOKUP)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            merged = mod.merged_bundle(AGENT_DIR, lang)
            if isinstance(merged, dict) and merged:
                return merged
        except Exception:
            pass
    path = os.path.join(I18N_DIR, '%s.json' % lang)
    with open(path, encoding='utf-8') as fh:
        return json.load(fh)


def resolve_all(conn, axis_rows, today, on_error=None):
    """[(AxisRow, state, last_date, days_elapsed), ...] in axis.conf order.
    An unregistered resolver name raises -- axis.conf and RESOLVERS drifting
    apart is a build bug. A resolver whose source table is missing on this
    install (sqlite OperationalError) drops only that axis and is reported
    through on_error(axis_id, exc); it never reads as unstarted or lapsed."""
    import sqlite3
    out = []
    for axis_row in axis_rows:
        fn = RESOLVERS.get(axis_row.resolver)
        if fn is None:
            raise KeyError(
                "axis.conf names resolver %r, not registered in "
                "axis_state.RESOLVERS" % axis_row.resolver)
        try:
            state, last_date, days_elapsed = fn(
                conn, today, axis_row.stale_after_days)
        except sqlite3.OperationalError as exc:
            if on_error is not None:
                on_error(axis_row.axis_id, exc)
            continue
        out.append((axis_row, state, last_date, days_elapsed))
    return out


# -- Weekly retro section (spec section 9) -----------------------------------

def _lapse_line(strings, label, days_elapsed):
    if days_elapsed >= 7:
        return strings['axis.retro.lapsed.weeks'].format(
            label=label, n=days_elapsed // 7)
    return strings['axis.retro.lapsed.days'].format(
        label=label, n=days_elapsed)


def weekly_lines(resolved, strings):
    """The section body: [line, ...], possibly empty (= no section).

    Only STATE_LAPSED rows speak. Most recent lapse first (smallest elapsed:
    what newly stopped this week is the news; a long-standing lapse follows),
    ties in axis.conf order. More than MAX_LINES lapsed axes -> the first
    MAX_LINES-1 get their own line and the rest share one closing line, so
    no lapsed axis silently falls off the glance."""
    lapsed = [(days, i, axis_row) for i, (axis_row, state, _, days)
              in enumerate(resolved) if state == STATE_LAPSED]
    lapsed.sort(key=lambda t: (t[0], t[1]))

    def label(axis_row):
        return strings.get(axis_row.label_key, axis_row.axis_id)

    if len(lapsed) <= MAX_LINES:
        return [_lapse_line(strings, label(r), d) for d, _, r in lapsed]
    head = lapsed[:MAX_LINES - 1]
    rest = lapsed[MAX_LINES - 1:]
    lines = [_lapse_line(strings, label(r), d) for d, _, r in head]
    lines.append(strings['axis.retro.lapsed.rest'].format(
        labels=strings['axis.retro.label_sep'].join(
            label(r) for _, _, r in rest)))
    return lines


def weekly_section(conn, today, lang='ko', axis_conf_path=AXIS_CONF_PATH,
                   on_error=None):
    """Read axis.conf -> run resolvers -> the lapse lines. The one entry the
    retro provider and the tests both call."""
    resolved = resolve_all(conn, parse_axis_conf(axis_conf_path), today,
                           on_error=on_error)
    return weekly_lines(resolved, load_i18n(lang))
