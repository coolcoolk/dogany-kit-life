"""sot_registry.py -- lifekit's ledger-preload provider (DGN-1176, kit half).

The framework (memory-engine/memory.py, canonical dev 5cc69601) owns WHEN the
ledgers are read, in what order, under what budget, and every failure branch.
This module owns WHAT the ledgers are and HOW each one is read.  It exposes
exactly two names to the frame:

    resolve_identity()      -> str | None
    get_ledger_registry()   -> list[entry]

LANDING PRECONDITION (2026-09-02)
    Registry mode REPLACES the frozen `_hook_body_state_line` fallback; it does
    not merge with it.  A provider that ships without the `body` entry silently
    deletes the every-turn body/goal line and nothing errors.  `body` is
    therefore a MANDATORY FIRST ENTRY.  Never remove it to ship something else.

READ DISCIPLINE (every callable in this file)
    - in-process.  A subprocess per ledger costs a flat ~125ms; 14 of those is
      ~1.75s per turn, which is dead on arrival.  In-process marginal reads
      measure 0.8-2.1ms.
    - side-effect-free.  Every connection is opened with `PRAGMA query_only=ON`
      and closed without a commit.  A read entry that mutates is a contract
      violation, not a caveat.
    - NOT `mode=ro`.  The shared crew DB runs in WAL; a uri `mode=ro`
      connection cannot set up the -shm and dies with "unable to open database
      file" -- but ONLY once the WAL is dirty, i.e. exactly on the turns right
      after a write.  That is the planned-meal-inject incident: a dead path
      masquerading as a quiet day.  `query_only=ON` opens normally so WAL
      works, and SQLite itself then refuses every write from the connection.
    - `get_conn()` from the engine is deliberately NOT used: it issues
      `PRAGMA journal_mode = WAL`, which is a write, and its close can trigger
      a WAL checkpoint that rewrites the main DB file.

INVIOLABLE IS AN ASSERTION, NOT A SHAPE (settled with the framework)
    `ledger_constraint.value` is TEXT.  `calorie_floor` is already a JSON
    policy object and a future `injury_knee` marked inviolable would be pure
    prose.  Nothing here parses, coerces, truncates or branches on the shape of
    a row value -- values ride through opaque.  `class='safety'` and
    `inviolable` are ORTHOGONAL and are not collapsed: four `class='safety'`
    rows are `inviolable=0` today.
"""

import datetime
import os
import sqlite3
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

# Busy timeout for a preload read.  Short on purpose: the frame budgets the
# whole lane at 2.0s and a preload that waits on a writer should lose, not
# stall the turn.  The frame's _deadline_call is the hard backstop.
BUSY_TIMEOUT_SEC = 1.5

_FALLBACK_STEMS = ("kit_life", "lifekit")


def _resolve_engine_stem():
    """Engine module stem, co-located with this file (DGN-1238: the canonical
    fixes no name).  Prefer the shared resolver in routines/lib/life_engine.py
    when the instance has it; otherwise probe the two known stems in this same
    directory.  Returns a stem or None."""
    agent_root = os.path.normpath(os.path.join(SCRIPT_DIR, ".."))
    try:
        le_dir = os.path.join(agent_root, "routines", "lib")
        if le_dir not in sys.path:
            sys.path.insert(0, le_dir)
        import life_engine as _le
        stem = _le.stem_in_dir(SCRIPT_DIR, ("py", "db"),
                               _le.pinned_stem(agent_root))
        if stem:
            return stem
    except BaseException:
        pass
    for cand in _FALLBACK_STEMS:
        if os.path.isfile(os.path.join(SCRIPT_DIR, cand + ".py")) and \
           os.path.isfile(os.path.join(SCRIPT_DIR, cand + ".db")):
            return cand
    return None


# Engine import is MODULE-LEVEL on purpose, and that is a failure-mode choice,
# not convenience.  The frame imports this provider under the lane deadline; if
# the import raises, _ledger_registry_load logs FAIL and falls back to the
# FROZEN body-state line -- the owner still gets today's line.  If instead the
# engine were imported lazily inside read(), an engine breakage would surface as
# a dropped `body` read, which the frame correctly renders as ZERO BYTES: the
# line would vanish silently.  Loud-and-fallback beats quiet-and-gone.
_stem = _resolve_engine_stem()
if _stem is None:
    raise ImportError("sot_registry: no life engine beside %s" % SCRIPT_DIR)
import importlib  # noqa: E402
engine = importlib.import_module(_stem)
ENGINE_STEM = _stem
DB_PATH = engine.DB_PATH


# ----------------------------------------------------------------------
# connection
# ----------------------------------------------------------------------

def _ro_conn():
    """Side-effect-free connection.  See READ DISCIPLINE in the module
    docstring for why this is query_only and not mode=ro, and why the engine's
    own get_conn() is not used."""
    conn = sqlite3.connect(DB_PATH, timeout=BUSY_TIMEOUT_SEC)
    conn.execute("PRAGMA query_only=ON")
    return conn


def _date_of(stamp):
    """Date part of a stored timestamp, for as_of.  Handles both stored
    shapes: config.updated_at 'YYYY-MM-DD HH:MM:SS' (localtime) and
    ledger_constraint.recorded_at 'YYYY-MM-DDThh:mm:ssZ'.  Returns None when
    there is nothing to report -- the FRAME renders that as the explicit
    string 'unknown', and omitting the age to look fresh is the defect this
    contract exists to prevent, so nothing here invents a date."""
    if not stamp:
        return None
    s = str(stamp).strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    return s or None


def _oldest(*stamps):
    """The stalest of several as_of dates.  A derived value is only as fresh as
    its stalest input -- eff_goal joins six config rows that age at completely
    different speeds (weight 1 day, NEAT 60+ days), and reporting the freshest
    would be exactly the J1 shape.  None if any input has no date at all."""
    got = [s for s in stamps if s]
    if len(got) != len(stamps):
        return None
    return min(got)


# ----------------------------------------------------------------------
# identity (frame calls this once per traversal, before any other callable)
# ----------------------------------------------------------------------

def resolve_identity():
    """The kit owns identity resolution; the FRAME never reads
    GOAL_OWNING_AGENT or SLUG.  This REUSES the engine's existing ladder
    (`_why_steward`: GOAL_OWNING_AGENT first, facade second, then the
    _WHY_AGENTS whitelist) rather than growing a second one -- a second ladder
    is how a WRITE and a READ end up on different scope keys.

    None -> the frame fail-closes every axis-bearing entry (SKIP-SCOPE).  That
    includes the case where the instance's engine build has no `_why_steward`
    at all (measured: some instance engine builds lack it).  Failing closed on an
    unresolved scope is the contract; a scope leak is silent and cross-agent."""
    try:
        steward = engine._why_steward()
    except BaseException:
        return None
    return steward if isinstance(steward, str) and steward.strip() else None


# ----------------------------------------------------------------------
# entry 1 -- body  (MANDATORY; scalar; always; crew-shared)
# ----------------------------------------------------------------------

# DGN-285 direction-A guard, locked 2026-07-23 and carried over verbatim in
# intent from the frozen fallback: decide from RAW config rows, never from the
# merged load_body_stats() dict.  The load contract is {**DEFAULT_STATS,
# **rows}, so on a fresh instance the merged dict still looks like a complete
# body (code defaults) and the caller cannot tell real user data from
# placeholders.  Injecting that would fabricate a body and poison the
# onboarding interview.  Under the registry the disposal changes shape but not
# meaning: the fallback returned None, this returns UNKNOWN -- both are ZERO
# BYTES, and UNKNOWN explicitly does not mean "no".
_BODY_REQUIRED = ("goal_mode", "weight_kg", "height_cm")

# Config keys compute_targets() actually consumes.  Kept explicit so the
# derived as_of stays honest if the formula grows an input.
_EFF_GOAL_INPUTS = ("weight_kg", "height_cm", "avg_steps", "deficit_kcal",
                    "other_neat_kcal", "lean_mass_kg", "fat_mass_kg")


def _fmt(x):
    """Integer-when-exact float rendering.  Byte-for-byte the `_n()` helper of
    the frozen fallback, so the VALUES the owner sees do not shift on the day
    the registry takes over."""
    try:
        fx = float(x)
        return int(fx) if fx == int(fx) else fx
    except (TypeError, ValueError):
        return x


def _unknown():
    return {"verdict": "UNKNOWN", "values": []}


def read_body(ctx):
    """Current body/goal state -- the line that has ridden every turn since v2.

    Crew-shared: the `config` table has no owning_agent column (schema v35,
    verified with PRAGMA table_info), so axis=None is the TRUE declaration, not
    an omission.  One connection, one pass; no writes.

    2.20.0 -- `source` now tells the owner WHOSE number this is.  The DGN-285
    guard above catches a TOTAL wipe (no goal_mode -> UNKNOWN -> zero bytes),
    but a PARTIAL one walked straight through it: on 2026-09-11 a raw-SQL purge
    took protein_g and deficit_kcal, the merge served DEFAULT_STATS in their
    place, and this line kept reporting `protein=120 (as_of=unknown,
    source=config)` -- which is not merely stale, it is false about its own
    provenance.  A value served from code now says `source=default`, and a
    derived value that folds one says `source=derived+default`."""
    conn = _ro_conn()
    try:
        raw = {}
        for key, value, updated in conn.execute(
                "SELECT key, value, updated_at FROM config;"):
            raw[key] = (value, updated)
        if any(k not in raw for k in _BODY_REQUIRED):
            return _unknown()          # fresh/partial setup -> stay silent
        if not str(raw["goal_mode"][0] or "").strip():
            return _unknown()          # goal_mode is only ever set by real setup
        # Same merge contract as load_body_stats(), reusing the store's own
        # _cast so numeric keys come back typed, but on OUR read-only
        # connection instead of the engine's read-write get_conn().
        stored = engine._CONFIG_STORE._read_rows(conn=conn)
        stats = dict(engine.DEFAULT_STATS)
        stats.update(stored)
        today = datetime.date.today().isoformat()
        burn = engine.agg_day(today, conn=conn).get("burn_kcal", 0) or 0
        targets = engine.compute_targets(stats, exercise_kcal=burn)
        macros = engine.compute_macro_goals(targets["eff_goal"], stats)
    finally:
        conn.close()

    def age(key):
        return _date_of(raw[key][1]) if key in raw else None

    # The which-keys-are-code rule has exactly one home (the engine), reached
    # here through the same merge the engine's own load path uses -- a second
    # copy of it is how a READ and a REPORT end up disagreeing.
    defaults = set(engine.body_stats_defaults_used(
        engine._merge_body_stats(stored)))

    def src_config(key):
        return "default" if key in defaults else "config"

    def src_derived(*keys):
        return "derived+default" if defaults.intersection(keys) else "derived"

    eff_age = _oldest(*[age(k) for k in _EFF_GOAL_INPUTS])
    values = [
        {"name": "goal_mode", "value": stats.get("goal_mode", ""),
         "as_of": age("goal_mode"), "source": src_config("goal_mode")},
        {"name": "weight", "value": _fmt(stats.get("weight_kg", "")),
         "as_of": age("weight_kg"), "source": src_config("weight_kg")},
        # eff_goal folds six config rows AND today's burn.  as_of reports the
        # stalest config input; today's burn can only make it fresher, so
        # naming the stale side is the conservative report.
        {"name": "eff_goal", "value": targets["eff_goal"],
         "as_of": eff_age, "source": src_derived(*_EFF_GOAL_INPUTS)},
        {"name": "protein", "value": _fmt(macros["protein"]),
         "as_of": age("protein_g"), "source": src_config("protein_g")},
        {"name": "carb", "value": _fmt(macros["carb"]),
         "as_of": _oldest(eff_age, age("protein_g"), age("fat_ratio")),
         "source": src_derived(*(_EFF_GOAL_INPUTS
                                 + ("protein_g", "fat_ratio")))},
        {"name": "fat", "value": _fmt(macros["fat"]),
         "as_of": _oldest(eff_age, age("fat_ratio")),
         "source": src_derived(*(_EFF_GOAL_INPUTS + ("fat_ratio",)))},
    ]
    return {"verdict": "KNOWN", "values": values}


# ----------------------------------------------------------------------
# entry 2 -- constraint-graph  (set; always; the safety floor)
# ----------------------------------------------------------------------
# The set boundary is FK CLOSURE **UNION** UNCONDITIONAL, and the union is
# computed by the FRAME, not here.  That split is the whole point: a pure FK
# traversal (ledger_constraint.goal_id -> ledger_goal) drops every row with
# goal_id IS NULL, and on live data those are exactly the two inviolable rows
# (max_deficit_cap, calorie_floor).  They are NULL deliberately -- they are
# domain-wide, not per-goal.  An FK-only implementation would have silently
# shipped a constraint set with its safety floor missing.

def _rows_from(cursor, uncond):
    """Frame-shaped rows.  `label` carries the inviolable ASSERTION as a
    prefix; nothing inspects the value.  `id` lets the frame's union dedup."""
    out = []
    for cid, key, value, inviolable, recorded in cursor:
        label = ("INVIOLABLE " + str(key)) if inviolable else str(key)
        out.append({"id": cid, "label": label, "value": value,
                    "as_of": _date_of(recorded), "inviolable": bool(inviolable),
                    "unconditional": uncond})
    return out


def constraint_anchor(ctx):
    """Active goals owned by this agent.  None is a REAL state (no active
    goal), not a failure: the frame then skips closure and still runs
    unconditional -- the fewer goals exist, the more the safety floor is
    needed.  identity is guaranteed non-empty here because this entry declares
    an axis and the frame fail-closes before calling us."""
    conn = _ro_conn()
    try:
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM ledger_goal "
            "WHERE status = 'active' AND owning_agent = ? ORDER BY id;",
            (ctx["identity"],))]
    finally:
        conn.close()
    return ids or None


def constraint_closure(ctx, anchor):
    """Constraints hanging off this agent's active goals.  Domain-scoped by
    inheritance from the anchor, which is the ratified line: non-inviolable
    constraints stay domain-scoped."""
    ids = list(anchor)
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    conn = _ro_conn()
    try:
        cur = conn.execute(
            "SELECT id, key, value, inviolable, recorded_at "
            "FROM ledger_constraint "
            "WHERE status = 'active' AND goal_id IN (%s) ORDER BY id;" % marks,
            ids)
        return _rows_from(cur, uncond=False)
    finally:
        conn.close()


def constraint_unconditional(ctx):
    """The safety floor: inviolable, domain-wide (goal_id IS NULL) rows.

    REQUIRED FIELD.  An explicitly empty list is a conscious declaration and is
    fine; the field being absent is an unconscious omission and the frame hard-
    rejects the entry (R1).

    Restricted to inviolable=1 on purpose.  `ledger_constraint` has no scoping
    column at schema v35, so a goal_id IS NULL row carries no axis at all.  The
    ratified line is "inviolable crosses scope, non-inviolable stays
    domain-scoped": inviolable rows ride here because crossing scope is their
    contract; the non-inviolable goal_id-NULL rows (today: four narrative
    injury rows) are NOT emitted, because emitting them unscoped would let one
    agent surface another's rows, and there is no honest way to scope them
    until the table gains an axis.  They are not lost -- they are simply not
    yet in the preload, exactly as they are not today."""
    conn = _ro_conn()
    try:
        cur = conn.execute(
            "SELECT id, key, value, inviolable, recorded_at "
            "FROM ledger_constraint "
            "WHERE status = 'active' AND goal_id IS NULL AND inviolable = 1 "
            "ORDER BY id;")
        return _rows_from(cur, uncond=True)
    finally:
        conn.close()


# ----------------------------------------------------------------------
# registry
# ----------------------------------------------------------------------

def get_ledger_registry():
    """Declaration order is traversal order and it is deterministic.

    `body` is first because it is the entry whose absence silently removes the
    line the owner has seen every turn since v2."""
    return [
        {
            "slug": "body",
            "kind": "scalar",
            "mode": "always",
            # config has no owning_agent column at schema v35 -> crew-shared.
            # axis None is DECLARED, never omitted (omission = R3 reject).
            "scope": {"tables": {"config": {"axis": None}}},
            "read": read_body,
        },
        {
            "slug": "constraint-graph",
            "kind": "set",
            # kind=set is forced to mode=always by the frame (R7): an
            # inviolable floor must not stand behind a prompt match.
            "mode": "always",
            "scope": {"tables": {
                "ledger_goal": {"axis": "owning_agent"},
                # No scoping column on this table at schema v35; see
                # constraint_unconditional for why that is bounded to
                # inviolable rows rather than papered over with a fake axis.
                "ledger_constraint": {"axis": None},
            }},
            "anchor": constraint_anchor,
            "closure": constraint_closure,
            "unconditional": constraint_unconditional,
        },
    ]
