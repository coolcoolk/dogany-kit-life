"""Typed ledger corrections, with owner precedence and guarded undo.

History lives in ledger_preference, like other ledger facts. Callers own the
transaction. An undo refuses stale state instead of erasing a later edit.
"""
import datetime
import json

import ledger_diag as D
import ledger_review as R

CLASSES = frozenset(('spend', 'income', 'saving', 'saving_back', 'loan',
                     'own_transfer', 'topup', 'card_bill', 'pass_through', 'meal_point'))
SPEND_COLUMNS = 'id,class,kind,amount,scope,pair_id,stream_id,is_fixed'


def history(c, key):
    r = c.execute("SELECT detail FROM ledger_preference WHERE ulid=?", ('ledger-correction:'+key,)).fetchone()
    return json.loads(r[0])['entries'] if r else []


def store(c, key, entries):
    c.execute("INSERT INTO ledger_preference(ulid,item,polarity,status,source,recorded_at,topic,detail) "
              "VALUES(?,?,'negotiable','active','owner',?,'ledger_correction',?) "
              "ON CONFLICT(ulid) DO UPDATE SET detail=excluded.detail",
              ('ledger-correction:'+key, key, datetime.datetime.now().isoformat(), json.dumps(dict(entries=entries))))


def save(c, key, before, after):
    entries = history(c, key)
    entries.append(dict(before=before, after=after))
    store(c, key, entries)


def pop(c, key, current):
    entries = history(c, key)
    if not entries:
        raise ValueError('no correction to undo')
    if current != entries[-1]['after']:
        raise ValueError('correction changed; undo later edits first')
    before = entries.pop()['before']
    store(c, key, entries)
    return before


def spend_snapshot(c, ids):
    meta = R.annotations(c)
    result = []
    for sid in sorted(set(ids)):
        found = D.rows(c, 'SELECT '+SPEND_COLUMNS+' FROM spend_entry WHERE id=?', (sid,))
        if not found:
            raise ValueError('ledger row not found')
        result.append(dict(found[0], owner_class=meta.get(sid, {}).get('owner_class')))
    return result


def current_spends(c, expected):
    current = spend_snapshot(c, [r['id'] for r in expected])
    for now, old in zip(current, expected):
        # Stream membership is recomputed by reconcile; it cannot veto an
        # undo of an otherwise unchanged owner classification.
        for key in ('stream_id', 'is_fixed'):
            now[key] = old[key]
    return current


def sync(K, c, sid):
    r = D.rows(c, 'SELECT * FROM spend_entry WHERE id=?', (sid,))[0]
    if r['kind'] == 'transfer':
        K._spend_shadow_del(c, sid)
    else:
        K._spend_write_v32(c, sid, r['date'], r['name'], r['amount'], r['kind'], r['method'])


def restore_spends(K, c, snapshot):
    for r in snapshot:
        values = {k:v for k,v in r.items() if k not in ('id', 'owner_class')}
        c.execute('UPDATE spend_entry SET '+','.join(k+'=?' for k in values)+' WHERE id=?',
                  [*values.values(), r['id']])
        R.put(c, r['id'], dict(owner_class=r['owner_class']))
        sync(K, c, r['id'])


def set_class(K, c, sid, cls):
    if cls not in CLASSES:
        raise ValueError('invalid spend class: '+str(cls))
    r = D.rows(c, 'SELECT * FROM spend_entry WHERE id=?', (sid,))
    if not r:
        return None
    r = r[0]
    partners = D.rows(c, 'SELECT * FROM spend_entry WHERE pair_id=? AND id<>? ORDER BY id', (r['pair_id'], sid)) if r['pair_id'] is not None else []
    # A card settlement and its funding receipt must move together. Other
    # transfer pairs retain their original account direction.
    paired = [p for p in partners if p['amount'] > 0 and p['kind'] in ('income','transfer')]
    card_pair = r['class'] in ('card_bill','pass_through') and cls in ('card_bill','pass_through','spend')
    # Keep the entire pair's identity stable when a later import attaches
    # fresh evidence. Only card funding changes the other leg's kind.
    affected = [sid] + [p['id'] for p in partners]
    before = spend_snapshot(c, affected)
    from ledger_v6 import effect
    outgoing = effect(r)
    kind = ('expense' if cls == 'spend' or cls in ('saving','loan') and outgoing >= 0
            else 'income' if cls == 'income' else 'transfer')
    amount = outgoing if kind == 'expense' else -outgoing
    c.execute('UPDATE spend_entry SET class=?,kind=?,amount=?,stream_id=NULL,is_fixed=0 WHERE id=?', (cls, kind, amount, sid))
    for p in paired if card_pair else []:
        pk, pc = ('transfer','pass_through') if cls=='pass_through' else ('income','income')
        c.execute('UPDATE spend_entry SET kind=?,class=? WHERE id=?', (pk, pc, p['id']))
    for target in affected:
        value = c.execute('SELECT class FROM spend_entry WHERE id=?', (target,)).fetchone()[0]
        R.put(c, target, dict(owner_class=value))
        sync(K, c, target)
    save(c, 'spend-class:'+str(sid), before, spend_snapshot(c, affected))
    return affected


def undo_class(K, c, sid):
    key = 'spend-class:'+str(sid)
    entries = history(c, key)
    expected = entries[-1]['after'] if entries else spend_snapshot(c, [sid])
    restore_spends(K, c, pop(c, key, current_spends(c, expected)))
    return dict(spend_id=sid, restored=True)


def cli(K, argv):
    if len(argv) != 1 or not argv[0].isdigit():
        K._err('usage: spend-class-undo ID')
    c = K.get_conn()
    try:
        c.execute('BEGIN IMMEDIATE')
        result = undo_class(K, c, int(argv[0]))
        c.commit()
        print(json.dumps(result))
    except (ValueError, KeyError) as e:
        c.rollback()
        K._err(str(e))
    finally:
        c.close()
