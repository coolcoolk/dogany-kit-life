# -*- coding: utf-8 -*-
"""One rehearsal step per message. All money and durable facts use kit verbs."""
import datetime
import html
import json
import calendar
import copy
import re

from . import kit, text, dashboard, end_dates as E
from . import history_flow as F
from .markup import CONT, ITEM, quote_lists

PREFIX = 'ledger.onboarding.reduce.'
STEPS = ('reduce_overview','reduce_cap','reduce_subscriptions','reduce_subscription_ai','reduce_subscription_cuts','reduce_habits','reduce_goal')
TRANSFER_ASK_MIN_WON = 100000
COPY_PICK_STEPS = {'reduce_big','reduce_cap','reduce_subscriptions','reduce_subscription_ai','reduce_subscription_cuts','reduce_habits',
                   'reduce_shopping','reduce_tradeoff','reduce_bill','reduce_goal'}


def t(key, **kw):
    return text.t(PREFIX+key, **kw)


def _money(value):
    return F._won(value)


def _payment_date(value, today=None):
    return dashboard.question_date(value, today)


def _man(value):
    return F._man(value)


def _q(qid, body, choices=(), **kw):
    from . import onboarding as O
    return O._interactive(O._q(qid, body, [dict(label=t(label),value=value) for label,value in choices], **kw))


def _read(flow):
    _hold_subscription_display(flow)
    flow['plan'] = kit.run('ledger-plan','--today',flow['today'], *F.window_args(flow))
    # A plan read can reconcile loan streams. Issue the card from that same
    # history so its first answer is not mistaken for a later owner correction.
    if flow.get('history') and (flow['history'].get('debts') or flow['plan'].get('loans')):
        flow['history']['debts'] = F._history(flow)['debts']
    flow['plan']['person_refunds_version'] = 2
    from ledger_plan import reduction_loan_basis
    reduction_loan_basis(flow['plan'])
    flow['plan']['facts'].pop('budget',None)
    omitted = set(flow.get('retired_purpose_payment_ids', []))
    if omitted:
        streams = []
        for stream in flow['plan']['hidden']:
            rows = [r for r in stream['recent'] if r['id'] not in omitted]
            if rows:
                streams.append(dict(stream, recent=rows, ids=[r['id'] for r in rows],
                                    count=len(rows), amount=sum(r['amount'] for r in rows)))
        flow['plan']['hidden'] = streams
    return flow['plan']


def refresh_debts(flow):
    """Direct stable-ID corrections invalidate dependent cached cards."""
    if not flow.get('history', {}).get('debts') or not flow.get('plan'):
        return False
    history = F._history(flow)
    if history['debts'] == flow['history'].get('debts', []):
        return False
    flow['history'] = history
    _read(flow)
    flow['questions'] = {key: q for key, q in flow['questions'].items()
                         if not key.startswith('reduce_') and key != 'conclusion'}
    flow.pop('resume_hold', None)
    flow.pop('resume_hold_token', None)
    return True


def _debt_open(debt):
    if (debt.get('end_provenance') == 'estimated' and debt.get('ends_on') and debt.get('remaining') is not None
            and debt.get('repayment_type') == 'equal_payment'):
        return False
    if debt.get('repayment_type_source') == 'owner' and debt.get('repayment_type') == 'unknown':
        return False
    return (debt.get('remaining') is None or debt.get('repayment_type_source') != 'owner'
            or not debt.get('end_asked'))


def _debt_pending(flow, debt):
    # Remembered cards can be confirmed or skipped without relabelling
    # an evidenced repayment guess as an owner-stated fact.
    return _debt_open(debt) and debt.get('id') not in flow.get('debt_reviewed_ids', [])


def _projection(flow, targets=None):
    return kit.run('ledger-plan-project','--data',json.dumps(dict(plan=flow['plan'],targets=flow.get('targets',{}) if targets is None else targets,**(dict(end_schedule=True) if E.enabled() else {})),ensure_ascii=False))


def _ledger(flow, targets=None):
    """One money basis for current rows, ceilings, cuts and the unpaid gap.

    Keep owner ceilings intact. Only the public projector determines spending
    and savings, including effective dates and ceilings above current spend.
    The AI lever consumes its part of the subscription row first; the other
    subscriptions remain in that row at their current amount.
    """
    p=flow['plan']; g=p['groups']
    targets=flow.get('targets',{}) if targets is None else targets
    projected=_projection(flow,targets)
    current=dict(big=g['big'],subscriptions=g['subscriptions'],
                 living=p['spend']-g['big']-g['subscriptions'])
    rows={key:dict(current=amount,spend=projected[key],cut=amount-projected[key])
          for key,amount in current.items()}
    from ledger_plan import subscription_basis, subscription_components
    ai=subscription_basis(p,targets)['ai']
    components=subscription_components(p,targets,projected['subscriptions'])
    rows['ai']=dict(current=ai,spend=components['ai'])
    rows['ai']['cut']=ai-rows['ai']['spend']
    loan=min(projected['remaining'],p['loan'])
    allowance=targets.get('accepted_deficit',loan)
    return dict(income=p.get('income',0),rows=rows,caps=copy.deepcopy(targets),
                loan=loan,living_gap=max(0,projected['remaining']-loan),
                loan_allowance=allowance,remaining=projected['remaining'],
                unfilled=max(0,projected['remaining']-allowance),projection=projected)


def _installment_items(plan):
    # Reuse the existing billed-payment detection and retained schedule match.
    # Classification review does not confirm an installment's remaining term.
    candidates = {sid for g in plan.get('installment_candidates', []) for sid in g['ids']}
    return [r for r in plan['big'] if r['id'] in candidates or r.get('installment_review_required') or any(
        r['method'] == it.get('card', it.get('method'))
        and r['amount'] == it['monthly']
        and it.get('purchase_date', it['first']+'-01') <= r['date']
        and r['date'][:7] <= it['end']
        and ('installment_review_required' not in r or it.get('release_unconfirmed'))
        for it in plan['installments'])]


def _big_items(plan):
    return [r for r in plan['big'] if not r['reviewed']
            and r.get('big_kind','unknown')=='unknown']


def goal_name(flow):
    accepted=flow.get('targets',{}).get('accepted_deficit',0)
    return t('deficit_goal',amount=_money(accepted)) if accepted else t('goal_title')


def goal_card(flow, picked=False):
    p=flow['plan']; g=p['groups']; targets=flow.get('targets',{}); s=_ledger(flow)['projection']
    man=lambda v: F._estimate(_man(v),p.get('projected'))
    localized_money = (lambda v: F._estimate(_money(v),p.get('projected'))) if text.lang() == 'en' else man
    from ledger_plan import render_monthly_money
    money = render_monthly_money(dict(spend=p['spend']+p['loan'],
        budget=sum(s[key] for key in ('living','big','subscriptions'))+p['loan'],
        income=p['income'],loan=p['loan']),text.t)
    body=[t('budget_title' if picked else 'cut_places')]
    habits=p['spend']-g['fixed']-g['big']-g['subscriptions']
    for n,name,step,now,target in ((1,'big','reduce_cap',g['big'],s['big']),
            (2,'subscriptions','reduce_subscriptions',g['subscriptions'],s['subscriptions']),
            (3,'habits','reduce_habits',habits,habits-sum(s['cuts'].values()))):
        done=step in flow['answers']
        if picked:
            if not done:
                body.append(t('budget_open',n=n,name=t(name)))
            elif name=='big':
                from ledger_plan import big_budget_lines
                body.extend(big_budget_lines(flow['today'][:7],target,targets,text.t,mark='[v] 1. '))
            else:
                body.append(t('budget_set',n=n,name=t(name),amount=localized_money(target)))
        elif name=='big' and done:
            from ledger_plan import big_budget_lines
            body.extend(big_budget_lines(flow['today'][:7],target,targets,text.t,mark='[v] 1. '))
        else:
            saved=man(0) if round((now-target)/10000,1)==0 else ('-' if now>target else '+')+man(abs(now-target))
            body.append(t('compact_changed' if done else 'compact_open',n=n,name=t(name),now=man(now),
                          target=man(target),saved=saved))
    if E.enabled() and not picked: body.extend(_end_lines(flow,s.get('end_schedule',[])))
    return money+'\n'+quote_lists('\n'.join(body))


def _shortfall_lines(flow):
    """Owner 2026-11-05: report the unpaid gap without an income target."""
    from ledger_plan import shortfall_copy
    return shortfall_copy(flow['plan'],flow.get('targets',{}),
                          _projection(flow)['projected_gap'],flow['today'],text.t,
                          **({'format_amount': _money} if text.lang() == 'en' else {}))


def _proposal(flow,counts):
    targets=dict(flow.get('targets',{}))
    targets['counts']=dict(targets.get('counts',{}),**counts)
    return _projection(flow,targets)


def _counts(flow,limits):
    # A proposal never increases frequency and never claims unsupported savings.
    return {k:min(n,int(flow['plan']['metrics'][k]['monthly_count'])) for k,n in limits.items()
            if flow['plan']['metrics'].get(k,{}).get('unit_price')}


def _habit_options(flow):
    return [_counts(flow,dict(zip(('delivery','alcohol','cafe'),values)))
            for values in ((18,4,10),(12,3,8),(6,2,5))]


def _count_lines(flow,counts):
    p=flow['plan']; projected=_proposal(flow,counts)
    return [t('count_change',name=t(k),now=format(p['metrics'][k]['monthly_count'],'.1f'),count=n,
              before=_man(p['groups'][k]),after=_man(p['groups'][k]-projected['cuts'].get(k,0))) for k,n in counts.items()]


def _next_month(month):
    y,m=map(int,month.split('-'));return '%04d-%02d'%(y+m//12,m%12+1)


def _shopping_options(flow):
    return [_counts(flow,dict(coupang=4,clothes=1,convenience=2)),
            _counts(flow,dict(coupang=6,clothes=2,cafe=5,alcohol=2,convenience=4))]


def _candidate_number(amount):
    return format(amount / 10000, '.1f').rstrip('0').rstrip('.')


def _tradeoff_basis(flow):
    p=flow['plan']; targets=flow['targets']
    ai=targets.get('subscription_group_targets',{}).get('ai',sum(
        s['monthly'] for s in p['subscriptions'] if s['key'] in ('claude','codex')))
    rest=_ledger(flow)['unfilled']
    # Option 1 keeps the existing half-shortfall bound; option 2 halves this
    # bounded cut again and also offers the issued shopping cuts.
    return ai,rest,min(ai,rest//2)


def _owner_ai_cap(flow):
    """Return the owner's immutable AI-tool ceiling, when screen B set one."""
    cap = flow.get('owner_ai_cap')
    return cap.get('amount_krw') if isinstance(cap, dict) else None


def _tradeoff_targets(flow,value):
    """The issued candidate and its acceptance use exactly the same targets."""
    targets=copy.deepcopy(flow['targets'])
    ai,rest,cut=_tradeoff_basis(flow)
    if _owner_ai_cap(flow) is not None:
        # The owner-set AI ceiling is a basis, not a lever to revisit. Any
        # remaining shortfall can only be assigned to non-AI spending.
        if value != 'shopping':
            raise ValueError('owner AI cap is fixed; choose a non-AI lever')
        targets.setdefault('counts',{}).update(_shopping_options(flow)[0])
        return targets
    if value!='shopping':
        if value=='half':cut//=2
        targets.setdefault('subscription_group_targets',{})['ai']=ai-cut
        targets['subscriptions']-=cut
    if value in ('half','shopping'):
        counts=_counts(flow,dict(coupang=6,clothes=2)) if value=='half' else _shopping_options(flow)[0]
        targets.setdefault('counts',{}).update(counts)
    return targets


def _candidate_counts(flow,counts):
    projected=_proposal(flow,counts); p=flow['plan']
    return [t('revisit_count',name=t(k),now=format(p['metrics'][k]['monthly_count'],'.1f').rstrip('0').rstrip('.'),
              count=n,before=_candidate_number(p['groups'][k]),
              after=_candidate_number(p['groups'][k]-projected['cuts'].get(k,0))) for k,n in counts.items()]


def _bill_candidate(flow,key='codex'):
    sub=next((s for s in flow['plan']['subscriptions'] if s['key']==key),None)
    if not sub: raise ValueError('no issued subscription billing evidence')
    month=sub['last']['date'][:7]
    rows=sorted((r for r in sub['members'] if r['date'].startswith(month) and r['amount']>0),key=lambda r:r['date'])
    # Evidence-derived comparison only: this is not a vendor's quoted price.
    amount=round(rows[0]['amount']/10000)*10000
    next_dates=[]
    for day in sorted({int(r['date'][8:]) for r in rows}):
        next_month=_next_month(month)
        while True:
            date=next_month+'-%02d'%min(day,calendar.monthrange(int(next_month[:4]),int(next_month[5:]))[1])
            if date>=flow['today']:break
            next_month=_next_month(next_month)
        next_dates.append(date)
    if key=='codex' and flow.get('billing_plan')=='Pro100':
        # Reuse the recorded USD conversion used by dated amount changes.
        # A selected plan is not priced by rounding one old payment.
        from ledger_plan import _amount_change
        amount=_amount_change(dict(amount=100,currency='USD',
            effective_from=min(next_dates)[:7]),None)['amount']
    return dict(key=key,rows=rows,month=month,next_dates=sorted(next_dates),amount=amount)


def _billing_targets(flow,bill,amount,effective_from=None):
    targets=copy.deepcopy(flow['targets'])
    previous=targets.setdefault('subscription_targets',{}).get(bill['key'],sum(r['amount'] for r in bill['rows']))
    targets.setdefault('subscription_revisions',{})[bill['key']]=dict(previous=previous,amount=amount)
    targets['subscriptions']+=amount-previous
    targets['subscription_targets'][bill['key']]=amount
    # A vendor revision changes the accepted group total by the same delta.
    if 'ai' in targets.get('subscription_group_targets',{}) and _owner_ai_cap(flow) is None:
        targets['subscription_group_targets']['ai']+=amount-previous
    targets.setdefault('subscription_effective_from',{})[bill['key']]=_month(effective_from or bill['next_dates'][0][:7])
    return targets


def _end_lines(flow,schedule=None):
    if schedule is None: schedule=_projection(flow).get('end_schedule',[])
    lines=[]
    for item in schedule:
        if item['bucket']=='big' and any(it['id']==item['id'] and it.get('release_unconfirmed') for it in flow['plan']['installments']):
            continue
        amount=item['freed']
        if item.get('cycle')=='yearly': amount=round(amount/100)*100
        year,month=map(int,item['from_month'].split('-'))
        sub=next((s for s in flow['plan']['subscriptions'] if item['id'] in s.get('fixed_ids',[])),None) if item['bucket']=='subscriptions' else None
        name=html.escape(sub.get('display_name',sub['name']) if sub else item.get('display_name',item['name']))
        end_month=(datetime.date(year,month,1)-datetime.timedelta(days=1)).strftime('%Y-%m')
        end_year,end_number=map(int,end_month.split('-'))
        installment=next((it for it in flow['plan']['installments'] if it['id']==item['id']),None) if item['bucket']=='big' else None
        if item['bucket']=='big':
            installment=installment or {}
            last=installment.get('last_payment_date')
            if last and last[:7]!=end_month:
                last=None
            if not last and installment.get('bill_only') and installment.get('charge_day'):
                last=end_month+'-%02d'%min(installment['charge_day'],calendar.monthrange(end_year,end_number)[1])
            lines.append(ITEM+E.t('installment_end_day' if last else 'installment_end_month',
                name=name,year=end_year,month=end_number,day=int(last[8:]) if last else None))
            start=E.t('release_month',month=month) if year==int(flow['today'][:4]) else E.t('release_year_month',year=year,month=month)
            lines.append(CONT+E.t('freed',start=start,bucket=t(item['bucket']),amount=format(amount,',')))
        elif item['bucket']=='loan':
            lines.append(ITEM+E.t('loan_end',name=name,year=end_year,month=end_number))
            lines.append(CONT+E.t('loan_freed',year=year,month=month,amount=format(amount,',')))
        else:
            lines.append(ITEM+E.t('expense_freed',name=name,year=year,month=month,amount=format(amount,',')))
    return lines


def next_question(st, flow, today):
    # Freeze the displayed basis for the round. New month after completion is
    # handled by the live budget read, not by silently changing chosen targets.
    p = flow.get('plan') or _read(flow)
    if not p['n']:
        return F._handoff(st,flow)
    answers = flow['answers']
    if 'reduce_subscription_cuts' in answers:
        for n,sub in enumerate(p['subscriptions']):
            fact=p['facts'].get('subscription:'+sub['key'],{})
            if sub['renewal'] and 'reminder_enabled' not in fact:
                return issue(st,flow,'reduce_yearly_'+str(n))
    if ('reduce_overview' not in answers and 'reduce_debts' not in answers
            and any(_debt_pending(flow, d) for d in flow['history']['debts'])):
        qid='reduce_debts'
    else:
        qid=next((q for q in STEPS if q not in answers),None)
    if flow.get('revisit') == 'reduce_income':
        # Resume an older round at its remaining reductions, never income.
        if 'reduce_shopping' not in answers:
            flow.setdefault('targets',{})['accepted_deficit']=min(p['loan'],_projection(flow)['remaining'])
        flow['revisit'] = 'reduce_goal' if 'reduce_shopping' in answers else 'reduce_shopping'
    if flow.get('revisit'):
        qid=flow['revisit']
    if qid is None:
        return F._completion(st,flow)
    if qid == 'reduce_installments':
        qid = 'reduce_cap'
    if qid == 'reduce_big' and not (p['big'] or p.get('big_oneoffs') or p.get('periodic_costs')):
        answers[qid] = dict(value='next', inferred=True)
        qid = 'reduce_cap'
    return issue(st, flow, qid)


def _recall_rows(rows, today):
    """Question scope only: three calendar months through the local today.

    Clamp month ends rather than approximating months as 90 days. Readers and
    durable facts retain the full history, including unknown older receipts.
    """
    end = datetime.date.fromisoformat(today)
    month = end.year * 12 + end.month - 1 - 3
    year, month = divmod(month, 12)
    month += 1
    start = end.replace(year=year, month=month,
                        day=min(end.day, calendar.monthrange(year, month)[1]))
    return [r for r in rows if start.isoformat() <= r['date'] <= today]


def _recall_today(flow):
    # Projection amounts can stay frozen after conclusion; recall cannot.
    return flow.get('recall_today', flow['today'])


def _transfer_items(plan, today=None):
    items = []
    for r in plan.get('transfers', []):
        name = re.sub(text._bundle('ko')['ledger.match.history_suffix'], '', r['name']).strip()
        label = ' '.join(part for part in (r.get('method'), name) if part)
        for row in _recall_rows(r['recent'], today) if today else r['recent']:
            if row['amount'] >= TRANSFER_ASK_MIN_WON:
                items.append(dict(row, number=1, place=html.escape(label), place_text=label))
    return sorted(items, key=lambda r: (r['date'], r['id']))


def intake_question(st, flow, stage=None):
    """Refunds, instrument roster, transfers, then recent opaque payments.

    Past-only starts need this evidence too. Do not wait for the planning
    handoff, and do not re-ask skipped or already answered items on resume.
    """
    p = flow.get('plan') or _read(flow)
    if p.get('person_refunds_version') != 2:
        p = _read(flow)
    for qid, rows in (('reduce_refunds', _recall_rows(p['refunds'], _recall_today(flow)) or p.get('matched_payments')),
                      ('reduce_purposes', p.get('module_roster',{}).get('instruments',p.get('instruments'))),
                      ('reduce_transfers', _transfer_items(p, _recall_today(flow))),
                      ('reduce_methods', p['hidden'])):
        if stage == 'roster' and qid not in ('reduce_refunds', 'reduce_purposes'):
            continue
        if stage == 'payments' and qid in ('reduce_refunds', 'reduce_purposes'):
            continue
        if rows and (qid == 'reduce_transfers' or qid not in flow['answers']
                     or qid == 'reduce_refunds' and _recall_rows(p['refunds'], _recall_today(flow))):
            return issue(st, flow, qid)
    if stage == 'payments' and 'reduce_overview' not in flow['answers']:
        if 'reduce_big' not in flow['answers'] and (p.get('big') or p.get('big_oneoffs') or
                p.get('periodic_costs') or p.get('installment_candidates')):
            return issue(st, flow, 'reduce_big')
        if 'reduce_debts' not in flow['answers'] and any(
                _debt_pending(flow, debt) for debt in flow['history']['debts']):
            return issue(st, flow, 'reduce_debts')
    return None


def _hold_subscription_display(flow):
    """Retain issued row evidence before navigation replaces the plan."""
    q = flow.get('questions', {}).get('reduce_subscriptions')
    p = flow.get('plan', {})
    if q and q.get('subscription_evidence') and 'subscription_display' not in q:
        q['subscription_display'] = copy.deepcopy(dict(
            subscriptions=p.get('current_subscriptions', p.get('subscriptions', [])),
            projected=p.get('projected'), recent_costs=q.get('recent_costs'),
            today=flow['today']))


def _subscription_label(sub):
    return (sub['display_name'] if sub.get('display_name_source') else
            t('vendor.'+sub['key']) if sub['key'] in ('claude','codex') else
            sub.get('display_name',sub['name']))


def _subscription_row(sub, today, projected=False):
    row=t('subscription_row' if sub.get('amount_varies',True) or sub.get('cycle')=='yearly' else 'subscription_monthly',
          name='<b>'+E.label(html.escape(_subscription_label(sub)),sub.get('ends_on'))+'</b>',
          amount=F._estimate(_money(sub['monthly']),projected and sub['cycle']!='yearly'))
    days=sub.get('recurring_days',[])
    if days:
        row+=' · '+', '.join(t('regular_charge',day=day) for day in days)
    elif sub.get('first_seen'):
        row+=' · '+t('subscription_first',date=dashboard.question_date(sub['first_seen'],today))
    return row


def _subscription_cards(subscriptions):
    groups={}
    for sub in subscriptions:
        methods=sub.get('payment_methods',sorted({r['method'] for r in sub.get('members',[]) if r.get('method')}))
        for method in methods:
            groups.setdefault(method,[]).append(_subscription_label(sub))
    from . import text as T
    # The confirmed card names only cards shared by multiple subscriptions.
    return [t('subscription_card',names=html.escape(', '.join(names)),
              subject=T.josa(html.escape(', '.join(names)),'eun'),card=html.escape(card))
            for card,names in groups.items() if len(names)>1]


def _subscription_name_lines(display, oneoffs):
    """Only lines containing merchant labels, from the issued evidence."""
    lines = []
    for sub in display['subscriptions']:
        lines.append(_subscription_row(sub,display['today'],display['projected']))
    lines.extend(_subscription_cards(display['subscriptions']))
    lines.extend(t('oneoff_row', name=html.escape(row['display_name']), amount=_money(row['amount']),
                   date=dashboard.question_date(row['date'], display['today'])) for row in oneoffs)
    return lines


def _refresh_subscription_names(q, plan):
    from . import merchant_names
    display = q['subscription_display']
    oneoffs = q.get('oneoffs', [])
    before = _subscription_name_lines(display, oneoffs)
    evidence = dict(subscriptions=display['subscriptions'], oneoffs=oneoffs)
    merchant_names.refresh(evidence)
    after = _subscription_name_lines(display, oneoffs)
    replacements = {old:new for old,new in zip(before, after) if old != new}
    if replacements:
        quoted = q['ask'] == q.get('quote_rendered')
        if q['ask'] != q.get('merchant_name_rendered'):
            q['merchant_name_identity'] = (q['quote_source'] if q['ask'] == q.get('quote_rendered') else q['ask'])
        # Replace complete rendered rows once: names can overlap or contain HTML.
        pattern = '|'.join(re.escape(old) for old in sorted(replacements, key=len, reverse=True))
        replace = lambda body: re.sub(pattern, lambda match: replacements[match.group()], body)
        q['ask'] = replace(q['ask'])
        if quoted:
            q['quote_source'] = replace(q['quote_source'])
            q['quote_rendered'] = q['ask']
        q['merchant_name_rendered'] = q['ask']
    rows = display['subscriptions'] + oneoffs
    merchant_names.attach(q, rows, plan)


def issue(st, flow, qid):
    if qid == 'reduce_installments':
        flow.pop('revisit', None)
        qid = 'reduce_cap'
    if qid in ('reduce_big','reduce_cap') and flow.get('plan',{}).get('big_classification_version') != 1:
        _read(flow)
        flow['questions'].pop('reduce_big',None)
        flow['questions'].pop('reduce_cap',None)
    if qid == 'reduce_income':
        # A held pre-upgrade callback must also skip the retired question.
        flow['revisit'] = qid
        return next_question(st,flow,datetime.date.fromisoformat(flow['today']))
    _hold_subscription_display(flow)
    if flow.get('plan', {}).get('window', {}).get('display_revision') != 1:
        _read(flow)
        from . import goal_spend
        goal_spend.refresh_window(flow)
        flow['questions'] = {k: q for k, q in flow['questions'].items() if k in flow['answers']}
    # Read known values inside the kit before issuing an approved prefill card.
    # The agent never supplies a value, and an absent source changes no screen.
    from . import known_values
    if qid in known_values.SUBSCRIPTION_QUESTIONS:
        value = known_values.subscription(st, flow, qid)
        staged = flow.setdefault('known_answers', {})
        if staged.get(qid) != value:
            flow['questions'].pop(qid, None)
        if value:
            staged[qid] = value
        else:
            staged.pop(qid, None)
    elif qid == 'reduce_debts':
        reopen_id = flow.get('debt_reopen_id')
        loan = next((d for d in flow['history']['debts'] if d['id'] == reopen_id or
                     reopen_id is None and _debt_pending(flow, d)), None)
        if loan:
            value = known_values.debt(st, flow, loan)
            staged = flow.setdefault('debt_known', {})
            key = str(loan['id'])
            if staged.get(key) != value:
                flow['questions'].pop(qid, None)
            if value:
                staged[key] = value
            else:
                staged.pop(key, None)
    q = flow['questions'].get(qid)
    # Owner 2026-11-05 picks also replace parked copies on resume.
    if qid in COPY_PICK_STEPS and q and q.get('copy_pick_version') != 1:
        q = None
    if qid == 'reduce_goal' and q and not q.get('shortfall_version'):
        q = None
    if qid == 'reduce_refunds':
        # Saved pre-cutoff cards must not bring old rows back on resume. Keep
        # references already shown to the owner stable while rebuilding.
        numbers = flow.setdefault('refund_numbers', {})
        for row in (q or {}).get('items', []):
            numbers.setdefault(str(row['id']), row['number'])
        q = None
    from . import merchant_names
    if flow['plan'].get('today') and 'months' in flow['plan'] and merchant_names.refresh(flow['plan']):
        if qid != 'reduce_subscriptions':
            q = None
    if qid == 'reduce_overview' and (not q or not q.get('goal_spend_section')):
        # Upgrade only the goal section of a held overview.
        if 'goal_spend' not in flow['plan']:
            fresh = kit.run('ledger-plan', '--today', flow['today'], *F.window_args(flow))
            flow['plan']['goal_spend'] = fresh['goal_spend']
        q = None
    if qid == 'reduce_big':
        if q and q.get('big_classification_version') != 1:
            _read(flow)
        q = None
    if qid == 'reduce_cap' and q and (q.get('cap_sections') != 2 or q.get('big_classification_version')!=1):
        # Upgrade a parked cap card from the same frozen plan and targets.
        q = None
    if q and q.get('known') and not q['known'].get('kit_source'):
        q = None
    if qid == 'reduce_purposes':
        p = flow['plan']
        from . import roster_modules
        if p.get('today') and not (q and q.get('items')) and (
                (p.get('module_roster') or {}).get('version',0)<roster_modules.VERSION or any(key not in p for key in ('roster_months','roster_instruments'))
                or q and q.get('module_roster_version',0)<roster_modules.VERSION):
            fresh = kit.run('ledger-plan', '--today', flow['today'], *F.window_args(flow))
            for key in ('roster_months', 'roster_instruments', 'module_roster'):
                p[key] = fresh[key]
            q = None
        # A saved roster may predate instrument facts. Refresh identity only;
        # the round's amounts, payment population and callbacks stay frozen.
        from . import instrument_context
        facts = instrument_context.current()
        if q and q.get('items'):
            q['instrument_facts'] = [facts[r['id']] for r in q['items'] if r.get('id') in facts]
        else:
            for key in ('instruments', 'roster_instruments'):
                if key in flow['plan']:
                    flow['plan'][key] = [instrument_context.attach(r, facts) for r in flow['plan'][key]]
            if q and q.get('purpose_labels'):
                q = flow['questions'][qid] = screen(flow, qid)
    if qid == 'reduce_purposes' and not flow.get('plan',{}).get('module_roster') and (
            (q and not q.get('purpose_labels') and not q.get('items')) or
            (not q and any('role' not in r or 'purpose' not in r
                           for r in flow.get('plan', {}).get('instruments', [])))):
        # Refresh pre-tobe informational cards, keeping legacy answerable
        # purpose cards and their callbacks intact. A cached plan can also
        # predate roles when the owner has not reached the roster yet.
        _read(flow)
        q = None
    if qid == 'reduce_subscriptions' and flow['plan'].get('today') and (
            'current_subscriptions' not in flow['plan'] or not q
            or not q.get('subscription_evidence') or q.get('subscription_oneoffs_version', 0) < 3):
        fresh = kit.run('ledger-plan', '--today', flow['today'], *F.window_args(flow))
        for key in ('roster_months', 'current_subscriptions', 'subscription_oneoffs', 'display_name_missing', 'display_name_resolution'):
            flow['plan'][key] = fresh[key]
        q = None
    if qid == 'reduce_goal' and q and not q.get('confirmed_ui_version'):
        q = None
    if qid == 'reduce_transfers':
        # Rebuild legacy batch cards and advance after each per-item answer.
        q = None
    if qid == 'reduce_debts' and q and len(q.get('items', [])) != 1:
        # A saved pre-upgrade batch must also ask just one unanswered loan.
        q = None
    if q is None:
        if qid == 'reduce_debts':
            flow['history'] = F._history(flow)
        q = screen(flow,qid)
        if qid in COPY_PICK_STEPS: q['copy_pick_version'] = 1
        if qid == 'reduce_goal': q['confirmed_ui_version'] = 1
        flow['questions'][qid]=q
    if qid == 'reduce_subscriptions':
        _hold_subscription_display(flow)
        _refresh_subscription_names(q, flow['plan'])
    elif qid.startswith('reduce_yearly_'):
        merchant_names.attach(q, q.get('items', []), flow['plan'])
    else:
        merchant_names.attach(q, q, flow['plan'])
    from . import onboarding as O
    q['nonce']=flow['nonce'] + (':'+str(q['items'][0]['id']) if qid in ('reduce_transfers','reduce_debts') and q.get('items') else ':'+qid if qid in ('reduce_subscriptions','reduce_subscription_ai','reduce_subscription_cuts') else '')
    if qid == 'reduce_debts':
        q['nonce'] += ':reopen:' + str(flow.get('debt_reopen_count', 0))
    q['card']=O.review_card(q)
    flow['pending']=qid
    F._save(st,flow)
    return dict(question=q,done=False,step=None,of=None,ui_gate='draft-copy-see-rendered-report')


def _known_prefill(flow, qid, lines, choices):
    known=flow.get('known_answers',{}).get(qid)
    if not known or not known.get('kit_source'):
        return choices, {}
    lines.extend([t('prefill_known'), '<blockquote>'+html.escape(known.get('display', known['value']))+'</blockquote>', t('prefill_confirm')])
    return [('prefill_use',known['value']),('skip','keep')], {'known':copy.deepcopy(known)}


def screen(flow,qid):
    p=flow['plan'];g=p['groups']
    man = lambda value: F._estimate(_man(value), p.get('projected'))
    localized_money = (lambda value: F._estimate(_money(value),p.get('projected'))) if text.lang() == 'en' else man
    if qid == 'reduce_purposes':
        from . import roster_modules
        instruments = p.get('roster_instruments', p['instruments'])
        roster = p.get('module_roster') or roster_modules.legacy_rows(instruments)
        return _q(qid, roster_modules.render(roster), [('next','next')],
                  free=False, items=[], instrument_quotes=True, purpose_labels=True,
                  module_roster_version=roster.get('version'),
                  roster_months=p.get('roster_months'),
                  instrument_facts=[r['instrument_facts'] for r in instruments if r.get('instrument_facts')])
    if qid=='reduce_debts':
        reopen_id = flow.get('debt_reopen_id')
        candidates = [d for d in flow['history']['debts']
                      if d.get('id') == reopen_id or (reopen_id is None and _debt_pending(flow, d))]
        items=[dict(d,number=n) for n,d in enumerate(candidates,1)][:1]
        lines=[t('debts_title')]
        for d in items:
            lines.append(t('debt_item',n=d['number'],name=html.escape(d['name']),amount=_money(d['principal'])) if d.get('principal') is not None else str(d['number'])+'. '+html.escape(d['name']))
            months=[(m, amount) for m, amount in d.get('payment_months', []) if m in p['months']]
            if d.get('payment_account') and months:
                first_amount=months[0][1];last_amount=months[-1][1]
                changed=next((month for month,amount in months if amount != first_amount),months[-1][0])
                lines.append(t('debt_rows_changed' if first_amount != last_amount else 'debt_rows',
                               account=html.escape(d['payment_account']),amount=_money(first_amount),
                               month=changed,latest=_money(last_amount),count=d.get('display_payment_count',len(months))))
            guess=E.loan_guess(d) if E.enabled() else ''
            if guess: lines.append(chr(0x3000)+guess)
        known = (flow.get('debt_known') or {}).get(str(items[0]['id'])) if items else None
        if known and not known.get('kit_source'):
            known = None
        if known:
            as_of = datetime.date.fromisoformat(known['as_of'])
            end_year, end_month = map(int, known['ends_on'].split('-'))
            lines.extend([t('debt_known_intro'), '<blockquote>'+t('debt_known_value',
                remaining=format(known['amount'], ','), month=as_of.month, day=as_of.day,
                year=end_year, end_month=calendar.month_abbr[end_month] if text.lang() == 'en' else end_month)+'</blockquote>', t('debt_known_ask')])
            choices=[('debt_known_confirm','known'),('skip','skip')]
        else:
            lines.append(E.t('loan_ask') if E.enabled() else t('debts_ask'))
            choices=[('skip','skip')]
        question = _q(qid,'\n'.join(lines),choices,free=True,items=items,known=copy.deepcopy(known),
                      answer_format={'items':[{'number':1,'amount':0,'as_of':'YYYY-MM-DD',**({'ends_on':'YYYY-MM','repayment_type':'equal_payment|equal_principal|bullet|credit_line|unknown'} if E.enabled() else {})}]})
        if known:
            from . import onboarding as O
            O._interactive(question, [question['choices']])
        return question
    if qid=='reduce_transfers':
        # A transfer is asked what kind of money it was, never where it was spent.
        items = _transfer_items(p, _recall_today(flow))[:1]
        if not items:
            raise ValueError('no large transfer to ask')
        row = items[0]
        # Restore the dev.80 single-transfer screen verbatim. This is one
        # payment's purpose question, so it keeps its unbulleted quote.
        lines = ['<blockquote>'+t('transfer_item',date=_payment_date(row['date'], flow['today']),name=row['place'],amount=_money(row['amount']))+'</blockquote>',
                 text.t('ledger.onboarding.history.fact_ask')]
        return _q(qid,'\n'.join(lines),[('transfer_debt','debt'),('transfer_settle','settlement'),('transfer_gift','gift'),('keep_unknown','unknown')],
                  free=True,items=items,
                  answer_format={'items':[{'number':1,'answer':'debt|settlement|gift|unknown'}]})
    if qid in ('reduce_methods','reduce_refunds'):
        hidden=qid=='reduce_methods'
        if hidden:
            # Individual payments, newest first, three per stream at most (the rest wait for the next ask).
            # Numbering is continuous across streams so the owner answers by number.
            items=[]
            for stream,r in enumerate(p['hidden'][:3],1):
                text_label=(r['method']+' / '+r['name']) if r.get('method') else r['name']
                label=html.escape(text_label)
                for row in r['recent'][:3]:
                    items.append(dict(row,number=len(items)+1,method=r.get('method'),stream=stream,stream_name=r['name'],
                                      stream_ids=r['ids'],place=label,place_text=text_label))
        else:
            # Keep issued references stable when a partial answer leaves rows
            # pending. Retrying an old number must not select a different row.
            numbers = flow.setdefault('refund_numbers', {})
            items = []
            for row in _recall_rows(p['refunds'], _recall_today(flow)):
                key = str(row['id'])
                if key not in numbers:
                    numbers[key] = max(numbers.values(), default=0) + 1
                items.append(dict(row, number=numbers[key]))
        if not hidden and flow.get('refund_focus'):
            focused = [r for r in items if r['id'] in flow['refund_focus']]
            if focused:
                items = focused
            else:
                flow.pop('refund_focus', None)
                flow.pop('refund_selection', None)
        selection = flow.get('refund_selection') if not hidden else None
        if selection and sum(r['id'] in selection for r in items) < 2:
            # The existing clarification asks which *two* receipts. If the
            # window leaves fewer than two, use the ordinary in-window ask.
            flow.pop('refund_selection', None)
            selection = None
        if selection:
            candidates = [r for r in items if r['id'] in selection]
            names = {r['name'] for r in candidates}
            if len(names) == 1:
                count = t('refund_count_three') if len(candidates) == 3 else t('refund_count', n=len(candidates))
                lines = [t('refund_selection_ask', name=html.escape(candidates[0]['name']), count=count)]
                lines.extend(t('refund_selection_item', date=_payment_date(r['date'], flow['today']),
                               amount=_money(r['amount'])) for r in candidates)
            else:
                lines = [t('refund_selection_general')]
                lines.extend(t('refund_item', date=_payment_date(r['date'], flow['today']),
                               name=html.escape(r.get('display_name',r['name'])), amount=_money(r['amount'])) for r in candidates)
            return _q(qid, '\n'.join(lines), [('skip', 'skip')], free=True, items=candidates,
                      answer_format={'items': [{'number': candidates[0]['number'], 'bucket': 'leisure|unknown',
                                                'refund_kind': 'refund|expense_reimbursement|cash_exchange'}]})
        if hidden:
            lines = [t('methods_title')]
        else:
            first, last = text.month_bounds(p['months'][0], p['months'][-1])
            lines = [t('import_read', first=first, last=last)+
                     (t('import_through',month=int(p['through'][5:7]),day=int(p['through'][8:])) if p['through'] else ''), '']
        if not hidden and p.get('matched_payments'):
            lines.append(t('matched_title',owner=t('owner_slot')))
            month = None
            for row in p['matched_payments']:
                if row['date'][:7] != month:
                    month = row['date'][:7]
                    lines += ['', '<b>'+t('matched_month',month=int(month[5:]))+'</b>']
                key = ('matched_people' if row['shares']==row['senders'] else 'matched_shares') if row['people'] else 'matched_item'
                lines.append(t(key,date=_payment_date(row['date'], flow['today']),name=html.escape(row.get('display_name',row['name'])),
                               amount=_money(row['amount']),people=row['people'],senders=row['senders'],shares=row['shares']))
        if not hidden and items:
            lines += ['', t('refunds_title')]
        for r in items:
            lines.append(ITEM+str(r['number'])+'. '+t('methods_payment',date=_payment_date(r['date'], flow['today']),name=r['place'],amount=_money(r['amount'])) if hidden
                         else t('refund_item',n=r['number'],date=_payment_date(r['date'], flow['today']),name=html.escape(r.get('display_name',r['name'])),amount=_money(r['amount'])))
        if hidden or items:
            count=t('refund_count_three') if len(items)==3 else t('refund_count',n=len(items))
            lines += [t('methods_ask')] if hidden else ['', t('refunds_ask',count=count)]
        from ledger_roster_modules import MODULES
        question = _q(qid,'\n'.join(lines).lstrip(),[('skip','skip')] if items else [('next','next')],free=bool(items),items=items,
                  answer_format={'items':[{'number':1,**({'module':'|'.join((*MODULES, 'self_transfer', 'unknown')),
                      'scope':'item|stream (stream: the whole stream of that line)',
                      'inferred':False, 'area_module':'optional MODULES value', 'area_inferred':False} if hidden else {'bucket':'leisure|unknown','refund_kind':'refund|expense_reimbursement|cash_exchange'})}]})
        if hidden:
            question['instructions'] = (
                '사용처를 MODULES의 모듈에 연결하세요. 사용자에게 식비/카페 category를 고르게 하지 마세요. '
                '상품·목적을 말하면 되묻지 말고 명확한 모듈을 추론하세요: 음식점/카페 → module_dining, '
                '맥 허브 케이블 → module_shopping, 업무 용도는 area_module=module_work로 추정 연결. '
                '추론한 모듈은 inferred=true, 추론한 영역은 area_inferred=true로 저장하세요. '
                '내 계좌·내 지갑으로 충전한 돈은 self_transfer로 지출에서 제외하세요. '
                '정말 모르면 unknown. 선택지가 없다는 안내를 하지 마세요. 사용처라는 말을 쓰세요. '
                '같은 사용처 전체에 대한 명시적인 답만 scope=stream으로 적용하세요.')
            question['modules'] = [dict(value=module, label=text.t('module.'+module+'.name'))
                                   for module in MODULES]
        else:
            question['sender_answer_format'] = {'items': [{'sender': '<issued name>', 'bucket': 'leisure|unknown|skip',
                'refund_kind': 'refund|expense_reimbursement|cash_exchange'}]}
            question['senders'] = list(dict.fromkeys(r['name'] for r in items))
        return question
    if qid=='reduce_overview':
        window = p.get('window', {})
        months = [month+'-01~'+p['through']
                  if window.get('include_current') and month == window.get('current_month') and p.get('through')
                  else month for month in p['months']]
        lines=[t('overview'),t('basis',months=', '.join(months),n=p['n']),
               t('overview_basis',income=man(p['income']),spend=man(p['spend']))]
        rows=[t('fixed_total',amount=man(g['fixed']+g['subscriptions'])),t('subscription_child',amount=man(g['subscriptions']))]
        rows.append(t('category',name=t('big'),amount=man(g['big'])))
        food = ('meals','alcohol','delivery','cafe','food_cafe','food_other','convenience')
        rows.append(t('category',name=t('food'),amount=man(sum(g.get(key,0) for key in food))))
        for key in food:
            rows.append(t('category_child',name=t(key),amount=man(g.get(key,0))))
        for key in ('coupang','clothes','leisure','other','unknown'):
            rows.append(t('category',name=t(key),amount=man(g.get(key,0))))
        from . import goal_spend
        body = '\n'.join(lines)+'\n<blockquote>'+'\n'.join(rows)+'</blockquote>\n'+t('start_big')
        q = _q(qid, body+'\n\n'+goal_spend.render(flow), [('next','next')],
               free=bool(goal_spend.choices(flow)), goal_spend_section=True,
               goal_spend_groups=copy.deepcopy(goal_spend.groups(flow)))
        buttons = goal_spend.choices(flow)
        if buttons:
            from . import onboarding as O
            q['choices'] += buttons
            O._interactive(q, rows=[buttons, [q['choices'][0]]])
        return q
    if qid=='reduce_installments':
        return screen(flow, 'reduce_cap')
    if qid=='reduce_big':
        lines=[goal_card(flow,picked=True),t('big_title'),t('big_intro')]
        items = [dict(row, number=number) for number,row in enumerate(
            sorted(_big_items(p),key=lambda row:(row['date'],row['id'])),1)]
        unresolved = {r['id'] for r in items}
        for month in p['months']:
            rows=[r for r in p['big'] if r['date'].startswith(month)]
            lines.append('<b>'+t('month_total',month=month,amount=_money(sum(r['amount'] for r in rows)))+'</b>')
            for r in rows:
                name = html.escape(r['display_name'] if r.get('display_name_source') else r.get('installment_name') or r.get('display_name') or r['name'])
                end = r.get('installment_end')
                if end:
                    name=E.label(name,end+'-01')
                args=dict(date=dashboard.question_date(r['date'],flow['today'],short=False),name=name,amount=_money(r['amount']))
                lines.append(t('charge_row',**args))
        if p.get('big_oneoffs'):
            lines.append(t('big_oneoffs'))
            lines.extend(t('charge_row',date=dashboard.question_date(r['date'],flow['today'],short=False),
                name=html.escape(r.get('display_name',r['name'])),amount=_money(r['amount'])) for r in p['big_oneoffs'])
        for cost in p.get('periodic_costs',[]):
            if cost['bucket']!='fixed':
                continue
            row=max(cost['rows'],key=lambda r:(r['date'],r['id']))
            name=html.escape(row.get('display_name',row['name']))
            date=dashboard.question_date(cost['next_date'],flow['today'],short=False)
            lines.append(t('bill_next',name=name,dates=date))
            lines.append(t('charge_row',name=name,date=date,amount=_money(cost['amount'])))
        if items:
            unknown_rows=[]
            for r in items:
                name = html.escape(r['display_name'] if r.get('display_name_source') else r.get('installment_name') or r.get('display_name') or r['name'])
                unknown_rows.append(t('big_unknown_row',n=r['number'],date=dashboard.question_date(
                    r['date'],flow['today'],short=False),name=name,amount=_money(r['amount'])))
            lines += ['',t('big_unknown_count',n=len(items)),
                      '<blockquote>'+'\n'.join(unknown_rows)+'</blockquote>',t('big_unknown_ask')]
        candidates=[]
        for group in p.get('installment_candidates', []):
            row = next((r for r in p['big'] if r['id'] == group['ids'][-1]), None)
            if row is not None:
                candidates.append(dict(group, number=len(candidates)+1, row_id=row['id']))
        if candidates:
            lines += ['', t('big_installment_intro', n=len(candidates))]
            lines.extend(t('big_installment_row', name=html.escape(it.get('display_name',it['name'])),
                           amount=localized_money(it['amount']), day=int(it['dates'][-1][8:]),
                           months=', '.join(t('big_installment_month', month=int(month[5:]))
                                            for month in sorted({date[:7] for date in it['dates']})))
                         for it in candidates)
        if items or candidates:
            lines.append(t('big_combined_ask'))
        return _q(qid,'\n'.join(lines),[('keep_unknown','skip')] if items else [('next','next')],
                  free=bool(items or candidates),items=items,installment_candidates=candidates,big_classification_version=1,
                  answer_format={'items':[{'number':1,'bucket':'clothes|unknown','points_share':0.5}]})
    if qid=='reduce_cap':
        active=[]
        for it in p.get('installments', []):
            if it.get('this_month', it['monthly']) > 0:
                active.append(dict(name=it.get('display_name',it['name']), monthly=it.get('this_month',it['monthly']),
                                   day=it.get('charge_day'), end=it.get('end')))
        for it in p.get('installment_tracking', []):
            if it.get('active'):
                active.append(dict(name=it.get('display_name',it['name']), monthly=it['amount'],
                                   day=int(it['last'][8:]), end=it.get('end')))
        recurring=sum(it['monthly'] for it in active)
        total=max(0, p.get('group_totals',{}).get('big', round(g['big']*max(1,p['n'])))-recurring*max(1,p['n']))
        months=max(1,p['n'])
        lines=[t('cap_title'),t('cap_current',amount=localized_money(g['big']))]
        for it in active:
            if it.get('end'):
                year, month = map(int, it['end'].split('-'))
                now_year = int(flow['today'][:4])
                end = t('cap_end_this',month=month) if year == now_year else (
                    t('cap_end_next',month=month) if year == now_year + 1 else
                    t('cap_end_other',year=year,month=month))
            else:
                end = t('cap_end_unknown')
            lines.append(t('cap_active_row' if it.get('day') else 'cap_active_row_no_day',
                           name=html.escape(it['name']),amount=localized_money(it['monthly']),
                           day=it.get('day'),end=end))
        lines.append(t('cap_other_average',amount=localized_money(round(total/months)),total=localized_money(total),months=months,
                       first=int(p['months'][0][5:]),last=int(p['months'][-1][5:])))
        lines.append(t('cap_non_installment',month=int(flow['today'][5:7])))
        gap=max(0,p['spend']+p['loan']-p['income'])
        overall_money = (lambda value: F._estimate(F._whole_man(value),p.get('projected'))) if text.lang() == 'ko' else localized_money
        lines.append(t('cap_overall',income=overall_money(p['income']),spend=overall_money(p['spend']+p['loan']),
                       gap=overall_money(gap),loan=localized_money(p['loan'])))
        lines.append(text.t('ledger.unmatched_deposit.hint'))
        return _q(qid,'\n'.join(lines),[('cap30','300000'),('cap20','200000'),('cap10','100000')],free=True,
                  cap_sections=2,big_classification_version=1,answer_format={'amount':200000,'effective_from':'YYYY-MM'})
    if qid.startswith('reduce_yearly_'):
        sub=p['subscriptions'][int(qid.rsplit('_',1)[1])]
        date=datetime.date.fromisoformat(sub['renewal'])
        year=date.year-int(flow['today'][:4])
        next_date=t('yearly_date' if year==0 else 'yearly_next_year' if year==1 else 'yearly_other_year',
                    year=date.year,month=date.month,day=date.day)
        if year:
            next_date = dashboard.question_date(date, flow['today'])
        body=t('yearly_offer',name=html.escape(text.josa(sub.get('display_name',sub['name']),'eun')),
               amount=format(sub['yearly_amount'],','),monthly=format(round(sub['yearly_amount']/1200)*100,','),next_date=next_date)
        q=_q(qid,body,[('yearly_yes','yes'),('yearly_no','no')],items=[sub])
        if E.enabled():
            q['choices'].append(dict(label=E.t('cancelled'),value='cancelled'))
            from . import onboarding as O
            q=O._interactive(q,[[choice] for choice in q['choices']])
        return q
    if qid=='reduce_subscriptions':
        lines=[t('subscriptions_title'),goal_card(flow,picked=True)]
        subscriptions = p.get('current_subscriptions', p['subscriptions'])
        if p.get('roster_months'):
            lines.append(t('subscriptions_current',amount=(_money if text.lang() == 'en' else _man)(sum(sub['monthly'] for sub in subscriptions))))
        subscription_lines=[]
        for sub in subscriptions:
            subscription_lines.append(_subscription_row(sub,flow['today'],p.get('projected')))
            if sub.get('recent_change'):
                snapshots=sub['recent_change']
                if snapshots[0]['amount']!=snapshots[-1]['amount']:
                    subscription_lines.append('└ '+' → '.join(t('subscription_snapshot',
                        month=int(snapshot['month'][5:]),amount=_money(snapshot['amount'])) for snapshot in snapshots))
            if sub['cycle']=='yearly':
                subscription_lines.append(t('annual_cancelled') if sub['cancelled'] else t('annual_renewal',date=dashboard.question_date(sub['renewal'], flow['today'], short=False),amount=_money(sub['last']['amount'])))
        if subscription_lines:
            lines.append('<blockquote>'+'\n'.join(subscription_lines)+'</blockquote>')
        lines.extend(_subscription_cards(subscriptions))
        oneoffs = p.get('subscription_oneoffs', [])
        if oneoffs:
            unknown_lines=[]
            for row in oneoffs:
                unknown_lines.append(t('oneoff_row',name=html.escape(row['display_name']),
                    amount=_money(row['amount']),date=dashboard.question_date(row['date'],flow['today'])))
                for payment in row.get('payments',[row])[1:]:
                    unknown_lines.append(t('unknown_payment',amount=_money(payment['amount']),
                        date=dashboard.question_date(payment['date'],flow['today'])))
            lines.extend([t('oneoffs_title'),'<blockquote>'+'\n'.join(unknown_lines)+'</blockquote>'])
        lines.append(t('oneoffs_ask'))
        choices, known = _known_prefill(flow,qid,lines,[('keep','keep')])
        return _q(qid,'\n'.join(lines),choices,free=True,**known,
                  recent_costs='current_subscriptions' in p,
                  subscription_evidence=True, subscription_oneoffs_version=3,
                  oneoffs=copy.deepcopy(oneoffs),
                  subscription_display=copy.deepcopy(dict(subscriptions=subscriptions,
                      projected=p.get('projected'), recent_costs='current_subscriptions' in p,
                      today=flow['today'])),
                  answer_format={'facts':[{'key':'vendor','cycle':'yearly'}]})
    if qid=='reduce_subscription_ai':
        subscriptions = p.get('current_subscriptions', p['subscriptions'])
        ai = [sub for sub in subscriptions if sub['key'] in ('claude','codex')]
        lines = []
        if ai:
            lines.append('<blockquote>'+'\n'.join(_subscription_row(sub,flow['today'],p.get('projected')) for sub in ai)+'</blockquote>')
        lines.append(t('subscription_ai_ask'))
        choices, known = _known_prefill(flow,qid,lines,[('keep','keep')])
        return _q(qid,'\n'.join(lines),choices,free=True,**known,
                  answer_format={'targets':{'ai':600000},'effective_from':'YYYY-MM'})
    if qid=='reduce_subscription_cuts':
        subscriptions = p.get('current_subscriptions', p['subscriptions'])
        lines=[]
        if subscriptions:
            lines.append('<blockquote>'+'\n'.join(_subscription_row(sub,flow['today'],p.get('projected')) for sub in subscriptions)+'</blockquote>')
        lines.append(t('subscription_cuts_ask'))
        return _q(qid,'\n'.join(lines),[('keep','keep')],free=True,
                  answer_format={'facts':[{'key':'vendor','cancelled':True}]})
    if qid=='reduce_habits':
        lines=[*(flow.get('end_confirmations',[]) if E.enabled() else []),t('habits_title'),goal_card(flow,picked=True),
               '',t('subscriptions_set',amount=localized_money(_projection(flow)['subscriptions'])),t('habits_current')]
        food=('meals','delivery','alcohol','food_cafe','cafe','convenience','food_other')
        categories=((t('food'),food),(t('shopping_group'),('coupang','clothes')),
                    (t('other'),('leisure','other','unknown')))
        for label,keys in categories:
            lines.append('<b>'+label+'</b>')
            rows=[]
            for key in keys:
                m=p['metrics'].get(key)
                if m and m['unit_price']:
                    amount=F._estimate(_money(g[key]),p.get('projected'))
                    row=t('habit_amount',name=t(key),amount=amount)
                    if key!='meals':
                        row+=t('habit_count',count=F._estimate(format(m['monthly_count'],'.1f').rstrip('0').rstrip('.'),p.get('projected')))
                    rows.append(row)
                elif g.get(key):
                    rows.append(t('habit_amount',name=t(key),amount=localized_money(g[key])))
            if rows: lines.append('<blockquote>'+'\n'.join(rows)+'</blockquote>')
        lines += [t('meals_keep'),t('everyday'),t('habits_ask')]
        lines += [chr(0x2022)+' '+t(key) for key in ('delivery','alcohol','cafe','coupang','clothes')]
        proposals=_habit_options(flow)
        for n,(name,counts) in enumerate(zip(('gentle','delivery_focus','strong'),proposals),1):
            preview=_proposal(flow,counts)
            lines.append(t('proposal',n=n,name=t(name),saved=_man(sum(preview['cuts'].values())),remaining=_man(preview['remaining'])))
            lines.extend(_count_lines(flow,counts))
        lines.append(t('recommend_delivery'))
        return _q(qid,'\n'.join(lines),[('gentle','gentle'),('delivery_focus','delivery_focus'),('strong','strong'),('keep','keep')],free=True,
                  proposals=proposals,answer_format={'counts':{'delivery':12,'alcohol':3,'cafe':8,'coupang':6,'clothes':2}})
    if qid=='reduce_shopping':
        options=_shopping_options(flow)
        ledger=_ledger(flow)
        lines=[t('shopping_intro',amount=man(ledger['remaining']))]
        for n,(label,counts) in enumerate(zip(('shopping_only','spread'),options),1):
            preview=_proposal(flow,counts)
            lines.append(t('proposal',n=n,name=t(label),saved=man(sum(preview['cuts'].values())-sum(_projection(flow)['cuts'].values())),remaining=man(preview['remaining'])))
            lines.extend(_count_lines(flow,counts))
        return _q(qid,'\n'.join(lines),[('shopping_only','shopping'),('spread','spread')],free=True,proposals=options,
                  answer_format={'action':'subscriptions'})
    if qid=='reduce_tradeoff':
        if _owner_ai_cap(flow) is not None:
            counts=_shopping_options(flow)[0]
            candidate=_ledger(flow,_tradeoff_targets(flow,'shopping'))
            lines=[t('revisit_intro'),'',t('revisit_shopping_heading',label=t('revisit_recommended',label=t('revisit_shopping')))]
            lines.extend(_candidate_counts(flow,counts))
            lines += [t('revisit_remainder',remaining=_candidate_number(candidate['remaining'])),'',t('revisit_unselected')]
            return _q(qid,'\n'.join(lines),[('revisit_shopping','shopping')],free=False)
        current=_ledger(flow)
        candidates=[_ledger(flow,_tradeoff_targets(flow,value)) for value in ('ai','half','shopping')]
        ai=current['rows']['ai']['spend']
        half_cut=ai-candidates[1]['rows']['ai']['spend']
        counts=_counts(flow,dict(coupang=6,clothes=2))
        shopping=_shopping_options(flow)[0]
        remainders=[candidate['remaining'] for candidate in candidates]
        # Rank exact won values before display rounding; ties prefer half, AI,
        # then shopping. The body, headings and keyboard share this choice.
        recommended=min(range(3),key=lambda n:(remainders[n],(1,0,2)[n]))
        labels=[t('ai_only'),t('revisit_half_label'),t('revisit_shopping')]
        labels[recommended]=t('revisit_recommended',label=labels[recommended])
        lines=[t('revisit_intro'),
               '',t('revisit_ai',label=labels[0],before=_candidate_number(ai),after=_candidate_number(candidates[0]['rows']['ai']['spend'])),
               t('revisit_remainder',remaining=_candidate_number(remainders[0])),
               '',t('revisit_half',label=labels[1],before=_candidate_number(ai),after=_candidate_number(candidates[1]['rows']['ai']['spend']),cut=_candidate_number(half_cut))]
        lines.extend(_candidate_counts(flow,counts))
        lines += [t('revisit_remainder',remaining=_candidate_number(remainders[1])),'',t('revisit_shopping_heading',label=labels[2])]
        lines.extend(_candidate_counts(flow,shopping))
        lines += [t('revisit_remainder',remaining=_candidate_number(remainders[2])),'',t('revisit_unselected')]
        from . import onboarding as O
        return O._interactive(O._q(qid,'\n'.join(lines),
                  [dict(label=label,value=value) for label,value in zip(labels,('ai','half','shopping'))],free=True,
                  answer_format={'action':'next_bill','key':'subscription key'}))
    if qid=='reduce_bill':
        bill=_bill_candidate(flow,flow['billing_key'])
        sub=next(s for s in p['subscriptions'] if s['key']==bill['key'])
        name=html.escape(sub['display_name'] if sub.get('display_name_source') else t('vendor.'+bill['key']) if bill['key'] in ('claude','codex') else sub.get('display_name',sub['name'])); plan=html.escape(flow.get('billing_plan',''))
        before=sum(r['amount'] for r in bill['rows']); saved=before-bill['amount']
        current=_ledger(flow)
        targets=_billing_targets(flow,bill,bill['amount'])
        billing=_ledger(flow,targets)
        subscription_saved=current['rows']['subscriptions']['spend']-billing['rows']['subscriptions']['spend']
        lines=[]
        if sub.get('recurring_days') and sub.get('first_seen', '')[:7] in p['months']:
            lines.append(t('revisit_recurrence',name=name,month=int(sub['first_seen'][5:7]),day=min(sub['recurring_days'])))
        lines += [t('bill_next',name=name,dates='~'.join(_payment_date(d, flow['today']) for d in bill['next_dates'])),
               '',t('revisit_bill_header',month=int(bill['month'][5:]),name=name)]
        lines.extend(t('revisit_charge',date=_payment_date(r['date'], flow['today']),amount=_money(r['amount'])) for r in bill['rows'])
        lines.append(t('revisit_charge_sum',amount=_money(before)))
        bill_money = _money if text.lang() == 'en' else _candidate_number
        lines += ['',t('bill_possible',name=name,amount=bill_money(bill['amount']),cut=bill_money(subscription_saved)),
                  t('bill_totals',ai=bill_money(billing['rows']['ai']['spend']),subscriptions=bill_money(billing['rows']['subscriptions']['spend'])),
                  '',t('revisit_ai_header',cut=_candidate_number(subscription_saved)),
                  t('revisit_plan_cut',name=name,before=_candidate_number(before),after=_candidate_number(bill['amount']),month=int(bill['next_dates'][0][5:7]),plan=plan) if plan else
                  t('revisit_plan_cut_unknown',name=name,before=_candidate_number(before),after=_candidate_number(bill['amount']))]
        counts=_counts(flow,dict(coupang=6,clothes=2))
        targets['counts']=dict(targets.get('counts',{}),**counts)
        preview=_ledger(flow,targets)
        shopping_saved=preview['projection']['saved']-billing['projection']['saved']
        lines += ['',t('revisit_shopping_header',cut=_candidate_number(shopping_saved))]
        lines.extend(_candidate_counts(flow,counts))
        total=subscription_saved+shopping_saved
        fill=t('revisit_fill',amount=_candidate_number(total))
        lines.insert(lines.index(t('revisit_ai_header',cut=_candidate_number(subscription_saved))),fill+'\n')
        lines += ['',t('revisit_outcome',saved=_candidate_number(total),remaining=_candidate_number(preview['remaining']))]
        relation='less' if preview['remaining']<p['loan'] else 'equal' if preview['remaining']==p['loan'] else 'more'
        lines.append(t('revisit_loan_'+relation,loan=_candidate_number(p['loan'])))
        lines.append(t('revisit_confirm'))
        return _q(qid,'\n'.join(lines),[('confirm_bill','confirm'),('no_shopping','no_shopping')],free=True,billing=bill,counts=counts,
                  answer_format={'amount':bill['amount'],'effective_from':bill['next_dates'][0][:7]})
    if qid != 'reduce_goal':
        raise ValueError('unknown reduction screen')
    lines = [t('goal_final'), goal_card(flow)]
    lines.extend(_shortfall_lines(flow))
    lines.append(t('pin_ask'))
    return _q(qid,'\n'.join(lines),[('confirm','confirm')],shortfall_version=1)


def _month(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])',value):
        raise ValueError('target month must be YYYY-MM')
    return value


def _data(value):
    data=json.loads(value)
    if not isinstance(data,dict):raise ValueError('answer must be an object')
    return data


def _refund_scope(items, given, said, *, pair=False):
    """A relay's row numbers cannot resolve an owner's ambiguous 'both'."""
    if not pair and not re.search(r'둘\s*다|두\s*건|\bboth\b', said, re.I):
        return []
    # Bank statements mask the same names the owner spells out in full.
    named = [r for r in items if re.search('.'.join(re.escape(p) for p in r['name'].split('*')), said)]
    groups = {}
    for row in named or items:
        groups.setdefault(row['name'], []).append(row)
    selected = {r.get('number') for r in given}
    if not named and len(items) == 2:
        return [] if selected == {r['number'] for r in items} else items
    if named and (len(named) < 2 or selected - {r['number'] for r in named}):
        return items
    ambiguous = []
    owner_selected = set()
    for rows in groups.values():
        if len(rows) < 2:
            continue
        # Dates/amounts quoted by the OWNER can disambiguate two receipts.
        # Model-supplied JSON numbers alone are never evidence of that choice.
        explicit = []
        for row in rows:
            amount = str(abs(row['amount']))
            if (row['date'] in said or
                    re.search(r'(?<!\d)' + re.escape(amount) + r'(?!\d)', said.replace(',', ''))):
                explicit.append(row['number'])
        owner_selected.update(explicit)
        expected = set(explicit) if len(explicit) == 2 else {r['number'] for r in rows} if len(rows) == 2 else set()
        if not expected or selected.intersection(r['number'] for r in rows) != expected:
            ambiguous.extend(rows)
    # Bare 'both' across more than one sender needs an explicit pair as well.
    if not named and len(items) != 2 and (len(owner_selected) != 2 or selected != owner_selected):
        return items
    return ambiguous


def _refresh_refunds(flow):
    from . import onboarding as O, merchant_names
    question = screen(flow, 'reduce_refunds')
    merchant_names.attach(question, question, flow['plan'])
    question['nonce'] = flow['nonce']
    question['card'] = O.review_card(question)
    flow['questions']['reduce_refunds'] = question


def _answer_refunds(flow, q, value, provenance):
    items = q['items']
    numbers = flow.setdefault('refund_numbers', {})
    numbers.update({str(r['id']): r['number'] for r in items})
    data = {} if value == 'skip' or (value == 'next' and not items) else _data(value)
    given = data.get('items', [])
    expanded = []
    for fact in given:
        if 'sender' in fact:
            sender = fact['sender']
            rows = [r for r in items if r['name'] == sender]
            if not rows or not isinstance(sender, str):
                raise ValueError('choose an issued refund sender')
            expanded.extend(dict({k: v for k, v in fact.items() if k != 'sender'}, number=r['number'])
                            for r in rows)
        else:
            expanded.append(fact)
    given = expanded
    if value != 'skip':
        scope = _refund_scope(items, given, provenance, pair=bool(flow.get('refund_selection')))
        if scope:
            flow['refund_selection'] = [r['id'] for r in scope]
            flow['refund_focus'] = flow['refund_selection']
            _refresh_refunds(flow)
            return dict(complete=False, saved_refunds=[], selection_required=scope)
    facts = []; seen = set()
    for fact in given:
        number = fact.get('number')
        row = next((r for r in items if type(number) is int and r['number'] == number), None)
        if not row or number in seen:
            raise ValueError('answer only issued refund numbers once')
        if any(k in fact for k in ('stream', 'scope')):
            raise ValueError('refund answers select individual issued receipts')
        seen.add(number)
        facts.append(dict(fact, kind='refund', id=row['id'], said=provenance))
        facts[-1].pop('number')
    if value == 'skip':
        facts = [dict(kind='refund', id=r['id'], bucket='skip', said=provenance) for r in items]
    if not facts and items:
        raise ValueError('select issued refunds or explicitly skip')
    if facts:
        kit.run('ledger-plan-answer', '--data', json.dumps(dict(kind='batch', items=facts, said=provenance), ensure_ascii=False))
    _read(flow)
    # Read the committed facts, including normalized cash-exchange neutrality.
    saved = []
    for fact in facts:
        row = next(r for r in items if r['id'] == fact['id'])
        stored = flow['plan']['facts']['refund:' + str(row['id'])]
        saved.append(dict(id=row['id'], number=row['number'], date=row['date'], name=row['name'],
                          amount=row['amount'], bucket=stored['bucket'], refund_kind=stored.get('refund_kind'),
                          said=stored['said']))
    saved_lines = '\n'.join(t('refund_saved_item', date=_payment_date(r['date'], flow['today']),
                       name=html.escape(r.get('display_name',r['name'])), amount=_money(r['amount']),
                       kind=t('receipt_' + (r['refund_kind'] or 'unknown')),
                       bucket=(' / ' + t(r['bucket'], amount='').strip() if r['bucket'] != 'skip' else '')) for r in saved)
    flow.pop('refund_selection', None)
    # Finish the sender's clarification before issuing unrelated receipts.
    # They remain in the plan, without facts or renumbering, for the next ask.
    remaining_refunds = _recall_rows(flow['plan']['refunds'], _recall_today(flow))
    if not any(r['id'] in flow.get('refund_focus', []) for r in remaining_refunds):
        flow.pop('refund_focus', None)
    complete = not remaining_refunds
    if not complete:
        _refresh_refunds(flow)
    ack = ''
    if saved:
        ack = '\n'.join([t('refunds_title'), t('refund_saved'), '', saved_lines])
        remaining = flow['questions']['reduce_refunds']['items'] if not complete else []
        if remaining:
            items = ' · '.join(t('refund_remaining_item', date=_payment_date(r['date'], flow['today']),
                                 name=html.escape(r.get('display_name',r['name'])), amount=_money(r['amount'])) for r in remaining)
            ack += '\n\n' + t('refund_remaining', count=t('refund_count', n=len(remaining)), items=items)
    return dict(complete=complete, saved_refunds=saved, ack=ack, say=ack)


def _lifecycle_words(value, items, today):
    """Translate an explicitly identified payment's lifecycle, never --said alone."""
    if re.search(r'아니|않|not\b|never\b', value, re.I):
        return None
    number = re.search(r'(?<!\d)(\d+)\s*번', value)
    named = [r for r in items if r['name'].casefold() in value.casefold()]
    row = (next((r for r in items if r['number']==int(number[1])), None)
           if number else named[0] if len(named)==1 else None)
    if row is None:
        return None
    detail = dict(number=row['number'])
    remaining = re.search(r'(\d+)\s*(?:개월|months?)\s*(?:쯤|정도)?\s*(?:남|left)', value, re.I)
    if remaining and re.search(r'재등록|갱신|renew', value, re.I):
        detail.update(remaining_paid_months=int(remaining[1]), renewal_expected=True, renewal_amount=None)
    elif re.search(r'멈|중단|그만|해지|stopped|cancelled', value, re.I):
        date = re.search(r'\d{4}-\d{2}-\d{2}', value)
        detail.update(stopped_on=date[0] if date else today)
    elif re.search(r'일회성|한\s*번(?:만)?|one[ -]?off|one[ -]?time', value, re.I):
        detail.update(one_off=True)
    else:
        return None
    return json.dumps(dict(items=[detail]), ensure_ascii=False)


def answer(flow,qid,value,said,callback=None):
    p=flow['plan'];targets=flow.setdefault('targets',{});q=flow['questions'][qid]
    provenance=((q.get('known') or {}).get('said') if (q.get('known') or {}).get('value')==value else None) or said or next((c['label'] for c in q['choices'] if c['value']==value),'')
    if qid=='reduce_debts':
        if value == 'known':
            known = q.get('known')
            if not known or not known.get('kit_source'): raise ValueError('no kit-recorded value on this card')
            fact = {key: val for key, val in known.items() if key not in ('said', 'kit_source')}
            fact['number'] = 1
            fact['said'] = t('debt_known_provenance', provenance=known['said'])
            data = {'items': [fact]}
            provenance = fact['said']
        else:
            data={} if value=='skip' else _data(value)
        facts=[];seen=set()
        for fact in data.get('items',[]):
            row = (next((r for r in flow['history']['debts'] if type(fact.get('id')) is int and r['id']==fact['id']), None)
                   if 'id' in fact else next((r for r in q['items'] if r['number']==fact.get('number')),None))
            if not row or row['id'] in seen:raise ValueError('answer only issued loan numbers once')
            seen.add(row['id'])
            facts.append(dict(fact,kind='debt_balance',id=row['id'],said=fact.get('said') or provenance,
                              repayment_words=fact.get('said') or (provenance if len(data['items'])==1 else '')))
        if value=='skip' and not q.get('known'):
            for row in q['items']:
                facts.append(dict(kind='debt_balance',id=row['id'],repayment_type='unknown',said=t('skip')))
        # Skipping a remembered value is navigation, not an owner statement
        # that the repayment type is unknown. Preserve the evidenced block.
        if value != 'skip' or not q.get('known'):
            kit.run('ledger-plan-answer','--today',flow['today'],'--data',json.dumps(dict(kind='batch',items=facts,said=provenance),ensure_ascii=False))
        flow['history']=F._history(flow)
        _read(flow)
        if value == 'known' or value == 'skip' and q.get('known'):
            reviewed = flow.setdefault('debt_reviewed_ids', [])
            if q['known']['id'] not in reviewed:
                reviewed.append(q['known']['id'])
        if flow.get('debt_reopen_id') in seen or value == 'skip' and q.get('known'):
            flow.pop('debt_reopen_id', None)
            flow.pop('revisit', None)
        remaining=[d for d in flow['history']['debts'] if _debt_pending(flow, d)]
        if remaining:
            flow['questions'][qid]=screen(flow,qid)
            return False
        return True
    elif qid=='reduce_purposes':
        if not q['items']:
            if value != 'next': raise ValueError('choose next')
        else:
            data = {} if value == 'skip' else _data(value)
            facts = []; seen = set()
            for fact in data.get('items', []):
                number = fact.get('number')
                row = next((r for r in q['items'] if type(number) is int and r['number']==number), None)
                if not row or number in seen: raise ValueError('answer only issued purpose numbers once')
                seen.add(number)
                for stream in row['purpose_streams']:
                    facts.append(dict(kind='hidden',name=stream['name'],method=stream['method'],
                                      category=fact.get('category'),said=provenance,
                                      **{key:fact[key] for key in ('min_amount','max_amount') if key in fact}))
            if value == 'skip':
                for row in q['items']:
                    for stream in row['purpose_streams']:
                        facts.append(dict(kind='hidden',name=stream['name'],method=stream['method'],category='skip',said=provenance))
            kit.run('ledger-plan-answer','--data',json.dumps(dict(kind='batch',items=facts,said=provenance),ensure_ascii=False))
            _read(flow)
    elif qid=='reduce_methods':
        data={} if value=='skip' else _data(value)
        items=q['items'];seen=set();facts=[];streams=set()
        from ledger_roster_modules import MODULES
        for fact in data.get('items',[]):
            if fact.get('module') not in (*MODULES, 'self_transfer', 'unknown'):
                raise ValueError('answer with a module, self_transfer or unknown')
            label=fact.get('stream')
            if isinstance(label,str):
                # The owner named the stream: apply to the whole stream.
                members=[r for r in items if r['place_text']==label]
                if not members:raise ValueError('answer only issued streams')
                item=members[0];whole=True
            else:
                number=fact.get('number')
                if type(number) is not int or number not in {r['number'] for r in items}:
                    raise ValueError('answer only issued numbers')
                item=next(r for r in items if r['number']==number)
                # An explicit same-kind answer classifies the whole stream or rail range.
                whole=fact.get('scope')=='stream' or fact.get('max_amount') is not None
            if item['number'] in seen:continue
            detail={k:v for k,v in fact.items() if k not in ('number','stream','scope')}
            if whole:
                if item['stream'] in streams:continue
                streams.add(item['stream'])
                seen.update(r['number'] for r in items if r['stream']==item['stream'])
                facts.append(dict(detail,kind='hidden',said=provenance,name=item['stream_name'],method=item['method']))
            else:
                seen.add(item['number'])
                facts.append(dict(detail,kind='hidden',said=provenance,id=item['id']))
        if value=='skip':
            # The owner declined the whole ask: the streams stay unknown and are not asked again (unchanged behaviour).
            for stream in sorted({r['stream'] for r in items}):
                row=next(r for r in items if r['stream']==stream)
                facts.append(dict(kind='hidden',method=row['method'],name=row['stream_name'],category='skip',said=provenance))
                facts.extend(dict(kind='item',id=sid,bucket='skip',said=provenance) for sid in row['stream_ids'])
        else:
            # An unanswered shown payment remains unknown and is not asked again; older payments of the stream wait for the next ask.
            for item in items:
                if item['number'] not in seen:
                    facts.append(dict(kind='hidden',id=item['id'],category='skip',said=provenance))
        kit.run('ledger-plan-answer','--data',json.dumps(dict(kind='batch',items=facts,said=provenance),ensure_ascii=False))
        _read(flow)
    elif qid=='reduce_transfers':
        items=q['items'];facts=[];seen=set()
        if len(items) != 1 or items[0]['amount'] < TRANSFER_ASK_MIN_WON:
            raise ValueError('refresh the issued single-transfer card before answering')
        if value in ('debt','settlement','gift','unknown'):
            # A tap answers only this payment, never the rest of its stream.
            given=[dict(number=r['number'],answer=value) for r in items]
        elif value=='skip':
            given=[dict(number=r['number'],answer='unknown') for r in items]
        else:
            given=_data(value).get('items',[])
        for fact in given:
            if 'stream' in fact or fact.get('scope','item') != 'item':
                raise ValueError('answer only the issued transfer item')
            number=fact.get('number')
            if type(number) is not int or number != 1:
                raise ValueError('answer only issued numbers')
            item=items[0]
            if fact.get('answer') not in ('debt','settlement','gift','unknown'):
                raise ValueError('transfer answer is debt, settlement, gift or unknown')
            if item['number'] in seen:continue
            seen.add(item['number'])
            facts.append(dict(kind='transfer',answer=fact['answer'],said=provenance,id=item['id']))
        # An unanswered shown payment stays unknown and is not asked again.
        facts.extend(dict(kind='transfer',answer='unknown',said=provenance,id=r['id']) for r in items if r['number'] not in seen)
        kit.run('ledger-plan-answer','--today',flow['today'],'--data',json.dumps(dict(kind='batch',items=facts,said=provenance),ensure_ascii=False))
        flow['history']=F._history(flow)
        _read(flow)
    elif qid == 'reduce_refunds':
        return _answer_refunds(flow, q, value, provenance)
    elif qid in ('reduce_big', 'reduce_installments'):
        if qid == 'reduce_big' and value not in ('skip','next') and not value.startswith('{'):
            remaining = re.search(r'(?:(?P<name>[^,\n]+?)\s+)?(?P<count>\d{1,2})\s*번\s*남', value)
            candidates = q.get('installment_candidates', [])
            if remaining and candidates:
                name=(remaining.group('name') or '').strip()
                number=re.fullmatch(r'(\d+)\s*번?\s*(?:할부)?',name)
                def matching_name(value):
                    return re.sub(r'[\s_()]+', '', value.replace('할부','')).casefold()
                needle=matching_name(name)
                matches=[it for it in candidates if
                         (number and it['number']==int(number[1])) or
                         (not number and (not needle or any(needle in matching_name(label)
                             for label in (it['name'],it.get('display_name',it['name'])))))]
                group=matches[0] if len(matches)==1 else None
                if group:
                    item_match=re.search(r'(?<!\d)(\d+)\s*(?:는|번은)',value)
                    item_data=[]
                    if item_match and q.get('items'):
                        named=re.search(r'(?<!\d)\d+\s*(?:는|번은)\s*([^,\n]+)',value)
                        item_data=[dict(number=int(item_match.group(1)),bucket='unknown',
                                        **(dict(name=named.group(1).strip()) if named else {}))]
                    value=json.dumps(dict(items=item_data,installment=dict(number=group['number'],
                        remaining_payments=int(remaining.group('count')))),ensure_ascii=False)
            if not value.startswith('{'):
                value = _lifecycle_words(value, q['items'], flow['today']) or value
        if value not in ('skip','next') and not value.startswith('{'):
            # Accept an issued group number/name or the sole displayed group
            # with explicit months. Repetition alone never establishes terms.
            term = re.search(r'(\d{1,2})\s*(?:개월|months?)', value)
            number = re.search(r'(?<!\d)(\d+)\s*번', value)
            candidates = q.get('installment_candidates', [])
            if qid == 'reduce_big':
                candidates = [dict(g,number=r['number']) for g in p.get('installment_candidates',[])
                              for r in p['big'] if r['id']==g['ids'][0]]
            group = next((g for g in candidates
                          if number and g['number']==int(number[1])), None)
            if not number:
                named = [g for g in candidates if g['name'].casefold() in value.casefold()]
                group = candidates[0] if len(candidates)==1 else named[0] if len(named)==1 else None
            if not term or not group:
                raise ValueError('choose an issued installment group and state its months')
            name = value[:term.start()]
            name = re.sub(r'^\s*(?:\d+\s*번|그거)\s*', '', name).strip(' /,')
            value = json.dumps(dict(billed_terms=dict(number=group['number'],months=int(term[1]),
                **(dict(name=name) if name else {}))), ensure_ascii=False)
        data={} if value=='skip' or (value=='next' and not q['items']) else _data(value)
        items=q['items'];given=data.get('items',[]);seen=set();facts=[]
        for fact in given:
            number=fact.get('number')
            if type(number) is not int or number in seen or number not in {r['number'] for r in items}:
                raise ValueError('answer only issued numbers once')
            seen.add(number)
            item=next(r for r in items if r['number']==number)
            if qid == 'reduce_installments':
                raise ValueError('answer installment terms on this card')
            kind = 'payment_lifecycle' if any(k in fact for k in (
                'one_off','stopped_on','remaining_paid_months','paid_through','renewal_expected')) else 'item'
            detail=dict(fact,kind=kind,said=provenance)
            detail.pop('number',None)
            detail['id']=item['id']
            facts.append(detail)
        # An unanswered past item remains unknown and is never asked again.
        for item in items if qid == 'reduce_big' else []:
            if item['number'] in seen or item.get('reviewed'):continue
            facts.append(dict(kind='item',id=item['id'],bucket='skip',said=provenance))
        terms=data.get('billed_terms')
        if terms:
            row=next((r for r in p['big'] if r['number']==terms.get('number')),None)
            if not row: raise ValueError('installment must reference an issued big payment')
            facts.append(dict(terms,kind='billed_terms',id=row['id'],as_of=flow['today'],said=provenance))
        installment=data.get('installment')
        if installment:
            candidate=next((r for r in q.get('installment_candidates',[]) if r['number']==installment.get('number')),None)
            row=next((r for r in p['big'] if r['id']==candidate.get('row_id')),None) if candidate else next((r for r in p['big'] if r['number']==installment.get('number')),None)
            if not row:raise ValueError('installment must reference an issued big payment')
            # An explicit full term is anchored to the observed first charge.
            # A conversational remaining count must not shift that end month.
            kind = ('billed_installment' if 'total' in installment else 'billed_terms') if 'months' in installment else 'remaining_installment'
            facts.append(dict(installment,kind=kind,id=row['id'],as_of=flow['today'],said=provenance))
        kit.run('ledger-plan-answer','--data',json.dumps(dict(kind='batch',items=facts,said=provenance),ensure_ascii=False))
        _read(flow)
    elif qid=='reduce_overview':
        from . import goal_spend
        if goal_spend.answer(flow, value, said):
            return False
        if value!='next':raise ValueError('choose next')
    elif qid=='reduce_cap':
        data=_data(value) if value.startswith('{') else dict(amount=int(value))
        amount=data.get('amount')
        if type(amount) is not int or amount<0:raise ValueError('shopping cap must be whole nonnegative won')
        targets['big']=amount
        default=flow['today'][:7]
        targets['big_from']=_month(data.get('effective_from',default))
    elif qid.startswith('reduce_yearly_'):
        if value not in (('yes','no','cancelled') if E.enabled() else ('yes','no')):raise ValueError('choose the issued renewal reminder option')
        kit.run('ledger-plan-answer','--data',json.dumps(dict(kind='subscription',key=q['items'][0]['key'],
                cycle='yearly',name=q['items'][0]['name'],reminder_enabled=value=='yes',said=provenance,**(dict(cancelled=True,ends_on=q['items'][0]['renewal']) if value=='cancelled' else {})),ensure_ascii=False),'--today',flow['today'],*F.window_args(flow))
        _read(flow)
        if value=='cancelled':
            sub=next(s for s in flow['plan']['subscriptions'] if s['key']==q['items'][0]['key'])
            date=datetime.date.fromisoformat(sub['ends_on'])
            flow.setdefault('end_confirmations',[]).append(E.t('end_confirmation',name=html.escape(text.josa(sub.get('display_name',sub['name']),'eun')),year=date.year,month=date.month,day=date.day))
            from ledger_plan import subscription_services
            flow['targets']['subscriptions']=sum(flow['targets'].get('subscription_targets',{}).get(s['key'],s['monthly'] if not s['cancelled'] else s.get('paid_monthly',0)) for s in subscription_services(flow['plan']))
    elif qid=='reduce_subscriptions':
        data={} if value=='keep' else _data(value) if value.lstrip().startswith(('{','[')) else None
        facts=(data or {}).get('facts',[])
        # Compatibility for already-issued pre-split cards and direct callers:
        # land their combined payload atomically through the final step.
        if data is not None and (data.get('targets') or any(f.get('cycle')!='yearly' or set(f)-{'key','cycle'} for f in facts)):
            flow['subscription_ai_draft']=dict(targets=data.get('targets',{}),effective_from=data.get('effective_from',flow['today'][:7]))
            flow['questions']['reduce_subscription_cuts']=screen(flow,'reduce_subscription_cuts')
            cuts=dict(facts=facts)
            return answer(flow,'reduce_subscription_cuts',json.dumps(cuts,ensure_ascii=False),said,callback=callback)
        names=[]
        if data is None:
            names=[value]
        else:
            names=[f['key'] for f in facts if f.get('cycle')=='yearly']
            if any(f.get('cycle')!='yearly' for f in facts):
                raise ValueError('annual subscription facts only')
        for name in names:
            if p['facts'].get('subscription:' + name, {}).get('cycle') == 'yearly':
                # A sourced prior annual fact is already bound to its service.
                # Confirmation must not search the unconfirmed one-off list.
                kit.run('ledger-plan-answer', '--today', flow['today'], *F.window_args(flow),
                    '--data', json.dumps(dict(kind='subscription', key=name, cycle='yearly',
                                             said=provenance), ensure_ascii=False))
            else:
                kit.run('ledger-plan-answer', '--today', flow['today'], *F.window_args(flow),
                    '--data', json.dumps(dict(kind='subscription_annual_name', name=name,
                        issued_ids=[r['id'] for r in q.get('oneoffs', [])], said=provenance), ensure_ascii=False))
        if names:
            _read(flow)
        return True
    elif qid=='reduce_subscription_ai':
        usd = [] if value=='keep' or value.lstrip().startswith('{') else re.findall(r'(\d[\d,]*(?:\.\d+)?)\s*(?:달러|불|USD|\$)', value, re.I)
        if usd:
            original=sum(float(n.replace(',','')) for n in usd)
            if not original.is_integer():
                raise ValueError('AI USD cap needs whole dollars')
            from ledger_plan import _amount_change
            converted=_amount_change(dict(amount=int(original),currency='USD',effective_from=flow['today'][:7]),None)
            amount=converted['amount']
            data={'targets':{'ai':amount}}
            flow['owner_ai_cap']=dict(amount_krw=amount,original_amount=int(original),original_currency='USD',
                fx_krw_per_usd=converted['fx_krw_per_usd'],fx_source=converted['fx_source'],
                fx_date=flow['today'],said=said)
        else:
            data={} if value=='keep' else _data(value) if value.lstrip().startswith('{') else {'targets':{'ai':int(value.replace(',','').replace('원','').strip())}}
        requested=data.get('targets',{})
        if set(requested)-{'ai'} or any(type(a) is not int or a<0 for a in requested.values()):
            raise ValueError('AI target needs exact nonnegative won')
        flow['subscription_ai_draft']=dict(targets=requested,effective_from=data.get('effective_from',flow['today'][:7]))
        if 'ai' in requested and not usd:
            flow['owner_ai_cap']=dict(amount_krw=requested['ai'],original_amount=requested['ai'],
                original_currency='KRW',fx_date=flow['today'],said=said)
        if (q.get('known') or {}).get('value') == value and q['known'].get('owner_ai_cap'):
            flow['owner_ai_cap'] = copy.deepcopy(q['known']['owner_ai_cap'])
        return True
    elif qid=='reduce_subscription_cuts':
        draft=flow.pop('subscription_ai_draft',{})
        cut_data={} if value=='keep' else _data(value)
        data=dict(cut_data,targets=draft.get('targets',{}),effective_from=draft.get('effective_from',flow['today'][:7]))
        value=json.dumps(data,ensure_ascii=False)
        # Apply the old combined-answer contract only after all three screens.
        if 'current_subscriptions' not in p or not flow['questions'].get('reduce_subscriptions',{}).get('subscription_evidence'):
            # An owner may answer the pre-upgrade card without another next.
            # Refresh evidence before any implicit keep/end confirmations.
            fresh = kit.run('ledger-plan', '--today', flow['today'], *F.window_args(flow))
            for key in ('roster_months', 'current_subscriptions', 'subscription_oneoffs', 'display_name_missing', 'display_name_resolution'):
                p[key] = fresh[key]
        data={} if value=='keep' else _data(value) if value.lstrip().startswith(('{','[')) else None
        cycle_fact=(data or {}).get('facts',[])
        cycle_only=(data is not None and set(data)=={'facts'} and len(cycle_fact)==1
                    and set(cycle_fact[0])=={'key','cycle'} and cycle_fact[0].get('cycle')=='yearly'
                    and cycle_fact[0].get('key') in {r['key'] for r in flow['questions'].get('reduce_subscriptions',{}).get('oneoffs',[])})
        if data is None or cycle_only:
            # A stated annual cycle is a fact, not a budget confirmation. Both
            # name answers and typed relay facts use the issued payment anchor.
            result = kit.run('ledger-plan-answer', '--today', flow['today'], *F.window_args(flow),
                             '--data', json.dumps(dict(kind='subscription_annual_name',
                                 name=cycle_fact[0]['key'] if cycle_only else value,
                                 issued_ids=[r['id'] for r in q.get('oneoffs', [])], said=provenance), ensure_ascii=False))
            fresh = kit.run('ledger-plan', '--today', flow['today'], *F.window_args(flow))
            for field in ('roster_months', 'current_subscriptions', 'subscription_oneoffs', 'subscription_unknown', 'subscription_proved_oneoffs'):
                p[field] = fresh[field]
            fact_key = 'subscription:'+result['fact']['key']
            p['facts'][fact_key] = fresh['facts'][fact_key]
            flow['questions'][qid] = screen(flow, qid)
            return dict(annual_subscription=result['fact'])
        requested=data.get('targets',{})
        from ledger_plan import subscription_services
        available={s['key'] for s in subscription_services(p)}
        # Screen B itself is the issued identity for an owner-stated AI group
        # ceiling; sparse histories need not have resolved both vendor keys.
        if available & {'claude','codex'} or 'ai' in requested:available.add('ai')
        if 'ai' in requested and set(requested)&{'claude','codex'}:raise ValueError('choose a group cap or vendor caps, not both')
        if set(requested)-available or any(type(a) is not int or a<0 for a in requested.values()):
            raise ValueError('subscription targets need issued identities and exact nonnegative won')
        facts=data.get('facts',[])
        fact_keys=available | {r['key'] for r in flow['questions'].get('reduce_subscriptions',{}).get('oneoffs',[])}
        if len(facts)>3 or any(f.get('key') not in fact_keys for f in facts):
            raise ValueError('at most three issued subscription facts')
        keeping = value=='keep' or data.get('action')=='keep'
        shown = {s['key'] for s in p.get('current_subscriptions', p['subscriptions'])}
        if keeping:
            # Repair monthly status without revisiting annual reminder consent.
            active={s['key'] for s in p['subscriptions'] if s['key'] in shown and not s['cancelled'] and s.get('cycle')!='yearly'}
            for fact in facts:
                if fact['key'] in active: fact['cancelled']=False
            supplied={f['key'] for f in facts}
            facts += [dict(key=key,cancelled=False) for key in sorted(active-supplied)]
        changed=[f['key'] for f in facts if 'amount_change' in f]
        if len(set(changed))!=len(changed) or set(changed)&set(requested) or changed and 'ai' in requested:
            raise ValueError('choose one amount change or cap per subscription')
        if E.enabled():
            for fact in facts:
                old=next((sub for sub in p['subscriptions'] if sub['key']==fact.get('key')),None)
                if not old: raise ValueError('answer only issued subscriptions')
                if ('ends_on' not in fact and 'end_kind' not in fact and not old.get('end_asked')
                        and (not keeping or len(old.get('fixed_ids',[]))<=1)):
                    fact['end_kind']='unknown'
            supplied={f['key'] for f in facts}
            facts += [dict(key=sub['key'],end_kind='unknown') for sub in p['subscriptions'] if sub['key'] in shown and sub['key'] not in supplied and not sub.get('end_asked') and len(sub.get('fixed_ids',[]))<=1]
        kit.run('ledger-plan-answer','--data',json.dumps(dict(kind='batch',items=[dict(f,kind='subscription',said=provenance) for f in facts],said=provenance),ensure_ascii=False),'--today',flow['today'],*F.window_args(flow))
        _read(flow);p=flow['plan']
        for key in changed:
            sub=next(s for s in subscription_services(p) if s['key']==key)
            change=sub['amount_change'];amount=change['amount']
            requested[key]=amount
            previous=change.get('previous_amount',sub['monthly'])
            targets.setdefault('subscription_revisions',{})[key]=dict(previous=previous,amount=amount,
                **({'basis_month':change['basis_month']} if change.get('basis_month') else {}))
            targets.setdefault('subscription_effective_from',{})[key]=change['effective_from']
        services=subscription_services(p)
        targets['subscriptions']=sum(requested.get(s['key'],s.get('paid_monthly',0) if s['cancelled'] else s['monthly'])
                                     - (targets.get('subscription_revisions',{}).get(s['key'],{}).get('previous',s['monthly'])-s['monthly']
                                        if s['key'] in changed else 0) for s in services)
        if 'ai' in requested:
            targets['subscriptions'] += requested['ai']-sum(s.get('paid_monthly',0) if s['cancelled'] else s['monthly'] for s in services if s['key'] in ('claude','codex'))
        targets['subscription_group_targets']={k:v for k,v in requested.items() if k=='ai'}
        if _owner_ai_cap(flow) is not None:
            targets['ai']=_owner_ai_cap(flow)
        targets['subscription_targets']={k:v for k,v in requested.items() if k!='ai'}
        targets['subscriptions_from']=_month(data.get('effective_from',flow['today'][:7]))
    elif qid=='reduce_habits':
        names=('gentle','delivery_focus','strong')
        counts=q['proposals'][names.index(value)] if value in names else {} if value=='keep' else _data(value).get('counts',{})
        if not isinstance(counts,dict):raise ValueError('monthly counts required')
        for key,count in counts.items():
            m=p['metrics'].get(key)
            if not m or not m['unit_price'] or type(count) is not int or count<0:
                raise ValueError('choose a nonnegative monthly cap; meals are never a lever')
        targets['counts']=counts
        # Keep the loan portion for comparing further cuts, without asking
        # for an income goal. Final confirmation records the whole shortfall.
        targets['accepted_deficit']=min(p['loan'],_projection(flow)['remaining'])
        if _ledger(flow)['living_gap'] > 0:
            flow['revisit']='reduce_shopping'
    elif qid=='reduce_shopping':
        if value in ('shopping','spread'):
            targets.setdefault('counts',{}).update(q['proposals'][0 if value=='shopping' else 1])
            flow['revisit']='reduce_goal'
            flow['questions'].pop('reduce_goal',None)
        elif _data(value).get('action')=='subscriptions':
            flow['revisit']='reduce_tradeoff'
        else:raise ValueError('choose an issued shopping option or revisit subscriptions')
    elif qid=='reduce_tradeoff':
        # A relay's selected option is not an answer to an owner's unrelated
        # question. Preserve the existing read-only contract for typed choices.
        choices=q.get('choices',[])
        if not callback and not value.startswith('{') and choices:
            selected=next((n for n,c in enumerate(choices,1) if c['value']==value),None)
            label=next((c['label'] for c in choices if c['value']==value),'')
            plain=re.sub(r'\s*\([^()]*\)\s*$','',label)
            typed=(said or '').strip()
            ordinal=bool(selected and re.fullmatch(str(selected)+r'\s*(?:\ubc88)?',typed))
            named=bool(re.search(r'\b(?:issued|selected|choose|chose)\s+'+re.escape(value)+r'\b',typed,re.I))
            if typed not in (value,label,plain) and not ordinal and not named:
                return False
        if value.startswith('{'):
            data=_data(value)
            if data.get('action')!='next_bill' or data.get('key') not in {s['key'] for s in p['subscriptions']}:raise ValueError('choose an issued subscription')
            flow['billing_key']=data['key'];flow['billing_plan']=str(data.get('plan', ''));flow['revisit']='reduce_bill'
        elif value in ('ai','half','shopping'):
            targets.update(_tradeoff_targets(flow,value))
            flow['revisit']='reduce_goal';flow['questions'].pop('reduce_goal',None)
        else:raise ValueError('choose an issued tradeoff')
    elif qid=='reduce_bill':
        data=_data(value) if value.startswith('{') else {}
        if value not in ('confirm','no_shopping') and not data:raise ValueError('confirm the billing comparison')
        bill=q['billing'];amount=data.get('amount',bill['amount'])
        if type(amount) is not int or amount<0:raise ValueError('whole nonnegative subscription amount required')
        targets.update(_billing_targets(flow,bill,amount,data.get('effective_from')))
        if value!='no_shopping':targets.setdefault('counts',{}).update(q['counts'])
        flow['revisit']='reduce_goal';flow['questions'].pop('reduce_goal',None)
    elif qid=='reduce_goal':
        if value!='confirm':raise ValueError('confirm the issued goal card')
        flow.pop('revisit',None)
        for key in ('extra_income','extra_income_source'):
            targets.pop(key,None)
        targets['accepted_deficit']=_projection(flow)['remaining']
        # Reuse a nonce-anchored goal if a previous attempt wrote it before
        # its state save. Confirmation retries cannot duplicate the goal.
        goals=kit.run('goal-list','--domain',kit.GOAL_DOMAIN,'--json')
        existing=next((r for r in goals if (json.loads(r['detail']) if isinstance(r.get('detail'),str) else r.get('detail') or {}).get('plan_nonce')==flow['nonce']),None)
        gid=existing['id'] if existing else None
        gid=kit.goal_put(gid,goal_name(flow),None,'up',dict(plan_nonce=flow['nonce'],plan=p,targets=targets),provenance,new=gid is None)
        flow['goal_id']=gid
        kit.run('ledger-plan-commit','--data',json.dumps(dict(today=flow['today'],nonce=flow['nonce'],said=provenance,plan=p,targets=targets,goal_id=gid,shortfall_policy='show'),ensure_ascii=False))
    else:raise ValueError('unknown reduction screen')
    return True
