"""Owner-confirmed deposit suggestions; classification still uses kit review answers."""
import datetime
import hashlib
import html
import json

from . import dashboard, diagtext, paths, text, kit

PREFIX = 'review_deposit_group_'


def _sessions(rows):
    groups = {}
    for row in rows:
        if row.get('split_payment'):
            groups.setdefault(row['split_payment']['id'], []).append(row)
    return sorted(groups.values(), key=lambda batch: (batch[0]['split_payment']['date'], batch[0]['split_payment']['id']))


def _memory_key(rows):
    # Exact group identity and share multiplicities, never a date/amount guess.
    from ledger_profile import merchant_key
    payment = rows[0]['split_payment']
    signature = [merchant_key(payment['name']), payment['method'], payment['people'],
                 sorted((r['name'], r['method'], r['split_payment']['shares']) for r in rows)]
    return 'settlement:' + hashlib.sha256(json.dumps(signature, ensure_ascii=False).encode()).hexdigest()


def _items(rows, value, said):
    return [dict(id=r['id'], month=r.get('month') or r['date'][:7], answer=value, said=said,
                 spend_id=r['split_payment']['id'], payment=r['split_payment'],
                 receipt={key: r[key] for key in ('id', 'date', 'name', 'amount', 'method')}) for r in rows]


def remember(rows, said):
    """Reuse September's monthly answer memory; no separate store or ledger."""
    from . import opaque, allocation
    st = opaque.load_state()
    for batch in _sessions(rows):
        if len({r['name'] for r in batch}) < 2:
            continue
        evidence = []
        for row in batch:
            snap = allocation.call('show', receipt_id=row['id'])
            evidence.append(dict(receipt_id=row['id'], revision=snap['revision'],
                                 expense_id=row['split_payment']['id'], amount=row['amount']))
        opaque.remember(st, _memory_key(batch), 0, batch[0]['split_payment']['date'], said,
                        dict(kind='settlement', evidence=evidence))
    opaque.save_state(st)


def apply_remembered(rows):
    """Apply only an answered monthly pattern, through the ordinary writer.

    The kit revalidates both the new proposal and the original allocation
    revisions in one transaction. New/changed groups still get a question.
    """
    from . import opaque
    st = opaque.load_state()
    changed = False
    for batch in _sessions(rows):
        if len({r['name'] for r in batch}) < 2:
            continue
        mem = opaque.recall(st, _memory_key(batch), 0, batch[0]['split_payment']['date'], answered_pattern=True)
        if not mem or (mem.get('category') or {}).get('kind') != 'settlement':
            continue
        try:
            kit.run('ledger-review-answer', '--month', batch[0]['date'][:7], '--data', json.dumps(dict(
                kind='deposit_group', items=_items(batch, 'received', mem['name']),
                remembered_from=mem['category']['evidence'])))
        except kit.KitError:
            # Changed/rejected original facts revoke this memory. The ordinary
            # question remains; validation rolls back the complete session.
            continue
        remember(batch, mem['name'])
        changed = True
    return changed


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
        evidence = row.get('split_evidence')
        active_split = (not evidence or evidence['confirmed'] or any(
            sid not in flow.get('deposit_multi_declined', []) for sid in evidence['receipt_ids']))
        kind = ('own_transfer' if row.get('own_sender') or sender and sender in names else
                'insurance' if sender and row.get('insurer') else
                'multi' if include_person and sender and active_split and row.get('split_payments')
                and row['id'] not in flow.get('deposit_multi_declined', []) else
                'split' if include_person and sender and active_split and row.get('split_payment') else
                'person' if include_person and sender and row.get('person') else None)
        if kind and qid not in flow['answers'] and (row['id'] not in single or kind == 'multi') and flow.get('deposit_typing') != qid:
            if kind == 'multi':
                # A bundle identifies the question, regardless of when each
                # sender paid or whether a receipt needed continuation evidence.
                key = tuple(sorted((p['id'], p['unit'], p['people'])
                                   for p in row['split_payments']))
                peers = [r for r in deposits if r.get('split_payments') and
                         tuple(sorted((p['id'], p['unit'], p['people'])
                                      for p in r['split_payments'])) == key]
                capacity = row.get('split_capacities', {})
                overbooked = any((len(peers) + 1) * p['unit'] >
                                 capacity.get(str(p['id']), capacity.get(p['id'], p['amount']))
                                 for p in row['split_payments'])
                if row['id'] in single or overbooked:
                    key = ('single', row['id'])
            elif kind == 'split':
                key = tuple(row['split_stream']['payment_ids']) if row.get('split_stream') else row['split_payment']['id']
            else:
                key = sender if kind == 'person' else ''
            kinds.setdefault((kind, key), []).append(row)
    for (kind, sender), rows in kinds.items():
        if kind == 'multi':
            rows = sorted(rows, key=lambda r: (r['date'], r.get('at_time') or '', r['id']))
        # Month readers can contribute receipts on both sides of a month
        # boundary. Recheck their combined capacity before issuing one card.
        if kind == 'split':
            by_payment = {}
            for row in rows:
                by_payment.setdefault(row['split_payment']['id'], []).append(row)
            if any(sum(r['amount'] for r in batch) > min(
                    r.get('split_capacity', r['split_payment']['amount']) for r in batch)
                   for batch in by_payment.values()):
                continue
            # A recurring stream cannot lend its senders to another payment.
            # One exact half independently supports owner + one sender.
            rows = [row for batch in by_payment.values()
                    if len({r['name'].strip() for r in batch}) >= 2 or any(
                        r.get('split_continuation') for r in batch) or (
                        len(batch) == 1 and batch[0]['split_payment']['people'] == 2
                        and batch[0]['split_payment']['shares'] == 1
                        and batch[0]['amount'] * 2 == batch[0]['split_payment']['amount'])
                    for row in batch]
            if not rows:
                continue
        if len(rows) > 1 or kind in ('split', 'multi'):
            if kind == 'person':
                rows = sorted(rows, key=lambda r: (r['date'], r.get('at_time') or '', r['id']))
                numbers = flow.setdefault('deposit_numbers', {}).setdefault(sender, {})
                for row in rows:
                    numbers.setdefault(str(row['id']), max(numbers.values(), default=0) + 1)
            digest = hashlib.sha256(','.join(str(r['id']) for r in rows).encode()).hexdigest()[:12]
            groups[PREFIX + kind + '_' + digest] = dict(kind=kind, sender=sender, items=rows)
    flow['deposit_groups'] = groups
    grouped = {r['id']: qid for qid, group in groups.items() for r in group['items']}
    queue = list(dict.fromkeys(grouped.get(r['id'], 'review_deposit_' + str(r['id'])) for r in deposits))
    # Evidence-building cards must precede sender/individual classifications.
    # Earlier receipts establish a group before a delayed share is answered.
    def order(qid):
        group = groups.get(qid, {})
        evidence = group.get('kind') in ('split', 'multi')
        rows = group.get('items', [])
        return (0, min((r['date'], r.get('at_time') or '', r['id']) for r in rows)) if evidence else (1, ())
    return sorted(queue, key=order)


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
    if kind == 'multi':
        rows = sorted(rows, key=lambda r: (r['date'], r.get('at_time') or '', r['id']))
        row = rows[0]
        payments = row['split_payments']
        won = lambda amount: text.t('ledger.money.won', n=format(int(amount), ','))
        address = paths.agent_conf_value('AGENT_ADDRESS') or text.t('ledger.owner.address')
        dates = '·'.join(dict.fromkeys(dashboard.question_date(p['date'], flow['today']) for p in payments))
        lines = [text.t('ledger.unmatched_deposit.group.multi.conclusion',
                       sender=html.escape(row['counterparty']), amount=won(row['amount']), date=dates,
                       count=text.t('ledger.unmatched_deposit.group.multi.count.' + str(len(payments))),
                       address=html.escape(address))]
        if len(rows) > 1:
            # Reuse the approved split-card sentence and sender rows. Payment
            # details still name every venue, once, above the shared receipts.
            merchants = ' · '.join(dict.fromkeys(p.get('display_name') or p['name'] for p in payments))
            lines = [text.t('ledger.unmatched_deposit.group.split.conclusion',
                           merchant=html.escape(merchants), n=len(payments), address=html.escape(address)), '']
        for payment in payments:
            lines.append(text.t('ledger.unmatched_deposit.group.multi.payment',
                date=dashboard.question_date(payment['date'], flow['today']),
                merchant=html.escape(payment.get('display_name') or payment['name']),
                amount=won(payment['amount']), n=payment['people'], unit=won(payment['unit'])))
        if len(rows) > 1:
            lines.append('<blockquote>' + '\n'.join('• ' + text.t(
                'ledger.unmatched_deposit.group.split.receipt',
                date=dashboard.question_date(r['date'], flow['today']), name=html.escape(r['counterparty']),
                amount=won(r['amount']), shares='') for r in rows) + '</blockquote>')
        buttons = [dict(label=text.t('ledger.unmatched_deposit.group.split.' + key), value=value)
                   for key, value in (('yes', 'received'), ('no', 'decline'))]
        if len(rows) > 1:
            buttons.append(dict(label=text.t('ledger.unmatched_deposit.group.split.separate'), value='one_by_one'))
        return O._interactive(O._q(qid, '\n'.join(lines), buttons, items=rows,
                                   deposit_kind=kind, deposit_today=flow['today']), [buttons])
    if kind == 'split':
        by_payment = {}
        for row in rows:
            by_payment.setdefault(row['split_payment']['id'], []).append(row)
        payment = rows[0]['split_payment']
        merchant = payment.get('display_name') or payment['name']
        address = paths.agent_conf_value('AGENT_ADDRESS') or text.t('ledger.owner.address')
        lines = [text.t('ledger.unmatched_deposit.group.split.conclusion' + ('.one' if len(by_payment) == 1 else ''),
                        merchant=html.escape(merchant), n=len(by_payment), address=html.escape(address)), '']
        won = lambda amount: text.t('ledger.money.won', n=format(int(amount), ','))
        for batch in sorted(by_payment.values(), key=lambda b: (b[0]['split_payment']['date'], b[0]['split_payment']['id'])):
            payment = batch[0]['split_payment']
            lines.append(text.t('ledger.unmatched_deposit.group.split.payment',
                date=dashboard.question_date(payment['date'], flow['today']),
                amount=won(payment['amount']), n=payment['people'], unit=won(payment['unit'])))
            lines.append('<blockquote>' + '\n'.join('• ' + text.t(
                'ledger.unmatched_deposit.group.split.receipt',
                date=dashboard.question_date(row['date'], flow['today']), name=html.escape(row['counterparty']),
                amount=won(row['amount']), shares=text.t(
                    'ledger.unmatched_deposit.group.split.shares', k=row['split_payment']['shares'])
                    if row['split_payment']['shares'] > 1 else '') for row in batch) + '</blockquote>')
        typing = flow.get('deposit_group_typing') == qid
        buttons = [] if typing else [dict(label=text.t('ledger.unmatched_deposit.group.split.' + key), value=value)
            for key, value in (('yes', 'received'), ('no', 'custom'), ('separate', 'one_by_one'))]
        return O._interactive(O._q(qid, '\n'.join(lines), buttons, items=rows, free=True,
                                   deposit_kind=kind, deposit_today=flow['today']), [buttons] if buttons else [])
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
    for row in sorted(rows, key=lambda r: (r['date'], r['id'])):
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
    if q['deposit_kind'] == 'multi':
        if value == 'decline':
            flow['deposit_multi_declined'] = list(dict.fromkeys(
                flow.get('deposit_multi_declined', []) + [r['id'] for r in q['items']]))
            return dict(ok=True, split=True)
        if value not in ('received', 'one_by_one'):
            raise ValueError('choose an issued settlement answer')
    if value == 'one_by_one':
        flow.pop('deposit_group_typing', None)
        flow['deposit_singles'] = list(dict.fromkeys(flow.get('deposit_singles', []) + [r['id'] for r in q['items']]))
        return dict(ok=True, split=True)
    if q['deposit_kind'] in ('person', 'split') and value == 'type':
        flow['deposit_group_typing'] = q['id']
        return dict(ok=True, pending_input=True)
    if q['deposit_kind'] == 'split' and value == 'custom' and not said:
        said = next(c['label'] for c in q['choices'] if c['value'] == 'custom')
    allowed = (('received',) if q['deposit_kind'] == 'multi' else
               ('income', 'received', 'own_transfer', 'custom') if q['deposit_kind'] in ('person', 'split') else (q['deposit_kind'],))
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
    if q['deposit_kind'] == 'multi':
        for row, item in zip(rows, items):
            item.update(payments=row['split_payments'],
                        receipt={key: row[key] for key in ('id', 'date', 'name', 'amount', 'method')})
    result = kit.run('ledger-review-answer', '--month', items[0]['month'], '--data',
                     json.dumps(dict(kind='deposit_group', items=items)))
    if q['deposit_kind'] == 'split' and all(item['answer'] == 'received' for item in items):
        remember(rows, said or value)
    flow.pop('deposit_group_typing', None)
    for row, item, rec in zip(rows, items, result['results']):
        flow['answers']['review_deposit_' + str(row['id'])] = dict(ok=True, result=rec, value=item['answer'], said=item['said'])
    return dict(ok=True, result=result)
