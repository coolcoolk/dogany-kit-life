"""Recorded month cash and dated commitments, separate from history averages."""
import calendar
import collections
import hashlib
import json


def snapshot(c, today, plan):
    import ledger_diag as D
    import ledger_plan as P
    import ledger_review as R
    import ledger_profile as LP
    import ledger_obligation as E
    import ledger_window as W
    from ledger_installment import at

    month = today[:7]
    meta, cache = P.facts(c), P.merchant_kinds(c)
    rows = [r for r in D._month_rows(c, month) if r['date'] <= today]
    periodic = {r['id']: item for item in P._periodic_costs(c, meta, today, cache) for r in item['rows']}
    # Reuse the same unique observed merchant/account family as ledger_loan.
    last_day = month+'-%02d' % calendar.monthrange(int(month[:4]), int(month[5:]))[1]
    fixed_rows = D.rows(c, "SELECT * FROM fixed_cost WHERE direction='expense' AND state<>'stopped'")
    # Old streams can share a descriptor and account with the current contract.
    # They cannot make this month's unique carrier ambiguous indefinitely.
    dated_ids = {r['id'] for r in fixed_rows
                 if month+'-01' <= (r.get('next_due') or r.get('expected_next') or '')[:10] <= last_day}
    unbound_ids = {r['id'] for r in fixed_rows if not r.get('method_id')}
    method_names = dict(c.execute('SELECT id,name FROM pay_method'))
    bound_methods = {r['id']: method_names.get(r['method_id']) for r in fixed_rows if r.get('method_id')}
    def method_matches(fid, row):
        return fid not in bound_methods or row['method'] == bound_methods[fid]
    families, merchants = collections.defaultdict(set), collections.defaultdict(set)
    def family(row):
        return row['class'], LP.merchant_key(row['name']) or D.norm(row['name']), row['method']
    for row in D.rows(c, "SELECT name,method,class,stream_id FROM spend_entry WHERE kind='expense' AND scope<>'business' AND stream_id IS NOT NULL AND date<=?", (today,)):
        families[family(row)].add(row['stream_id'])
        merchants[family(row)[:2]].add(row['stream_id'])
    resolved = {}
    for row in rows:
        if row['kind'] != 'expense' or row['class'] not in ('spend', 'loan'):
            continue
        candidates = {fid for fid in families[family(row)] & dated_ids if method_matches(fid, row)}
        # A changed payment rail is allowed only for a unique observed merchant
        # family. Explicit stream links always win, including links to old terms.
        if not candidates:
            candidates = merchants[family(row)[:2]] & dated_ids & unbound_ids
        if row.get('stream_id'):
            resolved[row['id']] = row['stream_id']
        elif len(candidates) == 1:
            resolved[row['id']] = next(iter(candidates))
    for loan in plan.get('loans', []):
        for sid in loan.get('payment_ids', []):
            resolved.setdefault(sid, loan.get('repayment_stream_id'))
    subscription_ids = {r['id'] for r in fixed_rows if r['class'] == 'subscription'}
    actual = collections.Counter({k: 0 for k in (*P.BUCKETS, 'loans')})
    classified = []
    for row in rows:
        if row['kind'] != 'expense' or row['class'] not in ('spend', 'loan'):
            continue
        key = 'loans' if row['class'] == 'loan' else periodic.get(row['id'], {}).get('bucket') or P.bucket(row, meta, cache)
        # A uniquely observed subscription contract is stronger evidence than
        # the amount-based large-purchase fallback. Keep explicit owner buckets.
        fact = meta.get('item:'+str(row['id']), {})
        if (key == 'unknown' and fact.get('bucket') not in P.BUCKETS
                and not fact.get('module') and not row.get('category_override')
                and resolved.get(row['id']) in subscription_ids):
            key = 'subscriptions'
        if key != 'loans' and key not in P.BIG_EXCLUDED and abs(row.get('purchase_amount', row['amount'])) >= 100000:
            key = 'big'
        classified.append(dict(row, progress_bucket=key))
        actual[key] += row['amount']
    for row in W.reviewed_credits(c, rows, meta, R.annotations(c)):
        actual[row['credit_bucket']] -= row['amount']

    pending, past_due, undated, used = [], [], [], set()
    money_out = []
    def commitment(entry, linked, method=None):
        """Reserve unobserved cash due today or later; keep a past-due audit."""
        if entry['date'] and entry['amount'] > 0 and not linked:
            money_out.append(dict(entry, method=method) if method else entry)
        if entry['date'] and entry['date'] < today:
            past_due.append(dict(entry, status='observed' if linked else 'not_observed',
                                 matched=[{k: r[k] for k in ('id', 'date', 'name', 'amount', 'method')}
                                          for r in linked]))
        elif entry['amount'] > 0:
            (pending if entry['date'] else undated).append(entry)
    review = R.annotations(c)
    schedules = []
    for item in plan.get('installments', []):
        shaped = at(item, month)
        if shaped['this_month']:
            schedules.append(dict(item, amount=shaped['this_month']))
    for item in plan.get('installment_tracking', []):
        if (item['active'] and (not item.get('end') or month <= item['end'])
                and not any(set(item['ids']) & set(s.get('observed_ids', [s.get('spend_id')])) for s in schedules)):
            schedules.append(dict(item, id='tracking:'+str(item['ids'][0]), source_name=item['name']))
    installment_pending, installment_spent = {}, {}
    for item in schedules:
        candidates = [r for r in classified if r['progress_bucket'] == 'big' and (
            review.get(r['id'], {}).get('billed_installment_id') == item['id']
            or (r['name'] == item.get('source_name', item['name']) and r['method'] == item.get('method', item.get('card'))
                and (not item.get('charge_day') or int(r['date'][8:]) == item['charge_day'])
                and abs(r['amount']) == item['amount']))]
        # One observed charge cannot cover multiple schedules.
        paid = sum(r['amount'] for r in candidates if r['id'] not in used)
        used.update(r['id'] for r in candidates)
        installment_spent[str(item['id'])] = paid
        left = max(0, item['amount'] - paid)
        installment_pending[str(item['id'])] = left
        day = item.get('charge_day') or (int(item['last'][8:10]) if item.get('last') else None)
        due = month+'-%02d' % min(day, calendar.monthrange(int(month[:4]), int(month[5:]))[1]) if day else None
        commitment(dict(identity='installment:'+str(item['id']), bucket='big', amount=left, ai=False,
                        date=due, name=P.installment_label(item)), candidates if paid > 0 else [],
                   method=item.get('method') or item.get('card'))

    services = P.subscription_services(plan)
    ai_ids = {fid for s in services if P.is_ai_subscription(s) for fid in s.get('fixed_ids', [])}
    ai_keys = {s['key'] for s in services if P.is_ai_subscription(s)}
    ai_actual = sum(r['amount'] for r in classified if r['progress_bucket'] == 'subscriptions'
                    and P._subscription_key(r['name'], c, resolved.get(r['id'])) in ai_keys)
    scheduled = set()
    for fixed in fixed_rows:
        due = fixed.get('next_due') or fixed.get('expected_next')
        if not due or not month+'-01' <= due[:10] <= last_day or fixed.get('cycle') not in R.CYCLE_MONTHS:
            continue
        term = E.term(c, fixed['id'])
        if term.get('ends_on') and due[:10] > term['ends_on']:
            continue
        sub = next((s for s in services if fixed['id'] in s.get('fixed_ids', [])), None)
        if sub and sub['cancelled'] and not sub.get('ends_on'):
            continue
        if fixed['class'] == 'saving':
            # Savings leave the funding account but never enter spending or
            # reserved bars. Reuse explicit links and unique merchant history.
            aliases = {P.merchant_key(r[0]) for r in c.execute(
                'SELECT DISTINCT name FROM spend_entry WHERE stream_id=?', (fixed['id'],))}
            aliases.add(P.merchant_key(fixed['name']))
            peers = [f for f in fixed_rows if f['class'] == 'saving' and f['id'] in dated_ids
                     and P.merchant_key(f['name']) in aliases]
            linked = [r for r in rows if r['class'] == 'saving'
                      and ((r['kind'] == 'expense' and r['amount'] > 0)
                           or (r['kind'] == 'transfer' and r['amount'] < 0))
                      and (r.get('stream_id') == fixed['id'] or (
                          not r.get('stream_id') and P.merchant_key(r['name']) in aliases
                          and len([f for f in peers if method_matches(f['id'], r)]) == 1
                          and method_matches(fixed['id'], r)))]
            if not linked:
                amount = fixed['current_amount'] if fixed.get('current_amount') is not None else fixed['amount']
                if amount > 0:
                    money_out.append(dict(identity='stream:'+str(fixed['id']), bucket='saving',
                                          amount=amount, date=due[:10], name=fixed['name']))
            continue
        linked = [r for r in classified if resolved.get(r['id']) == fixed['id']]
        if not linked:
            # Exact fallback only when there is a single possible contract.
            peers = c.execute("SELECT COUNT(*) FROM fixed_cost WHERE name=? AND state<>'stopped'", (fixed['name'],)).fetchone()[0]
            if peers == 1:
                linked = [r for r in classified if not resolved.get(r['id'])
                          and P.merchant_key(r['name']) == P.merchant_key(fixed['name'])
                          and r['class'] == ('loan' if fixed['class'] == 'loan' else 'spend')
                          and method_matches(fixed['id'], r)]
        if any(r['id'] in used for r in linked):
            continue
        paid = sum(r['amount'] for r in linked)
        if paid > 0:
            used.update(r['id'] for r in linked)
        amount = fixed['current_amount'] if fixed.get('current_amount') is not None else fixed['amount']
        left = 0 if paid > 0 else amount
        key = 'loans' if fixed['class'] == 'loan' else 'subscriptions' if fixed['class'] == 'subscription' else 'fixed'
        entry = dict(identity='stream:'+str(fixed['id']), bucket=key, amount=left,
                     ai=fixed['id'] in ai_ids, date=due[:10], name=fixed['name'], provenance='estimated')
        scheduled.add(entry['identity'])
        # Variable AI bills are capacity, not a promised next invoice.
        if fixed['id'] in ai_ids and (sub or {}).get('amount_varies', True):
            if left > 0:
                money_out.append(entry)
            if due[:10] < today:
                commitment(dict(entry, amount=0), linked if paid > 0 else [])
            continue
        commitment(entry, linked if paid > 0 else [])
    reserve = sum(s['monthly'] for s in services if s.get('cycle') == 'yearly' and not s['cancelled']
                  and sum(r['amount'] for r in classified if r['progress_bucket'] == 'subscriptions'
                          and resolved.get(r['id']) in s.get('fixed_ids', [])) <= 0
                  and not scheduled.intersection('stream:'+str(fid) for fid in s.get('fixed_ids', [])))
    result = dict(version=2, month=month, start=month+'-01', today=today, actual=dict(actual),
                  actual_ids=[r['id'] for r in classified], ai_actual=ai_actual, pending=pending,
                  past_due=past_due, undated=undated, installment_pending=installment_pending, installment_spent=installment_spent, annual_reserve=reserve,
                  money_out=money_out)
    result['revision'] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
    return result


def compare(plan, targets, projected, month):
    """Signed benchmarks and attainable totals; never move an overrun elsewhere."""
    import ledger_plan as P
    snap = plan.get('month_progress')
    if not snap or snap['month'] != month:
        return None
    actual = snap['actual']
    committed = collections.Counter()
    for row in snap['pending']:
        committed[row['bucket']] += row['amount']
    budgets = dict(plan['groups'], big=projected['big'], subscriptions=projected['subscriptions'], loans=plan['loan'])
    for key in P.LEVERS:
        budgets[key] = targets.get('habit_budgets', {}).get(key, budgets[key]-projected['cuts'].get(key, 0))
    balances = {}
    for key, target in budgets.items():
        spent = actual.get(key, 0)
        reserve = snap['annual_reserve'] if key == 'subscriptions' else 0
        remaining = target-spent
        balances[key] = dict(budget=target, spent=spent, remaining=remaining, overrun=max(0, -remaining),
                             committed=committed[key], reserve=reserve,
                             available=max(0, remaining-committed[key]-reserve),
                             attainable=max(target, spent+committed[key]+reserve))
    ai_budget = targets.get('subscription_group_targets', {}).get('ai')
    if ai_budget is None:
        ai_budget = sum(targets.get('subscription_targets', {}).get(s['key'], s['monthly'])
                        for s in P.subscription_services(plan) if P.is_ai_subscription(s))
    if not ai_budget and 'ai' in targets:
        ai_budget = targets['ai']
    ai_remaining = ai_budget-snap['ai_actual']
    non_ai_pending = sum(r['amount'] for r in snap['pending'] if r['bucket'] == 'subscriptions' and not r['ai'])
    ai_available = max(0, min(ai_remaining, balances['subscriptions']['remaining']-non_ai_pending-snap['annual_reserve']))
    attainable = sum(b['attainable'] for b in balances.values())
    spent = sum(actual.values())
    return dict(snapshot=snap, buckets=balances, ai=dict(budget=ai_budget, spent=snap['ai_actual'],
                remaining=ai_remaining, available=ai_available), spent=spent,
                attainable=attainable, remaining=attainable-spent)
