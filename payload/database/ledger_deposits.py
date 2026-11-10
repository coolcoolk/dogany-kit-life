"""Unresolved deposits, separate from confirmed income and allocations.

Owner-picked questions are part of the ordinary monthly review.
"""
import os
import re
from fractions import Fraction
from pathlib import Path

import ledger_diag as D
import ledger_profile as LP


def unknown_receipt(name, src_cat=''):
    """A missing bank memo/category is not evidence of earned income."""
    return (not (name or '').strip() or (name or '').strip() in
            ('-', '입금 내역', '입금', '미분류', '기타수입', 'unknown', 'deposit'))


def owner_names(root):
    """Explicit owner profile identity, shared by import and sender review."""
    for rel in ('identity/hot.custom.owner.md', 'USER.md'):
        try:
            profile = (Path(root) / rel).read_text(encoding='utf-8')
        except OSError:
            continue
        match = re.search(r'^\s*(?:-\s*)?(?:\*\*)?(?:Name|이름|성명)(?:\*\*)?\s*:\s*(?:\*\*)?([^\n]+)', profile, re.I | re.M)
        if match:
            return {name.strip() for name in re.split(r'[()（）]', match[1]) if name.strip()}
    return set()


def own_sender(c, name):
    """Only explicit owner identity, optionally prefixed by a bank label.

    A masked person is never resolved to the owner from similar letters.
    Paired account legs are handled by callers independently of the memo.
    """
    db = next((r[2] for r in c.execute('PRAGMA database_list') if r[1] == 'main'), '')
    root = os.environ.get('LEDGER_ROOT') or os.environ.get('DOGANY_INSTANCE_ROOT') or Path(db).parent.parent
    names = owner_names(root)
    sender = (name or '').strip()
    return any(sender == n or re.fullmatch(r'(?:토스|토뱅|카카오|네이버)\s*' + re.escape(n), sender)
               for n in names)


def _split_explanation(items, payment):
    """Count observed senders plus the owner; never invent missing people.

    Two independent senders must establish a common one-person amount before
    another sender can represent extra shares. Repeated transfers from one
    sender are one observation, not independent evidence for that amount.
    """
    totals = {}
    for row in items:
        sender = row['name'].strip()
        totals[sender] = totals.get(sender, 0) + row['amount']
    if len(totals) < 2 or any(amount <= 0 for amount in totals.values()):
        return None
    explanations = []
    for unit in set(totals.values()):
        if sum(amount == unit for amount in totals.values()) < 2:
            continue
        if any(amount % unit for amount in totals.values()):
            continue
        people = 1 + sum(amount // unit for amount in totals.values())
        exact = Fraction(payment['amount'], people)
        rounded = unit != exact
        if rounded and not any(unit in (payment['amount'] // (people * step) * step,
                                       (payment['amount'] + people * step - 1) // (people * step) * step)
                               for step in (100, 1000)):
            continue
        owner = payment['amount'] - sum(totals.values())
        if owner <= 0 or any(row['amount'] % unit for row in items):
            continue
        explanations.append((rounded, people, unit, owner, [row['amount'] // unit for row in items]))
    return min(explanations, key=lambda e: (e[0], e[1], -e[2])) if explanations else None


def pending(c, month):
    from ledger_review import annotations, received_person
    meta = annotations(c)
    rows = D.rows(c, "SELECT s.* FROM spend_entry s WHERE "
                  "s.scope<>'business' AND s.kind='transfer' AND (s.class IS NULL OR s.class='own_transfer') "
                  # A pair is already classified. Savings/expense outflows use
                  # positive amounts too; a negative-only test misses them.
                  "AND s.amount>0 AND s.pair_id IS NULL "
                  "AND NOT EXISTS(SELECT 1 FROM ledger_receipt_allocation a WHERE a.receipt_id=s.id)")
    pending_rows = [dict(r, counterparty=r['name'] if not unknown_receipt(r['name'], r['src_cat']) and r['name'].strip() not in (r['src_cat'], (r['src_cat'] or '').split('/')[-1]) else None, insurer=LP._insurance(LP.merchant_key(r['name'])) or '손보' in r['name'],
                 person=received_person(c, r) and not own_sender(c, r['name']),
                 own_sender=own_sender(c, r['name']), needs_owner_pick=False,
                 allocation_rejected_expenses=meta.get(r['id'], {}).get('allocation_rejected_expenses', [])) for r in rows
            if not LP.is_dust(r['amount']) and not any(meta.get(r['id'], {}).get(k) for k in
                       ('deposit_reviewed', 'spend_id', 'income_confirmed', 'history_received'))
            and (not meta.get(r['id'], {}).get('allocation_mode')
                 or meta.get(r['id'], {}).get('allocation_status') == 'released')]
    # A payment's three-day receipt window can cross a month boundary. Select
    # one explanation from the complete evidence, then return the asked month.
    month_rows = [r for r in pending_rows if r['date'][:7] == month]
    import ledger_history as H
    people = [r for r in pending_rows if r['person'] and not r['insurer']]
    if not people:
        return month_rows
    proposals = []
    for group in H.deposit_batches(people, H.deposit_expenses(c)):
        payment = group['payment']
        # Confirmed allocations remain observed evidence when the owner uses
        # the existing one-by-one path. Released/rejected receipts do not.
        credited = D.rows(c, "SELECT s.* FROM spend_entry s JOIN ledger_receipt_allocation a "
                          "ON a.receipt_id=s.id WHERE a.expense_id=? AND a.payer='owner' "
                          "AND a.amount=s.amount AND s.kind='transfer'", (payment['id'],))
        evidence = group['items'] + credited
        explanation = _split_explanation(evidence, payment)
        # A bank edit can make every previously plausible split stop fitting.
        # Leave those rows as ordinary pending deposits so an issued card reaches
        # the stale-receipt guard instead of crashing while the card is rebuilt.
        if explanation is None:
            continue
        _, people, unit, owner_share, shares = explanation
        category = c.execute('SELECT COALESCE(c.display_name,c.name) FROM spend_entry s '
                             'LEFT JOIN spend_category c ON c.id=s.category_id WHERE s.id=?',
                             (payment['id'],)).fetchone()[0]
        for row, count in zip(group['items'], shares):
            row['split_payment'] = {key: payment.get(key) for key in
                                    ('id', 'date', 'name', 'amount', 'method')}
            row['split_payment'].update(people=people, unit=round(unit), shares=count, category=category,
                                        owner_share=round(owner_share))
            row['split_capacity'] = group['payment']['remaining']
        proposals.append(group)
    # Repeated venue sessions share the existing merchant stream identity.
    # Require at least two repeat senders with the same unit multiplicities;
    # an extra participant is allowed, but unrelated/disjoint groups are not.
    for group in proposals:
        payment = group['payment']
        signature = {(r['name'], r['split_payment']['shares']) for r in group['items']}
        peers = [g for g in proposals if g['payment']['date'] != payment['date']
                 and LP.merchant_key(g['payment']['name']) == LP.merchant_key(payment['name'])
                 and g['payment']['method'] == payment['method']
                 and len(signature & {(r['name'], r['split_payment']['shares']) for r in g['items']}) >= 2
                 and (signature <= {(r['name'], r['split_payment']['shares']) for r in g['items']}
                      or signature >= {(r['name'], r['split_payment']['shares']) for r in g['items']})]
        if peers:
            # Use the established merchant key; this is a proposal only, never
            # a fixed-cost registration or a standing allocation rule.
            stream = dict(key=LP.merchant_key(payment['name']), method=payment['method'],
                          payment_ids=sorted({payment['id'], *(g['payment']['id'] for g in peers)}))
            for row in group['items']:
                row['split_stream'] = stream
    return month_rows


def _check_split_receipt(c, data):
    receipt = data.get('receipt')
    if data.get('spend_id') is None or receipt is None:
        return
    current = D.rows(c, 'SELECT id,date,name,amount,method FROM spend_entry WHERE id=?', (data.get('id'),))
    if (not isinstance(receipt, dict) or not current
            or any(receipt.get(key) != value for key, value in current[0].items())):
        raise ValueError('split receipt changed: reopen deposit review')


def answer(c, month, data, K=None, *, _pending=None):
    from ledger_review import put, annotations
    sid = data.get('id')
    fact = annotations(c).get(sid,{}) if type(sid) is int else {}
    classified = fact.get('history_received') or fact.get('deposit_reviewed') or fact.get('debt_direction')=='borrowed'
    reversible = classified and not fact.get('spend_id') and c.execute("SELECT 1 FROM spend_entry WHERE id=? AND substr(date,1,7)=? AND scope<>'business' AND amount>0 AND kind='transfer'",(sid,month)).fetchone() and not c.execute('SELECT 1 FROM ledger_receipt_allocation WHERE receipt_id=?',(sid,)).fetchone() and not c.execute('SELECT 1 FROM ledger_settlement_match WHERE transfer_id=? AND credited>0',(sid,)).fetchone()
    candidate = next((r for r in (pending(c, month) if _pending is None else _pending)
                      if r['id'] == sid), None)
    if type(sid) is not int or not (reversible or candidate):
        raise ValueError('choose an unmatched deposit awaiting review')
    value = data.get('answer')
    if value not in ('own_transfer', 'income', 'salary', 'received', 'custom', 'insurance'):
        raise ValueError('choose own_transfer, income, salary, received or custom')
    note = data.get('said', '')
    if not isinstance(note, str):
        raise ValueError('deposit note must be owner words')
    if value == 'custom' and not note.strip():
        raise ValueError('custom deposit answer needs owner words')
    payment = None
    if data.get('spend_id') is not None:
        # Recompute the proposal in the kit transaction. Bind both payment
        # and receipt facts, so an old card cannot apply unseen bank edits.
        payment = candidate.get('split_payment') if candidate else None
        issued = data.get('payment')
        _check_split_receipt(c, data)
        if (not payment or payment['id'] != data['spend_id'] or not isinstance(issued, dict)
                or any(issued.get(key) != value for key, value in payment.items())):
            raise ValueError('split payment changed: reopen deposit review')
    is_income = value in ('income', 'salary')
    if is_income:
        c.execute("UPDATE spend_entry SET kind='income',class='income' WHERE id=?", (sid,))
    else:
        c.execute("UPDATE spend_entry SET kind='transfer',class=?,stream_id=NULL WHERE id=?",
                  ('own_transfer' if value == 'own_transfer' else None, sid))
    if value == 'salary':
        c.execute('UPDATE spend_entry SET src_cat=? WHERE id=?', ('\uae09\uc5ec', sid))
    put(c, sid, dict(deposit_reviewed=True, deposit_answer=value, said=note, insurance_receipt=value == 'insurance',
                    income_confirmed=is_income, history_received=False,debt_direction=None,principal=None,start_date=None,role=None))
    c.execute("UPDATE ledger_preference SET source='owner' WHERE ulid=?",('review-item-%d'%sid,))
    if value == 'received' and payment:
        import ledger_allocation as A
        A.replace(c, sid, [dict(expense_id=payment['id'], amount=candidate['amount'], payer='owner')],
                  A.revision(c, sid), 'deposit-split:%d:%d' % (sid, payment['id']))
    if K is not None:
        if is_income:
            r=D.rows(c,'SELECT * FROM spend_entry WHERE id=?',(sid,))[0]
            K._spend_write_v32(c,sid,r['date'],r['name'],r['amount'],'income',r['method'])
        else:
            K._spend_shadow_del(c,sid)
    return dict(reviewed='deposit', id=sid, answer=value, applied=True)


def answer_group(c, data, K=None):
    """Apply the issued cross-month group through the same per-row write.

    A stale row invalidates the whole answer, including any shadow writes.
    """
    items = data.get('items')
    allowed = ('own_transfer', 'insurance', 'income', 'received', 'custom')
    if (not isinstance(items, list)
            or not items or any(not isinstance(r, dict) or type(r.get('id')) is not int
                                or not isinstance(r.get('month'), str)
                                or r.get('answer', data.get('answer')) not in allowed for r in items)
            or len({r['id'] for r in items}) != len(items)):
        raise ValueError('choose an explicit deposit group and its issued answer')
    if not c.in_transaction:
        c.execute('BEGIN')
    c.execute('SAVEPOINT deposit_group')
    try:
        if 'remembered_from' in data:
            import ledger_allocation as A
            evidence = data['remembered_from']
            if not isinstance(evidence, list) or not evidence:
                raise ValueError('remembered settlement needs its original allocations')
            for fact in evidence:
                snap = A.snapshot(c, fact['receipt_id'])
                if snap['revision'] != fact['revision'] or not any(
                        a['expense_id'] == fact['expense_id'] and a['amount'] == fact['amount']
                        and a['payer'] == 'owner' for a in snap['allocations']):
                    raise ValueError('remembered settlement changed: ask again')
        # A changed receipt can dissolve the entire split proposal. Check all
        # issued receipts before an earlier member reports a missing payment.
        for item in items:
            _check_split_receipt(c, item)
        # Validate all members against the same pre-answer evidence. Otherwise
        # crediting the first receipt can dissolve a rounded proxy explanation
        # before the second member is checked in this atomic group.
        pending_months = {month: pending(c, month) for month in {r['month'] for r in items}}
        results = [answer(c, r['month'], dict(kind='deposit', id=r['id'], answer=r.get('answer', data.get('answer')),
                                             said=r.get('said', data.get('said', '')),
                                             spend_id=r.get('spend_id'), payment=r.get('payment'),
                                             receipt=r.get('receipt')), K=K,
                          _pending=pending_months[r['month']]) for r in items]
        c.execute('RELEASE deposit_group')
    except Exception:
        c.execute('ROLLBACK TO deposit_group')
        c.execute('RELEASE deposit_group')
        raise
    return dict(reviewed='deposit_group', results=results)
