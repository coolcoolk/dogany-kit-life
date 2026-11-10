"""Actual-month review data and atomic owner corrections, never estimates.

Review annotations preserve imported amounts. Reimbursement links carry both
row ids and are subtracted by ledger_diag._month_rows, like settlement shares.
"""
import collections
import datetime
import json
import itertools
import re

import ledger_diag as D
import ledger_profile as LP

FIXED = ('housing', 'loan', 'tax', 'subscription', 'telecom', 'insurance', 'utility', 'other')
CYCLE_MONTHS = {'monthly': 1, 'bimonthly': 2, 'quarterly': 3, 'semiannual': 6, 'yearly': 12}
YEARLY_WORDS = ('\uc5f0\ud68c\ube44', 'annual fee', 'yearly fee')
TOPIC = 'ledger_review_item'
PAIR_DAYS = 14
PAIR_LIMIT = 3
PAIR_SHARE_DENOMINATORS = (2, 3, 4, 5)
PAIR_SHARE_TOLERANCE_PERCENT = 5


def pair_spends(c, received, minimum=None):
    """Prior personal spends with enough uncredited money, including prior months.

    The same eligibility check feeds suggestions and the write handler. Suggestions
    are possibilities, never evidence that the owner shared a payment.
    """
    minimum = received['amount'] if minimum is None else minimum
    found = D.rows(c, "SELECT s.*,s.amount-COALESCE(r.amount,0)-" + reimbursement_sql('s') +
                  " AS remaining FROM spend_entry s LEFT JOIN v_ledger_reimbursement r "
                  "ON r.spend_id=s.id WHERE s.kind='expense' AND COALESCE(s.class,'spend')='spend' "
                  "AND s.scope<>'business' AND s.date<=? AND s.amount>=? AND remaining>=?",
                  (received['date'], minimum, minimum))
    rejected = annotations(c).get(received['id'], {}).get('allocation_rejected_expenses', [])
    return [r for r in found if r['id'] not in rejected]


def pair_time(row):
    from ledger_v6 import minutes
    time = minutes(row.get('at_time') or '')
    if time is None:
        return None
    return (datetime.datetime.combine(datetime.date.fromisoformat(row['date']), datetime.time())
            + datetime.timedelta(minutes=time))


def pair_session(parts):
    times = [pair_time(p) for p in parts]
    known = [t for t in times if t is not None]
    if known and max(known) - min(known) > datetime.timedelta(hours=6):
        return False
    if all(t is not None for t in times):
        return max(times) - min(times) <= datetime.timedelta(hours=6)
    dates = [datetime.date.fromisoformat(p['date']) for p in parts]
    return (max(dates) - min(dates)).days <= 1


def pair_share_fit(spend, received):
    """Within 5% of the ORIGINAL payment's 1/2..1/5, never its remainder.

    Integer cross multiplication keeps the inclusive boundary exact. Rank
    fits as a group; a smaller rounding error is not evidence for one shop.
    """
    if spend['amount'] <= 0 or received['amount'] <= 0:
        return None
    for denominator in PAIR_SHARE_DENOMINATORS:
        error = abs(received['amount'] * denominator - spend['amount'])
        if error * 100 <= spend['amount'] * PAIR_SHARE_TOLERANCE_PERCENT:
            return dict(kind='share_fit', numerator=1, denominator=denominator,
                        amount=spend['amount'], date=spend['date'],
                        received_amount=received['amount'],
                        tolerance_percent=PAIR_SHARE_TOLERANCE_PERCENT)
    return None


def fixed_class(row, stream, annotation, is_fixed):
    """Effective review classification, including owner overrides and streams."""
    # An observed past charge keeps its known category after cancellation.
    # Stopping affects future obligations, not what a paid row was for.
    cls = stream.get('class')
    if row['class'] == 'loan':
        cls = 'loan'
    elif row.get('category_override'):
        return 'subscription' if row['category_override'][0]=='subscription' else None
    inferred = D._class_of([row], LP.merchant_key(row['name']))
    if not cls and (is_fixed or inferred in ('housing', 'loan', 'tax', 'telecom', 'insurance', 'utility')):
        cls = inferred
    if not cls and LP._has(row['name'].casefold(), YEARLY_WORDS):
        cls = 'subscription'
    if inferred == 'transport':
        # Repeated recharge streams use the existing other class. A stale
        # telecom guess and an isolated top-up still have no fixed evidence.
        cls = 'other' if stream.get('class')=='other' else None
    elif cls == 'telecom' and '인터넷상거래' in row['name']:
        cls = None
    if D.is_taxi(row['name']) and stream.get('source')=='ledger_v6':
        cls = None
    role = annotation.get('role')
    if role in FIXED:
        cls = role
    elif role in ('living', 'one_off'):
        cls = None
    return cls


def pair_candidates(c, received):
    date = datetime.date.fromisoformat(received['date'])
    streams = {r['id']: r for r in D.rows(c, 'SELECT * FROM fixed_cost')}
    meta = annotations(c)
    nearby = [r for r in pair_spends(c, received, minimum=LP.DUST_KRW)
              if (date - datetime.date.fromisoformat(r['date'])).days <= PAIR_DAYS
              and not (pair_time(r) and pair_time(received) and pair_time(r) > pair_time(received))
              and fixed_class(r, streams.get(r['stream_id'], {}), meta.get(r['id'], {}), r['is_fixed']) not in FIXED]
    def candidate(r):
        return dict(id=r['id'], date=r['date'], at_time=r['at_time'], amount=r['amount'], remaining=r['remaining'],
                    display_name=D._member_name(r['name'], c) or r['name'], share_fit=pair_share_fit(r, received))
    singles = [candidate(r) for r in nearby if r['amount'] >= received['amount'] and r['remaining'] >= received['amount']]
    # Existing shares require choosing a person in the allocation editor. Keep
    # those singles available, but do not invent a group allocation for them.
    shared = {r['spend_id'] for r in D.rows(c, 'SELECT spend_id FROM ledger_share')}
    group_rows = sorted((r for r in nearby if r['id'] not in shared), key=lambda r: (r['date'], r['at_time'] or '', r['id']))
    groups = []
    for index, first in enumerate(group_rows):
        session = [r for r in group_rows[index+1:] if pair_session([first, r])]
        for size in (2, 3):
            for tail in itertools.combinations(session, size-1):
                parts = [first, *tail]
                if not pair_session(parts):
                    continue
                total = sum(p['amount'] for p in parts)
                fit = pair_share_fit(dict(amount=total, date=parts[-1]['date']), received)
                if not fit:
                    continue
                # Proportional integer won reconcile exactly even for rounded
                # transfers; capacity remains authoritative at confirmation.
                amounts = [received['amount'] * p['amount'] // total for p in parts]
                amounts[-1] += received['amount'] - sum(amounts)
                if any(n <= 0 or n > p['remaining'] for n, p in zip(amounts, parts)):
                    continue
                groups.append(dict(id='group:' + ','.join(str(p['id']) for p in parts), date=parts[-1]['date'],
                                   at_time=parts[-1]['at_time'], amount=total, share_fit=fit,
                                   display_name='/'.join(p['name'] for p in parts), parts=[candidate(p) for p in parts],
                                   allocations=[dict(expense_id=p['id'], amount=n, payer='owner') for p,n in zip(parts, amounts)]))
    candidates = singles + groups
    unique_fit = sum(bool(r['share_fit']) for r in candidates) == 1
    def rank(r):
        time = pair_time(r) or datetime.datetime.combine(datetime.date.fromisoformat(r['date']), datetime.time())
        ids = tuple(p['id'] for p in r.get('parts', [r]))
        return (not bool(r['share_fit']), -time.toordinal(), -time.hour, -time.minute, ids)
    candidates.sort(key=rank)
    for r in candidates:
        r['recommended'] = bool(r['share_fit']) and unique_fit
    # Groups must not crowd all the plausible individual payments off the card.
    shown = candidates[:PAIR_LIMIT]
    if singles and not any('parts' not in r for r in shown):
        shown[-1] = sorted(singles, key=rank)[0]
    return shown


def reimbursement_sql(alias):
    """Owner-confirmed links shared by diagnosis and the kit's monthly readers."""
    if alias not in ('s', 'e', 'spend_entry'):
        raise ValueError('unsupported spend alias')
    legacy = ("COALESCE((SELECT SUM(json_extract(p.detail,'$.amount')) FROM ledger_preference p "
              "WHERE p.topic='ledger_review_item' AND p.status='active' "
              "AND COALESCE(json_extract(p.detail,'$.allocation_mode'),0)=0 "
              "AND CAST(json_extract(p.detail,'$.spend_id') AS INTEGER)=" + alias + ".id "
              "AND EXISTS (SELECT 1 FROM spend_entry t WHERE t.id=CAST(p.item AS INTEGER))),0)")
    direct = ("COALESCE((SELECT SUM(CASE WHEN a.payer='friend' THEN -a.amount ELSE a.amount END) "
              "FROM ledger_receipt_allocation a WHERE a.expense_id=" + alias +
              ".id AND a.share_id IS NULL),0)")
    friend = ("CASE WHEN EXISTS(SELECT 1 FROM ledger_preference f WHERE f.topic='ledger_review_item' "
              "AND f.status='active' AND CAST(f.item AS INTEGER)=" + alias +
              ".id AND json_extract(f.detail,'$.settlement_payer')='friend') OR (" + alias +
              ".amount<0 AND EXISTS(SELECT 1 FROM spend_entry original JOIN ledger_preference f ON CAST(f.item AS INTEGER)=original.id "
              "AND f.topic='ledger_review_item' AND f.status='active' AND json_extract(f.detail,'$.settlement_payer')='friend' "
              "WHERE original.amount>0 AND original.pair_id=" + alias + ".pair_id)) THEN " + alias + ".amount ELSE 0 END")
    return '(' + legacy + '+' + direct + '+' + friend + ')'



def annotations(c):
    return {int(r['item']): json.loads(r['detail']) for r in D.rows(c,
            "SELECT item,detail FROM ledger_preference WHERE topic=? AND status='active'", (TOPIC,))}


def received_person(c, r):
    from ledger_v6 import PERSON_RX, norm, _json_list
    if annotations(c).get(r['id'], {}).get('salary_evidence'):
        return False
    # A short issuer label can fit PERSON_RX's Hangul-name shape. An
    # institutional card credit is not evidence of a person paying a share;
    # leave its imported accounting and subsequent review intact.
    if re.search(r'\uce74\ub4dc(?:\uc0ac)?$|\bcard$|\bcashback\b|\uce90\uc2dc\ubc31',
                 (r['name'] or '').strip(), re.I):
        return False
    names = set()
    for p in D.rows(c, 'SELECT name,display_name,aliases FROM persons'):
        names.update(norm(n) for n in [p['name'], p['display_name']] + _json_list(p['aliases']) if n)
    if norm(r['name']) in names:
        return True
    if any(w in (r['src_cat'] or '') for w in ('\uae09\uc5ec', '\uae08\uc735\uc218\uc785')):
        return False
    return bool(PERSON_RX.match(r['name'] or ''))


INSURERS = ('insurance', '\ubcf4\ud5d8', '\ud55c\ud654\uc190', '\uc0bc\uc131\ud654\uc7ac', '\ud604\ub300\ud574\uc0c1', '\uba54\ub9ac\uce20\ud654\uc7ac')
MATERIAL_CARD_GAP = 10000


def classify_insurance(c):
    """Identified insurer receipts are reimbursements, never merchant refunds."""
    owner = annotations(c)
    for r in D.rows(c, "SELECT id,name,src_cat,kind FROM spend_entry WHERE (kind='income' AND amount>0) OR (kind='expense' AND amount<0)"):
        if owner.get(r['id'], {}).get('owner_class') is not None:
            continue
        source = (r['name'] + ' ' + (r['src_cat'] or '')).casefold()
        if LP._has(source, INSURERS) and not LP._has(r['src_cat'] or '', ('salary', '급여')):
            if r['kind'] == 'income':
                c.execute("UPDATE spend_entry SET kind='transfer',class='own_transfer',stream_id=NULL WHERE id=?", (r['id'],))
            put(c, r['id'], dict(insurance_receipt=True))


def insurance_claims(c):
    return [json.loads(r[0]) for r in c.execute("SELECT detail FROM ledger_preference WHERE topic='insurance_claim' AND status='active' ORDER BY id")]


def insurance_answer(c, data):
    """A stated reimbursement stays pending until an actual receipt is identified."""
    amount = data.get('amount')
    if type(amount) is not int or amount <= 0:
        raise ValueError('insurance amount must be positive integer won')
    receipt_id = data.get('receipt_id')
    candidates = D.rows(c, "SELECT s.* FROM spend_entry s JOIN ledger_preference p ON CAST(p.item AS INTEGER)=s.id AND p.topic='ledger_review_item' AND p.status='active' WHERE ABS(s.amount)=? AND json_extract(p.detail,'$.insurance_receipt')=1", (amount,))
    matches = [r for r in candidates if receipt_id is None or r['id'] == receipt_id]
    spend_id = data.get('spend_id')
    matched = len(matches) == 1
    linked = matched and spend_id is not None
    if linked:
        receipt = dict(matches[0], amount=abs(matches[0]['amount']))
        if not any(r['id'] == spend_id for r in pair_spends(c, receipt)):
            raise ValueError('insurance link needs an eligible prior medical expense')
        expense = D.rows(c, 'SELECT * FROM spend_entry WHERE id=?', (spend_id,))[0]
        if not LP._has((expense['name']+' '+(expense['src_cat'] or '')).casefold(), ('medical','dental','hospital','\uce58\uacfc','\ubcd1\uc6d0','\uc758\uc6d0','\uc758\ub8cc')):
            raise ValueError('insurance link needs a medical expense')
        put(c, receipt['id'], dict(spend_id=spend_id, amount=amount, insurance=True))
    detail = dict(amount=amount, said=data.get('said'), spend_id=spend_id,
                  receipt_id=matches[0]['id'] if matched else None,
                  status='linked' if linked else 'matched' if matched else 'pending', counted=linked)
    key = 'insurance-claim-%s-%s' % (amount, spend_id or 'unknown')
    if linked:
        c.execute("UPDATE ledger_preference SET status='superseded' WHERE topic='insurance_claim' AND ulid=?",('insurance-claim-%s-unknown'%amount,))
    c.execute("INSERT INTO ledger_preference(ulid,item,polarity,status,source,recorded_at,topic,detail) "
              "VALUES(?,?,'negotiable','active','owner',?,'insurance_claim',?) "
              "ON CONFLICT(ulid) DO UPDATE SET detail=excluded.detail", (key,key,datetime.datetime.now().isoformat(),json.dumps(detail)))
    return detail


GENERIC_ACCOUNTS = {'입출금통장', '입출금', '보통예금', '통장', '계좌', '예금', '저축예금',
                    '입출금계좌', '자유입출금통장', 'account', 'checking', 'savings'}


def method_display(row):
    if row['kind'] == 'account' and re.sub(r'\s+', '', row['name']).casefold() in GENERIC_ACCOUNTS:
        # Stable registration id disambiguates even when no bank was imported.
        return row['name'] + ' (#%s)' % row['id']
    return row['name']


def _funding_observations(c, methods):
    """Count the stored payment/top-up pairs, excluding the refund leg.

    first_seen/last_seen do not encode frequency (the importer can write the
    same date for many observations). Replays and cancellations must not turn
    one payment into two observations. Canonical method ids include aliases.
    """
    observations = collections.defaultdict(dict)
    pairs = D.rows(c, "SELECT s.id,s.method AS source,w.method AS wallet,w.date "
                      "FROM spend_entry s JOIN spend_entry w ON w.pair_id=s.pair_id AND w.id<>s.id "
                      "WHERE s.class IN ('topup','own_transfer') AND w.class IN ('topup','spend') "
                      "AND w.amount>0")
    for pair in pairs:
        wid, sid = methods.names.get(pair['wallet']), methods.names.get(pair['source'])
        if wid and sid:
            observations[(methods.canon(wid), methods.canon(sid))][pair['id']] = pair['date']
    return observations


def roster(c, today=None):
    methods = D.rows(c, "SELECT * FROM pay_method WHERE canonical_id IS NULL ORDER BY id")
    from ledger_instrument_facts import records
    facts = records(c)
    groups = {k:[] for k in ('card','wallet','account','saving')}
    for r in methods:
        group = 'saving' if r['kind'] in ('savings','securities') else r['layer'] or ('card' if r['kind'] in ('credit_card','corporate_card','debit_card') else 'account')
        groups[group].append(dict(id=r['id'],name=r['name'],kind=r['kind'],display_name=method_display(r),
                                  instrument_facts=facts.get(r['id'])))
    from ledger_v6 import Methods, _json_list
    methods_by_name = Methods(c)
    observations = _funding_observations(c, methods_by_name)
    edges = D.rows(c, "SELECT a.name AS wallet,b.name AS source,a.kind,f.wallet_id,f.source_id,f.last_seen,"
                   "COALESCE(p.source,'learned') AS origin "
                   "FROM pay_funding f JOIN pay_method a ON a.id=f.wallet_id JOIN pay_method b ON b.id=f.source_id "
                   "LEFT JOIN ledger_preference p ON p.topic='pay_funding_source' AND p.status='active' "
                   "AND p.item=CAST(f.wallet_id AS TEXT)||':'||f.source_id||':'||f.sub")
    links = []
    # Preserve history, but omit a lone wallet payment outside the review window.
    from ledger_purpose import window
    since, until = window(today)
    ranked = []
    for edge in edges:
        seen = observations[(methods_by_name.canon(edge['wallet_id']), methods_by_name.canon(edge['source_id']))]
        seen = {sid: date for sid, date in seen.items() if date <= until}
        if edge['kind'] == 'easy_pay' and len(seen) == 1 and max(seen.values()) < since:
            continue
        last_seen = max([edge['last_seen'] or ''] + list(seen.values()))
        ranked.append((last_seen, edge))
    # A bank/card row whose merchant is an easy-pay wallet's own name (e.g. "토스페이"
    # on 토스뱅크 통장) is a payment through that wallet funded by that method.
    known = {(e['wallet'], e['source']) for _, e in ranked}
    for r in D.rows(c, "SELECT w.name AS wallet, m.name AS source, MAX(s.date) AS last_seen "
                       ",COUNT(*) AS payments FROM spend_entry s JOIN pay_method w ON w.kind='easy_pay' AND w.canonical_id IS NULL "
                       "AND s.name=w.name JOIN pay_method m ON m.name=s.method AND m.kind<>'easy_pay' "
                       "WHERE s.kind='expense' AND s.amount>0 AND s.date<=? GROUP BY w.name, m.name", (until,)):
        if r['payments'] == 1 and r['last_seen'] < since:
            continue
        if (r['wallet'], r['source']) not in known:
            ranked.append((r['last_seen'] or '', dict(wallet=r['wallet'], source=r['source'], origin='learned')))
    ranked.sort(key=lambda item: item[0], reverse=True)
    for _last_seen, edge in ranked:
        links.append({key: edge[key] for key in ('wallet', 'source', 'origin')})
    links += D.rows(c, "SELECT a.name AS wallet,b.name AS source,'learned' AS origin FROM pay_method a "
                    "JOIN pay_method b ON b.id=a.linked_account_id WHERE a.canonical_id IS NULL "
                    "AND NOT EXISTS(SELECT 1 FROM pay_funding f WHERE f.wallet_id=a.id AND f.source_id=b.id)")
    # Latest observed card settlement establishes the paying account. Never
    # guess an account from the issuer or use a historical account as current.
    bills = D.rows(c, "SELECT name,method,date FROM spend_entry WHERE class='card_bill' ORDER BY date DESC,id DESC")
    for card in groups['card']:
        if card['kind'] not in ('credit_card', 'corporate_card'):
            continue
        family = D.LP_family(card['name'])
        names = {card['name']}
        names.update(_json_list(methods_by_name.row(card['id'])['aliases']))
        unique_family = family and sum(D.LP_family(row['name']) == family for row in groups['card'] if row['kind'] in ('credit_card', 'corporate_card')) == 1
        matching = [row for row in bills if row['name'] in names or (unique_family and D.LP_family(row['name']) == family)]
        latest = matching[0]['date'] if matching else None
        sources = set()
        for bill in matching:
            if bill['date'] != latest:
                continue
            mid = methods_by_name.names.get(bill['method'])
            if mid:
                source = methods_by_name.row(methods_by_name.canon(mid))
                if source['layer'] == 'account':
                    sources.add(source['name'])
        if len(sources) == 1:
            links.append(dict(wallet=card['name'], source=next(iter(sources)), origin='card-bill'))
    visible = {r['name']: r for r in methods}
    links = [r for r in links if r['wallet'] in visible and r['source'] in visible]
    unique = {}
    for r in links:  # keep newest-first order; group by wallet in first-seen wallet order
        unique.setdefault((r['wallet'], r['source']), r)
    order = {}
    for w, _ in unique:
        order.setdefault(w, len(order))
    links = sorted(unique.values(), key=lambda r: order[r['wallet']])
    accounts = {r['name']: dict(r, aliases=_json_list(r['aliases']))
                for r in methods if r['kind'] == 'account'}
    unresolved = []
    for target in groups['card'] + groups['wallet']:
        row = methods_by_name.row(target['id'])
        if target['kind'] in ('credit_card', 'corporate_card', 'debit_card') and row['linked_account_id'] and methods_by_name.row(methods_by_name.canon(row['linked_account_id']))['name'] in visible:
            # An explicit current account supersedes historical settlement edges.
            current = methods_by_name.row(methods_by_name.canon(row['linked_account_id']))
            origin = next((link['origin'] for link in links if link['wallet'] == target['name']
                           and link['source'] == current['name']), 'learned')
            links = [link for link in links if link['wallet'] != target['name']]
            links.append(dict(wallet=target['name'], source=current['name'], origin=origin))
        if any(link['wallet'] == target['name'] for link in links):
            continue
        # A matching bank/product is only a suggestion. It is never a link
        # and this read path never writes it to either funding store.
        guess = LP.infer_link(target, accounts, {})
        unresolved.append(dict(target, guess_id=accounts[guess]['id'] if guess else None))
    return dict(groups=groups, links=links, unresolved=unresolved)



def living_proposal(c, month, living, missing):
    """Compare three recorded months; proposals change no money until confirmed."""
    history = D.rows(c, "SELECT * FROM spend_entry WHERE date>=? AND date<? AND kind='expense' AND amount>0 AND scope<>'business'", (D.prev_month(month,2)+'-01',D.prev_month(month,-1)+'-01'))
    months = collections.defaultdict(set)
    for r in history:
        months[LP.merchant_key(r['name'])].add(r['date'][:7])
    subscriptions = [r for r in missing if not LP.is_dust(r['amount']) and len(months[LP.merchant_key(r['name'])])>=2]
    subids = {r['id'] for r in subscriptions}
    # A large infrequent purchase is a proposal, never an automatic exclusion.
    one_off = [r for r in living if r['one_off'] or (r['id'] not in subids and r['amount']>=100000
               and len(months[LP.merchant_key(r['name'])])==1
               and LP._has(r['src_cat'] or r['category'] or '', ('shopping','health','\uc1fc\ud551','\uac74\uac15','\uc758\ub8cc','\uc758\ub958')))]
    return dict(months=[D.prev_month(month,n) for n in (2,1,0)], one_off=one_off, subscriptions=subscriptions,
                usual=sum(r['amount'] for r in living if not r['one_off'] and r['id'] not in subids and r['id'] not in {x['id'] for x in one_off}))


def card_gaps(c, items, month):
    import ledger_installment as installments
    cards = D.rows(c, "SELECT * FROM pay_method WHERE kind IN ('credit_card','corporate_card') AND canonical_id IS NULL")
    bills = collections.defaultdict(int)
    for r in items:
        if r['class']=='card_bill' and r['amount']:
            family = D.LP_family(r['name'])
            matches = [p for p in cards if family and D.LP_family(p['name'])==family]
            bills[matches[0]['name'] if len(matches)==1 else r['name']] += abs(r['amount'])
    known = installments.listing(c,month)['items']
    gaps = []
    for card,bill in bills.items():
        pm = next((p for p in cards if p['name']==card),None)
        # Statement dates must be supplied as evidence. A payment day alone
        # does not establish the card's purchase cutoff.
        statement = c.execute("SELECT detail FROM ledger_preference WHERE topic='card_statement' AND item=? AND status='active'", (card+':'+month,)).fetchone()
        period = json.loads(statement[0]) if statement else {}
        start,end = period.get('start',D.prev_month(month)+'-01'), period.get('end',month+'-01')
        from ledger_v6 import _json_list
        names = [card] + (_json_list(pm['aliases']) if pm else [])
        usage = sum(r['amount'] for r in D.rows(c,"SELECT amount,method,kind,class FROM spend_entry WHERE date>=? AND date<? AND scope<>'business'",(start,end)) if r['method'] in names and r['kind']=='expense' and r['class'] in D.OUT_CLASSES)
        due = [i for i in known if i['card']==card and i.get('this_month',0)>0]
        # Only linked purchases can be replaced by a scheduled charge without
        # risking counting an installment purchase twice.
        adjustment = 0
        for i in due:
            spend_id = i.get('spend_id')
            if spend_id:
                original = c.execute('SELECT amount,date FROM spend_entry WHERE id=?',(spend_id,)).fetchone()
                if original:
                    adjustment += i['this_month'] - (original[0] if start<=original[1]<end else 0)
        gap = bill-usage-adjustment
        purchases = sorted([dict(r, display_name=D._member_name(r['name'], c) or r['name']) for r in
                            D.rows(c, "SELECT * FROM spend_entry WHERE date>=? AND date<? AND kind='expense' "
                                      "AND amount>=100000 AND scope='personal'", (start, end))
                            if r['method'] in names], key=lambda r: -r['amount'])[:10]
        if bill:
            gaps.append(dict(card=card,bill=bill,usage=usage,gap=gap,prior=D.prev_month(month),
                             period_start=start,period_end=end,statement_known=bool(statement),
                             installment_adjustment=adjustment,installments=due,purchases=purchases,
                             material=gap>=MATERIAL_CARD_GAP and not LP.is_dust(gap)))
    return sorted(gaps,key=lambda r:-abs(r['gap']))


def build(c, month, totals_only=False):
    from ledger_names import Names
    names = Names(c)
    items = D._month_rows(c, month)
    meta = annotations(c)
    streams = {r['id']: r for r in D.rows(c, 'SELECT * FROM fixed_cost')}
    categories = {r['id']: r['display_name'] or r['name'] for r in D.rows(c, 'SELECT * FROM spend_category')}
    raw = {r['id']: r for r in D.rows(c, 'SELECT id,category_id,is_fixed FROM spend_entry WHERE substr(date,1,7)=?', (month,))}
    # Dust rows stay in `items`/totals; they never reach a prompt or evidence list.
    income = {k: [] for k in ('salary', 'received', 'other')}
    fixed = {k: [] for k in FIXED}
    past_keys = {LP.merchant_key(r['name']) for r in D.rows(c,
                 "SELECT name FROM spend_entry WHERE date<? AND kind='expense' AND scope<>'business'", (month + '-01',))}
    living, saving, people, odd, missing = [], [], [], [], []
    for r in items:
        r['display_name'] = D._member_name(r['name']) or r['name']
        if r['kind']=='expense' and r['class']=='spend':
            r.update(names.fields(r['name'], r['display_name']))
        r['dust'] = LP.is_dust(r['amount'])
        from ledger_actual import category
        merchant_category = r.get('category_override') or category(r['name'])
        r['category'] = merchant_category[1] if merchant_category else categories.get(raw[r['id']]['category_id'])
        a = meta.get(r['id'], {})
        if r['class'] == 'income':
            r['small_interest'] = 0 < r['amount'] <= 173 and LP._has(
                (r['name'] + ' ' + (r['src_cat'] or '')).casefold(), ('이자', 'interest'))
            person = received_person(c, r)
            group = 'salary' if '\uae09\uc5ec' in (r['src_cat'] or '') or a.get('salary_evidence') else 'received' if person or a.get('deposit_answer') == 'received' else 'other'
            income[group].append(r)
            if person and not a.get('income_confirmed') and not LP.is_dust(r['amount']):
                people.append(r)
        elif r['kind'] == 'expense' and r['class'] in D.OUT_CLASSES:
            f = streams.get(r['stream_id'], {})
            role = a.get('role')
            if r['class'] == 'saving':
                saving.append(r)
                continue
            cls = fixed_class(r, f, a, raw[r['id']]['is_fixed'])
            if cls in FIXED:
                r['owner_class'] = a.get('role') if a.get('role') in FIXED else None
                r['cycle'] = f.get('cycle') or 'monthly'
                r['cycle_months'] = CYCLE_MONTHS.get(r['cycle'], 1)
                r['pay_mode'] = f.get('pay_mode')
                r['pay_mode_source'] = f.get('pay_mode_source')
                fixed[cls].append(r)
                if not a.get('checked') and not LP.is_dust(r['amount']):
                    if cls == 'telecom' and LP._has(r['name'], ('\ud2f0\uba38\ub2c8', 'tmoney')):
                        odd.append(dict(r, odd='transit'))
                    elif a.get('cycle_pending') or cls == 'tax' or r['cycle'] != 'monthly' or LP._has(r['name'].casefold(), YEARLY_WORDS) or LP.merchant_key(r['name']) not in past_keys:
                        odd.append(dict(r, odd='cycle'))
            else:
                living.append(dict(r, one_off=role == 'one_off'))
                if not a.get('checked') and not LP.is_dust(r['amount']) and LP._has(LP.merchant_key(r['name']), LP.SUBSCRIPTION):
                    missing.append(r)
    groups = collections.defaultdict(lambda: {'amount': 0, 'places': collections.Counter(), 'items': []})
    for r in living:
        label = r['category']
        g = groups[label or 'uncategorized']
        g['category'] = label
        g['amount'] += r['amount']
        g['places'][r['display_name']] += r['amount']
        g['items'].append(r)
    if totals_only:
        inc = sum(r['amount'] for rs in income.values() for r in rs)
        fix = sum(r['amount'] for rs in fixed.values() for r in rs)
        save = sum(r['amount'] for r in saving)
        live = sum(r['amount'] for r in living)
        return dict(fixed=fixed, saving=saving, living=list(groups.values()),
                    totals=dict(income=inc, fixed=fix, saving=save, living=live, balance=inc-fix-save-live))
    verified_refunds, unverified_refunds, refund_candidates = refund_review(c, items, meta)
    unverified_refunds = [r for r in unverified_refunds if not LP.is_dust(r['amount'])]
    refund_candidates = [r for r in refund_candidates if not LP.is_dust(r['amount'])]
    gaps = card_gaps(c, items, month)
    inc = sum(r['amount'] for rs in income.values() for r in rs)
    fix = sum(r['amount'] for rs in fixed.values() for r in rs)
    save = sum(r['amount'] for r in saving)
    converted = [dict(r, monthly_amount=round(r['amount'] / r['cycle_months']))
                 for rs in fixed.values() for r in rs if r['cycle_months'] > 1]
    monthly_fixed = fix - sum(r['amount'] for r in converted) + sum(r['monthly_amount'] for r in converted)
    live = sum(r['amount'] for r in living)
    allocated = D.rows(c, "SELECT a.*,s.name,s.date,s.amount AS payment,s.method,s.pair_id FROM ledger_receipt_allocation a "
                          "JOIN spend_entry s ON s.id=a.expense_id WHERE substr(s.date,1,7)=? ORDER BY s.date,s.at_time,s.id", (month,))
    effective = {r['id']:r['amount'] for r in items}
    for a in allocated:
        a['owner_cost'] = effective[a['expense_id']]
        if a['payer']=='owner' and a['pair_id'] is not None:
            a['owner_cost'] += c.execute("SELECT COALESCE(SUM(amount),0) FROM spend_entry WHERE pair_id=? AND kind='expense' AND amount<0 AND substr(date,1,7)=? AND scope<>'business'", (a['pair_id'],month)).fetchone()[0]
    unallocated = D.rows(c, "SELECT t.*,t.amount-COALESCE((SELECT SUM(CASE WHEN a.payer='owner' THEN a.amount ELSE -a.amount END) "
                            "FROM ledger_receipt_allocation a WHERE a.receipt_id=t.id),0) AS unallocated FROM spend_entry t "
                            "JOIN ledger_preference p ON CAST(p.item AS INTEGER)=t.id AND p.topic='ledger_review_item' "
                            "AND p.status='active' AND json_extract(p.detail,'$.allocation_mode')=1 "
                            "WHERE substr(t.date,1,7)=? AND t.kind='transfer' AND t.class='own_transfer' AND t.amount>0 AND unallocated>0", (month,))
    unallocated = [r for r in unallocated if not LP.is_dust(r['unallocated'])]
    import ledger_budget
    import ledger_deposits
    return dict(month=month, cycles=list(CYCLE_MONTHS), monthly_caps=ledger_budget.read(c, month),
                unmatched_deposits=ledger_deposits.pending(c, month),
                unallocated_receipts=unallocated, receipt_allocations=allocated, income=income, fixed=fixed, living=sorted(groups.values(), key=lambda g: -g['amount']),
                saving=saving, converted=converted, people=people, odd=group_odd(odd), missing=missing,
                roster=roster(c), insurance=insurance_claims(c),
                insurance_receipts=[dict(r, amount=abs(r['amount'])) for r in items if r['class']=='insurance_reimbursement' and not LP.is_dust(r['amount'])],
                refund_candidates=group_odd(refund_candidates), unverified_refunds=unverified_refunds,
                living_proposal=living_proposal(c,month,living,missing),
                pair_candidates={str(r['id']): pair_candidates(c, r) for r in people},
                refunds=verified_refunds,
                refund_items=[dict(r, display_name=D._member_name(r['name'], c) or r['name']) for r in items
                              if r['kind']=='expense' and r['amount']<0 and r['class'] in D.OUT_CLASSES
                              and r['id'] not in {v['id'] for v in unverified_refunds}],
                gaps=gaps, totals=dict(income=inc, fixed=fix, monthly_fixed=monthly_fixed, saving=save, living=live, balance=inc-fix-save-live,
                                      usual=live-sum(r['amount'] for r in living if r['one_off'])))


MEDICAL_WORDS = ('medical', 'dental', 'hospital', '치과', '병원', '의원', '의료비')


def refund_review(c, items, meta):
    """Only evidenced reversals are merchant refunds; medical proximity asks once."""
    expenses = D.rows(c, "SELECT * FROM spend_entry WHERE kind='expense' AND amount>0 AND scope<>'business'")
    verified, unverified, candidates = 0, [], []
    for row in items:
        if row['kind'] != 'expense' or row['amount'] >= 0 or row['class'] not in D.OUT_CLASSES:
            continue
        annotation = meta.get(row['id'], {})
        prior = [e for e in expenses if e['date'] <= row['date'] and
                 (datetime.date.fromisoformat(row['date']) - datetime.date.fromisoformat(e['date'])).days <= 90]
        reversal = any(LP.merchant_key(e['name']) == LP.merchant_key(row['name']) and
                       (e['amount'] == -row['amount'] or (row['pair_id'] and row['pair_id'] == e['pair_id'])) for e in prior)
        method = c.execute("SELECT kind FROM pay_method WHERE name=? OR aliases=? OR EXISTS(SELECT 1 FROM json_each(CASE WHEN json_valid(aliases) THEN aliases ELSE '[]' END) WHERE value=?)", (row['method'], row['method'], row['method'])).fetchone()
        named_card_cancel = bool(row['name'].strip() not in ('', '-', 'unknown', 'unnamed', '\ubbf8\uc0c1') and LP.merchant_key(row['name']) and method
                                 and method[0] in ('credit_card', 'corporate_card'))
        if annotation.get('refund_confirmed') or reversal or named_card_cancel or LP._has((row['name'] + ' ' + (row['src_cat'] or '')).casefold(), ('환불', '취소', 'refund', 'reversal')):
            verified -= row['amount']
            continue
        display = dict(row, amount=abs(row['amount']))
        unverified.append(display)
        medical = [e for e in prior if LP._has((e['name'] + ' ' + (e['src_cat'] or '')).casefold(), MEDICAL_WORDS)
                   and (datetime.date.fromisoformat(row['date']) - datetime.date.fromisoformat(e['date'])).days <= 30]
        unnamed = not row['name'].strip() or row['name'].casefold() in ('unknown', 'unnamed', '\ubbf8\uc0c1', '\uc54c \uc218 \uc5c6\uc74c')
        if (medical or unnamed) and not annotation.get('refund_reviewed'):
            candidates.append(dict(display, odd='insurance'))
    return verified, unverified, candidates


def group_odd(rows):
    groups = {}
    for row in rows:
        key = (LP.merchant_key(row['name']), row['odd'])
        # A transit correction/receipt-source answer applies across stale stream IDs.
        # Cycle questions retain separate obligations with different schedules.
        if row['odd'] == 'cycle':
            key += (row.get('stream_id'), row.get('cycle'), row.get('src_cat'))
        if key not in groups:
            groups[key] = dict(row, items=[], ids=[])
        groups[key]['items'].append(row)
        groups[key]['ids'].append(row['id'])
    return list(groups.values())


def put(c, sid, detail):
    old = annotations(c).get(sid, {})
    old.update(detail)
    c.execute("INSERT INTO ledger_preference(ulid,item,polarity,status,source,recorded_at,topic,detail) "
              "VALUES(?,?,'negotiable','active','owner',?,?,?) ON CONFLICT(ulid) DO UPDATE SET detail=excluded.detail,status='active'",
              ('review-item-%d' % sid, str(sid), datetime.datetime.now().isoformat(), TOPIC, json.dumps(old)))


def fixed_answer_class(value):
    """The agent supplies an exact issued fixed-cost class enum."""
    return value if isinstance(value, str) and value in FIXED else None


def isolate_fixed_stream(c, item, cls):
    """Keep corrections on one source-category stream, across months.

    Old imports can share a stream between tax and water bills. Splitting
    before either type or cycle edits prevents the next answer overwriting it.
    """
    fid = item['stream_id']
    source = item.get('src_cat') or ''
    if fid is not None and not c.execute(
            "SELECT 1 FROM spend_entry WHERE stream_id=? AND COALESCE(src_cat,'')<>?",
            (fid, source)).fetchone():
        return fid
    if fid is None:
        return None
    old = D.rows(c, 'SELECT * FROM fixed_cost WHERE id=?', (fid,))[0]
    new = c.execute("INSERT INTO fixed_cost(name,amount,direction,cycle,source,class) "
                    "VALUES(?,?,?,?,?,?)", (item['name'], item['amount'], old['direction'],
                    old['cycle'], 'owner', cls)).lastrowid
    key = D.stream_key(item, {}) + '|owner-category:' + D.norm(source)
    c.execute('INSERT OR REPLACE INTO fixed_cost_key(fixed_id,key) VALUES(?,?)', (new, key))
    members = [r[0] for r in c.execute("SELECT id FROM spend_entry WHERE stream_id=? AND COALESCE(src_cat,'')=?", (fid, source))]
    c.execute("UPDATE spend_entry SET stream_id=? WHERE stream_id=? AND COALESCE(src_cat,'')=?",
              (new, fid, source))
    for sid in members:
        put(c, sid, dict(role=cls, fixed_stream_id=new))
    return new


def answer(c, month, data, K=None):
    """Validate the whole answer before any write. The kit owns the transaction."""
    if not isinstance(data, dict):
        raise ValueError('review answer must be an object')
    kind = data['kind']
    if kind in ('benefit_terms', 'benefit_coverage', 'benefit_context'):
        import ledger_benefits
        return ledger_benefits.write(c, data)
    if kind == 'monthly_cap':
        import ledger_budget
        return ledger_budget.save(c, data)
    if kind == 'deposit_group':
        import ledger_deposits
        return ledger_deposits.answer_group(c, data, K=K)
    if kind == 'deposit':
        import ledger_deposits
        return ledger_deposits.answer(c, month, data, K=K)
    if kind == 'merchant_category':
        import ledger_actual
        return ledger_actual.save_category(c,data)
    if kind == 'billed_installment':
        import ledger_installment
        if K is None:
            raise ValueError('installment intake requires the kit context')
        return ledger_installment.billed_parts(K,c,data)
    if kind == 'history_fact':
        sid = data.get('id')
        if type(sid) is not int or not isinstance(data.get('said'), str) or not data['said'].strip():
            raise ValueError('history fact requires an explicit payment and owner words')
        if data.get('answer') not in ('spend', 'unknown'):
            raise ValueError('history fact answer is spend or unknown; debt uses its own correction')
        item = D.rows(c, 'SELECT * FROM spend_entry WHERE id=? AND substr(date,1,7)=?', (sid, month))
        if len(item)!=1 or item[0]['kind']!='expense' or item[0]['class'] not in (None,'spend'):
            raise ValueError('choose a spending payment in this month')
        put(c, sid, dict(history_reviewed=True, history_answer=data['answer'], said=data['said']))
        c.execute("UPDATE ledger_preference SET source='owner' WHERE ulid=?", ('review-item-%d'%sid,))
        return dict(reviewed='history_fact', id=sid, answer=data['answer'])
    if kind == 'debt':
        # The owner's loan label describes its nature. The signed payment
        # decides direction; an outflow can never fund the deficit.
        ids = data.get('ids', [data.get('id')])
        if not isinstance(ids, list) or not ids or any(type(sid) is not int for sid in ids) or len(set(ids)) != len(ids):
            raise ValueError('choose explicit debt payment ids')
        items = [D.rows(c, 'SELECT * FROM spend_entry WHERE id=? AND substr(date,1,7)=?', (sid, month)) for sid in ids]
        if not isinstance(data.get('said'), str) or not data['said'].strip() or any(len(rs)!=1 for rs in items):
            raise ValueError('debt correction needs owner words and payments in this month')
        items = [rs[0] for rs in items]
        business_debt = bool(re.search(r'\uc0ac\uc5c5|business', data['said'], re.I))
        prior = annotations(c)
        if any(r['amount']==0 or r['scope']=='business' or r['kind'] not in ('income','expense','transfer') or r['class'] not in (None,'spend','income','own_transfer','loan')
               or prior.get(r['id'],{}).get('spend_id')
               or c.execute('SELECT 1 FROM ledger_receipt_allocation WHERE receipt_id=? OR expense_id=?', (r['id'],r['id'])).fetchone()
               or c.execute('SELECT 1 FROM ledger_share WHERE spend_id=?', (r['id'],)).fetchone()
               or c.execute('SELECT 1 FROM ledger_settlement_match WHERE transfer_id=? AND credited>0',(r['id'],)).fetchone() for r in items):
            raise ValueError('choose unallocated personal debt payments')
        if K is None:
            raise ValueError('debt correction requires the kit context')
        applied = []
        for r in items:
            repayment = (-r['amount'] if r['kind']=='expense' else r['amount']) < 0
            direction = 'repaid' if repayment else 'borrowed'
            kind = 'expense' if repayment else 'transfer'
            amount = abs(r['amount'])
            c.execute("UPDATE spend_entry SET kind=?,class='loan',amount=?,stream_id=NULL WHERE id=?", (kind,amount,r['id']))
            if business_debt:
                c.execute("UPDATE spend_entry SET scope='business' WHERE id=?", (r['id'],))
            if repayment:
                K._spend_write_v32(c, r['id'], r['date'], r['name'], amount, kind, r['method'])
            else:
                K._spend_shadow_del(c, r['id'])
            put(c, r['id'], dict(debt_direction=direction, said=data['said'],role='loan',checked=True,
                                 history_reviewed=True,history_answer='debt',business_debt=business_debt,
                                 fixed_stream_id=None,subscription_stream_id=None,cycle_pending=False,
                                 history_received=False,deposit_answer=None,insurance_receipt=False,income_confirmed=False))
            c.execute("UPDATE ledger_preference SET source='owner' WHERE ulid=?",('review-item-%d'%r['id'],))
            applied.append(dict(id=r['id'], direction=direction, amount=amount))
        return dict(reviewed='debt', applied=applied)
    review = build(c, month)
    if kind == 'installment_said':
        import ledger_installment as installments
        words = data.get('said', '').strip()
        gap = next((g for g in review['gaps'] if g['card'] == data['card']), None)
        term = re.search(r'(\d{1,2})\s*(?:개월|months?)', words)
        if not gap:
            raise ValueError('choose an issued card difference')
        if not term:
            return dict(reviewed=kind, pending_input=True, fields=['months'])
        prefix = words[:term.start()].strip(' /,')
        candidates = gap['purchases']
        number = re.fullmatch(r'(\d+)(?:번)?', prefix)
        if number:
            n = int(number[1])
            matches = candidates[n-1:n] if n > 0 else []
        else:
            matches = [r for r in candidates if D.norm(r['display_name']) in D.norm(prefix)
                       or D.norm(r['name']) in D.norm(prefix)]
        if K is None:
            raise ValueError('installment intake requires the kit context')
        if len(matches) == 1:
            purchase = matches[0]
            item = installments.add(K, c, installments._card(c, data['card']), purchase['amount'],
                                    int(term[1]), name=purchase['display_name'],
                                    day=datetime.date.fromisoformat(purchase['date']), spend_id=purchase['id'])
            put(c, purchase['id'], dict(installment_said=words))
        else:
            date = re.search(r'\d{4}-\d{2}-\d{2}', words)
            amount = re.search(r'(\d[\d,]*(?:\.\d+)?)\s*(만)?\s*원', words)
            name = words[:amount.start()].replace(term[0], '').strip() if amount else ''
            if date:
                name = name.replace(date[0], '').strip(' /,')
            name = re.sub(r'\s*약$', '', name).strip()
            missing = [key for key, ok in (('date', date), ('amount', amount), ('name', name)) if not ok]
            if missing or len(matches) > 1:
                return dict(reviewed=kind, pending_input=True, fields=missing or ['purchase'])
            total = round(float(amount[1].replace(',', '')) * (10000 if amount[2] else 1))
            item = installments.add(K, c, installments._card(c, data['card']), total, int(term[1]),
                                    name=name, day=datetime.date.fromisoformat(date[0]), auto_link=False)
        return dict(reviewed=kind, installment=item, gaps=build(c, month)['gaps'])
    if kind == 'card':
        card = data['card']
        if not c.execute("SELECT 1 FROM pay_method WHERE name=? AND kind IN ('credit_card','corporate_card') AND canonical_id IS NULL",(card,)).fetchone():
            raise ValueError('choose a known credit card')
        start,end = data['start'],data['end']
        if datetime.date.fromisoformat(start)>=datetime.date.fromisoformat(end):
            raise ValueError('statement end is exclusive and must follow start')
        detail=json.dumps(dict(start=start,end=end))
        key='card-statement:'+card+':'+month
        c.execute("INSERT INTO ledger_preference(ulid,item,polarity,status,source,recorded_at,topic,detail) VALUES(?,?,'negotiable','active','owner',?,'card_statement',?) ON CONFLICT(ulid) DO UPDATE SET detail=excluded.detail",(key,card+':'+month,datetime.datetime.now().isoformat(),detail))
        return dict(reviewed=kind,applied=True,card=card,gaps=build(c,month)['gaps'])
    if kind == 'insurance_said':
        if data.get('amount') is None:
            return dict(reviewed=kind, insurance=None, pending_input=True, fields=['amount'])
        classify_insurance(c)
        return dict(reviewed=kind, insurance=insurance_answer(c, data))
    if kind == 'insurance':
        classify_insurance(c)
        return dict(reviewed=kind, insurance=insurance_answer(c,data))
    if kind == 'roster':
        merges = data.get('merges', [])
        if not isinstance(merges, list):
            raise ValueError('roster merges must be a list')
        import ledger_accounts
        import kit_life as K
        for pair in merges:
            ledger_accounts.merge(K, c, pair['id'], pair['into'])
        corrections = data.get('corrections',[])
        if not isinstance(corrections,list):
            raise ValueError('roster corrections must be a list')
        known = {r['id']:r for r in D.rows(c,'SELECT * FROM pay_method WHERE canonical_id IS NULL')}
        allowed = {'corporate_card':'card','credit_card':'card','debit_card':'card','easy_pay':'wallet','account':'account','savings':'account','securities':'account'}
        names = {r['name']:r['id'] for r in known.values()}
        for change in corrections:
            if change.get('id') not in known or set(change)-{'id','name','kind'}:
                raise ValueError('choose a roster method and name/kind corrections')
            name = change.get('name',known[change['id']]['name'])
            if not isinstance(name,str) or not name.strip() or names.get(name,change['id'])!=change['id']:
                raise ValueError('method name must be nonempty and unique')
            names[name] = change['id']
            if change.get('kind',known[change['id']]['kind']) not in allowed:
                raise ValueError('unsupported payment method kind')
        from ledger_v6 import _json_list
        for change in corrections:
            old = known[change['id']]
            name,kind = change.get('name',old['name']),change.get('kind',old['kind'])
            aliases = _json_list(old['aliases'])
            if name!=old['name']:
                renamed = ledger_accounts.rename(K, c, old['id'], name)
                aliases = renamed['aliases']
            c.execute('UPDATE pay_method SET name=?,kind=?,layer=?,aliases=? WHERE id=?',(name,kind,allowed[kind],json.dumps(aliases),old['id']))
        return dict(reviewed='roster', confirmed=True, corrected=len(corrections))
    if kind == 'person':
        sid = int(data['id'])
        candidate = next((r for r in review['people'] if r['id'] == sid), None)
        if not candidate:
            raise ValueError('received row is not awaiting review')
        if data.get('cash') is True:
            if data.get('spend_id') is not None or data.get('date') or data.get('income'):
                raise ValueError('cash reimbursement has no ledger expense or income')
            note = data.get('note')
            if note is not None and not isinstance(note, str):
                raise ValueError('cash note must be owner words')
            put(c, sid, dict(income_confirmed=True, cash=True, note=note))
            c.execute("UPDATE spend_entry SET class='pass_through',kind='transfer',note=? WHERE id=?", (note, sid))
        elif data.get('spend_id') is None and not data.get('date'):
            if data.get('income') is not True:
                raise ValueError('choose income or pair a spend')
            put(c, sid, {'income_confirmed': True})
        else:
            eligible = pair_spends(c, candidate)
            if data.get('spend_id') is None:
                matches = [r for r in eligible if r['date'] == data['date'] and
                           D.norm(data.get('name')) in (D.norm(r['name']), D.norm(D._member_name(r['name'], c)))]
                if len(matches) != 1:
                    raise ValueError('date and merchant must identify one eligible spend')
                target = matches[0]['id']
            else:
                target = int(data['spend_id'])
            if not any(r['id'] == target for r in eligible):
                raise ValueError('choose a prior personal spend')
            if c.execute('SELECT 1 FROM ledger_settlement_match WHERE transfer_id=? AND credited>0', (sid,)).fetchone():
                raise ValueError('received money is already credited by settlement')
            put(c, sid, dict(income_confirmed=True, spend_id=target, amount=candidate['amount']))
            c.execute("UPDATE spend_entry SET class='own_transfer',kind='transfer' WHERE id=?", (sid,))
    elif kind == 'fixed':
        end_data={key:data[key] for key in ('ends_on','end_kind') if key in data}
        sid = int(data['id'])
        item = next((r for r in review['odd'] if sid in r.get('ids', [r['id']])), None)
        value = data['answer']
        # A type correction does not answer the separate cycle question.
        cls = fixed_answer_class(value)
        if item and cls:
            members = item.get('items', [item])
            fid = isolate_fixed_stream(c, item, cls)
            c.execute('UPDATE fixed_cost SET class=?,source=? WHERE id=?', (cls, 'owner', fid))
            for member in members:
                put(c, member['id'], dict(role=cls, fixed_stream_id=fid, cycle_pending=True))
            if end_data and fid is not None:
                import ledger_obligation
                ledger_obligation.save(c,K._facade_agent() if K else 'ledger',fid,end_data,month+'-01')
            result=dict(reviewed=kind, applied=True, pending_cycle=True, class_corrected=cls, stream_id=fid)
            if end_data and fid is None: result['pending_end']=end_data
            return result
        if not item or value not in (tuple(CYCLE_MONTHS) if item['odd'] == 'cycle' else ('ok', 'transit')):
            raise ValueError('invalid fixed correction')
        members = item.get('items', [item])
        if value == 'transit':
            from ledger_v6 import _cat_id, catmap
            cid = _cat_id(c, *catmap()['major']['교통'])
            for member in members:
                c.execute('UPDATE spend_entry SET category_id=?,is_fixed=0,stream_id=NULL WHERE id=?', (cid, member['id']))
                put(c, member['id'], dict(role='living', checked=True))
        elif value in CYCLE_MONTHS:
            cls = next(k for k, rs in review['fixed'].items() if any(r['id'] == sid for r in rs))
            fid = isolate_fixed_stream(c, item, cls)
            if fid is None:
                fid = c.execute("INSERT INTO fixed_cost(name,amount,direction,cycle,source,class) VALUES(?,?,'expense',?,'owner',?)",
                                (item['name'], sum(r['amount'] for r in members), value, cls)).lastrowid
                c.execute('INSERT OR IGNORE INTO fixed_cost_key(fixed_id,key) VALUES(?,?)', (fid, D.stream_key(item, {})))
                for member in members:
                    c.execute('UPDATE spend_entry SET stream_id=?,is_fixed=1 WHERE id=?', (fid, member['id']))
            else:
                c.execute('UPDATE fixed_cost SET cycle=? WHERE id=?', (value, fid))
            for member in members:
                put(c, member['id'], dict(checked=True, fixed_stream_id=fid, role=cls, cycle_pending=False))
        else:
            for member in members:
                put(c, member['id'], dict(checked=True))
        if end_data and value!='transit':
            import ledger_obligation
            stream=c.execute('SELECT stream_id FROM spend_entry WHERE id=?',(sid,)).fetchone()[0]
            if stream: ledger_obligation.save(c,K._facade_agent() if K else 'ledger',stream,end_data,month+'-01')
    elif kind == 'refund':
        sid = int(data['id'])
        item = next((r for r in review['refund_candidates'] if r['id'] == sid), None)
        if not item or data.get('answer') not in ('insurance', 'refund', 'unknown'):
            raise ValueError('choose an issued refund review answer')
        for member in item['items']:
            put(c, member['id'], dict(refund_reviewed=True,
                insurance_receipt=data['answer'] == 'insurance', refund_confirmed=data['answer'] == 'refund'))
    elif kind == 'living':
        available = {r['id'] for r in D._month_rows(c,month) if r['kind']=='expense' and r['class']=='spend' and r['amount']>0}
        if any(not isinstance(data.get(k, []), list) or any(type(v) is not int for v in data.get(k, [])) for k in ('one_off', 'subscriptions')):
            raise ValueError('living choices must be lists of spend ids')
        one_off = set(data.get('one_off', []))
        subscriptions = set(data.get('subscriptions', []))
        if not (one_off | subscriptions) <= available or one_off & subscriptions:
            raise ValueError('choose separate living expense ids')
        for sid in one_off:
            fid = c.execute('SELECT stream_id FROM spend_entry WHERE id=?',(sid,)).fetchone()[0]
            put(c, sid, dict(role='one_off', checked=True))
            if fid and not c.execute("SELECT 1 FROM spend_entry s WHERE stream_id=? AND id<>? AND NOT EXISTS(SELECT 1 FROM ledger_preference p WHERE p.topic='ledger_review_item' AND p.status='active' AND CAST(p.item AS INTEGER)=s.id AND json_extract(p.detail,'$.role')='one_off')",(fid,sid)).fetchone():
                D.stream_stop(c,fid,said='owner one-off correction')
            c.execute('UPDATE spend_entry SET is_fixed=0,stream_id=NULL WHERE id=?',(sid,))
        applied = []
        for sid in sorted(subscriptions):
            item = D.rows(c,'SELECT * FROM spend_entry WHERE id=?',(sid,))[0]
            key = D.stream_key(item,{})
            fid = item['stream_id']
            if fid is None:
                key = 'owner-subscription:' + key
            if fid is None:
                got = c.execute('SELECT fixed_id FROM fixed_cost_key WHERE key=?',(key,)).fetchone()
                fid = got[0] if got else c.execute("INSERT INTO fixed_cost(name,amount,current_amount,direction,cycle,source,class,last_seen) VALUES(?,?,?,'expense','monthly','owner','subscription',?)", (D._member_name(item['name'], c) or item['name'],item['amount'],item['amount'],item['date'])).lastrowid
            c.execute("UPDATE fixed_cost SET class='subscription',source='owner',state='active' WHERE id=?",(fid,))
            c.execute('INSERT OR REPLACE INTO fixed_cost_key(fixed_id,key) VALUES(?,?)',(fid,key))
            c.execute('UPDATE spend_entry SET is_fixed=1,stream_id=? WHERE id=?',(fid,sid))
            put(c,sid,dict(role='subscription',checked=True,subscription_stream_id=fid))
            applied.append(dict(spend_id=sid,stream_id=fid))
        return dict(reviewed=kind, applied=True, one_off=sorted(one_off), subscriptions=applied)

    else:
        raise ValueError('unknown review answer kind')
    return {'reviewed': kind}
