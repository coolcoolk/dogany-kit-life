# -*- coding: utf-8 -*-
"""training_axis.py -- set-level read/write verbs over the training axis
(kit_life 061..).

WHAT THIS IS
  The kit's side of three health-pack roots that are schema questions, not
  prescription questions:

    061  a set row's SCOPE and what a tap records.  slot_num NULL = the row
         belongs to the session (no program slot); reps NULL on a prep/ramp
         row = done as prescribed, not counted; drill_code = which prep.yaml
         drill a prep row performed, so its OBSERVED load can be read back.

  Verbs (registered in kit_life.py _DISPATCH):
    drill-load-last <drill_code> [--exercise NAME] [--before-session ID]
        the load the owner last USED on a prep drill (observation, never a
        prescription).  "DRILL_LOAD drill=<c> kg=<x> date=<d> session=<id>"
        or "DRILL_LOAD drill=<c> none" when nothing was ever observed --
        the caller then asks once and records (root 3 item 3: 관측하고 기록).

    062  which machine a set was done on (workout_sets.machine_ref).  The
         owner ruled 2026-09-30: open the structure, seed nothing -- the agent
         captures machines in conversation, and a machine's pin grid is
         LEARNED from the loads logged on it.

    machine-register --label TEXT [--gym PLACE_ID] [--cable-lines single|dual]
                     [--loading-side single|per_side|stack]
        a machine the owner just named ("오른쪽 케이블").  provisional=1 until
        its grid is stated.  Same label (spacing/case-insensitive) at the same
        gym -> "MACHINE SAME <ulid>", nothing written.
    machine-list [--gym PLACE_ID] [--for-exercise NAME] [--all]
        the candidates to offer as buttons.  --for-exercise keeps machines
        whose use (sets logged on them, or exercises bound to them) shares that
        exercise's load_type, plus machines never used yet -- derived, there
        is no machine-kind column to drift.
    machine-last EXERCISE [--gym PLACE_ID]
        the remembered choice: the machine of the newest set of EXERCISE that
        named one (basis=logged); else the exercise's bound machine
        (basis=bound); else "none" -- ask.
    machine-grid ULID
        the pin grid: stated (available_plates / lattice_base+step) and
        observed (every distinct load logged on this machine), each point
        tagged with where it came from.
    machine-snap ULID KG [--dir near|down|up]
        KG on that grid.  stated grid first, observed loads otherwise;
        in_range=0 when KG lies outside what was ever observed (the grid
        there is unknown -- the caller must not present it as a pin).
    machine-upd ULID [--label] [--gym] [--plates "5,12.5,..."]
                [--lattice BASE STEP] [--cable-lines] [--loading-side]
                [--retire]
        what the owner states about a machine; a stated grid clears
        provisional.

    063  the muscle vocabulary is bound to muscle_node (triggers), and the
         focus-area -> muscles expansion is READ FROM THE TREE instead of a
         code dict (pack rx-calc FOCUS_AREAS_TO_MUSCLES said arms = biceps +
         triceps and legs = quads + hamstrings + glutes while the tree has
         forearms under arms and calves under legs).

    muscle-focus AREA [AREA ...]
        "FOCUS area=<a> node=<n> muscles=<m,m,..> basis=group|muscle|subregion"
        per area, or "FOCUS area=<a> unknown" -- never guessed.  AREA is a node
        key or a muscle_label text in any language (등 / 팔 / back / arms).
        group -> its descendant nodes of kind 'muscle'; muscle -> itself;
        subregion -> the muscle above it.
    muscle-vocab [--kind group|muscle|subregion]
        "NODE <node> kind=<k> parent=<p|->" per node, in tree sort order: the
        closed vocabulary a reader should take instead of a tuple in code.

  Machine tokens only on stdout.  No owner-facing sentence is composed here:
  the pack renders every question and every number from these lines.

  Imports nothing from kit_life.py (helpers are injected through Ctx), the
  same arrangement as muscle_fatigue.py, so no import cycle and a verb that
  is never called costs nothing.
"""
import argparse
import datetime
import json
import math
import sqlite3


class Ctx(object):
    """The kit helpers this module borrows (injected by kit_life.py)."""

    def __init__(self, get_conn, err, new_ulid, agent):
        self.get_conn = get_conn
        self.err = err
        self.new_ulid = new_ulid
        self.agent = agent


def _parser(verb):
    p = argparse.ArgumentParser(prog='kit_life.sh %s' % verb, add_help=False)
    p.add_argument('-h', '--help', action='help')
    return p


def _has_col(conn, table, col):
    return any(r[1] == col for r in
               conn.execute('PRAGMA table_info(%s)' % table).fetchall())


def _fmt_kg(v):
    """72.5 -> '72.5', 25.0 -> '25' (a load is printed as the owner said it)."""
    f = float(v)
    return ('%d' % f) if f == int(f) else ('%g' % f)


# ---------------------------------------------------------------------------
# 061 -- the observed load of a prep drill
# ---------------------------------------------------------------------------

def drill_load_last(conn, drill_code, exercise=None, before_session=None):
    """The newest prep row for `drill_code` that carries a load ->
    (kg, date, session_id), or None.

    Only rows that SAY a load count: a done-tap with no weight is not an
    observation of zero kilograms.  `exercise` narrows to one host lift (the
    same drill can be done with a different implement before a different
    lift); `before_session` excludes the session being planned so a re-entry
    never reads its own row back as "last time"."""
    if not _has_col(conn, 'workout_sets', 'drill_code'):
        return None                      # pre-061 DB: nothing was ever keyed
    sql = ("SELECT ws.weight_kg, w.date, ws.session_id "
           "FROM workout_sets ws JOIN workouts w ON w.id = ws.session_id "
           "WHERE ws.drill_code = ? AND ws.set_kind = 'prep' "
           "AND ws.weight_kg IS NOT NULL")
    args = [drill_code]
    if exercise:
        sql += " AND ws.exercise_name = ?"
        args.append(exercise)
    if before_session is not None:
        sql += " AND ws.session_id <> ?"
        args.append(int(before_session))
    sql += " ORDER BY w.date DESC, ws.recorded_at DESC, ws.id DESC LIMIT 1"
    row = conn.execute(sql, args).fetchone()
    return tuple(row) if row else None


def cli_drill_load_last(ctx, argv):
    p = _parser('drill-load-last')
    p.add_argument('drill_code')
    p.add_argument('--exercise')
    p.add_argument('--before-session', type=int)
    a = p.parse_args(argv)
    conn = ctx.get_conn()
    try:
        hit = drill_load_last(conn, a.drill_code, a.exercise, a.before_session)
    finally:
        conn.close()
    if hit is None:
        print('DRILL_LOAD drill=%s none' % a.drill_code)
        return 0
    kg, date, sid = hit
    print('DRILL_LOAD drill=%s kg=%s date=%s session=%s'
          % (a.drill_code, _fmt_kg(kg), date, sid))
    return 0


# ---------------------------------------------------------------------------
# 062 -- which machine, and its pin grid
# ---------------------------------------------------------------------------

def _now_z():
    return datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')


def _label_key(label):
    """'오른쪽 케이블' == '오른쪽케이블' == ' 오른쪽  케이블 ': one machine."""
    return ''.join((label or '').split()).casefold()


def _machine(conn, ulid):
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute('SELECT * FROM ax_machine WHERE ulid = ?',
                            (ulid,)).fetchone()
    finally:
        conn.row_factory = None


def _has_machine_col(conn):
    return _has_col(conn, 'workout_sets', 'machine_ref')


def _machine_line(m, extra=''):
    return ('MACHINE %s gym=%s status=%s provisional=%d label=%s%s'
            % (m['ulid'], m['gym_id'] if m['gym_id'] is not None else '-',
               m['status'], m['provisional'], m['label'], extra))


def machine_register(conn, agent, new_ulid, label, gym=None, cable_lines=None,
                     loading_side=None):
    """-> ('SAME'|'NEW', ulid).  Find-first on (gym, label key) among active
    machines; a new row is provisional (its grid has not been stated)."""
    key = _label_key(label)
    if not key:
        raise ValueError('label is empty')
    rows = conn.execute(
        "SELECT ulid, label FROM ax_machine WHERE status = 'active' AND "
        "gym_id IS ?", (gym,)).fetchall()
    for u, lb in rows:
        if _label_key(lb) == key:
            return 'SAME', u
    u = new_ulid()
    now = _now_z()
    conn.execute(
        "INSERT INTO ax_machine (ulid, owning_agent, gym_id, label, "
        "loading_side, cable_lines, status, provisional, created_at, "
        "updated_at) VALUES (?,?,?,?,?,?,'active',1,?,?)",
        (u, agent, gym, label.strip(), loading_side, cable_lines, now, now))
    return 'NEW', u


def _load_types_of(conn, ulid):
    """load_types this machine has served: sets logged on it + exercises bound
    to it.  Empty = never used yet."""
    out = set()
    if _has_machine_col(conn):
        out.update(r[0] for r in conn.execute(
            "SELECT DISTINCT e.load_type FROM workout_sets ws "
            "JOIN ax_exercise e ON e.exercise_name = ws.exercise_name "
            "WHERE ws.machine_ref = ?", (ulid,)))
    out.update(r[0] for r in conn.execute(
        "SELECT DISTINCT load_type FROM ax_exercise WHERE machine_ref = ?",
        (ulid,)))
    return out


def machine_list(conn, gym=None, for_exercise=None, include_retired=False):
    sql = "SELECT * FROM ax_machine WHERE 1=1"
    args = []
    if gym is not None:
        sql += " AND gym_id IS ?"
        args.append(gym)
    if not include_retired:
        sql += " AND status = 'active'"
    sql += " ORDER BY created_at, ulid"
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(sql, args).fetchall()
    finally:
        conn.row_factory = None
    if not for_exercise:
        return [(m, None) for m in rows]
    lt = conn.execute("SELECT load_type FROM ax_exercise WHERE "
                      "exercise_name = ? ORDER BY status = 'active' DESC "
                      "LIMIT 1", (for_exercise,)).fetchone()
    want = lt[0] if lt else None
    out = []
    for m in rows:
        served = _load_types_of(conn, m['ulid'])
        if not served:
            out.append((m, 'unused'))
        elif want is None or want in served:
            out.append((m, 'served'))
    return out


def machine_last(conn, exercise, gym=None):
    """-> (ulid, basis, date) or None.  basis: logged | bound."""
    if _has_machine_col(conn):
        sql = ("SELECT ws.machine_ref, w.date FROM workout_sets ws "
               "JOIN workouts w ON w.id = ws.session_id "
               "JOIN ax_machine m ON m.ulid = ws.machine_ref "
               "WHERE ws.exercise_name = ? AND m.status = 'active'")
        args = [exercise]
        if gym is not None:
            sql += " AND m.gym_id IS ?"
            args.append(gym)
        sql += " ORDER BY w.date DESC, ws.recorded_at DESC, ws.id DESC LIMIT 1"
        row = conn.execute(sql, args).fetchone()
        if row:
            return row[0], 'logged', row[1]
    sql = ("SELECT e.machine_ref FROM ax_exercise e JOIN ax_machine m "
           "ON m.ulid = e.machine_ref WHERE e.exercise_name = ? "
           "AND e.status = 'active' AND m.status = 'active'")
    args = [exercise]
    if gym is not None:
        sql += " AND m.gym_id IS ?"
        args.append(gym)
    row = conn.execute(sql + " LIMIT 1", args).fetchone()
    if row:
        return row[0], 'bound', None
    return None


def _stated_points(m):
    """The grid the owner stated: available_plates (a stack's pin values) or
    nothing.  A lattice is returned separately (it is unbounded)."""
    raw = m['available_plates']
    if not raw:
        return []
    try:
        vals = json.loads(raw)
    except (TypeError, ValueError):
        return []
    out = []
    for v in vals if isinstance(vals, list) else []:
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            pass
    return sorted(set(out))


def machine_grid(conn, ulid):
    """-> dict(stated=[kg], lattice=(base, step)|None, observed=[(kg, n)])."""
    m = _machine(conn, ulid)
    if m is None:
        return None
    lat = None
    if m['lattice_base'] is not None and m['lattice_step'] is not None:
        lat = (float(m['lattice_base']), float(m['lattice_step']))
    observed = []
    if _has_machine_col(conn):
        observed = [(float(kg), n) for kg, n in conn.execute(
            "SELECT weight_kg, COUNT(*) FROM workout_sets "
            "WHERE machine_ref = ? AND weight_kg IS NOT NULL "
            "GROUP BY weight_kg ORDER BY weight_kg", (ulid,))]
    return {'machine': m, 'stated': _stated_points(m), 'lattice': lat,
            'observed': observed}


def _pick(points, kg, direction):
    if direction == 'down':
        c = [p for p in points if p <= kg + 1e-9]
        return max(c) if c else None
    if direction == 'up':
        c = [p for p in points if p >= kg - 1e-9]
        return min(c) if c else None
    return min(points, key=lambda p: (abs(p - kg), p)) if points else None


def machine_snap(conn, ulid, kg, direction='near'):
    """-> (kg or None, basis, in_range).  basis: stated | lattice | observed |
    none.  in_range is about OBSERVATION: 0 when kg lies outside every load
    ever logged on this machine, so a learned grid is never extrapolated."""
    g = machine_grid(conn, ulid)
    if g is None:
        return None
    obs = [p for p, _n in g['observed']]
    in_range = int(bool(obs) and min(obs) - 1e-9 <= kg <= max(obs) + 1e-9)
    if g['stated']:
        return _pick(g['stated'], kg, direction), 'stated', int(
            g['stated'][0] - 1e-9 <= kg <= g['stated'][-1] + 1e-9)
    if g['lattice']:
        base, step = g['lattice']
        k = (kg - base) / step
        k = {'down': math.floor(k + 1e-9),
             'up': math.ceil(k - 1e-9)}.get(direction, math.floor(k + 0.5))
        return round(base + max(k, 0) * step, 4), 'lattice', int(kg >= base - 1e-9)
    if obs:
        return _pick(obs, kg, direction), 'observed', in_range
    return None, 'none', 0


def _gym_arg(v):
    if v is None or v == '':
        return None
    return int(v)


def cli_machine_register(ctx, argv):
    p = _parser('machine-register')
    p.add_argument('--label', required=True)
    p.add_argument('--gym', type=_gym_arg)
    p.add_argument('--cable-lines', choices=['single', 'dual'])
    p.add_argument('--loading-side', choices=['single', 'per_side', 'stack'])
    a = p.parse_args(argv)
    conn = ctx.get_conn()
    try:
        try:
            kind, u = machine_register(conn, ctx.agent(), ctx.new_ulid,
                                       a.label, a.gym, a.cable_lines,
                                       a.loading_side)
        except (ValueError, sqlite3.IntegrityError) as e:
            ctx.err('machine-register: %s' % e)
        conn.commit()
        print('MACHINE %s %s' % (kind, u))
    finally:
        conn.close()
    return 0


def cli_machine_list(ctx, argv):
    p = _parser('machine-list')
    p.add_argument('--gym', type=_gym_arg)
    p.add_argument('--for-exercise')
    p.add_argument('--all', action='store_true')
    a = p.parse_args(argv)
    conn = ctx.get_conn()
    try:
        rows = machine_list(conn, a.gym, a.for_exercise, a.all)
    finally:
        conn.close()
    for m, why in rows:
        print(_machine_line(m, (' match=%s' % why) if why else ''))
    print('MACHINES n=%d' % len(rows))
    return 0


def cli_machine_last(ctx, argv):
    p = _parser('machine-last')
    p.add_argument('exercise')
    p.add_argument('--gym', type=_gym_arg)
    a = p.parse_args(argv)
    conn = ctx.get_conn()
    try:
        hit = machine_last(conn, a.exercise, a.gym)
    finally:
        conn.close()
    if hit is None:
        print('MACHINE_LAST exercise=%s none' % a.exercise)
    else:
        u, basis, date = hit
        print('MACHINE_LAST exercise=%s machine=%s basis=%s%s'
              % (a.exercise, u, basis, (' date=%s' % date) if date else ''))
    return 0


def cli_machine_grid(ctx, argv):
    p = _parser('machine-grid')
    p.add_argument('ulid')
    a = p.parse_args(argv)
    conn = ctx.get_conn()
    try:
        g = machine_grid(conn, a.ulid)
    finally:
        conn.close()
    if g is None:
        ctx.err('machine-grid: no machine %s' % a.ulid)
    lat = g['lattice']
    print('GRID machine=%s stated=%d lattice=%s observed=%d'
          % (a.ulid, len(g['stated']),
             ('%s+%s' % (_fmt_kg(lat[0]), _fmt_kg(lat[1]))) if lat else '-',
             len(g['observed'])))
    for kg in g['stated']:
        print('PIN kg=%s source=stated' % _fmt_kg(kg))
    for kg, n in g['observed']:
        print('PIN kg=%s source=observed n=%d' % (_fmt_kg(kg), n))
    return 0


def cli_machine_snap(ctx, argv):
    p = _parser('machine-snap')
    p.add_argument('ulid')
    p.add_argument('kg', type=float)
    p.add_argument('--dir', choices=['near', 'down', 'up'], default='near')
    a = p.parse_args(argv)
    conn = ctx.get_conn()
    try:
        r = machine_snap(conn, a.ulid, a.kg, a.dir)
    finally:
        conn.close()
    if r is None:
        ctx.err('machine-snap: no machine %s' % a.ulid)
    kg, basis, in_range = r
    print('SNAP machine=%s want=%s kg=%s basis=%s in_range=%d'
          % (a.ulid, _fmt_kg(a.kg), _fmt_kg(kg) if kg is not None else '-',
             basis, in_range))
    return 0


def cli_machine_upd(ctx, argv):
    p = _parser('machine-upd')
    p.add_argument('ulid')
    p.add_argument('--label')
    p.add_argument('--gym', type=_gym_arg)
    p.add_argument('--plates')
    p.add_argument('--lattice', nargs=2, type=float, metavar=('BASE', 'STEP'))
    p.add_argument('--cable-lines', choices=['single', 'dual'])
    p.add_argument('--loading-side', choices=['single', 'per_side', 'stack'])
    p.add_argument('--retire', action='store_true')
    a = p.parse_args(argv)
    sets, args = [], []
    if a.label is not None:
        if not _label_key(a.label):
            ctx.err('machine-upd: label is empty')
        sets.append('label = ?')
        args.append(a.label.strip())
    if a.gym is not None:
        sets.append('gym_id = ?')
        args.append(a.gym)
    if a.plates is not None:
        try:
            pts = sorted(set(float(x) for x in a.plates.replace(
                ',', ' ').split()))
        except ValueError:
            ctx.err('machine-upd: --plates must be numbers: %r' % a.plates)
        sets.append('available_plates = ?')
        args.append(json.dumps([int(x) if x == int(x) else x for x in pts]))
    if a.lattice is not None:
        if a.lattice[1] <= 0:
            ctx.err('machine-upd: lattice step must be > 0')
        sets += ['lattice_base = ?', 'lattice_step = ?']
        args += list(a.lattice)
    if a.cable_lines is not None:
        sets.append('cable_lines = ?')
        args.append(a.cable_lines)
    if a.loading_side is not None:
        sets.append('loading_side = ?')
        args.append(a.loading_side)
    if a.retire:
        sets.append("status = 'retired'")
    if a.plates is not None or a.lattice is not None:
        sets.append('provisional = 0')     # the owner stated the grid
    if not sets:
        ctx.err('machine-upd: nothing to change')
    sets.append('updated_at = ?')
    args += [_now_z(), a.ulid]
    conn = ctx.get_conn()
    try:
        try:
            cur = conn.execute('UPDATE ax_machine SET %s WHERE ulid = ?'
                               % ', '.join(sets), args)
        except sqlite3.IntegrityError as e:
            ctx.err('machine-upd: %s' % e)
        if cur.rowcount == 0:
            ctx.err('machine-upd: no machine %s' % a.ulid)
        conn.commit()
        print(_machine_line(_machine(conn, a.ulid)))
    finally:
        conn.close()
    return 0


# ---------------------------------------------------------------------------
# 063 -- focus areas from the tree
# ---------------------------------------------------------------------------

def _tree(conn):
    return dict((n, (p, k, srt)) for n, p, k, srt in conn.execute(
        'SELECT node, parent, kind, sort FROM muscle_node'))


def resolve_area(conn, area):
    """-> (node, [muscles], basis) or None.  A node key first; else a
    muscle_label text (any language/form) naming exactly one node."""
    tree = _tree(conn)
    node = area if area in tree else None
    if node is None:
        hits = sorted(set(r[0] for r in conn.execute(
            'SELECT node FROM muscle_label WHERE text = ?', (area,))))
        if len(hits) != 1:
            return None
        node = hits[0]
    parent, kind, _s = tree[node]
    if kind == 'muscle':
        return node, [node], 'muscle'
    if kind == 'subregion':
        up = parent
        seen = set()
        while up is not None and up in tree and up not in seen:
            seen.add(up)
            if tree[up][1] == 'muscle':
                return node, [up], 'subregion'
            up = tree[up][0]
        return None
    # group: every descendant of kind 'muscle' (not below another muscle)
    out, stack, seen = [], [node], set()
    while stack:
        cur = stack.pop()
        for n, (p, k, _s) in tree.items():
            if p == cur and n not in seen:
                seen.add(n)
                if k == 'muscle':
                    out.append(n)
                elif k == 'group':
                    stack.append(n)
    out.sort(key=lambda n: (tree[n][2], n))
    return node, out, 'group'


def cli_muscle_focus(ctx, argv):
    p = _parser('muscle-focus')
    p.add_argument('areas', nargs='+')
    a = p.parse_args(argv)
    conn = ctx.get_conn()
    try:
        for area in a.areas:
            r = resolve_area(conn, area)
            if r is None or not r[1]:
                print('FOCUS area=%s unknown' % area)
                continue
            node, muscles, basis = r
            print('FOCUS area=%s node=%s muscles=%s basis=%s'
                  % (area, node, ','.join(muscles), basis))
    finally:
        conn.close()
    return 0


def cli_muscle_vocab(ctx, argv):
    p = _parser('muscle-vocab')
    p.add_argument('--kind', choices=['group', 'muscle', 'subregion'])
    a = p.parse_args(argv)
    conn = ctx.get_conn()
    try:
        sql = 'SELECT node, kind, parent FROM muscle_node'
        args = []
        if a.kind:
            sql += ' WHERE kind = ?'
            args.append(a.kind)
        rows = conn.execute(sql + ' ORDER BY sort, node', args).fetchall()
    finally:
        conn.close()
    for n, k, par in rows:
        print('NODE %s kind=%s parent=%s' % (n, k, par or '-'))
    print('NODES n=%d' % len(rows))
    return 0


VERBS = {
    'drill-load-last': cli_drill_load_last,
    'muscle-focus': cli_muscle_focus,
    'muscle-vocab': cli_muscle_vocab,
    'machine-register': cli_machine_register,
    'machine-list': cli_machine_list,
    'machine-last': cli_machine_last,
    'machine-grid': cli_machine_grid,
    'machine-snap': cli_machine_snap,
    'machine-upd': cli_machine_upd,
}
