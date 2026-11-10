"""Purchase lots and idempotent pantry use; no quantity is inferred from ownership.

Pack consumption rounds up when a recipe opens part of a sealed pack. A recipe
ingredient is charged once per session, on its first completed using step.
Every write runs inside the caller's transaction; readers never create schema.
"""
import json
import math
import re

import shop as S

VERBS = ('shop-stock-find', 'shop-stock-set', 'shop-stock-use', 'shop-stock-empty')


def ready(c):
    return S._table_exists(c, 'owned_stock')


def number(value, zero=True):
    try:
        n = float(value)
    except (TypeError, ValueError):
        raise ValueError('stock quantity must be a finite number')
    if not math.isfinite(n) or n < 0 or (not zero and n == 0):
        raise ValueError('stock quantity must be nonnegative (unit size positive)')
    return n


def norm_unit(unit):
    aliases = {'\ud329': 'pack', 'packs': 'pack', '\uac1c': 'piece', 'pieces': 'piece',
               '\uadf8\ub7a8': 'g', '\ubc00\ub9ac\ub9ac\ud130': 'ml'}
    unit = str(unit or 'pack').strip().lower()
    if not unit:
        raise ValueError('stock unit cannot be empty')
    return aliases.get(unit, unit)


def specification(opts, size=None):
    """Explicit quantity wins; a pack size alone does not imply one pack."""
    quantity = opts.get('--quantity')
    unit = norm_unit(opts.get('--unit'))
    unit_size, size_unit = opts.get('--unit-size'), opts.get('--size-unit')
    if quantity is None and size:
        match = re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*(pack|packs|\ud329|piece|pieces|\uac1c)\s*[xX\u00d7]\s*(\d+(?:\.\d+)?)\s*(g|ml)\s*', size)
        if match:
            quantity, unit, unit_size, size_unit = match.groups()
            unit = norm_unit(unit)
    if unit_size is None and size:
        match = re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*(g|ml)\s*', size)
        if match:
            unit_size, size_unit = match.groups()
    if (unit_size is None) != (size_unit is None):
        raise ValueError('--unit-size and --size-unit must be supplied together')
    return dict(quantity=None if quantity is None else number(quantity), unit=unit,
                unit_size=None if unit_size is None else number(unit_size, False),
                size_unit=norm_unit(size_unit) if size_unit else None)


def set_lot(c, owned_id, spec):
    if not ready(c):
        raise ValueError('pantry stock requires migration 090')
    owned = S._owned_must(c, owned_id)
    if owned['status'] in ('returned', 'gone'):
        raise ValueError('cannot add stock to a returned or gone purchase')
    c.execute('INSERT INTO owned_stock(owned_id,quantity,unit,unit_size,size_unit) VALUES(?,?,?,?,?) '
              'ON CONFLICT(owned_id) DO UPDATE SET quantity=excluded.quantity, unit=excluded.unit, '
              'unit_size=excluded.unit_size,size_unit=excluded.size_unit',
              (owned_id, spec['quantity'], spec['unit'], spec['unit_size'], spec['size_unit']))
    if spec['quantity'] is not None:
        c.execute("DELETE FROM config WHERE key IN (?,?)", ('stock_asked_%s_unknown' % owned['item_id'], 'stock_asked_%s_short' % owned['item_id']))
        status = 'in_use' if spec['quantity'] > 0 else 'used_up'
        c.execute('UPDATE owned_item SET status=?,status_on=? WHERE id=?',
                  (status, None if status == 'in_use' else S._today(), owned_id))


def add_purchase(c, owned_id, opts=None, size=None):
    if not ready(c):
        return
    if c.execute('SELECT 1 FROM owned_stock WHERE owned_id=?', (owned_id,)).fetchone():
        return
    owned = S._owned_must(c, owned_id)
    cat = S._cat_get(c, owned['category_id'])
    if cat and cat['consumable']:
        set_lot(c, owned_id, specification(opts or {}, size or owned['size']))


def lots(c, item_id=None):
    if not ready(c):
        return []
    cur = c.execute("SELECT o.id AS owned_id,o.item_id,i.name,i.aliases,o.status,s.quantity,s.unit,s.unit_size,s.size_unit "
                    "FROM owned_item o JOIN shop_item i ON i.id=o.item_id "
                    "JOIN shop_category cat ON cat.id=i.category_id "
                    "LEFT JOIN owned_stock s ON s.owned_id=o.id WHERE cat.consumable=1 "
                    "AND o.status='in_use' " + ('AND o.item_id=? ' if item_id else '') +
                    'ORDER BY o.bought_on,o.id', (item_id,) if item_id else ())
    return [dict(zip([d[0] for d in cur.description], row)) for row in cur]


def find(c, words=''):
    key = S.compact(words)
    result = []
    for row in lots(c):
        names = [row['name']] + S._json_list(row['aliases'])
        if not key or any(key in S.compact(n) for n in names):
            row['unit'] = row['unit'] or 'pack'
            result.append(row)
    return result


def resolve(c, ingredient):
    """Exact taught identity first, then containment; never choose between items."""
    rows = lots(c)
    names = [ingredient['name']] + ingredient.get('aliases', [])
    exact, partial = set(), set()
    for row in rows:
        for a in names:
            for b in [row['name']] + S._json_list(row['aliases']):
                a, b = S.compact(a), S.compact(b)
                if a == b:
                    exact.add(row['item_id'])
                elif min(len(a), len(b)) >= 2 and (a in b or b in a):
                    partial.add(row['item_id'])
    ids = exact or partial
    return next(iter(ids)) if len(ids) == 1 else None


def available(c, item_id):
    rows = lots(c, item_id)
    if not rows:
        return None
    units = {norm_unit(r['unit']) for r in rows}
    if len(units) != 1 or any(r['quantity'] is None for r in rows):
        return dict(quantity=None, unit='pack')
    return dict(quantity=sum(r['quantity'] for r in rows), unit=units.pop())


def ask_once(K, c, key):
    row = c.execute('SELECT state,detail,asked,item_id FROM stock_use WHERE usage_key=?', (key,)).fetchone()
    if not row or row[0] == 'done' or row[2]:
        return []
    c.execute('UPDATE stock_use SET asked=1 WHERE usage_key=?', (key,))
    ask_key = 'stock_asked_%s_%s' % (row[3], row[0])
    if S._cfg(c, ask_key):
        return []
    c.execute('INSERT INTO config(key,value) VALUES(?,?)', (ask_key, S._today()))
    return [K._ask_choices().options_line('STOCK_ASK', row[3], []), 'STOCK_ASK\t' + row[1], 'ASK\t' + S._say(K, 'shop.stock.ask',
            'How much is left, and in which unit? Set the stock quantity, then retry this use.')]


def consume(c, item_id, amount, unit, key, empty=False):
    if not key or not str(key).strip():
        raise ValueError('stock use needs a stable --key (meal id or cooking ingredient)')
    row = c.execute('SELECT item_id,quantity,unit,state FROM stock_use WHERE usage_key=?', (key,)).fetchone()
    amount = None if empty else number(amount)
    unit = norm_unit(unit)
    if row:
        unknown_amount = row[3] == 'unknown' and row[1] is None and row[0] == int(item_id)
        if not unknown_amount and (row[0], row[1], row[2]) != (int(item_id), amount, unit):
            raise ValueError('stock use key already identifies different use')
        if unknown_amount:
            c.execute('UPDATE stock_use SET quantity=?,unit=? WHERE usage_key=?', (amount, unit, key))
        if row[3] == 'done':
            return 'done'
    rows = lots(c, int(item_id))
    # All checks precede updates: unknown units or shortage cannot partially use lots.
    allocations, remaining = [], amount or 0
    state = 'done'
    if not rows:
        state = 'short'
    for lot in rows:
        qty, lot_unit = lot['quantity'], norm_unit(lot['unit'])
        if empty:
            allocations.append((lot['owned_id'], 0))
            continue
        if remaining <= 1e-9:
            break
        if qty is None:
            state = 'unknown'
            break
        if unit == lot_unit:
            need = remaining
            used = min(qty, need)
            remaining -= used
        elif unit == lot['size_unit'] and lot['unit_size']:
            need = math.ceil(max(0, remaining / lot['unit_size'] - 1e-9))
            used = min(qty, need)
            remaining = max(0, remaining - used * lot['unit_size'])
        else:
            state = 'unknown'
            break
        allocations.append((lot['owned_id'], max(0, qty-used)))
    if state == 'done' and not empty and remaining > 1e-9:
        state = 'short'
    detail = json.dumps(dict(item_id=int(item_id), quantity=amount, unit=unit, state=state), sort_keys=True)
    c.execute('INSERT INTO stock_use(usage_key,item_id,quantity,unit,state,detail) VALUES(?,?,?,?,?,?) '
              'ON CONFLICT(usage_key) DO UPDATE SET state=excluded.state,detail=excluded.detail',
              (key, item_id, amount, unit, state, detail))
    if state == 'done':
        for owned_id, qty in allocations:
            # Missing older stock rows become explicit zero only on "used it all".
            if empty:
                c.execute("INSERT OR IGNORE INTO owned_stock(owned_id,quantity,unit) VALUES(?,0,'pack')", (owned_id,))
            c.execute('UPDATE owned_stock SET quantity=? WHERE owned_id=?', (qty, owned_id))
            if qty == 0:
                c.execute("UPDATE owned_item SET status='used_up',status_on=? WHERE id=?", (S._today(), owned_id))
    return state


def describe(K, quantity, unit):
    if quantity is None:
        return S._say(K, 'shop.stock.unknown', 'in stock; quantity unknown')
    unit = S._say(K, 'shop.stock.unit.' + norm_unit(unit), unit)
    return S._say(K, 'shop.stock.remaining', 'in stock; {quantity}{unit} left',
                  quantity=format(quantity, '.4g'), unit=unit)


def lines(c, words=''):
    return ['STOCK\t' + json.dumps(r, ensure_ascii=False, sort_keys=True) for r in find(c, words)]


def owned_lines(c, owned_rows):
    ids = {r['id'] for r in owned_rows}
    return ['STOCK\t' + json.dumps(r, ensure_ascii=False, sort_keys=True)
            for r in lots(c) if r['owned_id'] in ids]


def decorate(K, c, plan):
    for ing in plan['ingredients']:
        ing.pop('stock_label', None)
        ing.pop('stock', None)
        ing.pop('stock_item', None)
        item = resolve(c, ing)
        if item is not None:
            ing['stock_item'] = item
        stock = available(c, item) if item else None
        if stock:
            # Cooking renders these facts using its own copy and counters.
            ing['stock'] = stock


def cooking_use(K, c, state, step):
    used = state.setdefault('stock_used', [])
    notices = []
    for ing in state['plan']['ingredients']:
        if ing['id'] not in step['ingredients'] or ing['id'] in used:
            continue
        item = resolve(c, ing)
        if item is None:
            # Absent pantry entries do not assert ownership or guess purchases.
            continue
        key = 'cooking:%s:%s' % (state.get('stock_plan_key', state['id']), ing['id'])
        prior = c.execute('SELECT item_id,state FROM stock_use WHERE usage_key=?', (key,)).fetchone()
        if prior and prior == (item, 'done'):
            used.append(ing['id'])
            continue
        status = consume(c, item, ing['quantity'], ing['unit'], key)
        if status == 'done':
            used.append(ing['id'])
        else:
            notices += ask_once(K, c, key)
    state.setdefault('stock_notices', []).extend(notices)


def cli(K, c, verb, argv):
    if not ready(c):
        raise ValueError('pantry stock requires migration 090')
    opts, pos = S._opt(argv, {k: True for k in ('--quantity','--unit','--unit-size','--size-unit','--key','--on')})
    if verb == 'shop-stock-find':
        import made_dish
        query = pos[0] if pos else ''
        cooked = []
        for d in made_dish.dishes(K, c, query):
            if d['remaining'] <= 1e-9:
                continue
            share = format(d['remaining'], '.4g')
            if abs(d['remaining']-.5) < 1e-9:
                share = K._i18n('dish.stock.half', 'half')
            quantity = d['remaining'] * d['yield_quantity']
            label = K._i18n('dish.stock.remaining', '{name}: {share} whole left').format(name=d['name'], share=share)
            cooked.append('MADE_DISH_STOCK\t' + json.dumps(dict(id=d['id'], name=d['name'], remaining=d['remaining'], quantity=quantity, unit=d['yield_unit'], label=label), ensure_ascii=False))
        return lines(c, query) + cooked or ['NONE\tstock'], 0
    if verb == 'shop-stock-set':
        if len(pos) != 2:
            raise ValueError('shop-stock-set OWNED_ID QUANTITY [--unit pack --unit-size 80 --size-unit g]')
        opts['--quantity'] = pos[1]
        owned = S._owned_must(c, pos[0])
        set_lot(c, owned['id'], specification(opts))
        K.experience_ensure(c, S._owned_title(owned), occurred_on=owned['bought_on'],
                            evidence=[('owned_item', owned['id']), ('owned_stock', owned['id'])])
        return lines(c, owned['name']), 0
    if len(pos) != (1 if verb == 'shop-stock-empty' else 2) or not opts.get('--key'):
        raise ValueError('shop-stock-use ITEM_ID QUANTITY --unit UNIT --key SOURCE_ID | shop-stock-empty ITEM_ID --key SOURCE_ID')
    item = S._item_must(c, pos[0])
    status = consume(c, item['id'], pos[1] if len(pos) > 1 else None,
                     opts.get('--unit', 'pack'), opts['--key'], empty=verb == 'shop-stock-empty')
    if status == 'done':
        usage = c.execute('SELECT rowid FROM stock_use WHERE usage_key=?', (opts['--key'],)).fetchone()
        K.experience_ensure(c, item['name'], occurred_on=opts.get('--on'),
                            evidence=[('stock_use', usage[0]), ('shop_item', item['id'])])
    return ['STOCK_USE\t'+status] + ask_once(K, c, opts['--key']) + lines(c, item['name']), 0


def cooking_batch(K, c, state, now):
    """Pair one earlier prep with one cook, without merging later meals."""
    import copy
    import hashlib
    from datetime import datetime
    from zoneinfo import ZoneInfo
    source = copy.deepcopy(state['source'])
    day = datetime.fromtimestamp(now, ZoneInfo(source.get('timezone', 'Asia/Seoul'))).date().isoformat()
    for field in ('block', 'target_at', 'changes'):
        source.pop(field, None)
    identity = source.get('plan_id') or hashlib.sha256(json.dumps(dict(recipes=source['recipes'], eaters=source.get('eaters', [])), sort_keys=True).encode()).hexdigest()
    key = 'cooking.prepared.' + K._pin_agent() + '.' + identity
    raw = K._config_stored(c, key)
    prep = json.loads(raw) if raw else {}
    state['stock_plan_key'] = state['id']
    if state['source']['block'] == 'prep':
        K._config_put(c, key, json.dumps(dict(day=day, batch=state['id'])))
    elif prep.get('day') == day and not prep.get('claimed_by'):
        state['stock_plan_key'] = prep['batch']
        prep['claimed_by'] = state['id']
        K._config_put(c, key, json.dumps(prep))
