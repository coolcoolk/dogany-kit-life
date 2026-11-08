"""Owner-confirmed deposit suggestions; classification still uses kit review answers."""
import datetime
import hashlib
import html
import json

from . import dashboard, diagtext, paths, text, kit

PREFIX = 'review_deposit_group_'


def owner_names():
    """Read the explicit Name field, including translated names in parentheses.

    Never infer the owner's identity from the most frequent bank sender.
    """
    from ledger_deposits import owner_names as read_names
    return read_names(paths.root())


def ids(flow, deposits, *, include_person=True):
    names = owner_names()
    single = set(flow.get('deposit_singles', []))
    groups = {}
    kinds = {}
    for row in deposits:
        qid = 'review_deposit_' + str(row['id'])
        sender = row.get('counterparty') or ''
        kind = ('own_transfer' if sender and sender in names else
                'insurance' if sender and row.get('insurer') else
                'split' if include_person and sender and row.get('split_payment') else
                'person' if include_person and sender and row.get('person') else None)
        if kind and qid not in flow['answers'] and row['id'] not in single and flow.get('deposit_typing') != qid:
            key = row['split_payment']['id'] if kind == 'split' else sender if kind == 'person' else ''
            kinds.setdefault((kind, key), []).append(row)
    for (kind, sender), rows in kinds.items():
        # Month readers can contribute receipts on both sides of a month
        # boundary. Recheck their combined capacity before issuing one card.
        if kind == 'split' and sum(r['amount'] for r in rows) > min(
                r.get('split_capacity', r['split_payment']['amount']) for r in rows):
            continue
        if len(rows) > 1 or kind == 'split':
            if kind == 'person':
                rows = sorted(rows, key=lambda r: (r['date'], r.get('at_time') or '', r['id']))
                numbers = flow.setdefault('deposit_numbers', {}).setdefault(sender, {})
                for row in rows:
                    numbers.setdefault(str(row['id']), max(numbers.values(), default=0) + 1)
            digest = hashlib.sha256(','.join(str(r['id']) for r in rows).encode()).hexdigest()[:12]
            groups[PREFIX + kind + '_' + digest] = dict(kind=kind, sender=sender, items=rows)
    flow['deposit_groups'] = groups
    grouped = {r['id']: qid for qid, group in groups.items() for r in group['items']}
    return list(dict.fromkeys(grouped.get(r['id'], 'review_deposit_' + str(r['id'])) for r in deposits))


def _insurer_display(sender, names):
    """Trim the account holder from the display, preserving the bank memo."""
    for name in sorted(names, key=len, reverse=True):
        if sender.startswith(name) and sender[len(name):].strip():
            sender = sender[len(name):].strip()
        if sender.endswith(name) and sender[:-len(name)].strip():
            sender = sender[:-len(name)].strip()
    return sender


def choices(*, single=False, multi=True):
    """Deposit answer buttons. Typing is always accepted (the ask ends with the
    hint line), so there is no type button; one-by-one only for 2+ deposits."""
    keys = ('received', 'income', 'own_transfer') + (('single',) if multi and not single else ())
    return [dict(label=text.t('ledger.unmatched_deposit.approved.' + key),
                 value='one_by_one' if key == 'single' else key) for key in keys]


def hint():
    return text.t('ledger.unmatched_deposit.hint')


def question(qid, flow):
    from . import onboarding as O
    group = flow['deposit_groups'][qid]
    kind, rows = group['kind'], group['items']
    if kind == 'split':
        payment = rows[0]['split_payment']
        address = paths.agent_conf_value('AGENT_ADDRESS') or text.t('ledger.owner.address')
        category = payment.get('category') or ''
        purpose = category or text.t('ledger.unmatched_deposit.group.payment_fallback',
                                     merchant=html.escape(payment['name']))
        lead = text.t('ledger.unmatched_deposit.group.split.payment',
                      date=dashboard.question_date(payment['date'], flow['today']),
                      merchant=html.escape(payment['name']), amount=diagtext.money(payment['amount']),
                      n=payment['people'], unit=diagtext.money(payment['unit']))
        receipt_lines = []
        for row in rows:
            shares = row['split_payment']['shares']
            receipt_lines.append('• ' + text.t('ledger.unmatched_deposit.group.split.receipt',
                date=dashboard.question_date(row['date'], flow['today']),
                name=html.escape(row['counterparty']), amount=diagtext.money(row['amount']),
                shares=text.t('ledger.unmatched_deposit.group.split.shares', k=shares) if shares > 1 else ''))
        purpose_josa = text.josa(purpose, 'ro')
        ask = lead + '\n<blockquote>' + '\n'.join(receipt_lines) + '</blockquote>\n\n' + text.t(
            'ledger.unmatched_deposit.group.split.question', address=html.escape(address), purpose=html.escape(purpose), purpose_josa=html.escape(purpose_josa),
            unit=diagtext.money(payment['unit']))
        typing = flow.get('deposit_group_typing') == qid
        if not typing:
            ask += '\n' + hint()
        buttons = [] if typing else choices(multi=len(rows) > 1)
        return O._interactive(O._q(qid, ask, buttons, items=rows, free=True,
                                   deposit_kind=kind, deposit_today=flow['today']))
    if kind == 'person':
        numbers = flow['deposit_numbers'][group['sender']]
        items = [dict(r, number=numbers[str(r['id'])]) for r in rows]
        lines = [text.t('ledger.unmatched_deposit.group.person', name=html.escape(group['sender']), n=len(rows))]
        for row in items:
            day = datetime.date.fromisoformat(row['date'])
            stamp = '%d.%d.%d(%s)' % (day.year, day.month, day.day, text.t('ledger.dow.short.' + O.DOW[day.weekday()]))
            if row.get('at_time'):
                stamp += ' ' + row['at_time'][:5]
            lines.append('%d. %s  %s → %s' % (row['number'], stamp, diagtext.money(row['amount']),
                         html.escape(dashboard.roster_display(row.get('method') or ''))))
        lines.append(text.t('ledger.unmatched_deposit.group.applies', n=len(rows)))
        lines.append(text.t('ledger.unmatched_deposit.group.example', first=text.josa(str(items[0]['number']), 'eun'),
                            last=text.josa(str(items[-1]['number']), 'eun')))
        typing = flow.get('deposit_group_typing') == qid
        if not typing:
            lines.append(hint())
        buttons = [] if typing else choices()
        return O._interactive(O._q(qid, '\n'.join(lines), buttons, items=items, free=True,
                                   sender_answer_format={'items': [{'number': items[0]['number'],
                                       'answer': 'received|income|own_transfer|custom', 'said': 'owner words'}]},
                                   deposit_kind=kind, deposit_today=flow['today']))
    key = 'ledger.unmatched_deposit.group.'
    lines = [text.t(key + kind, n=len(rows))]
    names = owner_names() if kind == 'insurance' else set()
    for row in rows:
        detail = ('→ ' + dashboard.roster_display(row.get('method') or '') if kind == 'own_transfer'
                  else _insurer_display(row.get('counterparty') or '', names))
        lines.append('• %s %s %s' % (dashboard.question_date(row['date'], flow['today']),
                                     diagtext.money(row['amount']), html.escape(detail)))
    buttons = [dict(label=text.t('ledger.unmatched_deposit.own_transfer') if kind == 'own_transfer'
                               else text.t(key + 'insurance_label'), value=kind),
               dict(label=text.t(key + 'single'), value='one_by_one')]
    q = O._q(qid, '\n'.join(lines), buttons, items=rows, deposit_kind=kind, deposit_today=flow['today'])
    return O._interactive(q, [buttons])


def answer(q, value, flow, said):
    if value == 'one_by_one':
        flow.pop('deposit_group_typing', None)
        flow['deposit_singles'] = list(dict.fromkeys(flow.get('deposit_singles', []) + [r['id'] for r in q['items']]))
        return dict(ok=True, split=True)
    if q['deposit_kind'] in ('person', 'split') and value == 'type':
        flow['deposit_group_typing'] = q['id']
        return dict(ok=True, pending_input=True)
    allowed = ('income', 'received', 'own_transfer', 'custom') if q['deposit_kind'] in ('person', 'split') else (q['deposit_kind'],)
    if (q['deposit_kind'] in ('person', 'split') and value.strip() and not value.startswith('{')
            and (flow.get('deposit_group_typing') == q['id'] or value not in allowed)):
        value, said = 'custom', said or value
    if q['deposit_kind'] == 'person' and isinstance(value, str) and value.startswith('{'):
        parsed = json.loads(value)
        selections = parsed.get('items') if isinstance(parsed, dict) else None
        issued = {r['number']: r for r in q['items']}
        if (not isinstance(selections, list) or not selections
                or any(not isinstance(v, dict) or type(v.get('number')) is not int
                       or v['number'] not in issued or v.get('answer') not in allowed for v in selections)
                or len({v['number'] for v in selections}) != len(selections)):
            raise ValueError('choose issued sender numbers once with their deposit answers')
        rows = [issued[v['number']] for v in selections]
        items = [dict(id=r['id'], month=r.get('month') or r['date'][:7],
                      answer=v['answer'], said=v.get('said', said or '')) for r, v in zip(rows, selections)]
    else:
        if value not in allowed:
            raise ValueError('choose an issued deposit group answer')
        rows = q['items']
        items = [dict(id=r['id'], month=r.get('month') or r['date'][:7], answer=value, said=said or '') for r in rows]
    if q['deposit_kind'] == 'split':
        for row, item in zip(rows, items):
            item.update(spend_id=row['split_payment']['id'], payment=row['split_payment'],
                        receipt={key: row[key] for key in ('id', 'date', 'name', 'amount', 'method')})
    result = kit.run('ledger-review-answer', '--month', items[0]['month'], '--data',
                     json.dumps(dict(kind='deposit_group', items=items)))
    flow.pop('deposit_group_typing', None)
    for row, item, rec in zip(rows, items, result['results']):
        flow['answers']['review_deposit_' + str(row['id'])] = dict(ok=True, result=rec, value=item['answer'], said=item['said'])
    return dict(ok=True, result=result)
