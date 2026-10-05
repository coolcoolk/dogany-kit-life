#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DGN-240 routine roller (spec v3 section 2, T9) -- materialization + renewal
engine. Also the single home of the shared instance machinery (materialize /
regen path / group cancel / occupied) that the kit_life routine verbs call
inline (registration / rule change / resume), so nightly and inline paths run
the SAME code.

Nightly pass order (2.1, fixed):
    (1) lapse -> (2) rule-conformance -> (3) materialize
    (Monday run adds (4) renewal/retire + (5) roller_log prune)

Entry: launchd com.telegram-skill-bot.<agent-label>.routine-roller, daily
03:40 local time, via routines/routine-roller.sh (flock single-execution).
This module never
pushes to Telegram itself -- anomaly push lines go to stdout and the shell
wrapper routes them through routines/push.sh (notify seam, 2.5).

Deadman property (2.5): materialization and valid_until extension BOTH live
here and only here -- if this process dies, future instances stop at the
28-day horizon and autonomous windows stop extending. No infinite production
path exists.

English/ASCII only.
"""

import argparse
import datetime
import os
import sqlite3
import sys
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import kit_life  # noqa: E402
import routine_projection as rp  # noqa: E402
import routine_evidence  # noqa: E402

HORIZON_DAYS = 28
LAPSE_MARGIN_HOURS = 24
ROLLER_LOG_KEEP_DAYS = 180
OWNING_AGENT = "user"   # per-instance: replace with the agent's label
ROLLER_BY = "roller"
DISPLAY_TZ = "Asia/Seoul"

# JUDGMENT SCOPE. routine_def can be SHARED: instances materialize from the
# one table, and that crossing is DELIBERATE (P1-A -- either roller keeps
# the other's calendar filled, which is also this module's deadman
# property). JUDGING is not shared. Unscoped, each instance ran the anomaly
# gate over every owner's routines and pushed on whatever it found
# (measured live: one instance pushed an anomaly about a presence routine
# the OTHER instance owned).
#
# Identity source = ../.instance.conf DOGANY_AGENT_NAME -- the same key
# mirror/adapter.py resolves an instance by, and the same vocabulary
# routine_def.owning_agent uses. OWNING_AGENT above is NOT an identity
# source: its "replace with the agent's label" placeholder was never
# carried out in any live tree.
JUDGE_AGENT_ENV = "ROLLER_JUDGE_OWNING_AGENT"


def judge_owning_agent():
    """This instance's routine_def.owning_agent value, or None.

    None means UNSCOPED -- exactly the historical behaviour. That is the
    deliberate failure direction: an unreadable config must not silently
    gag the judge, because a detector that speaks about nothing looks
    identical to a detector with nothing to report. The stderr line below
    is the only thing that distinguishes them, so it must stay loud.

    Env override wins (set it EMPTY to force the legacy unscoped read);
    same env-then-file precedence agent_conf uses."""
    val = os.environ.get(JUDGE_AGENT_ENV)
    if val is not None:
        return val.strip() or None
    path = os.path.normpath(os.path.join(SCRIPT_DIR, "..", ".instance.conf"))
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("DOGANY_AGENT_NAME="):
                    val = line.split("=", 1)[1].strip()
                    if val:
                        return val
    except OSError:
        pass
    sys.stderr.write(
        "routine_roller: WARNING DOGANY_AGENT_NAME unresolved (%s) -- "
        "anomaly judgment runs UNSCOPED over every owner's routines\n"
        % path)
    return None

# SPEC 2026-08-14 title normalization: the work-presence ACTION is
# single-sourced as the WORK label -- office vs home is distinguished by the
# PLACE, NEVER by the title. A presence def titled with any work-presence
# label (work / commute / remote-work / work-at-office) emits instances titled
# with the WORK label, so the calendar's action=title renders clean
# (work + office place / work + home place). Non-work presence titles (a named
# trip etc.) are left as-is.
#
# i18n tier-5: the labels are ASCII TOKENS in code; the localized display
# strings live in WORK_PRESENCE_TITLE_I18N and are resolved at match time.
# Korean is never a dict key nor a comparison target. Same in-layer pattern as
# kit_life.TRAVEL_TITLE_I18N (ko values unicode-escaped so the source stays
# ASCII, ko is the fallback lang). The ko WORK value is the same string as the
# `mirror.label.work` catalog entry consumed by mirror/adapter.py
# (is_hidden_presence compares an instance title against it) -- keep in lockstep.
WORK_PRESENCE_WORK = "work"
WORK_PRESENCE_COMMUTE = "commute"
WORK_PRESENCE_REMOTE = "remote_work"
WORK_PRESENCE_OFFICE = "work_office"

WORK_PRESENCE_TITLE_I18N = {
    # ko values are unicode-escaped to honor this module's ASCII-only source
    # contract: work / commute / remote-work / work-at-office.
    "ko": {
        WORK_PRESENCE_WORK:    u"\ud68c\uc0ac",
        WORK_PRESENCE_COMMUTE: u"\ucd9c\uadfc",
        WORK_PRESENCE_REMOTE:  u"\uc7ac\ud0dd",
        WORK_PRESENCE_OFFICE:  u"\ud68c\uc0ac \uc0ac\ubb34\uc2e4",
    },
    "en": {
        WORK_PRESENCE_WORK:    u"Work",
        WORK_PRESENCE_COMMUTE: u"Commute",
        WORK_PRESENCE_REMOTE:  u"Remote work",
        WORK_PRESENCE_OFFICE:  u"Work at office",
    },
}


def work_presence_labels(lang="ko"):
    """token -> display map of the work-presence labels for `lang`
    (unknown lang -> ko, mirroring kit_life._travel_title)."""
    return WORK_PRESENCE_TITLE_I18N.get(lang, WORK_PRESENCE_TITLE_I18N["ko"])


def normalize_presence_title(title, lang="ko"):
    """Collapse any work-presence label to the WORK label; every other title
    (including None) passes through unchanged."""
    labels = work_presence_labels(lang)
    if title in tuple(labels.values()):
        return labels[WORK_PRESENCE_WORK]
    return title


# self-locating default mirror state db (adapter convention: module home
# database/ -> ../mirror/mirror_state.db).
#
# THIRD HOME for this path (mirror/adapter.py:STATE_DB_PATH and
# kit_life.py:_STATE_DB_PATH are the other two).  All three must honour the
# same override or the escape hatch leaks: measured 2026-09-09, patching the
# first two still left payload/mirror/mirror_state.db appearing on a suite run,
# because test_mirror_inbound_suite and test_routine_edit_preservation reach
# THIS copy.  A real-data sqlite inside the product payload is what compat-lint
# C5 refuses -- and the path is .gitignore'd, so `git status` calls the tree
# clean while the installer refuses it.  git-tracked is not the definition of
# what ships.
# Unset -> byte-identical to the prior behaviour (zero-delta).
def _mirror_state_default(script_dir):
    """Resolve the mirror state db path, refusing to mint one inside a PACK SOURCE.

    The state file is self-locating (module home -> ../mirror/mirror_state.db).
    In a live instance that is the instance directory and correct.  In the pack
    SOURCE tree the same rule writes a real-data sqlite INTO the product
    payload, which compat-lint C5 refuses -- and the path is .gitignore'd, so
    `git status` reports the tree clean while the installer refuses it.
    Measured 2026-09-09: this blocked an install three separate times, and
    opening an env override (MIRROR_STATE_DB) did not fix it because the CALLERS
    have to set it; six test entry points still did not.

    So the DEFAULT is made safe instead of asking every caller to remember.
    Discriminator, measured not guessed: a pack source carries pack-manifest.json
    beside payload/; a live instance does not (verified on live instances).
    Inside a pack source we fall back to a temp path -- the process still works,
    it just stops writing into the product.
    """
    explicit = os.environ.get("MIRROR_STATE_DB")
    if explicit:
        return explicit
    home = os.path.normpath(os.path.join(script_dir, "..", "mirror"))
    payload_parent = os.path.normpath(os.path.join(script_dir, "..", ".."))
    if os.path.isfile(os.path.join(payload_parent, "pack-manifest.json")):
        import tempfile
        d = os.path.join(tempfile.gettempdir(), "dogany-packsrc-mirror-state")
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            return os.path.join(home, "mirror_state.db")
        return os.path.join(d, "mirror_state.db")
    return os.path.join(home, "mirror_state.db")

STATE_DB_PATH = _mirror_state_default(SCRIPT_DIR)

# minimal outbox DDL (byte-compatible subset of mirror/mirror_state.sql):
# used only when the state db does not exist yet (fresh sandbox).
_STATE_MIN_DDL = """
CREATE TABLE IF NOT EXISTS mirror_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mirror_outbox (
    id           INTEGER PRIMARY KEY,
    event_ulid   TEXT NOT NULL,
    op           TEXT NOT NULL DEFAULT 'sync',
    status       TEXT NOT NULL DEFAULT 'queued',
    lease_at     TEXT,
    attempts     INTEGER NOT NULL DEFAULT 0,
    requeues     INTEGER NOT NULL DEFAULT 0,
    dead         INTEGER NOT NULL DEFAULT 0,
    last_error   TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    CHECK (status IN ('queued','claimed','pushed','failed'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_outbox_ulid_pending
    ON mirror_outbox(event_ulid) WHERE status IN ('queued','claimed');
CREATE TABLE IF NOT EXISTS push_snapshot (
    event_ulid   TEXT PRIMARY KEY,
    surface      TEXT NOT NULL,
    field_hash   TEXT NOT NULL,
    field_json   TEXT NOT NULL DEFAULT '{}',
    pushed_at    TEXT NOT NULL
);
"""


def open_state_db(path=None):
    """Open (or create) the mirror state db for outbox writes + read-only
    never-mirrored lookups. Returns None on failure -- callers treat an
    unavailable state db as 'tombstone path, never DELETE' (spec 4.2).

    No explicit path -> MIRROR_ENQUEUE_TO when set, else the self-located
    default (same precedence as kit_life._mirror_state_path).
    A db minted here is only an outbox: it holds no mirror targets, so
    _never_mirrored never reads its silence as proof (2026-09-27: an empty
    db minted on the health instance let a rule-regen hard-DELETE rows Ag
    had pushed)."""
    path = path or kit_life._mirror_enqueue_to() or STATE_DB_PATH
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.executescript(_STATE_MIN_DDL)
        conn.commit()
        return conn
    except sqlite3.Error:
        return None


def outbox_enqueue(state_conn, event_ulid):
    """Idempotent enqueue -- identical semantics to adapter.outbox_enqueue
    (unique pending index collapses duplicates)."""
    if state_conn is None:
        return False
    now = kit_life.now_utc()
    try:
        state_conn.execute(
            "INSERT INTO mirror_outbox(event_ulid, op, status, created_at, "
            "updated_at) VALUES(?, 'sync', 'queued', ?, ?)",
            (event_ulid, now, now))
        state_conn.commit()
        return True
    except sqlite3.IntegrityError:
        state_conn.rollback()
        return False


def roller_log(conn, category, recurrence_id=None, detail=None, now=None):
    conn.execute(
        "INSERT INTO roller_log(ts, recurrence_id, category, detail) "
        "VALUES(?,?,?,?);",
        (now or kit_life.now_utc(), recurrence_id, category, detail))
    conn.commit()


def _today(tz_name=DISPLAY_TZ):
    return datetime.datetime.now(ZoneInfo(tz_name)).strftime("%Y-%m-%d")


def _local_date(ts_utc, tz_name=DISPLAY_TZ):
    dt = datetime.datetime.strptime(ts_utc, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc)
    return dt.astimezone(ZoneInfo(tz_name)).strftime("%Y-%m-%d")


def _minus_hours(ts_utc, hours):
    dt = datetime.datetime.strptime(ts_utc, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc) - timedelta(hours=hours)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _plus_days(date_str, days):
    return (datetime.date.fromisoformat(date_str)
            + timedelta(days=days)).isoformat()


def upper(defn, today):
    """Spec 2.2 materialization cap:
        min(today+28d, valid_until)   if end_date IS NULL
        min(today+28d, end_date)      if end_date IS NOT NULL
    (valid_until never binds an end_date def -- F-2 separation).
    OQ-B3 ruling (2026-07-10): the 28-day horizon bound is EXCLUSIVE
    -- last materializable day = today+27, i.e. max 28 daily instances
    (charter '4 weeks' / spec 5.3 ceil(28/interval) is normative; the 2.2
    inclusive pseudocode was the drift -- spec erratum logged in the
    ticket). end_date / valid_until caps stay inclusive."""
    horizon = _plus_days(today, HORIZON_DAYS - 1)
    cap = defn["end_date"] if defn["end_date"] is not None else defn["valid_until"]
    return min(horizon, cap)


def occupied(conn, rid, d):
    """Spec 2.2 exact SQL (NOT inside the row predicate, NULL-safe IS):
    every settled/exception/user-skip row occupies its date; the ONLY
    carve-out is the rule-regen tombstone."""
    return conn.execute(
        "SELECT EXISTS ("
        "    SELECT 1 FROM timeblock"
        "    WHERE recurrence_id = :rid AND rec_date = :d"
        "      AND NOT (settled_outcome IS 'abandoned'"
        "               AND settled_by IS 'rule-regen')"
        ");", {"rid": rid, "d": d}).fetchone()[0] == 1


def include_day(defn, d, today, now):
    """Spec 2.2 start-day rule (m1+m11 unified guard):
        d > today  -> true
        d == today -> all_day: true (day not over yet)
                      timed:   rule instant > now (never born-expired)
        d < today  -> false (past instants are never created)."""
    if d > today:
        return True
    if d < today:
        return False
    if defn["schedule_kind"] == "all_day":
        return True
    sa, _ea, _sk = rp.rule_instants(defn, d)
    return sa > now


def active_defs(conn):
    old = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM routine_def WHERE status='active' "
            "AND cadence IS NOT NULL ORDER BY id;")]
    finally:
        conn.row_factory = old


def _all_cadence_defs(conn):
    old = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM routine_def WHERE cadence IS NOT NULL "
            "ORDER BY id;")]
    finally:
        conn.row_factory = old


def advisory_displaced_check(conn, defn, d, sa, ea, now=None):
    """Spec 2.2 displaced advisory metric (M6 redefinition): instances are
    non-occupying and ALWAYS insert; if a timed occurrence overlaps a live
    exclusive row (a pre-booked appointment won the advisory warning), log
    roller_log('displaced', rid, d) -- (rid, rec_date) deduped. Pure metric:
    no blocking, no forcing."""
    if defn["schedule_kind"] != "timed":
        return False
    rid = defn["recurrence_id"]
    dup = conn.execute(
        "SELECT 1 FROM roller_log WHERE category='displaced' "
        "AND recurrence_id=? AND detail=? LIMIT 1;", (rid, d)).fetchone()
    if dup:
        return False
    hit = conn.execute(
        "SELECT e.ulid FROM timeblock e WHERE " + kit_life.LIVE_FILTER +
        " AND ? < " + kit_life.EFF_END_SQL + " AND e.start_at < ? "
        "AND (e.recurrence_id IS NULL OR e.recurrence_id != ?) LIMIT 1;",
        (sa, ea, rid)).fetchone()
    if hit:
        roller_log(conn, "displaced", rid, d, now=now)
        return True
    return False


def materialize_def(conn, state_conn, defn, today=None, now=None,
                    floor_date=None):
    """Spec 2.2 materialize pass for ONE def (idempotent; shared verbatim by
    the nightly roller and the inline registration/rule-change paths).
    Returns the list of local dates materialized in this call."""
    if defn["status"] != "active" or defn["cadence"] is None:
        return []
    today = today or _today(defn["display_tz"])
    now = now or kit_life.now_utc()
    if state_conn is None:
        state_conn = open_state_db()
    rid = defn["recurrence_id"]
    # G-2 (grill-final): rule_effective_from joins the lower bound -- the
    # nightly run must never seed NEW-rule instances inside the protection
    # window [today, effective_from) (F-A(ii): that window lives under the
    # OLD rule; existing rows there stay as-is and occupy their dates).
    lo = max(today, defn["start_date"],
             defn["rule_effective_from"] or today, floor_date or today)
    hi = upper(defn, today)
    # DGN-579 E2: the def's kind rides onto every instance (presence defs
    # emit presence rows through timeblock_add's guarded presence path).
    def_kind = defn.get("kind") or "task"
    # SPEC 2026-08-14 title normalization (see WORK_PRESENCE_TITLE_I18N):
    # a work-presence label collapses to the WORK label; office vs home is
    # distinguished by PLACE, never by title.
    inst_title = defn["title"]
    if def_kind == "presence":
        inst_title = normalize_presence_title(inst_title)
    made = []
    for dd in rp.occurrence_dates(defn, lo, hi):
        d = dd.isoformat()
        if not include_day(defn, d, today, now):
            continue
        if occupied(conn, rid, d):
            continue
        sa, ea, sk = rp.rule_instants(defn, d)
        if def_kind == "presence":
            # DGN-579 E2 (T-010 fix locus): supersession NON-GENERATION
            # probe -- a live USER-AUTHORED presence overlapping [sa, ea)
            # wins; the roller skips emission for that date entirely.
            cee = kit_life._cand_eff_end(sa, ea, 0)
            hit = conn.execute(
                "SELECT 1 FROM timeblock e WHERE e.kind='presence' "
                "AND e.settled_at IS NULL AND e.recurrence_id IS NULL "
                "AND e.block_class IS NULL "
                "AND e.start_at < ? AND ? < " + kit_life.EFF_END_SQL +
                " LIMIT 1;", (cee, sa)).fetchone()
            if hit:
                roller_log(conn, "displaced", rid, d, now=now)
                continue
        # P1-A: instances inherit the routine_def's owner so recurring rows
        # attribute to the right domain agent (health -> warg, work -> ag),
        # not the module default. Fall back to the const only if the def owner
        # is null/empty (defensive: routine_def.owning_agent is NOT NULL).
        inst_owner = defn["owning_agent"] or OWNING_AGENT
        try:
            eid = kit_life.timeblock_add(
                conn, kind=def_kind, title=inst_title, schedule_kind=sk,
                start_at=sa, end_at=ea, owning_agent=inst_owner,
                created_by=ROLLER_BY, completion_rule="manual",
                area_id=defn["area_id"], display_tz=defn["display_tz"],
                recurrence_id=rid, rec_date=d, is_routine=1,
                place_id=defn.get("place_id"),
                # DGN-273: stamp the def's notify policy onto the instance
                # (remind poller reads per-row; NULL = legacy default).
                # .get: tolerate a pre-007 def dict (columns absent -> NULL).
                notify_policy=defn.get("notify_policy"),
                notify_lead_min=defn.get("notify_lead_min"))
        except sqlite3.IntegrityError as e:
            # n3: inline-vs-nightly race -- the later INSERT hits
            # idx_event_rec_live. Benign: skip this (rid, d), keep looping.
            if "idx_event_rec_live" in str(e) or "UNIQUE" in str(e):
                roller_log(conn, "materialized", rid,
                           "unique_skip d=%s" % d, now=now)
                continue
            raise
        if eid is None:
            # DGN-579: presence atomic guard lost (another live presence
            # covers the span) -- skip the date, log, keep looping.
            roller_log(conn, "displaced", rid, d, now=now)
            continue
        ulid = conn.execute("SELECT ulid FROM timeblock WHERE id=?;",
                            (eid,)).fetchone()[0]
        # 053: copy this def's default packing list onto the fresh instance,
        # right after the instance id is known (design report §4). Idempotent
        # via INSERT OR IGNORE + the sub_event partial unique index -- a
        # re-run of this date (n3 race above) cannot duplicate prep rows.
        kit_life.routine_prep_materialize(conn, defn["id"], eid, inst_owner,
                                          now=now)
        # DGN-579 D-C3 (owner 2026-07-26, reverses the E2 kind gate):
        # presence instances enqueue like every other row -- they surface
        # as GCal all-day banners on the travel calendar.
        outbox_enqueue(state_conn, ulid)
        roller_log(conn, "materialized", rid, d, now=now)
        if def_kind != "presence":
            # slot advisory stays actionable-only (presence holds no slot)
            advisory_displaced_check(conn, defn, d, sa, ea, now=now)
        made.append(d)
    return made


# ---------------------------------------------------------------------------
# regen path (spec 4.2 step 2 row handling; shared by routine_update, pause,
# and the nightly conformance pass)
# ---------------------------------------------------------------------------

def _never_mirrored(state_conn, row):
    """Never-mirrored test (spec 4.2): bookkeeping columns NULL AND no
    push_snapshot AND no pending outbox row. State db unavailable ->
    False (tombstone path, never DELETE). State db that cannot prove reach
    (no mirror targets engraved) -> False: the row is soft-tombstoned and
    the mirror owner's poll sweep pushes the cancel
    (kit_life._state_db_proves_reach)."""
    if row["gcal_event_id"] is not None or row["gtask_id"] is not None:
        return False
    if not kit_life._state_db_proves_reach(state_conn):
        return False
    try:
        snap = state_conn.execute(
            "SELECT 1 FROM push_snapshot WHERE event_ulid=? LIMIT 1;",
            (row["ulid"],)).fetchone()
        if snap:
            return False
        pending = state_conn.execute(
            "SELECT 1 FROM mirror_outbox WHERE event_ulid=? "
            "AND status IN ('queued','claimed') LIMIT 1;",
            (row["ulid"],)).fetchone()
        return pending is None
    except sqlite3.Error:
        return False


def regen_path(conn, state_conn, row, now=None):
    """One cancel-set row (4.2 step 2, row-per-txn). Returns
    'deleted' | 'cancelled' | 'skipped'."""
    if _never_mirrored(state_conn, row):
        conn.execute("BEGIN IMMEDIATE;")
        try:
            # 036: route through the axis-aware delete -- on v3.2 the
            # timeblock name is a read-only view and the row lives in block
            # (kit_life._tb_delete handles both axes + the version CAS).
            n = kit_life._tb_delete(
                conn, row["id"], where_extra=" AND settled_at IS NULL",
                cas_version=row["version"])
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        if n == 1:
            if state_conn is not None:
                # purge any pending outbox rows for the deleted ulid
                state_conn.execute(
                    "DELETE FROM mirror_outbox WHERE event_ulid=? "
                    "AND status IN ('queued','claimed');", (row["ulid"],))
                state_conn.commit()
            roller_log(conn, "regen_delete", row["recurrence_id"],
                       row["rec_date"], now=now)
            return "deleted"
        return "skipped"           # CAS moved under us -> converge nightly
    res = kit_life.cancel(conn, row["id"], row["version"], "rule-regen")
    if res == kit_life.MutationResult.APPLIED:
        # DGN-579 D-C3: presence included -- its banner needs the cancelled
        # tombstone push (E2 kind gate reversed).
        outbox_enqueue(state_conn, row["ulid"])
        roller_log(conn, "regen_cancel", row["recurrence_id"],
                   row["rec_date"], now=now)
        return "cancelled"
    return "skipped"


def _fetch_rows(conn, sql, params):
    old = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.row_factory = old


def cancel_set_regen(conn, state_conn, defn, effective_from, now=None):
    """4.2 step 2 cancel-set: roller-produced, unsettled, non-exception,
    version=0 rows from effective_from on. Returns (cancelled, deleted)."""
    rows = _fetch_rows(
        conn,
        "SELECT * FROM timeblock WHERE recurrence_id=? AND rec_date >= ? "
        "AND settled_at IS NULL AND rec_exception=0 "
        "AND created_by='roller' AND version=0;",
        (defn["recurrence_id"], effective_from))
    cancelled = deleted = 0
    for row in rows:
        r = regen_path(conn, state_conn, row, now=now)
        if r == "cancelled":
            cancelled += 1
        elif r == "deleted":
            deleted += 1
    return cancelled, deleted


def retire_group_cancel(conn, state_conn, defn, today, now=None,
                        by="routine-retire"):
    """5.4 retire cleanup: ALL future unsettled instances, exceptions
    INCLUDED (retire means stop -- user-shaped rows go too). 'Future' =
    rec_date >= today OR actual end still ahead (covers exception rows
    moved forward past their rec_date). Past/settled rows untouched
    (lifelog). Idempotent -- re-runnable as crash backstop."""
    now = now or kit_life.now_utc()
    rows = _fetch_rows(
        conn,
        "SELECT * FROM timeblock WHERE recurrence_id=? AND settled_at IS NULL "
        "AND (rec_date >= ? OR (end_at IS NOT NULL AND end_at > ?));",
        (defn["recurrence_id"], today, now))
    n = 0
    for row in rows:
        res = kit_life.cancel(conn, row["id"], row["version"], by)
        if res == kit_life.MutationResult.APPLIED:
            if row["kind"] != "presence":     # DGN-579 E2 kind gate
                outbox_enqueue(state_conn, row["ulid"])
            n += 1
    if n:
        roller_log(conn, "retired", defn["recurrence_id"],
                   "group_cancel n=%d" % n, now=now)
    return n


def rerun_group_cancel(conn, state_conn, defn, today, now=None):
    """2.4 non-active backstop: re-run the idempotent group-cancel sweep of
    pause/retire for any leftover live rows (crash recovery)."""
    if defn["status"] == "paused":
        return sum(cancel_set_regen(conn, state_conn, defn, today, now=now))
    if defn["status"] == "retired":
        return retire_group_cancel(conn, state_conn, defn, today, now=now)
    return 0


# ---------------------------------------------------------------------------
# nightly passes
# ---------------------------------------------------------------------------

def lapse_pass(conn, state_conn, now=None):
    """Spec 2.3: settle overdue roller instances by ACTUAL schedule end
    (end_at + 24h margin; rec_date takes no part -- F1). all_day (GTasks
    routing) -> cancel_abandoned('roller-lapse') + outbox (surface delete);
    timed (Calendar routing) -> untouched, expired graphite lifelog.
    rec_exception=1 rows are untouchable.
    DGN-579 D-C3/A3: presence is excluded -- presence never auto-settles
    (spec A3: no expiry, no auto-complete; settled only by supersession or
    explicit cancel), and an auto-abandon here would tombstone past stay
    banners off GCal 24h after the stay ends (history must remain, like
    past appointments)."""
    now = now or kit_life.now_utc()
    cutoff = _minus_hours(now, LAPSE_MARGIN_HOURS)
    rows = _fetch_rows(
        conn,
        "SELECT * FROM timeblock WHERE recurrence_id IS NOT NULL "
        "AND created_by='roller' AND rec_exception=0 "
        "AND kind != 'presence' "
        "AND settled_at IS NULL AND end_at IS NOT NULL AND end_at < ? "
        "AND schedule_kind='all_day';", (cutoff,))
    n = 0
    for row in rows:
        res = kit_life.cancel(conn, row["id"], row["version"], "roller-lapse")
        if res == kit_life.MutationResult.APPLIED:
            outbox_enqueue(state_conn, row["ulid"])
            roller_log(conn, "lapse_settled", row["recurrence_id"],
                       row["rec_date"], now=now)
            n += 1
        # CAS_FAIL = inbound settle won the race -> harmless convergence (m3)
    return n


def _frozen(conn, defn):
    """2.4 churn breaker state: a conformance_frozen log row newer than the
    def's last owner edit freezes conformance for that def. Release =
    routine_update (CAS bump refreshes updated_at)."""
    return conn.execute(
        "SELECT 1 FROM roller_log WHERE category='conformance_frozen' "
        "AND recurrence_id=? AND ts > ? LIMIT 1;",
        (defn["recurrence_id"], defn["updated_at"])).fetchone() is not None


def _regen_on_previous_run(conn, defn, run_id):
    return kit_life._conformance_regen_on_previous_run(
        conn, "conformance", defn["recurrence_id"], defn["updated_at"], run_id)


def stale_exceptions(conn, defn, floor, up):
    """2026-08-24 edit-preservation fix -- tell "the owner changed this day"
    apart from "the template changed".

    rec_exception=1 detaches a day from rule governance permanently. That is
    right for the first event (the owner moved ONE day; the roller must not
    argue). It is WRONG to let it silently swallow the second event: if the
    template itself is edited later, that is a fresh decision about every
    future day, and an exception day would keep an intent the owner has since
    superseded -- without anyone ever being told.

    The two events are already separately timestamped, so no new column is
    needed:
      - timeblock.updated_at  = when the OWNER last touched that instance
      - routine_def.updated_at = when the TEMPLATE was last changed
        (routine_update CAS-bumps it; roller bookkeeping deliberately does
        NOT -- see renewal_pass G-1, so a valid_until extension can never
        masquerade as a template change)

    updated_at(instance) >  updated_at(def)  -> the owner's edit is the newer
        decision. Still current intent. Leave it alone, say nothing.
    updated_at(instance) <= updated_at(def)  -> the template moved AFTER the
        owner pinned that day. STALE: the day is still not rewritten (never
        clobber an owner edit), but it is surfaced so the owner can decide.

    Pure read. Returns the list of stale rec_dates."""
    rows = conn.execute(
        "SELECT rec_date FROM timeblock WHERE recurrence_id=? "
        "AND rec_exception=1 AND settled_at IS NULL "
        "AND rec_date >= ? AND rec_date <= ? "
        "AND updated_at <= ? ORDER BY rec_date;",
        (defn["recurrence_id"], floor, up, defn["updated_at"])).fetchall()
    return [r[0] for r in rows]


def conformance_pass(conn, state_conn, today=None, now=None):
    """Spec 2.4 rule-conformance (nightly self-heal, F-A time scope):
    scope floor = max(today, rule_effective_from) -- past lifelog rows and
    the pre-effective_from window are INVIOLABLE, forever.

    rec_exception=1 rows are out of scope by predicate (they are the owner's
    hand-set days). They are not silently forgotten either -- stale ones are
    logged as 'exception_stale' for the owner-facing readout."""
    today = today or _today()
    now = now or kit_life.now_utc()
    run_id = kit_life._conformance_run_start(conn, "conformance", now)
    total = 0
    for defn in _all_cadence_defs(conn):
        if defn["status"] != "active":
            rerun_group_cancel(conn, state_conn, defn, today, now=now)
            continue
        if _frozen(conn, defn):
            continue
        floor = max(today, defn["rule_effective_from"])
        up = upper(defn, today)
        rows = _fetch_rows(
            conn,
            "SELECT * FROM timeblock WHERE recurrence_id=? "
            "AND created_by='roller' AND settled_at IS NULL "
            "AND rec_exception=0 AND version=0 "
            "AND rec_date >= ? AND rec_date <= ?;",
            (defn["recurrence_id"], floor, up))
        regen_count = 0
        for row in rows:
            conforms = (
                rp.occurs(defn["cadence"], row["rec_date"])
                and defn["start_date"] <= row["rec_date"]
                and ((row["start_at"], row["end_at"], row["schedule_kind"])
                     == rp.rule_instants(defn, row["rec_date"])))  # SAME fn
            if not conforms:
                regen_path(conn, state_conn, row, now=now)
                roller_log(conn, "conformance_regen", defn["recurrence_id"],
                           row["rec_date"], now=now)
                regen_count += 1
        total += regen_count
        # edit-preservation signal (2026-08-24): never rewrite an owner's
        # hand-set day, but never swallow a later template change either.
        # (rid, rec_date) deduped so a stale day pings once, not nightly.
        for d in stale_exceptions(conn, defn, floor, up):
            dup = conn.execute(
                "SELECT 1 FROM roller_log WHERE category='exception_stale' "
                "AND recurrence_id=? AND detail=? LIMIT 1;",
                (defn["recurrence_id"], d)).fetchone()
            if not dup:
                roller_log(conn, "exception_stale", defn["recurrence_id"], d,
                           now=now)
        if regen_count > 0 and _regen_on_previous_run(conn, defn, run_id):
            # M-A churn breaker: 2 consecutive nightly regen runs -> freeze
            # conformance for this def (materialization NOT frozen). Release
            # path = owner decision via routine_update.
            roller_log(conn, "conformance_frozen", defn["recurrence_id"],
                       "regen_count=%d" % regen_count, now=now)
    kit_life._conformance_run_end(conn, "conformance", run_id, now)
    return total


def renewal_pass(conn, state_conn, today=None, now=None):
    """Spec 2.5 weekly (Monday) pass. Returns (extended, retired,
    anomaly_push_lines). Silent extension; anomaly = exception-based ping,
    ack-suppressed; NO automatic pause/stop (retro is the only lifecycle
    gate). Push delivery is the caller's seam (shell wrapper -> push.sh)."""
    import json as _json
    today = today or _today()
    now = now or kit_life.now_utc()
    extended = retired = 0
    old = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        defs = [dict(r) for r in conn.execute(
            "SELECT * FROM routine_def WHERE status='active' ORDER BY id;")]
    finally:
        conn.row_factory = old
    for defn in defs:
        if defn["end_date"] is None:
            new_valid = _plus_days(today, 56)
            # G-1 (grill-final): pure roller BOOKKEEPING -- updated_at (and
            # version, OQ-B7) untouched. Only the owner's routine_update
            # refreshes updated_at and thereby releases conformance_frozen
            # (spec 2.4: owner decision is the ONLY release path).
            conn.execute(
                "UPDATE routine_def SET valid_until=? WHERE id=?;",
                (new_valid, defn["id"]))
            conn.commit()
            roller_log(conn, "extended", defn["recurrence_id"], new_valid,
                       now=now)
            extended += 1
        elif today > defn["end_date"]:
            conn.execute(
                "UPDATE routine_def SET status='retired', updated_at=? "
                "WHERE id=?;", (now, defn["id"]))
            conn.commit()
            retire_group_cancel(conn, state_conn, defn, today, now=now)
            roller_log(conn, "retired", defn["recurrence_id"],
                       "end_date reached", now=now)
            retired += 1
    # anomaly scan: this instance's active defs, end_date defs included
    # (n1 -- anomaly + retro is the only brake for a dead end_date
    # routine). The extension / retire loop above stays owner-agnostic on
    # purpose: valid_until is the materialization horizon and its
    # cross-instance redundancy is the P1-A design. JUDGMENT is not.
    push_lines = []
    judge_owner = judge_owning_agent()
    health = kit_life.routine_health(conn, window=28, as_of=today,
                                     owning_agent=judge_owner)
    by_rid = {h["rid"]: h for h in health}
    for defn in defs:
        if today > (defn["end_date"] or "9999-12-31"):
            continue                      # just retired above
        if judge_owner is not None and defn["owning_agent"] != judge_owner:
            # Not ours. This skip is load-bearing beyond the push: with
            # the health rows already scoped away, by_rid would simply
            # miss, `anomaly` would read None, and the improvement-week
            # branch below would clear the OTHER instance's anomaly_ack
            # from no evidence whatsoever.
            continue
        h = by_rid.get(defn["recurrence_id"])
        anomaly = h["anomaly"] if h is not None else None
        if anomaly not in ("rate", "streak"):
            # improvement week: an existing ack auto-clears (2.5). m-4: only
            # a NO-anomaly week is an improvement -- conformance_frozen is
            # not (keeps the ack). G-1: bookkeeping clear leaves updated_at
            # untouched (freeze release stays owner-only, 2.4).
            if anomaly is None and defn["anomaly_ack"] is not None:
                conn.execute(
                    "UPDATE routine_def SET anomaly_ack=NULL WHERE id=?;",
                    (defn["id"],))
                conn.commit()
            continue
        suppressed = False
        if defn["anomaly_ack"]:
            try:
                ack = _json.loads(defn["anomaly_ack"])
            except ValueError:
                ack = {}
            if ack.get("type") == h["anomaly"]:
                if (h["anomaly"] == "rate" and h["rate"] is not None
                        and h["rate"] > float(ack.get("rate") or 0) - 0.10):
                    suppressed = True
                if (h["anomaly"] == "streak"
                        and h["consec_miss"] < int(ack.get("streak") or 0) + 3):
                    suppressed = True
        roller_log(conn, "anomaly_ping", defn["recurrence_id"],
                   ("suppressed " if suppressed else "") + h["anomaly"],
                   now=now)
        if not suppressed:
            push_lines.append(
                "%s: %s (rate=%s, consec_miss=%s)"
                % (h["title"], h["anomaly"], h["rate"], h["consec_miss"]))
    # roller_log prune (m10): 180-day retention
    cutoff_d = _plus_days(today, -ROLLER_LOG_KEEP_DAYS)
    conn.execute("DELETE FROM roller_log WHERE ts < ?;",
                 (cutoff_d + "T00:00:00Z",))
    conn.commit()
    return extended, retired, push_lines


def run_nightly(conn, state_conn, today=None, now=None, weekly=None):
    """Full nightly run, fixed order (2.1). Returns a summary dict."""
    today = today or _today()
    now = now or kit_life.now_utc()
    if weekly is None:
        weekly = datetime.date.fromisoformat(today).weekday() == 0  # Monday
    summary = {"today": today, "lapsed": 0, "conformance_regen": 0,
               "materialized": 0, "extended": 0, "retired": 0,
               "evidence_settled": 0, "anomaly_push": []}
    summary["lapsed"] = lapse_pass(conn, state_conn, now=now)
    # SETTLE BEFORE JUDGE. lapse_pass settles all_day rows only -- timed
    # instances are left unsettled by design (2.3), so the domain record
    # is the ONLY thing that can resolve them. This has to run before
    # renewal_pass reads routine_health: measured live, the real
    # settlement landed hours AFTER the roller, and that gap is exactly
    # what produced the false "missed N times running" push (see
    # routine_evidence docstring).
    summary["evidence_settled"], _ = routine_evidence.evidence_settle_pass(
        conn, kit_life, today=today, log=roller_log)
    summary["conformance_regen"] = conformance_pass(conn, state_conn,
                                                    today=today, now=now)
    for defn in active_defs(conn):
        summary["materialized"] += len(
            materialize_def(conn, state_conn, defn, today=today, now=now))
    if weekly:
        ext, ret, lines = renewal_pass(conn, state_conn, today=today, now=now)
        summary["extended"], summary["retired"] = ext, ret
        summary["anomaly_push"] = lines
    # Travel conformance at pass end. kit_life.travel_conformance_pass has
    # declared itself "roller-called at pass end" since it was written, but
    # NOTHING called it: measured 2026-09-17, the only caller was the
    # travel-refresh CLI verb, and neither this roller nor mirror-poll named
    # it. Live consequence on the owner's instance: the last conformance run
    # was 2026-08-22 (hand-typed), so every presence date the roller minted
    # after that window got no move/prep block -- the owner's commute prep
    # silently stopped existing and nobody found out for 26 days.
    #
    # Placement is AFTER materialization on purpose: the def-level attach
    # probes a REPRESENTATIVE LIVE INSTANCE, so a date minted in this same run
    # must already exist before the pass looks for it.
    #
    # Best-effort by design. The travel lane is a derived convenience; a fault
    # there must never cost the owner the routine instances this run already
    # committed. The summary key is always present so a caller can tell
    # "ran, nothing to do" from "did not run" -- the distinction whose absence
    # made the 26-day gap unobservable.
    summary["travel_conformance"] = None
    try:
        tc = kit_life.travel_conformance_pass(
            conn, state_conn=state_conn, today=today, now=now)
        summary["travel_conformance"] = tc
        # BEAT -- written on EVERY run, including a no-op one. The existing
        # travel_conformance_regen row is conditional (logged only when
        # regen > 0), so "0 rows for N days" was ambiguous between "nothing
        # needed doing" and "the pass never ran". It was the latter for 26
        # days and the log could not say so. This row is the unconditional
        # heartbeat that makes lane silence measurable.
        roller_log(conn, "travel_conformance_beat", None,
                   "engraved=%s regen=%s frozen=%s attached=%s needs=%s"
                   % (tc.get("engraved"), tc.get("regen"), tc.get("frozen"),
                      tc.get("attached"), tc.get("needs")), now=now)
    except Exception as e:                                  # noqa: BLE001
        roller_log(conn, "travel_conformance_error", None,
                   "%s: %s" % (type(e).__name__, e), now=now)
    return summary


def _moved_today(summary):
    """True iff the nightly run cancelled/re-minted rows or attached travel
    blocks (conformance regen, travel conformance) -- the passes that can
    touch TODAY's rows. Plain materialization (future days) and lapse
    (yesterday and older) move nothing on today's card."""
    tc = summary.get("travel_conformance") or {}
    return bool(summary.get("conformance_regen") or tc.get("regen")
                or tc.get("attached"))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="routine_roller")
    ap.add_argument("--db", default=None, help="kit_life.db override (tests)")
    ap.add_argument("--state-db", default=None)
    ap.add_argument("--today", default=None)
    ap.add_argument("--weekly", action="store_true", default=None)
    args = ap.parse_args(argv)
    conn = kit_life.event_conn(args.db)
    state_conn = open_state_db(args.state_db)
    try:
        s = run_nightly(conn, state_conn, today=args.today,
                        weekly=args.weekly)
        print("roller: today=%s lapsed=%d evidence_settled=%d "
              "conformance=%d materialized=%d extended=%d retired=%d"
              % (s["today"], s["lapsed"], s.get("evidence_settled", 0),
                 s["conformance_regen"], s["materialized"],
                 s["extended"], s["retired"]))
        for line in s["anomaly_push"]:
            # notify seam: the shell wrapper greps this prefix and routes it
            # through routines/push.sh (single aggregated weekly ping, 2.5).
            print("ANOMALY_PUSH: %s" % line)
        # The roller writes through direct calls, never kit_life main(), so
        # the day-card refresh leg must be fired here (best-effort, silent).
        if _moved_today(s):
            kit_life._day_card_refresh()
        return 0
    finally:
        conn.close()
        if state_conn is not None:
            state_conn.close()


if __name__ == "__main__":
    sys.exit(main())
