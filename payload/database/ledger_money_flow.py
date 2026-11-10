"""Registered saving destinations and the three completed month flow card.

Investment moves keep the ledger's spend money effect; module_invest is
their separate projection, independent of whether that module is enabled.
"""
import collections
import calendar
import datetime
import re

import ledger_profile as LP


def registered(methods, name):
    from ledger_v6 import nfkc, _method_layer
    mid = methods.names.get(nfkc(name))
    if mid is None:
        return {}
    method = dict(methods.row(methods.canon(mid)))
    method['layer'] = _method_layer(method)
    return method


def saving_destination(methods, row, partner=None):
    """Only instrument evidence, never source categories or payee words."""
    from ledger_v6 import effect
    own = registered(methods, row.get('method'))
    other = registered(methods, (partner or {}).get('method'))
    named = registered(methods, row.get('name'))
    if effect(row) > 0 and other.get('kind') == 'savings' and effect(partner) < 0:
        return other
    if effect(row) > 0 and named.get('kind') == 'savings' and own.get('layer') != 'card':
        return named
    if effect(row) < 0 and own.get('kind') == 'savings':
        return own
    return None


def investment_destination(methods, row, partner=None):
    from ledger_v6 import norm
    own = registered(methods, row.get('method'))
    if own.get('layer') == 'card' or own.get('kind') == 'securities':
        return None
    other = registered(methods, (partner or {}).get('method'))
    named = registered(methods, row.get('name'))
    if other.get('kind') == 'securities' or named.get('kind') == 'securities':
        return (other if other.get('kind') == 'securities' else named)['name']
    if (any(norm(x) in norm(row.get('name')) for x in LP.EXCHANGE)
            or re.search(r'증권|securities|brokerage', row.get('name') or '', re.I)):
        return row['name']
    return None


def reconcile(K, c):
    """Repair old inferred saving through the same path as imports/replay."""
    from ledger_v6 import Methods, rows
    methods = Methods(c)
    payments = rows(c, 'SELECT * FROM spend_entry ORDER BY id')
    pairs = collections.defaultdict(list)
    for row in payments:
        if row['pair_id'] is not None:
            pairs[row['pair_id']].append(row)
    changed = []
    streams = set()
    from ledger_review import annotations
    owner = annotations(c)
    for row in payments:
        if owner.get(row['id'], {}).get('owner_class') is not None:
            continue
        if row['class'] != 'saving':
            continue
        partner = next((p for p in pairs[row['pair_id']] if p['id'] != row['id']), None)
        if (partner is None and row['kind'] == 'expense' and row['amount'] < 0
                and registered(methods, row['method']).get('kind') == 'savings'):
            # The old importer stored an unpaired incoming leg as a negative
            # expense. Preserve the account direction and remove its shadow.
            c.execute("UPDATE spend_entry SET kind='transfer',amount=? WHERE id=?", (-row['amount'], row['id']))
            K._spend_shadow_del(c, row['id'])
            row.update(kind='transfer', amount=-row['amount'])
            changed.append(row['id'])
        if saving_destination(methods, row, partner):
            continue
        # A prior saving outflow already has the expense sign and shadow.
        cls = 'spend' if row['kind'] == 'expense' else 'own_transfer'
        c.execute('UPDATE spend_entry SET class=?,stream_id=NULL,is_fixed=0 WHERE id=?', (cls, row['id']))
        row['class'] = cls
        changed.append(row['id'])
        if row['stream_id']:
            streams.add(row['stream_id'])
    import ledger_diag as D
    for fid in streams:
        members = [r for r in payments if r['stream_id'] == fid]
        if members and all(r['class'] != 'saving' for r in members):
            c.execute("UPDATE fixed_cost SET class=? WHERE id=? AND class='saving' AND source='ledger_v6'",
                      (D._class_of(members, LP.merchant_key(members[-1]['name'])), fid))
    return changed


def summary(c, today):
    from ledger_v6 import Methods, prev_month, rows
    import ledger_diag as D
    import ledger_window as W
    import ledger_review as R
    from ledger_plan import facts
    import ledger_actual as A
    today = today.isoformat() if isinstance(today, datetime.date) else today
    months = W.window(today)['average_months']
    methods = Methods(c)
    payments = [r for month in months for r in D._month_rows(c, month)]
    pairs = collections.defaultdict(list)
    for row in rows(c, 'SELECT * FROM spend_entry WHERE date BETWEEN ? AND ?',
                    (months[0]+'-01', today[:7]+'-01')):
        if row['pair_id'] is not None:
            pairs[row['pair_id']].append(row)
    meta = facts(c)
    annotations = R.annotations(c)
    investments = []
    for row in payments:
        if row['kind'] != 'expense' or row['class'] != 'spend':
            continue
        partner = next((p for p in pairs[row['pair_id']] if p['id'] != row['id']), None)
        destination = investment_destination(methods, row, partner)
        if destination or A.module_fact(row, meta).get('module') == 'module_invest':
            investments.append(dict(id=row['id'], module='module_invest', name=destination or row['name'], amount=row['amount']))
    investment_ids = {r['id'] for r in investments}
    income = sum(r['amount'] for r in payments if r['kind']=='income' and r['class']=='income') / 3
    # Fixed obligations use their current monthly amount; annual contracts
    # use one twelfth, independent of the three-month variable denominator.
    from ledger_plan import _periodic_costs, _oneoff_ids, merchant_kinds, bucket, _subscription_key, _subscription_recurs
    import ledger_obligation as E
    from ledger_installment import billed_groups, group_schedules, listing
    streams = {r['id']: r for r in D.rows(c, 'SELECT * FROM fixed_cost')}
    cache = merchant_kinds(c)
    periodic = _periodic_costs(c, meta, today, cache)
    periodic_ids = {r['id'] for item in periodic for r in item['rows']}
    installments = []
    installment_ids = set()
    for group in billed_groups(c):
        group = [r for r in group if r['date'] <= today]
        if len(group) < 2:
            continue
        schedules = group_schedules(c, group)
        # Recurrence discovers a candidate. A stored fact supplies its term
        # and item name; equal charges alone cannot establish a last date.
        if len(schedules) == 1:
            installment_ids.update(r['id'] for r in group)
    for it in listing(c, today[:7], include_all=True)['items']:
        if it.get('spend_id'):
            installment_ids.add(it['spend_id'])
        installment_ids.update(it.get('observed_ids', []))
        if not it['this_month'] or it['state'] != 'active':
            continue
        day = it.get('charge_day') or int(it['purchase_date'][8:])
        y, m = map(int, it['end'].split('-'))
        end = '%s-%02d' % (it['end'], min(day, calendar.monthrange(y, m)[1]))
        if end < today:
            continue
        installments.append(dict(name=it['name'], amount=it['this_month'], ends_on=end))
    fixed_groups = collections.defaultdict(list)
    fixed_ids = set(periodic_ids)
    # Current paid bills may replace the previous month's amount. No open
    # month variable spending or income enters a displayed average.
    current_rows = [r for r in D._month_rows(c, today[:7]) if r['date'] <= today]
    def family(row):
        return row['class'], LP.merchant_key(row['name']) or D.norm(row['name']), row['method']
    families = collections.defaultdict(set)
    for row in payments + current_rows:
        if row.get('stream_id'):
            families[family(row)].add(row['stream_id'])
    for row in payments + current_rows:
        candidates = families[family(row)]
        if not row.get('stream_id') and len(candidates) == 1:
            row['stream_id'] = next(iter(candidates))
    for r in payments + current_rows:
        if (r['kind'] != 'expense' or r['class'] != 'spend'
                or r['id'] in investment_ids | installment_ids | periodic_ids):
            continue
        stream = streams.get(r.get('stream_id'), {})
        if D.is_transit(r['name']):
            continue
        cls = R.fixed_class(r, stream, annotations.get(r['id'], {}), r.get('is_fixed'))
        if not cls and bucket(r, meta, cache) in ('fixed', 'subscriptions'):
            cls = 'subscription' if bucket(r, meta, cache) == 'subscriptions' else 'other'
        if cls not in R.FIXED or cls == 'loan':
            continue
        fixed_ids.add(r['id'])
        identity = ('stream', stream['id']) if stream else (cls, LP.merchant_key(r['name']), r['method'])
        fixed_groups[identity].append(dict(r, fixed_kind=cls))
    subscription_evidence = collections.defaultdict(list)
    for row in D.rows(c, "SELECT date,name,amount FROM spend_entry WHERE kind='expense' "
                        "AND class='spend' AND scope<>'business' AND date<=?", (today,)):
        subscription_evidence[_subscription_key(row['name'])].append(row)
    fixed, kinds = 0, collections.Counter()
    fixed_components = []
    for item in periodic:
        fixed += item['monthly']
        kind = 'subscription' if item['bucket']=='subscriptions' else R.fixed_class(
            item['rows'][-1], streams.get(item['rows'][-1].get('stream_id'), {}),
            annotations.get(item['rows'][-1]['id'], {}), True) or 'tax'
        kinds[kind] += item['monthly']
        fixed_components.append(dict(name=item['rows'][-1]['name'],kind=kind,monthly=item['monthly']))
    for members in fixed_groups.values():
        latest = max(members, key=lambda r:(r['date'],r['id']))
        stream = streams.get(latest.get('stream_id'), {})
        end = E.term(c, stream.get('id')).get('ends_on')
        vendor = _subscription_key(latest['name'])
        sub = meta.get('subscription:'+vendor, {})
        if latest['fixed_kind'] == 'subscription' and not (
                sub.get('cycle') or stream.get('source') in ('owner','chat','interview')
                or _subscription_recurs(subscription_evidence[vendor])):
            # A service merchant alone does not establish a monthly bill.
            fixed_ids.difference_update(r['id'] for r in members)
            continue
        if latest['date'][:7] < months[-1] and stream.get('source') not in ('owner','chat','interview'):
            continue
        if (end and end < today) or (stream.get('state') in ('stopped','stop_suspected') or (sub.get('cancelled') and not end)):
            continue
        amount = stream.get('current_amount')
        if amount is None:
            amount = sum(r['amount'] for r in members if r['date'][:7] == latest['date'][:7])
        fixed += amount
        kinds[latest['fixed_kind']] += amount
        fixed_components.append(dict(name=latest['name'],kind=latest['fixed_kind'],monthly=amount))
    oneoff_ids = _oneoff_ids(c, today, meta)
    variable = (sum(r['amount'] for r in payments if r['kind']=='expense' and r['class']=='spend'
                    and r['id'] not in investment_ids | fixed_ids | installment_ids | oneoff_ids)
                - sum(r['amount'] for r in W.reviewed_credits(c, payments, meta, annotations))) / len(months)
    current = {}
    for key in ('loan', 'saving'):
        observed = [r for r in payments + current_rows if r['kind']=='expense' and r['class']==key]
        by_stream = collections.defaultdict(list)
        for r in observed:
            identity = r.get('stream_id') or (LP.merchant_key(r['name']), r['method'])
            by_stream[identity].append(r)
        current[key] = 0
        for members in by_stream.values():
            latest = max(members, key=lambda r:(r['date'],r['id']))
            stream = streams.get(latest.get('stream_id'), {})
            end = E.term(c, stream.get('id')).get('ends_on')
            if latest['date'][:7] < months[-1] or stream.get('state') == 'stopped' or (end and end < today):
                continue
            if key == 'loan' and not stream and not any(
                    D.prev_month(m, 1) in {r['date'][:7] for r in members}
                    for m in {r['date'][:7] for r in members}):
                continue
            amount = stream.get('current_amount')
            current[key] += amount if amount is not None else sum(r['amount'] for r in members if r['date'][:7]==latest['date'][:7])
    changes = []
    # Read the account's incoming legs too; their registered instrument is
    # the stable identity even when the outgoing description changes.
    saved = collections.defaultdict(list)
    for row in rows(c, "SELECT * FROM spend_entry WHERE scope<>'business' AND class='saving' AND date<?", (today[:7]+'-01',)):
        partner = c.execute('SELECT method FROM spend_entry WHERE pair_id=? AND id<>? LIMIT 1', (row['pair_id'], row['id'])).fetchone()
        account = saving_destination(methods, row, dict(method=partner[0], kind='transfer', amount=abs(row['amount'])) if partner else None)
        if account:
            saved[account['name']].append(row['date'][:7])
    for name, seen in sorted(saved.items()):
        last = max(seen)
        if len(set(seen)) >= 2 and prev_month(months[0]) <= last < months[-1]:
            changes.append(dict(name=name, since=prev_month(last, -1)))
    investment = sum(r['amount'] for r in investments)
    return dict(months=months, income=income, living=fixed+variable, fixed=fixed, variable=variable,
                fixed_kinds=[k for k, amount in kinds.most_common() if amount > 0], fixed_components=fixed_components, installments=installments, **current,
                investment=investment, investment_names=sorted({r['name'] for r in investments}),
                changes=changes, out=fixed+variable+sum(it['amount'] for it in installments)+sum(current.values())+investment)
