"""Typed facts land in existing kit writers; owner words are audit evidence.

fact-route WORDS --data JSON [--kind KIND] [--as-of YYYY-MM-DD] [--json]
The agent chooses kind, identity and values. Without a typed kind the legacy
entry is inert. fact-landed reads anchored facts for a new conversation.
"""
import hashlib
import json
import math
import re
import sqlite3
import sys

VERBS = ('fact-route', 'fact-landed')
KINDS = ('stock', 'ingredient', 'gear', 'schedule', 'ledger', 'dish')
DOMAIN = 'fact'
UNITS = {'kg': ('g',1000), 'g': ('g',1), 'l': ('ml',1000), 'ml': ('ml',1),
         'piece': ('piece',1), 'pack': ('pack',1)}
_ISO = re.compile(r'\d{4}-\d{2}-\d{2}')


class Refused(Exception):
    pass


def classify(text, hint=None):
    if hint is not None and hint not in KINDS:
        raise Refused('kind is one of %s' % ','.join(KINDS))
    return hint


def dish_names(text, explicit=None):
    if explicit is None:
        return []
    names = explicit if isinstance(explicit,list) else explicit.split(',')
    if not names or any(not isinstance(n,str) or not n.strip() for n in names):
        raise Refused('supply a nonempty typed list of dish names')
    return list(dict.fromkeys(n.strip() for n in names))


def _shop(K):
    import shop
    return shop


def _item_for(K, conn, name, category):
    S = _shop(K)
    import pantry_stock
    hit = pantry_stock.resolve(conn, dict(name=name))
    if hit is not None:
        return S._item_get(conn, hit)
    cat = S.cat_resolve(K, conn, category)
    _st, item, _f = S.item_upsert(K, conn, cat, name, near_gate=False)
    return item


def _owned_for(K, conn, item, day):
    S = _shop(K)
    row = conn.execute(
        "SELECT id FROM owned_item WHERE item_id=? AND status='in_use' "
        "ORDER BY id DESC", (item['id'],)).fetchone()
    if row:
        return row[0]
    owned, _lines = S.owned_add(K, conn, item, day, new=True)
    return owned['id']


def _num(x):
    return ('%d' % x) if float(x).is_integer() else ('%g' % x)


def record_dishes(K, conn, names, day, said):
    """One config row per day: the dish set being cooked (latest statement wins)."""
    key = 'cooking.dishes.%s' % day
    K._config_put(conn, key, json.dumps(dict(dishes=list(names), said=said, as_of=day), ensure_ascii=False))
    return key


def dishes_today(K, conn, day=None):
    day = day or _shop(K)._today()
    raw = K._config_stored(conn, 'cooking.dishes.%s' % day)
    return json.loads(raw).get('dishes', []) if raw else []


def anchor(K, conn, kind, text, day, table, ref, summary, identity):
    import consult_anchor as A
    key = 'fact.%s.%s' % (day, hashlib.sha1(
        (kind + '|' + json.dumps(identity, sort_keys=True, ensure_ascii=False)).encode()).hexdigest()[:10])
    K._config_put(conn, key, json.dumps(
        dict(kind=kind, said=text, as_of=day, landed=table + ':' + ref,
             summary=summary), ensure_ascii=False))
    res = A.anchor_put(K, conn, DOMAIN, 'fact.' + kind, 'config', key, text,
                       'chat')
    return key, res


def route(K, conn, text, hint=None, day=None, item=None, *, data=None):
    data = {} if data is None else data
    if not isinstance(data,dict):
        raise Refused('fact data must be an object')
    kind = classify(text, hint or data.get('kind'))
    if kind is None:
        return dict(status='NO_TARGET', said=text,
                    next='Interpret the owner message; supply fact-route WORDS --data with typed kind and values. Read core-log/reference/core.md. Never ask for a stock sentence.')
    day = day or _shop(K)._today()
    name = data.get('item',item)
    if kind in ('stock','ingredient','gear','ledger') and (not isinstance(name,str) or not name.strip()):
        raise Refused('typed item name required')
    if kind in ('stock','ingredient'):
        import pantry_stock
        amount = data.get('quantity');unit = data.get('unit')
        if isinstance(amount,bool) or not isinstance(amount,(int,float)) or not math.isfinite(amount) or amount<=0 or unit not in UNITS:
            raise Refused('typed positive quantity and unit g|kg|ml|l|piece|pack required')
        unit,factor=UNITS[unit];amount*=factor
        product = _item_for(K,conn,name,'food_pantry');oid=_owned_for(K,conn,product,day)
        spec = dict(quantity=1.,unit='piece',unit_size=amount,size_unit=unit) if kind=='ingredient' and unit in ('g','ml') else dict(quantity=amount,unit=unit,unit_size=None,size_unit=None)
        pantry_stock.set_lot(conn,oid,spec)
        table,ref,summary='owned_stock',str(oid),'%s %s%s'%(name,_num(amount),unit)
    elif kind=='gear':
        product=_item_for(K,conn,name,'gadget');oid=_owned_for(K,conn,product,day)
        table,ref,summary='owned_item',str(oid),name
    elif kind=='ledger':
        amount=data.get('amount')
        if type(amount) is not int or amount<=0:
            raise Refused('typed positive integer amount required')
        ex=conn.execute("SELECT id FROM spend_entry WHERE date=? AND amount=? AND kind='expense' AND name=?",(day,amount,name)).fetchone()
        sid=ex[0] if ex else K.spend_add(day,amount,name,source='chat',conn=conn,commit=False)
        table,ref,summary='spend_entry',str(sid),'%s %d'%(name,amount)
    elif kind=='schedule':
        tid=data.get('task_id');new_day=data.get('date')
        if type(tid) is not int or not isinstance(new_day,str) or not _ISO.fullmatch(new_day):
            raise Refused('typed task_id and YYYY-MM-DD date required')
        argv=[str(tid),new_day]
        if data.get('at') is not None:argv+=['--at',str(data['at'])]
        if conn.in_transaction:conn.commit()
        import contextlib,io
        with contextlib.redirect_stdout(io.StringIO()):K.cli_task_reschedule(argv)
        table,ref,summary='timeblock',str(tid),new_day
    else:
        names=dish_names('',data.get('names'))
        if not names:raise Refused('typed dish names list required')
        table,ref,summary='config',record_dishes(K,conn,names,day,text),', '.join(names)
    key,res=anchor(K,conn,kind,text,day,table,ref,summary,dict(data,item=name,kind=kind))
    return dict(status='ROUTED',kind=kind,landed='%s:%s'%(table,ref),anchor=key,anchor_id=res['id'],summary=summary,as_of=day)


def landed(K, conn, day=None):
    day = day or _shop(K)._today()
    out = []
    for kind, key, said in conn.execute(
            "SELECT question, landed_ref, words FROM consult_answer WHERE "
            "domain=? AND status='active' AND landed_table='config' AND "
            "landed_ref LIKE ? ORDER BY id", (DOMAIN, 'fact.%s.%%' % day)):
        raw = conn.execute("SELECT value FROM config WHERE key=?",
                           (key,)).fetchone()
        info = json.loads(raw[0]) if raw else {}
        out.append(dict(kind=kind[len('fact.'):], said=said, anchor=key,
                        landed=info.get('landed'), summary=info.get('summary'),
                        held=raw is not None))
    return out


def cli(K, verb, argv):
    argv, as_json = K._pop_flag(argv, '--json')
    conn = K.get_conn()
    try:
        try:
            if verb == 'fact-route':
                argv, hint = K._pop_opt(argv, '--kind')
                argv, raw = K._pop_opt(argv, '--data')
                data = json.loads(raw) if raw is not None else None
                argv, item = K._pop_opt(argv, '--item')
                argv, day = K._pop_opt(argv, '--as-of')
                if len(argv) != 1 or (day and not _ISO.fullmatch(day)):
                    K._err('usage: kit_life.sh fact-route "<fact text>" '
                           '[--kind %s] [--item NAME] [--as-of YYYY-MM-DD] '
                           '[--json]' % '|'.join(KINDS))
                res = route(K, conn, argv[0], hint, day, item, data=data)
                if conn.in_transaction:
                    conn.commit()
                lines = (['NO_TARGET\t%s' % res['said']] if
                         res['status'] == 'NO_TARGET' else
                         ['ROUTED\t%s\t%s\t%s' % (res['kind'], res['landed'],
                                                  res['summary'])])
            else:
                argv, day = K._pop_opt(argv, '--date')
                if argv:
                    K._err('usage: kit_life.sh fact-landed [--date YYYY-MM-DD]'
                           ' [--json]')
                res = landed(K, conn, day)
                lines = ['FACT\t%s\t%s\t%s' % (r['kind'], r['landed'],
                                               r['summary']) for r in res
                         ] or ['NO_FACTS']
        except (Refused, sqlite3.Error, ValueError) as e:
            if conn.in_transaction:
                conn.rollback()
            K._err('%s: %s' % (verb, e))
    finally:
        conn.close()
    if as_json:
        print(json.dumps(res, ensure_ascii=False))
    else:
        for ln in lines:
            print(ln)
