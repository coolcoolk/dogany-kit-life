"""Match ledger arrivals to open captures without creating another spend.

Only a unique exact subset with merchant + seven-day date evidence is automatic.
Partial/ambiguous evidence is persisted for one question, never guessed. The
capture primary key prevents a second payment linking the same selection.
"""
import datetime
import json
from pathlib import Path
from urllib.parse import urlparse

import pantry_stock as P
import shop as S
import shop_capture as C


def ready(c):
    return S._table_exists(c, 'shop_payment')


def merchant(c, value):
    text = (value or '').strip().lower()
    if '://' in text:
        text = urlparse(text).netloc
    text = text.removeprefix('www.')
    for suffix in ('.co.kr', '.com', '.kr'):
        if text.endswith(suffix):
            text = text[:-len(suffix)]
            break
    key = S.compact(text)
    raw_aliases = S._cfg(c, 'shop_merchant_aliases')
    try:
        aliases = json.loads(raw_aliases) if raw_aliases else {}
    except (TypeError, ValueError):
        aliases = {}
    try:
        defaults = json.loads(Path(__file__).with_name('shop_merchant_aliases.json').read_text())
    except (OSError, ValueError):
        defaults = {}
    if isinstance(aliases, dict):
        defaults.update(aliases)
    for canonical, values in defaults.items():
        if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
            continue
        if key in [S.compact(canonical)] + [S.compact(v) for v in values]:
            return S.compact(canonical)
    return key


def candidates(c, spend):
    try:
        day = datetime.date.fromisoformat(spend['date'])
    except (TypeError, ValueError):
        return []
    since = (day - datetime.timedelta(days=7)).isoformat()
    key = merchant(c, spend['name'])
    rows = []
    for row in C._rows(c):
        if row['status'] not in ('picked', 'in_cart') or row['owned_id'] is not None:
            continue
        if not since <= row['selected_on'] <= spend['date']:
            continue
        if c.execute('SELECT 1 FROM shop_capture_payment WHERE capture_id=?', (row['id'],)).fetchone():
            continue
        shop = merchant(c, row['store'] or row['link'])
        row = dict(row, merchant_match=bool(key and shop and (key == shop or
                   min(len(key), len(shop)) >= 3 and (key in shop or shop in key))))
        if row['merchant_match'] or not shop:
            rows.append(row)
    return rows


def exact_subsets(rows, amount):
    """Dynamic sum search retains two solutions; ambiguity never disappears.

    Bound the work for a large cart rather than risking an exponential search.
    A zero/unknown offer cannot make an exact automatic order.
    """
    sums = {0: [()]}
    if len(rows) > 40:
        return []
    for row in rows:
        price = row['offer_price']
        if price is None or price <= 0 or price > amount:
            continue
        for total, paths in [(total, list(paths)) for total, paths in sums.items()]:
            new = total + price
            if new <= amount:
                sums.setdefault(new, [])
                for path in list(paths):
                    if len(sums[new]) < 2:
                        sums[new].append(path + (row['id'],))
        if len(sums) > 20000:
            return []
    return sums.get(amount, [])


def link(K, c, spend, rows):
    from ledger_v6 import spend_link
    for capture in rows:
        # Money is shared by an order; item allocations live on the link table.
        # owned_item.price remains NULL; the spend remains the only money home.
        cur = c.execute('INSERT INTO owned_item(ulid,item_id,bought_on,store,link,hunt_id,spend_id,price,size_label) '
                        'VALUES(?,?,?,?,?,?,?,NULL,?)',
                        (K.new_ulid(), capture['item_id'], spend['date'], capture['store'], capture['link'],
                         capture['hunt_id'], spend['id'], capture['size_label']))
        oid = cur.lastrowid
        c.execute('INSERT INTO shop_capture_payment(capture_id,spend_id,owned_id,amount) VALUES(?,?,?,?)',
                  (capture['id'], spend['id'], oid, capture['offer_price']))
        c.execute("UPDATE shop_capture SET status='bought',owned_id=?,offer_price=NULL,updated_at=? WHERE id=?",
                  (oid, S._stamp(), capture['id']))
        spec = c.execute('SELECT quantity,unit,unit_size,size_unit FROM shop_capture_quantity WHERE capture_id=?',
                         (capture['id'],)).fetchone()
        if spec:
            P.set_lot(c, oid, dict(zip(('quantity','unit','unit_size','size_unit'), spec)))
        else:
            P.add_purchase(c, oid, size=capture['size_label'])
        evidence = [('shop_capture', capture['id']), ('owned_item', oid),
                    ('shop_item', capture['item_id']), ('spend_entry', spend['id'])]
        if c.execute('SELECT 1 FROM owned_stock WHERE owned_id=?', (oid,)).fetchone():
            evidence.append(('owned_stock', oid))
        K.experience_ensure(c, S._item_must(c, capture['item_id'])['name'],
                            occurred_on=spend['date'], evidence=evidence,
                            event_id=capture['event_id'])
        spend_link(c, spend['id'], 'owned_item', str(oid), source='owner')
        spend_link(c, spend['id'], 'shop_item', str(capture['item_id']), source='owner')


def match(K, c, sid, selected=None):
    if not ready(c):
        return []
    cur = c.execute('SELECT id,date,amount,name,kind,class,scope FROM spend_entry WHERE id=?', (sid,))
    row = cur.fetchone()
    if not row:
        raise ValueError('spend not found')
    spend = dict(zip([d[0] for d in cur.description], row))
    old = c.execute('SELECT state FROM shop_payment WHERE spend_id=?', (sid,)).fetchone()
    if old and old[0] == 'matched':
        return []
    if spend['amount'] <= 0 or spend['kind'] != 'expense' or spend['class'] not in (None, 'spend') or spend['scope'] != 'personal':
        return []  # refunds, income, transfers and non-owner payments never buy stock
    if c.execute('SELECT 1 FROM owned_item WHERE spend_id=?', (sid,)).fetchone():
        return []
    rows = candidates(c, spend)
    if not rows:
        return []
    exact = exact_subsets([r for r in rows if r['merchant_match']], spend['amount'])
    chosen = exact[0] if len(exact) == 1 else None
    if selected is not None:
        selected = tuple(int(i) for i in selected)
        by_id = {r['id']: r for r in rows}
        if not selected or len(set(selected)) != len(selected) or not set(selected) <= by_id.keys():
            raise ValueError('selection must contain unlinked capture ids from this payment window')
        if any(by_id[i]['offer_price'] is None for i in selected) or sum(by_id[i]['offer_price'] for i in selected) != spend['amount']:
            raise ValueError('confirmed capture allocations must equal payment amount')
        chosen = selected
    state = 'matched' if chosen else 'ask'
    c.execute('INSERT INTO shop_payment(spend_id,state,candidates) VALUES(?,?,?) '
              'ON CONFLICT(spend_id) DO UPDATE SET state=excluded.state,candidates=excluded.candidates',
              (sid, state, json.dumps([r['id'] for r in rows])))
    if chosen:
        link(K, c, spend, [r for r in rows if r['id'] in chosen])
    return [dict(spend_id=sid, state=state, captures=list(chosen or []))]


def notices(K, c, sid):
    if not ready(c):
        return []
    row = c.execute('SELECT state,candidates,asked FROM shop_payment WHERE spend_id=?', (sid,)).fetchone()
    if not row or row[0] != 'ask' or row[2]:
        return []
    c.execute('UPDATE shop_payment SET asked=1 WHERE spend_id=?', (sid,))
    return [K._ask_choices().options_line('SHOP_PAYMENT_ASK', sid, []), 'SHOP_PAYMENT_ASK\tspend=%s\tcaptures=%s' % (sid, row[1]),
            'ASK\t' + S._say(K, 'shop.payment.ask',
               'Which of these cart items does this payment cover?')]


def cli(K, c, argv):
    opts, pos = S._opt(argv, {'--captures': True})
    if len(pos) != 1:
        raise ValueError('shop-payment-match SPEND_ID [--captures ID,ID]')
    if not ready(c):
        raise ValueError('payment matching requires migration 090')
    selected = opts['--captures'].split(',') if '--captures' in opts else None
    out = match(K, c, int(pos[0]), selected)
    return ['SHOP_PAYMENT\t' + json.dumps(r, sort_keys=True) for r in out] + notices(K, c, int(pos[0])), 0
