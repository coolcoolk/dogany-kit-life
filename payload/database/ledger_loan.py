# -*- coding: utf-8 -*-
"""Loan identities and payment shapes; principal balances are owner facts.

An unlinked recurring repayment has the stable id -fixed_cost.id. It is a
loan to ask about, never an invented borrowing transaction. Guesses are
read-only; owner answers (including unknown) take precedence on every read.
"""
import collections
import calendar
import datetime
import decimal
import json
import re

TYPES = ('equal_payment', 'equal_principal', 'bullet', 'credit_line', 'unknown')
WORDS = (
    ('equal_payment', r'원리금\s*균등|equal[_ ]payment'),
    ('equal_principal', r'원금\s*균등|equal[_ ]principal'),
    ('bullet', r'만기\s*일시|이자만|interest.only|\bbullet\b'),
    ('credit_line', r'한도\s*대출|마이너스\s*(?:통장|대출)|credit[_ ]line|overdraft'),
)


def balance_from_payments(data, payments=()):
    """Subtract stated repaid principal, or verified interest-free payments.

    The payment-count branch requires a stated count or a complete run.
    Scheduled months alone do not establish that any payment was made.
    Gross payments on interest-bearing loans cannot establish principal.
    """
    principal, count, payment = (data.get(k) for k in ('original_principal', 'payments_made', 'payment'))
    if 'repaid_principal' in data:
        # This is the repaid part of the displayed principal, not a sum of
        # gross monthly repayments (which may include interest).
        repaid = data['repaid_principal']
        if (type(principal) is not int or principal <= 0 or type(repaid) is not int
                or not 0 <= repaid <= principal or not data.get('as_of')):
            raise ValueError('principal subtraction needs dated original and repaid principal')
        amount = principal - repaid
        if data.get('amount') is not None and data['amount'] != amount:
            raise ValueError('supplied balance disagrees with principal subtraction')
        return amount, dict(original_principal=principal, repaid_principal=repaid,
                            as_of=data['as_of'], basis='original_minus_repaid_principal')
    if count is None and data.get('first_payment_on') and data.get('as_of'):
        first = datetime.date.fromisoformat(data['first_payment_on']).isoformat()
        through = datetime.date.fromisoformat(data['as_of']).isoformat()
        observed = [r for r in payments if first <= r['date'] <= through]
        months = [r['date'][:7] for r in observed]
        last = max(months, default=first[:7])
        expected = index(last)-index(first)+1
        # The current calendar month need not have been paid yet. Require
        # an uninterrupted observed run through at least the previous month;
        # never subtract a scheduled or missing payment.
        if (not 0 <= index(through)-index(last) <= 1
                or expected <= 0 or len(months) != expected or len(set(months)) != expected
                or any(r['amount'] != payment for r in observed)):
            raise ValueError('payment-count balance needs complete observed monthly repayments')
        count = len(observed)
    if (data.get('annual_rate') != 0 or type(data.get('annual_rate')) not in (int, float)
            or type(principal) is not int or principal <= 0
            or type(count) is not int or count < 0
            or type(payment) is not int or payment <= 0
            or count*payment > principal or not data.get('as_of')):
        raise ValueError('payment-count balance needs dated interest-free principal, payment and count')
    amount = principal-count*payment
    if data.get('amount') is not None and data['amount'] != amount:
        raise ValueError('supplied balance disagrees with payment-count evidence')
    return amount, dict(original_principal=principal, payments_made=count,
                        payment=payment, annual_rate=0, as_of=data['as_of'],
                        **(dict(first_payment_on=data['first_payment_on'],
                                payment_ids=[r['id'] for r in observed]) if 'first_payment_on' in data and data.get('payments_made') is None else {}))


def estimate_maturity(balance, latest_payment=0):
    """Amortize a dated balance; annual_rate is percent, payment is whole won.

    The balance follows that day's payments. next_payment_on may explicitly
    anchor a balance read before a same-day payment. No adapter-chosen month.
    """
    if balance.get('repayment_type') in ('bullet', 'credit_line', 'equal_principal', 'unknown'):
        return None
    if balance.get('amount') is None or balance.get('annual_rate') is None or not balance.get('as_of'):
        return None
    rate = balance['annual_rate']
    payment = balance.get('payment', latest_payment)
    day = balance.get('payment_day')
    if (type(rate) not in (int, float) or not decimal.Decimal(str(rate)).is_finite()
            or not 0 <= rate <= 100 or type(payment) is not int or payment <= 0
            or type(day) is not int or not 1 <= day <= 31):
        raise ValueError('estimate needs annual percent rate, positive payment and payment day 1..31')
    as_of = datetime.date.fromisoformat(balance['as_of'])
    next_on = balance.get('next_payment_on')
    if next_on:
        due = datetime.date.fromisoformat(next_on)
        if due < as_of or due.day != min(day, calendar.monthrange(due.year, due.month)[1]):
            raise ValueError('next payment must agree with payment day and balance date')
    else:
        due = as_of.replace(day=min(day, calendar.monthrange(as_of.year, as_of.month)[1]))
        if due <= as_of:
            y, m = divmod(as_of.year*12+as_of.month, 12)
            due = datetime.date(y, m+1, min(day, calendar.monthrange(y, m+1)[1]))
    principal = decimal.Decimal(balance['amount'])
    monthly_rate = decimal.Decimal(str(rate))/1200
    if principal <= 0 or principal*monthly_rate >= payment:
        return None
    for n in range(1, 1201):
        principal = principal*(1+monthly_rate)-payment
        if principal <= 0:
            y, m = divmod(due.year*12+due.month-1+n-1, 12)
            end = datetime.date(y, m+1, min(day, calendar.monthrange(y, m+1)[1]))
            return dict(ends_on=end.isoformat(), provenance='estimated', payments=n,
                        amount=balance['amount'], as_of=as_of.isoformat(), annual_rate=rate,
                        payment=payment, payment_day=day, next_payment_on=due.isoformat())
    return None


def owner_type(data):
    # The agent supplies one typed per-loan value. Words are provenance only.
    stated = data.get('repayment_type')
    if stated is not None and stated not in TYPES:
        raise ValueError('unknown repayment type')
    return stated


def resolve_facts(c, debts, facts):
    """Carry orphaned owner balances across replacement transaction ids.

    A unique observed repayment stream must independently identify one current
    borrowing. Live/reused old ids, competing borrowings and conflicting
    balances on the same date cannot establish that identity. This is a read
    projection; original owner words and as-of dates remain in their store.
    """
    from ledger_diag import norm
    # Replacement restarts retired older plan facts as well as replacing ids.
    # A retired balance needs the owner's named loan in addition to the
    # independently verified unique repayment stream; explicit retirement notes
    # and a different named loan cannot be used to recover a balance.
    retired = {r[0]: json.loads(r[1]) for r in c.execute(
        "SELECT item,detail FROM ledger_preference WHERE topic='ledger_plan_fact' "
        "AND status='retired' AND source='owner' AND note IS NULL AND item LIKE 'debt:%'")}
    available = dict(retired, **facts)
    result = dict(facts)
    live = {r[0] for r in c.execute('SELECT id FROM spend_entry')}
    for debt in debts:
        fid = debt['repayment_stream_id']
        if (debt['id'] <= 0 or not fid
                or debt.get('link_basis') != 'new_stream_same_account_after_inflow'
                or debt.get('repayment_candidates') != [fid]
                or sum(d['repayment_stream_id'] == fid for d in debts) != 1):
            continue
        key = 'debt:' + str(debt['id'])
        current = facts.get(key, {})
        if current.get('amount') is not None:
            continue
        sources = [(k, f) for k, f in available.items() if k.startswith('debt:')
                   and k[5:].isdigit() and int(k[5:]) not in live
                   and f.get('fixed_id') == fid and f.get('amount') is not None
                   and f.get('as_of')
                   and (k in facts or (norm(debt['name']) and norm(debt['name']) in
                        norm(f.get('carried_said') or f.get('said', ''))))]
        if not sources:
            continue
        latest = max(f['as_of'] for _, f in sources)
        sources = [(k, f) for k, f in sources if f['as_of'] == latest]
        if len({f['amount'] for _, f in sources}) != 1:
            continue
        old_key, prior = sources[0]
        result[key] = dict(prior, **current)
        result[key].update(id=debt['id'], fixed_id=fid, carried_from=old_key,
                           carried_from_status='active' if old_key in facts else 'retired',
                           carried_said=prior.get('carried_said', prior.get('said')))
    return result


def guess(name, payments, principal):
    if re.search(WORDS[3][1], name, re.I):
        return 'credit_line'
    amounts = list(payments.values())[-6:]
    if len(amounts) < 3 or any(a <= 0 for a in amounts):
        return 'unknown'
    if max(amounts)-min(amounts) <= 1:
        return 'equal_payment'
    falls = [a-b for a,b in zip(amounts, amounts[1:])]
    if min(falls) > 0 and max(falls) <= min(falls)*1.25:
        return 'equal_principal'
    if principal and max(amounts) < principal*.02:
        return 'bullet'
    return 'unknown'


def enrich(c, debts, facts, today):
    from ledger_names import Names
    names = Names(c)
    from ledger_diag import rows, norm
    from ledger_profile import merchant_key
    from ledger_obligation import term
    streams = rows(c, "SELECT * FROM fixed_cost WHERE class='loan' AND direction='expense' AND state<>'stopped' ORDER BY id")
    actual = rows(c, "SELECT id,date,name,method,amount,stream_id FROM spend_entry WHERE kind='expense' AND class='loan' AND scope<>'business' AND date<=? ORDER BY date,id", (today,))
    def family(row):
        return merchant_key(row['name']) or norm(row['name']), row['method']
    families = collections.defaultdict(set)
    for row in actual:
        if row['stream_id']:
            families[family(row)].add(row['stream_id'])
    # Current-month bills can lack stream ids until the next reconciliation.
    # Recover only a unique observed merchant/account identity, read-only.
    for row in actual:
        candidates = families[family(row)]
        if row['stream_id'] is None and len(candidates)==1:
            row['stream_id'] = next(iter(candidates))
    linked = {d['repayment_stream_id'] for d in debts if d['repayment_stream_id']}
    for stream in streams:
        if stream['id'] not in linked:
            debts.append(dict(id=-stream['id'], name=stream['name'], principal=None,
                              start_date=None, repayment_stream_id=stream['id'],
                              repayment_candidates=[stream['id']], link_basis='unlinked_repayment_stream',
                              remaining=None, remaining_basis='principal_interest_split_missing',
                              **term(c,stream['id'])))
    for debt in debts:
        debt.update(names.fields(debt['name']))
        fid = debt['repayment_stream_id']
        stream = next((s for s in streams if s['id']==fid), None)
        payments = collections.Counter()
        members = [r for r in actual if fid and r['stream_id']==fid]
        for row in members:
            payments[row['date'][:7]] += row['amount']
        latest = list(payments.values())[-1] if payments else (stream['current_amount'] if stream and stream['current_amount'] is not None else stream['amount'] if stream else 0)
        balance = facts.get('debt:'+str(debt['id']), {})
        if balance.get('amount') is not None and balance['as_of'] <= today:
            debt.update(remaining=balance['amount'],remaining_as_of=balance['as_of'],remaining_basis='owner_principal_balance')
            if balance.get('principal_calculation'):
                debt.update(remaining_basis=balance['principal_calculation'].get('basis', 'interest_free_payment_count'),
                            principal_calculation=balance['principal_calculation'])
        predicted = guess(debt['name']+' '+(stream['name'] if stream else ''), payments, debt['principal'])
        debt.update(latest_payment=latest, payment_ids=[r['id'] for r in members], repayment_guess=predicted,
                    payment_account=members[-1]['method'] if members else None,
                    payment_count=len(members), payment_months=list(payments.items()),
                    repayment_type=balance.get('repayment_type', predicted),
                    repayment_type_source='owner' if 'repayment_type' in balance else 'guessed')
    return debts


def index(month):
    y, m = map(int,month[:7].split('-'))
    return y*12+m-1


def baselines(loans, payments, projected, n):
    """Allocate the rounded legacy average once, including its residual.

    Independent per-loan rounding can leave a phantom won after every loan
    matures. Largest remainders preserve the original total exactly.
    """
    actual = [r for r in payments if r['kind']=='expense' and r['class']=='loan']
    pending = [r for r in projected if r['class']=='loan']
    amounts = [sum(r['amount'] for r in actual if r['id'] in loan['payment_ids']) +
               sum(r['amount'] for r in pending if loan['repayment_stream_id'] and r.get('stream_id')==loan['repayment_stream_id']) for loan in loans]
    total = sum(r['amount'] for r in actual+pending)
    amounts.append(total-sum(amounts))  # Unlinked one-off repayments survive.
    shares = [a//n if n else 0 for a in amounts]
    extra = round(total/n)-sum(shares) if n else 0
    if n:
        for i in sorted(range(len(amounts)),key=lambda i:(-(amounts[i]%n),i))[:extra]:
            shares[i] += 1
    for loan, share in zip(loans,shares):
        loan['baseline_monthly'] = share


def payment(loan, month):
    """Return a typed payment, or None to preserve the legacy forecast.

    For equal principal, infer the current interest component from the latest
    payment minus balance/remaining months, then scale it by remaining
    principal. An impossible negative interest component stays unknown.
    No observed gross repayment is subtracted from a principal balance.
    """
    kind = loan['repayment_type']
    latest = loan['latest_payment']
    end = loan.get('ends_on')
    if kind == 'unknown':
        return None
    if kind == 'credit_line':
        return dict(amount=latest, principal_due=0)
    if end and month > end[:7]:
        return dict(amount=0, principal_due=0)
    if kind == 'equal_payment':
        return dict(amount=latest, principal_due=0)
    if not end:
        return None
    principal = loan.get('remaining')
    anchor = loan.get('remaining_as_of')
    if principal is None or not anchor or month < anchor[:7]:
        return None
    if kind == 'bullet':
        due = principal if month == end[:7] else 0
        return dict(amount=latest+due, principal_due=due)
    months = index(end)-index(anchor)+1
    if months <= 0:
        return None
    share = principal/months
    if latest < share:
        return None
    elapsed = index(month)-index(anchor)
    interest = (latest-share)*max(0,months-elapsed)/months
    return dict(amount=round(share+interest), principal_due=0)


def forecast(plan, month):
    """Replace only identified typed streams; retain all legacy residuals."""
    delta = 0
    oneoffs = []
    for loan in plan.get('loans', []):
        shaped = payment(loan, month)
        unit = plan.get('loan_payment_unit', 1)
        if shaped is None and plan.get('loan_payment_unit'):
            # A missing repayment shape does not turn the current bill back
            # into a historical average. Do not infer principal or interest.
            shaped = dict(amount=loan['latest_payment'], principal_due=0)
            if loan.get('ends_on') and month > loan['ends_on'][:7]:
                shaped['amount'] = 0
        if shaped is None:
            continue
        legacy = loan['baseline_monthly']
        delta += round(shaped['amount']/unit)*unit-legacy
        if shaped['principal_due']:
            oneoffs.append(dict(id=loan['id'],name=loan['name'],month=month,amount=shaped['principal_due'],repayment_type='bullet'))
    return dict(delta=delta, oneoffs=oneoffs)
