"""ledger_balance.py -- account balances, cash on hand and card bills as
point-in-time readings on the payment instrument's entity (kit_life 2.46.0).

Owner 2026-10-10 10:01: the ledger stores and shows account balances, cash on
hand and card bills, using the seat the resource axis already designed. No new
storage design:

  instrument  pay_method (kind, billing_day, linked_account_id) is the roster.
              Its entity is the 039 rule (label namespace='entity', display ==
              pay_method.name; kit_life._spend_entity_id) -- the same entity
              every money block_delta already carries. No account table.
  reading     a measure row on that entity (measure.entity_id, the reading
              seat 036 built and nothing wrote): metric balance_krw (account /
              cash / easy_pay) or card_bill_krw (credit_card statement amount,
              due date in qualifier 'due:YYYY-MM-DD'), unit krw, basis actual,
              observed_at = the as-of date (date grade, the 6,035-row grade).
  provenance  hand -> value_origin 'declared' (the owner said it), auto ->
              'measured' (an app / export read it). seq 1 = hand, 2 = auto, so
              the two never overwrite each other on the same day (ux_measure_
              row); the same provenance on the same day is a correction and
              replaces the value.
  flow        block_delta money rows on the entity dated AFTER the as-of date
              (the reading is the balance at the end of that day), plus, for an
              account, the deltas of the debit cards linked to it. Transfers
              between own accounts never land as spend or income (intake
              excludes them, intake.py operation 'transfer'), so they are not in
              the flow; the estimate says so.

The metrics are registered by this module on first write (design B-3: the kit
seeds no metrics -- the module that writes a metric registers it).

Dispatched from kit_life._DISPATCH through cli(K, verb, argv), K = the running
kit_life module (the consult_anchor.py / shop.py convention: never import
kit_life here).

VERBS
  balance-set <instrument> <amount> [--as-of YYYY-MM-DD] [--auto [--source S]]
        [--json]                       an account / easy-pay balance reading
  cash-set <amount> [--as-of D] [--auto [--source S]] [--json]
                                       cash on hand (the cash instrument is
                                       found, or registered on first use)
  card-bill-set <card> <amount> --due YYYY-MM-DD [--as-of D] [--auto
        [--source S]] [--json]         a credit-card statement: amount + due
  balance-show [--today D] [--stale-days N] [--say] [--json]
        every instrument: latest reading + its age, the other provenance's
        latest when it differs, flow since the reading and the ESTIMATE
        (reading + flow); no reading -> MISSING, never 0
  balance-stale [--today D] [--stale-days N] [--tsv] [--json]
        the balance instruments whose reading is missing or older than N days
        (default 7); legacy read output is unchanged
  balance-stale --weekly --json [--claim-missing] [--today D]
        onboarded weekly feed: stale accounts ranked by 90-day use, plus a
        separate first-reading offer; --claim-missing records offered methods
  card-bill-month [YYYY-MM] [--today D] [--say] [--json]
        "this month's card bill": per credit card, the bill reading due in the
        month when one exists (said as the bill), else the recorded spend on
        that card in the month (said as recorded spend, not the bill); when
        installments touch the month (ledger_installment.py) the answer is an
        ESTIMATE = recorded one-off spend (linked installment purchases
        taken out) + the installment charges due that month

<instrument> = pay_method id | name | alias | entity token | entity label.
0 / several matches -> refused with the candidates (never a guess).

EXIT CODES: 0 ok; 1 usage / refused (stderr, nothing written).
"""
from date_parts import month_day
import datetime
import json
import os
import re
import sqlite3
from money_format import comma

VERBS = ('balance-del', 'balance-set', 'cash-set', 'card-bill-set', 'balance-show',
         'balance-stale', 'card-bill-month')

METRIC_BALANCE = 'balance_krw'
METRIC_BILL = 'card_bill_krw'
BALANCE_KINDS = ('account', 'savings', 'securities', 'cash', 'easy_pay')
BILL_KINDS = ('credit_card', 'corporate_card')
VIA_KINDS = ('debit_card',)          # spends from its linked account
ORIGIN = {'hand': 'declared', 'auto': 'measured'}
SEQ = {'hand': 1, 'auto': 2}
PROV_OF = {'declared': 'hand', 'measured': 'auto'}
STALE_DAYS = 7
AMOUNT_MAX = 10 ** 12
_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
_MONTH_RE = re.compile(r'^\d{4}-\d{2}$')


class Refused(Exception):
    pass


# -- helpers ------------------------------------------------------------------
def _date(raw, what):
    if raw is None:
        return None
    if not _DATE_RE.match(str(raw)):
        raise Refused('%s must be YYYY-MM-DD: %r' % (what, raw))
    try:
        return datetime.date.fromisoformat(raw)
    except ValueError:
        raise Refused('%s is not a date: %r' % (what, raw))


def _amount(raw, allow_negative=False):
    s = str(raw).replace(',', '').strip()
    if not re.match(r'^-?\d+$', s):
        raise Refused('amount must be whole KRW: %r' % raw)
    v = int(s)
    if (v < 0 and not allow_negative) or abs(v) > AMOUNT_MAX:
        raise Refused('amount out of range: %r' % raw)
    return v


def _today(K, raw):
    return _date(raw, '--today') if raw else K.person_today_local()


def ensure_metrics(conn):
    for tok in (METRIC_BALANCE, METRIC_BILL):
        conn.execute(
            "INSERT OR IGNORE INTO metric_def(token, scope_kind, accrual, "
            "unit_token, owner_module, origin) "
            "VALUES(?, 'entity', 'observed', 'krw', 'ledger', 'module');",
            (tok,))


def _entity_of(conn, name):
    """The 039 resolution, read-only: exactly one active entity labelled
    with the instrument's name, else None."""
    row = conn.execute(
        "SELECT MIN(e.id), COUNT(DISTINCT e.id) FROM entity e "
        "JOIN label l ON l.namespace='entity' AND l.token=e.token "
        "WHERE e.status='active' AND l.display=?;", (name,)).fetchone()
    return row[0] if row and row[1] == 1 else None


def _methods(conn):
    rows = conn.execute(
        "SELECT id, name, kind, aliases, billing_day, linked_account_id "
        "FROM pay_method ORDER BY id;").fetchall()
    out = []
    for r in rows:
        out.append({'method_id': r[0], 'name': r[1], 'kind': r[2],
                    'aliases': [a.strip() for a in (r[3] or '').split(',')
                                if a.strip()],
                    'billing_day': r[4], 'linked_account_id': r[5],
                    'entity_id': _entity_of(conn, r[1])})
    return out


def resolve(conn, ref):
    """ref -> one pay_method dict. Order: id, name, alias, entity token /
    label. Refused on 0 or several (the candidates are named)."""
    s = str(ref or '').strip()
    if not s:
        raise Refused('instrument required')
    ms = _methods(conn)
    for test in (lambda m: s.isdigit() and m['method_id'] == int(s),
                 lambda m: m['name'] == s,
                 lambda m: s in m['aliases']):
        hit = [m for m in ms if test(m)]
        if len(hit) == 1:
            return hit[0]
        if len(hit) > 1:
            raise Refused('ambiguous instrument %r: %s' % (
                s, ', '.join('%d:%s' % (m['method_id'], m['name'])
                             for m in hit)))
    eids = [r[0] for r in conn.execute(
        "SELECT DISTINCT e.id FROM entity e LEFT JOIN label l "
        "ON l.namespace='entity' AND l.token=e.token "
        "WHERE e.status='active' AND (e.token=? OR l.display=?);",
        (s, s)).fetchall()]
    hit = [m for m in ms if m['entity_id'] is not None
           and m['entity_id'] in eids]
    if len(hit) == 1:
        return hit[0]
    if len(hit) > 1:
        raise Refused('ambiguous instrument %r: %s' % (
            s, ', '.join('%d:%s' % (m['method_id'], m['name']) for m in hit)))
    raise Refused('no payment instrument %r (method-list shows them; '
                  'method-add registers one)' % s)


def _seat(K, conn, m):
    """The instrument's entity, accrued the 2.34 way when it has none yet
    (method-add's own find-first, same transaction)."""
    if m['entity_id'] is not None:
        return m['entity_id']
    status, eid = K._method_entity_accrue(conn, m['name'])
    if eid is None:
        raise Refused('instrument %r has no usable entity (%s)'
                      % (m['name'], status))
    return eid


def put_reading(K, conn, m, metric, amount, as_of, prov, source=None,
                due=None):
    ensure_metrics(conn)
    eid = _seat(K, conn, m)
    origin = ORIGIN[prov]
    seq = SEQ[prov]
    qual = ('due:%s' % due.isoformat()) if due else None
    src = source or prov
    old = conn.execute(
        "SELECT id, value, qualifier FROM measure WHERE entity_id=? AND "
        "metric=? AND seq=? AND basis='actual' AND observed_at=? AND "
        "block_id IS NULL AND place_link_id IS NULL;",
        (eid, metric, seq, as_of.isoformat())).fetchone()
    if old:
        conn.execute(
            "UPDATE measure SET value=?, qualifier=?, source=?, "
            "value_origin=? WHERE id=?;",
            (float(amount), qual, src, origin, old[0]))
        action, mid, prev = 'replaced', old[0], int(old[1])
    else:
        mid = conn.execute(
            "INSERT INTO measure(metric, value, unit, qualifier, basis, "
            "entity_id, seq, observed_at, source, value_origin) "
            "VALUES(?,?,'krw',?,'actual',?,?,?,?,?);",
            (metric, float(amount), qual, eid, seq, as_of.isoformat(), src,
             origin)).lastrowid
        action, prev = 'added', None
    return {'action': action, 'measure_id': mid, 'instrument': m['name'],
            'kind': m['kind'], 'entity_id': eid, 'metric': metric,
            'amount': int(amount), 'as_of': as_of.isoformat(),
            'provenance': prov, 'source': src,
            'due': due.isoformat() if due else None, 'previous': prev}


def cash_instrument(K, conn):
    """The cash instrument: the one pay_method kind='cash', registered on
    first use under the instance language's word for cash."""
    rows = [m for m in _methods(conn) if m['kind'] == 'cash']
    if len(rows) > 1:
        raise Refused('several cash instruments: %s -- name one with '
                      'balance-set' % ', '.join(m['name'] for m in rows))
    if rows:
        return rows[0]
    name = K._i18n('ledger.balance.cash_name', 'cash')
    K.method_add(name, 'cash', conn=conn)
    return resolve(conn, name)


# -- reads --------------------------------------------------------------------
def _readings(conn, eid, metric):
    """Latest reading per provenance on this entity -> {'hand': r, 'auto': r}."""
    out = {}
    for prov, origin in ORIGIN.items():
        r = conn.execute(
            "SELECT id, value, observed_at, source, qualifier FROM measure "
            "WHERE entity_id=? AND metric=? AND basis='actual' AND "
            "value_origin=? ORDER BY substr(observed_at,1,10) DESC, id DESC "
            "LIMIT 1;", (eid, metric, origin)).fetchone()
        if r:
            q = r[4] or ''
            out[prov] = {'measure_id': r[0], 'amount': int(r[1]),
                         'as_of': r[2][:10], 'source': r[3],
                         'provenance': prov,
                         'due': q[4:] if q.startswith('due:') else None}
    return out


def _primary(rd):
    """Newest as-of wins; a tie goes to hand (the owner's own word)."""
    h, a = rd.get('hand'), rd.get('auto')
    if h and a:
        return (a, h) if a['as_of'] > h['as_of'] else (h, a)
    return (h or a), None


def _flow(conn, eids, after, until):
    """Canonical ledger payments plus non-ledger deltas in (after, until].

    A spend shadow is a projection, not another payment. Old imports can
    leave orphan shadows behind; neither these nor the current shadows are
    added beside spend_entry. Transfer-only card bill rows still affect the
    paying account even though they are never consumption.
    """
    if not eids:
        return {'inflow': 0, 'outflow': 0, 'n': 0}
    q = ','.join('?' * len(eids))
    r = conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN d.amount>0 THEN d.amount END),0), "
        "COALESCE(SUM(CASE WHEN d.amount<0 THEN -d.amount END),0), COUNT(*) "
        "FROM block_delta d JOIN block b ON b.id=d.block_id "
        "WHERE d.resource='money' AND d.entity_id IN (%s) "
        "AND b.ulid NOT LIKE 'x-spend-%%' "
        "AND substr(COALESCE(b.date, b.start_at),1,10) > ? "
        "AND substr(COALESCE(b.date, b.start_at),1,10) <= ?;" % q,
        list(eids) + [after, until]).fetchone()
    names = set()
    from ledger_v6 import _json_list
    for method in _methods(conn):
        if method['entity_id'] in eids:
            names.add(method['name'])
            aliases = conn.execute('SELECT aliases FROM pay_method WHERE id=?', (method['method_id'],)).fetchone()
            names.update(_json_list(aliases[0]) if aliases else [])
    inflow, outflow, count = int(r[0]), int(r[1]), r[2]
    if names:
        marks = ','.join('?' for _ in names)
        for amount, kind, cls in conn.execute(
                "SELECT amount,kind,COALESCE(class,'spend') FROM spend_entry WHERE method IN ("+marks+
                ") AND date>? AND date<=?", [*sorted(names), after, until]):
            if cls == 'meal_point':
                continue
            signed = -amount if kind == 'expense' else amount
            inflow += max(signed, 0)
            outflow += max(-signed, 0)
            count += 1
    return {'inflow': inflow, 'outflow': outflow, 'n': count}


def _class(kind):
    if kind in BALANCE_KINDS:
        return 'balance'
    if kind in BILL_KINDS:
        return 'bill'
    if kind in VIA_KINDS:
        return 'via'
    return 'other'


def show(conn, today, stale_days=STALE_DAYS):
    ms = _methods(conn)
    by_id = {m['method_id']: m for m in ms}
    out = []
    for m in ms:
        cls = _class(m['kind'])
        row = {'instrument': m['name'], 'method_id': m['method_id'],
               'kind': m['kind'], 'class': cls, 'entity_id': m['entity_id']}
        if cls == 'via':
            la = by_id.get(m['linked_account_id'])
            row['linked_account'] = la['name'] if la else None
            out.append(row)
            continue
        if cls == 'other':
            out.append(row)
            continue
        metric = METRIC_BALANCE if cls == 'balance' else METRIC_BILL
        rd = (_readings(conn, m['entity_id'], metric)
              if m['entity_id'] is not None else {})
        prim, other = _primary(rd)
        if prim is None:
            row.update(state='missing', reading=None)
            out.append(row)
            continue
        age = (today - datetime.date.fromisoformat(prim['as_of'])).days
        if cls == 'balance':
            state = 'stale' if age > stale_days else 'fresh'
        else:
            # a statement is current until its payment date has passed
            state = ('stale' if (prim['due'] or prim['as_of'])
                     < today.isoformat() else 'fresh')
        row.update(reading=prim, age_days=age, state=state)
        if other and other['amount'] != prim['amount']:
            row['other'] = other
        if cls == 'balance':
            eids = [m['entity_id']]
            via = [x['name'] for x in ms if x['kind'] in VIA_KINDS
                   and x['linked_account_id'] == m['method_id']]
            eids += [x['entity_id'] for x in ms if x['kind'] in VIA_KINDS
                     and x['linked_account_id'] == m['method_id']
                     and x['entity_id'] is not None]
            f = _flow(conn, eids, prim['as_of'], today.isoformat())
            f['via'] = via
            row['flow_since'] = f
            row['estimated'] = prim['amount'] + f['inflow'] - f['outflow']
            # card bills paid from this account inside the window: a transfer
            # to an own card, never recorded as spend -- named, not subtracted
            row['bills_due_unpaid_in_flow'] = [
                {'card': c['name'], 'amount': b['amount'], 'due': b['due']}
                for c in ms if c['kind'] in BILL_KINDS
                and c['linked_account_id'] == m['method_id']
                and c['entity_id'] is not None
                for b in [_primary(_readings(conn, c['entity_id'],
                                             METRIC_BILL))[0]]
                if b and b['due'] and prim['as_of'] < b['due']
                <= today.isoformat()]
        else:
            f = _flow(conn, [m['entity_id']], prim['as_of'],
                      today.isoformat())
            row['spend_since_statement'] = f
        out.append(row)
    return out


def stale(conn, today, stale_days=STALE_DAYS):
    return [r for r in show(conn, today, stale_days)
            if r['class'] == 'balance' and r.get('state') in ('missing',
                                                              'stale')]


def weekly(K, conn, today, stale_days=STALE_DAYS, claim_missing=False):
    """Weekly consent gate; missing readings are a separate, once-only offer.

    Persist per method, so adding an account cannot re-offer old accounts.
    Preview is read-only; the gather side claims only for a real marker.
    """
    result = {'onboarded': False, 'stale': [], 'missing_once': []}
    if not K._manage_probe(conn, K._manage_root(), {}, 'ledger_onboarded'):
        return result
    result['onboarded'] = True
    result.update(aggregator=None, accounts=[])
    if claim_missing:
        conn.execute('BEGIN IMMEDIATE')
    rows = stale(conn, today, stale_days)
    methods = _methods(conn)
    result['accounts'] = [
        {'id': m['method_id'], 'name': m['name']}
        for m in methods if m['kind'] == 'account'
    ]
    # The onboarding flow has already validated this source against the
    # region's sources.json aggregator list. Recheck against that same data
    # before putting it on the renderer feed; old or hand-edited state must
    # not select the one-capture branch.
    try:
        root = K._manage_root()
        with open(os.path.join(root, 'files', 'ledger', 'onboarding.json'),
                  encoding='utf-8') as fh:
            state = json.load(fh)
        source = ((state.get('answers') or {}).get('source') or {}).get('source')
        with open(os.path.join(os.path.dirname(__file__), '..', 'service',
                               'kit_life', 'ledger', 'sources.json'),
                  encoding='utf-8') as fh:
            source_data = json.load(fh)
        regions = list(source_data.get('regions', {}).values())
        aggregators = {app for region in regions + [source_data.get('default', {})]
                       for app in region.get('aggregators', [])}
        if source in aggregators:
            result['aggregator'] = source
    except (OSError, TypeError, ValueError):
        pass
    since = (today - datetime.timedelta(days=90)).isoformat()
    for row in rows:
        if row['state'] == 'missing':
            key = 'ledger.balance.missing_asked.%d' % row['method_id']
            if conn.execute('SELECT 1 FROM config WHERE key=?', (key,)).fetchone():
                continue
            result['missing_once'].append(row)
            if claim_missing:
                conn.execute('INSERT INTO config(key,value) VALUES(?,?)',
                             (key, today.isoformat()))
            continue
        eids = [row['entity_id']] + [m['entity_id'] for m in methods
                if m['kind'] in VIA_KINDS
                and m['linked_account_id'] == row['method_id']
                and m['entity_id'] is not None]
        row['usage_count'] = conn.execute(
            "SELECT COUNT(DISTINCT b.id) FROM block_delta d "
            "JOIN block b ON b.id=d.block_id WHERE d.resource='money' "
            "AND d.entity_id IN (%s) "
            "AND substr(COALESCE(b.date,b.start_at),1,10) BETWEEN ? AND ?"
            % ','.join('?' * len(eids)), eids + [since, today.isoformat()]
        ).fetchone()[0]
        result['stale'].append(row)
    result['stale'].sort(key=lambda r: (-r['usage_count'], r['method_id']))
    return result


def _month_bounds(month):
    y, mo = int(month[:4]), int(month[5:7])
    lo = datetime.date(y, mo, 1)
    hi = datetime.date(y + (mo == 12), mo % 12 + 1, 1)
    return lo, hi


def bill_month(conn, month):
    lo, hi = _month_bounds(month)
    out = []
    for m in _methods(conn):
        if m['kind'] not in BILL_KINDS:
            continue
        row = {'instrument': m['name'], 'method_id': m['method_id'],
               'month': month}
        bill = None
        if m['entity_id'] is not None:
            cands = []
            for prov, origin in ORIGIN.items():
                for r in conn.execute(
                        "SELECT value, observed_at, qualifier, source FROM "
                        "measure WHERE entity_id=? AND metric=? AND "
                        "basis='actual' AND value_origin=? ORDER BY "
                        "observed_at DESC, id DESC;",
                        (m['entity_id'], METRIC_BILL, origin)).fetchall():
                    q = r[2] or ''
                    key = q[4:] if q.startswith('due:') else r[1][:10]
                    if lo.isoformat() <= key < hi.isoformat():
                        cands.append({'amount': int(r[0]),
                                      'as_of': r[1][:10],
                                      'due': q[4:] if q.startswith('due:')
                                      else None,
                                      'source': r[3], 'provenance': prov})
            hand = [c for c in cands if c['provenance'] == 'hand']
            bill = (hand or cands or [None])[0]
        if bill:
            row.update(basis='bill', amount=bill['amount'], reading=bill)
        else:
            f = _flow(conn, [m['entity_id']] if m['entity_id'] else [],
                      (lo - datetime.timedelta(days=1)).isoformat(),
                      (hi - datetime.timedelta(days=1)).isoformat())
            spent = f['outflow'] - f['inflow']
            # installments (ledger_installment.py): the purchase sits in the
            # recorded spend at full amount -- it leaves the one-off part and
            # the month's charge comes in. No installment -> unchanged.
            import ledger_installment
            inst = ledger_installment.month_charges(conn, m, month)
            if inst['charges'] or inst['purchases_n']:
                one_off = spent - inst['purchases']
                row.update(basis='estimate', amount=one_off + inst['charges'],
                           one_off=one_off, installments=inst, flow=f)
            else:
                row.update(basis='recorded_spend', amount=spent, flow=f)
        out.append(row)
    return out


# -- owner-language render ------------------------------------------------------
def _won(K, n):
    return K._i18n('ledger.balance.krw', '{n} KRW').format(n=comma(n))


def _md(K, iso):
    d = datetime.date.fromisoformat(iso[:10])
    month, day = month_day(d)
    return K._i18n('ledger.balance.md', '{m}/{d}').format(m=month, d=day)


def _prov(K, prov):
    return K._i18n('ledger.balance.prov.' + prov, prov)


def say_show(K, rows):
    t = K._i18n
    lines = []
    for r in rows:
        if r['class'] not in ('balance', 'bill'):
            continue
        rd = r.get('reading')
        if rd is None:
            lines.append(t('ledger.balance.say.missing_%s' % r['class'],
                           '').format(name=r['instrument']))
            continue
        key = 'ledger.balance.say.%s' % ('reading' if r['class'] == 'balance'
                                         else 'bill')
        line = t(key, '').format(
            name=r['instrument'], amount=_won(K, rd['amount']),
            date=_md(K, rd['as_of']), prov=_prov(K, rd['provenance']),
            due=_md(K, rd['due']) if rd.get('due') else '-')
        if r['state'] == 'stale':
            line += t('ledger.balance.say.stale_%s' % r['class'], '').format(
                days=r['age_days'])
        if r.get('other'):
            o = r['other']
            line += t('ledger.balance.say.other', '').format(
                prov=_prov(K, o['provenance']), amount=_won(K, o['amount']),
                date=_md(K, o['as_of']))
        lines.append(line)
        if r['class'] == 'balance' and r['flow_since']['n']:
            f = r['flow_since']
            lines.append(t('ledger.balance.say.estimate', '').format(
                amount=_won(K, r['estimated']), date=_md(K, rd['as_of']),
                inflow=_won(K, f['inflow']), outflow=_won(K, f['outflow']),
                n=f['n']))
        for b in r.get('bills_due_unpaid_in_flow') or ():
            lines.append(t('ledger.balance.say.bill_not_in', '').format(
                card=b['card'], amount=_won(K, b['amount']),
                due=_md(K, b['due'])))
    if any(r['class'] == 'balance' and r.get('flow_since', {}).get('n')
           for r in rows):
        lines.append(t('ledger.balance.say.transfer_note', ''))
    return lines


def say_bill_month(K, rows):
    t = K._i18n
    lines = []
    for r in rows:
        if r['basis'] == 'bill':
            rd = r['reading']
            lines.append(t('ledger.balance.say.month_bill', '').format(
                name=r['instrument'], amount=_won(K, r['amount']),
                due=_md(K, rd['due']) if rd['due'] else _md(K, rd['as_of']),
                prov=_prov(K, rd['provenance'])))
        elif r['basis'] == 'estimate':
            i = r['installments']
            lines.append(t('ledger.balance.say.month_estimate', '').format(
                name=r['instrument'], amount=_won(K, r['amount']),
                one_off=_won(K, r['one_off']),
                n=r['flow']['n'] - i['purchases_n'],
                inst=_won(K, i['charges']), k=i['n']))
        else:
            lines.append(t('ledger.balance.say.month_spend', '').format(
                name=r['instrument'], amount=_won(K, r['amount']),
                n=r['flow']['n']))
    if rows and all(r['basis'] == 'bill' for r in rows):
        lines.append(t('ledger.balance.say.month_total_bill', '').format(
            amount=_won(K, sum(r['amount'] for r in rows))))
    elif rows:
        lines.append(t('ledger.balance.say.month_total_mixed', '').format(
            amount=_won(K, sum(r['amount'] for r in rows))))
    return lines


# -- CLI ----------------------------------------------------------------------
def _usage(K, verb):
    K._err('usage: kit_life.sh %s -- see database/ledger_balance.py' % verb)


def _prov_opts(K, argv):
    argv, auto = K._pop_flag(argv, '--auto')
    argv, src = K._pop_opt(argv, '--source')
    if src and not auto:
        raise Refused('--source names an automatic reader: use with --auto')
    return argv, ('auto' if auto else 'hand'), src


def _out(as_json, res, lines):
    if as_json:
        print(json.dumps(res, ensure_ascii=False))
    else:
        for ln in lines:
            print(ln)


def _set_line(r):
    return '%s\t#%d\t%s\t%s\t%d\tas_of=%s\t%s%s%s' % (
        r['action'].upper(), r['measure_id'], r['metric'], r['instrument'],
        r['amount'], r['as_of'], r['provenance'],
        ('\tdue=%s' % r['due']) if r['due'] else '',
        ('\tprevious=%d' % r['previous']) if r['previous'] is not None
        else '')


def cli(K, verb, argv):
    argv, as_json = K._pop_flag(list(argv), '--json')
    conn = K.get_conn()
    try:
        try:
            if verb == 'balance-del':
                conn.execute('BEGIN IMMEDIATE')
            _run(K, conn, verb, argv, as_json)
            if conn.in_transaction:
                conn.commit()
        except (Refused, sqlite3.IntegrityError) as e:
            if conn.in_transaction:
                conn.rollback()
            K._err('%s: %s' % (verb, e))
    finally:
        conn.close()


def _run(K, conn, verb, argv, as_json):
    if verb == 'balance-del':
        if len(argv) != 1 or not argv[0].isdigit():
            _usage(K, verb)
        mid = int(argv[0])
        r = conn.execute("SELECT id FROM measure WHERE id=? AND metric IN (?,?) "
                         "AND basis='actual' AND entity_id IS NOT NULL AND block_id IS NULL AND place_link_id IS NULL",
                         (mid, METRIC_BALANCE, METRIC_BILL)).fetchone()
        if not r:
            raise Refused('balance reading not found')
        conn.execute('DELETE FROM measure WHERE id=?', (mid,))
        print(json.dumps(dict(deleted=mid)))
        return
    if verb in ('balance-set', 'cash-set', 'card-bill-set'):
        argv, prov, src = _prov_opts(K, argv)
        argv, as_of = K._pop_opt(argv, '--as-of')
        argv, due = K._pop_opt(argv, '--due')
        as_of = _date(as_of, '--as-of') or K.person_today_local()
        if verb == 'cash-set':
            if len(argv) != 1 or due:
                _usage(K, verb)
            m = cash_instrument(K, conn)
            metric = METRIC_BALANCE
        else:
            if len(argv) != 2:
                _usage(K, verb)
            m = resolve(conn, argv[0])
            cls = _class(m['kind'])
            if verb == 'balance-set':
                if due:
                    _usage(K, verb)
                if cls == 'via':
                    la = [x for x in _methods(conn)
                          if x['method_id'] == m['linked_account_id']]
                    raise Refused(
                        '%s is a debit card: its money is in %s -- record '
                        'the balance there' % (m['name'], la[0]['name']
                                               if la else 'its account'))
                if cls == 'bill':
                    raise Refused('%s is a credit card: record its statement '
                                  'with card-bill-set' % m['name'])
                if cls != 'balance':
                    raise Refused('%s (%s) carries no balance'
                                  % (m['name'], m['kind']))
                metric = METRIC_BALANCE
            else:
                if cls != 'bill':
                    raise Refused('%s is not a credit card (%s): a bill is '
                                  'a credit-card statement' % (m['name'],
                                                              m['kind']))
                if not due:
                    raise Refused('--due YYYY-MM-DD required (the payment '
                                  'date on the statement)')
                metric = METRIC_BILL
        due_d = _date(due, '--due') if verb == 'card-bill-set' else None
        if as_of > K.person_today_local():
            raise Refused('--as-of is in the future: %s' % as_of)
        amount = _amount(argv[-1], allow_negative=(verb == 'balance-set'))
        res = put_reading(K, conn, m, metric, amount, as_of, prov, src, due_d)
        _out(as_json, res, [_set_line(res)])
        return
    argv, today = K._pop_opt(argv, '--today')
    today = _today(K, today)
    argv, sd = K._pop_opt(argv, '--stale-days')
    if sd is not None and not str(sd).isdigit():
        _usage(K, verb)
    sd = int(sd) if sd is not None else STALE_DAYS
    argv, say = K._pop_flag(argv, '--say')
    if verb == 'balance-show':
        if argv:
            _usage(K, verb)
        rows = show(conn, today, sd)
        if say and not as_json:
            _out(False, None, say_show(K, rows) or ['NO_INSTRUMENTS'])
            return
        lines = []
        for r in rows:
            if r['class'] in ('via', 'other'):
                lines.append('%s\t%s\t%s%s' % (
                    r['class'].upper(), r['instrument'], r['kind'],
                    ('\t-> %s' % r['linked_account'])
                    if r.get('linked_account') else ''))
                continue
            rd = r.get('reading')
            if rd is None:
                lines.append('MISSING\t%s\t%s\tno reading' % (
                    r['instrument'], r['class']))
                continue
            ln = '%s\t%s\t%s\t%d\tas_of=%s\tage=%dd\t%s' % (
                r['state'].upper(), r['instrument'], r['class'],
                rd['amount'], rd['as_of'], r['age_days'], rd['provenance'])
            if rd.get('due'):
                ln += '\tdue=%s' % rd['due']
            if r.get('other'):
                ln += '\tother=%s:%d@%s' % (r['other']['provenance'],
                                            r['other']['amount'],
                                            r['other']['as_of'])
            if r['class'] == 'balance':
                f = r['flow_since']
                ln += ('\tflow_since=+%d/-%d(%d)\testimated=%d' % (
                    f['inflow'], f['outflow'], f['n'], r['estimated']))
                for b in r['bills_due_unpaid_in_flow']:
                    ln += '\tbill_not_in_flow=%s:%d@%s' % (
                        b['card'], b['amount'], b['due'])
            else:
                f = r['spend_since_statement']
                ln += '\tspend_since_statement=%d(%d)' % (
                    f['outflow'] - f['inflow'], f['n'])
            lines.append(ln)
        _out(as_json, rows, lines or ['NO_INSTRUMENTS'])
    elif verb == 'balance-stale':
        argv, tsv = K._pop_flag(argv, '--tsv')
        argv, for_weekly = K._pop_flag(argv, '--weekly')
        argv, claim = K._pop_flag(argv, '--claim-missing')
        if argv or (claim and not for_weekly) or (for_weekly and (not as_json or tsv)):
            _usage(K, verb)
        if for_weekly:
            print(json.dumps(weekly(K, conn, today, sd, claim), ensure_ascii=False))
            return
        rows = stale(conn, today, sd)
        if tsv and not as_json:
            for r in rows:
                rd = r.get('reading')
                print('%s\t%s\t%s\t%s' % (
                    r['instrument'], rd['as_of'] if rd else '-',
                    r['age_days'] if rd else '-',
                    r['estimated'] if rd else '-'))
            return
        _out(as_json, rows, ['%s\t%s\t%s' % (
            r['state'].upper(), r['instrument'],
            r['reading']['as_of'] if r.get('reading') else '-')
            for r in rows] or ['NONE_STALE'])
    elif verb == 'card-bill-month':
        if len(argv) > 1 or (argv and not _MONTH_RE.match(argv[0])):
            _usage(K, verb)
        month = argv[0] if argv else today.isoformat()[:7]
        rows = bill_month(conn, month)
        if say and not as_json:
            _out(False, None, say_bill_month(K, rows) or ['NO_CREDIT_CARDS'])
            return
        _out(as_json, rows, ['%s\t%s\t%s\t%d%s' % (
            r['basis'].upper(), r['instrument'], r['month'], r['amount'],
            ('\tdue=%s\t%s' % (r['reading']['due'] or '-',
                               r['reading']['provenance']))
            if r['basis'] == 'bill'
            else '\tnot_the_bill\tone_off=%d\tinstallments=%d(%d)' % (
                r['one_off'], r['installments']['charges'],
                r['installments']['n'])
            if r['basis'] == 'estimate'
            else '\tnot_the_bill\tn=%d' % r['flow']['n'])
            for r in rows] or ['NO_CREDIT_CARDS'])
    else:
        _usage(K, verb)
