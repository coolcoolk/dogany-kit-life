"""Unmatched own-transfer inflows, separate from income and allocations.

Owner-picked questions are part of the ordinary monthly review.
"""
import re
from pathlib import Path

import ledger_diag as D
import ledger_profile as LP


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


def pending(c, month):
    from ledger_review import annotations, received_person
    meta = annotations(c)
    rows = D.rows(c, "SELECT s.* FROM spend_entry s WHERE substr(s.date,1,7)=? "
                  "AND s.scope<>'business' AND s.kind='transfer' AND s.class='own_transfer' "
                  # A pair is already classified. Savings/expense outflows use
                  # positive amounts too; a negative-only test misses them.
                  "AND s.amount>0 AND s.pair_id IS NULL "
                  "AND NOT EXISTS(SELECT 1 FROM ledger_receipt_allocation a WHERE a.receipt_id=s.id)", (month,))
    pending_rows = [dict(r, counterparty=r['name'] if r['name'].strip() not in ('', '-', r['src_cat'], (r['src_cat'] or '').split('/')[-1]) else None, insurer=LP._insurance(LP.merchant_key(r['name'])) or '손보' in r['name'],
                 person=received_person(c, r), needs_owner_pick=False) for r in rows
            if not LP.is_dust(r['amount']) and not any(meta.get(r['id'], {}).get(k) for k in
                       ('deposit_reviewed', 'allocation_mode', 'spend_id', 'income_confirmed', 'history_received'))]
    import ledger_history as H
    people = [r for r in pending_rows if r['person'] and not r['insurer']]
    if not people:
        return pending_rows
    for group in H.deposit_batches(people, H.deposit_expenses(c)):
        payment = group['payment']
        explanations = []
        for n in range(1, H.SPLIT_CAP + 1):
            fits = [next(((rounded, k) for rounded, people, k in H.deposit_fits(row, payment)
                          if people == n), None) for row in group['items']]
            if all(fits) and sum(fit[1] for fit in fits) <= n - 1:
                explanations.append((any(fit[0] for fit in fits), n, fits))
        # A bank edit can make every previously plausible split stop fitting.
        # Leave those rows as ordinary pending deposits so an issued card reaches
        # the stale-receipt guard instead of crashing while the card is rebuilt.
        if not explanations:
            continue
        _, people, fits = min(explanations, key=lambda item: (item[0], item[1]))
        unit = round(payment['amount'] / people)
        category = c.execute('SELECT COALESCE(c.display_name,c.name) FROM spend_entry s '
                             'LEFT JOIN spend_category c ON c.id=s.category_id WHERE s.id=?',
                             (payment['id'],)).fetchone()[0]
        for row, (_, shares) in zip(group['items'], fits):
            row['split_payment'] = {key: payment.get(key) for key in
                                    ('id', 'date', 'name', 'amount', 'method')}
            row['split_payment'].update(people=people, unit=unit, shares=shares, category=category)
            row['split_capacity'] = group['payment']['remaining']
    return pending_rows


def _check_split_receipt(c, data):
    receipt = data.get('receipt')
    if data.get('spend_id') is None or receipt is None:
        return
    current = D.rows(c, 'SELECT id,date,name,amount,method FROM spend_entry WHERE id=?', (data.get('id'),))
    if (not isinstance(receipt, dict) or not current
            or any(receipt.get(key) != value for key, value in current[0].items())):
        raise ValueError('split receipt changed: reopen deposit review')


def answer(c, month, data, K=None):
    from ledger_review import put, annotations
    sid = data.get('id')
    fact = annotations(c).get(sid,{}) if type(sid) is int else {}
    classified = fact.get('history_received') or fact.get('deposit_reviewed') or fact.get('debt_direction')=='borrowed'
    reversible = classified and not fact.get('spend_id') and c.execute("SELECT 1 FROM spend_entry WHERE id=? AND substr(date,1,7)=? AND scope<>'business' AND amount>0 AND kind='transfer'",(sid,month)).fetchone() and not c.execute('SELECT 1 FROM ledger_receipt_allocation WHERE receipt_id=?',(sid,)).fetchone() and not c.execute('SELECT 1 FROM ledger_settlement_match WHERE transfer_id=? AND credited>0',(sid,)).fetchone()
    candidate = next((r for r in pending(c, month) if r['id'] == sid), None)
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
        c.execute("UPDATE spend_entry SET kind='transfer',class='own_transfer',stream_id=NULL WHERE id=?", (sid,))
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
        # A changed receipt can dissolve the entire split proposal. Check all
        # issued receipts before an earlier member reports a missing payment.
        for item in items:
            _check_split_receipt(c, item)
        results = [answer(c, r['month'], dict(kind='deposit', id=r['id'], answer=r.get('answer', data.get('answer')),
                                             said=r.get('said', data.get('said', '')),
                                             spend_id=r.get('spend_id'), payment=r.get('payment'),
                                             receipt=r.get('receipt')), K=K) for r in items]
        c.execute('RELEASE deposit_group')
    except Exception:
        c.execute('ROLLBACK TO deposit_group')
        c.execute('RELEASE deposit_group')
        raise
    return dict(reviewed='deposit_group', results=results)
