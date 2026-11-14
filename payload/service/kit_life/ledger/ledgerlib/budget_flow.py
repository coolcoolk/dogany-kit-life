"""Owner-approved seven-step budget interview, with explicit owner amounts only."""
import calendar
import copy
import datetime
import html
import json
import re

from . import text
from . import history_flow as F

STEPS = ('reduce_fixed', 'reduce_subscriptions', 'reduce_habits',
         'reduce_big_review', 'reduce_cap', 'reduce_goal', 'reduce_apply')
SCREENS = set(STEPS) - {'reduce_habits'} | {'reduce_revisit'}
GROUPS = ('ai', 'shopping', 'media', 'other')


def t(key, **kw):
    return text.t('ledger.onboarding.budget.' + key, **kw)


def money(amount):
    from . import reduction_flow as R
    return R._budget_money(amount)


def compact_money(amount, unit=True):
    from money_format import dining_man
    if text.lang() != 'ko':
        return F._won(amount)
    number = dining_man(amount)
    return t('short_money', amount=number) if unit else number


def quote(rows):
    return '<blockquote>' + '\n'.join(rows) + '</blockquote>'


def card(qid, lines, choices=(), **kw):
    from . import onboarding as O
    return O._interactive(O._q(qid, '\n'.join(lines),
        [dict(label=t(label), value=value) for label, value in choices],
        budget_flow_version=3,
        relay='Send the service text and buttons verbatim, alone. Do not add any sentence, example, amount, advice or question.', **kw))


def monthly(flow, targets=None):
    """Monthly decision arithmetic excludes current-month floors (step seven)."""
    from . import reduction_flow as R
    snapshot = copy.deepcopy(flow)
    snapshot['plan'].pop('month_progress', None)
    snapshot['plan'].pop('current_estimate', None)
    snapshot['plan'].pop('current_subscriptions', None)
    return R._projection(snapshot, targets)


def installments(plan):
    from ledger_installment import at
    month = plan['today'][:7]
    return (sum(at(it, month)['this_month'] for it in plan.get('installments', [])) +
            sum(it['amount'] for it in plan.get('installment_tracking', [])
                if it['active'] and (not it.get('end') or month <= it['end'])))


def normalize_plan(flow):
    """Carry step one's actual total into the saved category budgets too.

    Keep the six measured habit amounts, shopping and subscriptions intact.
    Reconcile fixed obligations first, then the unclassified living remainder.
    Otherwise step seven would revive a larger living budget than step six.
    Sparse histories may have a smaller cash total than known obligations.
    Retain classified amounts in that case and use their conservative total.
    """
    basis = flow.get('budget_actuals')
    if not basis:
        return
    p = flow['plan']; groups = p['groups']
    p['income'] = round(basis['income'])
    p['loan'], p['saving'] = basis['loan'], basis['saving']
    components = basis.get('fixed_components', [])
    if components:
        groups['fixed'] = sum(r['monthly'] for r in components if r['kind'] != 'subscription')
    total = p['income'] + round(basis['out'] - basis['income']) - p['loan'] - p['saving']
    delta = total - sum(groups.values())
    from ledger_plan import LEVERS
    remainder = [key for key in ('unknown', 'other', *groups)
                 if key in groups and key not in (*LEVERS, 'fixed', 'subscriptions', 'big')]
    remainder = list(dict.fromkeys(remainder))
    if delta >= 0:
        if remainder:
            groups[remainder[0]] += delta
    else:
        for key in remainder:
            taken = min(max(0, groups[key]), -delta)
            groups[key] -= taken
            delta += taken
            if not delta:
                break
        # Classified owner facts are a lower bound, even when a sparse cash
        # history cannot account for them. Do not invent negative living costs
        # or discard an obligation just to force the two sources to agree.
    p['spend'] = sum(groups.values())
    p['gap'] = p['income'] - p['spend'] - p['loan']
    classified_out = p['spend'] + p['loan'] + p['saving']
    if classified_out > p['income'] + round(basis['out'] - basis['income']):
        basis['classified_out'] = classified_out
    else:
        basis.pop('classified_out', None)
    p['budget_actuals'] = actuals(flow)


def actuals(flow):
    """Render the retained classified floor without rewriting measured history."""
    basis = copy.deepcopy(flow.get('budget_actuals') or flow.get('history', {}).get('money_flow', {}))
    total = basis.get('classified_out', basis.get('out', 0))
    if total > round(basis.get('income', 0)) + round(basis.get('out', 0) - basis.get('income', 0)):
        basis['variable'] = basis.get('variable', 0) + total - basis['out']
        basis['out'] = total
    return basis


def amounts(flow):
    """Return the one arithmetic basis used by every budget screen and goal.

    Installments are obligations, not discretionary big shopping.  Keep them
    in the fixed side exactly once and remove the same load from the measured
    big-shopping bucket.
    """
    p = flow['plan']; g = p['groups']; projected = monthly(flow)
    debt = p['loan']; saving = p.get('saving', 0); installment = installments(p)
    before = dict(fixed=g.get('fixed', 0) + installment + debt + saving,
                  subscriptions=g['subscriptions'], big=max(0, g['big'] - installment),
                  living=p['spend'] - g.get('fixed', 0) - g['big'] - g['subscriptions'])
    basis = actuals(flow)
    income = p['income']
    if basis:
        # Step one explains completed-month actuals, not a second forecast.
        # Keep that total (or its classified floor); categories partition it.
        # Only owner
        # edits change it, measured against an untouched plan projection.
        income = round(basis['income'])
        components = basis.get('fixed_components', [])
        if components:
            before['fixed'] = (sum(r['monthly'] for r in components if r['kind'] != 'subscription') +
                               installment +
                               basis['loan'] + basis['saving'])
        total = income + round(basis['out'] - basis['income'])
        before['living'] = total - sum(before[k] for k in ('fixed', 'subscriptions', 'big'))
    baseline = monthly(flow, {})
    after = dict(fixed=before['fixed'],
                 subscriptions=before['subscriptions'] + projected['subscriptions'] - baseline['subscriptions'],
                 big=before['big'] + projected['big'] - baseline['big'],
                 living=before['living'] + projected['living'] - baseline['living'])
    return dict(before=before, after=after, income=income,
                shortage_before=max(0, sum(before.values()) - income),
                shortage_after=max(0, sum(after.values()) - income))


def services(flow):
    # The baseline is the measured plan, never archived owner budgets or the
    # open-month roster's extrapolated amounts.
    return flow['plan'].get('subscriptions', [])


def purpose(sub, flow):
    from ledger_plan import is_ai_subscription
    override = flow.get('subscription_purposes', {}).get(sub['key'])
    if override:
        return override
    if is_ai_subscription(sub):
        return 'ai'
    name = sub['name'].casefold()
    for group in ('ai', 'shopping', 'media'):
        if re.search(t('merchant_' + group), name, re.I):
            return group
    return 'other'


def subscription_groups(flow):
    grouped = {}
    for sub in services(flow):
        grouped.setdefault(purpose(sub, flow), []).append(sub)
    return [(key, grouped[key]) for key in GROUPS if key in grouped]


def shopping_months(flow):
    from . import reduction_flow as R
    p = flow['plan']
    excluded = {r['id'] for r in R._installment_items(p)}
    excluded.update(sid for it in p.get('installments', [])
                    for sid in it.get('observed_ids', [it.get('spend_id')]))
    excluded.update(sid for it in p.get('installment_tracking', []) for sid in it['ids'])
    rows = [r for r in p.get('big', []) if r['id'] not in excluded
            and r['date'][:7] in p['months'] and r['amount'] > 100000
            and not r.get('recorded_installment')]
    return [(month, sorted([r for r in rows if r['date'][:7] == month], key=lambda r:(r['date'], r['id'])))
            for month in p['months'] if any(r['date'][:7] == month for r in rows)]


def advance(flow, qid):
    flow['questions'].pop(qid, None)
    if qid == 'reduce_big_review' and flow.get('budget_return') == 'reduce_cap':
        flow['revisit'] = 'reduce_cap'
    elif flow.get('budget_return') == qid:
        flow.pop('budget_return')
        flow['revisit'] = 'reduce_goal'
        flow['questions'].pop('reduce_goal', None)
    elif flow.get('revisit') == qid:
        flow.pop('revisit')


def next_step(flow):
    answers = flow['answers']
    if flow.get('revisit'):
        return flow['revisit']
    for qid in STEPS:
        if qid in answers:
            continue
        if qid == 'reduce_subscriptions' and not subscription_groups(flow):
            flow.setdefault('targets', {})['subscriptions'] = flow['plan']['groups']['subscriptions']
            answers[qid] = dict(value='keep')
            continue
        if qid == 'reduce_big_review' and not shopping_months(flow):
            flow['needed_shopping'] = {}
            answers[qid] = dict(value='none')
            continue
        return qid
    return None


def fixed_rows(flow):
    p = flow['plan']; g = p['groups']
    components = (flow.get('history', {}).get('money_flow') or {}).get('fixed_components', [])
    fixed = [dict(name=r.get('display_name') or r['name'], amount=r['monthly'])
             for r in components if r['kind'] != 'subscription' and r['monthly'] > 0]
    if not components:
        # Itemize the same measured fixed bucket as the summary. Periodic
        # contracts contribute their monthly share, other rows their average.
        fixed = []
        periodic_ids = set()
        for cost in p.get('periodic_costs', []):
            if cost['bucket'] == 'fixed':
                row = cost['rows'][-1]
                fixed.append(dict(name=row.get('display_name') or row['name'], amount=cost['monthly']))
                periodic_ids.update(r['id'] for r in cost['rows'])
        from ledger_profile import merchant_key
        grouped = {}
        for row in p.get('classifications', []):
            if row['bucket'] == 'fixed' and row['id'] not in periodic_ids:
                name = row.get('display_name') or row['name']
                grouped.setdefault(merchant_key(name), []).append(row)
        for rows in grouped.values():
            latest = max(rows, key=lambda r: (r.get('date', ''), r['id']))
            name = latest.get('display_name') or latest['name']
            amount = round(sum(r['amount'] for r in rows)/3)
            if amount:
                fixed.append(dict(name=name, amount=amount))
    its = [dict(name=it.get('display_name') or it['name'], amount=it['this_month'], end=it['end'], day=it.get('charge_day'))
           for it in p.get('installments', []) if it['this_month']]
    its += [dict(name=it.get('display_name') or it['name'], amount=it['amount'], end=it.get('end'),
                 day=int(it['last'][8:]) if it.get('last') else None)
            for it in p.get('installment_tracking', []) if it['active']]
    debts = [dict(name=r.get('display_name') or r['name'], amount=r.get('monthly', r.get('monthly_payment', 0)))
             for r in p.get('loans', [])]
    loan = (flow.get('budget_actuals') or flow.get('history', {}).get('money_flow') or {}).get('loan', p['loan'])
    if sum(r['amount'] for r in debts) != loan:
        debts = [dict(name=t('loan_count', n=len(p.get('loans', []))) if p.get('loans') else t('loan'), amount=loan)]
    fixed = [r for r in fixed if r['amount'] > 0 and money(r['amount']) != money(0)]
    return [('fixed', fixed), ('installment', its), ('loan', debts),
            ('saving', [dict(name=t('saving'), amount=p['saving'])] if p.get('saving') else [])]


def installment_end(row):
    if not row.get('end'):
        return ''
    year, month = map(int, row['end'][:7].split('-'))
    last = calendar.monthrange(year, month)[1]
    return t('installment_end', month=month, day=min(row.get('day') or last, last))


def screen(flow, qid):
    p = flow['plan']; g = p['groups']
    if qid == 'reduce_fixed':
        lines = [t('fixed_title')]
        from . import reduction_flow as R
        for key, rows in fixed_rows(flow):
            if not rows:
                continue
            if key == 'installment':
                rendered = [R.t('cap_active_row' if r.get('day') else 'cap_active_row_no_day',
                    name=html.escape(R._cap_installment_name(r)), amount=money(r['amount']), day=r.get('day'),
                    end=R.t('cap_last_month', month=R._cap_month(flow, r['end']))
                        if r.get('end') else R.t('cap_end_unknown')) for r in rows]
            else:
                rendered = [t('fixed_row', name=html.escape(r['name']), amount=money(r['amount']),
                              end=installment_end(r)) for r in rows]
            if key == 'fixed':
                for cost in p.get('periodic_costs', []):
                    if cost['bucket'] == 'fixed':
                        row = max(cost['rows'], key=lambda r: (r['date'], r['id']))
                        rendered.append(R.t('bill_next', name=html.escape(row.get('display_name') or row['name']),
                                            dates=cost['next_date']))
            lines += [t('group_heading', name=t(key)), quote(rendered)]
        lines.append(t('fixed_total', amount=money(amounts(flow)['before']['fixed'])))
        return card(qid, lines, [('correct', 'keep'), ('correction', 'custom')], free=True,
                    answer_format={'facts': [{'kind': 'item', 'id': 1, 'bucket': 'fixed'}]})
    if qid == 'reduce_subscriptions':
        groups = subscription_groups(flow)
        index = min(flow.get('subscription_group_index', 0), len(groups)-1)
        group, subs = groups[index]
        rows = []
        for n, s in enumerate(subs, 1):
            annual = t('annual', amount=F._won(s['yearly_amount'])) if s.get('yearly_amount') else ''
            rows.append(t('subscription_row', n=n, name=html.escape(s.get('display_name') or s['name']),
                          amount=F._won(s['monthly']) if s['monthly'] < 10000 else money(s['monthly']), annual=annual))
        return card(qid, [t('subscription_title', n=index+1, total=len(groups), group=t('purpose_'+group),
                            amount=money(sum(s['monthly'] for s in subs))), quote(rows), t('cancel_ask')],
                    [('keep_all', 'keep')], free=True, items=copy.deepcopy(subs), purpose=group,
                    answer_format={'cancel': [1], 'move': {'number': 2, 'group': 'other'}})
    if qid == 'reduce_big_review':
        months = shopping_months(flow)
        index = min(flow.get('shopping_month_index', 0), len(months)-1)
        month, rows = months[index]
        return card(qid, [t('shopping_title', n=index+1, total=len(months), month=int(month[5:]),
                            amount=money(sum(r['amount'] for r in rows))), quote([
                t('shopping_row', n=n, name=html.escape(r.get('display_name') or r['name']),
                  month=int(r['date'][5:7]), day=int(r['date'][8:]), amount=money(r['amount']))
                for n, r in enumerate(rows, 1)]), t('needed_ask')],
                    [('needed_all', 'all'), ('needed_none', 'none')], free=True,
                    items=copy.deepcopy(rows), month=month)
    if qid == 'reduce_cap':
        needed = flow.get('needed_shopping', {})
        average = round(sum(needed.values()) / 3)
        cap = flow.get('targets', {}).get('big', average)
        lines = [t('cap_title'), t('needed_summary', rows=t('join').join(
            t('needed_month', month=int(month[5:]), amount=money(needed.get(month, 0))) for month in p['months'])),
            t('cap_average', amount=money(average))]
        if cap != average:
            lines.append(t('cap_changed', before=money(average), after=money(cap)))
        return card(qid, lines, [('budget_keep', 'keep'), ('budget_edit', 'custom')], free=True,
                    big_budget=cap, original_big_budget=average)
    if qid == 'reduce_goal':
        a = amounts(flow)
        edits = [t('habit_change', name=text.t('ledger.onboarding.reduce.habit_label_'+key),
                   before=compact_money(g[key], unit=False), after=compact_money(value))
                 for key, value in flow.get('targets', {}).get('habit_budgets', {}).items() if value != g[key]]
        rows = [t('summary_fixed', amount=money(a['after']['fixed']))]
        for area in ('subscriptions', 'living', 'big'):
            rows.append(t('summary_row', name=t(area), before=money(a['before'][area]),
                          after=money(a['after'][area]), edits=t('edits', rows=t('join').join(edits)) if area == 'living' and edits else ''))
        return card(qid, [t('summary_title'), t('income', amount=money(a['income'])), t('outgoing'), quote(rows),
                          t('shortage_change', before=money(a['shortage_before']), after=money(a['shortage_after'])),
                          t('remaining_shortage', amount=money(a['shortage_after']))],
                    [('confirm', 'confirm'), ('revise', 'revise')], arithmetic=a)
    if qid == 'reduce_revisit':
        return card(qid, [t('revisit_ask')], [('subscriptions', 'subscriptions'), ('living', 'living'), ('big', 'big')])
    if qid == 'reduce_apply':
        from ledger_plan import LEVERS
        from . import reduction_flow as R
        # Keep ac7dbb94: budget - actual, signed, with no proration or carry.
        progress = R._projection(flow)['progress']['buckets']
        today = datetime.date.fromisoformat(flow['today'])
        days = calendar.monthrange(today.year, today.month)[1] - today.day + 1
        lines = [t('apply_title', month=today.month, day=today.day, days=days), t('apply_intro')]
        rows = []
        for key in LEVERS:
            row = progress[key]
            rows.append(apply_row(text.t('ledger.onboarding.reduce.habit_label_'+key), row['budget'], row['spent']))
        others = [r for key, r in progress.items() if key not in (*LEVERS, 'fixed', 'subscriptions', 'big', 'loans')]
        rows.append(apply_row(t('other_living'), sum(r['budget'] for r in others), sum(r['spent'] for r in others)))
        row = progress['big']; load = installments(p)
        # Shopping cap excludes the separate installment obligation in step 2.
        installment_spent = sum(p.get('month_progress', {}).get('installment_spent', {}).values())
        rows.append(apply_row(t('big'), max(0, row['budget']-load), max(0, row['spent']-installment_spent)))
        lines.append(quote(rows))
        lines.append(t('next_month', month=today.month % 12 + 1))
        return card(qid, lines, [('start', 'start')])
    raise ValueError('unknown budget step')


def apply_row(name, budget, spent):
    if spent == 0:
        return t('apply_unspent', name=name, budget=compact_money(budget))
    remaining = budget - spent
    return t('apply_row', name=name, budget=compact_money(budget), spent=compact_money(spent),
             remaining=t('over_budget' if remaining < 0 else 'remaining', amount=compact_money(abs(remaining))))


def numbers(value, total):
    data = json.loads(value) if value.lstrip().startswith('[') else [int(s) for s in re.split(r'[,\s]+', value.strip()) if s]
    if not isinstance(data, list) or any(type(n) is not int or not 1 <= n <= total for n in data) or len(set(data)) != len(data):
        raise ValueError('choose only issued numbers once')
    return data


def answer(flow, qid, value, said):
    """Return None only for the existing commit or amount-edit handlers."""
    q = flow['questions'][qid]; targets = flow.setdefault('targets', {})
    if qid == 'reduce_fixed':
        if value == 'custom':
            return False
        if value != 'keep':
            # Corrections are facts, not reductions. Re-read the plan after
            # the same public owner-fact verb used by the existing intake.
            from . import reduction_flow as R
            data = json.loads(value)
            facts = data.get('facts')
            if not isinstance(facts, list) or not facts:
                raise ValueError('record corrected ledger facts before confirming')
            R.kit.run('ledger-plan-answer', '--data', json.dumps(dict(kind='batch', items=facts, said=said), ensure_ascii=False))
            R._read(flow)
            if flow.get('history'):
                flow['history'] = F._history(flow)
                flow['budget_actuals'] = copy.deepcopy(flow['history']['money_flow'])
                normalize_plan(flow)
            flow['questions'][qid] = screen(flow, qid)
            return False
        advance(flow, qid)
        return True
    if qid == 'reduce_subscriptions':
        subs = q['items']
        data = json.loads(value) if value.lstrip().startswith('{') else {}
        if data.get('move'):
            move = data['move']; n = move.get('number'); group = move.get('group')
            if type(n) is not int or not 1 <= n <= len(subs) or group not in GROUPS:
                raise ValueError('move an issued service to a purpose group')
            flow.setdefault('subscription_purposes', {})[subs[n-1]['key']] = group
            groups = [key for key, _ in subscription_groups(flow)]
            flow['subscription_group_index'] = min(flow.get('subscription_group_index', 0), groups.index(group))
            flow['questions'].pop(qid, None)
            return False
        # Natural language corrections use locale templates; names are exact
        # issued merchant labels, so unrelated text never changes a group.
        for n, sub in enumerate(subs, 1):
            for group in GROUPS:
                if value.strip() == t('move_words', name=sub.get('display_name') or sub['name'], group=t('purpose_'+group)):
                    return answer(flow, qid, json.dumps({'move': {'number': n, 'group': group}}), said)
        selected = [] if value == 'keep' else data.get('cancel') if 'cancel' in data else numbers(value, len(subs))
        selected = numbers(json.dumps(selected), len(subs))
        requested = targets.setdefault('subscription_targets', {})
        for n, sub in enumerate(subs, 1):
            requested[sub['key']] = 0 if n in selected else sub['monthly']
        # Keep unrepresented historical shares. A sparse service list cannot
        # silently remove another subscription's measured cost.
        targets['subscriptions'] = max(0, flow['plan']['groups']['subscriptions'] - sum(
            s['monthly'] - requested.get(s['key'], s['monthly']) for s in services(flow)))
        targets['subscriptions_from'] = flow['today'][:7]
        index = flow.get('subscription_group_index', 0) + 1
        if index < len(subscription_groups(flow)):
            flow['subscription_group_index'] = index
            flow['questions'].pop(qid, None)
            return False
        flow.pop('subscription_group_index', None)
        advance(flow, qid)
        return True
    if qid == 'reduce_big_review':
        rows = q['items']
        selected = list(range(1, len(rows)+1)) if value == 'all' else [] if value == 'none' else numbers(value, len(rows))
        flow.setdefault('needed_shopping', {})[q['month']] = sum(rows[n-1]['amount'] for n in selected)
        index = flow.get('shopping_month_index', 0) + 1
        if index < len(shopping_months(flow)):
            flow['shopping_month_index'] = index
            flow['questions'].pop(qid, None)
            return False
        flow.pop('shopping_month_index', None)
        targets['big'] = round(sum(flow['needed_shopping'].values()) / 3)
        advance(flow, qid)
        return True
    if qid == 'reduce_cap' and value == 'keep':
        targets['big'] = q['big_budget']
        advance(flow, qid)
        return True
    if qid == 'reduce_goal' and value == 'revise':
        flow['revisit'] = 'reduce_revisit'
        return False
    if qid == 'reduce_revisit':
        step = dict(subscriptions='reduce_subscriptions', living='reduce_habits', big='reduce_big_review').get(value)
        if not step:
            raise ValueError('choose an issued budget area')
        if step == 'reduce_big_review' and not shopping_months(flow):
            step = 'reduce_cap'
        flow['budget_return'] = 'reduce_cap' if step == 'reduce_big_review' else step
        flow['revisit'] = step
        if step == 'reduce_big_review':
            flow.pop('shopping_month_index', None)
            flow['answers'].pop('reduce_cap', None)
        if step == 'reduce_subscriptions':
            flow.pop('subscription_group_index', None)
        flow['questions'].pop(step, None)
        return True
    if qid == 'reduce_apply':
        if value != 'start':
            raise ValueError('confirm starting this month')
        advance(flow, qid)
        return True
    return None
