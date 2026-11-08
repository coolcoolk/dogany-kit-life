"""Read the health pack's existing week projection for the life pin.

The kit owns occurrence dates/completion; the pack owns session names. No
program parsing or rotation policy is duplicated here. Missing names stay
unknown, counted in day_pin_data rather than replaced with a guessed mark.
"""
import datetime as dt
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys


def projection(conn, root, day):
    """Reuse workbench.program_state/week_cells, without rendering/publishing."""
    directory = Path(root) / 'service/health'
    program = Path(root) / 'files/program/program.yaml'
    if not program.is_file():
        return {}
    old_path = sys.path[:]
    try:
        sys.path.insert(0, str(directory))
        spec = importlib.util.spec_from_file_location('_pin_health_workbench', directory / 'workbench.py')
        workbench = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(workbench)
        state = json.loads((program.parent / '.program-plan.json').read_text())
        # Do not accept stale plan-state labels if the confirmed YAML cannot
        # be read. program_state supplies only the pack-parsed YAML rotation.
        state = dict(state, structure=dict(state.get('structure') or {}, rotation=[]))
        state = workbench.program_state(state, program)
        rotation = (state.get('structure') or {}).get('rotation') or []
        if not rotation:
            return {}
        monday = day - dt.timedelta(days=day.weekday())
        done = {}
        for wid, date, code, category, sub, note, minutes in workbench.week_workouts(
                conn, monday.isoformat(), (monday + dt.timedelta(days=6)).isoformat()):
            done.setdefault(date, []).append((code, category, wid, minutes, sub))
        cells, _ = workbench.week_cells(conn, state, day, done)
        return {monday + dt.timedelta(days=i): cells.get(key)
                for i, key in enumerate(workbench.UL.WEEKDAY_ORDER)
                if cells.get(key) in rotation}
    finally:
        sys.path[:] = old_path


def week(K, conn, day, tz, start, end):
    """({date: {'session': name or None, 'done': bool}}, unknown day count)."""
    owner = K._health_agent_resolve(conn)[0]
    try:
        tasks = conn.execute(
            "SELECT start_at, settled_outcome FROM timeblock "
            "WHERE kind='task' AND title=? AND owning_agent=? "
            "AND block_class IS NULL AND start_at >= ? AND start_at < ? "
            "AND (settled_at IS NULL OR settled_outcome='done') "
            "ORDER BY start_at, id", ('\ud5ec\uc2a4', owner, start, end)).fetchall()
    except sqlite3.Error:
        tasks = []
    days = {}
    for stamp, outcome in tasks:
        date = K._day_local_date(stamp, tz)
        if date is not None:
            old = days.setdefault(date, {'session': None, 'done': False})
            old['done'] = old['done'] or outcome == 'done'
    if not days:
        return {}, 0
    labels = {}
    _, roots = K._health_crew_members()
    for root in roots:
        conf = K._instance_conf_read(root)
        if conf.get('DOGANY_AGENT_NAME', '').strip().lower() != owner:
            continue
        try:
            labels = projection(conn, root, day)
        except (OSError, ValueError, TypeError, AttributeError, ImportError,
                sqlite3.Error, SystemExit):
            labels = {}
        break
    for date, row in days.items():
        row['session'] = labels.get(date)
    return days, sum(not row['session'] for row in days.values())
