"""One display window: the last three complete local calendar months."""
import calendar
import collections
import datetime

import ledger_diag as D
import ledger_review as R
import ledger_profile as LP
import ledger_obligation as E
import ledger_lifecycle as LC


def window(today):
    day = datetime.date.fromisoformat(today) if isinstance(today, str) else today
    elapsed = day.day - 1
    days = calendar.monthrange(day.year, day.month)[1]
    include = elapsed >= 14
    month = day.strftime('%Y-%m')
    return dict(display_revision=1, current_month=month, elapsed_days=elapsed, days_in_month=days,
                remaining_days=days-elapsed, include_current=include,
                history_through=(day.replace(day=1)-datetime.timedelta(days=1)).isoformat(),
                average_months=[D.prev_month(month, n) for n in (3, 2, 1)],
                budget_start=day.isoformat() if include else day.replace(day=1).isoformat(),
                budget_days=days-elapsed if include else days)


def reviewed_credits(c, rows, meta, annotations):
    """Owner-classified unmatched refunds are spending credits, not income.

    refund and expense_reimbursement carry the spend bucket they net; a
    cash_exchange is stored with bucket 'skip' and so credits nothing."""
    from ledger_plan import BUCKETS
    return [dict(r, credit_bucket=meta['refund:'+str(r['id'])]['bucket']) for r in rows
            if r['class']=='reimbursement' and annotations.get(r['id'], {}).get('history_received')
            and meta.get('refund:'+str(r['id']), {}).get('bucket') in BUCKETS
            and not c.execute('SELECT 1 FROM ledger_receipt_allocation WHERE receipt_id=?', (r['id'],)).fetchone()]


def projected_rows(c, today):
    """Ephemeral amounts only: unpaid prior-month fixed + 3-month daily spend.

    No forecast is inserted into spend_entry, or presented as an actual bill.
    A stream id is its identity; unlinked fixed bills use class/merchant/method.
    Future rows never contribute. Income is actual only.
    """
    from ledger_plan import bucket, facts, _subscription_key
    import ledger_actual as A
    w = window(today)
    if not w['include_current']:
        return []
    month = w['current_month']
    prior = [D.prev_month(month, n) for n in (3, 2, 1)]
    streams = {r['id']: r for r in D.rows(c, 'SELECT * FROM fixed_cost')}
    raw_fixed = dict(c.execute('SELECT id,is_fixed FROM spend_entry'))
    annotations = R.annotations(c)
    meta = facts(c)
    prior_rows = {m:D._month_rows(c, m) for m in prior}
    cadence = collections.defaultdict(set)
    for m, rows in prior_rows.items():
        for r in rows:
            if r['kind']=='expense' and r['class'] in ('loan','saving') and r['amount']>0:
                cadence[(r['class'],LP.merchant_key(r['name']),r['method'])].add(D.month_index(m))

    def fixed(r):
        # A one-time debt repayment or investment is not a monthly bill.
        if r['class'] in ('loan','saving') and not (r['stream_id'] or raw_fixed.get(r['id'])):
            months = cadence[(r['class'],LP.merchant_key(r['name']),r['method'])]
            if not any(m+1 in months for m in months):
                return False
        cls = R.fixed_class(r, streams.get(r['stream_id'], {}), annotations.get(r['id'], {}), raw_fixed.get(r['id']))
        return (r['class'] in ('loan', 'saving') or cls in R.FIXED
                or bucket(r, meta) in ('fixed', 'subscriptions')) and annotations.get(r['id'], {}).get('role') != 'one_off'

    def identity(r):
        return ('stream', r['stream_id']) if r['stream_id'] else (r['class'], D.norm(r['name']), r['method'])

    def eligible(r):
        if LC.future_excluded(r, meta, today):
            return False
        stream = streams.get(r['stream_id'], {})
        sub = meta.get('subscription:'+_subscription_key(r['name']), {})
        end = E.term(c, r['stream_id']).get('ends_on')
        if end and end < today:
            return False
        # Cancel-ahead retains its paid term; yearly charges keep their own workflow.
        return (stream.get('state') != 'stopped' and stream.get('cycle') != 'yearly'
                and (not sub.get('cancelled') or bool(end)) and sub.get('cycle') != 'yearly')

    actual = [r for r in D._month_rows(c, month) if r['date'] <= today]
    previous = collections.defaultdict(list)
    for r in prior_rows[prior[-1]]:
        if r['kind'] == 'expense' and r['class'] in D.OUT_CLASSES and fixed(r) and eligible(r):
            previous[identity(r)].append(r)
    # The open month has not necessarily gone through stream reconciliation.
    # Match unlinked current bills without writing stream ids or counting one
    # charge against several contracts from the same vendor.
    current = [r for r in actual if r['kind']=='expense' and r['class'] in D.OUT_CLASSES]
    cancelled = {sid for pair in A.order_metrics(current)['cancelled'] for sid in pair}
    charged = collections.Counter()
    for r in current:
        if r['stream_id']:
            charged[identity(r)] += r['amount']
    covered = {key for key, amount in charged.items() if amount > 0 and key in previous}

    def family(r):
        return r['class'], LP.merchant_key(r['name']) or D.norm(r['name']), r['method']

    for r in sorted(current, key=lambda r: (-r['amount'], r['id'])):
        if r['stream_id'] or r['amount'] <= 0 or r['id'] in cancelled:
            continue
        candidates = [key for key, members in previous.items() if key not in covered
                      and family(members[-1]) == family(r) and sum(m['amount'] for m in members)>0]
        if candidates:
            key = min(candidates, key=lambda key: (abs(sum(m['amount'] for m in previous[key])-r['amount']), str(key)))
            covered.add(key)
    result = []
    for key, members in previous.items():
        amount = sum(r['amount'] for r in members)
        if amount > 0 and key not in covered:
            result.append(dict(members[-1], amount=amount, projected=True, projection_basis='last_month_fixed',
                               source_ids=[r['id'] for r in members], projected_count=0))
    observed = list(prior_rows.items())
    observed = [(m, rows) for m, rows in observed if rows]
    # Missing data months are not zero-spending days and older months do not
    # replace them. A recorded month with no variable spend remains valid.
    days = sum(calendar.monthrange(int(m[:4]), int(m[5:]))[1] for m, rows in observed)
    variable = collections.defaultdict(list)
    for m, rows in observed:
        for r in rows:
            if (r['kind'] == 'expense' and r['class'] == 'spend' and not fixed(r)
                    and not LC.future_excluded(r, meta, today)):
                # Match the plan's large-payment partition, independent of src_cat.
                key = 'big' if abs(r['amount']) >= 100000 else bucket(r, meta)
                variable[key].append(r)
        for r in reviewed_credits(c, rows, meta, annotations):
            if r['credit_bucket'] not in ('fixed', 'subscriptions'):
                variable[r['credit_bucket']].append(dict(r, kind='expense', **{'class':'spend'}, amount=-r['amount']))
    # Allocate whole won by largest remainder, so bucket projections add to
    # the single three-month daily-rate estimate, rather than rounding drift.
    numerators = {key:max(0, sum(r['amount'] for r in members))*w['remaining_days']
                  for key, members in variable.items()}
    amounts = {key:value//days for key, value in numerators.items()}
    extra = round(sum(numerators.values())/days)-sum(amounts.values()) if days else 0
    for key in sorted(numerators, key=lambda key: (-(numerators[key]%days), key))[:extra]:
        amounts[key] += 1
    for key, members in variable.items():
        amount = amounts[key]
        if amount:
            result.append(dict(members[-1], amount=amount, projected=True, projection_basis='three_month_variable_daily',
                               projection_bucket=key, source_ids=[r['id'] for r in members],
                               projected_count=A.order_metrics(members)['count']*w['remaining_days']/days,
                               rate_days=days, rate_months=[m for m, rows in observed]))
    return result
