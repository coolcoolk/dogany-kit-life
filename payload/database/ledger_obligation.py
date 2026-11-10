# -*- coding: utf-8 -*-
"""Recurring end facts, stored only in existing linked contract records.

Month precision becomes its last calendar day. A charge remains in its end
month; actual payment history is never removed by a future term.
"""
import calendar
import datetime as dt
import json
import re


def endpoint(value, today):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError('end requires a date or month')
    if re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', value):
        y, m = map(int, value.split('-'))
        return dt.date(y, m, calendar.monthrange(y, m)[1]).isoformat()
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        return dt.date.fromisoformat(value).isoformat()
    match = re.fullmatch(r'(내년\s*|올해\s*|\d{4}년\s*)?(\d{1,2})월(?:\s*(\d{1,2})일)?(?:까지야?|\s*만기)?', value.strip())
    if not match:
        raise ValueError('end requires an exact date/month or stated Korean end')
    anchor = dt.date.fromisoformat(today)
    prefix, month, day = match.groups(); month = int(month)
    year = anchor.year + (1 if prefix and prefix.strip() == '내년' else 0)
    if prefix and prefix.strip().endswith('년') and prefix.strip()[:-1].isdigit():
        year = int(prefix.strip()[:-1])
    if not prefix and month < anchor.month:
        year += 1
    return dt.date(year, month, int(day) if day else calendar.monthrange(year, month)[1]).isoformat()


def term(c, fixed_id):
    if not fixed_id or not c.execute("SELECT 1 FROM sqlite_master WHERE name='contract_record'").fetchone():
        return dict(ends_on=None, end_kind='unknown', end_asked=False)
    rows = c.execute('SELECT facts,sources FROM contract_record WHERE fixed_id=? ORDER BY updated_at DESC,id DESC', (fixed_id,)).fetchall()
    for raw, origins in rows:
        facts, sources = json.loads(raw), json.loads(origins)
        import contracts
        contracts.derive(facts, sources)
        if facts.get('ends_on') or 'end_kind' in facts:
            result = dict(ends_on=facts.get('ends_on'), end_kind='date' if facts.get('ends_on') else facts['end_kind'],
                          end_asked=sources.get('ends_on') != 'estimated')
            if sources.get('ends_on') in ('estimated', 'document', 'screen'):
                result['end_provenance'] = sources['ends_on']
            return result
    return dict(ends_on=None, end_kind='unknown', end_asked=False)


def save(c, owner, fixed_id, data, today, record_key=None):
    import contracts
    fixed = c.execute('SELECT name FROM fixed_cost WHERE id=?', (fixed_id,)).fetchone()
    if not fixed:
        raise ValueError('existing fixed obligation required')
    end = endpoint(data['ends_on'], today) if 'ends_on' in data else None
    mode = 'date' if end else data.get('end_kind', 'none' if 'ends_on' in data else 'unknown')
    if mode not in ('date', 'none', 'unknown') or mode == 'date' and not end:
        raise ValueError('end kind requires date, none or unknown')
    # Reset/reconciliation can detach fixed_id while retaining the stable key.
    # Resolve that identity before inserting, including for the default key.
    record_key = record_key or 'ledger:fixed:'+str(fixed_id)
    prior = c.execute('SELECT id,facts,sources FROM contract_record WHERE owner=? AND record_key=?', (owner,record_key)).fetchone()
    if prior is None:
        prior = c.execute('SELECT id,facts,sources FROM contract_record WHERE owner=? AND fixed_id=? ORDER BY updated_at DESC,id DESC LIMIT 1', (owner,fixed_id)).fetchone()
    facts = json.loads(prior[1]) if prior else {}
    sources = json.loads(prior[2]) if prior else {}
    facts.update(ends_on=end, end_kind=mode)
    provenance = data.get('provenance', 'owner')
    if provenance not in ('owner', 'document', 'screen', 'estimated'):
        raise ValueError('unknown obligation provenance')
    sources.update(ends_on=provenance, end_kind=provenance)
    if mode == 'none':
        # An explicit indefinite term must not regenerate an older end.

        facts['term_months'] = None
    if data.get('cancelled'):
        facts.update(status='cancelled', auto_renew=False, reminder_enabled=False)
        sources.update({k:'owner' for k in ('status','auto_renew','reminder_enabled')})
    contracts.validate(facts)
    if prior:
        c.execute("UPDATE contract_record SET facts=?,sources=?,fixed_id=?,revision=revision+1,updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?", (json.dumps(facts),json.dumps(sources),fixed_id,prior[0]))
    else:
        c.execute('INSERT INTO contract_record(owner,record_key,kind,title,fixed_id,facts,sources) VALUES(?,?,?,?,?,?,?)', (owner,record_key,'ledger_obligation',fixed[0],fixed_id,json.dumps(facts),json.dumps(sources)))
    return term(c, fixed_id)


def ended(item, month):
    return bool(item.get('ends_on') and month > item['ends_on'][:7])


def paid_monthly(c, fixed_ids, monthly, month):
    from ledger_review import CYCLE_MONTHS
    weights=[]
    for fid in fixed_ids:
        row=c.execute('SELECT COALESCE(current_amount,amount),cycle FROM fixed_cost WHERE id=?',(fid,)).fetchone()
        if row: weights.append((fid,round(row[0]/CYCLE_MONTHS.get(row[1],1))))
    total=sum(amount for _,amount in weights)
    active=sum(amount for fid,amount in weights if term(c,fid)['ends_on'] and not ended(term(c,fid),month))
    return round(monthly*active/total) if total else 0


def obligations(c, month):
    from ledger_diag import rows
    from ledger_review import CYCLE_MONTHS
    result = []
    for f in rows(c, "SELECT * FROM fixed_cost WHERE direction='expense' AND state<>'stopped' AND COALESCE(class,'other')<>'saving'"):
        result.append(dict(id=f['id'],name=f['name'],cycle=f['cycle'],bucket='subscriptions' if f['class']=='subscription' else 'loan' if f['class']=='loan' else 'living',
                           monthly=round((f['current_amount'] if f['current_amount'] is not None else f['amount']) / CYCLE_MONTHS.get(f['cycle'],1)), **term(c,f['id'])))
    from ledger_plan import facts
    meta = facts(c)
    for item in result:
        stops = [f['stopped_on'] for key,f in meta.items() if key.startswith('lifecycle:')
                 and f.get('scope')=='series' and f.get('stream_id')==item['id'] and f.get('stopped_on')]
        if stops:
            end = (dt.date.fromisoformat(min(stops))-dt.timedelta(days=1)).isoformat()
            if not item.get('ends_on') or end < item['ends_on']:
                item.update(ends_on=end, end_kind='date', end_asked=True)
    return result


def forecast(plan, targets, month):
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])',month):
        raise ValueError('forecast month requires YYYY-MM')
    from ledger_plan import projection, subscription_services, migrate_subscriptions
    import copy
    plan, targets = copy.deepcopy(plan), copy.deepcopy(targets)
    migrate_subscriptions(plan, targets)
    base = projection(plan, targets, month=month)
    freed = dict(living=0, subscriptions=0, loan=0)
    details = []
    legacy_loan_freed = 0
    for item in sorted(plan.get('obligations', []),key=lambda r:(r.get('ends_on') or '9999-12-31',r['id'])):
        if item['bucket']=='loan' and any(d['repayment_stream_id']==item['id'] and d['repayment_type']=='credit_line' for d in plan.get('loans',[])):
            continue
        if not ended(item, month):
            continue
        amount = item['monthly']
        if item['bucket']=='loan' and plan.get('loan_payment_unit'):
            unit = plan['loan_payment_unit']
            amount = round(amount/unit)*unit
        if item['bucket']=='subscriptions':
            services = subscription_services(plan) if ('subscriptions' in targets and
                month >= targets.get('subscriptions_from',month)) else plan['subscriptions']
            sub = next((s for s in services if item['id'] in s.get('fixed_ids',[r.get('stream_id') for r in s.get('members',[])])),None)
            if sub is None and services is not plan['subscriptions']:
                continue
            if sub:
                vendor_target=targets.get('subscription_targets',{}).get(sub['key'],sub.get('paid_monthly',0 if not sub.get('ends_on') else sub['monthly']) if sub['cancelled'] else sub['monthly'])
                if month<targets.get('subscriptions_from',month) or month<targets.get('subscription_effective_from',{}).get(sub['key'],month):
                    vendor_target=targets.get('subscription_revisions',{}).get(sub['key'],{}).get('previous',sub['monthly'])
                members=[r for r in plan.get('obligations',[]) if r['id'] in sub.get('fixed_ids',[m.get('stream_id') for m in sub.get('members',[])])]
                if sub['cancelled']:
                    members=[r for r in members if r.get('ends_on') and not ended(r,plan['today'][:7])]
                total=sum(r['monthly'] for r in members)
                if len(members)>1 and total:
                    shares={r['id']:vendor_target*r['monthly']//total for r in members}
                    shares[members[-1]['id']]+=vendor_target-sum(shares.values())
                    amount=shares.get(item['id'],0)
                else: amount=vendor_target if not members or item['id']==members[0]['id'] else 0
        cap=plan['loan'] if item['bucket']=='loan' else base[item['bucket']]
        amount=min(amount,max(0,cap-freed[item['bucket']]))
        if item['bucket']=='loan':
            from ledger_loan import payment
            loan = next((d for d in plan.get('loans',[]) if d['repayment_stream_id']==item['id']),None)
            if loan is None or (payment(loan,month) is None and not plan.get('loan_payment_unit')):
                legacy_loan_freed += amount
        freed[item['bucket']] += amount
        details.append(dict(item,freed=amount,from_month=_next(item['ends_on'][:7])))
    # Installments retain their own schedule; no duplicate fixed-cost carrier.
    from ledger_installment import at
    installments = sum(at(it,month)['this_month'] for it in plan.get('installments',[]))
    freed_installments=0
    for item in plan.get('installments',[]):
        if month>item['end'] and not item.get('closed') and not item.get('superseded_by'):
            amount=at(item,item['end'])['this_month'];freed_installments+=amount
            details.append(dict(id=item['id'],name=item['name'],bucket='big',freed=amount,from_month=_next(item['end'])))
    from ledger_loan import forecast as loan_forecast
    shaped = loan_forecast(plan,month)
    # plan.loan already reflects the current month's shape. Future deltas
    # are measured against the same historical baseline, never compounded.
    future_loan = max(0,plan.get('loan_baseline',plan['loan'])-legacy_loan_freed+shaped['delta'])
    loan_delta = future_loan-plan['loan']
    # The projector owns dated shopping caps and pauses before their start.
    big=max(base['big'],installments)
    return dict(month=month,living=max(0,base['living']-freed['living']),
                subscriptions=max(0,base['subscriptions']-freed['subscriptions']),
                loan=future_loan,loan_oneoffs=shaped['oneoffs'],big=big,
                freed=sum(freed.values())+freed_installments,freed_installments=freed_installments,freed_by_bucket=freed,ended=details,
                projected_gap=base['projected_gap']+freed['living']+freed['subscriptions']-(big-base['big'])-loan_delta)


def end_schedule(plan,targets):
    months={_next(item['ends_on'][:7]) for item in plan.get('obligations',[]) if item.get('ends_on')}
    months.update(_next(item['end']) for item in plan.get('installments',[]))
    return [item for month in sorted(months) for item in forecast(plan,targets,month)['ended'] if item['from_month']==month and item['freed']>0]


def _next(month):
    year, number = map(int,month.split('-'))
    return '%04d-%02d' % (year+number//12,number%12+1)
