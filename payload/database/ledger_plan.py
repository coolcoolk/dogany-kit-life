# -*- coding: utf-8 -*-
"""Rehearsal plan arithmetic. No imported category decides a plan bucket.

Reads landed, reimbursement-netted payments. Owner facts live in preferences;
the service holds an uncommitted plan until the final owner confirmation.
"""
import calendar
import collections
import datetime
import json
import re
from pathlib import Path

from money_format import comma, man_one_decimal, man_whole

import ledger_obligation as E
import ledger_actual as A
import ledger_diag as D
import ledger_history as H
import ledger_loan as L
import ledger_review as R
import ledger_window as W
import ledger_names as N
import ledger_lifecycle as LC

# Language-specific matching data is shared across display locales.
_LEXICON = json.loads(Path(__file__).with_name('ledger_plan_lexicon.json').read_text(encoding='utf-8'))

YEARLY_REMINDER_DAYS = 7
TOPIC = 'ledger_plan_fact'
BUCKETS = ('fixed', 'subscriptions', 'big', 'meals', 'alcohol', 'delivery',
           'cafe', 'coupang', 'clothes', 'convenience', 'food_cafe', 'food_other', 'leisure', 'other', 'unknown')
# Owner 2026-11-02: food/drink stays in its category at any amount.
BIG_EXCLUDED = frozenset(('fixed', 'subscriptions', 'meals', 'alcohol', 'delivery',
                          'cafe', 'food_cafe', 'food_other', 'convenience'))
TRANSFER = _LEXICON['TRANSFER']
TRANSFER_ANSWERS = ('debt', 'settlement', 'gift', 'unknown')
# What an unmatched receipt was, as the owner meant it (data kinds, not buttons):
#   refund                 money back from a shop; nets the spend bucket it names
#   expense_reimbursement  someone paid back a cost the owner fronted; nets the
#                          spend bucket of that cost, never income
#   cash_exchange          cash handed over and paid back by transfer; neutral,
#                          neither income nor a spend reduction
REFUND_KINDS = ('refund', 'expense_reimbursement', 'cash_exchange')
HIDDEN_BUCKETS = dict(food='meals', food_cafe='food_cafe', cafe_snack='cafe', shopping='clothes', subscription='subscriptions',
                      leisure='leisure', housing='fixed', tax='fixed', unknown_merchant='unknown')
LEVERS = ('delivery', 'alcohol', 'cafe', 'coupang', 'clothes', 'convenience')
# Business kind first, then our bucket. Never use import categories, amounts
# or a user's local merchant names as business evidence. Instance facts precede
# these generic business words and widely used chains/subscription/bill rails.
KIND_TOPIC = 'ledger_merchant_kind'
KIND_BUCKETS = dict(subscription='subscriptions', recurring_bill='fixed',
                    delivery_service='delivery', alcohol='alcohol',
                    cafe_bakery='cafe', convenience_store='convenience',
                    grocery='food_other', restaurant='meals', marketplace='coupang',
                    clothing_household='clothes', recreation='leisure',
                    transport_health='other', payment_processor='unknown')
MERCHANT_RULES = _LEXICON['MERCHANT_RULES']


def merchant_key(name):
    # Exact identity apart from case/whitespace; never merge branches or PGs.
    return ' '.join(name.split()).casefold()


def merchant_kinds(c):
    return {r[0]: dict(json.loads(r[1]), source=r[2], recorded_at=r[3])
            for r in c.execute("SELECT item,detail,source,recorded_at FROM ledger_preference "
                               "WHERE topic=? AND status='active'", (KIND_TOPIC,))}


def merchant_kind_set(c, name, kind, source, evidence):
    if not isinstance(name, str) or not name.strip():
        raise ValueError('merchant name required')
    if kind not in (*KIND_BUCKETS, 'unresolved'):
        raise ValueError('invalid merchant kind')
    if source not in ('web', 'owner'):
        raise ValueError('merchant kind source must be web or owner')
    if not isinstance(evidence, str) or not evidence.strip():
        raise ValueError('lookup evidence or owner words required')
    if source == 'web' and kind != 'unresolved' and not re.match(r'https?://\S+', evidence):
        raise ValueError('web merchant kind requires a source URL')
    key = merchant_key(name)
    detail = dict(name=name.strip(), kind=kind, evidence=evidence.strip())
    c.execute("INSERT INTO ledger_preference(ulid,item,polarity,status,source,recorded_at,topic,detail) "
              "VALUES(?,?,'negotiable','active',?,?,?,?) ON CONFLICT(ulid) DO UPDATE SET "
              "detail=excluded.detail,status='active',source=excluded.source,recorded_at=excluded.recorded_at",
              ('merchant-kind:'+key, key, source, datetime.datetime.now().isoformat(),
               KIND_TOPIC, json.dumps(detail, ensure_ascii=False)))
    return dict(saved=True, **detail, source=source)


def merchant_kind(name, cache=None):
    fact = (cache or {}).get(merchant_key(name or ''))
    if fact:
        return fact['kind']
    for kind, pattern in MERCHANT_RULES:
        if re.search(pattern, name or '', re.I):
            return kind
    return None


def _subscription_fact(c, meta, key, fixed_ids):
    fact = dict(meta.get('subscription:'+key, {}))
    if fact.get('cycle') or fact.get('one_off'):
        return fact
    cycles = set()
    for fid in fixed_ids:
        row = c.execute('SELECT facts FROM contract_record WHERE fixed_id=? ORDER BY updated_at DESC,id DESC LIMIT 1', (fid,)).fetchone()
        cycle = json.loads(row[0]).get('cycle') if row else None
        if cycle in R.CYCLE_MONTHS:
            cycles.add(cycle)
    if len(cycles) == 1:
        fact['cycle'] = cycles.pop()
    return fact


def _yearly_renewal(paid, today):
    anchor = datetime.date.fromisoformat(paid)
    year = anchor.year+1
    while True:
        renewal = anchor.replace(year=year,day=min(anchor.day,calendar.monthrange(year,anchor.month)[1])).isoformat()
        if renewal > today:
            return renewal
        year += 1

def _subscription_identities(c):
    links = collections.defaultdict(set)
    for raw, fid in c.execute('SELECT DISTINCT name,stream_id FROM spend_entry WHERE stream_id IS NOT NULL'):
        links[merchant_key(raw)].add('stream:'+str(fid))
    for item,detail in c.execute("SELECT item,detail FROM ledger_preference WHERE topic=? AND status='active' AND item LIKE 'subscription:%'",(TOPIC,)):
        for raw in json.loads(detail).get('merchant_names',[]):
            links[merchant_key(raw)].add(item[13:])
    return {raw:next(iter(ids)) for raw,ids in links.items() if len(ids)==1}


def _subscription_key(name, context=None, stream_id=None):
    if stream_id:
        return 'stream:'+str(stream_id)
    identities = _subscription_identities(context) if hasattr(context,'execute') else context or {}
    return identities.get(merchant_key(name), merchant_key(name))


def subscription_aliases(plan):
    """Read old opaque keys through their saved receipt evidence, without a dictionary."""
    aliases = {}
    for field in ('subscriptions', 'current_subscriptions', 'subscription_oneoffs'):
        for sub in plan.get(field, []):
            raw = sub.get('last', {}).get('name') or sub.get('merchant') or sub.get('name')
            if raw and sub.get('key'):
                fixed_ids=sub.get('fixed_ids',[])
                fid=(fixed_ids or [sub.get('last',{}).get('stream_id')])[0] if len(fixed_ids)<=1 else None
                aliases[sub['key']] = _subscription_key(raw,stream_id=fid)
    return aliases


def migrate_subscriptions(plan, targets=None, aliases=None):
    """Normalize read snapshots only. The source facts and schema stay intact."""
    aliases = dict(aliases or {}, **subscription_aliases(plan))
    for field in ('subscriptions', 'current_subscriptions', 'subscription_oneoffs'):
        for sub in plan.get(field, []):
            old = sub.get('key')
            sub['key'] = aliases.get(old, old)
            raw = sub.get('last', {}).get('name') or sub.get('name')
            if raw and not sub.get('display_name_source'):
                sub['display_name'] = raw
    meta = plan.get('facts', {})
    for key, fact in list(meta.items()):
        if key.startswith('subscription:'):
            meta.setdefault('subscription:'+aliases.get(key[13:], key[13:]), fact)
    if targets is not None:
        for field in ('subscription_targets', 'subscription_quotes', 'subscription_revisions', 'subscription_effective_from'):
            if field in targets:
                targets[field] = {aliases.get(key, key): value for key, value in targets[field].items()}
    return aliases


def is_ai_subscription(sub):
    return sub.get('category') == 'ai_tool'

def _subscription_fixed_ids(c, key, members=(), last=None):
    ids = sorted({row['stream_id'] for row in members if row.get('stream_id')})
    if not ids and last:
        hit = c.execute('SELECT stream_id FROM spend_entry WHERE id=?', (last['id'],)).fetchone()
        if hit and hit[0]: ids = [hit[0]]
    if not ids:
        # Classification refreshes can detach an annual payment. Its explicit
        # contract identity survives; accept only one unambiguous linked stream.
        carriers = {row[0] for row in c.execute(
            'SELECT fixed_id FROM contract_record WHERE record_key=? AND fixed_id IS NOT NULL',
            ('ledger:yearly:'+key,))}
        if len(carriers)==1: ids = list(carriers)
    return ids


def facts(c):
    result = {}
    for key, detail, recorded_at in c.execute(
            "SELECT item,detail,recorded_at FROM ledger_preference WHERE topic=? AND status='active'", (TOPIC,)):
        fact = json.loads(detail)
        if key.startswith('subscription:') and fact.get('cycle') == 'yearly' and recorded_at:
            fact.setdefault('stated_on', recorded_at[:10])
        result[key] = fact
    identities=_subscription_identities(c)
    aliases = {}
    for fact in result.values():
        if isinstance(fact.get('plan'), dict):
            aliases.update(subscription_aliases(fact['plan']))
            migrate_subscriptions(fact['plan'], fact.get('targets'))
    linked = collections.defaultdict(set)
    for fixed_name, raw in c.execute('SELECT f.name,s.name FROM fixed_cost f JOIN spend_entry s ON s.stream_id=f.id'):
        linked[merchant_key(fixed_name)].add(_subscription_key(raw,identities))
    for key, fact in list(result.items()):
        if not key.startswith('subscription:'):
            continue
        old = key[13:]
        candidates = linked.get(merchant_key(old), set())
        identity = aliases.get(old) or identities.get(merchant_key(old)) or (next(iter(candidates)) if len(candidates)==1 else merchant_key(old))
        if identity:
            result.setdefault('subscription:'+identity, fact)
    result['_subscription_identities']=identities
    # Older settlement answers carried guessed links, never duplicate proof.
    # Sanitize the read snapshot without rewriting the owner's source facts.
    for fact in result.values():
        if fact.get('answer')=='settlement': fact['settles']=None
    return result


def put(c, key, data):
    if key.startswith('item:') and 'module' not in data:
        # Later budget/review annotations must not erase a 사용처 link.
        prior = facts(c).get(key, {})
        data = dict({k: prior[k] for k in ('module', 'inferred', 'area_module', 'area_inferred')
                     if k in prior}, **data)
    c.execute("INSERT INTO ledger_preference(ulid,item,polarity,status,source,recorded_at,topic,detail) "
              "VALUES(?,?,'negotiable','active','owner',?,?,?) ON CONFLICT(ulid) DO UPDATE SET "
              "detail=excluded.detail,status='active',recorded_at=excluded.recorded_at",
              ('plan:'+key, key, datetime.datetime.now().isoformat(), TOPIC, json.dumps(data, ensure_ascii=False)))


def bucket(row, meta=None, cache=None):
    fact = (meta or {}).get('item:'+str(row['id']), {})
    if fact.get('answer')=='settlement': return 'other'
    if fact.get('bucket') in BUCKETS:
        return fact['bucket']
    if (meta or {}).get('subscription:'+_subscription_key(row['name'], (meta or {}).get('_subscription_identities'), row.get('stream_id')), {}).get('kind')=='subscription':
        return 'subscriptions'
    linked = A.module_fact(row, meta or {})
    if linked:
        return HIDDEN_BUCKETS.get(A.MODULE_CATEGORIES.get(linked['module']),
                                  'unknown' if linked['module']=='unknown' else 'other')
    override = row.get('category_override')
    if override:
        return dict(food='meals', food_cafe='food_cafe', cafe_snack='cafe', shopping='clothes', subscription='subscriptions',
                    leisure='leisure', housing='fixed', tax='fixed', unknown_merchant='unknown').get(override[0], 'other')
    return KIND_BUCKETS.get(merchant_kind(row['name'], cache), 'unknown')


def _roster_role(e):
    """The roster's purpose line: the owner's word (or an owner-set account
    role a card inherits) as is; otherwise a card's guess comes from its own
    spending, never from an inferred role of the account that pays its bill
    (owner tobe 2026-11-03: a daily-spending card reads living, not salary)."""
    if not e:
        return None
    if e.get('role_from') in ('owner', 'account') or e.get('kind') not in (
            'credit_card', 'corporate_card', 'debit_card', 'easy_pay'):
        return e.get('role')
    from ledger_profile import infer_role
    return infer_role(dict(e, linked_spent=0))


def _payments(c, today):
    return [r for r in D._month_rows(c, today[:7]) if r['date'] <= today]


def _matched_payments(c, payments):
    """Describe durable allocations, never discover matches from equal amounts."""
    ids = {r['id'] for r in payments}
    edges = D.rows(c, 'SELECT a.*,s.date,s.name,s.amount AS payment,t.name AS sender,t.date AS receipt_date '
                   'FROM ledger_receipt_allocation a JOIN spend_entry s ON s.id=a.expense_id '
                   "JOIN spend_entry t ON t.id=a.receipt_id WHERE a.payer='owner' AND a.amount>0 "
                   'ORDER BY s.date,s.id,a.receipt_id')
    edges = [r for r in edges if r['expense_id'] in ids]
    groups = []
    while edges:
        component = [edges.pop(0)]
        expenses = {component[0]['expense_id']}
        receipts = {component[0]['receipt_id']}
        while True:
            joined = [r for r in edges if r['expense_id'] in expenses or r['receipt_id'] in receipts]
            if not joined:
                break
            component.extend(joined)
            expenses.update(r['expense_id'] for r in joined)
            receipts.update(r['receipt_id'] for r in joined)
            edges = [r for r in edges if r not in joined]
        # Each dated session is its own line, even if a receipt covers several days.
        for date in sorted({r['date'] for r in component}):
            rows = [r for r in component if r['date'] == date]
            purchases = {r['expense_id']: r for r in rows}
            total = sum(r['payment'] for r in purchases.values())
            amounts = collections.Counter()
            for r in rows:
                amounts[r['receipt_id']] += r['amount']
            denominators = [n for n in range(2, 9) if total > 0 and all(
                1 <= round(amount*n/total) < n and abs(amount*n-total*round(amount*n/total)) <= n
                for amount in amounts.values()) and sum(round(amount*n/total) for amount in amounts.values()) < n]
            people = min(denominators) if denominators else None
            groups.append(dict(date=date, name=' + '.join(dict.fromkeys(r['name'] for r in purchases.values())),
                               amount=total, received=sum(amounts.values()), people=people,
                               receipts=[dict(id=sid, name=next(r['sender'] for r in rows if r['receipt_id']==sid),
                                              date=next(r['receipt_date'] for r in rows if r['receipt_id']==sid),
                                              amount=amount) for sid, amount in amounts.items()],
                               senders=len({r['sender'] for r in rows}),
                               shares=sum(round(amount*people/total) for amount in amounts.values()) if people else None,
                               expense_ids=sorted(purchases), receipt_ids=sorted(amounts)))
    return sorted(groups, key=lambda r: (r['date'], r['expense_ids']))


USAGE_LABELS = dict(food_cafe='eat', meals='eat', cafe='eat', delivery='eat', alcohol='eat',
                    food_other='shop', convenience='shop', coupang='shop', leisure='hobby')


def _usage(rows, meta, cache, months):
    """Where one instrument's window money went: dominant purposes + top merchants."""
    paid = collections.OrderedDict()
    for r in rows:
        key = (r['date'], merchant_key(r['name']))
        hit = paid.setdefault(key, dict(name=r['name'], amount=0, rows=[]))
        hit['amount'] += r['amount']
        hit['rows'].append(r)
    weights = collections.Counter()
    shops = {}
    for hit in paid.values():
        transfer = '송금' in hit['name']
        name = '송금' if transfer else hit['name']
        shop = shops.setdefault(merchant_key(name), dict(name=name, n=0, amount=0))
        shop['n'] += 1
        shop['amount'] += hit['amount']
        if transfer:
            weights['transfer'] += hit['amount']
            continue
        for r in hit['rows']:
            label = USAGE_LABELS.get(bucket(r, meta, cache))
            if label:
                weights[label] += r['amount']
    ranked = [k for k, _ in sorted(weights.items(), key=lambda kv: (-kv[1], kv[0]))]
    guess = ranked[:1]
    if len(ranked) > 1 and weights[ranked[1]] >= 0.3*weights[ranked[0]]:
        guess = ranked[:2]
    top = sorted(shops.values(), key=lambda s: (-s['amount'], s['name']))[:5]
    return dict(guess=guess, merchants=top, first=int(months[0][5:]) if months else None,
                last=int(months[-1][5:]) if months else None,
                months=list(months))


def _instruments(c, payments, n, meta, cache, months=(), seen=None):
    """Observed purchases by canonical instrument, with named wallets on their own rail.

    No funding totals or imported categories become a purpose. Generic payment
    processors remain in the subsequent recent-payment question.
    """
    from ledger_v6 import Methods, nfkc, _method_layer
    from ledger_purpose_labels import owner_purpose
    methods = Methods(c)
    # Owner 2026-11-03: the roster shows each instrument's purpose again
    # (the owner's word, else the profile's guess, marked as a guess).
    from ledger_profile import methods_seen
    if seen is None:
        seen = {e['method_id']: e for e in methods_seen(c)['methods'] if e.get('method_id') is not None}
    pools = collections.defaultdict(dict)
    cancelled = {sid for pair in A.order_metrics(r for r in payments if r['kind']=='expense' and r['class']=='spend')['cancelled'] for sid in pair}
    for r in payments:
        if r['kind'] != 'expense' or r['class'] != 'spend' or r['amount'] <= 0 or r['id'] in cancelled:
            continue
        mid = methods.names.get(nfkc(r.get('method') or ''))
        if mid is not None:
            pools[methods.canon(mid)][r['id']] = r
        wallet = methods.names.get(nfkc(r['name']))
        if wallet is not None:
            wallet = methods.canon(wallet)
            if _method_layer(methods.by_id[wallet]) == 'wallet':
                pools[wallet][r['id']] = r
    result = []
    for mid, pool in pools.items():
        method = methods.by_id[mid]
        layer = _method_layer(method)
        if layer not in ('card', 'account', 'wallet') or method['kind'] in ('savings', 'securities', 'cash'):
            continue
        rows = list(pool.values())
        known = [r for r in rows if merchant_kind(r['name'], cache) not in (None, 'payment_processor')
                 or r.get('category_override') or meta.get('item:'+str(r['id']), {}).get('bucket') in BUCKETS]
        purpose_streams = []
        if layer == 'wallet' and not known:
            by_stream = collections.defaultdict(list)
            for r in rows:
                # Only the wallet's own opaque entries establish an unknown
                # easy-pay purpose. An unknown merchant alone does not.
                if (methods.names.get(nfkc(r['name'])) is not None
                        and methods.canon(methods.names[nfkc(r['name'])]) == mid
                        and not meta.get('hidden:'+str(r.get('method'))+':'+r['name'])
                        and not meta.get('item:'+str(r['id']))):
                    by_stream[(r.get('method'), r['name'])].append(r)
            purpose_streams = [dict(method=m, name=name, ids=[r['id'] for r in rs])
                               for (m, name), rs in by_stream.items()]
        result.append(dict(id=mid, name=method['name'], kind=method['kind'], layer=layer,
                           purpose=owner_purpose(c, method),
                           role=_roster_role(seen.get(mid)),
                           role_sure=(seen.get(mid) or {}).get('role_from') in ('owner', 'account'),
                           instrument_facts=(seen.get(mid) or {}).get('instrument_facts'),
                           payment_ids=[r['id'] for r in rows],
                           merchants=list(dict.fromkeys(r['name'] for r in known))[:4],
                           count=len(rows), amount=sum(r['amount'] for r in rows),
                           marketplace=_dominant_marketplace(rows, cache),
                           unresolved_amount=sum(r['amount'] for r in rows
                                                 if bucket(r, meta, cache) == 'unknown'),
                           monthly_count=len(rows)/n if n else 0,
                           monthly_amount=round(sum(r['amount'] for r in rows)/n) if n else 0,
                           purpose_streams=purpose_streams,
                           usage=_usage(rows, meta, cache, months)))
    return sorted(result, key=lambda r: r['id'])


def _advance_date(date, months):
    year, month = divmod(int(date[:4])*12+int(date[5:7])-1+months, 12)
    return '%04d-%02d-%02d' % (year, month+1, min(int(date[8:]), calendar.monthrange(year,month+1)[1]))


def _large_history(c, today):
    return D.rows(c, "SELECT * FROM spend_entry WHERE date<=? AND kind='expense' "
                    "AND COALESCE(class,'spend')='spend' AND scope<>'business' AND amount>0", (today,))


def _oneoff_ids(c, today, meta):
    # A person's unpaired transfer is not a monthly shopping habit. Generic
    # transfer rails carry many payees: use amount too when identity is opaque.
    rows = _large_history(c, today)
    counts = collections.Counter((merchant_key(r['name']), r['method'], r['amount']) for r in rows)
    annotations = R.annotations(c)
    identities = collections.Counter((merchant_key(r['name']),r['method']) for r in rows)
    result = set()
    methods = {r['name']:r for r in D.rows(c,'SELECT name,kind,layer FROM pay_method')}
    cache = merchant_kinds(c)
    person_ids = {r[0] for r in c.execute("SELECT spend_id FROM spend_link WHERE entity_kind='person'")}
    for row in rows:
        if row['amount'] < 100000:
            continue
        fact = meta.get('item:'+str(row['id']), {})
        named_person = row['id'] in person_ids or (methods.get(row['method'],{}).get('kind') in ('account','easy_pay')
            and merchant_kind(row['name'],cache) is None and R.received_person(c,row))
        transfer = bool(re.search(TRANSFER, row['name'])) or fact.get('answer')=='gift' or named_person
        if any(f.get('one_off') for f in LC.matching(row, meta)) or annotations.get(row['id'], {}).get('role')=='one_off' or (transfer
                and not row.get('stream_id') and not row.get('pair_id')
                and fact.get('answer') not in ('debt','settlement')
                and counts[(merchant_key(row['name']),row['method'],row['amount'])]==1
                and (re.search(TRANSFER,row['name']) or identities[(merchant_key(row['name']),row['method'])]==1)):
            result.add(row['id'])
    return result


def _periodic_cycles(c, today):
    """Long intervals require repeated evidence; a lone gym bill has no term."""
    groups = collections.defaultdict(list)
    for row in _large_history(c, today):
        if row['amount'] >= 100000:
            groups[(merchant_key(row['name']), row['method'])].append(row)
    result = {}
    for rows in groups.values():
        rows.sort(key=lambda r:(r['date'], r['id']))
        if len(rows)<2:
            continue
        for cycle, months in R.CYCLE_MONTHS.items():
            if months < 3:
                continue
            if all(abs((datetime.date.fromisoformat(after['date'])-
                        datetime.date.fromisoformat(_advance_date(before['date'],months))).days)<=7
                   for before, after in zip(rows, rows[1:])):
                result.update({r['id']:cycle for r in rows})
                break
    return result


def _installment_tracking(c, today, meta, cache, repeated, periodic_rows, stopped_ids):
    from ledger_installment import group_schedules
    tracking = []
    for rows in repeated:
        if group_schedules(c, rows):
            continue
        last = rows[-1]
        if (bucket(last, meta, cache) in BIG_EXCLUDED or last['id'] in periodic_rows
                or last['id'] in stopped_ids or LC.future_excluded(last,meta,today)):
            continue
        tracking.append(dict(name=last['name'], method=last['method'], amount=last['amount'],
            ids=[r['id'] for r in rows], last=last['date'],
            active=_advance_date(last['date'][:7]+'-01',2)>today[:7]+'-01'))
    return tracking


def _periodic_costs(c, meta, today, cache):
    """Confirmed multi-month bills, independent of the history window.

    Keep payment rows intact; only the plan's monthly basis is replaced.
    Streams are identities, so two contracts from one vendor stay separate.
    """
    streams = {r['id']: r for r in D.rows(c, 'SELECT * FROM fixed_cost')}
    inferred_cycles = _periodic_cycles(c, today)
    annotations = R.annotations(c)
    groups = collections.defaultdict(list)
    payment_months = [r[0] for r in c.execute(
        "SELECT DISTINCT substr(date,1,7) FROM spend_entry WHERE date<=? AND kind='expense' "
        "AND COALESCE(class,'spend')='spend' AND scope<>'business'", (today,))]
    for month in payment_months:
        for row in D._month_rows(c, month):
            if row['date'] > today or row['kind'] != 'expense' or row['class'] != 'spend':
                continue
            if LC.future_excluded(row, meta, today):
                continue
            stream = streams.get(row.get('stream_id'), {})
            key = _subscription_key(row['name'], (meta or {}).get('_subscription_identities'), row.get('stream_id'))
            fact = _subscription_fact(c, meta, key, [row['stream_id']] if row.get('stream_id') else [])
            annotation = annotations.get(row['id'], {})
            annual_tax = bool(re.search(r'재산세|property tax', row['name']+' '+(row.get('note') or ''), re.I))
            cycle = annotation.get('cycle') or fact.get('cycle') or ('yearly' if annual_tax else stream.get('cycle') or inferred_cycles.get(row['id']))
            if stream.get('state') in ('stopped','stop_suspected','stopped_suspected'):
                continue
            if R.CYCLE_MONTHS.get(cycle, 1) <= 1:
                continue
            category = bucket(row, meta, cache)
            if not row.get('category_override') and (annotation.get('role') in R.FIXED
                    or not meta.get('item:'+str(row['id'])) or meta['item:'+str(row['id'])].get('bucket')=='skip'):
                cls = annotation.get('role') or stream.get('class') or ('tax' if annual_tax else None)
                if cls in R.FIXED or row['id'] in inferred_cycles:
                    category = 'subscriptions' if cls=='subscription' else 'fixed'
            if category not in ('fixed', 'subscriptions'):
                continue
            identity = ('stream', stream['id']) if stream else ('subscription', key, cycle)
            groups[identity].append(dict(row, period_cycle=cycle, period_bucket=category))
    result = []
    for identity, rows in groups.items():
        latest = max(rows, key=lambda r:(r['date'],r['id']))
        stream = streams.get(latest.get('stream_id'), {})
        # Carry an active long-cycle bill across quiet months, without
        # resurrecting a cancelled/expired contract outside the history window.
        start = W.window(today)['average_months'][0]
        if latest['date'][:7] < start:
            end = E.term(c,stream.get('id')).get('ends_on')
            cancelled = meta.get('subscription:'+_subscription_key(latest['name'], c, latest.get('stream_id')),{}).get('cancelled')
            if (end and end[:7] < start) or ((stream.get('state')=='stopped' or cancelled) and not end):
                continue
        # Use the current contract amount when known, otherwise the latest bill.
        amount = stream.get('current_amount')
        if amount is None: amount = latest['amount']
        cycle = latest['period_cycle']
        due = stream.get('next_due') or stream.get('expected_next')
        if not due or due<=latest['date']:
            due = _advance_date(latest['date'], R.CYCLE_MONTHS[cycle])
        # A missed whole cycle is stopped evidence, not an endlessly moving renewal.
        if due < today and (datetime.date.fromisoformat(today)-datetime.date.fromisoformat(due)).days > 31:
            continue
        result.append(dict(identity=identity, rows=rows, bucket=latest['period_bucket'],
                           cycle=cycle, next_date=due, amount=amount,
                           monthly=round(amount/R.CYCLE_MONTHS[cycle])))
    return result
def _dominant_marketplace(rows, cache):
    totals = collections.Counter()
    for row in rows:
        if merchant_kind(row['name'], cache) == 'marketplace':
            totals[merchant_key(row['name'])] += row['amount']
    if not totals:
        return None
    key, amount = totals.most_common(1)[0]
    return dict(name=next(r['name'] for r in rows if merchant_key(r['name']) == key), amount=amount)


def _roster(c, today, meta, cache, seen):
    """Keep known instruments; quote only the shared three complete months.

    Quiet months stay in the denominator for every displayed average.
    """
    months = W.window(today)['average_months']
    history_months = [r[0] for r in c.execute(
        "SELECT DISTINCT substr(date,1,7) FROM spend_entry WHERE date<? ORDER BY 1",
        (today[:7]+'-01',))]
    payments = [r for month in history_months for r in D._month_rows(c, month)]
    historical = _instruments(c, payments, len(history_months), meta, cache, history_months, seen=seen)
    recent = {r['id']: r for r in _instruments(c,
        [r for r in payments if r['date'][:7] in months], 3, meta, cache, months, seen=seen)}
    dates = {r['id']: r['date'] for r in payments}
    result = []
    for old in historical:
        first = min(dates[sid] for sid in old['payment_ids'])[:7]
        span = months
        row = recent.get(old['id'])
        if row is None:
            row = dict(old, payment_ids=[], merchants=[], count=0, amount=0,
                       monthly_count=0, monthly_amount=0, marketplace=None,
                       unresolved_amount=0, purpose_streams=[], usage=_usage([], meta, cache, months))
        row = dict(row, basis_months=span, first_observed_month=first,
                   monthly_count=row['count']/len(span),
                   monthly_amount=round(row['amount']/len(span)))
        result.append(row)
    return months, result


def _subscription_recurs(rows):
    """Two distinct positive payment dates on a regular billing interval.

    Same-day split tenders and refunds are not additional charges. Calendar
    cycles allow three days of billing drift and clamp month-end dates. A
    recurring subset can coexist with usage charges from the same vendor.
    """
    dates = sorted({datetime.date.fromisoformat(r['date']) for r in rows if r['amount'] > 0})
    for n, before in enumerate(dates):
        for after in dates[n+1:]:
            if (after-before).days in (7, 14):
                return True
            months = (after.year-before.year)*12 + after.month-before.month
            if months in (1, 2, 3, 6, 12):
                nominal = after.replace(day=min(before.day, calendar.monthrange(after.year, after.month)[1]))
                if abs((after-nominal).days) <= 3:
                    return True
    return False


def _subscription_oneoffs(subscriptions, months):
    """All positive payments with unconfirmed periodicity, grouped by identity."""
    result = []
    for sub in subscriptions:
        members = sorted((r for r in sub['members'] if r['date'][:7] in months and r['amount'] > 0),
                         key=lambda r:(r['date'],r['id']))
        if not sub['recurrence_confirmed'] and sub.get('periodicity') != 'one_off' and members:
            result.append(dict(members[0], payments=members, key=sub['key'],
                periodicity=sub.get('periodicity', 'unknown'), **{k:sub[k] for k in
                ('display_name', 'display_name_missing', 'display_name_source') if k in sub}))
    return sorted(result, key=lambda r: (-r['amount'], r['date'], r['id']))


def _confirm_annual_name(c, data, K, today, all_months):
    import near_match
    name = data.get('name')
    if not isinstance(name, str) or not name.strip():
        raise ValueError('subscription name required')
    plan = read(c, today, all_months=all_months)
    issued = set(data.get('issued_ids', []))
    stated = re.fullmatch(r'(.+?)(?:은|는)\s*(?:1년에\s*한\s*번\s*내요|일년에\s*한\s*번\s*내요|연간\s*구독(?:이에요|이야)?|yearly)',name.strip(),re.I)
    query_name = stated[1].strip() if stated else name
    query = near_match.compact(query_name)
    candidates = []
    for row in plan['subscription_oneoffs']:
        if row['id'] not in issued:
            continue
        names = (row['display_name'], row['name'])
        if any(query == near_match.compact(n) or near_match.compare(query_name, n, 'place')
               or len(query) >= 3 and near_match.compact(n).startswith(query) for n in names):
            candidates.append(row)
    if len(candidates) != 1:
        if candidates and K is not None:
            raise K.NearMatchError('method', name, [
                dict(ref=r['id'], name=r['display_name'], signal='contains', matched=r['display_name'])
                for r in candidates])
        raise ValueError('name must identify one issued single subscription charge')
    row = candidates[0]
    fact = dict(kind='subscription', key=row['key'], cycle='yearly', cancelled=False,
                anchor_date=row['date'], charge_id=row['id'], said=data['said'])
    answer(c, fact, K=K, today=today, all_months=all_months)
    return dict(saved=True, fact=fact)


def _current_subscriptions(c, subscriptions, periodic, months):
    """Display current recurring costs without replacing the history plan.

    Long-cycle contracts retain their monthly share through quiet months.
    Ordinary bills use all three complete months, including quiet months.
    Only changes in amount occupy a month-over-month line.
    The current card note uses the latest observed month, once per method.
    """
    identities=_subscription_identities(c)
    result = []
    for sub in subscriptions:
        members = [r for r in sub['members'] if r['date'][:7] in months]
        periods = [item for item in periodic if item['bucket']=='subscriptions'
                   and any(_subscription_key(r['name'], identities, r.get('stream_id'))==sub['key'] for r in item['rows'])]
        if not sub.get('recurrence_confirmed', bool(periods) or _subscription_recurs(sub['members'])):
            continue
        if not members and not periods:
            continue
        first = min([sub['first_seen'], *(r['date'] for r in sub['members'])])[:7]
        span = months
        if not span or first > months[-1]:
            continue
        period_ids = {r['id'] for item in periods for r in item['rows']}
        monthly = sum(item['monthly'] for item in periods) + round(
            sum(r['amount'] for r in members if r['id'] not in period_ids)/len(span))
        observed = sorted({r['date'][:7] for r in members})
        snapshots = []
        for month in observed:
            rows = [r for r in members if r['date'][:7]==month]
            snapshots.append(dict(month=month, amount=sum(r['amount'] for r in rows),
                methods=sorted({r['method'] for r in rows if r.get('method')}),
                days=sorted({int(r['date'][8:]) for r in rows if r['amount']>0})))
        days = [set(s['days']) for s in snapshots]
        changes = [[before, after] for before, after in zip(snapshots, snapshots[1:])
                   if before['amount'] != after['amount']]
        result.append(dict(sub, monthly=monthly, members=members, basis_months=span,
            amount_varies=len({s['amount'] for s in snapshots})>1 or bool(snapshots) and monthly!=snapshots[0]['amount'],
            payment_methods=snapshots[-1]['methods'] if snapshots else [],
            recurring_days=sorted(set.intersection(*days)) if len(days)>1 else [],
            recent_change=changes[-1] if changes else []))
    return sorted(result,key=lambda s:(s['amount_varies'],-s['monthly'],s['key']))


def _current_estimate(c, today, plan, meta, cache):
    """Open-month planning only; completed-month display fields stay frozen."""
    if not W.window(today)['include_current']:
        return None
    pending = W.projected_rows(c, today)
    actual = [r for r in D._month_rows(c, today[:7]) if r['date'] <= today]
    oneoffs = _oneoff_ids(c, today, meta)
    periodic = _periodic_costs(c, meta, today, cache)
    period_ids = {r['id'] for item in periodic for r in item['rows']}
    totals = collections.Counter({k:0 for k in BUCKETS})
    for row in actual + pending:
        if row['kind'] != 'expense' or row['class'] != 'spend' or row['id'] in oneoffs | period_ids:
            continue
        if LC.future_excluded(row, meta, today):
            continue
        key = row.get('projection_bucket') or bucket(row, meta, cache)
        if 'projection_bucket' not in row and key not in BIG_EXCLUDED and abs(row['amount']) >= 100000:
            key = 'big'
        totals[key] += row['amount']
    for item in periodic:
        totals[item['bucket']] += item['monthly']
    for row in W.reviewed_credits(c, actual, meta, R.annotations(c)):
        totals[row['credit_bucket']] -= row['amount']
    spend = sum(totals.values())
    loan = sum(r['amount'] for r in actual+pending if r['kind']=='expense' and r['class']=='loan')
    return dict(month=today[:7], groups=dict(totals), spend=spend, loan=loan,
                out=spend+loan, gap=plan['income']-spend-loan, income=plan['income'],
                projection=pending, provenance='estimated', window=W.window(today))


def read(c, today, all_months=False, *, display=True):
    import ledger_deposits
    today = datetime.date.fromisoformat(today).isoformat()
    history = H.history(c, today, all_months=all_months)
    window = history['window']['average_months']
    months = [m for m in history['months'] if m['month'] in window]
    n = len(window)
    meta = facts(c)
    from ledger_installment import billed_groups, group_schedules
    repeated = [[r for r in rows if r['date']<=today] for rows in billed_groups(c)]
    repeated = [rows for rows in repeated if len(rows)>1]
    installment_candidates = []
    installment_rows = {}
    review = R.annotations(c)
    for rows in repeated:
        schedules = group_schedules(c, rows)
        confirmed = len(schedules)==1 and (schedules[0].get('bill_only') or (
            schedules[0]['first']==rows[0]['date'][:7]
            and all(review.get(r['id'],{}).get('billed_installment_id')==schedules[0]['id'] for r in rows)))
        for r in rows:
            installment_rows[r['id']] = dict(installment_review_required=not confirmed,
                installment_name=schedules[0]['name'] if len(schedules)==1 else None,
                installment_end=schedules[0]['end'] if len(schedules)==1 else None,
                recorded_installment=bool(schedules), big_kind='installment')
        recent = [r for r in rows if r['date'][:7] in window]
        if not confirmed and recent:
            installment_candidates.append(dict(ids=[r['id'] for r in recent], dates=[r['date'] for r in recent],
                name=rows[0]['name'], method=rows[0]['method'], amount=rows[0]['amount']))
    # A stored purchase schedule also classifies a lone observed charge.
    # Strong row IDs take precedence over an unresolved merchant label.
    from ledger_installment import listing
    for it in listing(c,today[:7],include_all=True)['items']:
        for sid in set(it.get('observed_ids',[])) | {it.get('spend_id'), it.get('latest_source_id')}:
            if sid is not None:
                installment_rows.setdefault(sid,dict(big_kind='installment',installment_name=it['name'],
                    installment_end=it['end'],recorded_installment=True,installment_review_required=False))
    cache = merchant_kinds(c)
    names = N.Names(c)
    periodic = _periodic_costs(c, meta, today, cache)
    periodic_rows = {r['id']:item for item in periodic for r in item['rows']}
    oneoff_ids = _oneoff_ids(c, today, meta)
    stopped_ids = {r[0] for r in c.execute("SELECT s.id FROM spend_entry s JOIN fixed_cost f ON f.id=s.stream_id WHERE f.state IN ('stopped','stop_suspected')")}
    payments = [r for m in months for r in D._month_rows(c, m['month']) if r['date'] <= today]
    expenses = [r for r in payments if r['kind']=='expense' and r['class']=='spend']
    # Keep both layers beside each other in the read model. Existing cards
    # retain their owner-picked text; no purpose question is introduced.
    import ledger_goal_purpose
    purposes = ledger_goal_purpose.classify(c, expenses, today)
    import ledger_goal_spend
    classifications = [dict(id=r['id'], date=r['date'], name=r['name'], amount=r['amount'],
                            merchant_kind=merchant_kind(r['name'], cache),
                            bucket=bucket(r, meta, cache), purpose=purposes[r['id']]) for r in expenses]
    lookups = collections.defaultdict(list)
    totals = collections.Counter({k: 0 for k in BUCKETS})
    by_bucket = collections.defaultdict(list)
    big = []
    oneoffs = []
    cancelled={sid for pair in A.order_metrics(expenses)['cancelled'] for sid in pair}
    hidden = collections.defaultdict(list)
    transfers = collections.defaultdict(list)
    interviewed = {sid for fact in meta.values() if fact.get('kind') == 'instrument'
                   for sid in fact.get('payment_ids', [])}
    settled_expenses = {sid for sid, in c.execute(
        "SELECT DISTINCT expense_id FROM ledger_receipt_allocation WHERE payer='owner'")}
    for r in expenses:
        if r['id'] in oneoff_ids:
            oneoffs.append(dict(r, big_kind='oneoff'))
            # One-off budgeting does not establish a transfer's purpose.
            # Keep the dev.80 purpose ask independent of shopping/instruments.
            if (re.search(TRANSFER, r['name']) and bucket(r, meta, cache) == 'unknown'
                    and not meta.get('item:'+str(r['id']))
                    and not meta.get('hidden:'+str(r.get('method'))+':'+r['name'])):
                transfers[(r.get('method'), r['name'])].append(r)
            continue
        if (r['id'] in stopped_ids or LC.future_excluded(r,meta,today)) and abs(r.get('purchase_amount',r['amount']))>=100000:
            continue
        key = periodic_rows[r['id']]['bucket'] if r['id'] in periodic_rows else bucket(r, meta, cache)
        if (key == 'unknown' and merchant_kind(r['name'], cache) is None
                and not meta.get('item:'+str(r['id'])) and not r.get('category_override')):
            lookups[r['name']].append(r)
        if (key == 'unknown' and r['amount'] >= 1000 and not meta.get('hidden:'+str(r.get('method'))+':'+r['name'])
                # Instrument use never answers what an individual transfer was.
                and not meta.get('item:'+str(r['id']))
                and (r['id'] not in interviewed or re.search(TRANSFER, r['name']))):
            if re.search(_LEXICON['opaque_payment'], r['name'], re.I):
                # A transfer is never "where was it spent": it gets its own ask (reduce_transfers).
                (transfers if re.search(TRANSFER, r['name']) else hidden)[(r.get('method'), r['name'])].append(r)
        large = abs(r.get('purchase_amount',r['amount'])) >= 100000
        if key not in BIG_EXCLUDED and large:
            detail = dict(big_kind='unknown' if key=='unknown' else 'shopping')
            detail.update(installment_rows.get(r['id'], {}))
            key = 'big'
            big.append(dict(r, **detail, number=len(big)+1, reviewed=bool(meta.get('item:'+str(r['id']))) or r['id'] in cancelled or r['amount']<0 or r['id'] in settled_expenses,
                            unknown=meta.get('item:'+str(r['id']),{}).get('bucket')=='skip' and not (installment_rows.get(r['id'],{}).get('recorded_installment') or review.get(r['id'],{}).get('billed_installment_id'))))
        totals[key] += r['amount']
        by_bucket[key].append(r)
    forecast = [r for m in months for r in m['projection']]
    projected_groups = collections.Counter()
    for r in forecast:
        if r['class'] == 'spend':
            key = periodic_rows[r['id']]['bucket'] if r['id'] in periodic_rows else r.get('projection_bucket') or bucket(r, meta, cache)
            if key not in BIG_EXCLUDED and abs(r['amount']) >= 100000 and 'projection_bucket' not in r:
                key = 'big'
            totals[key] += r['amount']
            projected_groups[key] += r['amount']
    # Refunds whose payment is unknown are a separate credit, never income.
    # Only an owner category fact can place one in a spend bucket.
    annotations=R.annotations(c)
    refunds = [r for r in payments if r['class']=='reimbursement' and annotations.get(r['id'],{}).get('history_received')
               and not c.execute('SELECT 1 FROM ledger_receipt_allocation WHERE receipt_id=?', (r['id'],)).fetchone()]
    for r in refunds:
        fact = meta.get('refund:'+str(r['id']), {})
        if fact.get('bucket') in BUCKETS:
            totals[fact['bucket']] -= r['amount']
    metrics = {}
    for key in (*LEVERS,'meals'):
        m = A.order_metrics(by_bucket[key])
        metrics[key] = dict(m, monthly_count=(m['count']+sum(r['projected_count'] for r in forecast if r.get('projection_bucket')==key))/n if n else 0,
                            monthly_amount=round((m['amount']+projected_groups[key])/n) if n else 0)
    # Replace paid/projected period amounts with one monthly share per contract.
    # Raw history and subscription payment evidence still show the actual charge.
    for item in periodic:
        ids = {r['id'] for r in item['rows']}
        for key, rows in by_bucket.items():
            totals[key] -= sum(r['amount'] for r in rows if r['id'] in ids)
        for row in forecast:
            if row['id'] in ids and row['class']=='spend':
                key = row.get('projection_bucket') or item['bucket']
                totals[key] -= row['amount']
                projected_groups[key] -= row['amount']
        totals[item['bucket']] += item['monthly']*n
        if item['bucket']=='subscriptions':
            present = {r['id'] for r in by_bucket['subscriptions']}
            # Active annual contracts retain their evidence through quiet months.
            # _current_subscriptions filters displayed payments to the window.
            by_bucket['subscriptions'].extend(r for r in item['rows'] if r['id'] not in present)
    subscriptions = []
    groups = collections.defaultdict(list)
    evidence = collections.defaultdict(list)
    for row in D.rows(c, "SELECT date,name,amount,stream_id FROM spend_entry WHERE date<=? AND kind='expense' "
                        "AND COALESCE(class,'spend')='spend' AND scope<>'business' AND amount>0", (today,)):
        evidence[_subscription_key(row['name'], (meta or {}).get('_subscription_identities'), row.get('stream_id'))].append(row)
    for r in by_bucket['subscriptions']:
        vendor = _subscription_key(r['name'], meta.get('_subscription_identities'), r.get('stream_id'))
        groups[vendor].append(r)
    for vendor, members in groups.items():
        stream_ids = _subscription_fixed_ids(c,vendor,members)
        f = _subscription_fact(c,meta,vendor,stream_ids)
        streams=[r for r in D.rows(c,'SELECT * FROM fixed_cost') if r['id'] in stream_ids]
        confirmed = bool(f.get('cycle') or f.get('amount_change')) or any(
            stream['class']=='subscription' and stream['source'] in ('owner','chat','interview') for stream in streams)
        stopped=bool(streams) and all(s['state']=='stopped' for s in streams)
        if len(streams)==1 and streams[0]['source']!='ledger_v6':
            f=dict(dict(cycle=streams[0]['cycle'],cancelled=stopped),**f)
        latest = max(members, key=lambda r:(r['date'],r['id']))
        annual = f.get('cycle')=='yearly'
        periodic_members = [item for item in periodic if item['bucket']=='subscriptions'
                            and any(_subscription_key(r['name'], meta.get('_subscription_identities'), r.get('stream_id'))==vendor for r in item['rows'])]
        periodic_ids = {r['id'] for item in periodic_members for r in item['rows']}
        monthly = sum(item['monthly'] for item in periodic_members) + (round((
            sum(r['amount'] for r in members if r['id'] not in periodic_ids)+sum(
            r['amount'] for r in forecast if r['class']=='spend' and r['id'] not in periodic_ids
            and _subscription_key(r['name'], meta.get('_subscription_identities'), r.get('stream_id'))==vendor))/n) if n else 0)
        renew = None
        if annual and not (f.get('cancelled') or stopped):
            renew = _yearly_renewal(latest['date'], today)
        terms=[E.term(c,stream['id']) for stream in streams]
        end=terms[0] if len(terms)==1 or terms and all(t==terms[0] for t in terms) else dict(ends_on=None,end_kind='unknown',end_asked=bool(terms) and all(t['end_asked'] for t in terms))
        charge_days=collections.defaultdict(set)
        for row in members:
            if row['amount']>0:charge_days[row['date'][:7]].add(int(row['date'][8:]))
        regular=sorted(set.intersection(*charge_days.values())) if len(charge_days)>1 else []
        complete_months=sorted({r['date'][:7] for r in members if r['date'][:7] < today[:7]})
        basis_month=complete_months[-1] if complete_months else None
        basis_amount=sum(r['amount'] for r in members if r['date'][:7]==basis_month) if basis_month else monthly
        first_seen=min((date for date,name,fid in c.execute("SELECT date,name,stream_id FROM spend_entry WHERE kind='expense' AND COALESCE(class,'spend')='spend'") if _subscription_key(name,meta.get('_subscription_identities'),fid)==vendor),default=latest['date'])
        subscriptions.append(dict(**end, **({'amount_change':f['amount_change']} if f.get('amount_change') else {}),
                                  recurrence_confirmed=not f.get('one_off') and (confirmed or _subscription_recurs(evidence[vendor])),
                                  periodicity='one_off' if f.get('one_off') else f.get('cycle') or ('recurring' if confirmed or _subscription_recurs(evidence[vendor]) else 'unknown'),
                                  **names.fields(latest['name']), category=f.get('category'), aliases=f.get('aliases', []),
                                  first_seen=first_seen,recurring_days=regular,paid_monthly=E.paid_monthly(c,[stream['id'] for stream in streams],monthly,today[:7]), fixed_ids=[stream['id'] for stream in streams], key=vendor, name=latest['name'], monthly=monthly,
                                  latest_full_month=basis_month,latest_full_amount=basis_amount,
                                  cancelled=bool(f.get('cancelled') or stopped), cycle=f.get('cycle'),
                                  renewal=renew, yearly_amount=latest['amount'] if annual else None,
                                  reminder_due=(datetime.date.fromisoformat(renew)-datetime.timedelta(days=YEARLY_REMINDER_DAYS)).isoformat() if renew else None,
                                  reminder_enabled=bool(f.get('reminder_enabled') and not (f.get('cancelled') or stopped)), last=latest, members=members))
    from ledger_installment import listing
    installments = listing(c,today[:7])['items']
    # A retained schedule can outlive its source during a reimport. Keep its
    # conservative cash-flow load, but do not promise a release date until
    # the owner reviews the replacement payment's remaining term.
    for it in installments:
        if it.get('spend_id') and not c.execute('SELECT 1 FROM spend_entry WHERE id=?',(it['spend_id'],)).fetchone():
            it['release_unconfirmed'] = True
    income = round(sum(m['income'] for m in months)/n) if n else 0
    # Unknown terms stay read-only tracking records. Never fabricate an end
    # or a principal from equal monthly card charges. One missing full month
    # stops the carry; a new observed charge restarts it on the next read.
    tracking = _installment_tracking(c,today,meta,cache,repeated,periodic_rows,stopped_ids)
    # Recurring shopping budgets use current charges, never an average of
    # installment bills. Historical rows remain intact for the evidence card.
    billed_ids = {r['id'] for rows in repeated for r in rows
                  if bucket(r,meta,cache) not in BIG_EXCLUDED and r['id'] not in periodic_rows}
    # Stored purchases may have only one linked row, or no observed bill at
    # all. Replace their purchase amount just like repeated billed charges,
    # then carry every current schedule once, independent of row matching.
    billed_ids.update(sid for sid, detail in installment_rows.items() if detail.get('recorded_installment'))
    billed_total = sum(r['amount'] for r in big if r['id'] in billed_ids)
    totals['big'] -= billed_total
    totals['big'] += n*(sum(it['this_month'] for it in installments) +
        sum(it['amount'] for it in tracking if it['active']))
    # Savings are a separate choice, never a cut lever.
    loan = round(sum(m['parts']['loan'] for m in months)/n) if n else 0
    loans = history['debts']
    L.baselines(loans,payments,forecast,n)
    loan_baseline = loan
    loan = max(0,loan+L.forecast(dict(loans=loans), today[:7])['delta'])
    monthly = {key:round(value/n) if n else 0 for key,value in totals.items()}
    spend = round(sum(totals.values())/n) if n else 0
    from ledger_profile import methods_seen
    profiles = {e['method_id']: e for e in methods_seen(c)['methods'] if e.get('method_id') is not None}
    instruments = _instruments(c, payments, n, meta, cache, [m['month'] for m in months], seen=profiles)
    instrument_review = list(instruments)
    display_fields = {}
    if display:
        roster_months, roster_instruments = _roster(c, today, meta, cache, profiles)
        display_fields = dict(roster_months=roster_months, roster_instruments=roster_instruments,
            current_subscriptions=_current_subscriptions(c, subscriptions, periodic, roster_months),
            subscription_oneoffs=_subscription_oneoffs(subscriptions, roster_months))
    display_fields['subscription_unknown'] = _subscription_oneoffs(subscriptions, window)
    display_fields['subscription_proved_oneoffs'] = [s for s in subscriptions if s['periodicity']=='one_off']
    if today[:7] not in window:
        # The interview quotes the roster's period, population and rounding.
        # Still discover new instruments before the current month is complete,
        # with their own current-month evidence rather than a mixed average.
        seen = {r['id'] for r in instruments}
        instrument_review += [r for r in _instruments(c, _payments(c, today), 1, meta, cache, [today[:7]], seen=profiles)
                              if r['id'] not in seen]
    result = dict(today=today, window=history['window'], projected=bool(forecast), projected_groups=dict(projected_groups), months=[m['month'] for m in months],
                through=max((r['date'] for r in payments if r['date'].startswith(today[:7])),default=None), n=n,
                income=income, spend=spend, loan=loan, loan_baseline=loan_baseline, loans=loans, out=spend+loan, gap=income-spend-loan,
                saving=round(sum(m['parts']['saving'] for m in months)/n) if n else 0,
                groups=monthly, group_totals=dict(totals), big=big, big_oneoffs=oneoffs,
                payment_lifecycles=[v for k,v in meta.items() if k.startswith('lifecycle:')],
                periodic_costs=periodic, installment_tracking=tracking, big_classification_version=1, metrics=metrics,
                classifications=classifications,
                display_name_missing=[dict(name=s['last']['name'], key=s['key'], display_name_missing=True)
                                      for s in subscriptions if s['display_name_missing']],
                display_name_resolution=N.RESOLUTION,
                goal_spend=ledger_goal_spend.candidates(c, expenses, today),
                merchant_lookups=sorted([dict(name=name, ids=[r['id'] for r in rs],
                    count=len(rs), amount=sum(r['amount'] for r in rs),
                    lookup_required=True) for name,rs in lookups.items()],
                    key=lambda r: (-abs(r['amount']), r['name'])),
                hidden=[dict(method=method,name=name,count=len(rs),amount=sum(r['amount'] for r in rs),
                             ids=[r['id'] for r in rs],
                             # Newest first: the owner is asked about individual payments, never the monthly aggregate.
                             recent=[dict(id=r['id'],date=r['date'],name=r['name'],amount=r['amount'])
                                     for r in sorted(rs,key=lambda r:(r['date'],r['id']),reverse=True)])
                        for (method,name),rs in hidden.items()],
                transfers=[dict(method=method,name=name,count=len(rs),amount=sum(r['amount'] for r in rs),
                             ids=[r['id'] for r in rs],
                             # Newest first: the owner is asked about individual payments, never the monthly aggregate.
                             recent=[dict(id=r['id'],date=r['date'],name=r['name'],amount=r['amount'])
                                     for r in sorted(rs,key=lambda r:(r['date'],r['id']),reverse=True)])
                        for (method,name),rs in transfers.items()],
                matched_payments=_matched_payments(c, payments),
                deposits=[dict(r, month=m['month']) for m in months
                          for r in ledger_deposits.pending(c, m['month']) if r['date'] <= today
                          and 'refund:'+str(r['id']) not in meta],
                **display_fields,
                instruments=instruments,
                instrument_review=instrument_review,
                refunds=[r for r in refunds if 'refund:'+str(r['id']) not in meta],
                installment_candidates=installment_candidates, subscriptions=subscriptions, installments=installments, obligations=E.obligations(c,today[:7]), facts=meta)
    if display:
        N.present(c, result)
        # Module/instrument identities are not unresolved merchant names.
        # Keep the informational projection out of the existing lookup queue.
        from ledger_roster_modules import read as module_roster
        result['module_roster'] = module_roster(c, today, cache, meta)
    result['current_estimate'] = _current_estimate(c, today, result, meta, cache)
    from ledger_progress import snapshot
    result['month_progress'] = snapshot(c, today, result)
    return result


def _hidden_module(c, data, K=None):
    """One durable module link, with optional inferred life-area evidence."""
    from ledger_roster_modules import MODULES
    module = data.get('module')
    if module not in (*MODULES, 'self_transfer', 'unknown'):
        raise ValueError('choose a roster module, self_transfer or unknown')
    if (type(data.get('inferred', False)) is not bool
            or type(data.get('area_inferred', False)) is not bool
            or data.get('area_module') not in (None, *MODULES)
            or data.get('area_module') and module not in MODULES):
        raise ValueError('module inference and area link must be valid')
    lo, hi = data.get('min_amount', 0), data.get('max_amount')
    if (type(lo) is not int or lo < 0 or hi is not None
            and (type(hi) is not int or hi <= lo)):
        raise ValueError('valid module amount range required')
    sid = data.get('id')
    if sid is not None:
        if type(sid) is not int:
            raise ValueError('choose an existing payment id')
        rows = D.rows(c, "SELECT * FROM spend_entry WHERE id=? AND scope<>'business'", (sid,))
        retry = (module=='self_transfer' and facts(c).get('item:'+str(sid), {}).get('module')=='self_transfer')
        if not rows or not (rows[0]['kind']=='expense' and rows[0]['class'] in (None, 'spend')
                           or retry and rows[0]['kind']=='transfer' and rows[0]['class']=='own_transfer'):
            raise ValueError('module item must be an existing spending payment')
        key = 'item:'+str(sid)
    else:
        if not data.get('name') or not data.get('method'):
            raise ValueError('module stream needs method and name')
        rows = D.rows(c, "SELECT * FROM spend_entry WHERE name=? AND method=? AND scope<>'business' "
                         "AND kind='expense' AND COALESCE(class,'spend')='spend' "
                         "AND abs(amount)>=? AND (? IS NULL OR abs(amount)<?)",
                      (data['name'], data['method'], lo, hi, hi))
        key = 'hidden:'+data['method']+':'+data['name']
    detail = dict(data)
    if sid is not None:
        category = A.MODULE_CATEGORIES.get(module, 'unknown_merchant')
        detail['bucket'] = HIDDEN_BUCKETS.get(category, 'other')
    put(c, key, detail)
    if module=='self_transfer':
        # Reuse own-transfer plumbing: no spending, income or recurring stream.
        c.executemany("UPDATE spend_entry SET amount=CASE WHEN kind='expense' THEN -amount ELSE amount END,"
                      "kind='transfer',class='own_transfer',stream_id=NULL WHERE id=?",
                      [(r['id'],) for r in rows])
        if K is not None:
            for row in rows:
                K._spend_shadow_del(c, row['id'])
    return dict(saved=True, fact=detail, ids=[r['id'] for r in rows])


def answer(c, data, K=None, today=None, all_months=False):
    said = data.get('said')
    if not isinstance(said,str) or not said.strip():
        raise ValueError('owner words required')
    kind = data.get('kind')
    if kind=='payment_lifecycle':
        return LC.save(c, data, today or datetime.date.today().isoformat())
    if kind=='subscription_annual_name':
        return _confirm_annual_name(c, data, K, today or datetime.date.today().isoformat(), all_months)
    if kind=='batch':
        results=[]
        for item in data.get('items',[]):
            if item.get('kind')=='billed_installment':
                results.append(R.answer(c, c.execute('SELECT substr(date,1,7) FROM spend_entry WHERE id=?',(item['id'],)).fetchone()[0],item,K=K))
            else:
                results.append(answer(c,item,K=K,today=today,all_months=all_months))
        return dict(saved=True,items=results)
    if kind=='billed_terms':
        from ledger_installment import billed_terms
        return billed_terms(c,data)
    if kind=='remaining_installment':
        from ledger_installment import remaining_bills
        return remaining_bills(c,data)
    if kind=='goal_evidence':
        import ledger_goal_purpose
        return ledger_goal_purpose.record(c,data,today or datetime.date.today().isoformat())
    if kind=='goal_spend_confirm':
        import ledger_goal_spend
        return ledger_goal_spend.confirm(c, data, today or datetime.date.today().isoformat())
    if kind == 'instrument':
        from ledger_v6 import _existing_method
        from ledger_purpose_labels import store, normalize
        method = _existing_method(c, data.get('id'))
        if data.get('answer') not in ('groceries', 'household', 'clothes', 'mixed', 'skip', 'note', 'capture',
                                     'eat', 'shop', 'hobby', 'transfer', 'online', 'label'):
            raise ValueError('invalid instrument evidence answer')
        # A use note never assigns a category or an amount to past payments.
        purpose = None
        if data['answer'] not in ('skip', 'capture'):
            tags = data.get('tags')
            if tags is None and data.get('confirm_guess'):
                tags = normalize(data.get('note') or '')
            purpose = store(c, method, data.get('said') or data.get('note') or '',
                            tags=tags, button=data['answer'])
            data = dict(data, purpose_tags=purpose['tags'], needs_clarification=purpose['needs_clarification'],
                        note=', '.join(purpose['tags']))
            data.pop('said', None)  # raw words live only in pay_method.purpose_memo
            data.pop('tags', None)
        put(c, 'instrument:'+str(method['id']), dict(data, status='skipped' if data['answer']=='skip' else 'answered'))
        return dict(saved=True, id=method['id'], purpose=purpose)
    if kind in ('item','refund'):
        sid = data.get('id')
        row = c.execute('SELECT id,kind,class FROM spend_entry WHERE id=? AND scope<>\'business\'',(sid,)).fetchone()
        if kind=='refund':
            refund_kind=data.get('refund_kind','refund')
            if refund_kind not in REFUND_KINDS:
                raise ValueError('refund kind is refund, expense_reimbursement or cash_exchange')
            if data.get('bucket') != 'skip':
                # Persist the effective kind, including the legacy default.
                # Readbacks must not have to reconstruct what was saved.
                data = dict(data, refund_kind=refund_kind)
            if refund_kind=='cash_exchange':
                # No bucket: it is excluded from both sides (budget/window netting reads a bucket only).
                data=dict(data,bucket='skip',refund_kind=refund_kind)
            elif refund_kind=='expense_reimbursement' and data.get('bucket') not in BUCKETS:
                raise ValueError('an expense reimbursement names the spend bucket it reimbursed')
        if not row or data.get('bucket') not in (*BUCKETS,'skip'):
            raise ValueError('existing payment and known bucket required')
        if kind=='item' and row[1]!='expense':
            raise ValueError('item must be an expense')
        if kind=='refund' and not R.annotations(c).get(sid,{}).get('history_received'):
            import ledger_deposits
            month = c.execute('SELECT substr(date,1,7) FROM spend_entry WHERE id=?', (sid,)).fetchone()[0]
            if not any(r['id'] == sid and r['person'] for r in ledger_deposits.pending(c, month)):
                raise ValueError('refund must be an unmatched receipt')
            # Only the owner's refund answer promotes a raw transfer. This
            # preserves credit arithmetic and excludes it from deposit review.
            R.put(c, sid, dict(history_received=True))
        previous=facts(c).get('item:'+str(sid),{}) if kind=='item' else {}
        if 'points_share' in previous and 'points_share' not in data:
            data=dict(data,points_share=previous['points_share'])
        if 'points_share' in data:
            from ledger_cash import allocation
            if kind!='item' or row[2] not in (None,'spend'):
                raise ValueError('points allocation requires an owner spending expense')
            purchase=c.execute('SELECT amount FROM spend_entry WHERE id=?',(sid,)).fetchone()[0]
            data=dict(data,**allocation(purchase,data['points_share']))
        put(c,kind+':'+str(sid),data)
    elif kind=='hidden' and 'module' in data:
        return _hidden_module(c, data, K=K)
    elif kind=='hidden' and data.get('id') is not None:
        # One payment of a hidden-method stream: classify (or skip) that payment only.
        row = c.execute("SELECT id,name,method,amount,kind FROM spend_entry WHERE id=? AND scope<>'business'",(data['id'],)).fetchone()
        if not row or row[4]!='expense':
            raise ValueError('hidden item must be an existing expense')
        category = data.get('category')
        if category not in ('skip',*A.CATEGORIES):
            raise ValueError('known owner category required')
        if category!='skip':
            # Exact amount range of this one payment (same merchant and rail).
            A.save_category(c,dict(data,name=row[1],method=row[2],min_amount=int(abs(row[3])),max_amount=int(abs(row[3]))+1))
        put(c,'item:'+str(row[0]),dict(data,bucket=HIDDEN_BUCKETS.get(category,'other') if category!='skip' else 'skip'))
    elif kind=='transfer':
        # The owner's word on a transfer: debt repayment, shared-cost settlement, gift/allowance or unknown.
        answer_=data.get('answer')
        if answer_ not in TRANSFER_ANSWERS:
            raise ValueError('transfer answer is debt, settlement, gift or unknown')
        ids=data.get('ids') if data.get('ids') is not None else [data.get('id')]
        if not ids or any(type(i) is not int for i in ids):
            raise ValueError('choose explicit transfer payment ids')
        rows=[c.execute("SELECT id,date,name,method,amount,kind,class FROM spend_entry WHERE id=? AND scope<>'business'",(i,)).fetchone() for i in ids]
        if any(not r or r[5]!='expense' or not re.search(TRANSFER,r[2]) for r in rows):
            raise ValueError('transfer item must be an existing transfer expense')
        applied=[]
        for r in rows:
            sid,date,name,method,amount=r[:5]
            detail=dict(answer=answer_,said=data['said'])
            if answer_=='debt':
                if K is None: raise ValueError('debt answer requires the kit context')
                R.answer(c,date[:7],dict(kind='debt',id=sid,said=data['said']),K=K)
                put(c,'item:'+str(sid),dict(detail,bucket='skip'))
                applied.append(dict(id=sid))
            elif answer_=='settlement':
                # Paying a friend for food/rehearsal is still owner spending.
                # Date/amount proximity supplies no transaction identity.
                c.execute("UPDATE spend_entry SET class='spend' WHERE id=?",(sid,))
                put(c,'item:'+str(sid),dict(detail,bucket='other',settles=None))
                applied.append(dict(id=sid,settles=None))
            elif answer_=='gift':
                put(c,'item:'+str(sid),dict(detail,bucket='other',category='gift'))
                applied.append(dict(id=sid))
            else:
                put(c,'item:'+str(sid),dict(detail,bucket='skip'))
                applied.append(dict(id=sid))
        if data.get('method') and data.get('name'):
            put(c,'hidden:'+data['method']+':'+data['name'],dict(data,category='skip'))
        return dict(transfer=answer_,applied=applied)
    elif kind=='hidden':
        if not data.get('name') or not data.get('method'):
            raise ValueError('hidden merchant needs method and name')
        if data.get('category') not in ('skip',*A.CATEGORIES):
            raise ValueError('known owner category required')
        if data['category']!='skip':
            A.save_category(c,data)
        put(c,'hidden:'+data['method']+':'+data['name'],data)
    elif kind=='subscription':
        if isinstance(data.get('key'),str):
            raw_key=merchant_key(data['key'])
            linked={_subscription_key(raw,c) for fixed,raw in c.execute(
                'SELECT f.name,s.name FROM fixed_cost f JOIN spend_entry s ON s.stream_id=f.id')
                if merchant_key(fixed)==raw_key}
            data=dict(data,key=next(iter(linked)) if len(linked)==1 else _subscription_key(data['key'],c))
        if (not isinstance(data.get('key'),str) or not data['key'].strip()
                or data.get('cycle') not in (None,*R.CYCLE_MONTHS)
                or type(data.get('cancelled',False)) is not bool
                or type(data.get('one_off',False)) is not bool
                or data.get('one_off') and data.get('cycle') is not None
                or data.get('category') not in (None, 'ai_tool', 'other')
                or ('merchant_names' in data and (not isinstance(data['merchant_names'],list) or any(not isinstance(n,str) or not n.strip() for n in data['merchant_names'])))
                or ('aliases' in data and (not isinstance(data['aliases'], list) or any(not isinstance(a,str) or not a.strip() for a in data['aliases'])))
                or ('reminder_enabled' in data and type(data['reminder_enabled']) is not bool)):
            raise ValueError('subscription identity, cycle and cancellation state required')
        if 'ends_on' in data or 'end_kind' in data or 'amount_change' in data:
            sub = next((s for s in read(c,today or datetime.date.today().isoformat(),all_months=all_months,display=False)['subscriptions'] if s['key']==data['key']),None)
            ids = sub['fixed_ids'] if sub else []
            if len(ids) > 1:
                raise ValueError('ambiguous subscription streams; answer a fixed id')
            if not ids:
                if not sub: raise ValueError('existing subscription required')
                fid=c.execute("INSERT INTO fixed_cost(name,amount,cycle,class,source) VALUES(?,?,?,'subscription','chat')",(data.get('name') or sub['name'],sub['last']['amount'],data.get('cycle') or 'monthly')).lastrowid
                c.executemany('UPDATE spend_entry SET stream_id=? WHERE id=?',[(fid,r['id']) for r in sub['members']])
            else: fid=ids[0]
            c.execute("UPDATE fixed_cost SET class='subscription' WHERE id=?",(fid,))
            if data.get('cycle'):
                c.execute('UPDATE fixed_cost SET cycle=?,amount=?,current_amount=? WHERE id=?',(data['cycle'],sub['last']['amount'],sub['last']['amount'],fid))
            if 'amount_change' in data:
                if sub['cancelled'] or data.get('cancelled') or data.get('cycle',sub['cycle'])=='yearly':
                    raise ValueError('amount change requires an active monthly subscription')
                data=dict(data,amount_change=_amount_change(data['amount_change'],fid,sub))
            if 'ends_on' in data or 'end_kind' in data:
                E.save(c,K._facade_agent() if K else 'ledger',fid,data,today or datetime.date.today().isoformat(),record_key='ledger:yearly:'+data['key'] if data.get('cycle',sub['cycle'])=='yearly' else None)
        if 'tiers' in data:
            if not isinstance(data['tiers'],list) or any(not isinstance(t,dict) or not isinstance(t.get('tier'),str)
                    or type(t.get('lo')) is not int or type(t.get('hi')) is not int
                    or not 0 <= t['lo'] <= t['hi'] for t in data['tiers']):
                raise ValueError('tiers require labels and nonnegative inclusive won bands')
        data=dict(data,key=_subscription_key(data['key'],c))
        previous = facts(c).get('subscription:'+data['key'], {})
        if 'category' in data and 'merchant_names' not in data and 'merchant_names' not in previous:
            if data['key'].startswith('stream:'):
                data=dict(data,merchant_names=[r[0] for r in c.execute('SELECT DISTINCT name FROM spend_entry WHERE stream_id=?',(data['key'][7:],))])
            else:
                data=dict(data,merchant_names=[data['key']])
        if data.get('cycle'):
            data = dict(data, one_off=False)
        elif data.get('one_off'):
            data = dict(data, cycle=None)
        put(c,'subscription:'+data['key'],{k:v for k,v in dict(previous, **data).items() if k not in ('ends_on','end_kind')})
        if K is not None:
            _stage_annual_reminder(K, c, data['key'], today)
    elif kind=='obligation_end':
        E.save(c,K._facade_agent() if K else 'ledger',data.get('fixed_id'),data,today or datetime.date.today().isoformat())
    elif kind=='debt_balance':
        sid=data.get('id'); amount=data.get('amount')
        repayment_type=L.owner_type(data)
        if amount is None and 'ends_on' not in data and 'end_kind' not in data and repayment_type is None and 'annual_rate' not in data:
            raise ValueError('principal balance, maturity or repayment type required')
        day=today or datetime.date.today().isoformat()
        debts=H.history(c,day)['debts']
        debt=next((d for d in debts if type(sid) is int and d['id']==sid),None)
        if not debt:
            raise ValueError('unknown loan')
        previous=L.resolve_facts(c,debts,facts(c)).get('debt:'+str(sid),{})
        balance=dict(previous)
        if amount is not None:
            as_of=datetime.date.fromisoformat(data.get('as_of',day)).isoformat()
            if type(amount) is not int or amount<0 or as_of>day:
                raise ValueError('principal balance needs an exact nonnegative amount as of today or earlier')
            balance.update(amount=amount,as_of=as_of)
        fid=data.get('fixed_id') or debt['repayment_stream_id']
        if fid:
            balance['fixed_id']=fid
        if data.get('fixed_id'):
            if not c.execute("SELECT 1 FROM fixed_cost WHERE id=? AND class='loan' AND direction='expense'",(fid,)).fetchone():
                raise ValueError('maturity needs a loan repayment stream')
            if debt['id']<0 and fid!=debt['repayment_stream_id'] or any(d['id']!=sid and d['id']>0 and d['repayment_stream_id']==fid for d in debts):
                raise ValueError('unrelated or already linked loan stream')
            balance['fixed_id']=fid
        if repayment_type is not None:
            balance.update(repayment_type=repayment_type,repayment_type_source='owner')
        for field in ('annual_rate', 'payment', 'payment_day', 'next_payment_on'):
            if field in data:
                balance[field] = data[field]
        if 'payment_day' not in balance and debt.get('payment_ids'):
            last = c.execute('SELECT date FROM spend_entry WHERE id=?', (debt['payment_ids'][-1],)).fetchone()
            balance['payment_day'] = int(last[0][8:])
        estimate = L.estimate_maturity(dict(balance, repayment_type=balance.get('repayment_type', debt['repayment_type'])), debt.get('latest_payment', 0))
        if estimate:
            balance['maturity_estimate'] = estimate
            if not ('ends_on' in data or 'end_kind' in data) and (
                    not debt.get('end_asked') or debt.get('end_provenance') == 'estimated'):
                if not fid:
                    fid=c.execute("INSERT INTO fixed_cost(name,amount,class,source) VALUES(?,0,'loan','owner')",(debt['name'],)).lastrowid
                E.save(c,K._facade_agent() if K else 'ledger',fid,estimate,day)
                balance['fixed_id']=fid
        elif debt.get('end_provenance') == 'estimated' and not ('ends_on' in data or 'end_kind' in data):
            balance.pop('maturity_estimate', None)
            E.save(c,K._facade_agent() if K else 'ledger',fid,dict(end_kind='unknown',provenance='estimated'),day)
        if 'ends_on' in data or 'end_kind' in data or repayment_type=='credit_line':
            if not fid:
                # An identity anchor, never an invented repayment amount.
                fid=c.execute("INSERT INTO fixed_cost(name,amount,class,source) VALUES(?,0,'loan','owner')",(debt['name'],)).lastrowid
            terms=dict(ends_on=None,end_kind='none') if repayment_type=='credit_line' else data
            E.save(c,K._facade_agent() if K else 'ledger',fid,terms,day)
            balance['fixed_id']=fid
        if balance:
            put(c,'debt:'+str(sid),dict(balance,said=said,kind=kind,id=sid))
    else:
        raise ValueError('unknown plan fact')
    return dict(saved=True,kind=kind)



def _annual_row(c, owner, key):
    prior=c.execute('SELECT id,facts,record_key FROM contract_record WHERE owner=? AND record_key=?',(owner,'ledger:yearly:'+key)).fetchone()
    if prior: return prior
    ids={sid for name,sid in c.execute('SELECT name,stream_id FROM spend_entry WHERE stream_id IS NOT NULL') if _subscription_key(name,c)==key}
    if len(ids)==1:
        matches=c.execute('SELECT id,facts,record_key FROM contract_record WHERE owner=? AND fixed_id=?',(owner,next(iter(ids)))).fetchall()
        if len(matches)==1: return matches[0]
    return None


def _stage_annual_reminder(K, c, key, today=None):
    """Keep annual terms pending until the owner's explicit reminder choice."""
    import contracts
    owner = K._facade_agent()
    record_key = 'ledger:yearly:'+key
    prior = _annual_row(c,owner,key)
    if prior: record_key=prior[2]
    fact = facts(c).get('subscription:'+key, {})
    paid = fact.get('anchor_date') or max((date for name,date in c.execute(
                "SELECT name,date FROM spend_entry WHERE kind='expense' AND COALESCE(class,'spend')='spend' AND scope<>'business' AND date<=?",
                (today or datetime.date.today().isoformat(),)) if _subscription_key(name,c)==key),default=None)
    # The charge must be in a complete month of this evidence-only read.
    evidence_day = _advance_date(paid[:7]+'-01', 1) if paid else None
    sub = next((s for s in read(c, evidence_day, display=False)['subscriptions'] if s['key']==key), None) if paid else None
    # Only saved owner consent enables a reminder; cancelled terms stay inert.
    cancelled = bool(fact.get('cancelled') or (sub and sub['cancelled']))
    if cancelled or fact.get('cycle') != 'yearly':
        if prior:
            terms = json.loads(prior[1])
            terms.update(reminder_enabled=False)
            if cancelled: terms['status']='cancelled'
            elif fact.get('cancelled') is False: terms['status']='active'
            c.execute('UPDATE contract_record SET facts=?,revision=revision+1 WHERE id=?',
                      (json.dumps(terms),prior[0]))
        return
    if not sub or not sub['renewal']:
        return
    renewal = _yearly_renewal(fact.get('anchor_date') or sub['last']['date'], today or datetime.date.today().isoformat())
    terms = dict(json.loads(prior[1]) if prior else {},cycle='yearly',amount=sub['yearly_amount'],renews_on=renewal,
                 reminder_days=YEARLY_REMINDER_DAYS,reminder_at='09:00',
                 reminder_enabled=fact.get('reminder_enabled',False),status='active')
    contracts.validate(terms)
    sources = {k:('owner' if k in ('cycle','status') or (k=='reminder_enabled' and k in fact) else 'document' if k=='amount' else 'calculated') for k in terms}
    if prior:
        prior_sources=json.loads(c.execute('SELECT sources FROM contract_record WHERE id=?',(prior[0],)).fetchone()[0])
        staged={'cycle','amount','renews_on','reminder_days','reminder_at','reminder_enabled','status'}
        for field,origin in prior_sources.items():
            if field not in staged: sources[field]=origin
    c.execute("INSERT INTO contract_record(owner,record_key,kind,title,provider,facts,sources,fixed_id) "
              "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(owner,record_key) DO UPDATE SET "
              "kind=excluded.kind,title=excluded.title,facts=excluded.facts,sources=excluded.sources,fixed_id=COALESCE(contract_record.fixed_id,excluded.fixed_id),"
              "revision=contract_record.revision+(contract_record.facts<>excluded.facts OR contract_record.title<>excluded.title OR contract_record.sources<>excluded.sources),"
              "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')",
              (owner,record_key,'subscription' if terms['reminder_enabled'] else 'ledger_yearly_pending',
               sub.get('display_name',sub['name']),key,json.dumps(terms),json.dumps(sources),sub['fixed_ids'][0] if len(sub['fixed_ids'])==1 else None))


def reconcile_annual_reminders(K,c,data=None,*,today=None):
    """Recover saved consent, then use the existing contract/task transaction."""
    import contracts
    if data is None:
        meta=facts(c)
        keys={_subscription_key(key[13:],c) for key,fact in meta.items()
              if key.startswith('subscription:') and fact.get('cycle')=='yearly'
              and fact.get('reminder_enabled') is True and not fact.get('cancelled')}
        for key in sorted(keys):
            reconcile_annual_reminders(K,c,dict(kind='subscription',key=key),today=today)
        return
    if data.get('kind')=='batch':
        for item in data.get('items',[]):
            reconcile_annual_reminders(K,c,item,today=today)
        return
    if data.get('kind')!='subscription':
        return
    owner=K._facade_agent()
    row=_annual_row(c,owner,data['key'])
    carrier=contracts.get(c,owner,row[0]) if row else None
    fact=facts(c).get('subscription:'+data['key'],{})
    if (not carrier or carrier['kind'] in ('ledger_obligation','ledger_yearly_pending')) and (
            fact.get('cycle')=='yearly' and fact.get('reminder_enabled') is True
            and not fact.get('cancelled')):
        # Older installs saved Yes before the NULL-class receipt lookup failed.
        # An end-term-only carrier must be enriched in place, preserving its key.
        _stage_annual_reminder(K,c,data['key'],today)
        if c.in_transaction:
            c.commit()
        row=_annual_row(c,owner,data['key'])
    if row:
        carrier=contracts.get(c,owner,row[0])
        if carrier['facts'].get('reminder_enabled') or carrier['reminder']:
            contracts.reconcile(K,c,owner,row[0])


def _amount_change(change, fixed_id, subscription=None):
    """Keep the quote and conversion basis; never rewrite an observed charge."""
    if not isinstance(change,dict):
        raise ValueError('subscription amount change must be an object')
    amount=change.get('amount');currency=change.get('currency','KRW');month=change.get('effective_from')
    scope=change.get('scope')
    if type(amount) is not int or amount<0 or currency not in ('KRW','USD'):
        raise ValueError('subscription amount change requires whole nonnegative KRW or USD')
    if not isinstance(month,str) or not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])',month):
        raise ValueError('subscription amount change requires YYYY-MM')
    if scope not in (None,'vendor'):
        raise ValueError('subscription amount change scope must be vendor')
    result=dict(amount=amount,currency='KRW',effective_from=month,fixed_id=fixed_id)
    if scope=='vendor':
        if not subscription or not subscription.get('latest_full_month'):
            raise ValueError('vendor replacement requires a latest full month')
        result.update(scope=scope,basis_month=subscription['latest_full_month'],
                      previous_amount=subscription['latest_full_amount'])
    if currency=='USD':
        # Reuse the ledger's stored FX estimate, without borrowing another
        # vendor's VAT assumption. It is not a live exchange-rate quote.
        from ledger_profile import services
        config=json.loads(Path(__file__).with_name('subscription_tiers.json').read_text())
        rates={config['fx_krw_per_usd']} if config.get('fx_krw_per_usd') else {svc['source']['fx_krw_per_usd'] for svc in services().values()
               if svc.get('source',{}).get('fx_krw_per_usd')}
        if len(rates)!=1:
            raise ValueError('one evidenced ledger USD exchange rate required')
        rate=rates.pop()
        result.update(amount=round(amount*rate),original_amount=amount,original_currency=currency,
                      fx_krw_per_usd=rate,fx_source='subscription_tiers.json:fx_krw_per_usd')
    return result


def reduction_loan_basis(plan):
    """Plan at the roster's per-loan precision, preserving raw bill evidence."""
    plan['loan_payment_unit'] = 1000
    plan['loan'] = max(0, plan.get('loan_baseline', plan['loan']) +
                       L.forecast(plan, plan['today'][:7])['delta'])
    plan['out'] = plan['spend'] + plan['loan']
    plan['gap'] = plan['income'] - plan['out']


def subscription_services(plan, targets=None, month=None):
    """Issued services; optionally overlay owner choices without changing history."""
    import copy
    plan, targets = copy.deepcopy(plan), copy.deepcopy(targets)
    migrate_subscriptions(plan, targets)
    services = plan.get('current_subscriptions', plan['subscriptions'])
    if targets is None:
        return services
    month = month or plan['today'][:7]
    result = []
    for service in services:
        sub = dict(service)
        key = sub['key']
        change = sub.get('amount_change', {})
        quote = targets.get('subscription_quotes', {}).get(key, change)
        start = targets.get('subscription_effective_from', {}).get(key,
            quote.get('effective_from', targets.get('subscriptions_from', month)))
        amount = targets.get('subscription_targets', {}).get(key, change.get('amount'))
        if amount is not None and month >= start:
            sub.update(monthly=amount, owner_amount=dict(quote if quote.get('amount')==amount else {}, amount=amount))
        result.append(sub)
    return result


def subscription_basis(plan, targets):
    """Keep history for comparison; accepted plans use the issued service list."""
    forward = 'subscriptions' in targets and 'current_subscriptions' in plan
    services = subscription_services(plan) if forward else plan['subscriptions']
    other = sum(s['monthly'] for s in services if not is_ai_subscription(s))
    total = sum(s['monthly'] for s in services) if forward else plan['groups']['subscriptions']
    # Per-service rounding can differ from the rounded group by one won.
    # Preserve the other service amounts and reconcile that residual in AI.
    return dict(total=total, ai=max(0,total-other) if any(is_ai_subscription(s) for s in services) else 0)


def subscription_components(plan, targets, spend, month=None):
    """Allocate the projected total, keeping other services at their dated cost.

    AI cuts consume the AI component first. Cancellations and explicit vendor
    revisions of other services belong to those services, including yearly
    monthly shares; they must not appear as AI savings.
    """
    import copy
    plan, targets = copy.deepcopy(plan), copy.deepcopy(targets)
    migrate_subscriptions(plan, targets)
    month = month or plan['today'][:7]
    basis = subscription_basis(plan, {} if month < targets.get('subscriptions_from',month) else targets)
    other = basis['total'] - basis['ai']
    if month >= targets.get('subscriptions_from',month):
        services = subscription_services(plan) if 'subscriptions' in targets else plan['subscriptions']
        for sub in services:
            if is_ai_subscription(sub): continue
            amount = targets.get('subscription_targets',{}).get(sub['key'],
                sub.get('paid_monthly',0) if sub['cancelled'] else sub['monthly'])
            revision = targets.get('subscription_revisions',{}).get(sub['key'],{})
            if sub['key'] in targets.get('subscription_targets',{}) and revision:
                # Vendor revisions change the accepted total by their delta
                # from the evidenced bill, which can exceed its history share.
                amount += sub['monthly']-revision.get('previous',sub['monthly'])
            if month < targets.get('subscription_effective_from',{}).get(sub['key'],month):
                amount = sub['monthly']
            other += amount-sub['monthly']
    other = min(spend,max(0,other))
    return dict(ai=spend-other, other=other)


def projection(plan, targets, month=None):
    """Only explicit caps/counts yield savings; unknowns never disappear."""
    import copy
    plan, targets = copy.deepcopy(plan), copy.deepcopy(targets)
    migrate_subscriptions(plan, targets)
    month=month or plan['today'][:7]
    estimate = plan.get('current_estimate')
    if estimate and month == estimate['month'] and not plan.get('month_progress'):
        plan = dict(plan, **{key: estimate[key] for key in ('groups','spend','loan','out','gap')})
    groups = plan['groups']
    capped = 'big' in targets and month >= targets.get('big_from', month)
    big = targets['big'] if capped else groups['big']
    from ledger_installment import at
    installment_load=sum(at(it,month)['this_month'] for it in plan.get('installments',[]))
    installment_load += sum(it['amount'] for it in plan.get('installment_tracking',[])
                            if it['active'] and (not it.get('end') or month <= it['end']))
    # The owner cap is only for non-installment shopping. Installments are a
    # cash-flow obligation added on top, rather than a floor that can swallow
    # the chosen cap.
    if capped:
        big += installment_load
    elif targets.get('big_pause_from', '9999-12') <= month < targets.get('big_from', month):
        big = installment_load
    # Quiet long-cycle contracts can contribute a monthly share without a
    # payment in the displayed window. Keep that cost unless the owner cuts
    # it; an absent service row is not a saving. Known cancellations still
    # replace their historical share with the retained paid-term amount.
    default_subs = max(0, groups['subscriptions'] + sum(
        s.get('paid_monthly',s['monthly'] if s.get('ends_on') and not E.ended(s,plan['today'][:7]) else 0)-s['monthly']
        for s in plan['subscriptions'] if s['cancelled']))
    subs = targets.get('subscriptions',default_subs)
    if month < targets.get('subscriptions_from',month):
        subs=groups['subscriptions']
    else:
        for key,start in targets.get('subscription_effective_from',{}).items():
            if month<start:
                sub=next(s for s in plan['subscriptions'] if s['key']==key)
                revision=targets.get('subscription_revisions',{}).get(key,{})
                subs+=revision.get('previous',sub['monthly'])-targets['subscription_targets'][key]
    if any(type(v) is not int or v<0 for v in (big,subs)):
        raise ValueError('category caps must be nonnegative integer won')
    # Accepted subscription plans use the issued recent service list. The
    # historical comparison, including months before the start, stays intact.
    basis_targets = {} if month < targets.get('subscriptions_from',month) else targets
    subs = min(subs,subscription_basis(plan,basis_targets)['total'])
    living = plan['spend']-groups['big']-groups['subscriptions']
    savings = groups['big']-big + groups['subscriptions']-subs
    cuts = {}
    for key,count in targets.get('counts',{}).items():
        if key not in LEVERS or type(count) is not int or count<0:
            raise ValueError('only explicit monthly habit counts are supported')
        m = plan['metrics'][key]
        if not m['unit_price']:
            raise ValueError('count needs an evidenced per-visit price')
        # Uncertain split orders, dust and unmatched amounts remain untouched.
        cuts[key] = min(max(0,groups[key]),max(0,round((m['monthly_count']-count)*m['unit_price'])))
    for key, amount in targets.get('habit_budgets', {}).items():
        if key not in LEVERS or type(amount) is not int or amount < 0:
            raise ValueError('habit budgets need exact nonnegative won')
        cuts[key] = groups[key]-amount
    saved_habits = sum(cuts.values())
    savings += saved_habits
    deficit = max(0, -plan['gap'])
    result = dict(big=big,subscriptions=subs,living=living-saved_habits,
                  saved=savings,cuts=cuts,deficit=deficit,remaining=max(0,deficit-savings),
                  projected_gap=plan['gap']+savings)
    from ledger_progress import compare
    progress = compare(plan, targets, result, month)
    if progress:
        rows = progress['buckets']
        result.update(big=rows['big']['attainable'], subscriptions=rows['subscriptions']['attainable'],
                      living=sum(row['attainable'] for key,row in rows.items() if key not in ('big','subscriptions','loans')),
                      saved=plan['spend']+plan['loan']-progress['attainable'],
                      progress=progress)
        # Legacy snapshots retain their saved gap basis when income is absent.
        income = plan.get('income')
        if income is not None:
            result.update(remaining=max(0,progress['attainable']-income),
                          projected_gap=income-progress['attainable'])
    return result


def commit(c, data):
    """Idempotent monthly budget publication, after all three reductions."""
    today = datetime.date.fromisoformat(data['today']).isoformat()
    if not data.get('said') or not data.get('nonce'):
        raise ValueError('final owner confirmation required')
    plan = data['plan']; targets = data['targets']
    for datekey in ('big_from','big_pause_from','subscriptions_from'):
        if datekey in targets and not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])',targets[datekey]):
            raise ValueError('target start must be YYYY-MM')
    for key in ('big','subscriptions'):
        if type(targets.get(key)) is not int or targets[key]<0:
            raise ValueError('complete explicit category caps required')
    if not any(key in targets for key in ('counts','habit_budgets')) or not set(targets.get('counts',{})).issubset(LEVERS):
        raise ValueError('habits review required')
    from ledger_progress import snapshot
    plan = dict(plan, today=today, month_progress=snapshot(c, today, plan))
    p = projection(plan, targets)
    extra,accepted = targets.get('extra_income',0),targets.get('accepted_deficit',0)
    if any(type(v) is not int or v<0 for v in (extra,accepted)):
        raise ValueError('deficit decisions need nonnegative whole won')
    if data.get('shortfall_policy') == 'show':
        if extra:
            raise ValueError('the reduction flow cannot plan unearned income')
    elif extra+accepted < p['remaining']:
        raise ValueError('remaining deficit needs an owner decision')
    w = W.window(today)
    detail = dict(data,plan=plan,window=w,budget_start=today[:7]+'-01' if plan.get('month_progress') else w['budget_start'],projection=p,effective_from=targets.get('effective_from',today[:7]))
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])',detail['effective_from']):
        raise ValueError('effective month must be YYYY-MM')
    put(c,'budget',detail)
    return dict(saved=True,projection=p,window=w,budget_start=w['budget_start'])


def _budget_income(c, stored):
    """Ignore old income targets only with reduction-onboarding provenance.

    Old flow budgets anchor their goal to the same plan nonce. Other callers
    of the budget API retain their own income policy. This is read-only.
    """
    extra = stored['targets'].get('extra_income',0)
    if stored.get('shortfall_policy') == 'show':
        extra = 0
    elif extra and stored.get('goal_id') and stored.get('nonce'):
        row = c.execute('SELECT detail FROM ledger_goal WHERE id=?',(stored['goal_id'],)).fetchone()
        try:
            detail = json.loads(row[0] or '{}') if row else {}
        except (TypeError,ValueError):
            detail = {}
        if isinstance(detail,dict) and detail.get('plan_nonce') == stored['nonce']:
            extra = 0
    return stored['plan']['income'] + extra


def budget(c, today):
    if not c.execute("SELECT 1 FROM sqlite_master WHERE name='ledger_preference'").fetchone():
        return None
    stored = facts(c).get('budget')
    if not stored or today[:7] < stored['effective_from']:
        return None
    meta = facts(c)
    for field in ('subscriptions','current_subscriptions'):
        for sub in stored.get('plan',{}).get(field,[]):
            fact=meta.get('subscription:'+sub['key'],{})
            if 'category' in fact: sub['category']=fact['category']
    cache = merchant_kinds(c)
    totals = collections.Counter()
    oneoff_ids = _oneoff_ids(c,today,meta)
    periodic_rows = {r['id']:item for item in _periodic_costs(c,meta,today,cache) for r in item['rows']}
    start = stored.get('budget_start', stored['today'][:7]+'-01')
    if today < start:
        return None
    for row in _payments(c,today):
        if row['date'] < start:
            continue
        refund=meta.get('refund:'+str(row['id']),{})
        if row['class']=='reimbursement' and refund.get('bucket') in BUCKETS:
            key='subscriptions' if refund['bucket']=='subscriptions' else 'big' if refund['bucket']=='big' else 'living'
            totals[key]-=row['amount']
        if row['kind']!='expense' or row['class']!='spend' or row['id'] in oneoff_ids:
            continue
        key = periodic_rows[row['id']]['bucket'] if row['id'] in periodic_rows else bucket(row,meta,cache)
        key = ('subscriptions' if key=='subscriptions' else 'big'
               if abs(row.get('purchase_amount',row['amount']))>=100000 and key not in BIG_EXCLUDED else 'living')
        totals[key] += row['amount']
    from ledger_installment import billed_groups
    repeated = [[r for r in rows if r['date']<=today] for rows in billed_groups(c)]
    repeated = [rows for rows in repeated if len(rows)>1]
    stopped_ids = {r[0] for r in c.execute("SELECT s.id FROM spend_entry s JOIN fixed_cost f ON f.id=s.stream_id WHERE f.state IN ('stopped','stop_suspected')")}
    from ledger_installment import listing
    current_plan = dict(stored['plan'],installments=listing(c,today[:7])['items'],
                        loans=H.history(c,today)['debts'],installment_tracking=_installment_tracking(
        c,today,meta,cache,repeated,periodic_rows,stopped_ids))
    from ledger_progress import snapshot as progress_snapshot
    current_plan['month_progress'] = progress_snapshot(c, today, current_plan)
    installments=sum(it['this_month'] for it in current_plan['installments'])
    # A published pre-step-seven plan may have treated cancel-ahead as
    # already free. Rehydrate its contract dates without rewriting history.
    snapshot=dict(stored['plan'],obligations=E.obligations(c,today[:7]))
    # Keep a retired obligation only when it contributed to this published
    # plan, so its dated stop can free that amount without subtracting a
    # different active bill from a freshly rebuilt plan.
    live_obligations = {o['id'] for o in snapshot['obligations']}
    for old in stored['plan'].get('obligations', []):
        stopped = c.execute("SELECT stopped_at FROM fixed_cost WHERE id=? AND state='stopped'", (old['id'],)).fetchone()
        if old['id'] not in live_obligations and stopped and stopped[0]:
            end = (datetime.date.fromisoformat(stopped[0][:10])-datetime.timedelta(days=1)).isoformat()
            snapshot['obligations'].append(dict(old,ends_on=end,end_kind='date',end_asked=True))
    # Owner loan corrections apply to an already published budget too. Keep
    # its original averaging window and caps; refresh only the loan facts.
    snapshot['loans']=current_plan['loans']
    snapshot['loan_baseline']=snapshot.get('loan_baseline',snapshot['loan'])
    old_months=snapshot['months']
    old_rows=[r for month in old_months for r in D._month_rows(c,month) if r['date']<=snapshot['today']]
    old_projection=W.projected_rows(c,snapshot['today']) if snapshot.get('projected') else []
    L.baselines(snapshot['loans'],old_rows,old_projection,snapshot['n'])
    subscriptions=[];restored=0
    eligible = {s['key'] for s in subscription_services(snapshot)}
    for sub in snapshot['subscriptions']:
        ids=sub.get('fixed_ids') or _subscription_fixed_ids(c,sub['key'],sub.get('members',[]),sub.get('last'))
        term=E.term(c,ids[0]) if len(ids)==1 else dict(ends_on=None,end_kind='unknown',end_asked=False)
        paid=E.paid_monthly(c,ids,sub['monthly'],snapshot['today'][:7])
        if paid and sub['key'] in eligible and sub['cancelled'] and not sub.get('ends_on') and sub['key'] not in stored['targets'].get('subscription_targets',{}):
            restored+=paid
        subscriptions.append(dict(sub,**term,fixed_ids=ids,paid_monthly=paid))
    snapshot['subscriptions']=subscriptions
    if 'current_subscriptions' in snapshot:
        hydrated = {s['key']:s for s in subscriptions}
        snapshot['current_subscriptions'] = [dict(s, **{k:hydrated[s['key']][k] for k in (
            'ends_on','end_kind','end_asked','paid_monthly','fixed_ids')}) for s in snapshot['current_subscriptions']]
    future_targets=dict(stored['targets'],subscriptions=stored['targets']['subscriptions']+restored)
    future=E.forecast(snapshot,future_targets,today[:7])
    # Use the owner-corrected dated repayment in both the budget bars and
    # forecast. A historical monthly baseline must not revive a matured loan.
    current_plan['loan'] = future['loan']
    p = projection(current_plan,stored['targets'],month=today[:7])
    p['big']=max(installments,p['big'])
    p['subscriptions']+=restored
    if p.get('progress'):
        # Recorded cash and still-due commitments remain a real monthly floor.
        loan_floor = p['progress']['buckets']['loans']['attainable']
        future['projected_gap'] -= max(0,loan_floor-future['loan'])
        future['loan'] = max(future['loan'],loan_floor)
        recorded = p['progress']['snapshot']['actual']
        totals = dict(big=recorded['big'],subscriptions=recorded['subscriptions'],
                      living=sum(value for key,value in recorded.items() if key not in ('big','subscriptions','loans')))
    for key in ('living','subscriptions'):
        p[key]=max(0,p[key]-future['freed_by_bucket'][key])
    # Older published plans may not contain the income needed by the E card.
    # Keep their original budget rows rather than inventing monthly totals.
    monthly_money = None
    if all(key in stored['plan'] for key in ('spend','loan','income')):
        monthly_money = dict(spend=stored['plan']['spend']+stored['plan']['loan'],
                             budget=sum(p[key] for key in ('living','big','subscriptions'))+future['loan'],
                             income=_budget_income(c,stored),
                             loan=future['loan'])
    # The first period starts with today's remaining days after the cutoff.
    # Subsequent months always use full caps, even across a new cutoff date.
    full_month_big = p['big']
    first = today[:7] == start[:7]
    ratio = stored.get('window', {}).get('budget_days', 0)/stored.get('window', {}).get('days_in_month', 1) if first and 'window' in stored and not p.get('progress') else 1
    p = {key:round(value*ratio) if key in ('living','big','subscriptions') else value for key,value in p.items()}
    summary = shortfall_copy(snapshot,future_targets,monthly_money['income']-monthly_money['budget'],today,
                             lambda key, **kw: dict(key=key,values=kw),
                             format_amount=lambda value: value) if monthly_money and stored.get('shortfall_policy')=='show' else []
    next_month = E._next(today[:7])
    next_plan = E.forecast(snapshot, future_targets, next_month)
    return dict(progress=p.get('progress'),next_month=dict(month=next_month,big=next_plan['big'],
                    budget=sum(next_plan[key] for key in ('living','big','subscriptions','loan'))),
                big_budget=dict(month=today[:7],amount=full_month_big,targets=stored['targets']),
                shortfall_copy=summary,forecast=future,monthly_money=monthly_money,month=int(today[5:7]),day=(datetime.date.fromisoformat(today)-datetime.date.fromisoformat(start)).days+1 if first else int(today[8:10]),
                start=start if first else today[:7]+'-01', projected=bool(stored['plan'].get('current_estimate')) or stored['plan'].get('projected',False),
                rows=[dict(key=key,used=totals[key],target=p[key]) for key in ('living','big','subscriptions')])


def shortfall_copy(p, targets, projected_gap, today, translate, *, format_amount=man_one_decimal):
    """Use the owner-approved closing copy on the goal and published pin."""
    if projected_gap >= 0:
        return []
    lines = [translate('ledger.onboarding.reduce.shortfall', gap=format_amount(-projected_gap), loan=format_amount(min(p['loan'],-projected_gap)))]
    from ledger_obligation import end_schedule
    loans = [item for item in end_schedule(p, targets)
             if item['bucket'] == 'loan' and item['from_month'] > today[:7]]
    # A loan can have an owner end date before a fixed-cost carrier exists.
    # Use its same current payment basis rather than losing that known end.
    streams = {item['id'] for item in loans}
    for loan in p.get('loans',[]):
        end = loan.get('ends_on')
        if not end or loan.get('repayment_type') == 'credit_line':
            continue
        year, month = map(int,end[:7].split('-'))
        start = '%04d-%02d' % (year+month//12,month%12+1)
        if start <= today[:7] or loan.get('repayment_stream_id') in streams:
            continue
        unit = p.get('loan_payment_unit',1)
        amount = round(loan['latest_payment']/unit)*unit
        if amount > 0:
            loans.append(dict(id=loan['id'],from_month=start,freed=amount))
    if loans:
        first = min(loans, key=lambda item:(item['from_month'],item['id']))
        year, month = map(int, first['from_month'].split('-'))
        lines.append(translate('ledger.onboarding.reduce.shortfall_end', first_ending_loan=format_amount(first['freed']), year=year, month=month))
    return lines



def render_monthly_money(data, translate):
    """Owner's E shape: three monthly amounts on one shared ten-cell scale."""
    if not data:
        return ''
    from kit_life import _card_dwidth, _card_pad
    keys = ('spend', 'budget', 'income')
    labels = [translate('ledger.pin.money.'+key) for key in keys]
    values = [data[key] for key in keys]
    is_man = translate('ledger.money.style') == 'man'
    number = man_whole if is_man else lambda value: translate(
        'ledger.onboarding.history.won', n=comma(round(value)))
    numbers = [number(value) for value in values]
    label_width = max(map(_card_dwidth, labels))
    number_width = max(map(_card_dwidth, numbers))
    scale = max(0, *values)
    rows = []
    for label, value, amount in zip(labels, values, numbers):
        cells = min(10, max(0, int(value*10/scale+0.5))) if scale else 0
        bar = chr(0x2588)*cells+chr(0x2591)*(10-cells)
        # Two display columns separate even the widest label from its bar.
        rows.append(_card_pad(label,label_width+2)+bar+'  '+_card_pad(amount,number_width,'r'))
    gap = data['budget']-data['income']
    return (translate('ledger.pin.money.title')+'\n<pre>'+'\n'.join(rows)+'</pre>\n'+
            translate('ledger.pin.money.more' if gap>0 else 'ledger.pin.money.less',
                      amount=number(abs(gap)),
                      loan=man_one_decimal(data['loan']).rstrip('0').rstrip('.') if is_man else number(data['loan'])))


def big_budget_lines(month, amount, targets, translate, mark='• '):
    def amount_text(value):
        return (man_one_decimal(value).rstrip('0').rstrip('.') if translate('ledger.money.style') == 'man'
                else translate('ledger.onboarding.history.won', n=comma(round(value))))
    year, number = map(int, month.split('-'))
    lines = [translate('ledger.onboarding.reduce.budget_big_current', mark=mark,
        year=year, month=number, amount=amount_text(amount))]
    if targets.get('big_from',month) > month:
        year, number = map(int, targets['big_from'].split('-'))
        lines.append(translate('ledger.onboarding.reduce.budget_big_later', year=year,
            month=number, cap=amount_text(targets['big'])))
    return lines


def installment_label(item):
    """Extract the purchase from old combined answers without vendor aliases."""
    name = item.get('display_name') or item['name']
    clause = re.search(r'(?:^|\s)할부(?:는|은)\s*(.+)$', name)
    if clause:
        name = clause[1]
    name = re.split(r'\s*~?\s*\d{4}년\s*\d{1,2}월|\d{1,2}\s*(?:개월|months?)', name, maxsplit=1)[0]
    return name.strip(' .,/\n\t') or item['name']


def render_budget_progress(data, translate):
    """Owner-selected final plan, using the shared cash/commitment snapshot."""
    import html
    progress = data['progress']
    snap = progress['snapshot']
    day = datetime.date.fromisoformat(snap['today'])
    def t(key, **values):
        return translate('ledger.pin.plan.'+key, **values)
    def row(name, spent, budget, loan=False):
        delta = budget-spent
        title = t('over' if delta < 0 else 'loan_remaining' if loan else 'remaining',
                  name=name, amount=comma(abs(delta)))
        filled = min(10, max(0, round(spent*10/budget))) if budget > 0 else (10 if spent > 0 else 0)
        bar = '█'*filled+'░'*(10-filled)+('+' if delta < 0 else '')
        return title+'\n'+bar+' '+comma(spent).rjust(9)+' / '+comma(budget)
    rows = [row(t('month',month=day.month), progress['spent'], progress['attainable'])]
    # These are the three bucket rows selected in the approved final screen.
    for key in ('subscriptions', 'big', 'loans'):
        bucket = progress['buckets'][key]
        if not (bucket['spent'] or bucket['committed'] or bucket['reserve']):
            continue
        # Loans show exact scheduled repayments rather than the rounded
        # historical benchmark. Spending budgets retain signed overruns.
        target = bucket['attainable'] if key == 'loans' else bucket['budget']
        rows.append(row(t(key), bucket['spent'], target, loan=key == 'loans'))
    lines = [t('title'), t('period',month=day.month,day=day.day),
             '<pre>'+html.escape('\n\n'.join(rows))+'</pre>']
    for pending in snap['pending']:
        if not pending.get('date') or pending['bucket'] not in ('big','loans'):
            continue
        due = datetime.date.fromisoformat(pending['date'])
        if pending['bucket'] == 'big':
            # A retained cap may allow other shopping; only promise a pause
            # when the remaining plan is fully taken by installments.
            key = 'installment_pause' if progress['buckets']['big']['available'] == 0 else 'installment'
            lines.append(t(key,day=due.day,name=html.escape(pending['name'])))
        else:
            lines.append(t('loan_due',day=due.day))
    if data.get('next_month'):
        future = data['next_month']
        lines.append(t('next_month',month=int(future['month'][5:]),
                       amount=comma(future['budget']),big=comma(future['big'])))
    return '\n'.join(lines)


def render_budget(data, translate):
    """Use the E card when totals exist; preserve approved legacy budget rows."""
    if not data:
        return ''
    if data.get('progress') and data['progress']['snapshot'].get('actual_ids'):
        return render_budget_progress(data, translate)
    if data.get('monthly_money'):
        body = render_monthly_money(data['monthly_money'],translate)
        if data.get('shortfall_copy'):
            # Keep raw won through the kit/service boundary; use the existing
            # history won template in English and the unchanged ko decimals.
            def amount(value):
                return (man_one_decimal(value) if translate('ledger.money.style') == 'man'
                        else translate('ledger.onboarding.history.won', n=comma(value)))
            lines = [translate(row['key'],**{
                key: amount(value) if key in ('gap', 'loan', 'first_ending_loan')
                and isinstance(value, (int, float)) else value
                for key, value in row['values'].items()}) for row in data['shortfall_copy']]
            # Keep the existing localized chart if this bundle has no approved ending.
            if all(line != row['key'] for line,row in zip(lines,data['shortfall_copy'])):
                body = body.rsplit('\n',1)[0]+'\n'+'\n'.join(lines)
        if data.get('big_budget'):
            big = data['big_budget']
            chart, summary = body.split('</pre>\n',1)
            body = chart+'</pre>\n<blockquote>'+'\n'.join(big_budget_lines(
                big['month'],big['amount'],big['targets'],translate))+'</blockquote>\n'+summary
        return body
    if not data.get('rows'):
        return ''
    import unicodedata
    def width(s):
        return sum(2 if unicodedata.east_asian_width(c) in ('W','F') else 1 for c in s)
    labels={row['key']:translate('ledger.pin.'+row['key']) for row in data['rows']}
    size=max(width(s) for s in labels.values())
    lines=[translate('ledger.pin.header',month=data['month'],day=data['day'])]
    for row in data['rows']:
        name=labels[row['key']];name+=' '*(size-width(name))
        used,target=row['used'],row['target']
        cells=min(10,max(0,used*10//target)) if target>0 else (10 if used>0 else 0)
        bar=chr(0x2588)*cells+chr(0x2591)*(10-cells)
        number=man_one_decimal
        line = translate('ledger.pin.row',name=name,bar=bar,over='+' if used>target else '',used=number(used),target=number(target))
        lines.append(line+(' '+translate('ledger.benefit.estimated') if data.get('projected') else ''))
    return '\n'.join(lines)


def reset(c):
    """Payment IDs and the published budget expire when the ledger is wiped."""
    c.execute("DELETE FROM ledger_preference WHERE topic=? AND "
              "(item='budget' OR item LIKE 'item:%' OR item LIKE 'refund:%' OR item LIKE 'debt:%' OR item LIKE 'installment:%')",(TOPIC,))
