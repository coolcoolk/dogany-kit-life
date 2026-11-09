"""Body corrections and field-guarded undo, using existing config history.

The legacy CLI writers open/commit several connections (including their
profile and consultation hooks). During one synchronous CLI invocation a
borrowed connection makes those writes one transaction with the history.
No private store, schema change, delivery, or owner-facing text is added.
"""
import argparse
import datetime
import io
import json
import math
import sqlite3
from contextlib import redirect_stdout

PREFIX = 'body-correction:'
HISTORY = PREFIX + 'history'
TARGET_OWNER = PREFIX + 'target-owner'
PROFILE = frozenset(('weight_kg', 'height_cm', 'age', 'sex', 'skeletal_muscle_kg',
    'fat_mass_kg', 'lean_mass_kg', 'avg_steps', 'other_neat_kcal', 'fat_ratio',
    'goal_mode', 'target_weight_kg', 'diet_bmr_method'))
TARGET_KEYS = ('deficit_kcal', 'surplus_kcal', 'protein_g', 'diet_style',
               'diet_protein_mode', 'diet_protein_override', 'calorie_target_kcal',
               'maintenance_mid', 'diet_target_source')
TARGET_ROWS = ('deficit_kcal', 'surplus_kcal', 'protein_g', 'diet_style', 'protein_mode')
EXISTING = ('log-metric', 'log-body-sheet', 'metric-edit', 'metric-del',
    'pain-add', 'pain-resolve', 'pain-link', 'body-signal-add',
    'body-signal-resolve', 'body-signal-del', 'diet-target-set', 'meal-target-set', 'config-set')
NEW = ('body-undo', 'metric-undo', 'pain-undo', 'body-signal-undo',
    'diet-target-undo', 'body-profile-undo', 'pain-edit', 'pain-del',
    'body-signal-edit', 'body-signal-reopen', 'diet-target-clear',
    'body-profile-set', 'body-profile-clear', 'metric-list')


class Borrowed:
    """Only the outer correction owns commit, rollback and close."""
    def __init__(self, conn):
        self.conn = conn

    def __getattr__(self, key):
        return getattr(self.conn, key)

    @property
    def row_factory(self):
        return self.conn.row_factory

    @row_factory.setter
    def row_factory(self, value):
        self.conn.row_factory = value

    def execute(self, sql, args=()):
        if sql.strip().upper().rstrip(';') in ('BEGIN', 'BEGIN IMMEDIATE'):
            return self.conn.execute('SELECT 1')
        return self.conn.execute(sql, args)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def rows(c, table, where='', args=()):
    cur = c.execute('SELECT * FROM ' + table + (' WHERE ' + where if where else ''), args)
    cols = [x[0] for x in cur.description]
    return [dict(zip(cols, r)) for r in cur]


def snapshot(c):
    # Keep the config's value, not its incidental write clock. A delta owns
    # only fields it changed, so unrelated subsequent edits survive undo.
    return {
        'metric_log': {str(r['id']): r for r in rows(c, 'metric_log')},
        'pain_event': {str(r['id']): r for r in rows(c, 'pain_event')},
        'ledger_constraint': {str(r['id']): r for r in rows(c, 'ledger_constraint', "class='nutrition'")},
        'consult_answer': {str(r['id']): r for r in rows(c, 'consult_answer')},
        'config': {r['key']: {'key': r['key'], 'value': r['value']}
                   for r in rows(c, 'config') if r['key'] != HISTORY and r['value'] is not None},
    }


def changes(before, after):
    delta = []
    for table, old in before.items():
        new = after[table]
        for key in sorted(old.keys() | new.keys(), key=None if table == 'config' else int):
            a, b = old.get(key), new.get(key)
            if a == b:
                continue
            if a is not None and b is not None:
                fields = [k for k in a if a[k] != b[k]]
                # Wall-clock metadata does not make a correction stale.
                meaningful = [k for k in fields if k not in ('updated_at', 'created_at')]
                if not meaningful:
                    continue
                # Stable identity must still match even for field-only edits.
                ident = ('key',) if table == 'config' else (('id', 'ulid') if 'ulid' in a else ('id', 'date', 'metric'))
                fields = list(dict.fromkeys([*ident, *fields]))
                a, b = ({k: a[k] for k in fields}, {k: b[k] for k in fields})
            delta.append(dict(table=table, key=key, before=a, after=b))
    return delta


def history(K, c):
    return json.loads(K._config_stored(c, HISTORY) or '[]')


def family(verb):
    if verb in ('log-metric', 'log-body-sheet') or verb.startswith('metric-'):
        return 'metric'
    if verb.startswith('diet-target-') or verb == 'meal-target-set':
        return 'diet-target'
    return 'body-signal' if verb.startswith('body-signal-') else ('pain' if verb.startswith('pain-') else 'body-profile')


def undo(K, c, verb):
    entries = history(K, c)
    wanted = None if verb == 'body-undo' else verb.removesuffix('-undo')
    idx = next((i for i in range(len(entries)-1, -1, -1)
                if wanted is None or entries[i]['family'] == wanted), None)
    if idx is None:
        raise ValueError('no correction to undo')
    entry = entries[idx]
    current = snapshot(c)
    for d in entry['changes']:
        found, expected = current[d['table']].get(d['key']), d['after']
        if expected is None:
            ok = found is None
        else:
            ok = found is not None and all(found.get(k) == v for k, v in expected.items() if k not in ('created_at', 'updated_at'))
        if not ok:
            raise ValueError('correction changed; undo later edits first')
    for d in reversed(entry['changes']):
        table, old, after = d['table'], d['before'], d['after']
        pk = 'key' if table == 'config' else 'id'
        if old is None:
            if table == 'config' and K._profile_kept(c, d['key']):
                c.execute('UPDATE config SET value=NULL WHERE key=?', (d['key'],))
            else:
                c.execute('DELETE FROM '+table+' WHERE '+pk+'=?', (d['key'],))
        elif after is None and table == 'config':
            K._config_put(c, old['key'], old['value'])
        elif after is None:
            c.execute('INSERT INTO '+table+'('+','.join(old)+') VALUES('+','.join('?' for _ in old)+')', list(old.values()))
        else:
            fields = {k: v for k, v in old.items() if k != pk}
            c.execute('UPDATE '+table+' SET '+','.join(k+'=?' for k in fields)+' WHERE '+pk+'=?', [*fields.values(), d['key']])
    entries.pop(idx)
    K._config_put(c, HISTORY, json.dumps(entries))
    print(json.dumps(dict(verb=entry['verb'], owner_corrected=True, restored=True)))


def metric_delete(K, c, args):
    p = argparse.ArgumentParser(prog='metric-del')
    p.add_argument('--date', required=True)
    p.add_argument('--metric', required=True)
    p.add_argument('--source', choices=('inbody',))
    p.add_argument('--owning-agent')
    a = p.parse_args(args)
    datetime.date.fromisoformat(a.date)
    hit = rows(c, 'metric_log', 'date=? AND metric=?', (a.date, a.metric))
    if not hit or (a.owning_agent and hit[0]['owning_agent'] != a.owning_agent) or (a.source and not (hit[0]['note'] or '').lower().startswith('inbody')):
        raise ValueError('observation not found')
    r = hit[0]
    c.execute('DELETE FROM metric_log WHERE id=?', (r['id'],))
    # Same fallback contract as the existing date-set delete.
    for key, metrics in K._METRIC_DEL_CONFIG.items():
        if a.metric not in metrics:
            continue
        cur = K._config_stored(c, key)
        if cur is None or K._num(cur, None) != r['value']:
            continue
        q = ','.join('?' for _ in metrics)
        if c.execute('SELECT 1 FROM metric_log WHERE metric IN ('+q+') AND date>?', (*metrics, a.date)).fetchone():
            continue
        prev = c.execute('SELECT value FROM metric_log WHERE metric IN ('+q+') ORDER BY date DESC,id DESC LIMIT 1', metrics).fetchone()
        if prev:
            K._config_put(c, key, K._fmeas(prev[0]))
        elif not K._profile_kept(c, key):
            c.execute('DELETE FROM config WHERE key=?', (key,))
    K._refresh_diet_from_body(c)
    print('DELETED\t%s\t1' % a.date)
    print('  ROW\t%s\t%s\t%s\t%s' % (r['metric'], K._metric_echo(r['metric'], r['value']), r['note'] or '-', r['owning_agent']))


def event(K, c, verb, args):
    p = argparse.ArgumentParser(prog=verb)
    p.add_argument('--id', required=True)
    editing = verb.endswith('-edit')
    if editing:
        for name in ('site', 'side', 'exercise', 'note', 'ts', 'rom', 'fault', 'action', 'session', 'guardrail', 'program-change', 'sensation'):
            p.add_argument('--'+name)
        for name in ('pain', 'set-no', 'rir'):
            p.add_argument('--'+name, type=int)
        p.add_argument('--load', type=float)
        p.add_argument('--unlink', action='store_true')
    a = p.parse_args(args)
    r = K._pain_fetch(c, a.id.lstrip('#'))
    if not r:
        raise ValueError('event not found')
    r['sensation'] = rows(c, 'pain_event', 'id=?', (r['id'],))[0]['sensation']
    if (verb.startswith('pain-') and r['sensation'] != 'pain') or (verb.startswith('body-signal-') and r['sensation'] == 'pain'):
        raise ValueError('wrong event lane')
    if verb.endswith('-del'):
        c.execute('DELETE FROM pain_event WHERE id=?', (r['id'],))
    elif verb.endswith('-reopen'):
        c.execute("UPDATE pain_event SET status='open' WHERE id=?", (r['id'],))
    else:
        patch = {}
        mapping = dict(exercise='exercise_label', ts='observed_at', rom='rom_phase', session='session_label', load='load_kg')
        for k, v in vars(a).items():
            if k in ('id', 'unlink') or v is None:
                continue
            patch[mapping.get(k, k)] = v if v != '' else None
        if not patch and not a.unlink:
            raise ValueError('expected correction fields')
        if 'pain' in patch and not 0 <= patch['pain'] <= 10:
            raise ValueError('pain must be 0-10')
        if 'side' in patch and patch['side'] not in ('L', 'R', 'both', 'NA', None):
            raise ValueError('invalid side')
        if 'sensation' in patch and (patch['sensation'] not in ('stiff', 'tight', 'sore') or verb.startswith('pain-')):
            raise ValueError('sensation correction must stay in the body-signal lane')
        if 'observed_at' in patch:
            datetime.datetime.strptime(patch['observed_at'], '%Y-%m-%d %H:%M')
        if 'load_kg' in patch and patch['load_kg'] is not None and (not math.isfinite(patch['load_kg']) or patch['load_kg'] < 0):
            raise ValueError('invalid load')
        if 'site' in patch:
            patch['site'] = K._pain_site_token(patch['site'])
            patch['site_origin'] = 'stated' if patch['site'] else None
        # Correcting joined facts must not silently retain a different set's
        # exercise/load. The caller explicitly unlinks, or uses pain-link.
        if r['set_ref'] and set(patch) & {'exercise_label', 'load_kg', 'set_no', 'rir', 'observed_at', 'session_label'} and not a.unlink:
            raise ValueError('linked facts require --unlink or pain-link')
        if a.unlink:
            patch.update(session_ref=None, set_ref=None)
        c.execute('UPDATE pain_event SET '+','.join(k+'=?' for k in patch)+' WHERE id=?', [*patch.values(), r['id']])
    print(json.dumps(dict(id=r['id'], owner_corrected=True)))
    if not verb.endswith('-del'):
        print(K._pain_row(K._pain_fetch(c, r['id'])))


def profile(K, c, verb, args):
    if not args:
        raise ValueError('expected profile fields')
    for arg in args:
        key, sep, value = arg.partition('=')
        if key not in PROFILE or (verb.endswith('-set') != bool(sep)):
            raise ValueError('invalid profile field: '+key)
        if sep:
            if key in PROFILE - {'sex', 'goal_mode', 'diet_bmr_method'}:
                K._metric_input_value(value)
            K._config_put(c, key, value)
        else:
            if K._profile_kept(c, key):
                c.execute('UPDATE config SET value=NULL WHERE key=?', (key,))
            else:
                c.execute('DELETE FROM config WHERE key=?', (key,))
    K._refresh_diet_from_body(c)
    print(json.dumps(dict(owner_corrected=True, fields=args)))


def run(K, original, verb, args):
    if verb == 'config-set' and not all(a.partition('=')[0].strip() in PROFILE | set(TARGET_KEYS) for a in args):
        return original(args)
    if verb == 'metric-list':
        p = argparse.ArgumentParser(prog=verb)
        p.add_argument('--date')
        p.add_argument('--metric')
        a = p.parse_args(args)
        c = K.get_conn()
        try:
            data = rows(c, 'metric_log', '(? IS NULL OR date=?) AND (? IS NULL OR metric=?)', (a.date, a.date, a.metric, a.metric))
            print(json.dumps(sorted(data, key=lambda r: (r['date'], r['metric'])), ensure_ascii=False))
        finally:
            c.close()
        return
    c = K.get_conn()
    get_conn = K.get_conn
    output = io.StringIO()
    try:
        c.execute('BEGIN IMMEDIATE')
        if verb in ('log-metric', 'log-body-sheet'):
            # Domain handover is shared identity maintenance, not a body
            # correction. It must not be partly reversed by body undo.
            K._health_agent_write(c)
        before = snapshot(c)
        K.get_conn = lambda: Borrowed(c)
        with redirect_stdout(output):
            if verb.endswith('-undo'):
                if args:
                    raise ValueError('expected no arguments')
                undo(K, c, verb)
            else:
                if verb == 'metric-del' and '--metric' in args:
                    metric_delete(K, c, args)
                elif verb in ('pain-edit', 'pain-del', 'body-signal-edit', 'body-signal-reopen'):
                    event(K, c, verb, args)
                elif verb.startswith('body-profile-'):
                    profile(K, c, verb, args)
                elif verb == 'diet-target-clear':
                    if args:
                        raise ValueError('expected no arguments')
                    for key in TARGET_KEYS:
                        c.execute('DELETE FROM config WHERE key=?', (key,))
                    c.execute("UPDATE ledger_constraint SET status='retired' WHERE class='nutrition' AND key IN ("+','.join('?' for _ in TARGET_ROWS)+')', TARGET_ROWS)
                    print(json.dumps(dict(owner_corrected=True, cleared=True)))
                else:
                    if verb == 'config-set' and any(a.partition('=')[0].strip() in TARGET_KEYS for a in args):
                        K._config_put(c, TARGET_OWNER, '1')
                    if verb == 'meal-target-set':
                        c.execute('DELETE FROM config WHERE key=?', (TARGET_OWNER,))
                    original(args)
                    if verb == 'diet-target-set' and K._settled_diet_base(c) is not None:
                        maintenance = float(K._config_stored(c, 'maintenance_mid'))
                        target = maintenance - float(K._config_stored(c, 'deficit_kcal') or 0) + float(K._config_stored(c, 'surplus_kcal') or 0)
                        if target <= 0:
                            raise ValueError('target must be positive')
                        K._config_put(c, 'calorie_target_kcal', target)
                if verb in ('diet-target-set', 'diet-target-clear', 'meal-target-set'):
                    K._config_put(c, TARGET_OWNER, '1')
                delta = changes(before, snapshot(c))
                if delta:
                    entries = history(K, c)
                    entries.append(dict(verb=verb, family=family(verb), owner_corrected=True, changes=delta))
                    K._config_put(c, HISTORY, json.dumps(entries))
        c.commit()
    except BaseException:
        c.rollback()
        # Preserve the legacy atomic-sheet rejection counters. Never expose
        # buffered success output from a transaction that rolled back.
        for line in output.getvalue().splitlines():
            if line.startswith('BODY-SHEET written 0 / rejected '):
                print(line)
        raise
    finally:
        K.get_conn = get_conn
        c.close()
    print(output.getvalue(), end='')


def install(K):
    for verb in (*EXISTING, *NEW):
        original = K._DISPATCH.get(verb)
        def cli(args, verb=verb, original=original):
            try:
                return run(K, original, verb, args)
            except (ValueError, TypeError, KeyError, sqlite3.IntegrityError) as exc:
                K._err(str(exc))
        K._DISPATCH[verb] = cli
