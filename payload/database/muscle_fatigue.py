# -*- coding: utf-8 -*-
"""muscle_fatigue.py -- per-muscle fatigue / recovery read model (kit_life 056).

WHAT THIS IS
  The calculation foundation of the muscle-fatigue feature (spec:
  files/outbox/muscle-fatigue-spec.md, phase 1).  Fatigue is NEVER stored: it
  is computed at read time from

      workout_sets  x  the muscle map  x  elapsed-time decay

  so a map fix, a late set edit or a deleted row can never leave a stale
  number behind.  The only stored inputs this module adds are the ones that
  cannot be computed (migration 056): declared regions for set-less sessions
  (workout_types.muscle_node), map provenance (exercise tag map=draft) and
  body signals ("my hamstrings feel stiff" -> pain_event.sensation).

  Verbs (registered in kit_life.py _DISPATCH):
    muscle-state        per-node residual / state / ETA + provenance, the
                        display grouping, body signals, and (with a planned
                        session) the graded fatigue level
    muscle-week         weekly load, overlap days, functional coverage
    body-signal-add / -list / -resolve / -del
    exercise-add / exercise-drafts / exercise-confirm / exercise-tag-del
    workout-unclassify

  This module prints numbers and tokens only.  No user-facing sentence is
  composed here: the session-start / session-end / weekly-retro copy is
  rendered by pack_health (service/health/fatigue-brief, phase 2), which
  reads this module and carries every number it prints from these outputs.  The graded level carries an i18n label (fatigue.level.*)
  so a renderer does not invent its own word for it.

CONSTANTS ARE PROVISIONAL (spec A6 grade C-D, practitioner heuristics).  They
live here, in code, released with the kit version -- they are not safety caps,
so they are not rows.  The grade thresholds carry their derivation below.
"""
import argparse
import datetime
import json
import re
import sqlite3
import sys
import time

# -- load formula (spec A2.2) ----------------------------------------------
KIND_W = {'working': 1.0, 'hold': 1.0, 'ramp': 0.25, 'prep': 0.0}
ROLE_W = {'primary': 1.0, 'secondary': 0.5}
STAB_W = 0.3                       # one fixed tier; never raises a node above
                                   # what the map already credits (max, not sum)
COST_W = {'high': 1.25, 'med': 1.0, 'low': 0.8}

# -- decay / dose (spec A2.3) ----------------------------------------------
MAJOR = ('chest', 'back', 'shoulders', 'quads', 'hamstrings', 'glutes')
MINOR = ('biceps', 'triceps', 'abs', 'calves', 'forearms')
# small muscles: the 056 nodes plus the small subregions the functional check
# watches.  Each carries its own dose instead of a share of its parent.
SMALL = ('rotator_cuff', 'hip_flexors', 'obliques', 'tibialis_anterior',
         'brachialis', 'brachioradialis', 'adductors', 'glute_med', 'serratus',
         'forearm_flexors', 'forearm_extensors')
# Anatomical share of a parent's dose for sub-regions whose heads are grossly
# unequal in size.  Without it a sub-region's dose is the parent's dose split
# EVENLY over its non-small siblings, and an exercise that loads the heads in
# their natural proportion would read hotter on the big head than on the
# whole.  Quadriceps volume (MRI, adult men; vastus intermedius folded into
# the two vasti, the heatmap draws no VI): RF ~15%, VL ~45%, VM ~40%.  A
# parent listed here must list ALL its share sub-regions (shares sum to 1).
# Hamstrings (~50:50 lateral:medial) and every pre-056 split stay even.
ANATOMICAL_SHARE = {'rect_fem': 0.15, 'vast_lat': 0.45, 'vast_med': 0.40}
HL_MAJOR, HL_MINOR, HL_SMALL = 36.0, 24.0, 24.0      # hours
DOSE_MAJOR, DOSE_MINOR, DOSE_SMALL = 6.0, 4.0, 3.0   # effective sets ~= one
                                                     # hard session on it
STATE_RECOVERING = 0.5             # residual/dose
STATE_HARD = 1.0
LOOKBACK_DAYS = 14                 # 24h HL: 2^-14; 36h HL: 2^-9.3 (< 0.2%)
ETA_SCAN_HOURS = 14 * 24
SIGNAL_WINDOW_H = 72               # stiff/tight/sore count as active this long
SIGNAL_SENSATIONS = ('stiff', 'tight', 'sore')
SENSATIONS = ('pain',) + SIGNAL_SENSATIONS
SIGNAL_REFRESH_H = 24              # body-signal-add find-first window
DECLARED_MUSCLE_FACTOR = 1.0       # declared region = one dose on that muscle
DECLARED_GROUP_FACTOR = 0.5        # declared group (legs/arms/core) = half a
                                   # dose on each child muscle
PLAN_DEFAULT_SETS = 3              # a planned exercise without a set count
PLAN_TARGET_SHARE = 0.5            # a planned session "trains" a node when it
                                   # puts >= this x dose on it (spec overlap)
PROVENANCE_MIN_SHARE = 0.05        # provenance rows below 5% of the residual
                                   # are folded away
DISPLAY_TOP = 4                    # owner 11:30: at most 4 lines up front

# -- heatmap node contract (pack_health heatmap renderer) ------------------
# The renderer draws these node keys and reads them from muscle-state --json
# `node_values`; until a key is emitted it paints the parent.  (node, parent)
# -- parent is the renderer's region, None for a standalone region.  'abs' is
# both a node and its own parent: its value is the load credited to the abs
# node itself (rectus), its children carry theirs.
HEATMAP_NODES = (
    ('chest_upper', 'chest'), ('chest_mid', 'chest'),
    ('chest_lower', 'chest'), ('serratus', 'chest'),
    ('front_delt', 'shoulders'), ('side_delt', 'shoulders'),
    ('rear_delt', 'shoulders'), ('rotator_cuff', 'shoulders'),
    ('back_upper', 'back'), ('back_lats', 'back'), ('back_erectors', 'back'),
    ('biceps_prox', 'biceps'), ('biceps_distal', 'biceps'),
    ('brachialis', 'biceps'),
    ('triceps_long', 'triceps'), ('triceps_lat_med', 'triceps'),
    ('forearm_flexors', 'forearms'), ('forearm_extensors', 'forearms'),
    ('brachioradialis', 'forearms'),
    ('abs', 'abs'), ('abs_lower', 'abs'), ('obliques', 'abs'),
    ('glute_max', 'glutes'), ('glute_med', 'glutes'),
    ('rect_fem', 'quads'), ('vast_lat', 'quads'), ('vast_med', 'quads'),
    ('adductors', None),
    ('hams_lateral', 'hamstrings'), ('hams_medial', 'hamstrings'),
    ('calf_gastroc', 'calves'), ('calf_soleus', 'calves'),
    ('tibialis_anterior', None),
)
# Per-node provenance tokens (node_values[*].provenance):
#   estimate         the value comes from exercise share rows that are
#                    literature estimates (every row today; the UI prints it
#                    as an estimate)
#   measured         every contributing share row has grade 'measured'
#   parent_fallback  no share reaches this node; the value is the parent's
#                    UNALLOCATED load (a declared session, a muscle-level map
#                    row) spread evenly -- the parent's own ratio
#   none             nothing in the region carries load in the window
# A share row is 'measured' only by its grade token; there is no provenance
# column (pack_health seeds exercise_subregion_map positionally, 056 M2).
SHARE_MEASURED_GRADE = 'measured'
DRIVERS_TOP = 3                    # contributing exercises listed per node

# -- graded fatigue level (owner 2026-09-26 11:27) -------------------------
# Levels are about a PLANNED session, never about whether to train: the agent
# never proposes skipping (owner 11:27, 08-23 soft-cost rule).  They drive:
#   light    -> keep the session, bump prep/warm-up for the hit regions
#               (no question, one line)
#   moderate -> propose ONE package of swaps / set adjustments (always with a
#               "keep as is" option, 11:29)
#   heavy    -> propose a different routine by name (11:30), "keep as is"
#               always offered
#
# Metrics over the session's PRIMARY target regions (muscle-level nodes the
# planned session puts >= PLAN_TARGET_SHARE x dose on), measured just before
# the session starts:
#   max_ratio  = the highest residual/dose among the targets (a region counts
#                with its most fatigued part: lats can be hard while "back" as
#                a whole is only recovering)
#   hit_share  = share of targets at >= LEVEL_HIT (or with an active body
#                signal)
#
#   none      max_ratio <  LEVEL_HIT and no active body signal on a target
#   light     otherwise, unless one of the two below
#   moderate  max_ratio >= LEVEL_MODERATE
#   heavy     max_ratio >= LEVEL_MODERATE and hit_share >= LEVEL_SPREAD and
#             at least LEVEL_SPREAD_MIN regions hit (it is WIDESPREAD, not
#             just deep: swapping two exercises cannot route around it; one
#             hard region alone is always a swap, never a routine change)
#
# BASIS (derived 2026-09-26 from the 09-11 backup, opened read-only and
# scored on a scratch copy; aggregates only, no row copied into the kit or
# the tests).  37 set-bearing sessions 07-15..09-11, map as corrected by 056,
# stabilizer tags as seeded by 056, the constants above; each session scored
# against the targets of its OWN exercise list, state taken just before it:
#   max_ratio   p25 0.54 / p50 0.73 / p75 1.06 / p90 1.61
#               22% of sessions < 0.5, 30% >= 1.0
#   hit_share   p25 0.20 / p50 0.29 / p75 0.40 / p90 0.54
#   -> LEVEL_HIT 0.5: the spec's "recovering" line; ~p22 of max_ratio, so
#      about one session in five starts fully fresh on its targets (silence).
#   -> LEVEL_MODERATE 1.0: the spec's "recovering_hard" line; ~p72 of
#      max_ratio, so ~30% of sessions get a proposal (moderate or heavy) --
#      the overlap-only question budget of the spec (0 typical, 1 on overlap).
#   -> LEVEL_SPREAD 0.5: ~p85 of hit_share; with LEVEL_SPREAD_MIN 2 it needs
#      a hard region AND at least half the session's regions still loaded.
# Resulting split on the backup: none 8 / light 18 / moderate 5 / heavy 6.
# The 6 heavy sessions are the 07-17 double day (15:03 + 22:40) and its
# follow-ups 07-18 / 07-20, and 08-19 / 08-21 / 08-22 -- the back/glute streak
# the spec simulation flagged by hand (S1).  08-20 (rear delts only) stays
# moderate: one region, a swap is enough.
# Reproduce: tests/test_muscle_fatigue.py builds a synthetic fixture with the
# same shape (never owner rows) and pins the level of each scenario.
LEVEL_HIT = 0.5
LEVEL_MODERATE = 1.0
LEVEL_SPREAD = 0.5
LEVEL_SPREAD_MIN = 2
LEVELS = ('none', 'light', 'moderate', 'heavy')
LEVEL_ACTION = {'none': 'none', 'light': 'prep_bump',
                'moderate': 'swap_package', 'heavy': 'routine_change'}
LEVEL_LABEL_FALLBACK = {'none': '', 'light': 'a little', 'moderate': 'some',
                        'heavy': 'a lot'}

# session_muscle.role token for a session's main movers (pre-existing Korean
# data token; escaped so this source stays ASCII).
ROLE_MAIN_MOVER = u'\uc8fc\ub3d9'
# workout_types.category token of strength training (pre-existing data token).
# A set-less session of another category (cardio, mobility) carries no muscle
# node in v1: it is reported as not counted, never as a coverage hole.
CATEGORY_STRENGTH = u'\uadfc\ub825'

# A mid-session drafted muscle map is marked by this exercise tag (056 M2:
# a tag, not a column -- pack_health seeds exercise_muscle_map positionally).
DRAFT_TAG = ('map', 'draft')

# -- functional coverage items (spec A2.4) ---------------------------------
FUNCTIONAL_ITEMS = ('unilateral_stance', 'cuff_scapular', 'core_direct',
                    'hip_abductor', 'calf_ankle')

_TS_FORMATS = ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%dT%H:%M:%S',
               '%Y-%m-%dT%H:%M', '%Y-%m-%d')


def parse_ts(val):
    """Local naive datetime from any of the stamp grades this DB stores."""
    if val is None:
        return None
    if isinstance(val, datetime.datetime):
        return val
    s = str(val).strip().rstrip('Z')
    s = re.sub(r'\.\d+$', '', s)
    for fmt in _TS_FORMATS:
        try:
            return datetime.datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError('unreadable timestamp: %r' % (val,))


def fmt_ts(dt):
    return dt.strftime('%Y-%m-%d %H:%M') if dt else None


def _cols(conn, table):
    try:
        return {r[1] for r in conn.execute("PRAGMA table_info(%s)" % table)}
    except sqlite3.Error:
        return set()


def _has_table(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE name=?",
                        (name,)).fetchone() is not None


# === taxonomy ============================================================
class Taxonomy(object):
    """muscle_node / muscle_label as the calculation reads them."""

    def __init__(self, conn, lang='ko'):
        self.nodes = {}
        self.labels = {}
        if _has_table(conn, 'muscle_node'):
            for node, parent, kind, sort in conn.execute(
                    "SELECT node, parent, kind, sort FROM muscle_node"):
                self.nodes[node] = {'parent': parent, 'kind': kind,
                                    'sort': sort}
        if _has_table(conn, 'muscle_label'):
            for node, form, text in conn.execute(
                    "SELECT node, form, text FROM muscle_label WHERE lang=?",
                    (lang,)):
                self.labels[(node, form)] = text
        self.children = {}
        for n, info in self.nodes.items():
            if info['parent']:
                self.children.setdefault(info['parent'], []).append(n)

    def kind(self, node):
        info = self.nodes.get(node)
        return info['kind'] if info else None

    def parent(self, node):
        info = self.nodes.get(node)
        return info['parent'] if info else None

    def muscle_of(self, node):
        """The muscle-level node a node belongs to (itself for a muscle),
        None for a group or a subregion hanging off a group (adductors)."""
        cur, seen = node, set()
        while cur and cur not in seen:
            seen.add(cur)
            k = self.kind(cur)
            if k == 'muscle':
                return cur
            if k is None and cur == node:
                return None
            cur = self.parent(cur)
        return None

    def order(self, node):
        """Body-flow order: root sort, then the node's own sort."""
        root, cur, seen = node, node, set()
        while self.parent(cur) and cur not in seen:
            seen.add(cur)
            cur = self.parent(cur)
            root = cur
        info = self.nodes.get(node, {})
        rinfo = self.nodes.get(root, {})
        return (rinfo.get('sort', 999), info.get('sort', 999), node)

    def label(self, node):
        return (self.labels.get((node, 'long'))
                or self.labels.get((node, 'short')) or node)

    def _size(self, node):
        if node in SMALL:
            return 'small'
        k = self.kind(node)
        if k == 'subregion':
            p = self.parent(node)
            if p and self.kind(p) == 'muscle':
                return 'share'
            return 'small'
        if node in MAJOR:
            return 'major'
        return 'minor'

    def hl(self, node):
        size = self._size(node)
        if size == 'share':
            return self.hl(self.parent(node))
        return {'major': HL_MAJOR, 'minor': HL_MINOR}.get(size, HL_SMALL)

    def dose(self, node):
        """Effective sets that make roughly one hard session on this node.
        A subregion of a muscle gets its share of the parent's dose (split
        over the parent's non-small subregions), so an evenly split exercise
        shows the same ratio on the parts as on the whole.  A sub-region in
        ANATOMICAL_SHARE gets that share instead, so a load in the heads'
        natural proportion shows the parent's ratio on every head."""
        size = self._size(node)
        if size == 'major':
            return DOSE_MAJOR
        if size == 'minor':
            return DOSE_MINOR
        if size == 'small':
            return DOSE_SMALL
        p = self.parent(node)
        if node in ANATOMICAL_SHARE:
            return self.dose(p) * ANATOMICAL_SHARE[node]
        sibs = [c for c in self.children.get(p, [])
                if self.kind(c) == 'subregion' and c not in SMALL]
        return self.dose(p) / max(1, len(sibs))

    def subregions(self, muscle):
        return sorted((c for c in self.children.get(muscle, [])
                       if self.kind(c) == 'subregion'), key=self.order)


# === exercise credit =====================================================
def _cost_heuristic(pattern, load_type, movement):
    """Fallback cost for an exercise with no exercise_fatigue_cost row.
    Supported/isolated -> low, free hinge -> high, anything else med
    (a coarse cut of rx-calc's fatigue_cost_heuristic)."""
    if load_type in ('machine', 'cable') or movement == 'isolation' \
            or pattern == 'isolation':
        return 'low'
    if pattern == 'hinge':
        return 'high'
    return 'med'


class Library(object):
    """Per-exercise node credit, cost and provenance."""

    def __init__(self, conn, tax):
        self.tax = tax
        self.muscle = {}      # ex -> [(muscle, role)]
        self.sub = {}         # ex -> [(node, weight)]
        self.sub_grade = {}   # (ex, node) -> grade token of the share row
        self.cost = {}        # ex -> (cost, source)
        self.pattern = {}
        self.ax = {}          # ex -> (movement, load_type)
        self.tags = {}        # ex -> {(group, value)}
        if _has_table(conn, 'exercise_muscle_map'):
            for ex, m, role in conn.execute(
                    "SELECT exercise_name, muscle, role FROM exercise_muscle_map"):
                self.muscle.setdefault(ex, []).append((m, role))
        if _has_table(conn, 'exercise_subregion_map'):
            for ex, node, w, g in conn.execute(
                    "SELECT exercise_name, subregion, weight, grade "
                    "FROM exercise_subregion_map"):
                self.sub.setdefault(ex, []).append((node, float(w)))
                self.sub_grade[(ex, node)] = g
        if _has_table(conn, 'exercise_fatigue_cost'):
            for ex, c, src in conn.execute(
                    "SELECT exercise_name, cost, source FROM exercise_fatigue_cost"):
                self.cost[ex] = (c, src)
        if _has_table(conn, 'exercise_pattern'):
            for ex, p in conn.execute(
                    "SELECT exercise_name, pattern FROM exercise_pattern"):
                self.pattern[ex] = p
        if _has_table(conn, 'ax_exercise'):
            for ex, mv, lt in conn.execute(
                    "SELECT exercise_name, movement, load_type FROM ax_exercise"):
                self.ax[ex] = (mv, lt)
        if _has_table(conn, 'ax_exercise_tag'):
            for ex, g, v in conn.execute(
                    "SELECT e.exercise_name, t.tag_group, t.value "
                    "FROM ax_exercise_tag et "
                    "JOIN ax_exercise e ON e.ulid = et.exercise_ref "
                    "JOIN ax_tag t ON t.ulid = et.tag_ref "
                    "WHERE t.status = 'active'"):
                self.tags.setdefault(ex, set()).add((g, v))
        self._credit = {}

    def names(self):
        return set(self.muscle) | set(self.sub)

    def mapped(self, ex):
        return ex in self.muscle or ex in self.sub

    def map_source(self, ex):
        if DRAFT_TAG in self.tags.get(ex, ()):
            return 'draft'
        return 'explicit' if self.mapped(ex) else None

    def cost_of(self, ex):
        if ex in self.cost:
            return self.cost[ex]
        mv, lt = self.ax.get(ex, (None, None))
        return _cost_heuristic(self.pattern.get(ex), lt, mv), 'heuristic'

    def credit(self, ex):
        """{node: weight} for one working set of `ex` (before cost).

        The subregion map is the precise source: where it names any node at or
        below a muscle, it REPLACES that muscle's role credit (its weights
        already fold the role in: a secondary muscle's parts sum to 0.5).  A
        muscle with no subregion rows gets its role weight directly.  A
        `stabilizes` tag credits STAB_W, never more than the map already
        gives."""
        if ex in self._credit:
            return self._credit[ex]
        c = {}
        covered = set()
        for node, w in self.sub.get(ex, []):
            c[node] = c.get(node, 0.0) + w
            m = self.tax.muscle_of(node)
            covered.add(m or node)
        for m, role in self.muscle.get(ex, []):
            if m in covered:
                continue
            c[m] = c.get(m, 0.0) + ROLE_W.get(role, 0.5)
        for g, v in self.tags.get(ex, ()):
            if g != 'stabilizes':
                continue
            # a stabilizer on a muscle whose parts are already credited is
            # judged against the parts' sum
            have = c.get(v, 0.0) + sum(
                c.get(s, 0.0) for s in self.tax.subregions(v))
            if have < STAB_W:
                c[v] = c.get(v, 0.0) + (STAB_W - have)
        self._credit[ex] = c
        return c

    def primary_nodes(self, ex):
        return {m for m, role in self.muscle.get(ex, [])
                if role == 'primary'}


# === sessions ============================================================
class Session(object):
    __slots__ = ('workout_id', 'date', 't', 'lane', 'load', 'by_ex',
                 'unmapped', 'not_counted', 'label')

    def __init__(self, workout_id, date, t, lane, label=None):
        self.workout_id = workout_id
        self.date = date
        self.t = t
        self.lane = lane          # 'sets' | 'declared'
        self.load = {}            # leaf node -> load
        self.by_ex = {}           # leaf node -> {exercise: [sets, load]}
        self.unmapped = []        # exercises with no map (coverage holes)
        self.not_counted = []     # declared non-strength types (v1 limit)
        self.label = label


def _add(sess, node, load, ex, sets):
    if load <= 0:
        return
    sess.load[node] = sess.load.get(node, 0.0) + load
    slot = sess.by_ex.setdefault(node, {}).setdefault(ex, [0.0, 0.0])
    slot[0] += sets
    slot[1] += load


def load_sessions(conn, tax, lib, start, end):
    """Every session whose time falls in [start, end).  The set lane wins over
    the declared lane for a workout that has both."""
    out = []
    has_kind = 'set_kind' in _cols(conn, 'workout_sets')
    rows = conn.execute(
        "SELECT w.id, w.date, s.exercise_name, %s, s.recorded_at "
        "FROM workout_sets s JOIN workouts w ON w.id = s.session_id "
        "WHERE w.date >= ? AND w.date <= ? ORDER BY w.id, s.id"
        % ('s.set_kind' if has_kind else "'working'"),
        ((start - datetime.timedelta(days=1)).strftime('%Y-%m-%d'),
         end.strftime('%Y-%m-%d'))).fetchall()
    by_w = {}
    for wid, date, ex, kind, rec in rows:
        by_w.setdefault(wid, {'date': date, 'sets': []})['sets'].append(
            (ex, kind or 'working', parse_ts(rec)))
    with_sets = set(by_w)
    for wid, info in by_w.items():
        stamps = [s[2] for s in info['sets']]
        t = min(stamps) + (max(stamps) - min(stamps)) / 2
        if not (start <= t < end):
            continue
        sess = Session(wid, info['date'], t, 'sets')
        for ex, kind, _ in info['sets']:
            kw = KIND_W.get(kind, 1.0)
            if not lib.mapped(ex):
                if ex not in sess.unmapped:
                    sess.unmapped.append(ex)
                continue
            cw = COST_W.get(lib.cost_of(ex)[0], 1.0)
            for node, w in lib.credit(ex).items():
                _add(sess, node, kw * w * cw, ex, kw)
        out.append(sess)
    # declared lane
    if 'muscle_node' in _cols(conn, 'workout_types'):
        drows = conn.execute(
            "SELECT w.id, w.date, w.created_at, wt.category, wt.subtype, "
            "wt.muscle_node FROM workouts w "
            "JOIN workout_classifications wc ON wc.workout_id = w.id "
            "JOIN workout_types wt ON wt.id = wc.type_id "
            "WHERE w.date >= ? AND w.date <= ? ORDER BY w.id",
            ((start - datetime.timedelta(days=1)).strftime('%Y-%m-%d'),
             end.strftime('%Y-%m-%d'))).fetchall()
        dec = {}
        for wid, date, created, cat, sub, node in drows:
            if wid in with_sets:
                continue
            d = dec.setdefault(wid, {'date': date, 'created': created,
                                     'nodes': [], 'unmapped': [],
                                     'not_counted': []})
            if node:
                d['nodes'].append(node)
            elif cat == CATEGORY_STRENGTH:
                d['unmapped'].append('%s/%s' % (cat, sub))
            else:
                d['not_counted'].append('%s/%s' % (cat, sub))
        for wid, d in dec.items():
            created = parse_ts(d['created']) if d['created'] else None
            if created and created.strftime('%Y-%m-%d') == d['date']:
                t = created
            else:
                t = parse_ts(d['date']) + datetime.timedelta(hours=12)
            if not (start <= t < end):
                continue
            sess = Session(wid, d['date'], t, 'declared')
            sess.unmapped = d['unmapped']
            sess.not_counted = d['not_counted']
            for node in d['nodes']:
                if tax.kind(node) == 'group':
                    for child in tax.children.get(node, []):
                        if tax.kind(child) == 'muscle':
                            _add(sess, child,
                                 DECLARED_GROUP_FACTOR * tax.dose(child),
                                 '(declared:%s)' % node, 0)
                else:
                    _add(sess, node, DECLARED_MUSCLE_FACTOR * tax.dose(node),
                         '(declared:%s)' % node, 0)
            out.append(sess)
    out.sort(key=lambda s: s.t)
    return out


# === state ===============================================================
def _decay(load, hours, hl):
    return load * (0.5 ** (hours / hl))


def _members(tax, node, present):
    """node itself + every descendant that carries load."""
    out = [node]
    stack = list(tax.children.get(node, []))
    while stack:
        c = stack.pop()
        out.append(c)
        stack.extend(tax.children.get(c, []))
    return [n for n in out if n in present]


def residual_at(tax, sessions, node, at, present):
    tot = 0.0
    for s in sessions:
        if s.t >= at:
            continue
        h = (at - s.t).total_seconds() / 3600.0
        for leaf in _members(tax, node, present):
            if leaf in s.load:
                tot += _decay(s.load[leaf], h, tax.hl(leaf))
    return tot


def _state(ratio):
    if ratio >= STATE_HARD:
        return 'recovering_hard'
    if ratio >= STATE_RECOVERING:
        return 'recovering'
    return 'ready'


def _eta(tax, sessions, node, at, present, dose):
    line = STATE_RECOVERING * dose
    if residual_at(tax, sessions, node, at, present) < line:
        return None
    for h in range(1, ETA_SCAN_HOURS + 1):
        t = at + datetime.timedelta(hours=h)
        if residual_at(tax, sessions, node, t, present) < line:
            return t
    return at + datetime.timedelta(hours=ETA_SCAN_HOURS)


def _provenance(tax, sessions, node, at, present, total):
    rows = []
    for s in sessions:
        if s.t >= at:
            continue
        h = (at - s.t).total_seconds() / 3600.0
        contrib, exs = 0.0, {}
        for leaf in _members(tax, node, present):
            if leaf not in s.load:
                continue
            f = 0.5 ** (h / tax.hl(leaf))
            contrib += s.load[leaf] * f
            for ex, (sets, ld) in s.by_ex.get(leaf, {}).items():
                e = exs.setdefault(ex, [0.0, 0.0])
                e[0] = max(e[0], sets)
                e[1] += ld * f
        if total > 0 and contrib / total >= PROVENANCE_MIN_SHARE:
            rows.append({
                'workout_id': s.workout_id, 'at': fmt_ts(s.t), 'lane': s.lane,
                'residual': round(contrib, 2),
                'exercises': [{'name': ex, 'sets': round(v[0], 2),
                               'residual': round(v[1], 2)}
                              for ex, v in sorted(exs.items(),
                                                  key=lambda kv: -kv[1][1])]})
    rows.sort(key=lambda r: -r['residual'])
    return rows


def body_signals(conn, tax, at):
    """Active body signals at `at`: stiff/tight/sore for SIGNAL_WINDOW_H after
    observed_at (derived, no expiry write), plus open pain events in the same
    window, which are handed to pain-triage rather than read as fatigue."""
    if not _has_table(conn, 'pain_event') \
            or 'sensation' not in _cols(conn, 'pain_event'):
        return []
    lo = at - datetime.timedelta(hours=SIGNAL_WINDOW_H)
    out = []
    for eid, obs, site, side, sens, pain in conn.execute(
            "SELECT id, observed_at, site, side, sensation, pain "
            "FROM pain_event WHERE status = 'open' AND site IS NOT NULL"):
        t = parse_ts(obs)
        if not (lo <= t <= at):
            continue
        out.append({'id': eid, 'observed_at': fmt_ts(t), 'site': site,
                    'side': side, 'sensation': sens, 'pain': pain,
                    'node': site if site in tax.nodes else None,
                    'pain_gate': sens == 'pain'})
    out.sort(key=lambda r: r['observed_at'])
    return out


def compute_state(conn, at, lang='ko', sessions=None, tax=None, lib=None):
    """The whole per-node picture at `at`."""
    tax = tax or Taxonomy(conn, lang)
    lib = lib or Library(conn, tax)
    if sessions is None:
        sessions = load_sessions(conn, tax, lib,
                                 at - datetime.timedelta(days=LOOKBACK_DAYS),
                                 at)
    present = set()
    for s in sessions:
        present.update(s.load)
    # every node that carries load, plus the muscles above them
    nodes = set(present)
    for n in list(present):
        m = tax.muscle_of(n)
        if m:
            nodes.add(m)
    signals = body_signals(conn, tax, at)
    sig_nodes = {}
    for sg in signals:
        if sg['node'] and not sg['pain_gate']:
            sig_nodes.setdefault(sg['node'], []).append(sg['id'])
            nodes.add(sg['node'])
    rows = {}
    for n in nodes:
        dose = tax.dose(n) if tax.kind(n) != 'group' else None
        if not dose:
            continue
        res = residual_at(tax, sessions, n, at, present)
        ratio = res / dose
        state = _state(ratio)
        if n in sig_nodes and state == 'ready':
            state = 'recovering'          # one step conservative (spec A2.6)
        rows[n] = {
            'node': n, 'label': tax.label(n), 'kind': tax.kind(n) or 'unknown',
            'parent': tax.parent(n), 'residual': round(res, 3),
            'dose': round(dose, 3), 'ratio': round(ratio, 3),
            'pct': int(round(ratio * 100)), 'state': state,
            'hl_hours': tax.hl(n),
            'eta': fmt_ts(_eta(tax, sessions, n, at, present, dose)),
            'signals': sig_nodes.get(n, []),
            'provenance': _provenance(tax, sessions, n, at, present, res),
        }
    holes = []
    for s in sessions:
        for ex in s.unmapped:
            holes.append({'workout_id': s.workout_id, 'date': s.date,
                          'lane': s.lane, 'what': ex})
    not_counted = [{'workout_id': s.workout_id, 'date': s.date, 'what': w}
                   for s in sessions for w in s.not_counted]
    return {'at': fmt_ts(at), 'nodes': rows, 'signals': signals,
            'coverage_holes': holes, 'not_counted': not_counted,
            'sessions': sessions, 'tax': tax, 'lib': lib}


def _hot(row):
    return row['ratio'] >= STATE_RECOVERING or bool(row['signals'])


def display_items(state):
    """The display-grouping rule (owner 11:30 + this phase's brief):
    name the SUB-REGION; name the parent only when several of its
    sub-regions are fatigued together.  The back is shown split
    (trapezius / latissimus / erector spinae).  Groups (legs/arms/core) are never used as
    a display name -- they hide which muscle it is."""
    tax, rows = state['tax'], state['nodes']
    items, used = [], set()
    muscles = sorted({n for n in rows if tax.kind(n) == 'muscle'},
                     key=tax.order)
    for m in muscles:
        hot = [s for s in tax.subregions(m) if s in rows and _hot(rows[s])]
        if len(hot) >= 2:
            it = dict(rows[m])
            it['members'] = hot
            items.append(it)
            used.update([m] + hot)
        elif len(hot) == 1:
            it = dict(rows[hot[0]])
            it['members'] = [hot[0]]
            items.append(it)
            used.update([m, hot[0]])
        elif _hot(rows[m]):
            it = dict(rows[m])
            it['members'] = [m]
            items.append(it)
            used.add(m)
    for n, r in rows.items():            # subregions hanging off a group root
        if n in used or tax.muscle_of(n) is not None or not _hot(r):
            continue
        if tax.kind(n) == 'subregion' or tax.kind(n) is None:
            it = dict(r)
            it['members'] = [n]
            items.append(it)
    ranked = sorted(items, key=lambda r: -r['ratio'])
    top = {id(r) for r in ranked[:DISPLAY_TOP]}
    for r in items:
        r['top'] = id(r) in top
    items.sort(key=lambda r: tax.order(r['node']))
    return items


def _allocated(lib, tax, ex, parent):
    """Does `ex` split its credit under `parent` into the parent's children?
    Declared-lane load and muscle-level credit are unallocated."""
    if ex.startswith('('):
        return False
    return any(k != parent and tax.parent(k) == parent
               for k in lib.credit(ex))


def node_values(state, at=None):
    """{node: value} for every HEATMAP_NODES key (the renderer contract).

    ratio = exercise-derived part (load on the node / the node's dose)
          + parent fallback part (the parent's unallocated load / the
            parent's dose -- evenly spread, the parent's own ratio).
    Parent aggregation (state['nodes']) is not touched: this is a view."""
    tax, lib, sessions = state['tax'], state['lib'], state['sessions']
    at = at or parse_ts(state['at'])
    present = set()
    for s in sessions:
        if s.t < at:
            present.update(s.load)
    out = {}
    for node, parent in HEATMAP_NODES:
        ex_res, fb_res, grades, drivers = 0.0, 0.0, set(), {}
        for s in sessions:
            if s.t >= at:
                continue
            h = (at - s.t).total_seconds() / 3600.0
            if node in s.load:
                f = 0.5 ** (h / tax.hl(node))
                for ex, (_, ld) in s.by_ex.get(node, {}).items():
                    if node == parent and not _allocated(lib, tax, ex, parent):
                        fb_res += ld * f
                        continue
                    ex_res += ld * f
                    drivers[ex] = drivers.get(ex, 0.0) + ld * f
                    grades.add(lib.sub_grade.get((ex, node)))
            if parent and parent != node and parent in s.load:
                f = 0.5 ** (h / tax.hl(parent))
                for ex, (_, ld) in s.by_ex.get(parent, {}).items():
                    if not _allocated(lib, tax, ex, parent):
                        fb_res += ld * f
        r_ex = ex_res / tax.dose(node)
        r_fb = (fb_res / tax.dose(parent or node)) if fb_res else 0.0
        if ex_res > 0:
            prov = ('measured' if grades == {SHARE_MEASURED_GRADE}
                    else 'estimate')
        elif fb_res > 0:
            prov = 'parent_fallback'
        else:
            region = [node] + ([parent] + tax.children.get(parent, [])
                               if parent else [])
            prov = 'estimate' if any(n in present for n in region) else 'none'
        ratio = r_ex + r_fb
        out[node] = {
            'node': node, 'parent': parent, 'label': tax.label(node),
            'ratio': round(ratio, 3), 'pct': int(round(ratio * 100)),
            'state': _state(ratio), 'provenance': prov,
            'ratio_estimate': round(r_ex, 3),
            'ratio_parent_fallback': round(r_fb, 3),
            'drivers': [ex for ex, _ in sorted(drivers.items(),
                                               key=lambda kv: -kv[1])
                        ][:DRIVERS_TOP],
        }
    return out


# === planned session + graded level =====================================
def plan_targets(conn, tax, lib, exercises=None, session_type=None,
                 nodes=None):
    """(primary target muscles, planned load per leaf, unknown exercises).

    exercises: [(name, sets)] -- the most precise input (the routine's list).
    session_type: session_type id or code -> session_muscle regions (the
                  'main mover' role = primary; assisting regions are ignored
                  for the level).
    nodes: explicit node tokens."""
    targets, planned, unknown = set(), {}, []
    if exercises:
        for ex, sets in exercises:
            if not lib.mapped(ex):
                unknown.append(ex)
                continue
            cw = COST_W.get(lib.cost_of(ex)[0], 1.0)
            for node, w in lib.credit(ex).items():
                planned[node] = planned.get(node, 0.0) + sets * w * cw
        per_muscle = {}
        for node, ld in planned.items():
            m = tax.muscle_of(node) or node
            per_muscle[m] = per_muscle.get(m, 0.0) + ld
        for m, ld in per_muscle.items():
            # a target REGION is a muscle; small parts hanging off a group
            # (adductors) are watched by the functional check, not graded
            if tax.nodes and tax.kind(m) != 'muscle':
                continue
            d = tax.dose(m)
            if ld >= PLAN_TARGET_SHARE * d:
                targets.add(m)
    if session_type is not None:
        row = None
        if str(session_type).isdigit():
            row = conn.execute("SELECT id FROM session_type WHERE id=?",
                               (int(session_type),)).fetchone()
        if row is None:
            row = conn.execute("SELECT id FROM session_type WHERE code=? "
                               "ORDER BY id LIMIT 1",
                               (str(session_type),)).fetchone()
        if row is None:
            raise ValueError('unknown session type: %s' % session_type)
        for node, role in conn.execute(
                "SELECT wt.muscle_node, sm.role FROM session_muscle sm "
                "JOIN workout_types wt ON wt.id = sm.type_id "
                "WHERE sm.session_type_id = ?", (row[0],)):
            if not node or role not in (ROLE_MAIN_MOVER, 'primary'):
                continue
            if tax.kind(node) == 'group':
                targets.update(c for c in tax.children.get(node, [])
                               if tax.kind(c) == 'muscle')
            else:
                targets.add(tax.muscle_of(node) or node)
    for n in nodes or ():
        if tax.kind(n) == 'group':
            targets.update(c for c in tax.children.get(n, [])
                           if tax.kind(c) == 'muscle')
        else:
            targets.add(n)
    return targets, planned, unknown


def grade(state, targets):
    """The graded level for a planned session over `targets` (see the
    LEVEL_* block for the rule and its basis)."""
    tax, rows = state['tax'], state['nodes']
    drivers = []
    signal_hit = False
    for m in sorted(targets, key=tax.order):
        cands = [m] + tax.subregions(m)
        best = None
        for c in cands:
            r = rows.get(c)
            if r and (best is None or r['ratio'] > best['ratio']):
                best = r
        sig = [c for c in cands if rows.get(c) and rows[c]['signals']]
        if sig:
            signal_hit = True
        ratio = best['ratio'] if best else 0.0
        drivers.append({'target': m, 'label': tax.label(m),
                        'worst_node': best['node'] if best else None,
                        'worst_label': best['label'] if best else None,
                        'ratio': round(ratio, 3),
                        'pct': int(round(ratio * 100)),
                        'hit': ratio >= LEVEL_HIT or bool(sig),
                        'signal_nodes': sig})
    n = len(drivers)
    max_ratio = max([d['ratio'] for d in drivers] or [0.0])
    hits = sum(1 for d in drivers if d['hit'])
    hit_share = (hits / float(n)) if n else 0.0
    if n == 0 or (max_ratio < LEVEL_HIT and not signal_hit):
        level = 'none'
    elif max_ratio >= LEVEL_MODERATE and hit_share >= LEVEL_SPREAD \
            and hits >= LEVEL_SPREAD_MIN:
        level = 'heavy'
    elif max_ratio >= LEVEL_MODERATE:
        level = 'moderate'
    else:
        level = 'light'
    return {'level': level, 'action': LEVEL_ACTION[level],
            'max_ratio': round(max_ratio, 3),
            'hit_share': round(hit_share, 3), 'targets': drivers,
            'thresholds': {'hit': LEVEL_HIT, 'moderate': LEVEL_MODERATE,
                           'spread': LEVEL_SPREAD,
                           'spread_min': LEVEL_SPREAD_MIN}}


# === week ================================================================
def _week_bounds(spec, today):
    if spec:
        m = re.match(r'^(\d{4})-W(\d{2})$', spec)
        if not m:
            raise ValueError('--week must be YYYY-Www')
        mon = datetime.datetime.strptime('%s-W%s-1' % (m.group(1), m.group(2)),
                                         '%G-W%V-%u')
    else:
        d = today.replace(hour=0, minute=0, second=0, microsecond=0)
        mon = d - datetime.timedelta(days=d.weekday())
    return mon, mon + datetime.timedelta(days=7)


def functional_flags(lib, tax, ex):
    """Which functional items one working set of `ex` counts toward."""
    c = lib.credit(ex)
    tags = lib.tags.get(ex, set())
    pat = lib.pattern.get(ex)
    prim = lib.primary_nodes(ex)
    out = set()
    if ('laterality', 'unilateral') in tags and pat in ('squat', 'hinge'):
        out.add('unilateral_stance')
    if c.get('rear_delt', 0.0) >= 0.5 or \
            (('stabilizes', 'rotator_cuff') in tags and 'shoulders' in prim) \
            or c.get('rotator_cuff', 0.0) >= 0.5:
        out.add('cuff_scapular')
    if pat == 'core':
        out.add('core_direct')
    if c.get('glute_med', 0.0) >= 1.0:
        out.add('hip_abductor')
    calf = sum(v for k, v in c.items()
               if k in ('calves', 'tibialis_anterior')
               or tax.parent(k) == 'calves')
    if calf >= 0.5:
        out.add('calf_ankle')
    return out


# Metrics a FUNCTION strand owns (055 goal_metric_source rows, pack_health
# E14).  A user whose own picture strand claims one of these has set a
# function direction; only then does the weekly functional check speak
# (spec Q2 / owner 11:26: "function check = users who set a function
# direction only").
FUNCTION_METRICS = ('unilateral_share_sets', 'zero_load_control_share_sets')


def function_direction(conn):
    """{'on': bool, 'strands': [label]} -- does the user hold a function-kind
    direction?  Read from the user's OWN strands (metric_axis origin='user',
    role='picture', 055) that claim a FUNCTION_METRICS row.  A seeded axis is
    never read as the user's direction (055: seeded picture rows were one
    person's words).  Before 055 there are no user strands: off."""
    if not (_has_table(conn, 'metric_axis')
            and _has_table(conn, 'goal_metric_source')):
        return {'on': False, 'strands': []}
    cols = _cols(conn, 'metric_axis')
    if 'origin' not in cols or 'role' not in cols:
        return {'on': False, 'strands': []}
    rows = conn.execute(
        "SELECT DISTINCT a.label FROM goal_metric_source g "
        "JOIN metric_axis a ON a.axis_key = g.axis_key "
        "WHERE a.origin = 'user' AND a.role = 'picture' "
        "AND g.metric_token IN (%s) ORDER BY a.axis_rank, a.label"
        % ','.join('?' * len(FUNCTION_METRICS)), FUNCTION_METRICS).fetchall()
    return {'on': bool(rows), 'strands': [r[0] for r in rows]}


def compute_week(conn, week=None, today=None, lang='ko'):
    today = today or datetime.datetime.now()
    mon, nxt = _week_bounds(week, today)
    tax = Taxonomy(conn, lang)
    lib = Library(conn, tax)
    all_sess = load_sessions(conn, tax, lib,
                             mon - datetime.timedelta(days=LOOKBACK_DAYS), nxt)
    week_sess = [s for s in all_sess if mon <= s.t < nxt]
    load = {}
    for s in week_sess:
        for leaf, ld in s.load.items():
            m = tax.muscle_of(leaf) or leaf
            load[m] = load.get(m, 0.0) + ld
    top = sorted(load.items(), key=lambda kv: -kv[1])[:3]
    overlaps = []
    for s in week_sess:
        if s.lane != 'sets':
            continue
        prior = [p for p in all_sess if p.t < s.t]
        present = set()
        for p in prior:
            present.update(p.load)
        per_m = {}
        for leaf, ld in s.load.items():
            m = tax.muscle_of(leaf) or leaf
            per_m[m] = per_m.get(m, 0.0) + ld
        for m, ld in per_m.items():
            if tax.nodes and tax.kind(m) != 'muscle':
                continue                  # regions, as in grade()
            d = tax.dose(m)
            if ld < PLAN_TARGET_SHARE * d:
                continue
            pre = residual_at(tax, prior, m, s.t, present) / d
            if pre >= STATE_RECOVERING:
                overlaps.append({'workout_id': s.workout_id,
                                 'at': fmt_ts(s.t), 'node': m,
                                 'label': tax.label(m),
                                 'pre_ratio': round(pre, 3)})
    cover = dict((k, 0.0) for k in FUNCTIONAL_ITEMS)
    has_sets = conn.execute(
        "SELECT s.exercise_name, %s FROM workout_sets s JOIN workouts w "
        "ON w.id = s.session_id WHERE s.recorded_at >= ? AND s.recorded_at < ?"
        % ("s.set_kind" if 'set_kind' in _cols(conn, 'workout_sets')
           else "'working'"),
        (mon.strftime('%Y-%m-%d %H:%M:%S'),
         nxt.strftime('%Y-%m-%d %H:%M:%S'))).fetchall()
    for ex, kind in has_sets:
        if kind not in ('working', 'hold'):
            continue
        for item in functional_flags(lib, tax, ex):
            cover[item] += 1
    library = dict((k, False) for k in FUNCTIONAL_ITEMS)
    for ex in lib.names():
        for item in functional_flags(lib, tax, ex):
            library[item] = True
    return {
        'week': mon.strftime('%G-W%V'), 'start': fmt_ts(mon),
        'sessions': len(week_sess),
        'declared_sessions': sum(1 for s in week_sess if s.lane == 'declared'),
        'top': [{'node': m, 'label': tax.label(m), 'load': round(v, 2)}
                for m, v in top],
        'overlaps': overlaps,
        'functional': [{'item': k, 'sets': int(cover[k]),
                        'library_has': library[k]} for k in FUNCTIONAL_ITEMS],
        'coverage_holes': sorted({ex for s in week_sess for ex in s.unmapped}),
        'function_direction': function_direction(conn),
    }


# === CLI =================================================================
class Ctx(object):
    """The kit helpers this module borrows (injected by kit_life.py so this
    file imports nothing from it -- no cycle)."""

    def __init__(self, get_conn, err, new_ulid, agent, i18n, lang):
        self.get_conn = get_conn
        self.err = err
        self.new_ulid = new_ulid
        self.agent = agent
        self.i18n = i18n
        self.lang = lang


def _parser(verb):
    p = argparse.ArgumentParser(prog='kit_life.sh %s' % verb, add_help=False)
    p.add_argument('-h', '--help', action='help')
    return p


def _parse_exercises(spec):
    out = []
    for tok in (spec or '').split(','):
        tok = tok.strip()
        if not tok:
            continue
        if ':' in tok:
            name, n = tok.rsplit(':', 1)
            out.append((name.strip(), float(n)))
        else:
            out.append((tok, float(PLAN_DEFAULT_SETS)))
    return out


def _strip(state):
    return dict((k, v) for k, v in state.items()
                if k not in ('sessions', 'tax', 'lib'))


def cli_muscle_state(ctx, argv):
    p = _parser('muscle-state')
    p.add_argument('--at', help="'YYYY-MM-DD HH:MM' local (default now)")
    p.add_argument('--exercises',
                   help='planned session: name[:sets],... (sets default %d)'
                        % PLAN_DEFAULT_SETS)
    p.add_argument('--session-type', dest='session_type',
                   help='planned session: session_type id or code')
    p.add_argument('--nodes', help='planned session: node,node')
    p.add_argument('--all', action='store_true',
                   help='list every loaded node, not only fatigued ones')
    p.add_argument('--json', action='store_true')
    a = p.parse_args(argv)
    at = parse_ts(a.at) if a.at else datetime.datetime.now()
    conn = ctx.get_conn()
    try:
        st = compute_state(conn, at, ctx.lang)
        items = display_items(st)
        plan = None
        if a.exercises or a.session_type or a.nodes:
            try:
                targets, planned, unknown = plan_targets(
                    conn, st['tax'], st['lib'],
                    _parse_exercises(a.exercises), a.session_type,
                    [n.strip() for n in (a.nodes or '').split(',')
                     if n.strip()])
            except ValueError as e:
                ctx.err(str(e))
            plan = grade(st, targets)
            plan['level_label'] = ctx.i18n('fatigue.level.' + plan['level'],
                                           LEVEL_LABEL_FALLBACK[plan['level']])
            plan['unknown_exercises'] = unknown
    finally:
        conn.close()
    if a.json:
        out = _strip(st)
        out['nodes'] = sorted(out['nodes'].values(),
                              key=lambda r: st['tax'].order(r['node']))
        out['display'] = items
        out['node_values'] = node_values(st, at)
        out['plan'] = plan
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return
    print('AT\t%s' % st['at'])
    rows = sorted(st['nodes'].values(), key=lambda r: st['tax'].order(r['node']))
    for r in rows:
        if not a.all and r['state'] == 'ready':
            continue
        prov = '; '.join('%s %s' % (x['at'], ','.join(e['name'] for e in
                                                      x['exercises'][:3]))
                         for x in r['provenance'][:3])
        print('NODE\t%s\t%s\t%d%%\t%s\teta=%s\t%s'
              % (r['node'], r['label'], r['pct'], r['state'],
                 r['eta'] or '-', prov))
    for it in items:
        print('SHOW\t%s\t%s\t%d%%\t%s%s'
              % (it['node'], it['label'], it['pct'],
                 '+'.join(it['members']), '\tTOP' if it['top'] else ''))
    for sg in st['signals']:
        print('SIGNAL\t#%d\t%s\t%s\t%s%s'
              % (sg['id'], sg['site'], sg['sensation'], sg['observed_at'],
                 '\tPAIN_GATE' if sg['pain_gate'] else ''))
    for h in st['coverage_holes']:
        print('COVERAGE_HOLE\t%s\t%s\t%s' % (h['date'], h['lane'], h['what']))
    for h in st['not_counted']:
        print('NOT_COUNTED\t%s\t%s' % (h['date'], h['what']))
    if plan:
        print('LEVEL\t%s\t%s\t%s\tmax=%d%%\tspread=%d%%'
              % (plan['level'], plan['level_label'], plan['action'],
                 int(round(plan['max_ratio'] * 100)),
                 int(round(plan['hit_share'] * 100))))
        for d in plan['targets']:
            print('TARGET\t%s\t%s\t%d%%\t%s%s'
                  % (d['target'], d['worst_label'] or '-', d['pct'],
                     'hit' if d['hit'] else '-',
                     ('\tsignal=' + ','.join(d['signal_nodes']))
                     if d['signal_nodes'] else ''))
        for ex in plan['unknown_exercises']:
            print('COVERAGE_HOLE\tplan\t-\t%s' % ex)


def cli_muscle_week(ctx, argv):
    p = _parser('muscle-week')
    p.add_argument('--week', help='YYYY-Www (default: this week)')
    p.add_argument('--today', help=argparse.SUPPRESS)
    p.add_argument('--json', action='store_true')
    a = p.parse_args(argv)
    conn = ctx.get_conn()
    try:
        try:
            w = compute_week(conn, a.week,
                             parse_ts(a.today) if a.today else None, ctx.lang)
        except ValueError as e:
            ctx.err(str(e))
    finally:
        conn.close()
    if a.json:
        print(json.dumps(w, ensure_ascii=False, indent=1))
        return
    print('WEEK\t%s\tsessions=%d\tdeclared=%d'
          % (w['week'], w['sessions'], w['declared_sessions']))
    for t in w['top']:
        print('TOP\t%s\t%s\t%.1f' % (t['node'], t['label'], t['load']))
    for o in w['overlaps']:
        print('OVERLAP\t%s\t%s\t%d%%' % (o['at'], o['node'],
                                         int(round(o['pre_ratio'] * 100))))
    for f in w['functional']:
        print('FUNCTION\t%s\t%d\t%s'
              % (f['item'], f['sets'],
                 'library' if f['library_has'] else 'NO_EXERCISE'))
    for h in w['coverage_holes']:
        print('COVERAGE_HOLE\t%s' % h)
    print('FUNCTION_DIRECTION\t%s' % ('on' if w['function_direction']['on']
                                      else 'off'))


# -- body signals (pain_event + sensation) ---------------------------------
def _site_token(raw):
    if raw is None:
        return None
    t = re.sub(r'[^a-z0-9]+', '_', raw.strip().lower()).strip('_')
    return t or None


def cli_body_signal_add(ctx, argv):
    p = _parser('body-signal-add')
    p.add_argument('--site', required=True,
                   help='muscle node token preferred (hamstrings); a joint '
                        'token (knee) is kept as free vocabulary')
    p.add_argument('--sensation', required=True, choices=SENSATIONS)
    p.add_argument('--side', choices=['L', 'R', 'both', 'NA'])
    p.add_argument('--pain', type=int, help='0-10; required for pain')
    p.add_argument('--exercise')
    p.add_argument('--note')
    p.add_argument('--ts', help="'YYYY-MM-DD HH:MM' (default now)")
    a = p.parse_args(argv)
    site = _site_token(a.site)
    if not site:
        ctx.err('body-signal-add: --site is empty')
    if a.sensation == 'pain' and a.pain is None:
        ctx.err('body-signal-add: sensation=pain needs --pain 0-10 '
                '(or use pain-add, the pain-triage lane)')
    pain = a.pain if a.pain is not None else 0
    if not 0 <= pain <= 10:
        ctx.err('body-signal-add: pain must be 0-10')
    ts = parse_ts(a.ts) if a.ts else datetime.datetime.now()
    conn = ctx.get_conn()
    try:
        tax = Taxonomy(conn)
        lo = fmt_ts(ts - datetime.timedelta(hours=SIGNAL_REFRESH_H))
        hit = conn.execute(
            "SELECT id FROM pain_event WHERE status='open' AND site=? "
            "AND sensation=? AND observed_at >= ? AND observed_at <= ? "
            "ORDER BY observed_at DESC, id DESC LIMIT 1",
            (site, a.sensation, lo, fmt_ts(ts))).fetchone()
        if hit:
            conn.execute(
                "UPDATE pain_event SET observed_at=?, "
                "side=COALESCE(?, side), pain=MAX(pain, ?), "
                "note=CASE WHEN ? IS NULL THEN note "
                "WHEN note IS NULL THEN ? ELSE note || ' / ' || ? END "
                "WHERE id=?",
                (fmt_ts(ts), a.side, pain, a.note, a.note, a.note, hit[0]))
            conn.commit()
            print('UPDATED\t#%d\t%s\t%s\t%s'
                  % (hit[0], site, a.sensation, fmt_ts(ts)))
            return
        cur = conn.execute(
            "INSERT INTO pain_event (ulid, owning_agent, observed_at, site, "
            "site_origin, side, pain, sensation, exercise_label, note, status, "
            "source, recorded_at) VALUES (?,?,?,?,'stated',?,?,?,?,?,'open',"
            "'utterance',?)",
            (ctx.new_ulid(), ctx.agent(), fmt_ts(ts), site, a.side, pain,
             a.sensation, a.exercise, a.note,
             datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        conn.commit()
        print('ADDED\t#%d\t%s\t%s\t%s'
              % (cur.lastrowid, site, a.sensation, fmt_ts(ts)))
        if site not in tax.nodes:
            print('SITE_NOT_A_MUSCLE\t%s\t(kept as free vocabulary; it will '
                  'not shift a muscle state)' % site)
    finally:
        conn.close()


def cli_body_signal_list(ctx, argv):
    p = _parser('body-signal-list')
    p.add_argument('--days', type=int, default=14)
    p.add_argument('--site')
    p.add_argument('--open', action='store_true')
    p.add_argument('--with-pain', dest='with_pain', action='store_true')
    p.add_argument('--json', action='store_true')
    a = p.parse_args(argv)
    lo = fmt_ts(datetime.datetime.now() - datetime.timedelta(days=a.days))
    conds, args = ['observed_at >= ?'], [lo]
    if not a.with_pain:
        conds.append("sensation <> 'pain'")
    if a.site:
        conds.append('site = ?')
        args.append(_site_token(a.site))
    if a.open:
        conds.append("status = 'open'")
    conn = ctx.get_conn()
    try:
        rows = conn.execute(
            "SELECT id, observed_at, site, side, sensation, pain, status, note "
            "FROM pain_event WHERE %s ORDER BY observed_at DESC, id DESC"
            % ' AND '.join(conds), args).fetchall()
    finally:
        conn.close()
    keys = ('id', 'observed_at', 'site', 'side', 'sensation', 'pain',
            'status', 'note')
    if a.json:
        print(json.dumps([dict(zip(keys, r)) for r in rows],
                         ensure_ascii=False, indent=1))
        return
    if not rows:
        print('NO_SIGNALS')
    for r in rows:
        print('#%d\t%s\t%s\t%s\t%s\t%s%s'
              % (r[0], r[1], r[2], r[4], r[3] or '-', r[6],
                 ('\t' + r[7]) if r[7] else ''))


def _signal_row(ctx, conn, ident):
    row = conn.execute("SELECT id, sensation, status FROM pain_event "
                       "WHERE id=?", (ident,)).fetchone()
    if row is None:
        ctx.err('no body signal #%s' % ident)
    return row


def cli_body_signal_resolve(ctx, argv):
    p = _parser('body-signal-resolve')
    p.add_argument('--id', type=int, required=True)
    a = p.parse_args(argv)
    conn = ctx.get_conn()
    try:
        row = _signal_row(ctx, conn, a.id)
        if row[1] == 'pain':
            ctx.err('#%d is a pain event -- resolve it in the pain lane '
                    '(pain-resolve)' % a.id)
        conn.execute("UPDATE pain_event SET status='resolved' WHERE id=?",
                     (a.id,))
        conn.commit()
        print('RESOLVED\t#%d' % a.id)
    finally:
        conn.close()


def cli_body_signal_del(ctx, argv):
    p = _parser('body-signal-del')
    p.add_argument('--id', type=int, required=True)
    a = p.parse_args(argv)
    conn = ctx.get_conn()
    try:
        row = _signal_row(ctx, conn, a.id)
        if row[1] == 'pain':
            ctx.err('#%d is a pain event -- pain events are evidence and are '
                    'not deleted here' % a.id)
        conn.execute("DELETE FROM pain_event WHERE id=?", (a.id,))
        conn.commit()
        print('DELETED\t#%d' % a.id)
    finally:
        conn.close()


# -- exercise-creation gate (tool layer; 056 trigger is the DB layer) ------
_NAME_RE = re.compile(r'^[a-z][a-z0-9_]*$')


def _draft_muscles(conn, motion, grip):
    """movement_muscle_base + grip_muscle_delta, the same union
    muscle-preset.py --draft computes.  [] when the tables are unseeded."""
    rows = set()
    if motion and _has_table(conn, 'movement_muscle_base'):
        rows |= set(conn.execute(
            "SELECT muscle, role FROM movement_muscle_base WHERE movement=?",
            (motion,)).fetchall())
    if grip and _has_table(conn, 'grip_muscle_delta'):
        rows |= set(conn.execute(
            "SELECT muscle, role FROM grip_muscle_delta WHERE grip=?",
            (grip,)).fetchall())
    best = {}
    for m, role in rows:
        if best.get(m) != 'primary':
            best[m] = role
    return sorted(best.items())


def _tag_ulid(conn, ctx, group, value, agent):
    row = conn.execute("SELECT ulid FROM ax_tag WHERE tag_group=? AND value=? "
                       "AND status='active' ORDER BY created_at LIMIT 1",
                       (group, value)).fetchone()
    if row:
        return row[0]
    u = ctx.new_ulid()
    conn.execute("INSERT INTO ax_tag (ulid, owning_agent, tag_group, value, "
                 "status, created_at) VALUES (?,?,?,?,'active',?)",
                 (u, agent, group, value, _now_z()))
    return u


def _now_z():
    return datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')


NEAR_MATCH_CODE = 5     # kit_life.NEAR_MATCH_CODE (same contract, same code)


def _exercise_near_gate(ctx, conn, name, confirmed):
    """2026-09-28 near-match gate for a NEW exercise: 'lat_pull_down' beside
    'lat_pulldown' is one exercise under two names, and its sets would split
    across both. Same contract as every kit create verb (kit_life NEAR_MATCH
    block, exit 5); --new-confirmed = the owner said different, remembered in
    near_match_refusal. Without the sibling near_match.py the gate is off."""
    try:
        import near_match as nm
    except ImportError:
        return
    try:
        rows = [(n, n, []) for (n,) in conn.execute(
            "SELECT DISTINCT exercise_name FROM ax_exercise "
            "WHERE status='active' ORDER BY exercise_name").fetchall()]
    except sqlite3.Error:
        return
    cands = nm.candidates(name, rows, 'exercise',
                          nm.refused_refs(conn, 'exercise', name))
    if not cands:
        return
    if confirmed:
        if nm.record_refusal(conn, 'exercise', name,
                             [c['ref'] for c in cands],
                             said='--new-confirmed') is not None:
            conn.commit()
        return
    print('NEAR_MATCH\t%d\texercise\t%s' % (len(cands), name))
    for c in cands:
        print('%s\t%s\t%s\t%s' % (c['ref'], c['name'], c['signal'],
                                   c['matched']))
    key = nm.question_key('exercise', len(cands))
    if len(cands) == 1:
        q = ctx.i18n(key, '혹시 {name}{rang} 같은 건가요?').format(
            name=cands[0]['name'], rang='랑')
    else:
        q = ctx.i18n(key, '혹시 {names} 중에 같은 게 있나요?').format(
            names=', '.join(c['name'] for c in cands))
    print('ASK\t%s' % q)
    sys.exit(NEAR_MATCH_CODE)


def cli_exercise_add(ctx, argv):
    p = _parser('exercise-add')
    p.add_argument('--name', required=True, help='ascii snake_case')
    p.add_argument('--movement', choices=['compound', 'isolation'])
    p.add_argument('--measure', default='reps',
                   choices=['reps', 'hold_sec', 'tempo'])
    p.add_argument('--load-type', dest='load_type',
                   choices=['freeweight', 'machine', 'cable', 'bodyweight'])
    p.add_argument('--machine', help='ax_machine ulid (machine/cable)')
    p.add_argument('--hold-sec', dest='hold_sec', type=int)
    p.add_argument('--tempo')
    p.add_argument('--pattern', help='movement pattern (squat / hinge / '
                                     'horizontal_pull / core / isolation ...)')
    p.add_argument('--muscle', action='append', default=[],
                   help='node:primary|secondary (repeatable)')
    p.add_argument('--motion', help='movement_muscle_base key for a DRAFT map '
                                    '(pull_compound, squat, curl ...)')
    p.add_argument('--grip')
    p.add_argument('--cost', choices=['high', 'med', 'low'])
    p.add_argument('--stabilizes', action='append', default=[])
    p.add_argument('--unilateral', action='store_true')
    p.add_argument('--update', action='store_true',
                   help='the exercise exists: add/replace what is given')
    p.add_argument('--new-confirmed', dest='new_confirmed',
                   action='store_true',
                   help='the owner said this is NOT the near-match candidate '
                        '(remembered, never asked again)')
    a = p.parse_args(argv)
    name = a.name.strip()
    if not _NAME_RE.match(name):
        ctx.err('exercise-add: --name must be ascii snake_case: %r' % name)
    agent = ctx.agent()
    conn = ctx.get_conn()
    try:
        tax = Taxonomy(conn)
        exists = conn.execute("SELECT ulid FROM ax_exercise WHERE "
                              "exercise_name=?", (name,)).fetchone()
        if exists and not a.update:
            print('EXISTS 1\t%s\t%s' % (name, exists[0]))
            sys.exit(3)
        if not exists and a.update:
            ctx.err('exercise-add --update: %s is not registered' % name)
        if not exists:
            _exercise_near_gate(ctx, conn, name, a.new_confirmed)
        missing = []
        if not exists:
            for flag, val in (('--movement', a.movement),
                              ('--load-type', a.load_type),
                              ('--pattern', a.pattern)):
                if not val:
                    missing.append(flag)
            if a.load_type in ('machine', 'cable') and not a.machine:
                missing.append('--machine (machine/cable exercises name '
                               'their machine)')
        muscles, source = [], 'explicit'
        for spec in a.muscle:
            if ':' not in spec:
                ctx.err('exercise-add: --muscle takes node:primary|secondary, '
                        'got %r' % spec)
            node, role = spec.split(':', 1)
            if role not in ROLE_W:
                ctx.err('exercise-add: role must be primary|secondary: %r'
                        % spec)
            muscles.append((node.strip(), role))
        if not muscles and a.motion:
            muscles = _draft_muscles(conn, a.motion, a.grip)
            source = 'draft'
        if not exists or muscles:
            if not any(r == 'primary' for _, r in muscles):
                missing.append('--muscle node:primary (or --motion for a '
                               'draft map)')
        if tax.nodes:
            bad = [n for n, _ in muscles if n not in tax.nodes]
            bad += [n for n in a.stabilizes if n not in tax.nodes]
            if bad:
                ctx.err('exercise-add: not in the muscle taxonomy: %s'
                        % ', '.join(bad))
        if missing:
            ctx.err('exercise-add refused -- missing: %s' % ', '.join(missing))
        cost, cost_src = a.cost, 'explicit'
        if not cost and not exists:
            cost = _cost_heuristic(a.pattern, a.load_type, a.movement)
            cost_src = 'heuristic'
        conn.execute('BEGIN IMMEDIATE')
        try:
            if muscles:
                conn.execute("DELETE FROM exercise_muscle_map "
                             "WHERE exercise_name=?", (name,))
                for node, role in muscles:
                    conn.execute("INSERT INTO exercise_muscle_map "
                                 "(exercise_name, muscle, role) VALUES "
                                 "(?,?,?)", (name, node, role))
            if cost:
                conn.execute("INSERT INTO exercise_fatigue_cost "
                             "(exercise_name, cost, source) VALUES (?,?,?) "
                             "ON CONFLICT(exercise_name) DO UPDATE SET "
                             "cost=excluded.cost, source=excluded.source",
                             (name, cost, cost_src))
            if a.pattern:
                conn.execute("INSERT INTO exercise_pattern (exercise_name, "
                             "pattern) VALUES (?,?) ON CONFLICT(exercise_name) "
                             "DO UPDATE SET pattern=excluded.pattern",
                             (name, a.pattern))
            if a.motion:
                conn.execute("INSERT INTO exercise_movement_grip (exercise_name,"
                             " movement, grip) VALUES (?,?,?) ON CONFLICT"
                             "(exercise_name) DO UPDATE SET movement="
                             "excluded.movement, grip=excluded.grip",
                             (name, a.motion, a.grip))
            if exists:
                ulid = exists[0]
            else:
                ulid = ctx.new_ulid()
                now = _now_z()
                conn.execute(
                    "INSERT INTO ax_exercise (ulid, owning_agent, exercise_name,"
                    " movement, measure, load_type, tempo, hold_sec, "
                    "machine_ref, status, created_at, updated_at) VALUES "
                    "(?,?,?,?,?,?,?,?,?,'active',?,?)",
                    (ulid, agent, name, a.movement, a.measure, a.load_type,
                     a.tempo, a.hold_sec, a.machine, now, now))
            tags = [('stabilizes', n) for n in a.stabilizes]
            if a.unilateral:
                tags.append(('laterality', 'unilateral'))
            if muscles and source == 'draft':
                tags.append(DRAFT_TAG)
            elif muscles:
                _untag(conn, ulid, DRAFT_TAG)     # an explicit map replaces it
            for g, v in tags:
                t = _tag_ulid(conn, ctx, g, v, agent)
                if not conn.execute("SELECT 1 FROM ax_exercise_tag WHERE "
                                    "exercise_ref=? AND tag_ref=?",
                                    (ulid, t)).fetchone():
                    conn.execute("INSERT INTO ax_exercise_tag (ulid, "
                                 "owning_agent, exercise_ref, tag_ref, "
                                 "created_at) VALUES (?,?,?,?,?)",
                                 (ctx.new_ulid(), agent, ulid, t, _now_z()))
            conn.commit()
        except sqlite3.Error as e:
            conn.rollback()
            ctx.err('exercise-add refused: %s' % e)
        print('%s\t%s\tmap=%s\tcost=%s(%s)'
              % ('UPDATED' if exists else 'ADDED', name,
                 ','.join('%s:%s' % m for m in muscles) or '(unchanged)',
                 cost or '(unchanged)', cost_src if cost else '-'))
        if source == 'draft' or cost_src == 'heuristic':
            print('DRAFT\t%s\t(confirm once at session end: exercise-confirm)'
                  % name)
    finally:
        conn.close()


def _untag(conn, exercise_ulid, tag):
    return conn.execute(
        "DELETE FROM ax_exercise_tag WHERE exercise_ref=? AND tag_ref IN "
        "(SELECT ulid FROM ax_tag WHERE tag_group=? AND value=?)",
        (exercise_ulid, tag[0], tag[1])).rowcount


def _drafts(conn, since_z=None):
    rows = conn.execute(
        "SELECT e.exercise_name, e.created_at, "
        "EXISTS (SELECT 1 FROM ax_exercise_tag x JOIN ax_tag t ON t.ulid = "
        "x.tag_ref WHERE x.exercise_ref = e.ulid AND t.tag_group = ? AND "
        "t.value = ?), "
        "EXISTS (SELECT 1 FROM exercise_fatigue_cost c WHERE c.exercise_name = "
        "e.exercise_name AND c.source = 'heuristic') "
        "FROM ax_exercise e ORDER BY 1", DRAFT_TAG).fetchall()
    return [(n, created, bool(m), bool(c)) for n, created, m, c in rows
            if (m or c) and (since_z is None or (created or '') >= since_z)]


def _local_to_z(ts):
    """'YYYY-MM-DD HH:MM[:SS]' local -> the UTC 'Z' stamp ax_exercise keeps."""
    dt = parse_ts(ts)
    if dt is None:
        raise ValueError('bad time: %r' % ts)
    return datetime.datetime.utcfromtimestamp(
        time.mktime(dt.timetuple())).strftime('%Y-%m-%dT%H:%M:%SZ')


def cli_exercise_drafts(ctx, argv):
    p = _parser('exercise-drafts')
    p.add_argument('--since', help="only exercises created at or after this "
                                   "local time 'YYYY-MM-DD HH:MM' (a session's "
                                   "start: the ones made mid-session)")
    p.add_argument('--json', action='store_true')
    a = p.parse_args(argv)
    since_z = None
    if a.since:
        try:
            since_z = _local_to_z(a.since)
        except ValueError as e:
            ctx.err('exercise-drafts: %s' % e)
    conn = ctx.get_conn()
    try:
        tax = Taxonomy(conn, ctx.lang)
        out = []
        for name, created, map_d, cost_d in _drafts(conn, since_z):
            musc = conn.execute("SELECT muscle, role FROM exercise_muscle_map "
                                "WHERE exercise_name=? ORDER BY role, muscle",
                                (name,)).fetchall()
            cost = conn.execute("SELECT cost FROM exercise_fatigue_cost WHERE "
                                "exercise_name=?", (name,)).fetchone()
            out.append({'name': name, 'created_at': created,
                        'map_draft': map_d, 'cost_draft': cost_d,
                        'muscles': [{'node': m, 'label': tax.label(m),
                                     'role': r} for m, r in musc],
                        'cost': cost[0] if cost else None})
    finally:
        conn.close()
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return
    if not out:
        print('NO_DRAFTS')
    for d in out:
        print('DRAFT\t%s\t%s\t%s'
              % (d['name'], ','.join('%s:%s' % (m['label'], m['role'])
                                     for m in d['muscles']), d['cost'] or '-'))


def cli_exercise_confirm(ctx, argv):
    p = _parser('exercise-confirm')
    p.add_argument('--name', required=True)
    a = p.parse_args(argv)
    conn = ctx.get_conn()
    try:
        ex = conn.execute("SELECT ulid FROM ax_exercise WHERE exercise_name=?",
                          (a.name,)).fetchone()
        if ex is None:
            ctx.err('exercise-confirm: %s is not registered' % a.name)
        n1 = _untag(conn, ex[0], DRAFT_TAG)
        n2 = conn.execute("UPDATE exercise_fatigue_cost SET source='explicit' "
                          "WHERE exercise_name=? AND source='heuristic'",
                          (a.name,)).rowcount
        conn.commit()
    finally:
        conn.close()
    print('CONFIRMED\t%s\tmap=%d\tcost=%d' % (a.name, n1, n2))


def cli_exercise_tag_del(ctx, argv):
    p = _parser('exercise-tag-del')
    p.add_argument('--name', required=True)
    p.add_argument('--group', required=True)
    p.add_argument('--value', required=True)
    a = p.parse_args(argv)
    conn = ctx.get_conn()
    try:
        n = conn.execute(
            "DELETE FROM ax_exercise_tag WHERE exercise_ref IN (SELECT ulid "
            "FROM ax_exercise WHERE exercise_name=?) AND tag_ref IN (SELECT "
            "ulid FROM ax_tag WHERE tag_group=? AND value=?)",
            (a.name, a.group, a.value)).rowcount
        conn.commit()
    finally:
        conn.close()
    print('UNTAGGED\t%s\t%s=%s\t%d' % (a.name, a.group, a.value, n))


def cli_workout_unclassify(ctx, argv):
    if len(argv) < 3 or not str(argv[0]).isdigit():
        ctx.err('usage: kit_life.sh workout-unclassify <workout_id> '
                '<category> <subtype>')
    wid, cat, sub = int(argv[0]), argv[1], argv[2]
    conn = ctx.get_conn()
    try:
        n = conn.execute(
            "DELETE FROM workout_classifications WHERE workout_id=? AND "
            "type_id IN (SELECT id FROM workout_types WHERE category=? AND "
            "subtype=?)", (wid, cat, sub)).rowcount
        conn.commit()
    finally:
        conn.close()
    if n == 0:
        ctx.err('workout-unclassify: workout %d has no %s/%s' % (wid, cat, sub))
    print('UNCLASSIFIED\t%d\t%s\t%s' % (wid, cat, sub))


VERBS = {
    'muscle-state': cli_muscle_state,
    'muscle-week': cli_muscle_week,
    'body-signal-add': cli_body_signal_add,
    'body-signal-list': cli_body_signal_list,
    'body-signal-resolve': cli_body_signal_resolve,
    'body-signal-del': cli_body_signal_del,
    'exercise-add': cli_exercise_add,
    'exercise-drafts': cli_exercise_drafts,
    'exercise-confirm': cli_exercise_confirm,
    'exercise-tag-del': cli_exercise_tag_del,
    'workout-unclassify': cli_workout_unclassify,
}

