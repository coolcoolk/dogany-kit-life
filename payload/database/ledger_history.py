# -*- coding: utf-8 -*-
"""Evidence-only repairs for imported history, shared by import and replay.

This module owns no screens. It preserves original amounts and uses the
existing review/allocation stores, so corrections and undo keep working.
"""
import datetime
import re

import ledger_diag as D
import ledger_profile as LP
import ledger_review as R
import ledger_window as W

LOAN = re.compile(r'대출|스마트론|직장인.*론|\bloan\b', re.I)


def _annotate(c, sid, detail):
    existing = c.execute('SELECT source FROM ledger_preference WHERE ulid=?', ('review-item-%d'%sid,)).fetchone()
    R.put(c,sid,detail)
    if not existing:
        c.execute("UPDATE ledger_preference SET source='ledger_history' WHERE ulid=?", ('review-item-%d'%sid,))


def _day(row):
    return datetime.date.fromisoformat(row['date'])


SPLIT_CAP = 12


def deposit_fits(receipt, payment):
    """Possible (rounded, people, shares) explanations, using integer won.

    A sender can cover k units of the same per-person share. Round that unit
    only to the approved 100/1,000 won boundaries, never to integer won first.
    """
    if receipt['amount'] <= 0 or payment['amount'] <= 0:
        return []
    fits = []
    for n in range(1, SPLIT_CAP + 1):
        for k in range(1, max(2, n)):
            if receipt['amount'] * n == payment['amount'] * k:
                fits.append((False, n, k))
                continue
            for unit in (100, 1000):
                divisor = n * unit
                if receipt['amount'] in (payment['amount'] // divisor * unit * k,
                                         (payment['amount'] + divisor - 1) // divisor * unit * k):
                    fits.append((True, n, k))
                    break
    return sorted(fits)


def deposit_rank(receipt, payment):
    return (deposit_fits(receipt, payment)[0][0], (_day(receipt) - _day(payment)).days)


def deposit_candidates(receipt, expenses):
    """Prior-three-day matches, exact before rounded, then nearer first.

    Equal-ranked payments remain ambiguous; ids only stabilize display order.
    """
    return sorted((p for p in expenses if 0 <= (_day(receipt) - _day(p)).days <= 3
                   and deposit_fits(receipt, p)), key=lambda p: (*deposit_rank(receipt, p), p['id']))


def best_deposit_payment(receipt, expenses):
    candidates = deposit_candidates(receipt, expenses)
    if candidates and (len(candidates) == 1 or
                       deposit_rank(receipt, candidates[0]) < deposit_rank(receipt, candidates[1])):
        return candidates[0]
    return None


def deposit_expenses(c, ledger=None):
    """Eligible payments with remaining capacity, excluding cancelled charges."""
    ledger = ledger if ledger is not None else D.rows(c, "SELECT * FROM spend_entry WHERE scope<>'business'")
    meta = R.annotations(c)
    streams = {r['id']: r for r in D.rows(c, 'SELECT * FROM fixed_cost')}
    expenses = []
    for r in ledger:
        if (r['kind'] != 'expense' or r['class'] not in (None, 'spend') or r['amount'] <= 0
                or c.execute('SELECT 1 FROM ledger_share WHERE spend_id=?', (r['id'],)).fetchone()
                or R.fixed_class(r, streams.get(r['stream_id'], {}), meta.get(r['id'], {}), r['is_fixed']) in R.FIXED
                or any(s['kind']=='expense' and s['amount']==-r['amount'] and s['name']==r['name']
                       and s['method']==r['method'] and s['date']==r['date'] for s in ledger)):
            continue
        remaining = c.execute('SELECT s.amount-' + R.reimbursement_sql('s') +
                              ' FROM spend_entry s WHERE id=?', (r['id'],)).fetchone()[0]
        if remaining > 0:
            expenses.append(dict(r, remaining=remaining))
    return expenses


def deposit_batches(receipts, expenses):
    """Group independently ranked matches; never spend the owner's remainder."""
    groups = {}
    for r in receipts:
        payment = best_deposit_payment(r, [e for e in expenses
            if e['id'] not in r.get('allocation_rejected_expenses', [])])
        if payment:
            groups.setdefault(payment['id'], dict(payment=payment, items=[]))['items'].append(r)
    return [g for g in groups.values() if sum(r['amount'] for r in g['items']) <=
            g['payment'].get('remaining', g['payment']['amount'])]


def _salary_ids(payments):
    """Recurring month-end early payroll with an independently named anchor.

    Own-name alone is never evidence. Opposite transfer legs are excluded
    by the caller; two consecutive monthly observations and a nearby known
    salary on the same instrument are required.
    """
    anchors = [r for r in payments if r['kind'] == 'income' and r['class'] == 'income'
               and ('급여' in r['name'] or 'salary' in r['name'].casefold())]
    possible = [r for r in payments if r['kind'] == 'transfer' and r['class'] == 'own_transfer'
                and r['amount'] >= LP.INCOME_MIN and _day(r).day >= 25
                and (r['at_time'] or '') in ('07:00', '07:01', '07:02') and r['pair_id'] is None]
    found = set()
    for r in possible:
        repeat = any(s['id'] != r['id'] and s['method'] == r['method'] and s['name'] == r['name']
                     and abs(D.month_index(s['date'][:7])-D.month_index(r['date'][:7])) == 1
                     and s['amount'] == r['amount'] for s in possible)
        anchor = any(s['method'] == r['method'] and abs((_day(s)-_day(r)).days) <= 95
                     and abs(s['amount']-r['amount']) <= r['amount']*.2 for s in anchors)
        if repeat and anchor:
            found.add(r['id'])
    return found


def repair(K, c):
    import ledger_money_flow
    saving_repairs = ledger_money_flow.reconcile(K, c)
    reviewed_debts = _repair_reviewed_debts(K, c)
    payments = D.rows(c, "SELECT * FROM spend_entry WHERE scope<>'business' ORDER BY date,at_time,id")
    meta = R.annotations(c)
    protected = {sid for sid,fact in meta.items() if any(fact.get(key) for key in
                 ('owner_class','deposit_reviewed','income_confirmed','allocation_mode','spend_id','insurance_receipt','cash','debt_direction'))}
    salary = _salary_ids(payments) - protected
    loans = []
    from ledger_v6 import Methods, topup_wallet, _method_layer, nfkc
    methods = Methods(c)
    wallet_funding = []
    for r in payments:
        wallet = topup_wallet(methods, r['name'])
        source = methods.names.get(nfkc(r['method'] or ''))
        if (r['id'] not in protected and wallet is not None
                and (source is None or methods.canon(source) != wallet) and r['kind']=='expense' and r['amount']>0
                and r['class']=='spend'):
            c.execute("UPDATE spend_entry SET kind='transfer',class='topup',stream_id=NULL,is_fixed=0 WHERE id=?", (r['id'],))
            K._spend_shadow_del(c, r['id'])
            if source is not None:
                source = methods.canon(source)
                sub = 'card' if _method_layer(methods.row(source))=='card' else 'account'
                c.execute("INSERT INTO pay_funding(wallet_id,source_id,sub,is_default,first_seen,last_seen) VALUES(?,?,?,0,?,?) "
                          "ON CONFLICT(wallet_id,source_id,sub) DO UPDATE SET last_seen=MAX(COALESCE(last_seen,''),excluded.last_seen)",
                          (wallet,source,sub,r['date'],r['date']))
            _annotate(c, r['id'], dict(wallet_funding=wallet))
            wallet_funding.append(r['id'])
            continue
        if meta.get(r['id'], {}).get('owner_class') is not None:
            continue
        if meta.get(r['id'],{}).get('deposit_answer')=='received' and r['kind']=='income':
            c.execute("UPDATE spend_entry SET kind='transfer',class='own_transfer',stream_id=NULL WHERE id=?",(r['id'],))
            K._spend_shadow_del(c,r['id'])
        elif r['id'] in salary:
            c.execute("UPDATE spend_entry SET kind='income',class='income' WHERE id=?", (r['id'],))
            K._spend_write_v32(c, r['id'], r['date'], r['name'], r['amount'], 'income', r['method'])
            _annotate(c, r['id'], dict(salary_evidence='month_end_repeat_with_payroll_anchor'))
        elif (r['id'] not in protected and r['kind'] in ('income', 'transfer') and r['amount'] >= 1000000 and LOAN.search(r['name'])
              and r['class'] in ('income', 'own_transfer', 'loan') and r['pair_id'] is None):
            if r['kind'] != 'transfer' or r['class'] != 'loan':
                c.execute("UPDATE spend_entry SET kind='transfer',class='loan',stream_id=NULL WHERE id=?", (r['id'],))
                K._spend_shadow_del(c, r['id'])
            _annotate(c, r['id'], dict(debt_direction='borrowed', principal=r['amount'], start_date=r['date']))
            loans.append(r['id'])
    result = dict(salary=sorted(salary), loan_inflows=loans, saving_classes=saving_repairs, wallet_funding=wallet_funding)
    if reviewed_debts:
        result['reviewed_debts'] = reviewed_debts
    return result


def _repair_reviewed_debts(K, c):
    """Recover the old single-item adapter's spend/debt contradiction.

    Only explicit owner repayment words on a reviewed, unallocated spend are
    eligible. Use the ordinary correction writer, including its scope and
    allocation guards; ambiguous/negated words remain available for review.
    """
    meta = R.annotations(c)
    repaired = []
    owner_ids = {int(r[0]) for r in c.execute(
        "SELECT item FROM ledger_preference WHERE topic='ledger_review_item' "
        "AND status='active' AND source='owner'") if str(r[0]).isdigit()}
    for row in D.rows(c, "SELECT * FROM spend_entry WHERE kind='expense' AND class='spend' AND scope<>'business' AND amount>0 AND pair_id IS NULL"):
        fact = meta.get(row['id'], {})
        said = fact.get('said', '')
        if (fact.get('owner_class') is not None or row['id'] not in owner_ids or not fact.get('history_reviewed')
                or fact.get('history_answer') != 'spend' or not isinstance(said, str)
                or not re.search(r'상환|갚|repay', said, re.I)
                or re.search(r'아니|아냐|않|못|not\b|never\b|상환\s*예정|갚을', said, re.I)
                or any(fact.get(k) for k in ('debt_direction','deposit_reviewed','income_confirmed','allocation_mode','spend_id','insurance_receipt','cash'))):
            continue
        # Allocated/shared/settled rows need an explicit correction. Keep the
        # normal writer's validation errors transactional, never swallow a
        # failure after it has begun writing the correction.
        if (c.execute('SELECT 1 FROM ledger_receipt_allocation WHERE receipt_id=? OR expense_id=?', (row['id'], row['id'])).fetchone()
                or c.execute('SELECT 1 FROM ledger_share WHERE spend_id=?', (row['id'],)).fetchone()
                or c.execute('SELECT 1 FROM ledger_settlement_match WHERE transfer_id=? AND credited>0', (row['id'],)).fetchone()):
            continue
        R.answer(c, row['date'][:7], dict(kind='debt', id=row['id'], said=said), K=K)
        repaired.append(row['id'])
    return repaired


def match_history(c, K=None, hold_unmatched=True, review_deposits=False):
    """Expose unresolved personal receipts; never allocate from proximity.

    Amount/date evidence belongs in deposit proposals. Only the answer writer
    may link that proposal to a payment. Existing allocations and owner facts
    are retained, including released corrections.
    """
    from ledger_deposits import own_sender, unknown_receipt
    meta = R.annotations(c)
    owner_receipts = {int(item) for item, in c.execute(
        "SELECT item FROM ledger_preference WHERE topic='ledger_review_item' AND status='active' "
        "AND source='owner' AND json_extract(detail,'$.history_received')=1") if item.isdigit()}
    owner_receipts.update(int(item[7:]) for item, in c.execute(
        "SELECT item FROM ledger_preference WHERE topic='ledger_plan_fact' AND status='active' "
        "AND item LIKE 'refund:%'") if item[7:].isdigit())
    pending = []
    for r in D.rows(c, "SELECT * FROM spend_entry WHERE scope<>'business' ORDER BY date,at_time,id"):
        if (r['kind'] not in ('income', 'transfer') or r['amount'] < LP.DUST_KRW
                or r['class'] not in (None, 'income', 'own_transfer') or r['pair_id'] is not None
                or r['id'] in owner_receipts or own_sender(c, r['name'])
                or not (R.received_person(c, r) or unknown_receipt(r['name'], r['src_cat']))
                or any(meta.get(r['id'], {}).get(k) for k in
                       ('owner_class', 'income_confirmed', 'deposit_reviewed', 'allocation_mode',
                        'spend_id', 'salary_evidence', 'debt_direction'))
                or c.execute('SELECT 1 FROM ledger_receipt_allocation WHERE receipt_id=?', (r['id'],)).fetchone()
                or c.execute('SELECT 1 FROM ledger_settlement_match WHERE transfer_id=?', (r['id'],)).fetchone()):
            continue
        c.execute("UPDATE spend_entry SET kind='transfer',class=NULL,stream_id=NULL WHERE id=?", (r['id'],))
        # Earlier history imports used this flag to suppress deposit questions.
        # It is an inference, not an owner answer; allow the existing card path.
        if meta.get(r['id'], {}).get('history_received'):
            _annotate(c, r['id'], dict(history_received=False))
        if K is not None:
            K._spend_shadow_del(c, r['id'])
        pending.append(r['id'])
    return dict(matched=[], unmatched=pending)


def history(c, today=None, all_months=False):
    """Three complete months for every display, including history-first starts.

    all_months is retained for CLI/state compatibility; long history is read
    separately below only to detect debts and their repayment streams.
    """
    today = datetime.date.fromisoformat(today) if isinstance(today, str) else today or datetime.date.today()
    w = W.window(today)
    cutoff = w['history_through']
    months = w['average_months']
    business_present = bool(c.execute(
        "SELECT 1 FROM spend_entry WHERE scope='business' AND date BETWEEN ? AND ? LIMIT 1",
        (months[0]+'-01', cutoff)).fetchone())
    first_observed = c.execute("SELECT MIN(date) FROM spend_entry WHERE scope<>'business' AND date<=?",(cutoff,)).fetchone()[0]
    from ledger_plan import facts
    owner_facts = facts(c)
    owner_annotations = R.annotations(c)
    forecast = []
    result = []
    for ym in months:
        rs = [r for r in D._month_rows(c, ym) if r['date'] <= today.isoformat()]
        inc = sum(r['amount'] for r in rs if r['kind'] == 'income' and r['class'] == 'income')
        parts = {key: sum(r['amount'] for r in rs if r['kind'] == 'expense' and r['class'] == key) for key in D.OUT_CLASSES}
        parts['spend'] -= sum(r['amount'] for r in W.reviewed_credits(c, rs, owner_facts, owner_annotations))
        out = sum(parts.values())
        projected = forecast if ym == w['current_month'] else []
        actual = dict(recorded=bool(rs), income=inc, out=out, parts=dict(parts), gap=inc-out)
        for r in projected:
            parts[r['class']] += r['amount']
        out = sum(parts.values())
        partial = ym == today.strftime('%Y-%m') or (first_observed and ym==first_observed[:7] and first_observed[8:]!='01')
        result.append(dict(month=ym, through=max((r['date'] for r in rs),default=today.isoformat() if projected else None) if partial else None,
                           observed_from=min((r['date'] for r in rs),default=ym+'-01' if projected else None),
                           partial=bool(partial), recorded=bool(rs or projected),
                           income=inc, out=out, parts=parts, gap=inc-out, actual=actual,
                           projected=bool(projected), projection=projected,
                           borrowed=sum(r['amount'] for r in rs if r['kind']=='transfer' and r['class']=='loan' and r['amount']>0)))
    inflows = D.rows(c, "SELECT * FROM spend_entry WHERE scope<>'business' AND kind='transfer' AND class='loan' AND amount>0 AND date<=?", (today.isoformat(),))
    debts = []
    for r in inflows:
        candidates = []
        for f in D.rows(c, "SELECT * FROM fixed_cost WHERE class='loan' AND direction='expense'"):
            members = D.rows(c,"SELECT * FROM spend_entry WHERE stream_id=? AND kind='expense' AND class='loan' AND date<=? ORDER BY date",(f['id'],today.isoformat()))
            if (len({p['date'][:7] for p in members}) >= 3 and members[0]['method']==r['method']
                    and 0 <= (_day(members[0])-_day(r)).days <= 60):
                candidates.append(f['id'])
        competing = [p for p in inflows if p['id']!=r['id'] and p['method']==r['method'] and abs((_day(p)-_day(r)).days)<=60]
        linked = candidates[0] if len(candidates)==1 and not competing else None
        debts.append(dict(id=r['id'], name=r['name'], principal=r['amount'], start_date=r['date'],
                          repayment_stream_id=linked, repayment_candidates=candidates,
                          link_basis='new_stream_same_account_after_inflow' if linked else None,
                          remaining=None, remaining_basis='principal_interest_split_missing'))
    from ledger_loan import resolve_facts, enrich
    owner_facts = resolve_facts(c, debts, owner_facts)
    balances = owner_facts
    from ledger_obligation import term
    for debt in debts:
        debt.update(term(c,debt['repayment_stream_id']))
        balance = balances.get('debt:'+str(debt['id']))
        if balance and balance.get('fixed_id'):
            debt['repayment_stream_id']=balance['fixed_id']
            debt.update(term(c,balance['fixed_id']))
        if balance and balance.get('amount') is not None and balance['as_of'] <= today.isoformat():
            debt.update(remaining=balance['amount'], remaining_as_of=balance['as_of'], remaining_basis='owner_principal_balance')
    debts = enrich(c, debts, owner_facts, today.isoformat())
    meta = R.annotations(c)
    actuals = [r for month in result for r in D._month_rows(c, month['month'])
               if r['date'] <= today.isoformat()]
    visible_ids = {r['id'] for r in actuals}
    for debt in debts:
        debt['display_payment_count'] = len(visible_ids.intersection(debt.get('payment_ids', [])))
        debt['total_payment_count'] = len(debt.get('payment_ids', []))
    salary_reads = [dict(id=r['id'], date=r['date'], name=r['name'], amount=r['amount'],
                         basis=meta[r['id']]['salary_evidence'])
                    for r in actuals if r['kind']=='income' and r['class']=='income'
                    and meta.get(r['id'], {}).get('salary_evidence')]
    # A material payment to a person can distort an otherwise ordinary month.
    # This identifies an unexplained outflow, never its purpose or debt nature.
    anomalies = [dict(id=r['id'], date=r['date'], name=r['name'], amount=r['amount'], method=r['method'])
                 for r in actuals if r['kind']=='expense' and r['class']=='spend' and r['amount']>=1000000
                 and R.received_person(c, r) and not meta.get(r['id'], {}).get('checked')
                 and not meta.get(r['id'], {}).get('history_reviewed')]
    # One explicit repayment is loan evidence too, even without a recurring
    # stream. This flag invents no borrowing identity or principal balance.
    loan_present = bool(debts or c.execute(
        "SELECT 1 FROM spend_entry WHERE scope<>'business' AND kind='expense' "
        "AND class='loan' AND amount>0 AND date<=? LIMIT 1", (today.isoformat(),)).fetchone())
    import ledger_money_flow
    return dict(money_flow=ledger_money_flow.summary(c, today), months=result, window=w, projected=bool(forecast), debts=debts, loan_present=loan_present, salary_reads=salary_reads,
                business_present=business_present,
                anomalies=sorted(anomalies, key=lambda r: (-r['amount'], r['date'], r['id'])),
                total_gap=sum(r['gap'] for r in result))
