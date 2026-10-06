# -*- coding: utf-8 -*-
"""
DGN-180 GCal/Tasks surface adapter -- PRODUCTION build (mirror/).
Staged by step-4 prep from the sandbox ground truth (harness 108/108);
delta vs sandbox = paths/surface constants + live-SDK seam only.

DGN-240 S8 delta (staged copy, apply at S9 step 3 -- see
sandbox/dgn240-build/patches/adapter.py.patch.md):
  T1  in_mirror_scope: materialized routine instances IN scope;
      notion-import out (7.4); anchor-less belt kept (N1).
  T2  sweep candidate query: expired-task terminal filter (2.3, OQ-3)
      + scope-comment refresh.
  T12 tasks due-clear inbound on a routine instance: 'rejected_untimed'
      terminal classification (log + snapshot advance + re-echo, 5.5 M-C).
Original header follows.

DGN-180 GCal/Tasks surface adapter -- sandbox implementation, fix round 2.
Spec: DGN-180 v3 canonical (W0-W14) + grill-3 findings doc
(DGN-180 grill-3 real-code findings, 2026-07-09) -- both LAW.
SoT = sqlite event (DGN-179 v5 LOCK).
NO live cutover. Writes only to disposable dgn180-sandbox calendar/tasklist.
English/ASCII only.

Fix map (grill-3):
  F1  projections: inbound converted to sqlite representation FIRST, hashed
      over the SAME canonical field dict as push (calendar_projection_* /
      tasks_projection_*).
  F2  pageToken loops on both pulls; syncToken saved only from final page;
      tasks watermark advances only after full traversal.
  F3  foreign-item guard (_extract_ulid) + per-item try/except in both pulls.
  F4  inbound completed -> sdk_bridge.settle_done (SDK force_settle verb).
  M5  inbound calendar apply = per-field 3-way vs stored snapshot projection;
      schedule via sdk_bridge.bypass_schedule_apply (version/updated_at/
      schedule_kind re-derivation/recompute), content via content_update.
  M6  etag guard: compare-then-put (gws cannot send If-Match header -- see
      OPEN QUESTION); mismatch -> pull-first 3-way re-merge, then push.
  M7  mirror_outbox drain worker: claim/lease, exponential backoff on
      403/429/5xx, resumable backfill, single-flight lock.
  M8  GTasks inbound consumes title/notes/due + needsAction reopen
      (sdk_bridge.unsettle, DGN-180 verb).
  M9  in_mirror_scope: recurrence_id/is_routine rows excluded + counter log.
  M10 revive dead-end: second 404 -> generation-suffixed surface id.
  M11 get_src_conn -> 179 get_conn discipline (WAL/busy_timeout/retry).
  m12 bootstrap re-discovery by description marker (calendar).
  m14 open-ended placeholder end computed via display_tz (no +09:00 literal).
  m15 comment fixed: sqlite all_day end_at and GCal end.date are BOTH
      exclusive -> direct date conversion, no day shift.
  m16 missing GCal description key = explicit empty note ('').
  m17 sentinel strip is non-anchored (text below the sentinel survives).
  +   circuit breaker on mass-cancelled inbound (W10).
"""

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import time
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import sdk_bridge
import notify as notify_mod
import http_direct
import mirror_i18n
import mirror_colors

# Self-locating paths (module home = mirror/): SoT DB = ../database/
# kit_life.db, mirror state lives next to the module. No absolute home paths.
_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# DGN-268 S1: config seam. Per-instance surface identity (calendar/tasklist
# names, marker, display tz) is parameterized out of source literals into the
# instance config so the mirror is a product, not a single-instance fixture.
#
# Sources (both optional; a fresh dev checkout has NEITHER -> {} / defaults):
#   ../config/kit_life.conf   -- per-instance kit_life activation + mirror keys
#   ../.instance.conf        -- non-secret instance manifest (agent label etc.)
# Format = shell-style KEY=value, '#' comments, blank lines ignored.
#
# ZERO-DELTA GUARANTEE: every default below equals the prior canonical literal
# (or a system-derived value that falls back to it), so behavior is byte-
# identical when no config file is present. S1 changes NO sync logic.
# ---------------------------------------------------------------------------

_CONF_CACHE = None


def _parse_conf_file(path, into):
    """Merge KEY=value pairs from a shell-style conf file into `into`.
    Existing keys are NOT overwritten (earlier source wins). Missing file =
    no-op. Tolerant: malformed lines are skipped, never raised."""
    try:
        with open(path, "r") as fh:
            lines = fh.readlines()
    except (OSError, IOError):
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if not key or key in into:
            continue
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        into[key] = val


def _load_conf():
    """Parse the instance config once, cached. kit_life.conf is the primary
    source; .instance.conf provides fallback identity keys (agent label).
    Returns {} when neither file exists (fresh checkout)."""
    global _CONF_CACHE
    if _CONF_CACHE is None:
        conf = {}
        _parse_conf_file(
            os.path.join(_MODULE_DIR, "..", "config", "kit_life.conf"), conf)
        _parse_conf_file(
            os.path.join(_MODULE_DIR, "..", ".instance.conf"), conf)
        _CONF_CACHE = conf
    return _CONF_CACHE


def _reset_conf_cache():
    """Test seam: drop the cached parse so a new config file is re-read."""
    global _CONF_CACHE
    _CONF_CACHE = None


def _conf_int(key, default):
    """DGN-849 A2: integer config knob. The config value wins when it parses
    as an int; absent/malformed -> the prior canonical literal (zero-delta)."""
    try:
        return int((_load_conf().get(key) or "").strip())
    except ValueError:
        return default


def _agent_slug():
    """Instance agent slug (lowercase name) for marker/label derivation.
    Default 'agent' keeps derived strings well-formed on a bare checkout."""
    conf = _load_conf()
    return conf.get("DOGANY_AGENT_NAME") or "agent"


def _resolve_display_tz():
    """Display timezone. Config-supplied MIRROR_TZ wins; when absent the
    default is the prior canonical literal "Asia/Seoul" UNCONDITIONALLY on
    every platform (strict S1 zero-delta). System-tz autodetect is a value-
    add that belongs in S4 onboarding (the installer already knows the
    instance TZ and wires MIRROR_TZ explicitly), and it is cross-platform
    fragile (a fixed-offset tzinfo like 'KST' has no ZoneInfo key)."""
    conf = _load_conf()
    return conf.get("MIRROR_TZ") or "Asia/Seoul"


def _resolve_cal_summary():
    """Calendar name. Default = agent label (.instance.conf) when present,
    else the prior canonical placeholder literal (zero-delta)."""
    conf = _load_conf()
    return (conf.get("MIRROR_CAL_NAME")
            or conf.get("DOGANY_AGENT_LABEL")
            or "<agent-calendar-name>")


def _resolve_tasklist_title():
    """Tasklist name. Default = the resolved calendar name (spec H2)."""
    conf = _load_conf()
    return conf.get("MIRROR_TASKLIST_NAME") or _resolve_cal_summary()


def _derived_cal_marker():
    """Per-instance calendar description marker: dogany-mirror-<agent> (spec
    H3). Survives a calendar rename because bootstrap FREEZES the value that
    first created the calendar into the mirror_state KV (key 'cal_marker')
    and reads KV-first thereafter."""
    return "dogany-mirror-%s" % _agent_slug()


# Module-level surface identity (config-resolved at import; defaults preserve
# the prior canonical literals when no config file is present).
DISPLAY_TZ_NAME = _resolve_display_tz()
SANDBOX_CAL_SUMMARY = _resolve_cal_summary()
SANDBOX_TASKLIST_TITLE = _resolve_tasklist_title()
CAL_DESCRIPTION_MARKER = _derived_cal_marker()

# H6 (DGN-268 S1): calendar description text. Product string now (was the
# sandbox "Safe to delete." literal). DGN-268 S4: resolved through mirror_i18n
# by AGENT_LANG (i18n key 'mirror.cal_description'); the English literal here is
# the fallback used verbatim when the key/locale file is absent (zero-delta).
CAL_DESCRIPTION_TEXT = mirror_i18n.t(
    "mirror.cal_description",
    "Managed by the agent -- two-way synced with your assistant. "
    "Safe to edit; do not delete.")
DB_PATH = os.path.normpath(os.path.join(_MODULE_DIR, "..", "database", "kit_life.db"))
# The mirror writes its sync state NEXT TO ITS OWN SOURCE.  In a live
# instance that is the instance directory and correct.  In the PACK tree
# it means importing this module -- which every mirror test does -- mints a
# REAL-DATA sqlite file inside the product payload.  Measured 2026-09-09:
# test_dgn579_unified and test_mirror_inbound_suite each created
# payload/mirror/mirror_state.db (110 rows), and because that path is
# .gitignore'd, `git status` reported the tree CLEAN while the installer's
# personal-data gate (compat-lint C5) refused the release.  git-tracked is
# not the definition of what ships.
# MIRROR_STATE_DB lets a caller aim the state file somewhere disposable.
# Unset -> byte-identical to the prior behaviour (zero-delta).
# Same pack-source guard the other two homes use (kit_life.py /
# routine_roller.py).  _MODULE_DIR is payload/mirror, so its parent is the
# payload root and pack-manifest.json sits one above that.
def _mirror_state_default_here():
    explicit = os.environ.get("MIRROR_STATE_DB")
    if explicit:
        return explicit
    payload_parent = os.path.normpath(os.path.join(_MODULE_DIR, "..", ".."))
    if os.path.isfile(os.path.join(payload_parent, "pack-manifest.json")):
        import tempfile
        d = os.path.join(tempfile.gettempdir(), "dogany-packsrc-mirror-state")
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            return os.path.join(_MODULE_DIR, "mirror_state.db")
        return os.path.join(d, "mirror_state.db")
    return os.path.join(_MODULE_DIR, "mirror_state.db")


STATE_DB_PATH = _mirror_state_default_here()

# DGN-849 A2: operational thresholds are config knobs (kit_life.conf),
# defaults = the prior canonical literals (zero-delta when unset).
CB_CANCELLED_THRESHOLD = _conf_int(
    "MIRROR_CB_CANCELLED_THRESHOLD", 10)  # W10 breaker: mass-cancelled/poll
OUTBOX_MAX_ATTEMPTS = _conf_int("MIRROR_OUTBOX_MAX_ATTEMPTS", 5)
OUTBOX_LEASE_SECONDS = _conf_int("MIRROR_OUTBOX_LEASE_SECONDS", 300)
# 412 included (grill-5 finding 9): a double-412 hot-edit window is a
# transient contention state -- requeue within the attempts budget.
RETRYABLE_HTTP = {403, 412, 429, 500, 502, 503, 504}


# ---------------------------------------------------------------------------
# ulid <-> calendar-safe hex id (W2/V2)
# ---------------------------------------------------------------------------

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_CROCKFORD_MAP = {c: i for i, c in enumerate(_CROCKFORD)}
_HEX32_RE = re.compile(r"^[0-9a-f]{32}$")
_SENTINEL_RE = re.compile(r"\n?\[ulid:([^\]]+)\]")

def ulid_to_hex(ulid: str) -> str:
    """ULID (26 Crockford base32 chars) -> 32-char lowercase hex.
    hex charset is a subset of base32hex -> legal Calendar event id."""
    ulid = ulid.upper()
    val = 0
    for c in ulid:
        val = val * 32 + _CROCKFORD_MAP[c]
    return format(val, "032x")

def hex_to_ulid(hex_id: str) -> str:
    """32-char hex -> ULID string. Caller MUST verify _HEX32_RE first (F3)."""
    val = int(hex_id, 16)
    result = []
    for _ in range(26):
        result.append(_CROCKFORD[val & 31])
        val >>= 5
    return "".join(reversed(result))


# ---------------------------------------------------------------------------
# Routing predicate (V1) + mirror scope (M9)
# ---------------------------------------------------------------------------

# All-day-task conversion floor (owner 2026-08-24). FROZEN, exactly like the
# notion-import bounds below and for the same reason: routing must NOT track
# now(). A moving floor would drag settled history onto the calendar one day
# at a time and delete its Google Tasks rows behind the owner's back.
#
# Why a floor at all: before this change every all_day task went to Google
# Tasks, and kit_life already records that the owner does not use Google Tasks
# -- so a whole class of his items had no surface he reads. Flipping the class
# to Calendar is the fix, but doing it unbounded would have ALSO re-published
# 52 settled 2024-2026 rows as new calendar events and deleted their surface
# tasks (sync_event's calendar branch cleans up the tasks-side residue). The
# floor keeps history exactly where it already lives, untouched, and moves
# only rows dated on/after the cutover. Backfilling history is a separate,
# owner-gated call -- raise or lower this knob to change the boundary.
ALLDAY_TASK_CAL_FROM = (
    _load_conf().get("MIRROR_ALLDAY_TASK_CAL_FROM")
    or "2026-08-24")            # local start-date floor (inclusive)


def route_surface(event: dict, conn=None) -> str:
    """'calendar' or 'tasks'. Routing only -- scope filter is separate (M9).
    DGN-579 D-C3 (owner 2026-07-26): presence mirrors to calendar (reverses
    the B5/E3 exclusion); DGN-849 renders it as a subtype-labeled timed
    block (was: all-day banner).

    Owner 2026-08-24 ("캘린더엔왜안나오는데"): an all_day task is a Calendar
    row too, from ALLDAY_TASK_CAL_FROM onward. This is the ONE place the
    calendar/tasks decision is made -- push, reconcile drift, repaint and the
    routing-flip detector all call this same function, so there is no second
    branch that can disagree.

    untimed tasks stay on Tasks and MUST: schema CHECK forces start_at/end_at
    NULL for untimed, so there is no interval to render and
    utc_instant_to_gcal_datetime would raise on one.

    Fails toward the pre-change surface: an all_day row with a missing or
    unparseable start_at yields '' from _local_date_of, which sorts below any
    real floor -> 'tasks'. Routing never raises on data shape."""
    policy = sdk_bridge.kind_policy.for_event(event, conn)
    surface = policy["mirror_surface"]
    if surface == "none":
        raise ValueError("Unknown kind: %r" % event["kind"])
    if surface == "calendar":
        return surface
    if event["schedule_kind"] == "timed":
        return "calendar"
    if event["schedule_kind"] == "all_day":
        local = _local_date_of(event.get("start_at"), event.get("display_tz"))
        return "calendar" if local >= ALLDAY_TASK_CAL_FROM else "tasks"
    return "tasks"


# DGN-265: the 7.4 notion-import exclusion over-reached -- it dropped LIVE/future
# import instances, not just history, so they never mirrored (invisible in GCal).
# Carve back in only the still-live ones. BOTH bounds are FROZEN values and must
# NOT track now()/today: a moving bound would drop already-pushed rows out of scope
# tomorrow and orphan their GCal surface objects.
# DGN-849 A2: the bounds are per-instance config now (an import epoch is
# instance data, not base-kit code). The guard as a whole is inert on any
# instance without created_by='notion-import' rows. NOT removed despite being
# date-stale: the rec_date floor permanently keeps import HISTORY out of mirror
# scope, and the start_at floor still gates rows whose surface objects were
# already pushed -- deleting either arm would flood history into GCal or orphan
# live surface objects (DGN-364 spec 2.6).
# DGN-870: NO literal fallback. The old default was the first instance's
# 2026-07-12 epoch, silently applied to every instance whose config lacked the
# keys. An unset or malformed bound now FAILS CLOSED the moment a notion-import
# recurrence row reaches the scope check (NotionImportBoundError, naming the
# keys). Raising -- not returning False -- is the closed side: dropping a row
# from scope would let reconcile treat its already-pushed surface object as an
# orphan, while a raise stops the run before any write. Instances with no
# notion-import rows never read these values.
NOTION_IMPORT_MIRROR_CUTOFF = (
    (_load_conf().get("MIRROR_NOTION_IMPORT_CUTOFF") or "").strip()
    or None)                       # rec_date floor (history < this stays out)
NOTION_IMPORT_MIRROR_MIN_START = (
    (_load_conf().get("MIRROR_NOTION_IMPORT_MIN_START") or "").strip()
    or None)                       # start_at floor (imports: future only)


class NotionImportBoundError(RuntimeError):
    """A notion-import row needs a mirror scope bound the instance config does
    not supply (or supplies malformed). Refuse rather than guess a date."""


def _notion_import_bounds(event):
    """(cutoff, min_start) from config, validated. Raises NotionImportBoundError
    when either is unset or not in its canonical shape -- both bounds are
    compared as strings, so a wrong shape would mis-scope silently."""
    cutoff = NOTION_IMPORT_MIRROR_CUTOFF
    min_start = NOTION_IMPORT_MIRROR_MIN_START
    problems = []
    try:
        if cutoff is None:
            raise ValueError
        datetime.strptime(cutoff, "%Y-%m-%d")
    except ValueError:
        problems.append("MIRROR_NOTION_IMPORT_CUTOFF=%r (want YYYY-MM-DD)" % cutoff)
    try:
        if min_start is None:
            raise ValueError
        datetime.strptime(min_start, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        problems.append("MIRROR_NOTION_IMPORT_MIN_START=%r (want YYYY-MM-DDTHH:MM:SSZ)"
                        % min_start)
    if problems:
        raise NotionImportBoundError(
            "notion-import row %s cannot be scoped: %s in config/kit_life.conf. "
            "Set the instance's import epoch; refusing to fall back to a "
            "literal date (DGN-870)." % (event.get("ulid"), "; ".join(problems)))
    return cutoff, min_start


def in_mirror_scope(event: dict) -> bool:
    """DGN-240 5.1 (T1, replaces the M9 safe default): materialized routine
    instances ARE mirror scope. notion-import history stays out (7.4) EXCEPT
    live/future instances carved in per DGN-265; anchor-less routine rows stay
    out (N1 belt).

    DGN-579 D-C3 (owner 2026-07-26, supersedes the E3 belt): presence IS
    mirror scope -- it renders as a non-blocking block on the travel
    calendar (DGN-849: subtype-labeled timed block, was all-day banner).
    Presence rows flow through the same recurrence /
    is_routine logic as actionable rows (roller presence instances carry
    recurrence_id -> in scope; user presence has neither flag -> in
    scope).

    SPEC 2026-08-14: 집쉼 (home NON-WORK presence) is HIDDEN -- NOT mirrored
    (base case). The check reads the enriched place render (place_render /
    location); callers on the push + reconcile paths enrich BEFORE the scope
    check, so a home-rest stay is dropped from scope entirely. A bare
    (un-enriched) dict has no place info -> not classified hidden (safe: the
    push/reconcile paths always enrich first)."""
    # 036 introduced `record` blocks (meals moved out of their own table into
    # the block axis).  A record is a LOG of something already eaten -- it is
    # not an appointment, has no interval to occupy, and belongs on no calendar.
    # It was never routable, but nothing said so: scope let it through and
    # route_surface raised `Unknown kind: 'record'` on it.  reconcile therefore
    # died on EVERY run from the moment 036 landed (2026-09-06) until this fix.
    #
    # Nobody saw it for three days, and that is the part worth writing down:
    # cron-guard parks this routine in the heavy queue, so the wrapper exited 0
    # and the crash was never even reached.  The stability gate scored a dead
    # routine as a pass until a human ran it by hand (2026-09-09).
    #
    # DENY the known-unroutable kind here rather than adding a route for it, and
    # rather than turning route_surface into an allowlist: an allowlist would
    # silently drop the NEXT new kind, and silent is exactly how this one
    # survived three days.  route_surface keeps raising on a genuinely unknown
    # kind -- that raise is correct, it just needs to be reachable.
    if sdk_bridge.kind_policy.for_event(event)["mirror_surface"] == "none":
        return False
    if is_hidden_presence(event):
        return False
    if event.get("recurrence_id"):
        if event.get("created_by") == "notion-import":
            cutoff, min_start = _notion_import_bounds(event)   # DGN-870: fail closed
            if (event.get("rec_date") or "") < cutoff:
                return False   # 7.4: import history stays out
            return (event.get("start_at") or "") >= min_start  # DGN-265: future only
        return True   # non-import routine instances in scope
    return not event.get("is_routine")   # N1 belt: anchor-less stays out


# ---------------------------------------------------------------------------
# DGN-294 / DGN-364 (V15 promotion): 3-category calendar routing.
# Calendar IDENTITY = id engraved in mirror config (mirror_state KV); names
# come from i18n keys and are display-only (a user rename never affects
# routing -- the name lookup happens once at creation/provision).
# ---------------------------------------------------------------------------

CAL_STATE_KEYS = {          # mirror config homes for the 4 engraved ids
    "appt": "cal_id_appt",
    "task": "cal_id_task",
    "travel": "cal_id_travel",
}
CHECKLIST_STATE_KEY = "gtasks_checklist_id"

# Legacy (single-calendar) state keys: the pre-V15 engrave shape. Kept as a
# PERMANENT first-class read path (DGN-364 section 3), never a shim.
LEGACY_CAL_STATE_KEY = "agent_calendar_id"
LEGACY_TASKLIST_STATE_KEY = "agent_tasklist_id"

# i18n keys for provision display names. The fallbacks are unicode-escaped
# (source stays ASCII, A7) and OWN the naming pattern (DGN-364 4.1 / dec-033):
# {name} = the user's chosen calendar name (MIRROR_CAL_NAME) used as display
# PREFIX; absent config falls back to the "Dogany-*" i18n defaults.
CAL_I18N = {
    "appt": ("mirror.cal.appointments", u"{name}-\uc57d\uc18d"),
    "task": ("mirror.cal.tasks", u"{name}-\ud0dc\uc2a4\ud06c"),
    "travel": ("mirror.cal.travel", u"{name}-\uc774\ub3d9"),
}
CHECKLIST_I18N = ("mirror.gtasks.checklist",
                  u"{name}-\uccb4\ud06c\ub9ac\uc2a4\ud2b8")

# Default display prefix when MIRROR_CAL_NAME is absent: renders the i18n
# defaults ("Dogany-...") byte-identically to the Ag V15 literals.
_CATEGORY_NAME_DEFAULT_PREFIX = "Dogany"


def route_cal_key(event: dict, conn=None) -> str:
    """Deterministic calendar-category key for a calendar-routed row
    (spec 4.1 route(row), calendar side):
        kind='appointment'                       -> 'appt'   (CAL_APPT)
        kind='presence' AND schedule_kind='timed'-> 'task'   (CAL_TASK, DGN-849)
        kind='presence' (all_day / away)         -> 'travel' (CAL_TRAVEL)
        kind='task' AND block_class='travel'     -> 'travel' (CAL_TRAVEL)
        kind='task' AND block_class IS NULL      -> 'task'   (CAL_TASK)
    kind and block_class are insert-immutable -> no cross-calendar moves in
    steady state; a row found in the wrong calendar is DRIFT (attention
    line, never a silent move). The primary calendar is never read or
    written.

    DGN-849 presence routing (owner 2026-08-16, EVENT-MODEL / CALENDAR-MIRROR
    SoT -- supersedes the D-C3 "any presence -> CAL_TRAVEL" line): a TIMED
    presence (work / at-place span, e.g. 회사 / 속초) is a timed subtype block
    on CAL_TASK; only all-day / non-work / away (여행-natured) presence rides
    CAL_TRAVEL. This is the SAME rule the push side and the reconcile drift
    check both call, so found==expected and the appt<->travel churn stops."""
    policy = sdk_bridge.kind_policy.for_event(event, conn)
    if event.get("block_class") == "travel" and not policy["persons_join"]:
        return "travel"
    if policy["presence_mode"]:
        if _presence_crosses_night(event):
            return policy["overnight_calendar_key"]
        if event.get("schedule_kind") == "timed":
            return policy["timed_calendar_key"]
    return policy["calendar_key"]


def adopt_kind_for_cal(cal_key, schedule_kind, start_at, end_at,
                       display_tz=None, conn=None):
    """Invert the push routing for a hand-made entry's source calendar."""
    if cal_key not in CAL_STATE_KEYS:
        return "appointment"
    policy_conn = conn or sdk_bridge.kind_policy._default_conn()
    candidates = policy_conn.execute(
        "SELECT token FROM block_kind_behavior WHERE adoptable=1 "
        "ORDER BY presence_mode, persons_join DESC").fetchall()
    for (kind,) in candidates:
        event = {"kind": kind, "schedule_kind": schedule_kind,
                 "start_at": start_at, "end_at": end_at,
                 "display_tz": display_tz or DISPLAY_TZ_NAME,
                 "block_class": None}
        if (route_surface(event, policy_conn) == "calendar"
                and route_cal_key(event, policy_conn) == cal_key):
            return kind
    return "appointment"


def _presence_crosses_night(event: dict) -> bool:
    """True when a presence spans a LOCAL date boundary -- the user sleeps
    somewhere other than base, so this is 숙박이동 rather than 낮체류.

    Compared in the row's own display_tz, never UTC: a 08:30-17:30 Seoul
    workday is stored as 23:30Z-08:30Z and crosses a UTC date every single
    time. Getting this wrong would file every commute as a trip.

    Fails closed -- unparseable or missing bounds return False, which keeps
    the pre-existing timed/all-day routing rather than moving a row on a
    guess.
    """
    start, end = event.get("start_at"), event.get("end_at")
    if not start or not end:
        return False
    try:
        tz = ZoneInfo(event.get("display_tz") or "Asia/Seoul")
        s = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        e = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
        return s.astimezone(tz).date() != e.astimezone(tz).date()
    except Exception:  # noqa: BLE001 -- routing must never raise
        return False


def route(event: dict):
    """spec 4.1 route(row) -> ('tasks', 'checklist') | ('calendar', key)."""
    if route_surface(event) == "tasks":
        return ("tasks", "checklist")
    return ("calendar", route_cal_key(event))


def _is_engraved(value):
    """Engraved-ness (DGN-364 2.1, TM-5, normative): a key counts as engraved
    only when its value is non-None AND non-empty after strip. The empty
    string '' is NOT engraved -- everywhere: resolution, scan-set
    construction, cleanup."""
    return value is not None and str(value).strip() != ""


def get_cal_ids(state_conn) -> dict:
    """{'appt': id, 'task': id, 'travel': id} from the engraved config.
    Legacy fan-out fallback (DGN-364 2.1): when NO V15 cal key is engraved
    but the legacy agent_calendar_id is, that one id is fanned out to all
    three categories -- an Ag-style call site is legacy-safe too. Never
    raises; unconfigured returns the all-None dict (callers that want loud
    failure use get_mirror_targets)."""
    ids = {k: get_state(state_conn, v) for k, v in CAL_STATE_KEYS.items()}
    if not any(_is_engraved(v) for v in ids.values()):
        legacy = get_state(state_conn, LEGACY_CAL_STATE_KEY)
        if _is_engraved(legacy):
            return {"appt": legacy, "task": legacy, "travel": legacy}
    return ids


class MirrorUnconfigured(RuntimeError):
    """No mirror calendar routing ids engraved. Message lists the exact
    keys checked and the remedy (run kit_life setup mirror provisioning)."""


class MirrorConfigError(RuntimeError):
    """Ids partially engraved (named keys missing). Non-retryable.
    Attributes (normative, DGN-364 M3):
      provenance:  'multi'  -- at least one V15 cal key present
                   'legacy' -- no V15 cal key present, partial agent_* pair
      missing_keys: tuple of the exact absent key names."""

    def __init__(self, message, provenance, missing_keys):
        super().__init__(message)
        self.provenance = provenance
        self.missing_keys = tuple(missing_keys)


def get_mirror_targets(state_conn) -> dict:
    """Resolve engraved surface ids with legacy fallback (DGN-364 2.1).
    Returns {'mode': 'multi'|'legacy',
             'cal_ids': {'appt': .., 'task': .., 'travel': ..},
             'checklist_id': str}
    Raises MirrorUnconfigured / MirrorConfigError (DGN-364 section 6).

    Accuracy rule (R2-6, normative): the checklist keys (gtasks_checklist_id
    / agent_tasklist_id-as-fallback) are DELIBERATELY not consulted when
    determining configured-ness -- only the three CAL_STATE_KEYS and the
    agent_* pair (five keys total) decide. A state with ONLY
    gtasks_checklist_id engraved resolves as MirrorUnconfigured, and the
    message claims only that no CALENDAR ROUTING ids are engraved."""
    cal_vals = {k: get_state(state_conn, v) for k, v in CAL_STATE_KEYS.items()}
    engraved = {k for k, v in cal_vals.items() if _is_engraved(v)}
    if engraved:
        # Any V15 cal key present -> mode='multi', V15 wholesale precedence.
        missing = [CAL_STATE_KEYS[k] for k in ("appt", "task", "travel")
                   if k not in engraved]
        if missing:
            raise MirrorConfigError(
                "mirror calendar ids partially engraved; missing: %s; "
                "remedy: re-run provisioning (provision_category_calendars "
                "is idempotent and fills only missing ids)"
                % ", ".join(missing),
                provenance="multi", missing_keys=missing)
        checklist = get_state(state_conn, CHECKLIST_STATE_KEY)
        if not _is_engraved(checklist):
            # Fallback chain (2.1 step 2): provision renames the legacy
            # tasklist in place, so an upgraded legacy instance ends with
            # both keys engraved and equal.
            checklist = get_state(state_conn, LEGACY_TASKLIST_STATE_KEY)
        if not _is_engraved(checklist):
            missing = (CHECKLIST_STATE_KEY, LEGACY_TASKLIST_STATE_KEY)
            raise MirrorConfigError(
                "mirror checklist tasklist id not engraved; missing: %s; "
                "remedy: re-run provisioning (provision_category_calendars "
                "is idempotent and fills only missing ids)"
                % ", ".join(missing),
                provenance="multi", missing_keys=missing)
        return {"mode": "multi",
                "cal_ids": {k: cal_vals[k] for k in CAL_STATE_KEYS},
                "checklist_id": checklist}

    # No V15 key -> legacy pair.
    cal_id = get_state(state_conn, LEGACY_CAL_STATE_KEY)
    tl_id = get_state(state_conn, LEGACY_TASKLIST_STATE_KEY)
    cal_ok, tl_ok = _is_engraved(cal_id), _is_engraved(tl_id)
    if cal_ok and tl_ok:
        return {"mode": "legacy",
                "cal_ids": {"appt": cal_id, "task": cal_id, "travel": cal_id},
                "checklist_id": tl_id}
    if cal_ok or tl_ok:
        missing = (LEGACY_TASKLIST_STATE_KEY,) if cal_ok \
            else (LEGACY_CAL_STATE_KEY,)
        raise MirrorConfigError(
            "mirror legacy ids partially engraved; missing: %s; "
            "remedy: repair via bootstrap() (legacy writer of record) -- "
            "ensure_mirror_engraved routes this automatically"
            % ", ".join(missing),
            provenance="legacy", missing_keys=missing)
    raise MirrorUnconfigured(
        "no mirror calendar routing ids engraved; checked "
        "cal_id_appt/cal_id_task/cal_id_travel and "
        "agent_calendar_id/agent_tasklist_id; run kit_life setup mirror "
        "provisioning (ensure_mirror_engraved)")


def _cal_ids_dict(cal_ids):
    """Accept the 3-calendar dict or a legacy single id string (test /
    transition callers) -- a string routes every category to that one id."""
    if isinstance(cal_ids, dict):
        return cal_ids
    return {"appt": cal_ids, "task": cal_ids, "travel": cal_ids}


def _calendar_scan_set(cal_ids):
    """DGN-364 2.2 (normative, dict-shape-driven, no hidden mode flag):
    calendar scan set from the dict alone. Returns [(cal_id, cal_key)]:
      - unique ids = ordered de-dup of cal_ids values (appt, task, travel
        order), skipping unengraved entries (None or empty-string).
      - ALL engraved entries one identical id -> single pull with
        cal_key=None -> plain 'cal_sync_token' cursor (byte-identical
        legacy behavior).
      - else one pull per unique id with cal_key = the first category that
        maps to that id -> 'cal_sync_token:<key>' cursors (byte-identical
        Ag V15 behavior). A duplicate-id multi dict yields namespaced
        cursors only, no plain-cursor pull."""
    scan = []
    seen = []
    for key in ("appt", "task", "travel"):
        cid = cal_ids.get(key)
        if not _is_engraved(cid):
            continue
        if cid not in seen:
            seen.append(cid)
            scan.append((cid, key))
    if len(scan) == 1:
        return [(scan[0][0], None)]
    return scan


# ---------------------------------------------------------------------------
# Time conversion (179 M4 rule: display_tz for all_day, UTC for timed)
# ---------------------------------------------------------------------------

def _to_rfc3339_local(ts_utc: str, tz: ZoneInfo) -> str:
    dt = datetime.strptime(ts_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    dt_local = dt.astimezone(tz)
    offset = dt_local.utcoffset()
    total_secs = int(offset.total_seconds())
    sign = "+" if total_secs >= 0 else "-"
    h, m = divmod(abs(total_secs) // 60, 60)
    return dt_local.strftime("%Y-%m-%dT%H:%M:%S") + ("%s%02d:%02d" % (sign, h, m))


def utc_instant_to_gcal_datetime(start_at, end_at, schedule_kind,
                                  display_tz=DISPLAY_TZ_NAME):
    """sqlite canonical UTC instants -> GCal EventDateTime pair.
    timed -> dateTime+timeZone; all_day -> date via display_tz.
    m14: open-ended placeholder end (start+1h) goes through the SAME
    display_tz conversion (no hardcoded offset)."""
    tz = ZoneInfo(display_tz)
    if schedule_kind == "timed":
        start_dt = {"dateTime": _to_rfc3339_local(start_at, tz), "timeZone": display_tz}
        if end_at:
            end_dt = {"dateTime": _to_rfc3339_local(end_at, tz), "timeZone": display_tz}
        else:
            dt = datetime.strptime(start_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            ph = (dt + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
            end_dt = {"dateTime": _to_rfc3339_local(ph, tz), "timeZone": display_tz}
        return start_dt, end_dt
    elif schedule_kind == "all_day":
        def utc_to_date(ts_utc):
            dt = datetime.strptime(ts_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            return dt.astimezone(tz).strftime("%Y-%m-%d")
        start_dt = {"date": utc_to_date(start_at)}
        if end_at:
            # m15: sqlite all_day end_at is exclusive next-midnight AND GCal
            # end.date is exclusive -> direct conversion, no day shift.
            end_dt = {"date": utc_to_date(end_at)}
        else:
            # No end stored: single-day block = start date + 1 day (exclusive).
            d = datetime.strptime(utc_to_date(start_at), "%Y-%m-%d") + timedelta(days=1)
            end_dt = {"date": d.strftime("%Y-%m-%d")}
        return start_dt, end_dt
    raise ValueError("Unexpected schedule_kind for Calendar: %r" % schedule_kind)


def gcal_datetime_to_utc_instants(start_obj, end_obj, display_tz=DISPLAY_TZ_NAME):
    """GCal EventDateTime pair -> sqlite UTC instants (start_at, end_at)."""
    tz = ZoneInfo(display_tz)

    def parse_dt(obj):
        if not obj:
            return None
        if "dateTime" in obj:
            try:
                dt = datetime.fromisoformat(obj["dateTime"])
                return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                return None
        if "date" in obj:
            d = datetime.strptime(obj["date"], "%Y-%m-%d")
            return d.replace(tzinfo=tz).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return None

    return parse_dt(start_obj), parse_dt(end_obj)


def task_due_from_event(event: dict):
    """GTasks due date (YYYY-MM-DD) for all_day tasks; untimed -> None."""
    if event["schedule_kind"] == "all_day" and event.get("start_at"):
        tz = ZoneInfo(event.get("display_tz") or DISPLAY_TZ_NAME)
        dt = datetime.strptime(event["start_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return dt.astimezone(tz).strftime("%Y-%m-%d")
    return None


def all_day_instants_from_date(date_str, display_tz=DISPLAY_TZ_NAME):
    """YYYY-MM-DD -> (start_at, end_at) canonical UTC instants for a one-day
    all_day block (local midnight .. next local midnight, exclusive)."""
    tz = ZoneInfo(display_tz)
    d = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=tz)
    start = d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    end = (d + timedelta(days=1)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return start, end


# ---------------------------------------------------------------------------
# GWS CLI wrapper
# ---------------------------------------------------------------------------

class GwsError(RuntimeError):
    def __init__(self, msg, code=0, reason=""):
        super().__init__(msg)
        self.http_code = code
        self.reason = reason


def _gws_bin() -> str:
    """Resolve the gws binary WITHOUT trusting PATH alone.

    DGN-963 (2026-08-21) reordered PATH and dropped $HOME/.npm-global/bin as
    "dead". It was not dead -- gws lives there and nothing else does, so every
    mirror routine's `command -v gws` gate went false and the calendar sync
    exited 0 SILENTLY. Nothing broke loudly; the sync just stopped, and blocks
    retired locally kept living in the owner's Google Calendar for days. This
    is the same defect that killed place-view's agent-browser on the same day,
    so it gets the same fix: PATH first, then the known install locations, so
    no future PATH policy can silently unplug us. Override with GWS_BIN.
    """
    override = os.environ.get("GWS_BIN")
    if override:
        return override
    found = shutil.which("gws")
    if found:
        return found
    for cand in (os.path.expanduser("~/.npm-global/bin/gws"),
                 os.path.expanduser("~/.local/bin/gws"),
                 "/opt/homebrew/bin/gws",
                 "/usr/local/bin/gws"):
        if os.access(cand, os.X_OK):
            return cand
    return "gws"  # fall through so the caller fails with a real "not found"


def gws(*args, body=None) -> dict:
    """Call gws CLI, return parsed JSON. Raises GwsError on API errors."""
    cmd = [_gws_bin()] + list(args)
    if body is not None:
        cmd += ["--json", json.dumps(body)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    stderr_clean = "\n".join(
        l for l in result.stderr.splitlines() if "keyring" not in l.lower())
    if not result.stdout.strip():
        raise GwsError("gws no output: %s\nArgs: %s" % (stderr_clean, args))
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise GwsError("gws invalid JSON: %s" % result.stdout[:200])
    if "error" in data and isinstance(data["error"], dict):
        err = data["error"]
        raise GwsError("API error %s %s: %s" % (
            err.get("code", 0), err.get("reason", ""), err.get("message", "")),
            code=err.get("code", 0), reason=err.get("reason", ""))
    return data


def gws_delete(*args) -> bool:
    """Delete commands return 204 No Content (empty stdout, exit 0)."""
    cmd = [_gws_bin()] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        return True
    try:
        data = json.loads(result.stdout)
        err = data.get("error", {})
        raise GwsError("API error %s: %s" % (err.get("code", 0), err.get("message", "")),
                       code=err.get("code", 0), reason=err.get("reason", ""))
    except (json.JSONDecodeError, AttributeError):
        raise GwsError("gws delete failed (exit %d): %s"
                       % (result.returncode, result.stderr[:200]))


# ---------------------------------------------------------------------------
# State DB helpers
# ---------------------------------------------------------------------------

def open_state_db() -> sqlite3.Connection:
    conn = sqlite3.connect(STATE_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    schema_path = os.path.join(os.path.dirname(__file__), "mirror_state.sql")
    conn.executescript(open(schema_path).read())
    try:  # pre-existing state DBs: add reconcile requeue column (X10b-6)
        conn.execute(
            "ALTER TABLE mirror_outbox ADD COLUMN requeues "
            "INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:  # g5-4 terminal state for repeatedly-failed rows
        conn.execute(
            "ALTER TABLE mirror_outbox ADD COLUMN dead "
            "INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:  # DGN-294/DGN-364 (V15): target calendar id bookkeeping (additive)
        conn.execute(
            "ALTER TABLE push_snapshot ADD COLUMN calendar_id TEXT")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    return conn


def get_state(conn, key, default=None):
    row = conn.execute("SELECT value FROM mirror_state WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_state(conn, key, value):
    conn.execute(
        "INSERT INTO mirror_state(key, value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    conn.commit()


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_local_date(tz_name=None):
    """Local (display-tz) calendar date, YYYY-MM-DD. Used by the SPEC repaint
    today-onward gate so 'today' matches the user's wall clock (not UTC)."""
    tz = ZoneInfo(tz_name or DISPLAY_TZ_NAME)
    return datetime.now(tz).strftime("%Y-%m-%d")


def _local_date_of(utc_instant, tz_name=None):
    """Local calendar date (YYYY-MM-DD) of a canonical UTC instant, or '' when
    unparseable. Converts to the display tz first so a late-evening UTC value
    maps to the correct local day."""
    if not utc_instant:
        return ""
    try:
        dt = datetime.strptime(utc_instant, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
        return dt.astimezone(ZoneInfo(tz_name or DISPLAY_TZ_NAME)).strftime(
            "%Y-%m-%d")
    except (ValueError, TypeError):
        return ""


def mirror_log(conn, category, event_ulid=None, detail=None):
    conn.execute(
        "INSERT INTO mirror_log(ts, event_ulid, category, detail) VALUES(?,?,?,?)",
        (_now_iso(), event_ulid, category, detail))
    conn.commit()


# ---------------------------------------------------------------------------
# W6 promotion: surface bookkeeping home = event-table columns (v3 W6
# "필수 승격", v4 X10b). Spec basis: v3 W6 mandates event.gcal_event_id /
# event.gtask_id as REQUIRED bookkeeping columns -- adding nullable mirror
# columns is DGN-180-owned and does not touch 179 v5-locked semantics
# (no CHECK, no derivation, no slot predicate involvement). Bookkeeping
# writes do NOT bump version/updated_at (not a domain mutation; W6: push
# txn records/clears). State KV becomes cache-only fallback (src_conn
# absent or columns not yet migrated).
# ---------------------------------------------------------------------------

BK_KV_KEY = {
    "gcal_event_id": "gcal_id:%s",
    "gtask_id": "gtask_id:%s",
    "gcal_etag": "etag:cal:%s",
    "gtask_etag": "etag:task:%s",
}
def _bk_cols_available(src_conn):
    """No caching: id(conn)-keyed caches risk GC id reuse (grill-5 NIT) and
    PRAGMA table_info is an in-memory schema lookup (cheap per call)."""
    if src_conn is None:
        return False
    try:
        cols = {r[1] for r in src_conn.execute("PRAGMA table_info(timeblock)")}
        return "gcal_event_id" in cols
    except sqlite3.Error:
        return False


def bk_get(state_conn, src_conn, ulid, field):
    """Read surface bookkeeping: promoted column first, KV cache fallback."""
    if _bk_cols_available(src_conn):
        row = src_conn.execute(
            "SELECT %s FROM timeblock WHERE ulid=?" % field, (ulid,)).fetchone()
        if row is not None and row[0]:
            return row[0]
    return get_state(state_conn, BK_KV_KEY[field] % ulid) or None


def bk_set(state_conn, src_conn, ulid, field, value):
    """Write bookkeeping to the promoted home (event column) when available;
    always mirror to KV (cache-only during transition). No version bump.

    v3.2 (036): the timeblock name is a read-only compat VIEW; the promoted
    home of a bookkeeping field is a block_attr row (which is exactly what
    the view serves back to bk_get). Capability-detected, not flag-detected,
    same discipline as kit_life._is_v32."""
    if _bk_cols_available(src_conn):
        is_v32 = src_conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='block';"
        ).fetchone() is not None
        if is_v32:
            row = src_conn.execute(
                "SELECT id FROM block WHERE ulid=?", (ulid,)).fetchone()
            if row is not None:
                if value:
                    src_conn.execute(
                        "INSERT OR REPLACE INTO block_attr"
                        "(block_id, attr_token, value, source) "
                        "VALUES (?,?,?,'user')", (row[0], field, str(value)))
                else:
                    src_conn.execute(
                        "DELETE FROM block_attr WHERE block_id=? "
                        "AND attr_token=?", (row[0], field))
        else:
            src_conn.execute(
                "UPDATE timeblock SET %s=? WHERE ulid=?" % field,
                (value or None, ulid))
        src_conn.commit()
    set_state(state_conn, BK_KV_KEY[field] % ulid, value or "")


IFMATCH_STATS = {"puts": 0, "http412": 0}


def _direct(fn, *args, **kwargs):
    """grill-5 finding 1: every direct-HTTPS failure must re-enter the
    outbox retry classifier as GwsError. HttpError keeps its HTTP code;
    token-endpoint / network / store-rotation (InvalidTag) failures map to
    GwsError 503 (retryable infra)."""
    try:
        return fn(*args, **kwargs)
    except http_direct.HttpError as e:
        raise GwsError("direct HTTP %d: %s" % (e.code, e), code=e.code) from e
    except GwsError:
        raise
    except Exception as e:
        raise GwsError("direct lane infra error: %s" % e, code=503) from e


def _title_of(src_conn, ulid):
    if src_conn is None:
        return ulid
    row = src_conn.execute(
        "SELECT title FROM timeblock WHERE ulid=?", (ulid,)).fetchone()
    return row["title"] if row else ulid


def _notify_overlap(state_conn, src_conn, ulid, detail):
    notify_mod.notify(state_conn, "overlap_notice", ulid,
                      title=_title_of(src_conn, ulid), detail=detail)


# DGN-333 (MAJOR-5 rev): per-apply overlap DETECTION stays (audit line at the
# apply site), but the user NOTIFICATION is deferred to the end of the sync
# cycle -- mid-batch sequential applies create transient overlaps against
# not-yet-moved rows (false alarms). Candidates collected here are re-checked
# against the FINAL state by overlap_flush() (poll_cycle owns the flush; one
# poller cycle = one process, so module state is cycle-local).
_OVERLAP_PENDING = []  # list of (ulid, detail-at-detection)


def _overlap_defer(ulid, detail):
    if not any(u == ulid for u, _d in _OVERLAP_PENDING):
        _OVERLAP_PENDING.append((ulid, detail))


def overlap_flush(state_conn, src_conn):
    """Batch-end recheck of deferred overlap candidates (DGN-333). Notifies
    only overlaps that still exist in the final state; at most one notice
    per overlap pair. Returns the number of notices queued."""
    pending, _OVERLAP_PENDING[:] = list(_OVERLAP_PENDING), []
    notified_pairs = set()
    n = 0
    for ulid, detail in pending:
        hit, warning = sdk_bridge.mirror_overlap_recheck(src_conn, ulid)
        if hit is None:
            mirror_log(state_conn, "overlap_recheck_cleared", ulid,
                       "transient mid-batch overlap resolved (was: %s)"
                       % detail)
            continue
        pair = frozenset((ulid, hit))
        if pair in notified_pairs:
            continue
        notified_pairs.add(pair)
        mirror_log(state_conn, "overlap_recheck_confirmed", ulid, warning)
        _notify_overlap(state_conn, src_conn, ulid, warning)
        n += 1
    return n


# ---------------------------------------------------------------------------
# DGN-579 calendar bundle: place-FK location, event_persons exposure.
# DGN-849: presence/travel/prep render as subtype-labeled TIMED blocks
# (supersedes the D-C3 presence all-day banner) + kit-agnostic color seam.
#
# F1 discipline: everything the surface shows must be derivable identically
# on the push side (from the SoT row) and the inbound side (from the GCal
# item), or echo detection breaks. Hence:
#   - place/persons are attached to the event DICT once (enrich_event) at
#     every row-load seam, never resolved ad hoc inside one path;
#   - the persons line is composed/stripped by one prefix contract
#     (_compose_description / _strip_desc_persons);
#   - the surface title (subtype label + status decoration) is composed by
#     ONE seam (_projected_cal_title) used by projection AND body build, and
#     mirror-authored prefixes are stripped before any inbound title
#     write-back (_strip_title_decorations).
# ---------------------------------------------------------------------------

# i18n-resolved at import like CAL_DESCRIPTION_TEXT; fallbacks are the ko
# literals (unicode-escaped, source stays ASCII).
WITH_PREFIX = mirror_i18n.t("mirror.with_prefix", u"\ud568\uaed8: ")

# DGN-849 R&R (owner ruling): the framework owns the MECHANISM only --
# subtype rows render as TIMED blocks whose title carries a label slot, and
# terminal status can ride a decoration slot. The label/decoration TEXT is
# kit PRODUCT CONTENT (kit lane) and lives entirely in the kit's color
# config; the framework code carries NO domain/locale words. When the kit
# supplies nothing, the neutral fallback attaches NOTHING (no label, no
# decoration -- never a domain word inherited from base-kit). A kit opts a
# label in by publishing config/calendar-colors.json "subtype_labels" /
# "status_decor" maps (+ optional "subtype_fmt" wrap).
#
# NEUTRAL DEGRADE CONTRACT: a subtype with no config label renders with its
# raw subtype KEY (a code value: presence/travel/prep), never a translated
# word. A kit that wants a human word must publish it. Empty fmt disables
# the wrap. The inbound strip (_strip_title_decorations) peels only prefixes
# the config actually declares, so a config-less instance strips nothing.


# ---------------------------------------------------------------------------
# SPEC 2026-08-14: calendar render model  "행위 · 장소 · 동행"
# (the 2026-08-14 calendar render/presence spec). The render is kit_life
# PRODUCT content (mirror/ is kit-owned), so the label/separator/exception
# words live here in the product adapter (not the framework color seam).
#
# CRITICAL SAFETY: display_name columns (persons/place) and the person_group
# table do NOT exist until migration 024 is applied. Every DB access below
# is FEATURE-DETECTED (_has_column / _has_table cached per-connection) and
# falls back to name/existing behavior. So the render improvements that need no
# new schema (title normalization effect, place.name, person.name) light up
# BEFORE the migration, and display_name/group lookups light up AFTER -- the
# adapter never crashes on the pre-migration DB.
# ---------------------------------------------------------------------------

RENDER_SEP = u" · "            # " . " middle-dot part separator
TRAVEL_MOVE_FMT = u"이동: %s → %s"   # "이동: {출발} -> {도착}"
# D3-4: a PINNED (frozen) derived block used to render byte-identically to a
# live one -- `grep -n pinned` over adapter / mirror_colors / reconcile /
# sdk_bridge returned nothing -- so the owner had no way to tell a block the
# deriver still maintains from one it has stopped touching. The colorId axis is
# kit-config owned (DGN-849: area domain), so "frozen" rides a TITLE marker
# instead of stealing that axis. It never enters the SoT: this row's title
# column is deriver-owned and the inbound lane now refuses travel rows outright
# (rejected_travel_block).
TRAVEL_PINNED_MARK = u"📌 "
PREP_FLAT_TITLE = u"외출준비"     # "외출준비" (flat)
GROUP_COMPANION_THRESHOLD = 3       # >=3 companions -> group label; <3 -> names

# Home / work labels. These are the ONE pair of render words the hidden-presence
# rule has to match on, because place_render and title arrive as user-language
# strings from the DB (place.display_name || place.name, timeblock.title) -- the
# rule has no ascii key to join on pre-migration. So the words are resolved
# through the locale bundle (same seam + same zero-delta fallback contract as
# WITH_PREFIX above) and code refers to them ONLY by these ascii constants: no
# Korean literal is ever a comparison target or a dict key in this module.
# An instance whose data language is not ko publishes mirror.label.* in its own
# bundle and the rule follows the data. The fallbacks are the ko literals, which
# guarantees zero delta on an instance with no bundle entry.
HOME_PLACE_LABEL = mirror_i18n.t("mirror.label.home", u"집")          # 집
WORK_TITLE_LABEL = mirror_i18n.t("mirror.label.work", u"회사")    # 회사

# Per-connection schema-presence cache (feature detect). Keyed by id(conn);
# a connection is a live handle so id-reuse across the cache lifetime is a
# non-issue (closed handles are simply never queried again).
_SCHEMA_PRESENCE_CACHE = {}


def _has_column(src_conn, table, column):
    """True if `table.column` exists. Cached. Any error -> False (degrade to
    the pre-migration fallback, never raise)."""
    key = (id(src_conn), "col", table, column)
    hit = _SCHEMA_PRESENCE_CACHE.get(key)
    if hit is not None:
        return hit
    present = False
    try:
        cols = [r[1] for r in src_conn.execute(
            "PRAGMA table_info(%s)" % table).fetchall()]
        present = column in cols
    except sqlite3.Error:
        present = False
    _SCHEMA_PRESENCE_CACHE[key] = present
    return present


def _has_table(src_conn, table):
    """True if `table` exists. Cached. Any error -> False."""
    key = (id(src_conn), "tbl", table)
    hit = _SCHEMA_PRESENCE_CACHE.get(key)
    if hit is not None:
        return hit
    present = False
    try:
        row = src_conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,)).fetchone()
        present = row is not None
    except sqlite3.Error:
        present = False
    _SCHEMA_PRESENCE_CACHE[key] = present
    return present


def _reset_schema_presence_cache():
    """Test seam: drop the feature-detect cache (a scratch DB gets migrated
    mid-test so the same-id handle must re-probe)."""
    _SCHEMA_PRESENCE_CACHE.clear()


def _enrich_render_place(src_conn, event):
    """Attach event['place_render'] = place.display_name || place.name for the
    row's place_id (장소 part). Feature-detects place.display_name; on a
    pre-migration DB it uses place.name. Omitted key when no place."""
    if "place_render" in event or not event.get("place_id"):
        return
    try:
        if _has_column(src_conn, "place", "display_name"):
            prow = src_conn.execute(
                "SELECT COALESCE(NULLIF(display_name,''), name) "
                "FROM place WHERE id=?", (event["place_id"],)).fetchone()
        else:
            prow = src_conn.execute(
                "SELECT name FROM place WHERE id=?",
                (event["place_id"],)).fetchone()
    except sqlite3.Error:
        return
    if prow is not None and prow[0]:
        event["place_render"] = prow[0]


def _person_render_names(src_conn, event):
    """List of per-person render labels (display_name || name), person_id
    order. Feature-detects persons.display_name."""
    if event.get("id") is None:
        return []
    try:
        if _has_column(src_conn, "persons", "display_name"):
            rows = src_conn.execute(
                "SELECT COALESCE(NULLIF(p.display_name,''), p.name) "
                "FROM event_persons ep JOIN persons p ON p.id = ep.person_id "
                "WHERE ep.event_id=? ORDER BY ep.person_id",
                (event["id"],)).fetchall()
        else:
            rows = src_conn.execute(
                "SELECT p.name FROM event_persons ep "
                "JOIN persons p ON p.id = ep.person_id "
                "WHERE ep.event_id=? ORDER BY ep.person_id",
                (event["id"],)).fetchall()
    except sqlite3.Error:
        return []
    return [r[0] for r in rows if r[0]]


def _group_display_for_persons(src_conn, event):
    """>=3 companions render as their most-common group's display label.
    Membership source = persons.groups (comma-list); the label comes from
    person_group.display_name (feature-detected; missing table -> use the raw
    group name). Returns the label or None (no clear group / no data)."""
    if event.get("id") is None:
        return None
    try:
        group_cells = [r[0] for r in src_conn.execute(
            "SELECT p.groups FROM event_persons ep "
            "JOIN persons p ON p.id = ep.person_id "
            "WHERE ep.event_id=?", (event["id"],)).fetchall()]
    except sqlite3.Error:
        return None
    counts = {}
    for cell in group_cells:
        if not cell:
            continue
        for tok in cell.split(","):
            g = tok.strip()
            if g:
                counts[g] = counts.get(g, 0) + 1
    if not counts:
        return None
    # most-common group; ties break on name for determinism.
    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    if _has_table(src_conn, "person_group"):
        try:
            grow = src_conn.execute(
                "SELECT COALESCE(NULLIF(display_name,''), name) "
                "FROM person_group WHERE name=?", (top,)).fetchone()
            if grow is not None and grow[0]:
                return grow[0]
        except sqlite3.Error:
            pass
    return top   # no lookup table / no match -> raw group name


def _enrich_render_companions(src_conn, event):
    """Attach event['companions_render'] = the 동행 part string:
    >=3 companions -> most-common group's display label; <3 -> per-person
    render labels joined; omitted when there are no companions."""
    if "companions_render" in event:
        return
    names = _person_render_names(src_conn, event)
    if not names:
        return
    if len(names) >= GROUP_COMPANION_THRESHOLD:
        label = _group_display_for_persons(src_conn, event)
        if label:
            event["companions_render"] = label
            return
    event["companions_render"] = ", ".join(names)


def _enrich_render_travel(src_conn, event):
    """Attach travel-block render support (block_class='travel'):
      event['travel_to']   = destination place render (place_id, display_name).
      event['travel_from'] = the 출발 = the presence place at the block's start
        (per the P1 'travel from-place from presence' rule, NOT hardcoded 집).
    Feature-detect place.display_name; a missing presence -> travel_from unset
    (title seam falls back to base title, never crashes)."""
    if event.get("block_class") != "travel":
        return
    # destination: the block's own place_id
    if event.get("place_id") and "travel_to" not in event:
        try:
            if _has_column(src_conn, "place", "display_name"):
                drow = src_conn.execute(
                    "SELECT COALESCE(NULLIF(display_name,''), name) "
                    "FROM place WHERE id=?", (event["place_id"],)).fetchone()
            else:
                drow = src_conn.execute(
                    "SELECT name FROM place WHERE id=?",
                    (event["place_id"],)).fetchone()
            if drow is not None and drow[0]:
                event["travel_to"] = drow[0]
        except sqlite3.Error:
            pass
    # origin: the presence covering this block's start instant
    if "travel_from" not in event and event.get("start_at"):
        try:
            disp = ("COALESCE(NULLIF(pl.display_name,''), pl.name)"
                    if _has_column(src_conn, "place", "display_name")
                    else "pl.name")
            frow = src_conn.execute(
                "SELECT " + disp + " FROM timeblock tb "
                "JOIN place pl ON pl.id = tb.place_id "
                "WHERE tb.kind='presence' AND tb.settled_at IS NULL "
                "AND COALESCE(tb.block_class,'') != 'travel' "
                "AND tb.start_at <= ? "
                "AND (tb.end_at IS NULL OR tb.end_at > ?) "
                "ORDER BY tb.start_at DESC LIMIT 1",
                (event["start_at"], event["start_at"])).fetchone()
            if frow is not None and frow[0]:
                event["travel_from"] = frow[0]
        except sqlite3.Error:
            pass


def enrich_event(src_conn, event):
    """Attach place-FK-resolved location + event_persons names to a raw
    event-row dict so every projection/body/hash site sees the SAME values.
    - location: the legacy free-text column WINS when set; an empty location
      with place_id set resolves to place.name (the DGN-579 gap fix).
    - persons: comma-joined names from event_persons (person_id order,
      deterministic), under key 'persons'.
    - DGN-849: config-declared derived fields (mirror_colors resolve specs)
      are attached generically -- single-hop FK lookups the kit declares so
      its color-source axis is reachable with zero adapter knowledge of the
      kit's tables. Identifiers are validated by mirror_colors; a failing
      lookup only skips itself (runs LAST, cannot break location/persons).
    Tolerant: no conn / bare test dicts / sqlite errors -> dict unchanged.
    Mutates and returns the dict (callers pass throwaway dict(row))."""
    if src_conn is None or not isinstance(event, dict):
        return event
    try:
        if not event.get("location") and event.get("place_id"):
            prow = src_conn.execute(
                "SELECT name FROM place WHERE id=?",
                (event["place_id"],)).fetchone()
            if prow is not None and prow[0]:
                event["location"] = prow[0]
        if "persons" not in event and event.get("id") is not None:
            names = [r[0] for r in src_conn.execute(
                "SELECT p.name FROM event_persons ep "
                "JOIN persons p ON p.id = ep.person_id "
                "WHERE ep.event_id=? ORDER BY ep.person_id",
                (event["id"],)).fetchall()]
            event["persons"] = ", ".join(n for n in names if n)
        # SPEC 2026-08-14 (calendar render 행위 . 장소 . 동행): attach the
        # render-support fields the pure title seam consumes. All DB access
        # (display_name columns + person_group table) is FEATURE-DETECTED
        # here so the adapter never crashes on a pre-migration DB -- a missing
        # column/table degrades to name/existing behavior. Kept off the hot
        # persons/location path (runs after it) and inside the try so a
        # sqlite error only skips the render extras.
        _enrich_render_place(src_conn, event)
        _enrich_render_companions(src_conn, event)
        _enrich_render_travel(src_conn, event)
    except sqlite3.Error:
        pass
    for field, spec in mirror_colors.resolves().items():
        if field in event:
            continue
        ref = event.get(spec["via"])
        if ref is None:
            continue
        try:
            frow = src_conn.execute(
                "SELECT %s FROM %s WHERE %s=?"
                % (spec["select"], spec["table"], spec["key"]),
                (ref,)).fetchone()
        except sqlite3.Error:
            continue
        if frow is not None and frow[0] is not None:
            event[field] = frow[0]
    return event


def _compose_description(event):
    """GCal description = note + trailing persons with-line (when persons
    exist). The with-line is ALWAYS the last line -- that position is the
    strip contract on the inbound side."""
    note = event.get("note") or ""
    persons = event.get("persons") or ""
    if not persons:
        return note
    line = WITH_PREFIX + persons
    return (note + "\n\n" + line) if note else line


def _strip_desc_persons(description):
    """Inbound: split a GCal description into (note, persons_str). Contract:
    the with-line is the LAST line and starts with WITH_PREFIX verbatim; a
    preceding blank separator line is consumed with it. A user note whose
    own last line matches the prefix is indistinguishable by construction --
    accepted residual ambiguity (documented in the DGN-579 bundle spec)."""
    desc = description or ""
    lines = desc.split("\n")
    if lines and lines[-1].startswith(WITH_PREFIX):
        persons = lines[-1][len(WITH_PREFIX):].strip()
        lines = lines[:-1]
        while lines and lines[-1] == "":
            lines.pop()
        return "\n".join(lines), persons
    return desc, ""


def _event_subtype(event):
    """DGN-849: subtype key for timed-block labeling, or None (plain row).
    kind/block_class/derived_role are insert-immutable framework columns,
    so the classification is deterministic and never flips in steady state:
        kind='presence'            -> 'presence' (stay block)
        derived_role='before_prep' -> 'prep'
        block_class='travel'       -> 'travel'   (before/after move blocks)
    """
    if event.get("kind") == "presence":
        return "presence"
    if event.get("derived_role") == "before_prep":
        return "prep"
    if event.get("block_class") == "travel":
        return "travel"
    return None


def _join_parts(*parts):
    """행위 · 장소 · 동행: join the non-empty parts with RENDER_SEP."""
    return RENDER_SEP.join(p for p in parts if p)


def is_hidden_presence(event):
    """SPEC 집쉼: home NON-WORK presence is HIDDEN (not mirrored). A presence
    row at the base/home place whose title is NOT the normalized work label
    (회사) is a home-rest stay -> HIDDEN. Work-from-home (재택) normalizes to
    title 회사 and is NOT hidden (renders 회사 · 집). Travel presence
    (block_class='travel') is never a home-rest stay. Uses only dict fields
    (enrich_event fills place_render / location); safe pre-migration."""
    if event.get("kind") != "presence":
        return False
    if event.get("block_class") == "travel":
        return False
    place = event.get("place_render") or event.get("location") or ""
    if place != HOME_PLACE_LABEL:
        return False
    return (event.get("title") or "") != WORK_TITLE_LABEL


def _render_base_title(event):
    """SPEC 2026-08-14 calendar render model. Returns the base surface title
    per the per-kind/per-case table (before status decoration). Pure over the
    enriched dict; all parts CONDITIONAL, joined by RENDER_SEP.
      행위 = timeblock.title (omit if empty)
      장소 = place_render (place.display_name || place.name; omit if none)
      동행 = companions_render (>=3 -> group label; <3 -> person labels)
    EXCEPTIONS:
      block_class='travel'       -> "이동: {출발} → {도착}" (출발 from presence)
      derived_role='before_prep' -> "외출준비" (flat)
    Presence work cases fall out of the generic 행위·장소·동행 join once the
    title is normalized to 회사 (재택 -> 회사 · 집 ; 출근 -> 회사 · 사무실).
    집쉼 (home non-work) is HIDDEN upstream (is_hidden_presence) and never
    reaches a render; here it degrades to its bare title as a belt."""
    # EXCEPTION: prep block -> flat.
    if event.get("derived_role") == "before_prep":
        return PREP_FLAT_TITLE
    # EXCEPTION: travel move block -> "이동: 출발 → 도착".
    if event.get("block_class") == "travel":
        mark = TRAVEL_PINNED_MARK if event.get("derived_pinned") else ""
        # D2: the arrival-buffer block is a travel-CLASS row (it needs the
        # derived lifecycle: pre-clean, tombstone, unique-live index) but it is
        # NOT a move -- it carries its own product title ("약속 버퍼: <anchor>"),
        # which is the whole point: a named block explains itself, an
        # unexplained 15-minute gap does not.
        if event.get("derived_role") == "buffer":
            return mark + (event.get("title") or "")
        origin = event.get("travel_from") or HOME_PLACE_LABEL  # P1: from presence, home fallback
        dest = (event.get("travel_to") or event.get("place_render")
                or event.get("location") or "")
        if dest:
            return mark + (TRAVEL_MOVE_FMT % (origin, dest))
        # no destination resolvable -> degrade to the row's own title.
        return mark + (event.get("title") or "")
    # Generic 행위 · 장소 · 동행 (presence work / appointment / timed task).
    act = event.get("title") or ""
    place = event.get("place_render") or event.get("location") or ""
    companions = event.get("companions_render") or ""
    return _join_parts(act, place, companions)


def _projected_cal_title(event):
    """ONE title seam for projection AND body build (F1). Compose order:
    product render base (SPEC 2026-08-14) -> status decoration.
    - base: the per-kind/per-case render model (_render_base_title). mirror/
      is kit_life PRODUCT, so the render words/separator live in
      this product adapter (the DGN-849 kit color seam still owns colorId and
      the terminal-status decoration below).
    - status decoration (D2): ONLY when the kit color config is armed AND the
      kit declares a decoration for that terminal status -- unchanged;
      no color config / no declared decoration -> undecorated (zero-delta)."""
    title = _render_base_title(event)
    if mirror_colors.active():
        outcome = event.get("settled_outcome")
        status = event.get("status", "open")
        decor = None
        if outcome == "done" or status == "done":
            decor = mirror_colors.status_decor("done")
        elif status == "expired":
            decor = mirror_colors.status_decor("expired")
        if decor:
            title = decor + title
    return title


def _strip_render_suffix(title):
    """SPEC inbound parse (lenient): a mirror-rendered title is
    "행위 · 장소 · 동행"; only 행위 (the FIRST segment) is SoT title data --
    장소/동행 are rendered from place_id / event_persons, never from the title
    column. Peel the render suffix so an inbound edited title write-back
    carries only the 행위 part. Lenient separators: the ` · ` middle-dot
    render sep, a bare middle-dot, or a colon (manual edits). The 이동 render
    "이동: 출발 → 도착" is presence/travel -- travel inbound edits do not reach
    this title write path (presence is rejected wholesale; travel blocks are
    derived, not user-content-edited), so splitting on ':' here only affects a
    genuinely appointment/task title the user typed with a colon -- accepted
    residual (same class as the DGN-579 with-line ambiguity)."""
    t = title or ""
    for sep in (RENDER_SEP, u"·", u":"):
        if sep in t:
            return t.split(sep, 1)[0].strip()
    return t


def _strip_title_decorations(title):
    """Inbound guard: peel mirror-authored decorations (config-declared status
    decoration) AND the SPEC render suffix (장소/동행) off a surface title so
    they can NEVER enter the SoT title column through an inbound content
    write-back. Config-less instances still get the render-suffix peel (the
    render is product content, not gated on the color config); leading-only,
    the user's own 행위 text is preserved."""
    t = title or ""
    for decor in mirror_colors.status_decor_values():
        if decor and t.startswith(decor):
            t = t[len(decor):]
            break
    fmt = mirror_colors.subtype_fmt()
    for label in mirror_colors.subtype_label_values():
        prefix = fmt.format(label=label, title="")
        if prefix and t.startswith(prefix):
            t = t[len(prefix):]
            break
    return _strip_render_suffix(t)


# ---------------------------------------------------------------------------
# F1: canonical surface projections (SAME dict on push and inbound sides)
# All values in sqlite representation, canonicalized: None-note -> '',
# missing surface keys -> defaults matching what push emits.
# ---------------------------------------------------------------------------

def event_to_cal_status(event: dict):
    """W4: (gcal_status, colorId|None).
    DGN-849 D2 axis ruling (deterministic, no dual ownership):
      - kit color config ARMED (mirror_colors.active()): the declared
        color-source axis OWNS colorId exclusively; the legacy status color
        markers (done->'10', expired->'8') are retired and status rides the
        gcal status field + title decoration (_projected_cal_title).
      - NO color config: prior behavior byte-identical (legacy markers
        kept) -- instances that did not opt in see zero delta."""
    outcome = event.get("settled_outcome")
    status = event.get("status", "open")
    if outcome == "abandoned" or status == "abandoned":
        return "cancelled", None
    if mirror_colors.active():
        return "confirmed", mirror_colors.color_for_event(event)
    if outcome == "done" or status == "done":
        return "confirmed", "10"   # done marker (sage)
    if status == "expired":
        return "confirmed", "8"    # expired marker (graphite)
    return "confirmed", None


def calendar_projection_from_event(event: dict) -> dict:
    """What the Calendar surface SHOULD hold for this event, expressed in
    sqlite representation. start/end are roundtripped through the exact push
    conversion so placeholder ends (open-ended) hash identically.
    DGN-579 bundle: 'persons' joins the projection (enrich_event supplies
    it; bare dicts default to '').
    DGN-849: presence projects as a subtype-labeled TIMED block on the row's
    OWN schedule (supersedes the D-C3 all-day banner) -- one generic branch
    for every row; the title seam carries the subtype label. Presence stays
    non-blocking via slot_exclusive=0 (schema CHECK) -> transparent."""
    gcal_status, color_id = event_to_cal_status(event)
    tz_name = event.get("display_tz") or DISPLAY_TZ_NAME
    start_at = end_at = None
    if event.get("schedule_kind") in ("timed", "all_day") and event.get("start_at"):
        s_obj, e_obj = utc_instant_to_gcal_datetime(
            event["start_at"], event.get("end_at"), event["schedule_kind"],
            tz_name)
        start_at, end_at = gcal_datetime_to_utc_instants(s_obj, e_obj, tz_name)
    return {
        "title": _projected_cal_title(event),
        "note": event.get("note") or "",
        "location": event.get("location") or "",
        "start_at": start_at,
        "end_at": end_at,
        "schedule_kind": event.get("schedule_kind"),
        "gcal_status": gcal_status,
        "color_id": color_id,
        "transparency": "opaque" if event.get("slot_exclusive") else "transparent",
        "persons": event.get("persons") or "",
    }


def calendar_projection_from_item(item: dict, display_tz=DISPLAY_TZ_NAME) -> dict:
    """Inbound GCal item -> the SAME canonical dict (F1). m16: missing
    description key = explicit empty note. DGN-579 bundle: the trailing
    with-line is split out of the description into 'persons' so an
    unedited surface item hashes identically to the push snapshot."""
    start_obj = item.get("start", {}) or {}
    schedule_kind = "all_day" if "date" in start_obj else "timed"
    start_at, end_at = gcal_datetime_to_utc_instants(
        start_obj, item.get("end", {}) or {}, display_tz)
    note, persons = _strip_desc_persons(item.get("description", "") or "")
    return {
        "title": item.get("summary", "") or "",
        "note": note,
        "location": item.get("location", "") or "",
        "start_at": start_at,
        "end_at": end_at,
        "schedule_kind": schedule_kind,
        "gcal_status": item.get("status", "confirmed"),
        "color_id": item.get("colorId"),
        "transparency": item.get("transparency", "opaque"),
        "persons": persons,
    }


def tasks_projection_from_event(event: dict) -> dict:
    outcome = event.get("settled_outcome")
    status = event.get("status", "open")
    completed = (outcome == "done" or status == "done")
    return {
        "title": event.get("title") or "",
        "note": event.get("note") or "",
        "due_date": task_due_from_event(event),
        "status": "completed" if completed else "needsAction",
    }


def tasks_projection_from_item(item: dict) -> dict:
    due = item.get("due")
    return {
        "title": item.get("title", "") or "",
        "note": _strip_sentinel(item.get("notes", "")),
        "due_date": due[:10] if due else None,
        "status": item.get("status", "needsAction"),
    }


def projection_hash(proj: dict) -> str:
    return hashlib.sha256(json.dumps(proj, sort_keys=True).encode()).hexdigest()


def compute_push_hash(surface: str, event: dict) -> str:
    proj = (calendar_projection_from_event(event) if surface == "calendar"
            else tasks_projection_from_event(event))
    return projection_hash(proj)


def store_push_snapshot(conn, event_ulid, surface, proj: dict,
                        calendar_id=None):
    """DGN-294/DGN-364 (V15): push_snapshot carries the target calendar id --
    the bookkeeping dimension tombstones/updates need to hit the RIGHT
    calendar. calendar_id=None preserves the stored value via COALESCE
    (tasks-surface writes / legacy callers)."""
    conn.execute(
        "INSERT INTO push_snapshot(event_ulid, surface, field_hash, "
        "field_json, pushed_at, calendar_id) VALUES(?,?,?,?,?,?) "
        "ON CONFLICT(event_ulid) DO UPDATE SET "
        "surface=excluded.surface, field_hash=excluded.field_hash, "
        "field_json=excluded.field_json, pushed_at=excluded.pushed_at, "
        "calendar_id=COALESCE(excluded.calendar_id, "
        "push_snapshot.calendar_id)",
        (event_ulid, surface, projection_hash(proj),
         json.dumps(proj, sort_keys=True, ensure_ascii=False), _now_iso(),
         calendar_id))
    conn.commit()


def snapshot_calendar_id(conn, event_ulid):
    row = conn.execute(
        "SELECT calendar_id FROM push_snapshot WHERE event_ulid=?",
        (event_ulid,)).fetchone()
    return row["calendar_id"] if row else None


def load_push_snapshot(conn, event_ulid):
    """Returns (hash, projection_dict) or (None, {})."""
    row = conn.execute(
        "SELECT field_hash, field_json FROM push_snapshot WHERE event_ulid=?",
        (event_ulid,)).fetchone()
    if row is None:
        return None, {}
    try:
        return row["field_hash"], json.loads(row["field_json"])
    except Exception:
        return row["field_hash"], {}


# ---------------------------------------------------------------------------
# F3: foreign-item guard
# ---------------------------------------------------------------------------

def _stamped_ulid(item: dict):
    """AUTHORITATIVE ownership: the ulid WE stamped into
    extendedProperties.private. Presence of this is proof; absence is not
    disproof (see _extract_ulid)."""
    return (item.get("extendedProperties", {}) or {}).get("private", {}).get("ulid")


def _extract_ulid(item: dict):
    """Ours iff extendedProperties.private.ulid present OR id matches
    ^[0-9a-f]{32}$ (our deterministic encoding). Else None = foreign.

    WARNING -- the id-shape branch is a HEURISTIC, not proof. Google's own web
    UI mints event ids as a 32-char UUID hex, which is shape-identical to our
    encoding, so every event the OWNER created in the Google Calendar UI looks
    like ours here. Callers that can reach the SoT must use
    _extract_owned_ulid() instead; this bare form stays for the read-only
    call sites (e.g. the cancelled-item circuit-breaker tally) where a
    false positive is harmless."""
    ulid = _stamped_ulid(item)
    if ulid:
        return ulid
    event_id = item.get("id", "") or ""
    if _HEX32_RE.match(event_id):
        return hex_to_ulid(event_id)
    return None


def _extract_owned_ulid(item: dict, src_conn, state_conn=None):
    """Ownership decided by EVIDENCE, not by id shape.

    Fixes a silent data-loss path: an owner-created Google Calendar event whose
    auto-generated id happens to be 32 hex chars (which is what the Google web
    UI mints -- a UUID in hex) was read as OURS, decoded to a ulid that exists
    nowhere, and dropped as `inbound_unknown_ulid`. It was never adopted, and
    the owner got no signal at all. Live evidence: 25 such log lines, i.e. 25
    owner-created events silently discarded.

    Rules:
      - a stamped extendedProperties ulid is authoritative -> ours, always.
      - an id-SHAPE match only counts as ours when the decoded ulid actually
        EXISTS in the SoT. If it does not, the shape was a coincidence and the
        item is FOREIGN -> the caller adopts it.
      - no SoT handle (src_conn None) -> fall back to the shape heuristic
        (cannot verify; preserves the old behavior for read-only callers).

    Returns the ulid when the item is ours, else None."""
    stamped = _stamped_ulid(item)
    if stamped:
        return stamped
    event_id = item.get("id", "") or ""
    if not _HEX32_RE.match(event_id):
        return None
    decoded = hex_to_ulid(event_id)
    if src_conn is None:
        return decoded
    row = src_conn.execute(
        "SELECT 1 FROM timeblock WHERE ulid=?;", (decoded,)).fetchone()
    if row is not None:
        return decoded
    if state_conn is not None:
        mirror_log(state_conn, "foreign_hex_id", None,
                   "calendar id=%s: 32-hex id is not ours (no SoT row for %s)"
                   " -- treating as owner-created, adopting"
                   % (event_id, decoded))
    return None


# ---------------------------------------------------------------------------
# Bootstrap (m12: description-marker re-discovery for the calendar)
# ---------------------------------------------------------------------------

def _frozen_cal_marker(conn):
    """DGN-268 S1 (H3): the calendar description marker, KV-first. The first
    bootstrap freezes the derived marker into mirror_state (key 'cal_marker');
    every later run reads that frozen value. This makes a later rename of
    MIRROR_CAL_NAME / the agent slug safe -- the calendar is re-discovered by
    the marker that CREATED it, never orphaned. Legacy state DBs with no
    frozen marker fall back to the derived value (and freeze it)."""
    marker = get_state(conn, "cal_marker")
    if not marker:
        marker = CAL_DESCRIPTION_MARKER
        set_state(conn, "cal_marker", marker)
    return marker


class BootstrapAmbiguous(RuntimeError):
    """DGN-268 S2: bootstrap found a same-name surface that does NOT carry our
    marker -- a foreign/ambiguous calendar or tasklist that must NOT be
    silently adopted or written into. Carries the candidate(s) so the S4
    onboarding layer can ask the user 'adopt this existing one, or create a
    new one under a different name?'. Setting MIRROR_ADOPT_UNMARKED=true (the
    user answered 'adopt') suppresses the signal and adopts + stamps."""
    def __init__(self, candidates):
        self.candidates = candidates   # list of dicts (surface/candidate_id/..)
        summary = ", ".join(
            "%s '%s' (%s)" % (c["surface"], c["summary"], c["candidate_id"])
            for c in candidates)
        super().__init__("ambiguous existing surface(s): %s" % summary)


def _adopt_unmarked_enabled():
    """Config gate MIRROR_ADOPT_UNMARKED (default false). Onboarding sets it
    true after the user explicitly chooses to adopt an existing same-name
    surface. Only 'true'/'1'/'yes' (case-insensitive) enable adoption."""
    val = (_load_conf().get("MIRROR_ADOPT_UNMARKED") or "").strip().lower()
    return val in ("true", "1", "yes", "on")


def bootstrap(conn):
    """Resolve (or create) the agent's calendar + tasklist, return (cal_id,
    tl_id). Adopt-or-create policy (DGN-268 S2): a marker match is ours ->
    adopt; a bare summary/title match WITHOUT our marker is ambiguous ->
    NEVER auto-adopt or write into it unless MIRROR_ADOPT_UNMARKED=true (then
    adopt + stamp the marker so the next run is unambiguous); no match ->
    create + stamp. Ambiguous surfaces with the gate off collect into a
    single BootstrapAmbiguous signal (no state mutation, no inserts) for the
    onboarding layer to resolve."""
    cal_id = get_state(conn, "agent_calendar_id")
    tl_id = get_state(conn, "agent_tasklist_id")
    cal_marker = _frozen_cal_marker(conn)
    adopt_unmarked = _adopt_unmarked_enabled()

    if cal_id:
        try:
            gws("calendar", "calendars", "get",
                "--params", json.dumps({"calendarId": cal_id}))
        except Exception:
            cal_id = None
    if tl_id:
        try:
            gws("tasks", "tasklists", "get",
                "--params", json.dumps({"tasklist": tl_id}))
        except Exception:
            tl_id = None

    # --- Phase 1: resolve without mutating state. Decide per surface whether
    # it is already known, adoptable (marker or gated summary), ambiguous, or
    # to-be-created. Nothing is inserted/adopted until all ambiguity is clear.
    ambiguous = []
    cal_action = tl_action = None   # ("adopt", id) | ("create", None) | None

    if not cal_id:
        # m12: primary re-discovery key = description marker (survives rename);
        # a bare summary match is NOT trusted (S2: could be a foreign cal).
        cal_list = gws("calendar", "calendarList", "list")
        marker_hit = summary_hit = None
        for item in cal_list.get("items", []):
            if cal_marker in (item.get("description") or ""):
                marker_hit = item["id"]
                break
            if item.get("summary") == SANDBOX_CAL_SUMMARY:
                summary_hit = item["id"]
        if marker_hit:
            cal_action = ("adopt", marker_hit)
        elif summary_hit and adopt_unmarked:
            cal_action = ("adopt_unmarked", summary_hit)
        elif summary_hit:
            ambiguous.append({"surface": "calendar",
                              "candidate_id": summary_hit,
                              "summary": SANDBOX_CAL_SUMMARY})
        else:
            cal_action = ("create", None)

    if not tl_id:
        # Tasklist has no description field -> title match is the ONLY
        # re-discovery key. That makes a bare title collision even more
        # likely to be foreign, so the same S2 guard applies.
        tl_list = gws("tasks", "tasklists", "list")
        title_hit = None
        for item in tl_list.get("items", []):
            if item.get("title") == SANDBOX_TASKLIST_TITLE:
                title_hit = item["id"]
                break
        if title_hit and adopt_unmarked:
            tl_action = ("adopt_unmarked", title_hit)
        elif title_hit:
            ambiguous.append({"surface": "tasklist",
                              "candidate_id": title_hit,
                              "summary": SANDBOX_TASKLIST_TITLE})
        elif not title_hit:
            tl_action = ("create", None)

    if ambiguous:
        # Guard off + a foreign same-name surface present: signal, do NOT
        # create or adopt anything this run (no partial bootstrap).
        raise BootstrapAmbiguous(ambiguous)

    # --- Phase 2: commit the resolved actions (all unambiguous now). ---
    if cal_action is not None:
        verb, hit = cal_action
        if verb == "adopt":
            cal_id = hit
            print("[bootstrap] Re-discovered calendar (marker): %s" % cal_id)
        elif verb == "adopt_unmarked":
            cal_id = hit
            # Stamp our marker into the description so the next run is
            # unambiguous (marker match, no longer summary-only).
            _stamp_calendar_marker(cal_id, cal_marker)
            print("[bootstrap] Adopted unmarked calendar + stamped: %s" % cal_id)
        else:  # create
            r = gws("calendar", "calendars", "insert", body={
                "summary": SANDBOX_CAL_SUMMARY,
                "description": "%s -- %s" % (cal_marker, CAL_DESCRIPTION_TEXT),
                "timeZone": DISPLAY_TZ_NAME})
            cal_id = r["id"]
            print("[bootstrap] Created calendar: %s" % cal_id)
    set_state(conn, "agent_calendar_id", cal_id)

    if tl_action is not None:
        verb, hit = tl_action
        if verb == "adopt_unmarked":
            tl_id = hit
            print("[bootstrap] Adopted unmarked tasklist: %s" % tl_id)
        else:  # create
            r = gws("tasks", "tasklists", "insert",
                    body={"title": SANDBOX_TASKLIST_TITLE})
            tl_id = r["id"]
            print("[bootstrap] Created tasklist: %s" % tl_id)
    set_state(conn, "agent_tasklist_id", tl_id)
    return cal_id, tl_id


def _stamp_calendar_marker(cal_id, cal_marker):
    """Adopt-unmarked: write our marker into an existing calendar's
    description so future bootstraps re-discover it by marker (unambiguous),
    not by the weaker summary match. Preserves any existing description text
    the user had, appending our marker line if absent."""
    try:
        cur = gws("calendar", "calendars", "get",
                  "--params", json.dumps({"calendarId": cal_id}))
    except Exception:
        cur = {}
    existing = (cur.get("description") or "").strip()
    if cal_marker in existing:
        return
    marker_line = "%s -- %s" % (cal_marker, CAL_DESCRIPTION_TEXT)
    new_desc = (existing + "\n" + marker_line) if existing else marker_line
    gws("calendar", "calendars", "patch",
        "--params", json.dumps({"calendarId": cal_id}),
        body={"description": new_desc})


# ---------------------------------------------------------------------------
# DGN-364 4.1: V15 provision (fresh-mint engrave path) + engrave router.
# bootstrap() above is DEMOTED to legacy-maintenance API: nothing in the
# fresh-mint path calls it, but it IS the routed target for legacy-provenance
# repair (M3 routing table in ensure_mirror_engraved). It remains the writer
# of record for agent_* keys on existing legacy instances.
# ---------------------------------------------------------------------------

def _category_marker_line(cal_marker, key):
    """Category calendar description: '<frozen cal_marker> category=<key>'."""
    return "%s category=%s" % (cal_marker, key)


def _has_category_token(description, key):
    """M4 rule 2 (normative): category token match is EXACT-TOKEN equality --
    the description is whitespace-split and one token must equal
    'category=<key>' verbatim. Substring/containment matching is FORBIDDEN
    (a first-containment scan can cross-bind categories, producing a
    permanent wrong-calendar drift storm)."""
    return ("category=%s" % key) in (description or "").split()


def _stamp_category_marker(cal_id, cal_marker, key):
    """Gated adopt of an unmarked same-name calendar: stamp marker + category
    token (S2 semantics, per category). Preserves existing description."""
    try:
        cur = gws("calendar", "calendars", "get",
                  "--params", json.dumps({"calendarId": cal_id}))
    except Exception:
        cur = {}
    existing = (cur.get("description") or "").strip()
    if cal_marker in existing and _has_category_token(existing, key):
        return
    marker_line = _category_marker_line(cal_marker, key)
    new_desc = (existing + "\n" + marker_line) if existing else marker_line
    gws("calendar", "calendars", "patch",
        "--params", json.dumps({"calendarId": cal_id}),
        body={"description": new_desc})


def _resolve_category_names(i18n_t=None):
    """Prefix-over-i18n naming (4.1, dec-033): MIRROR_CAL_NAME (user's chosen
    name from kit_life setup step 4) becomes the display PREFIX of the
    category names; the pattern is owned by the i18n strings; absent config
    falls back to the i18n defaults (prefix 'Dogany'). Returns
    ({key: name}, checklist_name).

    Name-set coupling (OQC-1, normative): the S2 same-name ambiguity guard's
    name set derives from THIS function -- the same source as the creation
    names -- never a second hardcoded list. NOTE (m5): MIRROR_TASKLIST_NAME
    is DEPRECATED for the multi path -- the checklist display name comes from
    prefix-over-i18n here; MIRROR_TASKLIST_NAME is consulted ONLY by legacy
    bootstrap()."""
    if i18n_t is None:
        try:
            from mirror_i18n import t as i18n_t
        except Exception:
            i18n_t = lambda key, fallback, **kw: fallback.format(**kw) \
                if kw else fallback
    prefix = _load_conf().get("MIRROR_CAL_NAME") \
        or _CATEGORY_NAME_DEFAULT_PREFIX
    names = {key: i18n_t(i18n_key, fallback, name=prefix)
             for key, (i18n_key, fallback) in CAL_I18N.items()}
    checklist_name = i18n_t(CHECKLIST_I18N[0], CHECKLIST_I18N[1], name=prefix)
    return names, checklist_name


def provision_category_calendars(state_conn, i18n_t=None):
    """C2 generalized (DGN-364 4.1, idempotent per key): resolve-or-create the
    3 category calendars + the checklist tasklist, engrave the 4 V15 ids.
    Re-runs fill only holes (also the remedy for a V15-partial engrave, 6.2).

    Adopt rules (NORMATIVE, M4):
      1. Marked-orphan adopt is MANDATORY: before creating, look for a
         calendar whose description carries the frozen cal_marker AND the
         exact category token -- found -> adopt (engrave its id), never a
         twin, never a raise (closes the provision crash window, TM-4).
      2. Category token match = EXACT-TOKEN equality (see
         _has_category_token).
      3. Unmarked same-name calendar: BootstrapAmbiguous exactly like
         bootstrap(), unless MIRROR_ADOPT_UNMARKED authorizes adoption; on
         gated adopt the marker + category token are stamped.
      4. Tasklist adopt stays title+gate (no description field): reuse-and-
         rename when a legacy tasklist id is engraved (generic key
         agent_tasklist_id), else title match under the same S2 guard/gate,
         else create.
    Multi-candidate gate scope (m6): a single run can surface up to FOUR
    adopt candidates (3 calendars + 1 tasklist); they collect into ONE
    BootstrapAmbiguous signal so the step-5 dialog consents to the full SET
    in one exchange. No state mutation happens while any ambiguity stands."""
    cal_marker = _frozen_cal_marker(state_conn)
    adopt_unmarked = _adopt_unmarked_enabled()
    names, checklist_name = _resolve_category_names(i18n_t)

    # --- Phase 1: resolve without mutating state (bootstrap discipline). ---
    ambiguous = []
    cal_actions = {}   # key -> ("keep"|"adopt"|"adopt_unmarked"|"create", id)
    cal_list = None
    for key in ("appt", "task", "travel"):
        cal_id = get_state(state_conn, CAL_STATE_KEYS[key])
        if _is_engraved(cal_id):
            cal_actions[key] = ("keep", cal_id)
            continue
        if cal_list is None:
            cal_list = gws("calendar", "calendarList", "list").get("items", [])
        marker_hit = summary_hit = None
        for item in cal_list:
            desc = item.get("description") or ""
            if cal_marker in desc and _has_category_token(desc, key):
                marker_hit = item["id"]
                break
            if item.get("summary") == names[key] and summary_hit is None:
                summary_hit = item["id"]
        if marker_hit:
            cal_actions[key] = ("adopt", marker_hit)
        elif summary_hit and adopt_unmarked:
            cal_actions[key] = ("adopt_unmarked", summary_hit)
        elif summary_hit:
            ambiguous.append({"surface": "calendar",
                              "candidate_id": summary_hit,
                              "summary": names[key]})
        else:
            cal_actions[key] = ("create", None)

    tl_action = None   # ("keep"|"reuse_legacy"|"adopt_unmarked"|"create", id)
    tl_id = get_state(state_conn, CHECKLIST_STATE_KEY)
    if _is_engraved(tl_id):
        tl_action = ("keep", tl_id)
    else:
        # Rule 4: reuse-and-rename the engraved legacy tasklist (generic key
        # agent_tasklist_id -- replaces Ag's literal ag_tasklist_id read).
        legacy = get_state(state_conn, LEGACY_TASKLIST_STATE_KEY)
        if _is_engraved(legacy):
            tl_action = ("reuse_legacy", legacy)
        else:
            tl_list = gws("tasks", "tasklists", "list").get("items", [])
            title_hit = None
            for item in tl_list:
                if item.get("title") == checklist_name:
                    title_hit = item["id"]
                    break
            if title_hit and adopt_unmarked:
                tl_action = ("adopt_unmarked", title_hit)
            elif title_hit:
                ambiguous.append({"surface": "tasklist",
                                  "candidate_id": title_hit,
                                  "summary": checklist_name})
            else:
                tl_action = ("create", None)

    if ambiguous:
        # Gate off + foreign same-name surface(s): signal the FULL candidate
        # set (m6), mutate nothing this run (no partial provision).
        raise BootstrapAmbiguous(ambiguous)

    # --- Phase 2: commit the resolved actions (all unambiguous now). ---
    ids = {}
    for key in ("appt", "task", "travel"):
        verb, hit = cal_actions[key]
        if verb == "keep":
            cal_id = hit
        elif verb == "adopt":
            cal_id = hit
            print("[provision] Adopted marked-orphan calendar (%s): %s"
                  % (key, cal_id))
        elif verb == "adopt_unmarked":
            cal_id = hit
            _stamp_category_marker(cal_id, cal_marker, key)
            print("[provision] Adopted unmarked calendar + stamped (%s): %s"
                  % (key, cal_id))
        else:  # create
            resp = gws("calendar", "calendars", "insert",
                       body={"summary": names[key],
                             "description": _category_marker_line(
                                 cal_marker, key)})
            cal_id = resp["id"]
            print("[provision] Created calendar (%s): %s" % (key, cal_id))
        if verb != "keep":
            set_state(state_conn, CAL_STATE_KEYS[key], cal_id)
        ids[key] = cal_id

    verb, hit = tl_action
    if verb == "keep":
        tl_id = hit
    elif verb == "reuse_legacy":
        gws("tasks", "tasklists", "update", "--params",
            json.dumps({"tasklist": hit}),
            body={"id": hit, "title": checklist_name})
        tl_id = hit
        print("[provision] Renamed legacy tasklist in place: %s" % tl_id)
    elif verb == "adopt_unmarked":
        tl_id = hit
        print("[provision] Adopted unmarked tasklist: %s" % tl_id)
    else:  # create
        resp = gws("tasks", "tasklists", "insert",
                   body={"title": checklist_name})
        tl_id = resp["id"]
        print("[provision] Created tasklist: %s" % tl_id)
    if verb != "keep":
        set_state(state_conn, CHECKLIST_STATE_KEY, tl_id)
    ids["checklist"] = tl_id
    return ids


def ensure_mirror_engraved(state_conn) -> dict:
    """Fresh-setup engrave path (DGN-364 4.1). Routing table (normative, M3):
    resolver outcome                         action
    ---------------------------------------  -----------------------------
    targets resolve (multi or legacy)        return unchanged (no-op)
    MirrorUnconfigured (nothing engraved)    provision_category_calendars
    MirrorConfigError provenance='multi'     provision (fills holes only)
    MirrorConfigError provenance='legacy'    bootstrap()  -- NEVER provision
    Returns the resolved targets after the action.

    Rationale for the legacy row (M3): a legacy instance that lost one
    agent_* key must be repaired IN legacy mode -- provisioning there would
    create new calendars, flip precedence to multi, orphan the live populated
    calendar, and duplicate every row. bootstrap() stays the legacy
    writer-of-record. An operator who WANTS category calendars uses the
    opt-in upgrade lane (DGN-364 section 8), never a repair path."""
    try:
        return get_mirror_targets(state_conn)
    except MirrorUnconfigured:
        provision_category_calendars(state_conn)
    except MirrorConfigError as e:
        if e.provenance == "legacy":
            bootstrap(state_conn)
        else:
            provision_category_calendars(state_conn)
    return get_mirror_targets(state_conn)


# ---------------------------------------------------------------------------
# Sentinel helpers (m17: non-anchored strip)
# ---------------------------------------------------------------------------

def _sentinel_str(ulid):
    return "\n[ulid:%s]" % ulid

def _notes_with_sentinel(note, ulid):
    base = note or ""
    sentinel = _sentinel_str(ulid)
    if sentinel in base:
        return base
    return base + sentinel

def _strip_sentinel(notes):
    """Remove sentinel tag(s) wherever they appear (m17: user may have typed
    text below the sentinel -- do not require end-of-string)."""
    if not notes:
        return ""
    return _SENTINEL_RE.sub("", notes)


# ---------------------------------------------------------------------------
# Surface id bookkeeping (M10 generation ids)
# ---------------------------------------------------------------------------

def _cal_surface_id(state_conn, ulid, src_conn=None):
    """Active Calendar surface id: promoted bookkeeping (W6) or hex(ulid)."""
    return bk_get(state_conn, src_conn, ulid, "gcal_event_id") or ulid_to_hex(ulid)


def _next_generation_id(state_conn, ulid, src_conn=None):
    """M10: retire the dead surface id, mint hex+'g'+N (charset stays inside
    base32hex 0-9 a-v; extProps still carries the true ulid). Generation
    counter stays in state KV (internal); the ACTIVE id lives in the W6
    bookkeeping home."""
    gen = int(get_state(state_conn, "gcal_gen:%s" % ulid) or "0") + 1
    set_state(state_conn, "gcal_gen:%s" % ulid, str(gen))
    new_id = "%sg%d" % (ulid_to_hex(ulid), gen)
    bk_set(state_conn, src_conn, ulid, "gcal_event_id", new_id)
    return new_id


# ---------------------------------------------------------------------------
# Outbound: Calendar (W2/W3/W4 + M6 etag + M10)
# ---------------------------------------------------------------------------

def _build_cal_body(event, surface_id):
    gcal_status, color_id = event_to_cal_status(event)
    private_props = {"ulid": event["ulid"], "version": str(event.get("version", 0))}
    if event.get("location_url"):
        private_props["location_url"] = event["location_url"]
    if event.get("purpose"):
        private_props["purpose"] = event["purpose"]
    if event.get("summary"):
        private_props["summary_text"] = event["summary"]
    body = {
        "id": surface_id,
        # DGN-849: ONE title seam with projection (subtype label + status
        # decoration ride along identically on both sides -- F1).
        "summary": _projected_cal_title(event),
        # DGN-579 bundle: description = note + trailing persons with-line.
        "description": _compose_description(event),
        "status": gcal_status,
        # presence is slot_exclusive=0 by CHECK -> transparent (non-blocking).
        "transparency": "opaque" if event.get("slot_exclusive") else "transparent",
        "extendedProperties": {"private": private_props},
        # Notifications stay Telegram-only (W0): native reminders OFF.
        "reminders": {"useDefault": False, "overrides": []},
    }
    if event.get("location"):
        body["location"] = event["location"]
    if color_id:
        body["colorId"] = color_id
    # DGN-849: presence rides the generic schedule branch below (timed block
    # on the row's own schedule; the D-C3 forced banner span is retired).
    sk = event.get("schedule_kind")
    if sk in ("timed", "all_day") and event.get("start_at"):
        s_obj, e_obj = utc_instant_to_gcal_datetime(
            event["start_at"], event.get("end_at"), sk,
            event.get("display_tz") or DISPLAY_TZ_NAME)
        body["start"] = s_obj
        body["end"] = e_obj
    return body


def _cancel_body_keep_interval(body, current):
    """A cancel PUT is a full-resource update, and Google rejects one without
    start/end ("Missing end time") even though the object is being hidden.
    _build_cal_body emits start/end only for a row with a renderable interval
    (timed|all_day AND start_at); a row that LOST its interval after it was
    pushed would therefore wedge its own cancel. Carry the live object's
    interval instead -- the owner's intent is 'gone', not 'moved'. Confirmed
    bodies are left alone: an interval-less live row is a data problem the
    400 should keep surfacing."""
    if (body.get("status") == "cancelled" and "end" not in body
            and current and current.get("start") and current.get("end")):
        body = dict(body, start=current["start"], end=current["end"])
    return body


def push_calendar(event, cal_id, state_conn, src_conn=None):
    """Upsert one event to Calendar.
    M6 etag guard (compare-then-put emulation; gws cannot send If-Match):
      get current item -> if etag != stored etag AND a snapshot exists,
      run the 3-way merge FIRST (user's surface edits land in sqlite),
      reload the event, then push.
    M10: insert 409 -> revive update -> second 404 -> generation id."""
    ulid = event["ulid"]
    surface_id = _cal_surface_id(state_conn, ulid, src_conn)

    current = None
    try:
        current = gws("calendar", "events", "get", "--params",
                      json.dumps({"calendarId": cal_id, "eventId": surface_id}))
    except GwsError as e:
        if e.http_code != 404:
            raise

    # Cancelling something the surface does not hold is a no-op, never an
    # insert. Measured on Ag 2026-09-26 18:02: appt-cancel on a block that
    # was never pushed -> get 404 -> current None -> INSERT of a
    # status='cancelled' body -> Google 400 "Missing end time" (the row had
    # no renderable interval, so _build_cal_body emitted no start/end) ->
    # outbox_exhausted -> the owner was warned about a sync of a row they
    # had just cancelled.
    # Even with an interval the insert would have been wrong: it mints a
    # tombstone for an object that never existed. An already-cancelled
    # surface object is the same end state -- nothing to write.
    if (event_to_cal_status(event)[0] == "cancelled"
            and (current is None or current.get("status") == "cancelled")):
        mirror_log(state_conn, "cancel_unpushed_noop", ulid,
                   "surface %s on %s %s -- nothing to cancel, no insert"
                   % (surface_id, cal_id,
                      "absent (404)" if current is None
                      else "already cancelled"))
        return {"id": surface_id if current is not None else None,
                "noop": "cancel_unpushed"}

    if current is not None and current.get("status") != "cancelled":
        stored_etag = bk_get(state_conn, src_conn, ulid, "gcal_etag")
        if (stored_etag and current.get("etag") != stored_etag
                and src_conn is not None):
            # Surface changed since our last push -> merge before overwriting.
            _apply_calendar_3way(current, state_conn, src_conn)
            row = src_conn.execute(
                "SELECT * FROM timeblock WHERE ulid=?", (ulid,)).fetchone()
            if row is not None:
                event = enrich_event(src_conn, dict(row))

    body = _build_cal_body(event, surface_id)
    params_update = {"calendarId": cal_id, "eventId": surface_id}
    params_insert = {"calendarId": cal_id}

    if current is not None:
        body = _cancel_body_keep_interval(body, current)
        # X10b-1 (grill-4 finding 5b): main update lane = direct HTTPS with
        # a REAL If-Match on the freshest known etag. 412 -> pull-first
        # 3-way merge -> refetch -> single retry. Retires the get->put race.
        status, resp = _direct(http_direct.cal_update_ifmatch,
                               cal_id, surface_id, body, current.get("etag"))
        IFMATCH_STATS["puts"] += 1
        if status == 412:
            IFMATCH_STATS["http412"] += 1
            _st, fresh = _direct(http_direct.cal_get, cal_id, surface_id)
            if fresh is not None and src_conn is not None:
                _apply_calendar_3way(fresh, state_conn, src_conn)
                row = src_conn.execute(
                    "SELECT * FROM timeblock WHERE ulid=?", (ulid,)).fetchone()
                if row is not None:
                    event = enrich_event(src_conn, dict(row))
                body = _cancel_body_keep_interval(
                    _build_cal_body(event, surface_id), fresh)
            status, resp = _direct(http_direct.cal_update_ifmatch,
                                   cal_id, surface_id, body,
                                   (fresh or {}).get("etag"))
            IFMATCH_STATS["puts"] += 1
            if status == 412:
                raise GwsError("If-Match 412 twice for %s" % surface_id,
                               code=412, reason="preconditionFailed")
    else:
        try:
            resp = gws("calendar", "events", "insert",
                       "--params", json.dumps(params_insert), body=body)
        except GwsError as e:
            if e.http_code != 409:
                raise
            # Tombstone exists -> revive via update (W3).
            try:
                resp = gws("calendar", "events", "update",
                           "--params", json.dumps(params_update),
                           body={**body, "status": "confirmed"})
            except GwsError as e2:
                if e2.http_code != 404:
                    raise
                # M10 dead-end: id purged server-side. Retire + regenerate.
                new_id = _next_generation_id(state_conn, ulid, src_conn)
                mirror_log(state_conn, "surface_id_regenerated", ulid,
                           "old=%s new=%s" % (surface_id, new_id))
                body = _build_cal_body(event, new_id)
                resp = gws("calendar", "events", "insert",
                           "--params", json.dumps(params_insert), body=body)

    if resp.get("etag"):
        bk_set(state_conn, src_conn, ulid, "gcal_etag", resp["etag"])
    return resp


# ---------------------------------------------------------------------------
# Outbound: GTasks (W2b/W8 + M6 etag)
# ---------------------------------------------------------------------------

def _build_task_body(event, ulid):
    proj = tasks_projection_from_event(event)
    body = {"title": proj["title"],
            "notes": _notes_with_sentinel(event.get("note"), ulid),
            "status": proj["status"]}
    if proj["due_date"]:
        body["due"] = "%sT00:00:00.000Z" % proj["due_date"]
    if proj["status"] == "completed" and event.get("settled_at"):
        body["completed"] = event["settled_at"].replace("Z", ".000Z")
    return body


def push_tasks(event, tl_id, state_conn, src_conn=None):
    ulid = event["ulid"]

    # grill-4 finding 4 (W2b): abandoned task -> surface delete (symmetric
    # with calendar cancelled). Never insert/resurrect an abandoned task.
    if (event.get("settled_outcome") == "abandoned"
            or event.get("status") == "abandoned"):
        gtask_id = bk_get(state_conn, src_conn, ulid, "gtask_id")
        if not gtask_id:
            gtask_id = _find_task_by_sentinel(ulid, tl_id)  # crash-window
        if gtask_id:
            try:
                gws_delete("tasks", "tasks", "delete", "--params",
                           json.dumps({"tasklist": tl_id, "task": gtask_id}))
            except GwsError as e:
                if e.http_code != 404:
                    raise
        bk_set(state_conn, src_conn, ulid, "gtask_id", "")
        bk_set(state_conn, src_conn, ulid, "gtask_etag", "")
        return {"deleted_abandoned": True}

    body = _build_task_body(event, ulid)
    gtask_id = bk_get(state_conn, src_conn, ulid, "gtask_id")
    resp = None

    if gtask_id:
        current = None
        try:
            current = gws("tasks", "tasks", "get", "--params",
                          json.dumps({"tasklist": tl_id, "task": gtask_id}))
        except GwsError as e:
            if e.http_code == 404:
                gtask_id = None
            else:
                raise
        if gtask_id:
            stored_etag = bk_get(state_conn, src_conn, ulid, "gtask_etag")
            if (stored_etag and current is not None
                    and current.get("etag") != stored_etag
                    and src_conn is not None):
                # M6: surface changed since last push -> merge first.
                _apply_tasks_3way(current, tl_id, state_conn, src_conn)
                row = src_conn.execute(
                    "SELECT * FROM timeblock WHERE ulid=?", (ulid,)).fetchone()
                if row is not None:
                    event = dict(row)
                    body = _build_task_body(event, ulid)
            resp = gws("tasks", "tasks", "update", "--params",
                       json.dumps({"tasklist": tl_id, "task": gtask_id}),
                       body={**body, "id": gtask_id})

    if not gtask_id:
        # W8 crash-window dedup: full-tasklist sentinel scan before insert.
        existing_id = _find_task_by_sentinel(ulid, tl_id)
        if existing_id:
            gtask_id = existing_id
            bk_set(state_conn, src_conn, ulid, "gtask_id", gtask_id)
            resp = gws("tasks", "tasks", "update", "--params",
                       json.dumps({"tasklist": tl_id, "task": gtask_id}),
                       body={**body, "id": gtask_id})
        else:
            resp = gws("tasks", "tasks", "insert", "--params",
                       json.dumps({"tasklist": tl_id}), body=body)
            gtask_id = resp.get("id")
            if gtask_id:
                bk_set(state_conn, src_conn, ulid, "gtask_id", gtask_id)

    if resp is not None and resp.get("etag"):
        bk_set(state_conn, src_conn, ulid, "gtask_etag", resp["etag"])
    return resp


def _find_task_by_sentinel(ulid, tl_id):
    """Full-tasklist paged scan for [ulid:XXXX] (W8 crash-window dedup)."""
    sentinel = "[ulid:%s]" % ulid
    page_token = None
    while True:
        params = {"tasklist": tl_id, "showHidden": True, "showCompleted": True,
                  "showDeleted": False, "maxResults": 100}
        if page_token:
            params["pageToken"] = page_token
        resp = gws("tasks", "tasks", "list", "--params", json.dumps(params))
        for task in resp.get("items", []):
            if sentinel in (task.get("notes") or ""):
                return task["id"]
        page_token = resp.get("nextPageToken")
        if not page_token:
            return None


# ---------------------------------------------------------------------------
# sync(ulid): single outbox op (W6)
# ---------------------------------------------------------------------------

# 2026-08-25 scope-exit lane. sync_event's terminal results: a row that is out
# of mirror scope is NEVER retried (the scope predicate is pure over the row --
# a re-drain computes the identical answer), so the drain records it terminal
# instead of counting it a push. Same retryable-vs-terminal split the inbound
# lanes use (PRESENCE_INBOUND_TERMINAL / BYPASS_SCHEDULE_TERMINAL): a terminal
# refusal advances bookkeeping so there is no 5-minute re-fire.
SYNC_TERMINAL_SKIP = ("skipped_out_of_scope", "skipped_out_of_scope_tasks")


def _retire_calendar_surface(cal_id, surface_id):
    """Flip one calendar object to 'cancelled'. Same call shape the tasks-flip
    residue branch in sync_event already uses -- read the item, PUT it back
    with status='cancelled'. Never the raw Google API from outside the mirror
    (that misfires the bulk-delete safety guard).
    Returns True when the object is not visible any more; 404 (never there /
    already purged) and an already-cancelled item both count."""
    try:
        existing = gws("calendar", "events", "get", "--params",
                       json.dumps({"calendarId": cal_id,
                                   "eventId": surface_id}))
    except GwsError as e:
        if e.http_code in (404, 410):
            return True
        raise
    if existing.get("status") == "cancelled":
        return True
    gws("calendar", "events", "update", "--params",
        json.dumps({"calendarId": cal_id, "eventId": surface_id}),
        body={**existing, "status": "cancelled"})
    return True


def _scope_exit(event, cal_ids, state_conn, src_conn):
    """Handle a row that is NOT in mirror scope, at the push seam.

    This used to be an unconditional log-and-skip. The defect that forced the
    branch (measured 2026-08-25, ulid 01KZW5YTSTGG.../timeblock 12798, the
    owner's 8/14 재택 block): a row can LEAVE mirror scope AFTER it was
    already mirrored. The SPEC 2026-08-14 집쉼 gate did exactly that to every
    home-rest presence row already sitting on the owner's calendar. Scope is
    a rule about what may be PUT ON the surface; it says nothing about an
    object Google already holds. Skipping unconditionally strands that object
    as a live 'confirmed' event forever -- and because the DGN-302 abandoned
    scan keys on "snapshot not yet cancelled", it also re-enqueued the row
    every poll cycle with nothing on this side able to advance the
    bookkeeping: 2,515 enqueues over 13 days, never converging, while the
    owner's cancelled block stayed visible in his calendar.

    So the rule is snapshot-keyed, not scope-keyed:
      - no snapshot / already cancelled -> nothing on the surface: pure skip,
        and it is a FIXED POINT (the producer's snapshot gate will not fire).
      - live snapshot -> RETIRE the surface object and record the retirement.
        This is the same "ghost retired" doctrine the travel-expire lane uses.
    Either way the bookkeeping ends converged, which is what makes the
    producer stop re-firing."""
    ulid = event["ulid"]
    reason = ("hidden_presence place=%r title=%r"
              % (event.get("place_render") or event.get("location"),
                 event.get("title"))
              if is_hidden_presence(event) else
              "recurrence_id=%r is_routine=%r created_by=%r"
              % (event.get("recurrence_id"), event.get("is_routine"),
                 event.get("created_by")))

    snap_hash, snap = load_push_snapshot(state_conn, ulid)
    if snap_hash is None or snap.get("gcal_status") == "cancelled":
        # Nothing of ours on the surface -> terminal skip, already converged.
        mirror_log(state_conn, "scope_skip", ulid, reason)
        return {"ulid": ulid, "target": None,
                "status": "skipped_out_of_scope"}

    srow = state_conn.execute(
        "SELECT surface FROM push_snapshot WHERE event_ulid=?",
        (ulid,)).fetchone()
    if (srow["surface"] if srow else None) != "calendar":
        # Out-of-scope row holding a TASKS snapshot. Not observed in the
        # measured fleet (every live non-cancelled snapshot is calendar), and
        # a tasks retirement is a different verb (delete, not status flip).
        # Logged loudly rather than silently skipped so it cannot become the
        # next 13-day silence.
        mirror_log(state_conn, "scope_exit_unhandled", ulid,
                   "%s; live tasks snapshot -- not retired" % reason)
        return {"ulid": ulid, "target": None,
                "status": "skipped_out_of_scope_tasks"}

    cal_id = (snapshot_calendar_id(state_conn, ulid)
              or cal_ids[route_cal_key(event, src_conn)])
    surface_id = _cal_surface_id(state_conn, ulid, src_conn)
    _retire_calendar_surface(cal_id, surface_id)

    # Bookkeeping advance: the projection is stored with gcal_status forced to
    # 'cancelled' -- forced, because an out-of-scope row is not necessarily
    # settled (a LIVE 집쉼 stay leaving scope must still come off the surface,
    # and its own projection would say 'confirmed').
    proj = calendar_projection_from_event(event)
    proj["gcal_status"] = "cancelled"
    store_push_snapshot(state_conn, ulid, "calendar", proj,
                        calendar_id=cal_id)
    # Same reasoning as the tasks-flip residue branch: once the surface object
    # is gone the stored etag only lies about who owns the row.
    bk_set(state_conn, src_conn, ulid, "gcal_etag", "")
    mirror_log(state_conn, "scope_exit_retired", ulid,
               "%s; surface %s on %s cancelled, snapshot advanced"
               % (reason, surface_id, cal_id))
    return {"ulid": ulid, "target": "calendar",
            "status": "retired_out_of_scope"}


def sync_event(event, cal_ids, tl_id, state_conn, src_conn=None):
    """1. route  2. upsert to target  3. clean residual on the other surface.
    M9: routine/recurrence rows are refused (not mirror scope).
    DGN-294/DGN-364 (V15): cal_ids = {'appt','task','travel'} category ids;
    the target calendar comes from route_cal_key(event). The primary
    calendar is never touched.
    DGN-579 D-C3: presence rows sync too (travel calendar; DGN-849 renders
    them as subtype-labeled timed blocks); the former skipped_presence
    early-return is retired.
    DGN-579 bundle: the event dict is enriched here (place-FK location +
    persons) so body build AND snapshot store see identical values."""
    cal_ids = _cal_ids_dict(cal_ids)
    event = enrich_event(src_conn, event)
    ulid = event["ulid"]
    if not in_mirror_scope(event):
        # NOTE: the old log line here printed ONLY recurrence_id/is_routine
        # under the category 'routine_skip', which made 13 days of this loop
        # unreadable: in_mirror_scope returns True for any non-import row that
        # HAS a recurrence_id, so those two fields were never the refusal
        # reason. _scope_exit names the predicate that actually fired.
        return _scope_exit(event, cal_ids, state_conn, src_conn)

    target = route_surface(event, src_conn)
    result = {"ulid": ulid, "target": target, "status": "ok", "error": None}

    if target == "calendar":
        cal_key = route_cal_key(event, src_conn)
        routed_id = cal_ids[cal_key]
        cal_id = routed_id
        result["calendar_key"] = cal_key
        # DGN-364 M5 (bounded snapshot-unpin, REVISES Ag's unconditional
        # push-follows-snapshot): updates/tombstones must hit the calendar
        # the surface object actually LIVES on (snapshot bookkeeping) -- but
        # ONLY while that calendar is still among the currently-engraved
        # ids. A snapshot pinned to a dead (unengraved) calendar would
        # otherwise self-renew a 404 wedge and kill the outbox row.
        engraved_ids = {v for v in cal_ids.values() if _is_engraved(v)}
        snap_cal = snapshot_calendar_id(state_conn, ulid)
        pinned = False
        if snap_cal and snap_cal != routed_id:
            if snap_cal in engraved_ids:
                # Legitimate multi-mode drift (user moved the event across
                # category calendars): push follows snapshot, ONE drift line.
                mirror_log(state_conn, "calendar_drift_outbound", ulid,
                           "snapshot=%s routed=%s (push follows snapshot)"
                           % (snap_cal, routed_id))
                cal_id = snap_cal
                pinned = True
            else:
                # Snapshot-unpin: dead/unengraved pin -> push follows the
                # ROUTED id; the snapshot's calendar_id is refreshed to the
                # routed id at store time (end of this function). No retry
                # is consumed chasing a dead calendar.
                mirror_log(state_conn, "calendar_snapshot_unpin", ulid,
                           "old=%s new=%s" % (snap_cal, routed_id))
        try:
            resp = push_calendar(event, cal_id, state_conn, src_conn)
        except GwsError as e:
            # M5 addendum: calendar-level 404/410 during a push aimed at a
            # snapshot-pinned calendar clears the stored calendar_id (NULL)
            # BEFORE retry accounting, so the next drain routes fresh.
            # (Event-level 404 is handled inside push_calendar and never
            # propagates here.)
            # NOTE (DGN-364 final grill m-1): This clear is a deliberate
            # over-approximation of spec 2.4 -- it fires on any 404/410
            # escaping push_calendar (event-level included), which is benign
            # and convergent (event already gone; next requeue routes fresh).
            if pinned and e.http_code in (404, 410):
                state_conn.execute(
                    "UPDATE push_snapshot SET calendar_id=NULL "
                    "WHERE event_ulid=?", (ulid,))
                state_conn.commit()
                mirror_log(state_conn, "calendar_pin_cleared", ulid,
                           "calendar-level %d on pinned push; cleared %s"
                           % (e.http_code, cal_id))
            raise
        gtask_id = bk_get(state_conn, src_conn, ulid, "gtask_id")
        if gtask_id:
            # m13: 404 = already gone -> clear bookkeeping either way.
            try:
                gws_delete("tasks", "tasks", "delete", "--params",
                           json.dumps({"tasklist": tl_id, "task": gtask_id}))
            except GwsError as e:
                if e.http_code != 404:
                    result["cleanup_warning"] = str(e)
            bk_set(state_conn, src_conn, ulid, "gtask_id", "")
        result["gcal_id"] = resp.get("id")
        result["etag"] = resp.get("etag")
    else:
        resp = push_tasks(event, tl_id, state_conn, src_conn)
        surface_id = _cal_surface_id(state_conn, ulid, src_conn)
        # V15: flip residue lives on the calendar the row was last pushed
        # to (snapshot bookkeeping); fall back to the routed category.
        resid_cal = (snapshot_calendar_id(state_conn, ulid)
                     or cal_ids[route_cal_key(event, src_conn)])
        cal_residue_cleared = False
        try:
            existing = gws("calendar", "events", "get", "--params",
                           json.dumps({"calendarId": resid_cal, "eventId": surface_id}))
            if existing.get("status") != "cancelled":
                gws("calendar", "events", "update", "--params",
                    json.dumps({"calendarId": resid_cal, "eventId": surface_id}),
                    body={**existing, "status": "cancelled"})
            cal_residue_cleared = True
        except GwsError:
            pass  # not present -> nothing to clean
        if cal_residue_cleared:
            # Symmetry with the calendar branch above, which clears gtask_id
            # once the tasks-side residue is gone. Without this a flipped row
            # kept a live-looking gcal_etag with no calendar surface behind
            # it (owner's 12999 '공연 큐시트 만들기' -- etag present,
            # gcal_event_id NULL because the surface id is DERIVED from the
            # ulid and only ever stored on regeneration/adoption). The stale
            # value is inert today (push_calendar skips the etag guard for a
            # cancelled item) but it makes the bookkeeping lie about which
            # surface owns the row, which is how the next reader gets fooled.
            bk_set(state_conn, src_conn, ulid, "gcal_etag", "")
        if resp.get("deleted_abandoned"):
            # finding 4: surface task removed -- drop the snapshot so a
            # (spec-illegal) resurrection would be re-derived, not echoed.
            state_conn.execute(
                "DELETE FROM push_snapshot WHERE event_ulid=?", (ulid,))
            state_conn.commit()
            result["status"] = "deleted_abandoned"
            return result
        result["gtask_id"] = resp.get("id")
        result["etag"] = resp.get("etag")

    # grill-4 finding 1: snapshot must reflect what was ACTUALLY pushed.
    # push_calendar/push_tasks may have 3-way-merged surface edits into
    # sqlite first (etag guard) -- re-load the row so the stored projection
    # matches the pushed (merged) state, not the stale caller dict.
    if src_conn is not None:
        fresh = src_conn.execute(
            "SELECT * FROM timeblock WHERE ulid=?", (ulid,)).fetchone()
        if fresh is not None:
            event = enrich_event(src_conn, dict(fresh))
    proj = (calendar_projection_from_event(event) if target == "calendar"
            else tasks_projection_from_event(event))
    store_push_snapshot(state_conn, ulid, target, proj,
                        calendar_id=(cal_id if target == "calendar"
                                     else None))
    return result


# ---------------------------------------------------------------------------
# Verb result discipline (grill-4 finding 2) + surface timestamp (finding 6)
# ---------------------------------------------------------------------------

VERB_OK = ("applied", "already_done", "noop")


def _run_verb(fn):
    """Run an sdk_bridge verb. cas_fail -> ONE inline retry (the verb re-reads
    id/version itself, so the retry sees the fresh version). Returns the
    final result string."""
    res = fn()
    if res == "cas_fail":
        res = fn()
    return res


def _run_verb_tuple(fn):
    """Same, for verbs returning (result, overlap_warning)."""
    res, warn = fn()
    if res == "cas_fail":
        res, warn = fn()
    return res, warn


def _canonical_from_rfc3339(ts):
    """RFC3339 (e.g. '2026-07-09T04:04:49.000Z') -> canonical
    'YYYY-MM-DDThh:mm:ssZ' via parse->reformat (NEVER string truncation).
    Returns None for absent/garbage input (caller falls back + logs)."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, AttributeError):
        return None


# DGN-849 A2: config knob; default = prior literal (open-ended outbound
# placeholder end = start + 1h, m14).
PLACEHOLDER_SECONDS = _conf_int("MIRROR_PLACEHOLDER_SECONDS", 3600)


def _duration_seconds(start_ts, end_ts):
    """Seconds between two canonical UTC instants."""
    s = datetime.strptime(start_ts, "%Y-%m-%dT%H:%M:%SZ")
    e = datetime.strptime(end_ts, "%Y-%m-%dT%H:%M:%SZ")
    return int((e - s).total_seconds())


# ---------------------------------------------------------------------------
# M5/F1: inbound calendar 3-way apply
# ---------------------------------------------------------------------------

CAL_CONTENT_FIELDS = ("title", "note", "location")
CAL_SCHEDULE_FIELDS = ("start_at", "end_at", "schedule_kind")
CAL_SYSTEM_FIELDS = ("gcal_status", "color_id", "transparency")  # IN-ignored (W2)


def _apply_calendar_3way(item, state_conn, src_conn):
    """Per-field 3-way: snapshot (last reconcile) vs surface vs local sqlite.
    Surface wins ONLY on fields the surface actually changed; conflicting
    same-field local change -> surface wins + audit line (coordinator rule).
    System fields are IN-ignored per W2. Returns action string."""
    ulid = _extract_owned_ulid(item, src_conn)
    if ulid is None:
        return "foreign_skip"

    surface = calendar_projection_from_item(item)
    snap_hash, snap = load_push_snapshot(state_conn, ulid)
    if snap_hash is not None and projection_hash(surface) == snap_hash:
        return "echo_skip"

    row = src_conn.execute("SELECT * FROM timeblock WHERE ulid=?", (ulid,)).fetchone()
    if row is None:
        mirror_log(state_conn, "inbound_unknown_ulid", ulid, "calendar item")
        return "unknown_ulid"
    # 2026-08-25 inbound presence lane. This used to be a blanket early
    # return: ANY delivery of a presence item -- even one whose per-field
    # diff is empty -- was logged 'presence_inbound_rejected' and answered
    # with a canonical re-push. Measured cost: the owner drags his work
    # block in Google Calendar, the edit is dropped and the old value is
    # pushed back over him, with no message (mirror_log 8211/8213, ulid
    # 01KZW5YTSVXTFYTZ17XCYVP4J8).
    #
    # The gate itself was right about ONE thing: presence carries a
    # one-presence-at-a-time invariant, and bypass_schedule_apply -- the
    # verb this lane uses for every other kind -- skips the overlap
    # predicate, so it must never touch presence. The fix is not to weaken
    # that. Schedule edits route to presence_inbound_apply ->
    # presence_move, the presence-SCOPED CAS verb, which enforces the
    # invariant and stamps rec_exception=1 in the same txn (the same
    # treatment the chat lane got on 2026-08-24).
    #
    # Non-schedule presence fields stay one-way: the surface title carries
    # the mirror's subtype label and the location is SoT-derived, so a
    # surface edit there is a rendering artifact, not owner intent.
    # Deletion is still handled by the pull's cancelled lane.
    is_presence = (row["kind"] == "presence")
    local = calendar_projection_from_event(
        enrich_event(src_conn, dict(row)))

    content_changes = {}
    schedule_changed = False
    persons_changed = False
    presence_reechoed = False        # one-way presence field dropped
    presence_refusal = None          # terminal invariant refusal
    schedule_refusal = None          # D3-2/D4 non-presence terminal refusal
    for f in surface:
        if f in CAL_SYSTEM_FIELDS:
            continue
        surf_v = surface.get(f)
        snap_v = snap.get(f) if snap else None
        local_v = local.get(f)
        if surf_v == snap_v:
            continue  # surface did not change this field
        if local_v == surf_v:
            continue  # already in sync locally -- nothing to apply
        if f == "persons":
            # DGN-579 bundle: persons are SoT-owned (event_persons junction);
            # a surface edit of the with-line never enters the SoT.
            persons_changed = True
            continue
        if is_presence and f in CAL_CONTENT_FIELDS:
            # One-way on presence (see the lane note above). Logged, not
            # silent, and the canonical re-push below restores the block.
            presence_reechoed = True
            mirror_log(state_conn, "presence_field_reechoed", ulid,
                       "field=%s surface=%r dropped; canonical re-push "
                       "enqueued" % (f, surf_v))
            continue
        if local_v != snap_v and local_v != surf_v:
            mirror_log(state_conn, "conflict_surface_wins", ulid,
                       "field=%s local=%r surface=%r" % (f, local_v, surf_v))
        if f in CAL_CONTENT_FIELDS:
            if (f == "location" and
                    sdk_bridge.kind_policy.for_event(row)["location_mode"] == "place"):
                # Framework ruling (grill-6 g14): location is not a task column
                # in the 179 meta policy (timeblock_set_meta would ValueError).
                # Explicit drop + audit line, never a crash path.
                mirror_log(state_conn, "field_dropped", ulid,
                           "location edit on task row -- not a task column")
                continue
            if f == "title":
                # DGN-849: mirror-authored title prefixes (subtype label,
                # status decoration) are surface renderings, never SoT data.
                # Comparison above ran on the decorated values (correct for
                # change detection); the WRITE value is stripped so a user
                # edit of a decorated title cannot leak the prefix into the
                # SoT title column. A user deleting only the prefix strips
                # to the unchanged base title -> canonical re-push restores
                # the label (same one-way contract as the persons line).
                surf_v = _strip_title_decorations(surf_v)
            content_changes[f] = surf_v
        elif f in CAL_SCHEDULE_FIELDS:
            schedule_changed = True

    applied, failures = [], []
    if content_changes:
        res = _run_verb(lambda: sdk_bridge.content_update(
            src_conn, ulid, content_changes))
        (applied if res in VERB_OK else failures).append("content:%s" % res)
        if "location" in content_changes and res in VERB_OK:
            # The owner typing a venue into the Google Calendar entry is the
            # same intake event as `appt-upd <id> location=...`, which DOES
            # auto-attach. Without this the two lanes disagree: the venue that
            # arrives by hand derives move/prep, the identical venue that
            # arrives from the surface derives nothing and leaves place_id
            # NULL. Headless lane -> the elicit codes are LOGGED, not asked.
            try:
                st, _o, _d = sdk_bridge.ec.travel_attach_event(
                    src_conn, ulid, content_changes["location"])
                if st in _INBOUND_TRAVEL_NEEDS:
                    mirror_log(state_conn, "inbound_place_needed", ulid,
                               "%s location=%r"
                               % (st, content_changes["location"]))
            except Exception as e:              # noqa: BLE001 -- best-effort
                mirror_log(state_conn, "inbound_travel_attach_failed", ulid,
                           str(e)[:120])
    if schedule_changed:
        new_start, new_end = surface["start_at"], surface["end_at"]
        # v4 ruling 1 (open-ended preservation, grill-4 finding 10 / OQ-1):
        # the outbound placeholder end (start+1h) is a mirror artifact and
        # must NEVER materialize into the SoT. If the local row is open-ended
        # (timed, end_at NULL) and the surface interval is EXACTLY the
        # placeholder duration, the user only dragged the start -> apply the
        # start shift, keep end NULL (open_ended flag preserved by the verb).
        # A differing duration = the user expressed a real end -> materialize
        # it (verb clears open_ended).
        if (row["schedule_kind"] == "timed" and row["end_at"] is None
                and surface["schedule_kind"] == "timed"
                and new_start and new_end
                and _duration_seconds(new_start, new_end) == PLACEHOLDER_SECONDS):
            new_end = None
        if is_presence:
            # Presence-scoped CAS (keeps the one-at-a-time invariant, stamps
            # rec_exception=1 on a recurrence instance) instead of bypass.
            res, warning = _run_verb_tuple(
                lambda: sdk_bridge.presence_inbound_apply(
                    src_conn, ulid, surface["schedule_kind"],
                    new_start, new_end))
            if res in sdk_bridge.PRESENCE_INBOUND_TERMINAL:
                presence_refusal = res
        else:
            res, warning = _run_verb_tuple(
                lambda: sdk_bridge.bypass_schedule_apply(
                    src_conn, ulid, surface["schedule_kind"],
                    new_start, new_end))
            if res in sdk_bridge.BYPASS_SCHEDULE_TERMINAL:
                schedule_refusal = res
        (applied if res in VERB_OK else failures).append("schedule:%s" % res)
        if warning:
            mirror_log(state_conn, "bypass_overlap_notice", ulid, warning)
            _overlap_defer(ulid, warning)  # DGN-333: notify at batch end
        if res in VERB_OK:
            # grill-4 finding 7: schedule edit may flip routing (e.g. timed
            # task dragged to all-day) -> re-converge surfaces via outbox.
            _refresh_travel_after_schedule(src_conn, state_conn, ulid)
            fresh = src_conn.execute(
                "SELECT * FROM timeblock WHERE ulid=?", (ulid,)).fetchone()
            if fresh is not None and route_surface(dict(fresh)) != "calendar":
                outbox_enqueue(state_conn, ulid)
                mirror_log(state_conn, "routing_flip_enqueued", ulid,
                           "calendar -> %s" % route_surface(dict(fresh)))

    if persons_changed:
        # One-way field: drop the surface edit, re-push the canonical
        # with-line (the drain's push refreshes surface + snapshot).
        mirror_log(state_conn, "persons_edit_reechoed", ulid,
                   "surface with-line edit dropped; canonical re-push "
                   "enqueued")
        outbox_enqueue(state_conn, ulid)

    if presence_refusal:
        # TERMINAL refusal: the one-presence-at-a-time invariant (or a shape
        # the presence verb cannot express) said no. Three things happen, and
        # the third is the one that was missing before: the audit line, the
        # canonical re-push so the calendar stops showing a change that did
        # not take, and a MESSAGE TO THE OWNER. Silently re-pushing over him
        # is exactly the defect this ticket exists for.
        # No snapshot write: the re-push stores its own reconcile point.
        mirror_log(state_conn, "presence_inbound_rejected", ulid,
                   "presence schedule edit refused (%s); canonical re-push "
                   "enqueued + owner notified" % presence_refusal)
        outbox_enqueue(state_conn, ulid)
        notify_mod.notify(
            state_conn, "presence_edit_refused", ulid,
            title=_title_of(src_conn, ulid),
            reason=notify_mod.presence_refusal_reason(presence_refusal))
        return "presence_refused(%s)" % presence_refusal
    if schedule_refusal:
        # D3-2/D4 TERMINAL refusal on a non-presence row: a dead (settled) row,
        # or a DERIVED movement/buffer block that only travel-move may move.
        # Same three-part contract the presence lane got: audit line, canonical
        # re-push so the calendar stops showing a change that did not take, and
        # a MESSAGE naming the supported path. What the travel branch did
        # before was the exact opposite -- it silently FROZE the block instead.
        # No snapshot write: the re-push stores its own reconcile point.
        mirror_log(state_conn, "schedule_inbound_rejected", ulid,
                   "schedule edit refused (%s); canonical re-push enqueued "
                   "+ owner notified" % schedule_refusal)
        outbox_enqueue(state_conn, ulid)
        notify_mod.notify(
            state_conn, "schedule_edit_refused", ulid,
            title=_title_of(src_conn, ulid),
            reason=notify_mod.schedule_refusal_reason(schedule_refusal))
        return "schedule_refused(%s)" % schedule_refusal
    if presence_reechoed:
        # One-way field dropped -> restore the canonical rendering. This is
        # NOT the accept path: an accepted schedule edit alone enqueues
        # nothing, so the re-push can never overwrite the owner's new times.
        outbox_enqueue(state_conn, ulid)
    if failures:
        # grill-4 finding 2: verb failure is NOT success. No snapshot write
        # (next delivery retries the merge) + audit line.
        mirror_log(state_conn, "verb_failed", ulid,
                   "calendar apply failed=%s applied=%s" % (failures, applied))
        return "apply_failed(%s)" % ",".join(failures)
    if not applied:
        if persons_changed:
            return "persons_reechoed"
        if presence_reechoed:
            return "presence_reechoed"
        return "no_editable_change"
    # New reconcile point = what the surface holds now.
    store_push_snapshot(state_conn, ulid, "calendar", surface)
    if item.get("etag"):
        bk_set(state_conn, src_conn, ulid, "gcal_etag", item["etag"])
    return "applied(%s)" % ",".join(applied)


# ---------------------------------------------------------------------------
# F2/F3: inbound calendar pull (paged, guarded, circuit-broken)
# ---------------------------------------------------------------------------

# DGN-849 A2: config knob; default = prior literal (g5-4: aggregate
# per-kind per-drain above this).
NOTIFY_AGG_THRESHOLD = _conf_int("MIRROR_NOTIFY_AGG_THRESHOLD", 3)


# Auto-attach outcomes that mean "a travel datum is missing". Inbound is a
# headless cron poll with no LLM turn, so these are LOGGED, never asked (the
# keyless travel-resolve path needs a conversational turn -- see
# kit_life.travel_source_select).
#
# READ FROM THE KIT, not re-typed. This was a hand-copied duplicate of
# kit_life's elicit set, and a duplicate of a list is a list that goes stale
# in silence: adding 'link_place' on the kit side (2.20.0) would have left the
# mirror treating a map-link appointment as an ordinary non-event, so the one
# inbound surface that logs "a travel datum is missing" would have stayed
# quiet about the exact case that started this. The literal remains only as
# the pre-2.20 fallback for an older kit on the other side of the seam.
_INBOUND_TRAVEL_NEEDS = tuple(getattr(
    sdk_bridge.ec, "TRAVEL_ATTACH_NEEDS_ELICIT",
    ("unknown_place", "no_duration", "edge_missing")))


def _refresh_travel_after_schedule(src_conn, state_conn, ulid):
    """Re-derive move/prep after an INBOUND schedule change.

    bypass_schedule_apply swaps the instants and stops there. The CLI lane
    (cli_appt_upd) and the routine lane (routine_exception) both follow a
    committed schedule change with travel_refresh -- the mirror did not, so an
    appointment dragged in Google Calendar kept its OLD travel/prep blocks
    (or, once the anchor moved past them, none at all).

    No pre-clean here on purpose: pre-clean exists to dodge the FG-1 slot
    predicate on the CLI's guarded update. This lane already went through
    BYPASS (no predicate -> no self-deadlock), which is exactly the
    routine_exception situation, and its docstring records that pre-clean is
    unnecessary there. travel_refresh is idempotent and no-ops when the row is
    not a travel anchor.

    Best-effort: a derive failure must never fail an inbound apply that has
    already been committed."""
    def _log(category, detail):
        # The AUDIT LINE must not be able to break the caller either. state_conn
        # is always live in production, but "best-effort" has to mean the whole
        # function -- a logging failure taking down an inbound apply that was
        # ALREADY COMMITTED is the worst possible trade.
        try:
            mirror_log(state_conn, category, ulid, detail)
        except Exception:                       # noqa: BLE001
            pass

    # An anchor with NO rule has nothing to refresh -- travel_refresh returns
    # a clean zero summary and the appointment lands on its new instant with
    # no move/prep, forever. Rescheduling a place-bearing appointment is an
    # intake event for travel, so attach FIRST (idempotent: an armed anchor is
    # 'exists' and writes nothing), then converge. Same choke point the CLI
    # lane uses, same outcome codes.
    try:
        status, _origin, _dest = sdk_bridge.ec.travel_attach_event(
            src_conn, ulid)
        if status in _INBOUND_TRAVEL_NEEDS:
            _log("inbound_place_needed", "%s (schedule change)" % status)
    except Exception as e:                      # noqa: BLE001 -- best-effort
        _log("inbound_travel_attach_failed", str(e)[:120])
    try:
        summary = sdk_bridge.ec.travel_refresh(src_conn, None, ulid)
    except Exception as e:                      # noqa: BLE001 -- best-effort
        _log("inbound_travel_refresh_failed", str(e)[:120])
        return None
    if summary and summary.get("result") == "ok" and (
            summary.get("created") or summary.get("tombstoned")
            or summary.get("moved") or summary.get("regen")):
        _log("inbound_travel_refreshed",
             "created=%s tombstoned=%s moved=%s regen=%s"
             % (summary.get("created"), summary.get("tombstoned"),
                summary.get("moved"), summary.get("regen")))
    return summary


def _adopt_foreign_calendar(item, cal_id, state_conn, src_conn, cal_key=None):
    """dec-009: an item WITHOUT our marker but IN the dedicated calendar is
    an owner hand-made entry -> adopt as timeblock_add(kind='appointment') via
    the SDK verb, stamp extProps ulid on the surface object, notify.
    True-foreign guard: no start (schema-unadoptable) -> skip + log."""
    start_obj = item.get("start") or {}
    if not ("date" in start_obj or "dateTime" in start_obj):
        mirror_log(state_conn, "foreign_skip", None,
                   "calendar id=%s (no start -- unadoptable)" % item.get("id"))
        return {"id": item.get("id"), "action": "foreign_skip"}
    if src_conn is None:
        mirror_log(state_conn, "foreign_skip", None,
                   "calendar id=%s (no SoT conn)" % item.get("id"))
        return {"id": item.get("id"), "action": "foreign_skip"}

    title = (item.get("summary") or "").strip() or "(untitled)"
    # g5-... grill-6 g9: recurring events (master rrule or expanded instance)
    # are outside mirror scope until recurrence support (DGN-240) -- skip +
    # one owner notification, never adopt (instance-per-day adoption flood).
    if item.get("recurrence") or item.get("recurringEventId"):
        mirror_log(state_conn, "recurring_skipped", None,
                   "calendar id=%s title=%r" % (item.get("id"), title[:40]))
        notify_mod.notify(state_conn, "recurring_skipped",
                          item.get("recurringEventId") or item.get("id"),
                          title=title)
        return {"id": item.get("id"), "action": "recurring_skipped"}
    sk = "all_day" if "date" in start_obj else "timed"
    s_at, e_at = gcal_datetime_to_utc_instants(
        start_obj, item.get("end") or {})
    # DGN-623 / gcal-inbound: go through the kit_life seam, NOT timeblock_add.
    # Calling timeblock_add directly (what this did before) dropped the GCal
    # `location` on the floor AND bypassed _travel_autoattach_appt -- so an
    # owner-created calendar entry silently got no move/prep blocks, while the
    # SAME appointment typed at the agent got them. mirror_adopt_appt owns both
    # halves (location into meta + presence-aware auto-attach) in one txn
    # window, and keeps the FG-1 slot-loss bypass lane internally.
    kind = adopt_kind_for_cal(cal_key, sk, s_at, e_at, conn=src_conn)
    eid, ulid, overlap_warn, travel = sdk_bridge.ec.mirror_adopt_block(
        src_conn, kind=kind, title=title, schedule_kind=sk, start_at=s_at, end_at=e_at,
        owning_agent="ag", created_by="gcal-inbound-create",
        note=item.get("description") or None,
        location=(item.get("location") or None))
    if eid is None:
        kind = "appointment"
        eid, ulid, overlap_warn, travel = sdk_bridge.ec.mirror_adopt_appt(
            src_conn, title=title, schedule_kind=sk, start_at=s_at, end_at=e_at,
            owning_agent="ag", created_by="gcal-inbound-create",
            note=item.get("description") or None,
            location=item.get("location") or None)
    if overlap_warn:
        mirror_log(state_conn, "adopt_slot_conflict_forced", None,
                   "calendar id=%s title=%r overlap=%s"
                   % (item.get("id"), title[:40], overlap_warn))
    if travel is not None:
        # The elicit codes mean a datum is missing (unknown place / no edge).
        # There is NO conversational turn here (cron poll), so we do not ask --
        # we log the need. travel-resolve runs on the next turn that touches
        # this appointment (or when the owner mentions it).
        if travel["status"] in _INBOUND_TRAVEL_NEEDS:
            mirror_log(state_conn, "inbound_travel_pending", None,
                       "calendar id=%s status=%s origin=%r dest=%r"
                       % (item.get("id"), travel["status"],
                          travel["origin"], travel["dest"]))
        else:
            mirror_log(state_conn, "inbound_travel_%s" % travel["status"],
                       None, "calendar id=%s dest=%r"
                       % (item.get("id"), travel["dest"]))
    row = src_conn.execute("SELECT * FROM timeblock WHERE id=?", (eid,)).fetchone()
    # Stamp the surface object so it is ours from now on.
    patched = gws("calendar", "events", "patch", "--params",
                  json.dumps({"calendarId": cal_id, "eventId": item["id"]}),
                  body={"extendedProperties": {"private": {
                      "ulid": ulid, "version": "0"}}})
    bk_set(state_conn, src_conn, ulid, "gcal_event_id", item["id"])
    if patched.get("etag"):
        bk_set(state_conn, src_conn, ulid, "gcal_etag", patched["etag"])
    store_push_snapshot(state_conn, ulid, "calendar",
                        calendar_projection_from_item(patched),
                        calendar_id=cal_id)
    notify_mod.notify(state_conn, "inbound_adopted", ulid, title=title)
    if overlap_warn:
        _overlap_defer(ulid, overlap_warn)  # DGN-333: notify at batch end
    mirror_log(state_conn, "inbound_adopted", ulid,
               "surface id=%s" % item.get("id"))
    return {"ulid": ulid, "action": "adopted"}


def pull_calendar(cal_id, state_conn, src_conn, cal_key=None):
    """Public entry: acquires the single mirror lock shared with the drain
    (grill-4 finding 5a) -- pull and drain never interleave. If the lock is
    held, this poll is skipped (next poll catches up; cursors unmoved).
    cal_key (V15): category key of this calendar; enables the per-calendar
    sync cursor + drift attention."""
    if not _acquire_drain_lock(state_conn):
        mirror_log(state_conn, "pull_skipped_locked", None, "calendar poll")
        return []
    try:
        return _pull_calendar_locked(cal_id, state_conn, src_conn,
                                     cal_key=cal_key)
    finally:
        _release_drain_lock(state_conn)


def _pull_calendar_locked(cal_id, state_conn, src_conn, cal_key=None):
    """syncToken incremental pull. F2: pageToken loop, nextSyncToken saved
    only after the final page. F3: foreign guard + per-item try/except.
    W10: mass-cancelled circuit breaker (finding 3: on trip the token is NOT
    saved so held deltas redeliver; own cancel echoes excluded from count).
    Finding 13: invalid-token 400 handled like 410 (reset + one retry).
    Cursor-reset containment (DGN-364 TM-6): a 410 (or bounded 400-as-410)
    on a namespaced pull resets ONLY that pull's cursor key
    'cal_sync_token:<key>'; sibling cursors and the plain legacy cursor are
    never touched."""
    results = []
    items = []
    next_sync_token = None

    token_key = ("cal_sync_token:%s" % cal_key) if cal_key else "cal_sync_token"
    for attempt in range(2):
        sync_token = get_state(state_conn, token_key)
        items = []
        page_token = None
        try:
            while True:
                # g9: singleEvents/showDeleted MUST match across initial and
                # incremental pulls (recurring representation consistency).
                if sync_token:
                    params = {"calendarId": cal_id, "syncToken": sync_token,
                              "singleEvents": True, "maxResults": 250}
                else:
                    params = {"calendarId": cal_id, "showDeleted": True,
                              "singleEvents": True, "maxResults": 250}
                if page_token:
                    params["pageToken"] = page_token
                resp = gws("calendar", "events", "list",
                           "--params", json.dumps(params))
                items.extend(resp.get("items", []))
                page_token = resp.get("nextPageToken")
                if not page_token:
                    next_sync_token = resp.get("nextSyncToken")
                    break
            break
        except GwsError as e:
            # 410 GONE or invalid-token 400 -> reset + full resync (once).
            if e.http_code in (410, 400) and sync_token and attempt == 0:
                set_state(state_conn, token_key, "")
                continue
            raise

    # W10 circuit breaker: mass-cancelled = likely calendar loss, not intent.
    # Finding 3: our own pushed cancels (snapshot gcal_status=='cancelled')
    # are echoes, not owner actions -- excluded from the count.
    ours_cancelled = []
    for i in items:
        if i.get("status") != "cancelled":
            continue
        u = _extract_ulid(i)
        if not u:
            continue
        _h, snap = load_push_snapshot(state_conn, u)
        if snap.get("gcal_status") == "cancelled":
            continue  # own cancel echo
        ours_cancelled.append(u)
    breaker_tripped = len(ours_cancelled) > CB_CANCELLED_THRESHOLD
    if breaker_tripped:
        mirror_log(state_conn, "circuit_breaker_tripped", None,
                   "cancelled=%d threshold=%d -- cancelled processing halted, "
                   "syncToken withheld (deltas redeliver), owner notification "
                   "required" % (len(ours_cancelled), CB_CANCELLED_THRESHOLD))
        notify_mod.notify(state_conn, "circuit_breaker", None,
                          count=len(ours_cancelled))

    for item in items:
        try:
            ulid = _extract_owned_ulid(item, src_conn, state_conn)
            if ulid is None:
                if item.get("status") == "cancelled":
                    # foreign tombstone: nothing to adopt
                    results.append({"id": item.get("id"),
                                    "action": "foreign_skip"})
                    continue
                results.append(_adopt_foreign_calendar(
                    item, cal_id, state_conn, src_conn, cal_key=cal_key))
                continue
            if item.get("status") == "cancelled":
                # dec-011: an owner-deleted LIVE calendar-routed event is
                # cancelled in the SoT (abandoned) + one confirm notification.
                # Held under breaker trip; system tombstones (flip residue,
                # our own cancel echoes) stay no-op.
                action = ("cancelled_held_breaker" if breaker_tripped
                          else "cancelled_noted")
                if action == "cancelled_noted" and src_conn is not None:
                    row = src_conn.execute(
                        "SELECT * FROM timeblock WHERE ulid=?", (ulid,)).fetchone()
                    _h2, snapc = load_push_snapshot(state_conn, ulid)
                    if (row is not None and row["settled_outcome"] is None
                            and route_surface(dict(row)) == "calendar"
                            and snapc.get("gcal_status") != "cancelled"):
                        res = _run_verb(lambda: sdk_bridge.cancel_abandoned(
                            src_conn, ulid, "gcal-owner-delete"))
                        if res in VERB_OK:
                            action = "cancelled_abandoned"
                            store_push_snapshot(
                                state_conn, ulid, "calendar",
                                calendar_projection_from_item(item))
                            notify_mod.notify(state_conn, "inbound_cancel",
                                              ulid, title=row["title"])
                        else:
                            mirror_log(state_conn, "verb_failed", ulid,
                                       "inbound cancel apply=%s" % res)
                results.append({"ulid": ulid, "action": action})
                continue
            # V15 drift attention (4.1): kind/block_class are insert-
            # immutable, so a row surfacing in a calendar its route does
            # not map to = drift -> ONE attention line, NEVER a silent
            # move (and no apply from the drifted copy).
            if cal_key is not None and src_conn is not None:
                drow = src_conn.execute(
                    "SELECT * FROM timeblock WHERE ulid=?", (ulid,)).fetchone()
                if (drow is not None
                        and route_cal_key(dict(drow), src_conn) != cal_key):
                    mirror_log(state_conn, "calendar_drift", ulid,
                               "found_in=%s expected=%s"
                               % (cal_key, route_cal_key(dict(drow), src_conn)))
                    results.append({"ulid": ulid,
                                    "action": "drift_attention"})
                    continue
            action = _apply_calendar_3way(item, state_conn, src_conn)
            results.append({"ulid": ulid, "action": action})
        except Exception as e:  # F3: one bad item never kills the poll
            mirror_log(state_conn, "inbound_item_error", None,
                       "calendar id=%s err=%s" % (item.get("id", "?"), e))
            results.append({"id": item.get("id"), "action": "item_error",
                            "error": str(e)})

    # Finding 3: on breaker trip the token is NOT advanced -- the held
    # cancelled deltas redeliver on the next poll (W12 backstop absent).
    if next_sync_token and not breaker_tripped:
        set_state(state_conn, token_key, next_sync_token)
    return results


# ---------------------------------------------------------------------------
# M8/F4/F2: inbound tasks pull + 3-way
# ---------------------------------------------------------------------------

TASK_CONTENT_FIELDS = ("title", "note")


def _apply_tasks_3way(item, tl_id, state_conn, src_conn):
    """M8: consume title/notes/due edits + completed (F4 settle verb) +
    needsAction reopen (unsettle verb). Same 3-way discipline as calendar.
    g5-8: ours-identification = gtask_id bookkeeping column FIRST (real
    tombstones may not preserve notes), sentinel as fallback."""
    notes = item.get("notes", "") or ""
    ulid = None
    tid = item.get("id")
    if tid and src_conn is not None and _bk_cols_available(src_conn):
        r0 = src_conn.execute(
            "SELECT ulid FROM timeblock WHERE gtask_id=?", (tid,)).fetchone()
        if r0 is not None:
            ulid = r0["ulid"]
    if ulid is None:
        m = _SENTINEL_RE.search(notes)
        if not m:
            return "foreign_skip"
        ulid = m.group(1)

    if item.get("deleted"):
        # V3 updatedMin is INCLUSIVE (>=): tombstones redeliver -- id-dedup
        # via updated marker (MG180-8, harness E18).
        marker_key = "deleted_noted:%s" % ulid
        cur = item.get("updated") or "-"
        if get_state(state_conn, marker_key) == cur:
            return "echo_skip"  # tombstone already processed
        row_d = src_conn.execute(
            "SELECT * FROM timeblock WHERE ulid=?", (ulid,)).fetchone()
        if (row_d is not None and row_d["settled_outcome"] is None
                and route_surface(dict(row_d)) == "tasks"):
            # dec-011: owner deleted our live task -> cancel (abandoned) +
            # bookkeeping clear (W2b delete semantics: no resurrection) +
            # one confirm notification. Marker set only on success so a
            # verb failure retries via reconcile owner_deleted backstop.
            res = _run_verb(lambda: sdk_bridge.cancel_abandoned(
                src_conn, ulid, "gtasks-owner-delete"))
            if res in VERB_OK:
                bk_set(state_conn, src_conn, ulid, "gtask_id", "")
                bk_set(state_conn, src_conn, ulid, "gtask_etag", "")
                state_conn.execute(
                    "DELETE FROM push_snapshot WHERE event_ulid=?", (ulid,))
                state_conn.commit()
                set_state(state_conn, marker_key, cur)
                mirror_log(state_conn, "task_deleted_abandoned", ulid,
                           "dec-011 applied (gtasks-owner-delete)")
                notify_mod.notify(state_conn, "task_deleted", ulid,
                                  title=row_d["title"])
                return "deleted_abandoned_applied"
            mirror_log(state_conn, "verb_failed", ulid,
                       "dec-011 cancel apply=%s" % res)
            return "apply_failed(cancel:%s)" % res
        # settled / non-tasks-routed / unknown: note once, no action.
        set_state(state_conn, marker_key, cur)
        mirror_log(state_conn, "task_deleted_noted", ulid,
                   "tombstone noted (system/settled row -- no action)")
        return "deleted_noted"

    surface = tasks_projection_from_item(item)
    snap_hash, snap = load_push_snapshot(state_conn, ulid)
    if snap_hash is not None and projection_hash(surface) == snap_hash:
        return "echo_skip"

    row = src_conn.execute("SELECT * FROM timeblock WHERE ulid=?", (ulid,)).fetchone()
    if row is None:
        mirror_log(state_conn, "inbound_unknown_ulid", ulid, "tasks item")
        return "unknown_ulid"
    event = dict(row)
    local = tasks_projection_from_event(event)

    applied, failures = [], []
    content_changes = {}
    for f in surface:
        surf_v = surface.get(f)
        snap_v = snap.get(f) if snap else None
        local_v = local.get(f)
        if surf_v == snap_v:
            continue
        if local_v == surf_v:
            continue  # already in sync locally -- nothing to apply
        if local_v != snap_v and local_v != surf_v:
            mirror_log(state_conn, "conflict_surface_wins", ulid,
                       "field=%s local=%r surface=%r" % (f, local_v, surf_v))
        if f in TASK_CONTENT_FIELDS:
            content_changes[f] = surf_v
        elif f == "due_date":
            if surf_v is None:
                res, warn = _run_verb_tuple(lambda: sdk_bridge.bypass_schedule_apply(
                    src_conn, ulid, "untimed", None, None))
                if res == "rejected_untimed":
                    # DGN-240 T12 (spec 5.5, M-C): a routine instance never
                    # goes untimed -- classified TERMINAL result, not a verb
                    # failure. (a) audit line; (b) bookkeeping advances:
                    # applied-class, so the end-of-function snapshot records
                    # the current surface projection (no 5-min re-fire);
                    # (c) re-echo: SoT keeps its due, so the next outbound
                    # drain restores due on the surface ("this edit is
                    # void" expressed by surface restoration).
                    mirror_log(state_conn, "routine_untimed_rejected", ulid,
                               "due-clear inbound on routine instance")
                    outbox_enqueue(state_conn, ulid)
                    applied.append("due:rejected_untimed")
                    continue
            else:
                s, e = all_day_instants_from_date(
                    surf_v, event.get("display_tz") or DISPLAY_TZ_NAME)
                res, warn = _run_verb_tuple(lambda: sdk_bridge.bypass_schedule_apply(
                    src_conn, ulid, "all_day", s, e))
            if res in sdk_bridge.BYPASS_SCHEDULE_TERMINAL:
                # D3-2/D4 parity on the tasks lane: a terminal refusal is not a
                # retryable verb failure. Applied-class bookkeeping (so there
                # is no 5-minute re-fire) + canonical re-push + owner message.
                mirror_log(state_conn, "schedule_inbound_rejected", ulid,
                           "tasks due edit refused (%s); canonical re-push "
                           "enqueued + owner notified" % res)
                outbox_enqueue(state_conn, ulid)
                notify_mod.notify(
                    state_conn, "schedule_edit_refused", ulid,
                    title=_title_of(src_conn, ulid),
                    reason=notify_mod.schedule_refusal_reason(res))
                applied.append("due:%s" % res)
                continue
            (applied if res in VERB_OK else failures).append("due:%s" % res)
            if warn:
                mirror_log(state_conn, "bypass_overlap_notice", ulid, warn)
                _overlap_defer(ulid, warn)  # DGN-333: notify at batch end
            if res in VERB_OK:
                # a TIMED task with a place is a travel anchor too (G1), so the
                # due lane needs the same re-derive as the calendar lane.
                _refresh_travel_after_schedule(src_conn, state_conn, ulid)
                fresh = src_conn.execute(
                    "SELECT * FROM timeblock WHERE ulid=?", (ulid,)).fetchone()
                if fresh is not None and route_surface(dict(fresh)) != "tasks":
                    outbox_enqueue(state_conn, ulid)
                    mirror_log(state_conn, "routing_flip_enqueued", ulid,
                               "tasks -> %s" % route_surface(dict(fresh)))
        elif f == "status":
            if surf_v == "completed":
                # grill-4 finding 6: surface completion time, parse->reformat
                # to canonical Z; absent/garbage -> now_utc fallback + log.
                ts = _canonical_from_rfc3339(item.get("completed"))
                if ts is None:
                    mirror_log(state_conn, "completed_ts_fallback", ulid,
                               "completed=%r -> now_utc" % item.get("completed"))
                res = _run_verb(lambda: sdk_bridge.settle_done(
                    src_conn, ulid, "gtasks-inbound", ts))
                (applied if res in VERB_OK else failures).append("settle:%s" % res)
            else:  # needsAction reopen (M8, DGN-180 unsettle verb)
                res, warn = _run_verb_tuple(lambda: sdk_bridge.unsettle(
                    src_conn, ulid, "gtasks-inbound"))
                (applied if res in VERB_OK else failures).append("unsettle:%s" % res)
                if warn:
                    mirror_log(state_conn, "bypass_overlap_notice", ulid, warn)
                    _overlap_defer(ulid, warn)  # DGN-333: notify at batch end

    if content_changes:
        res = _run_verb(lambda: sdk_bridge.content_update(
            src_conn, ulid, content_changes))
        (applied if res in VERB_OK else failures).append("content:%s" % res)

    if failures:
        # grill-4 finding 2: NO snapshot write on failure + audit line.
        mirror_log(state_conn, "verb_failed", ulid,
                   "tasks apply failed=%s applied=%s" % (failures, applied))
        return "apply_failed(%s)" % ",".join(failures)
    if not applied:
        return "no_editable_change"
    store_push_snapshot(state_conn, ulid, "tasks", surface)
    if item.get("etag"):
        bk_set(state_conn, src_conn, ulid, "gtask_etag", item["etag"])
    return "applied(%s)" % ",".join(applied)


def pull_tasks(tl_id, state_conn, src_conn):
    """Public entry: single mirror lock shared with drain (finding 5a)."""
    if not _acquire_drain_lock(state_conn):
        mirror_log(state_conn, "pull_skipped_locked", None, "tasks poll")
        return []
    try:
        return _pull_tasks_locked(tl_id, state_conn, src_conn)
    finally:
        _release_drain_lock(state_conn)


def _pull_tasks_locked(tl_id, state_conn, src_conn):
    """updatedMin watermark pull. F2: pageToken loop, watermark advances only
    after the FULL traversal completes. F3-style per-item try/except."""
    watermark = get_state(state_conn, "tasks_updated_watermark")
    results = []
    items = []
    page_token = None

    while True:
        params = {"tasklist": tl_id, "showHidden": True, "showCompleted": True,
                  "showDeleted": True, "maxResults": 100}
        if watermark:
            params["updatedMin"] = watermark
        if page_token:
            params["pageToken"] = page_token
        resp = gws("tasks", "tasks", "list", "--params", json.dumps(params))
        items.extend(resp.get("items", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    max_updated = watermark
    for item in items:
        try:
            action = _apply_tasks_3way(item, tl_id, state_conn, src_conn)
            results.append({"id": item.get("id"), "action": action})
        except Exception as e:  # per-item isolation
            mirror_log(state_conn, "inbound_item_error", None,
                       "tasks id=%s err=%s" % (item.get("id", "?"), e))
            results.append({"id": item.get("id"), "action": "item_error",
                            "error": str(e)})
        updated = item.get("updated", "")
        if updated and updated > (max_updated or ""):
            max_updated = updated

    # F2: watermark advances only here, after the full traversal. A poll-level
    # exception above never reaches this line -> watermark preserved.
    if max_updated and max_updated != watermark:
        set_state(state_conn, "tasks_updated_watermark", max_updated)
    return results


# ---------------------------------------------------------------------------
# M7: mirror_outbox drain worker
# ---------------------------------------------------------------------------

def outbox_enqueue(state_conn, event_ulid):
    """Idempotent enqueue (unique pending index collapses duplicates)."""
    now = _now_iso()
    try:
        state_conn.execute(
            "INSERT INTO mirror_outbox(event_ulid, op, status, created_at, updated_at) "
            "VALUES(?, 'sync', 'queued', ?, ?)", (event_ulid, now, now))
        state_conn.commit()
        return True
    except sqlite3.IntegrityError:
        state_conn.rollback()
        return False  # already pending


def outbox_backfill(state_conn, src_conn):
    """Resumable backfill: enqueue every in-scope event. Idempotent re-run
    (pending dedup via unique index; already-pushed rows converge on sync)."""
    count = 0
    for row in src_conn.execute("SELECT * FROM timeblock").fetchall():
        ev = dict(row)
        if not in_mirror_scope(ev):
            continue
        if outbox_enqueue(state_conn, ev["ulid"]):
            count += 1
    return count


def _acquire_drain_lock(state_conn, lease_seconds=OUTBOX_LEASE_SECONDS):
    """Single-flight lock (MG180-9): mirror_state row with pid+expiry."""
    now = time.time()
    state_conn.execute("BEGIN IMMEDIATE")
    try:
        row = state_conn.execute(
            "SELECT value FROM mirror_state WHERE key='drain_lock'").fetchone()
        if row:
            try:
                lock = json.loads(row["value"])
                if lock.get("expires", 0) > now:
                    state_conn.rollback()
                    return False
            except Exception:
                pass
        state_conn.execute(
            "INSERT INTO mirror_state(key, value) VALUES('drain_lock', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (json.dumps({"pid": os.getpid(), "expires": now + lease_seconds}),))
        state_conn.commit()
        return True
    except Exception:
        state_conn.rollback()
        raise


def _release_drain_lock(state_conn):
    state_conn.execute("DELETE FROM mirror_state WHERE key='drain_lock'")
    state_conn.commit()


def outbox_drain(state_conn, src_conn, cal_ids, tl_id, max_items=50,
                 backoff_base=1.0, sleep_fn=time.sleep):
    """Drain the outbox: claim -> sync -> pushed | retry(backoff) | failed.
    - claim/lease: stale claims (lease expired) are reclaimed.
    - 403/429/5xx -> exponential backoff (base * 2^attempts), bounded by
      OUTBOX_MAX_ATTEMPTS, then failed + exhaustion log (owner warning stub).
    - single-flight via drain_lock (MG180-9).
    Returns summary dict."""
    if not _acquire_drain_lock(state_conn):
        return {"status": "locked", "pushed": 0, "retried": 0, "failed": 0,
                "skipped": 0}

    pushed = retried = failed = skipped = 0
    exhausted_rows = []  # g5-4: per-drain aggregation buffer
    try:
        now = _now_iso()
        stale = (datetime.now(timezone.utc)
                 - timedelta(seconds=OUTBOX_LEASE_SECONDS)).strftime("%Y-%m-%dT%H:%M:%SZ")
        state_conn.execute(
            "UPDATE mirror_outbox SET status='claimed', lease_at=?, updated_at=? "
            "WHERE id IN (SELECT id FROM mirror_outbox WHERE status='queued' "
            "OR (status='claimed' AND lease_at < ?) ORDER BY id LIMIT ?)",
            (now, now, stale, max_items))
        state_conn.commit()

        rows = state_conn.execute(
            "SELECT * FROM mirror_outbox WHERE status='claimed' AND lease_at=? "
            "ORDER BY id", (now,)).fetchall()

        for row in rows:
            ulid = row["event_ulid"]
            attempts = row["attempts"]
            ev_row = src_conn.execute(
                "SELECT * FROM timeblock WHERE ulid=?", (ulid,)).fetchone()
            if ev_row is None:
                # TERMINAL on first sight. A ulid is minted per row and a
                # hard-deleted row never comes back under it, so there is
                # nothing to retry -- yet this landed as a plain 'failed'
                # with dead=0, which let reconcile.retry_failed burn its
                # whole requeue budget re-draining a row that cannot exist.
                # Measured 2026-08-25: 264 such rows (every one of them
                # "event row not found", 264 distinct ulids, none present in
                # timeblock), 93 of which had already ground through 1-3
                # pointless requeues to reach dead=1. Marked dead at source
                # instead. Cause of the enqueue itself is upstream: a derived
                # block (travel/prep) enqueued and then hard-deleted by the
                # travel re-derive before the drain claimed it -- benign, the
                # surface object is retired on the same pass.
                state_conn.execute(
                    "UPDATE mirror_outbox SET status='failed', dead=1, "
                    "last_error=?, updated_at=? WHERE id=?",
                    ("terminal: event row not found", _now_iso(), row["id"]))
                state_conn.commit()
                mirror_log(state_conn, "outbox_terminal_skip", ulid,
                           "SoT row hard-deleted before drain -- row marked "
                           "dead, not requeued")
                failed += 1
                continue
            try:
                res = sync_event(dict(ev_row), cal_ids, tl_id,
                                 state_conn, src_conn)
                if (res or {}).get("status") in SYNC_TERMINAL_SKIP:
                    # TERMINAL, not a push and not a retry. The scope
                    # predicate is pure over the row, so re-draining recomputes
                    # the identical refusal -- recording it as 'pushed' is what
                    # let 2,515 identical enqueues look like 2,515 successes in
                    # the ledger for 13 days. dead=1 is this codebase's
                    # existing terminal marker (reconcile.retry_failed skips
                    # dead rows; repair's g6-8 guard refuses to re-enqueue a
                    # dead ulid), so the row cannot be revived into a loop.
                    state_conn.execute(
                        "UPDATE mirror_outbox SET status='failed', dead=1, "
                        "last_error=?, updated_at=? WHERE id=?",
                        ("terminal: %s" % res["status"], _now_iso(),
                         row["id"]))
                    state_conn.commit()
                    mirror_log(state_conn, "outbox_terminal_skip", ulid,
                               "sync refused terminally (%s) -- row marked "
                               "dead, not requeued" % res["status"])
                    skipped += 1
                    continue
                state_conn.execute(
                    "UPDATE mirror_outbox SET status='pushed', updated_at=? "
                    "WHERE id=?", (_now_iso(), row["id"]))
                state_conn.commit()
                pushed += 1
            except GwsError as e:
                attempts += 1
                if e.http_code in RETRYABLE_HTTP and attempts < OUTBOX_MAX_ATTEMPTS:
                    sleep_fn(backoff_base * (2 ** (attempts - 1)))
                    state_conn.execute(
                        "UPDATE mirror_outbox SET status='queued', attempts=?, "
                        "last_error=?, updated_at=? WHERE id=?",
                        (attempts, str(e), _now_iso(), row["id"]))
                    state_conn.commit()
                    retried += 1
                else:
                    state_conn.execute(
                        "UPDATE mirror_outbox SET status='failed', attempts=?, "
                        "last_error=?, updated_at=? WHERE id=?",
                        (attempts, str(e), _now_iso(), row["id"]))
                    state_conn.commit()
                    mirror_log(state_conn, "outbox_exhausted", ulid,
                               "attempts=%d err=%s -- owner warning required"
                               % (attempts, e))
                    exhausted_rows.append(
                        (ulid, _title_of(src_conn, ulid)))
                    failed += 1
            except Exception as e:
                state_conn.execute(
                    "UPDATE mirror_outbox SET status='failed', attempts=?, "
                    "last_error=?, updated_at=? WHERE id=?",
                    (attempts + 1, str(e), _now_iso(), row["id"]))
                state_conn.commit()
                failed += 1
    finally:
        _release_drain_lock(state_conn)

    # g5-4: per-kind per-drain aggregation -- above the threshold a single
    # aggregate message replaces the 1:1 flood.
    if exhausted_rows:
        if len(exhausted_rows) > NOTIFY_AGG_THRESHOLD:
            notify_mod.notify(state_conn, "outbox_exhausted_agg", None,
                              dedup=False, count=len(exhausted_rows))
        else:
            for u, t in exhausted_rows:
                notify_mod.notify(state_conn, "outbox_exhausted", u, title=t)

    return {"status": "ok", "pushed": pushed, "retried": retried,
            "failed": failed, "skipped": skipped}


# ---------------------------------------------------------------------------
# X10b-3: time-based status sweep (V5 backbone / grill-4 finding 9).
# Framework ruling: the adapter poller owns the sweep step.
# ---------------------------------------------------------------------------

# DGN-849 A2: config knob; default = prior literal.
SWEEP_GRACE_SECONDS = _conf_int(
    "MIRROR_SWEEP_GRACE_SECONDS", 0)  # dec-010 (owner ruling): grace = 0 -- expiry
# materializes immediately at effective end (exclusive-end semantics match
# the 179 predicate; no deferral window).


def sweep_step(state_conn, src_conn):
    """Materialize time-based status transitions on open rows with an
    elapsed deadline (idempotent; runs first in every poller cycle):
      - appointment + summary text present -> settle done (W5 promotion:
        summary = owner's active assert; time-gate already passed here).
      - otherwise -> recompute derived status (expired materialization).
    Changed rows are enqueued for surface re-push. Returns change list."""
    now = (datetime.now(timezone.utc)
           - timedelta(seconds=SWEEP_GRACE_SECONDS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    changed = []
    # DGN-240 T2 (spec 2.3, OQ-3): expired-task rows are terminal for this
    # sweep -- every escape from 'expired' goes through a verb inline
    # recompute (derive_status priority), so re-scanning them each poll is
    # pure waste on a monotonically growing set. Appointments stay in
    # (summary-done late promotion, OQ-3 carve-out).
    # DGN-579 D-C3/A3: presence never expires or auto-completes (spec A3),
    # so it is excluded here -- now that presence is mirror scope, scanning
    # it would recompute-churn every elapsed banner on every poll.
    rows = src_conn.execute(
        "SELECT * FROM timeblock WHERE settled_at IS NULL "
        "AND start_at IS NOT NULL "
        "AND kind != 'presence' "
        "AND NOT (kind='task' AND status='expired')").fetchall()
    for r in rows:
        ev = dict(r)
        if not in_mirror_scope(ev):
            # DGN-240 T1 landed: roller instances are now IN scope, so their
            # expiry IS materialized here (spec 2.3 sweep integration; the
            # grill-5 finding-10 revisit note is resolved). This skip now
            # excludes only notion-import history + anchor-less belt rows.
            continue
        eff_end = sdk_bridge.ec._cand_eff_end(
            ev["start_at"], ev["end_at"], ev["open_ended"])
        if eff_end == sdk_bridge.ec.INF_STR or now < eff_end:
            continue  # not elapsed (open-ended never expires by time)
        if ev["kind"] == "appointment" and (ev.get("summary") or "").strip():
            res = _run_verb(lambda: sdk_bridge.settle_done(
                src_conn, ev["ulid"], "sweep-summary-done"))
            if res == "applied":
                changed.append((ev["ulid"], "done"))
                outbox_enqueue(state_conn, ev["ulid"])
        else:
            # grill-5 finding 5: derive Python-side FIRST; open a write txn
            # only when the cached status actually differs. Otherwise the
            # monotonically growing expired backlog would take a no-op write
            # lock per row per poll on the live DB.
            live = [x[0] for x in src_conn.execute(
                "SELECT done FROM sub_event WHERE event_id=? AND tombstone=0",
                (ev["id"],)).fetchall()]
            expected = sdk_bridge.ec.derive_status(
                ev["schedule_kind"], ev["start_at"], ev["end_at"],
                ev["open_ended"], ev["settled_at"], ev["settled_outcome"],
                live, ev["completion_rule"], now=now)
            if expected == ev["status"]:
                continue  # cache already correct -- zero write txns
            new_status = sdk_bridge.recompute(src_conn, ev["ulid"])
            if new_status is not None and new_status != ev["status"]:
                changed.append((ev["ulid"], new_status))
                outbox_enqueue(state_conn, ev["ulid"])

    # DGN-302: abandoned-transition tombstone scan.
    # The main scan above is gated on settled_at IS NULL, which correctly
    # excludes settled rows. But abandoned rows (settled_outcome='abandoned'
    # OR status='abandoned') may still hold a push_snapshot from when they
    # were last mirrored as 'confirmed'. The cancel push was never enqueued
    # because the settlement happened outside the sweep window. Find every
    # abandoned in-scope row that has a snapshot whose gcal_status is NOT
    # already 'cancelled', and enqueue a sync so push_calendar projects the
    # correct 'cancelled' status. Convergence: on the second sweep the
    # snapshot is updated to gcal_status='cancelled' by the drain, so the
    # hash comparison below finds nothing new and no re-enqueue happens.
    abandoned_rows = src_conn.execute(
        "SELECT * FROM timeblock WHERE "
        "(settled_outcome='abandoned' OR status='abandoned')").fetchall()
    for r in abandoned_rows:
        ev = dict(r)
        # 2026-08-25: the in_mirror_scope gate that stood here was the
        # PRODUCER half of the 12798 hot loop, for two compounding reasons.
        #
        # (1) It ran on a RAW dict(row). in_mirror_scope's 집쉼 gate reads the
        #     ENRICHED place render, and its own docstring leans on "the
        #     push/reconcile paths always enrich first" -- reconcile.py does
        #     (see its explicit enrich-before-scope comment); this sweep never
        #     did. So the two halves ran the SAME predicate on DIFFERENT input:
        #     raw (location='', place_id=2) said in-scope -> enqueue; enriched
        #     (location='집', title='재택' != '회사') said hidden -> refuse.
        #
        # (2) Enriching here would have silenced the loop by stranding the
        #     row: an out-of-scope row with a live 'confirmed' snapshot still
        #     needs its cancellation DELIVERED. So the scope gate is not
        #     restored in enriched form -- it is dropped. The snapshot gate
        #     below is the strictly correct and strictly stronger predicate
        #     for this scan: "Google still shows something for this abandoned
        #     row". Rows that were never pushed (notion-import history,
        #     anchor-less belt rows) have no snapshot and drop out there.
        # The consumer (_scope_exit) now retires such rows, so this enqueue is
        # productive and converges on the very next drain.
        snap_hash, snap = load_push_snapshot(state_conn, ev["ulid"])
        if snap_hash is None:
            continue  # no snapshot: row was never pushed, nothing to cancel
        if snap.get("gcal_status") == "cancelled":
            continue  # snapshot already reflects cancelled -- converged
        if outbox_enqueue(state_conn, ev["ulid"]):
            changed.append((ev["ulid"], "abandoned"))

    return changed


def _run_cycle_step(out, key, state_conn, fn):
    """DGN-268 S5: run one poll-cycle step under its own exception guard so a
    failure in one step does NOT abort the others. On success the step's normal
    result lands in out[key]; on failure out[key] becomes {"error": "..."} (the
    stdout summary still prints; the failure is visible + counted) and the error
    is logged via mirror_log. Ordering + idempotency are preserved because each
    step is already independent and self-locking (pulls + drain each acquire and
    release the shared mirror lock in their own finally, so a raised step never
    holds the lock hostage). NOTHING propagates -- a transient inbound 404 must
    never crash the cron or block the outbound drain.

    Error texture matches the rest of the module: GwsError carries http_code, so
    we tag transient (RETRYABLE_HTTP or 404 not-found) vs persistent. Persistent
    errors are logged at a distinct category for the weekly reconcile / operator
    to notice; we do NOT invent a new per-cycle notify/alarm (a single transient
    404 must stay silent -- the once/day on-but-unauth warning already owns the
    auth-failure alarm)."""
    try:
        out[key] = fn()
        return True
    except GwsError as e:
        transient = (e.http_code == 404 or e.http_code in RETRYABLE_HTTP)
        category = ("cycle_step_transient" if transient
                    else "cycle_step_persistent")
        mirror_log(state_conn, category, None,
                   "step=%s http=%s: %s" % (key, e.http_code, e))
        out[key] = {"error": str(e), "http_code": e.http_code,
                    "transient": transient}
        return False
    except Exception as e:  # non-GwsError: unexpected, treat as persistent
        mirror_log(state_conn, "cycle_step_persistent", None,
                   "step=%s: %s" % (key, e))
        out[key] = {"error": str(e), "http_code": None, "transient": False}
        return False


# ---------------------------------------------------------------------------
# Persistent-failure alert (ticket 2026-09-23 kitdb-index-corruption-mirror-
# down). On 2026-09-23 the kit DB carried a corrupt index and every cycle
# logged `cycle_step_persistent step=sweep: database disk image is malformed`
# -- 22 cycles, 1h27m -- and nobody was told; it was found by accident.
#
# Threshold: PERSIST_ALERT_CYCLES = 3 consecutive cycles with at least one
# PERSISTENT step failure (the category _run_cycle_step already assigns; a
# transient 404/5xx/429 never counts). At the 5-minute cadence that is the
# first failure plus ~10 minutes, i.e. the 3rd cycle, ~15 minutes into the
# window. Why not 1: a single persistent hit can be one cycle racing a manual
# DB operation (a restore, a REINDEX, a migration holding the file) and
# clears by itself; 2 still fits one such operation straddling two cycles.
# 3 means the failure survived two retries ten minutes apart, which is not a
# race. Why not more: the calendar is the owner's day; every extra cycle is
# another 5 minutes of drift nobody knows about (09-23 would have alerted at
# ~07:01Z instead of never).
#
# One alert per INCIDENT (streak of failing cycles; any clean cycle ends it),
# plus one recovery line when an alerted incident ends. Routed operator-only
# (notify.MACHINE_REPORT_KINDS: kit MIRROR_OPS_ENV_FILE, else push.sh
# --audience operator --no-owner-fallback, else nothing): machine/status
# reports go to the kit operator, the owner channel gets decision items only. State lives in mirror_state KV so it
# survives the per-cycle process.
# ---------------------------------------------------------------------------
PERSIST_ALERT_CYCLES = 3
_PERSIST_STREAK_KEY = "persist_fail_streak"
_PERSIST_SINCE_KEY = "persist_fail_since"
_PERSIST_ALERTED_KEY = "persist_fail_alerted"

_ALERT_SCRUB = (
    (re.compile(r"https?://\S+"), "<url>"),
    (re.compile(r"(?<![\w.])/[^\s:'\"]+"), "<path>"),
    (re.compile(r"(?<![0-9A-Za-z])[0-9a-fA-F]{7,}(?![0-9A-Za-z])"), "<id>"),
)
_ALERT_ERROR_MAX = 120


def _persistent_failures(out):
    """[(step, error)] for this cycle's persistent step failures, in step
    order. A failed step is exactly a dict carrying both 'error' and
    'transient' (the _run_cycle_step failure shape)."""
    fails = []
    for key, val in out.items():
        if (isinstance(val, dict) and "error" in val and "transient" in val
                and not val.get("transient")):
            fails.append((key, str(val.get("error") or "")))
    return fails


def _alert_error_text(err):
    """One short line for a chat: no URLs, paths or hex ids (push.sh's
    execution-identifier guard refuses those, and they mean nothing in a
    chat). The full text stays in mirror_log."""
    text = " ".join(str(err).split())
    for rx, repl in _ALERT_SCRUB:
        text = rx.sub(repl, text)
    if len(text) > _ALERT_ERROR_MAX:
        text = text[:_ALERT_ERROR_MAX - 3].rstrip() + "..."
    return text or "?"


def _minutes_since(since_iso, now):
    try:
        t0 = datetime.strptime(since_iso, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return 0
    return max(0, int(round((now - t0).total_seconds() / 60.0)))


def persistent_failure_watch(state_conn, out, now=None):
    """Advance the persistent-failure streak from one finished cycle's `out`
    and queue at most one operator notice. Returns a small status dict."""
    now = now or datetime.now(timezone.utc)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    fails = _persistent_failures(out)
    streak = int(get_state(state_conn, _PERSIST_STREAK_KEY, "0") or 0)
    since = get_state(state_conn, _PERSIST_SINCE_KEY) or ""
    alerted = get_state(state_conn, _PERSIST_ALERTED_KEY) == "1"
    if fails:
        streak += 1
        if streak == 1 or not since:
            since = now_iso
            set_state(state_conn, _PERSIST_SINCE_KEY, since)
        set_state(state_conn, _PERSIST_STREAK_KEY, str(streak))
        if streak >= PERSIST_ALERT_CYCLES and not alerted:
            steps = ", ".join(k for k, _ in fails)
            notify_mod.notify(
                state_conn, "mirror_persistent_failure",
                event_ulid="persist:%s" % since,
                cycles=streak, minutes=_minutes_since(since, now),
                steps=steps, error=_alert_error_text(fails[0][1]))
            set_state(state_conn, _PERSIST_ALERTED_KEY, "1")
            mirror_log(state_conn, "persist_alert", None,
                       "cycles=%d since=%s steps=%s" % (streak, since, steps))
            return {"streak": streak, "alert": "failure"}
        return {"streak": streak, "alert": None}
    if streak == 0 and not alerted:
        return {"streak": 0, "alert": None}
    result = {"streak": 0, "alert": None}
    if alerted:
        notify_mod.notify(
            state_conn, "mirror_persistent_recovered",
            event_ulid="persist:%s" % since,
            cycles=streak, minutes=_minutes_since(since, now))
        mirror_log(state_conn, "persist_recovered", None,
                   "cycles=%d since=%s" % (streak, since))
        result["alert"] = "recovered"
    set_state(state_conn, _PERSIST_STREAK_KEY, "0")
    set_state(state_conn, _PERSIST_SINCE_KEY, "")
    set_state(state_conn, _PERSIST_ALERTED_KEY, "0")
    return result


def travel_attach_step(state_conn, src_conn):
    """Cycle step: (re-)attach move/prep to future place-bearing appointments.

    Logs only when it ATTACHED something. The elicit outcomes (unknown_place /
    no_duration / edge_missing) are steady-state here -- an appointment whose
    venue nobody has registered a duration for reports the same thing every
    five minutes -- so logging them per cycle would bury the log and tell the
    owner nothing new. They are surfaced by `kit_life.sh travel-sweep`, which
    runs inside a conversational turn where the one-time question can actually
    be asked (the same split _INBOUND_TRAVEL_NEEDS already documents). The
    per-cycle needs COUNT rides on the attach line so a growing backlog is
    still visible.

    'retired' rows are counted apart and never logged: an appointment whose
    travel rule the owner retired is a standing decision this loop honors, not
    a backlog item and not a failure. (It also must not be re-derived -- this
    five-minute loop is exactly what made retire stop working; the refusal now
    lives in the deriver, see kit_life._travel_anchor_retired.)"""
    res = sdk_bridge.ec.travel_attach_sweep(src_conn)
    if res.get("attached"):
        mirror_log(state_conn, "travel_attached", None,
                   "attached=%s scanned=%s needs=%s"
                   % (res["attached"], res["scanned"], res["needs"]))
    return {"attached": res["attached"], "scanned": res["scanned"],
            "exists": res["exists"], "needs": res["needs"],
            "retired": res.get("retired", 0)}


def poll_cycle(state_conn, src_conn, cal_ids, tl_id):
    """One full poller cycle (the cron target at cutover):
    sweep -> inbound pulls (dict-shape scan set, V15) -> outbox drain.

    DGN-268 S5: each step is isolated (see _run_cycle_step). The outbound DRAIN
    always runs even if an inbound pull raised -- the outbound path must never
    be held hostage to an inbound 404 (the 2026-07-12 09:21 live-instance
    starvation: a transient pull_calendar 404 aborted the whole cycle before
    drain could push).

    DGN-364 2.3 (V15 loop MERGED with S5 isolation): the calendar scan set
    comes from _calendar_scan_set (2.2). Legacy mode (single unique id) emits
    the pre-promotion output keys byte-identically
    (sweep/calendar/tasks/drain/overlap_recheck) and calls pull_calendar in
    the pre-promotion 3-arg form. Multi mode emits calendar:appt /
    calendar:task / calendar:travel -- a pull failure on one category never
    blocks the others nor the drain; cursor resets stay per-key (TM-6)."""
    cal_ids = _cal_ids_dict(cal_ids)
    out = {}
    _run_cycle_step(out, "sweep", state_conn,
                    lambda: sweep_step(state_conn, src_conn))
    for cid, cal_key in _calendar_scan_set(cal_ids):
        if cal_key is None:
            # Legacy branch: byte-identical pre-promotion call shape.
            _run_cycle_step(out, "calendar", state_conn,
                            lambda cid=cid:
                                pull_calendar(cid, state_conn, src_conn))
        else:
            _run_cycle_step(out, "calendar:%s" % cal_key, state_conn,
                            lambda cid=cid, k=cal_key:
                                pull_calendar(cid, state_conn, src_conn,
                                              cal_key=k))
    _run_cycle_step(out, "tasks", state_conn,
                    lambda: pull_tasks(tl_id, state_conn, src_conn))
    # Travel attach sweep: the ONLY periodic loop this instance runs is this
    # cycle, so it is the only place a convergence pass can actually live.
    # Every per-row attach hook (appt-add, the location/time edits, the
    # inbound adopt) fires once, at intake, for the row in front of it -- an
    # appointment that missed that moment was never looked at again and the
    # owner found the hole himself. This re-runs the SAME idempotent deriver
    # over the future horizon, so a venue that becomes resolvable later (the
    # place row or the edge gets registered in a conversational turn) derives
    # on the next cycle without anyone asking. Isolated like every other step.
    _run_cycle_step(out, "travel_attach", state_conn,
                    lambda: travel_attach_step(state_conn, src_conn))
    # Drain runs unconditionally -- it is the outbound lane and must not be
    # skipped just because an inbound pull failed above.
    _run_cycle_step(out, "drain", state_conn,
                    lambda: outbox_drain(state_conn, src_conn, cal_ids, tl_id))
    # DGN-333 (MAJOR-5 rev): batch-end recheck of deferred overlap notices --
    # only overlaps that survive the whole cycle reach the owner.
    out["overlap_recheck"] = overlap_flush(state_conn, src_conn)
    # Persistent-failure alert (see persistent_failure_watch). Its own guard:
    # the watcher must never be the thing that breaks a cycle.
    if state_conn is not None:
        try:
            out["persist_watch"] = persistent_failure_watch(state_conn, out)
        except Exception as e:
            out["persist_watch"] = {"error": "watch: %s" % e}
    # Day-card leg 2 for INBOUND writes. The pin refreshes from kit_life
    # main() after a write verb, but this cycle writes through direct function
    # calls and never reaches main(): measured 2026-09-24 11:43, an owner edit
    # in the calendar landed in the DB and the pinned card kept the old day
    # until the next hourly run. Once per cycle, AFTER every step committed.
    if _cycle_moved_today(out):
        _refresh_day_card()
    return out


# pull_calendar / pull_tasks return one {"action": ...} per DELIVERED item,
# not per applied one: updatedMin is inclusive, so the tasks pull redelivers
# the newest item every cycle and reports len == 1 on a fully idle cycle
# (echo_skip). Only these actions mean the SoT changed. Everything else --
# echo_skip / no_editable_change / foreign_skip / *_reechoed / *_refused /
# apply_failed / unknown_ulid / deleted_noted / drift_attention -- left the
# blocks alone. "applied(due:...)" of a refused routine untimed is a harmless
# false positive (one extra idempotent refresh).
_INBOUND_APPLIED_ACTIONS = ("applied(", "adopted", "cancelled_abandoned",
                            "deleted_abandoned_applied")


def _cycle_moved_today(out):
    """True iff THIS cycle's inbound lanes (calendar / calendar:<key> / tasks)
    applied a create/update/delete/move, or the travel-attach step attached a
    derived block. Outbound (drain), sweep-only and idle cycles are False."""
    for key, val in out.items():
        if key == "tasks" or key == "calendar" or key.startswith("calendar:"):
            if not isinstance(val, list):
                continue        # {"error": ...} from a failed step
            for r in val:
                act = r.get("action") if isinstance(r, dict) else None
                if isinstance(act, str) and act.startswith(
                        _INBOUND_APPLIED_ACTIONS):
                    return True
    ta = out.get("travel_attach")
    return isinstance(ta, dict) and (ta.get("attached") or 0) > 0


def _refresh_day_card():
    """Best-effort, silent: same contract as kit_life._day_card_refresh (a
    display surface must never fail or slow the sync). Also swallows an old
    kit_life that has no such function."""
    try:
        sdk_bridge.ec._day_card_refresh()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

def cleanup(conn):
    """Teardown (DGN-364 2.5, normative): reads the generic state keys
    DIRECTLY -- it does NOT route through get_mirror_targets (which raises on
    partial states; cleanup needs the partial ids themselves).
      - Calendar deletions: every DISTINCT engraved id among
        {cal_id_appt, cal_id_task, cal_id_travel, agent_calendar_id}.
      - Tasklist deletions: every DISTINCT engraved id among
        {gtasks_checklist_id, agent_tasklist_id}.
    Partial/mixed states are handled by construction (whatever ids exist get
    deleted; missing keys are skipped silently -- cleanup is a teardown, not
    a validator). Explicit (m2): the Ag-literal keys ag_calendar_id /
    ag_tasklist_id are NEVER read by generic cleanup."""
    result = {"calendar": [], "tasklist": []}
    cal_ids = []
    for key in ("cal_id_appt", "cal_id_task", "cal_id_travel",
                LEGACY_CAL_STATE_KEY):
        v = get_state(conn, key)
        if _is_engraved(v) and v not in cal_ids:
            cal_ids.append(v)
    tl_ids = []
    for key in (CHECKLIST_STATE_KEY, LEGACY_TASKLIST_STATE_KEY):
        v = get_state(conn, key)
        if _is_engraved(v) and v not in tl_ids:
            tl_ids.append(v)
    for cal_id in cal_ids:
        try:
            gws_delete("calendar", "calendars", "delete",
                       "--params", json.dumps({"calendarId": cal_id}))
            result["calendar"].append("deleted:%s" % cal_id)
        except Exception as e:
            result["calendar"].append("ERROR:%s" % e)
    for tl_id in tl_ids:
        try:
            gws_delete("tasks", "tasklists", "delete",
                       "--params", json.dumps({"tasklist": tl_id}))
            result["tasklist"].append("deleted:%s" % tl_id)
        except Exception as e:
            result["tasklist"].append("ERROR:%s" % e)
    return result


# ---------------------------------------------------------------------------
# Source DB (M11: 179 get_conn discipline via sdk_bridge)
# ---------------------------------------------------------------------------

def get_src_conn() -> sqlite3.Connection:
    return sdk_bridge.get_conn(DB_PATH)


def load_sample_events(src_conn) -> list:
    """~20 sample events covering the routing buckets (all 6 all_day appts in
    live data are done travel containers -- included as such)."""
    buckets = [
        ("appointment", "timed", None, 5),
        ("appointment", "all_day", "done", 2),
        ("task", "timed", None, 5),
        ("task", "all_day", None, 3),
        ("task", "untimed", None, 3),
        ("task", "timed", "done", 1),
        ("appointment", "timed", "done", 1),
    ]
    rows, seen = [], set()
    for kind, sk, outcome, n in buckets:
        if outcome:
            q = ("SELECT * FROM timeblock WHERE kind=? AND schedule_kind=? "
                 "AND settled_outcome=? LIMIT ?")
            r = src_conn.execute(q, (kind, sk, outcome, n)).fetchall()
        else:
            q = ("SELECT * FROM timeblock WHERE kind=? AND schedule_kind=? "
                 "AND settled_outcome IS NULL LIMIT ?")
            r = src_conn.execute(q, (kind, sk, n)).fetchall()
        for row in r:
            d = dict(row)
            if d["ulid"] not in seen and in_mirror_scope(d):
                seen.add(d["ulid"])
                rows.append(d)
    return rows
