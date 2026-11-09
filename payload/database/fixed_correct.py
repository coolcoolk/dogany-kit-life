"""Resolve and correct an existing recurring fact; never insert a row.

The kit supplies connections, amount validation and locale lookup. Both the
duplicate gate and the correction verb use the same before/after renderer.
"""
import datetime
import json

import ledger_profile
from ledger_review import CYCLE_MONTHS

FIELDS = ('cycle', 'amount', 'next_due', 'name')
COLUMNS = ('id', 'name', 'amount', 'cycle', 'next_due', 'direction')
USAGE = ('Usage: kit_life.sh fixed-correct <name|id> field=value [...] '
         '[--direction expense|income] [--json]; fields: ' + ', '.join(FIELDS))


def tr(K, key, **values):
    return K._i18n('ledger.fixed.correct.' + key).format(**values)


def snapshot(conn, fid):
    row = conn.execute('SELECT ' + ','.join(COLUMNS) + ' FROM fixed_cost WHERE id=?',
                       (fid,)).fetchone()
    return dict(zip(COLUMNS, row)) if row else None


def changes(before, fields):
    return {key: {'before': before[key], 'after': fields[key]}
            for key in FIELDS if key in fields and before[key] != fields[key]}


def display(K, field, value):
    if value in (None, ''):
        return tr(K, 'unset')
    if field == 'cycle' and value in CYCLE_MONTHS:
        return tr(K, 'cycle.' + value)
    if field == 'amount':
        return tr(K, 'amount', n=format(int(value), ','))
    return str(value)


def describe(K, name, diff, completed=False):
    lines = []
    for field, pair in diff.items():
        lines.append(tr(K, ('done.' if completed else 'diff.') + field, name=name,
                        before=display(K, field, pair['before']),
                        after=display(K, field, pair['after'])))
    return '\n'.join(lines) if lines else tr(K, 'unchanged', name=name)


def duplicate(K, conn, fid, fields):
    before = snapshot(conn, fid)
    diff = changes(before, fields)
    return dict(status='exists', id=fid, changes=diff,
                say=describe(K, before['name'], diff),
                correction=['fixed-correct', str(fid)] +
                [key + '=' + (str(pair['after']) if pair['after'] is not None else '')
                 for key, pair in diff.items()])


def resolve(conn, ref, direction=None):
    rows = [dict(zip(COLUMNS, row)) for row in conn.execute(
        'SELECT ' + ','.join(COLUMNS) + ' FROM fixed_cost ORDER BY id')
        if direction is None or row[-1] == direction]
    if str(ref).isdigit():
        return [row for row in rows if row['id'] == int(ref)]
    want = ledger_profile.merchant_key(ref)
    if not want:
        return []
    aliases = ledger_profile.fixed_aliases(conn)
    exact, partial = [], []
    for row in rows:
        keys = [ledger_profile.merchant_key(row['name'])] + aliases.get(row['id'], [])
        if want in keys:
            exact.append(row)
        elif any(want in key for key in keys):
            partial.append(row)
    return exact or partial


def validate(K, fields):
    if not fields or set(fields) - set(FIELDS):
        raise ValueError(USAGE)
    result = dict(fields)
    if 'cycle' in fields and fields['cycle'] not in CYCLE_MONTHS:
        raise ValueError(tr(K, 'invalid_cycle', value=fields['cycle'],
                            allowed=', '.join(CYCLE_MONTHS)))
    if 'amount' in fields:
        result['amount'] = K._strict_amount(str(fields['amount']))
    if 'name' in fields:
        result['name'] = str(fields['name']).strip()
        if not result['name']:
            raise ValueError(tr(K, 'empty_name'))
    if 'next_due' in fields:
        value = fields['next_due']
        if value:
            try:
                date = datetime.date.fromisoformat(value)
                if date.isoformat() != value:
                    raise ValueError()
            except (TypeError, ValueError):
                raise ValueError(tr(K, 'invalid_due', value=value)) from None
        result['next_due'] = value or None
    return result


def correct(K, conn, ref, fields, direction=None):
    fields = validate(K, fields)
    if direction is not None and direction not in K._FIXED_DIRECTIONS:
        raise ValueError(tr(K, 'invalid_direction', value=direction))
    conn.execute('BEGIN IMMEDIATE')
    try:
        candidates = resolve(conn, ref, direction)
        if len(candidates) != 1:
            status = 'ambiguous' if candidates else 'not_found'
            return dict(status=status, candidates=candidates,
                        say=tr(K, status, ref=ref)), 3 if candidates else 2
        before = candidates[0]
        diff = changes(before, fields)
        after = dict(before, **fields)
        if diff:
            conn.execute('UPDATE fixed_cost SET ' + ','.join(key + '=?' for key in fields) +
                         ' WHERE id=?', [*fields.values(), before['id']])
            if 'name' in diff and before['direction']=='expense':
                from ledger_names import set_names
                set_names(conn, dict(items=[dict(name=fields['name'], display_name=fields['name'],
                                                source='owner_said', said=fields['name'])]))
            conn.commit()
        return dict(status='corrected' if diff else 'unchanged', id=before['id'],
                    before=before, after=after, changes=diff,
                    say=describe(K, before['name'], diff, completed=True)), 0
    finally:
        if conn.in_transaction:
            conn.rollback()


def cli(K, argv):
    argv, direction = K._pop_opt(argv, '--direction')
    argv, as_json = K._pop_flag(argv, '--json')
    try:
        if len(argv) < 2 or not argv[0].strip():
            raise ValueError(USAGE)
        fields = {}
        for arg in argv[1:]:
            key, sep, value = arg.partition('=')
            if not sep or key not in FIELDS:
                raise ValueError(USAGE)
            fields[key] = value
        conn = K.get_conn()
        try:
            result, code = correct(K, conn, argv[0], fields, direction)
        finally:
            conn.close()
    except ValueError as exc:
        result, code = dict(status='invalid', error=str(exc), say=str(exc)), 2
    if as_json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(result['say'])
        for row in result.get('candidates', []):
            print('{id}\t{name}\t{direction}\t{amount}\t{cycle}'.format(**row))
    if code:
        raise SystemExit(code)
