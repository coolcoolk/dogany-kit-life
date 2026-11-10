"""Prefill reads kit-owned facts and successful, scoped owner answers only."""
import copy
import datetime
import json

SUBSCRIPTION_QUESTIONS = ('reduce_subscriptions', 'reduce_subscription_ai')


def owner_answer(qid, value, said, callback, issued):
    record = dict(question=qid, value=value, said=said, callback=callback)
    if qid == 'reduce_debts' or qid in SUBSCRIPTION_QUESTIONS:
        record['kit_recorded'] = True
        record['issued_items'] = copy.deepcopy((issued or {}).get('items', []))
        known = (issued or {}).get('known') or {}
        if value == 'known' and known.get('kit_source'):
            record['value'] = json.dumps({'items': [{k: v for k, v in known.items()
                if k not in ('said', 'kit_source')}]}, ensure_ascii=False)
            record['said'] = known['said']
    return record


def _flows(state, flow):
    yield flow
    for entry in reversed(state.get('legacy_flows', [])):
        if isinstance(entry, dict):
            saved = entry.get('flow', entry)
            if isinstance(saved, dict):
                yield saved
    if isinstance(state.get('legacy_flow'), dict):
        yield state['legacy_flow']


def _identity(debt):
    return tuple(debt.get(key) for key in ('name', 'principal', 'method', 'start_date'))


def _debt_value(fact, loan, source):
    if type(fact.get('amount')) is not int or fact['amount'] < 0 or not fact.get('said'):
        return None
    try:
        as_of = datetime.date.fromisoformat(fact['as_of']).isoformat()
        ends = fact['ends_on'][:7]
        datetime.date.fromisoformat(ends + '-01')
    except (KeyError, TypeError, ValueError):
        return None
    value = {k: fact[k] for k in ('annual_rate', 'payment_day') if k in fact}
    return dict(value, id=loan['id'], amount=fact['amount'], as_of=as_of,
                ends_on=ends, said=fact['said'], kit_source=source)


def debt(state, flow, loan):
    # Query current kit facts, rather than accepting values in the prefill verb.
    from . import kit
    plan = kit.run('ledger-plan', '--today', flow['today'])
    fact = dict(plan['facts'].get('debt:' + str(loan['id']), {}))
    current = next((row for row in plan.get('loans', []) if row['id'] == loan['id']), {})
    # End dates live in the kit's linked obligation record, not the balance fact.
    # An estimate or imported screen date is not a prior owner statement.
    if current.get('end_asked') and not current.get('end_provenance') and current.get('ends_on'):
        fact['ends_on'] = current['ends_on']
    value = _debt_value(fact, loan, dict(kind='ledger_plan_fact',
        key='debt:' + str(loan['id']), fixed_id=fact.get('fixed_id')))
    if value:
        return value
    for saved in _flows(state, flow):
        for index, record in reversed(list(enumerate(saved.get('owner_answers', [])))):
            if record.get('question') != 'reduce_debts':
                continue
            try:
                items = json.loads(record['value']).get('items', [])
            except (TypeError, ValueError, AttributeError):
                continue
            issued = record.get('issued_items') or []
            for fact in items:
                # A later saved card cannot bind an older number-only answer.
                # Legacy records are reusable only when they carry an explicit ID.
                candidates = issued or saved.get('history', {}).get('debts', [])
                bound = next((row for row in candidates if
                    type(fact.get('id')) is int and row.get('id') == fact['id'] or
                    issued and type(fact.get('number')) is int and row.get('number') == fact['number']), None)
                if not bound or _identity(bound) != _identity(loan):
                    continue
                value = _debt_value(dict(fact, said=fact.get('said') or record.get('said')), loan,
                    dict(kind='owner_answer', nonce=saved.get('nonce'), index=index,
                         question='reduce_debts', loan_id=bound['id']))
                if value:
                    return value
    return None


def _annual_facts(flow):
    plan = flow.get('plan', {})
    services = {r['key']: r for r in plan.get('subscriptions', []) if r.get('key')}
    facts = [(key, fact) for key, fact in plan.get('facts', {}).items()
             if key.startswith('subscription:') and fact.get('cycle') == 'yearly'
             and not fact.get('cancelled') and fact.get('said') and key[13:] in services]
    if facts:
        annual = [dict(key=key[13:], cycle='yearly') for key, _ in facts]
        return dict(value=json.dumps({'facts': annual}, ensure_ascii=False),
            display='\n'.join(services[f['key']].get('display_name', services[f['key']].get('name', f['key']))
                              for f in annual),
            said=' / '.join(fact['said'] for _, fact in facts),
            kit_source=dict(kind='ledger_plan_fact', keys=[key for key, _ in facts]))
    return None


def subscription(state, flow, qid):
    if qid not in SUBSCRIPTION_QUESTIONS:
        raise ValueError('known qid is reduce_subscriptions or reduce_subscription_ai')
    if qid == 'reduce_subscriptions':
        value = _annual_facts(flow)
        if value:
            return value
    for saved in _flows(state, flow):
        cap = saved.get('owner_ai_cap', {})
        if qid == 'reduce_subscription_ai' and type(cap.get('amount_krw')) is int and cap['amount_krw'] >= 0 and cap.get('said'):
            from ledger_plan import subscription_aliases
            aliases=subscription_aliases(saved.get('plan',{}))
            cap=copy.deepcopy(cap)
            if isinstance(cap.get('services'),dict):
                cap['services']={aliases.get(k,k):v for k,v in cap['services'].items()}
            return dict(value=str(cap['amount_krw']), said=cap['said'], owner_ai_cap=cap,
                        kit_source=dict(kind='owner_ai_cap', nonce=saved.get('nonce')))
        for index, record in reversed(list(enumerate(saved.get('owner_answers', [])))):
            value = record.get('value')
            if (record.get('question') == qid and record.get('said')
                    and isinstance(value, str) and value.strip() not in ('', 'keep', 'skip')):
                display = value
                if value.lstrip().startswith('{'):
                    try:
                        data = json.loads(value)
                    except ValueError:
                        continue
                    if qid == 'reduce_subscription_ai':
                        amount = data.get('targets', {}).get('ai')
                        if type(amount) is not int or amount < 0:
                            continue
                        value = display = str(amount)
                    else:
                        facts = data.get('facts', [])
                        if not facts or any(f.get('cycle') != 'yearly' or not f.get('key') for f in facts):
                            continue
                        services = [r for key in ('subscriptions', 'current_subscriptions', 'subscription_oneoffs')
                                    for r in flow.get('plan', {}).get(key, [])]
                        names = {r['key']: r.get('display_name', r.get('name', r['key']))
                                 for r in services if r.get('key')}
                        if any(f['key'] not in names for f in facts):
                            continue
                        display = '\n'.join(names[f['key']] for f in facts)
                        # Reuse only A's annual answer from an old combined payload.
                        value = json.dumps({'facts': [dict(key=f['key'], cycle='yearly')
                                                     for f in facts]}, ensure_ascii=False)
                elif qid == 'reduce_subscription_ai':
                    try:
                        amount = int(value.replace(',', '').replace('원', '').strip())
                    except ValueError:
                        # USD restoration requires its recorded conversion above.
                        continue
                    if amount < 0:
                        continue
                    value = display = str(amount)
                return dict(value=value, display=display, said=record['said'], kit_source=dict(
                    kind='owner_answer', nonce=saved.get('nonce'), index=index, question=qid))
    return None
