"""Purpose-linked selections before checkout; purchase promotion reuses shop.bought.

The additive table is installed on the first capture write for existing >=72
kits, like other runtime extension tables. Reads never initialize schema.
No shopping management consent is required to remember owner-stated facts.
"""
import datetime
import json
import os
import re

import shop as S
import pantry_stock as P

STATES = ('picked', 'in_cart', 'ordered', 'bought')
DDL = """CREATE TABLE IF NOT EXISTS shop_capture (
    id INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL REFERENCES shop_item(id),
    purpose TEXT NOT NULL CHECK (TRIM(purpose) <> ''),
    event_id INTEGER REFERENCES event(id) ON DELETE SET NULL,
    hunt_id INTEGER REFERENCES shop_hunt(id) ON DELETE SET NULL,
    selected_on TEXT NOT NULL,
    size_label TEXT,
    offer_price INTEGER CHECK (offer_price IS NULL OR offer_price >= 0),
    store TEXT,
    link TEXT,
    status TEXT NOT NULL CHECK (status IN ('picked','in_cart','ordered','bought')),
    owned_id INTEGER REFERENCES owned_item(id) ON DELETE SET NULL,
    note TEXT,
    updated_at TEXT NOT NULL
)"""
# No UNIQUE index: merges and event deletion must preserve separate captures.
# Find-first is serialized by shop.cli's BEGIN IMMEDIATE transaction.


def _event(conn, ref):
    if ref is None:
        return None
    col = 'id' if str(ref).isdigit() else 'ulid'
    row = conn.execute('SELECT id FROM event WHERE %s=?' % col, (ref,)).fetchone()
    if not row:
        raise S.Usage('event %r not found (use its id or ulid)' % ref)
    return row[0]


def _rows(conn):
    if not S._table_exists(conn, 'shop_capture'):
        return []
    cursor = conn.execute('SELECT * FROM shop_capture ORDER BY id DESC')
    keys = [c[0] for c in cursor.description]
    return [dict(zip(keys, r)) for r in cursor]


def _must(conn, ref):
    for row in _rows(conn):
        if str(row['id']) == str(ref):
            return row
    raise S.Usage('capture %r not found (shop-capture-find)' % ref)


def facts(conn, row):
    it = S._item_must(conn, row['item_id'])
    out = dict(row, name=it['name'], brand=it['brand'], variant=it['variant'],
               aliases=S._json_list(it['aliases']), price=row['offer_price'])
    if row['owned_id'] is not None:
        owned = S._owned_must(conn, row['owned_id'])
        out.update(price=S.price_of(owned), store=owned['store'], link=owned['link'],
                   size_label=owned['size'] or row['size_label'],
                   bought_on=owned['bought_on'], spend_id=owned['spend_id'],
                   owned_status=owned['status'])
    if P.ready(conn):
        spec = conn.execute('SELECT quantity,unit,unit_size,size_unit FROM shop_capture_quantity WHERE capture_id=?',
                            (row['id'],)).fetchone()
        if spec:
            out['pack_quantity'] = dict(zip(('quantity','unit','unit_size','size_unit'), spec))
        stock = P.available(conn, row['item_id'])
        if stock:
            out['stock'] = stock
        allocated = conn.execute('SELECT amount FROM shop_capture_payment WHERE capture_id=?', (row['id'],)).fetchone()
        if allocated:
            out['price'] = allocated[0]
            out['order_total'] = conn.execute('SELECT amount FROM spend_entry WHERE id=?', (owned['spend_id'],)).fetchone()[0]
    return out


def _line(conn, row):
    return 'CAPTURE\t' + json.dumps(facts(conn, row), ensure_ascii=False, sort_keys=True)


def find(conn, words='', purpose=None, event=None, status=None):
    eid = _event(conn, event)
    if status is not None and status not in STATES:
        raise S.Usage('--status ' + '|'.join(STATES))
    lines = []
    for row in _rows(conn):
        it = S._item_must(conn, row['item_id'])
        names = [it['name'], it['brand'], it['variant'], row['purpose']] + S._json_list(it['aliases'])
        if words and row['event_id'] is not None:
            event_row = conn.execute('SELECT title FROM event WHERE id=?', (row['event_id'],)).fetchone()
            if event_row:
                names.append(event_row[0])
        if words and not any(S.compact(words) in S.compact(n) for n in names if n):
            continue
        if purpose and S.compact(purpose) not in S.compact(row['purpose']):
            continue
        if eid is not None and row['event_id'] != eid:
            continue
        if status is not None and row['status'] != status:
            continue
        lines.append(_line(conn, row))
    return lines + P.lines(conn, words) if lines else (P.lines(conn, words) or ['NONE\tshopping capture'])


def context_day():
    """The caller's conversation day; explicit --on remains authoritative."""
    return S._date(os.environ.get('LIFEKIT_CONTEXT_DATE'),
                   'LIFEKIT_CONTEXT_DATE') or S._today()


def related(conn, words='', block_id=None, today=None):
    """Read-only context for schedule readers: 30 days, 200 candidates, top 5.

    Never list unrelated shopping on an empty/date-only schedule lookup.
    Keep cart/purchase status visible so recall does not invent checkout.
    """
    stop = {'prep', 'cook', 'cooking', 'start', 'today', 'tomorrow',
            '\ud504\ub819', '\uc900\ube44', '\uc2dc\uc791', '\uc694\ub9ac', '\uc624\ub298', '\ub0b4\uc77c'}
    tokens = [S.compact(w) for w in re.findall(r'[^\W_]+', words or '')
              if len(w) >= 2 and S.compact(w) not in stop and not w.isdigit()]
    linked_ids = {r[0] for r in conn.execute(
        'SELECT event_id FROM event_timeblock WHERE timeblock_id=?', (block_id,))}
    if not tokens and not linked_ids:
        return []
    if not S._table_exists(conn, 'shop_capture'):
        return []
    day = today or context_day()
    since = (datetime.date.fromisoformat(day) - datetime.timedelta(days=30)).isoformat()
    cur = conn.execute('SELECT * FROM shop_capture WHERE selected_on BETWEEN ? AND ? '
                       'ORDER BY selected_on DESC,id DESC LIMIT 200', (since, day))
    keys = [c[0] for c in cur.description]
    rows = [dict(zip(keys, r)) for r in cur]
    lines = []
    stock_items = set()
    clean = lambda value: ' '.join(str(value or '').split())
    for row in rows:
        fact = facts(conn, row)
        if fact.get('owned_status') in ('returned', 'gone', 'used_up'):
            continue
        event = (conn.execute('SELECT title FROM event WHERE id=?',
                              (row['event_id'],)).fetchone() if row['event_id'] else None)
        names = [row['purpose'], fact['name'], fact['brand'], fact['variant']]
        names += fact['aliases'] + ([event[0]] if event else [])
        linked = row['event_id'] in linked_ids
        if not linked and not any(t in S.compact(n)
                                  for t in tokens for n in names if n):
            continue
        stock_items.add(row['item_id'])
        size = clean(fact['size_label'])
        variant = clean(fact['variant'])
        if variant and S.compact(variant) not in S.compact(size):
            size += ' [variant: %s]' % variant
        lines.append('RELATED_SHOPPING %s: %s %s (%s; %s; capture=%s)' % (
            clean(row['purpose']), clean(fact['name']), size,
            row['selected_on'], row['status'], row['id']))
        if fact.get('stock'):
            lines[-1] += ' [stock: ' + json.dumps(fact['stock'], sort_keys=True) + ']'
        if len(lines) == 5:
            break
    seen = set()
    for stock in P.lots(conn):
        if stock['item_id'] in stock_items:
            seen.add(stock['owned_id'])
    for token in tokens:
        for stock in P.find(conn, token):
            if stock['owned_id'] not in seen:
                seen.add(stock['owned_id'])
                lines.append('RELATED_STOCK ' + json.dumps(stock, ensure_ascii=False, sort_keys=True))
    return lines


def cooking_context(K, conn, plan):
    """Ready cards retain selected pack facts separately from recipe usage."""
    title = S.compact(plan['name'])
    day = context_day()
    since = (datetime.date.fromisoformat(day)-datetime.timedelta(days=30)).isoformat()
    rows = []
    for row in _rows(conn):
        if not since <= row['selected_on'] <= day:
            continue
        if S.compact(row['purpose']) not in title:
            continue
        fact = facts(conn,row)
        if fact.get('owned_status') in ('returned','gone','used_up'):
            continue
        size = fact['size_label'] or fact['variant'] or ''
        variant = fact['variant'] or ''
        if variant and S.compact(variant) not in S.compact(size):
            size += ' / ' + variant
        status = S._say(K, 'shop.capture.status.'+row['status'],row['status'])
        rows.append('%s %s (%s)' % (fact['name'],size,status))
        if len(rows)==5:
            break
    return rows


def write(K, conn, ref, opts, update=False):
    # Initialize inside the caller's transaction: failed writes leave no table.
    conn.execute(DDL)
    day = S._date(opts.get('--on'), '--on') or context_day()
    conversation_day = S._date(os.environ.get('LIFEKIT_CONTEXT_DATE'), 'LIFEKIT_CONTEXT_DATE')
    if conversation_day and day > conversation_day:
        raise S.Refuse([('CAPTURE_AFTER_CONVERSATION_DAY',
            'date=%s is after conversation day=%s; use --on %s for this turn' %
            (day, conversation_day, conversation_day))])
    status = opts.get('--status')
    if status is not None and status not in STATES:
        raise S.Usage('--status ' + '|'.join(STATES))
    if update:
        row = _must(conn, ref)
    else:
        item = S._item_must(conn, ref)
        purpose = S._ws(opts.get('--purpose'))
        if not purpose:
            raise S.Usage('shop-capture <item_id> --purpose "<dish/event/purpose>"')
        eid = _event(conn, opts.get('--event'))
        row = next((r for r in _rows(conn) if r['item_id'] == item['id'] and
                    r['purpose'] == purpose and r['event_id'] == eid and
                    r['selected_on'] == day), None)
        if row is None:
            cursor = conn.execute('INSERT INTO shop_capture '
                '(item_id,purpose,event_id,selected_on,status,updated_at) '
                "VALUES (?,?,?,?,'picked',?)", (item['id'], purpose, eid, day, S._stamp()))
            row = _must(conn, cursor.lastrowid)
    capture_eid = K.experience_ensure(conn, S._item_must(conn, row['item_id'])['name'],
        occurred_on=row['selected_on'], evidence=[('shop_capture', row['id'])],
        event_id=_event(conn, opts['--event']) if '--event' in opts else row['event_id'],
        note=opts.get('--note'))
    target = status or row['status']
    if STATES.index(target) < STATES.index(row['status']):
        raise S.Refuse([('STATUS_REGRESSION', '%s -> %s' % (row['status'], target))])
    for opt, col in (('--purpose', 'purpose'), ('--size', 'size_label'),
                     ('--store', 'store'), ('--link', 'link'), ('--note', 'note')):
        if opt in opts:
            value = S._ws(opts[opt])
            if not value:
                raise S.Usage('%s cannot be empty' % opt)
            row[col] = value
    if '--event' in opts:
        row['event_id'] = _event(conn, opts['--event'])
    if '--hunt' in opts:
        hunt = S._hunt_must(conn, opts['--hunt'])
        item = S._item_must(conn, row['item_id'])
        if item['category_id'] != hunt['category_id']:
            raise S.Usage('capture and hunt must have the same category')
        row['hunt_id'] = hunt['id']
    if '--price' in opts:
        row['offer_price'] = S._amount(opts['--price'])
    if any(k in opts for k in ('--quantity', '--unit', '--unit-size', '--size-unit')):
        if not P.ready(conn):
            raise S.Usage('capture quantities require migration 090')
        spec = P.specification(opts, row['size_label'])
        conn.execute('INSERT INTO shop_capture_quantity(capture_id,quantity,unit,unit_size,size_unit) VALUES(?,?,?,?,?) '
                     'ON CONFLICT(capture_id) DO UPDATE SET quantity=excluded.quantity,unit=excluded.unit, '
                     'unit_size=excluded.unit_size,size_unit=excluded.size_unit',
                     (row['id'],spec['quantity'],spec['unit'],spec['unit_size'],spec['size_unit']))
    lines = []
    receipt = None
    if row['owned_id'] is not None:
        # Corrections share the purchase's single price home and missing ask state.
        if any(o in opts for o in ('--size', '--store', '--link', '--price', '--on')):
            _ow, extra = S.owned_upd(K, conn, row['owned_id'],
                size=opts.get('--size'), store=opts.get('--store'), link=opts.get('--link'),
                price=opts.get('--price'), note=opts.get('--note'), day=opts.get('--on'))
            lines.extend(extra)
            if '--on' in opts and _ow['spend_id'] is not None:
                sid = _ow['spend_id']
                conn.execute('UPDATE spend_entry SET date=? WHERE id=?', (day, sid))
                spend = conn.execute('SELECT date,name,amount,kind,method FROM spend_entry '
                                     'WHERE id=?', (sid,)).fetchone()
                K._spend_write_v32(conn, sid, *spend)
    elif target in ('ordered', 'bought'):
        if day < row['selected_on']:
            raise S.Refuse([('PURCHASE_BEFORE_SELECTION', 'purchase=%s selected_on=%s; '
                            'use the owner-stated purchase date' % (day, row['selected_on']))])
        if opts.get('--online') not in (None, '0', '1'):
            raise S.Usage('--online 0|1')
        extra = S.bought(K, conn, item_ref=row['item_id'], day=day,
            store=row['store'], link=row['link'], price=row['offer_price'],
            size=row['size_label'], note=row['note'], online=opts.get('--online'),
            link_spend=opts.get('--link-spend'), new_spend='--new-spend' in opts,
            experience_id=capture_eid)
        row['owned_id'] = int(extra[0].split('\t')[1])
        receipt = extra.receipt
        lines.extend(extra)
        if row['hunt_id'] is not None:
            conn.execute('UPDATE owned_item SET hunt_id=? WHERE id=?',
                         (row['hunt_id'], row['owned_id']))
    row.update(status=target, updated_at=S._stamp())
    if row['owned_id'] is not None:
        row['offer_price'] = None  # purchase price now has exactly one home
    cols = ('purpose', 'event_id', 'hunt_id', 'size_label', 'offer_price', 'store',
            'link', 'status', 'owned_id', 'note', 'updated_at')
    conn.execute('UPDATE shop_capture SET ' + ','.join(c + '=?' for c in cols) +
                 ' WHERE id=?', [row[c] for c in cols] + [row['id']])
    if row['owned_id'] is not None and P.ready(conn):
        spec = conn.execute('SELECT quantity,unit,unit_size,size_unit FROM shop_capture_quantity WHERE capture_id=?', (row['id'],)).fetchone()
        # Initial promotion only: a later "bought it" never replenishes used stock.
        if spec and receipt:
            P.set_lot(conn, row['owned_id'], dict(zip(('quantity','unit','unit_size','size_unit'),spec)))
        elif not spec:
            P.add_purchase(conn, row['owned_id'], size=row['size_label'])
    if row['owned_id'] is not None:
        owned = S._owned_must(conn, row['owned_id'])
        evidence = [('owned_item', owned['id']), ('shop_capture', row['id']), ('shop_item', row['item_id'])]
        if owned['spend_id'] is not None:
            evidence.append(('spend_entry', owned['spend_id']))
        if P.ready(conn) and conn.execute('SELECT 1 FROM owned_stock WHERE owned_id=?', (owned['id'],)).fetchone():
            evidence.append(('owned_stock', owned['id']))
        K.experience_ensure(conn, S._owned_title(owned), occurred_on=owned['bought_on'],
                            evidence=evidence, event_id=capture_eid, note=row['note'])
    lines.append(_line(conn, row))
    return S.CaptureLines(lines, receipt) if receipt else lines


def cli(K, conn, verb, argv):
    if verb == 'shop-capture-find':
        o, p = S._opt(argv, {'--purpose': True, '--event': True, '--status': True})
        if len(p) > 1:
            raise S.Usage('shop-capture-find ["<ingredient/product>"] [--purpose ..] [--event ..]')
        return find(conn, p[0] if p else '', o.get('--purpose'), o.get('--event'),
                    o.get('--status')), 0
    o, p = S._opt(argv, {k: True for k in ('--purpose', '--event', '--hunt', '--size',
        '--price', '--store', '--link', '--status', '--on', '--note', '--online',
        '--link-spend', '--quantity', '--unit', '--unit-size', '--size-unit')} | {'--new-spend': False})
    if len(p) != 1:
        raise S.Usage('%s <id> [--purpose ..] [--status %s]' % (verb, '|'.join(STATES)))
    return write(K, conn, p[0], o, update=verb == 'shop-capture-upd'), 0
