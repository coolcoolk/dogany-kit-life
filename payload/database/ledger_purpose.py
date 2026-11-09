"""Read-only transaction-grounded purposes, including observed funding inheritance."""
import calendar
import collections
import datetime
import itertools
import json
import math
import re

from ledger_profile import is_dust, merchant_key

TAGS = ('월급·고정비', '생활비', '저축', '사업', '안 따짐')
SAVINGS = re.compile(r'저축|저금통|금고|적금|청약|savings|vault|coin\s*box', re.I)


def window(today):
    end = datetime.date.fromisoformat(today) if isinstance(today, str) else today or datetime.date.today()
    month = end.year * 12 + end.month - 1 - 3
    year, month = divmod(month, 12)
    start = datetime.date(year, month + 1, min(end.day, calendar.monthrange(year, month + 1)[1]))
    return start.isoformat(), end.isoformat()


INTEREST = re.compile(r'이자|interest', re.I)
SAVING_SPEND = re.compile(r'연금|적금|저축|증권|투자|pension|investment|saving', re.I)
FIXED_CLASSES = {'housing', 'loan', 'tax', 'subscription', 'telecom', 'insurance', 'utility', 'other'}
PLUMBING = {'own_transfer', 'pass_through', 'topup', 'card_bill', 'meal_point', 'interest'}


def non_plumbing(row):
    # A company answer is explicit business evidence, unlike an automatically
    # paired pass-through. Its incoming reimbursement is never counted.
    company = row.get('scope') == 'business' and card_payment(row)
    return (not is_dust(row['amount']) and (row.get('class') not in PLUMBING or company)
            and not INTEREST.search(row.get('name') or '')
            and not (row['kind'] == 'income' and INTEREST.search(row.get('src_cat') or '')))


def purpose(row, business=False):
    """Only landed transaction/stream facts and explicit scope; never product names/types."""
    if not non_plumbing(row):
        return None
    cls = row.get('class')
    # Refunds and investment withdrawals do not establish an outflow purpose.
    if cls == 'saving_back' or (row['kind'] == 'expense' and row['amount'] <= 0):
        return None
    if row.get('answered_by') and row.get('inherited_purpose'):
        return row['inherited_purpose']
    if business or row.get('scope') == 'business':
        return TAGS[3]
    if row.get('inherited_purpose'):
        return row['inherited_purpose']
    if row['kind'] == 'income' and cls == 'income':
        return TAGS[0]
    words = ' '.join(str(row.get(k) or '') for k in ('name', 'src_cat', 'category_name'))
    if (cls == 'saving' or row.get('stream_class') == 'saving'
            or (row['kind'] == 'expense' and SAVING_SPEND.search(words))):
        return TAGS[2]
    if (cls == 'loan' or row.get('is_fixed') or row.get('stream_class') in FIXED_CLASSES
            or re.search(r'월세|공과금|통신비|보험료|서비스구독|subscription|rent|utilities|\bSUBSCR\b', words, re.I)):
        return TAGS[0]
    if row['kind'] == 'expense' and cls in ('spend', None):
        return TAGS[1]
    return None


def guess(rows, business=False):
    totals = collections.Counter()
    money = lambda r: r.get('purpose_amount', abs(r['amount']))
    denominator = sum(money(r) for r in rows if non_plumbing(r))
    for row in rows:
        tag = purpose(row, business)
        if tag and not is_dust(row['amount']):
            totals[tag] += money(row)
    ordered = sorted(totals, key=lambda tag: (-totals[tag], TAGS.index(tag)))
    tags = [tag for tag in ordered if totals[tag] * 100 >= denominator * 15
            or math.isclose(totals[tag] * 100, denominator * 15, rel_tol=1e-12)][:3]
    return dict(tags=tags, total=denominator,
                shares=[dict(tag=tag, amount=totals[tag], share=totals[tag] / denominator,
                             guessed=tag in tags) for tag in ordered])


def representative(rows, tags):
    """Cover claims with distinct merchants where possible, then display newest first."""
    ranked = sorted((r for r in rows if r.get('purpose') in tags and not is_dust(r['amount'])),
                    key=lambda r: (abs(r['amount']), r['date'], r.get('at_time') or '', r['id']), reverse=True)
    if not tags:
        return []
    options = []
    purpose_merchants = {}
    for tag in tags:
        distinct = {}
        for row in ranked:
            if row['purpose'] == tag:
                distinct.setdefault(merchant_key(row['name']), row)
        # At most three claims: three choices per claim suffice to find a
        # distinct assignment, including when one merchant backs two purposes.
        options.append(list(distinct.values())[:len(tags)])
        purpose_merchants[tag] = set(distinct)
    options = [o for o in options if o]
    if not options:
        return []
    selected = list(min(itertools.product(*options),
                        key=lambda rs: len(rs) - len({merchant_key(r['name']) for r in rs})))
    ids = {r['id'] for r in selected}
    merchants = {merchant_key(r['name']) for r in selected}
    for row in ranked:
        key = merchant_key(row['name'])
        if len(selected) < 3 and row['id'] not in ids and key not in merchants:
            selected.append(row)
            ids.add(row['id']); merchants.add(key)
    # Repeat only where that purpose has no other merchant at all.
    for row in ranked:
        if (len(selected) < 3 and row['id'] not in ids
                and len(purpose_merchants[row['purpose']]) == 1):
            selected.append(row)
            ids.add(row['id'])
    return sorted(selected, key=lambda r: (r['date'], r.get('at_time') or '', r['id']), reverse=True)


def card_payment(row):
    return (row.get('class') in ('card_bill', 'pass_through')
            and ((row['kind'] == 'transfer' and row['amount'] < 0)
                 or (row['kind'] == 'expense' and row['amount'] > 0)))


def payment_card(row, account_id, methods, sources):
    """Resolve actual settlements, never an issuer-only corporate reimbursement."""
    from ledger_v6 import _method_layer, family, nfkc
    cards = {m['id']: m for m in methods.by_id.values()
             if not m['canonical_id'] and not m['dust'] and _method_layer(m) == 'card'}
    mid = methods.names.get(nfkc(row['name']))
    if mid is not None and methods.canon(mid) in cards:
        return methods.canon(mid)
    issuer = family(row['name'])
    matches = [mid for mid, m in cards.items()
               if issuer and family(m['name']) == issuer and account_id in sources[mid]]
    return matches[0] if len(matches) == 1 else None


def evidence(conn, today=None):
    from ledger_v6 import Methods, _method_layer, nfkc, rows
    from ledger_instrument_facts import records
    facts = records(conn)
    start, end = window(today)
    methods = Methods(conn)
    by_method = collections.defaultdict(list)
    for row in rows(conn, 'SELECT s.*,f.class AS stream_class,c.name AS category_name FROM spend_entry s '
                    'LEFT JOIN fixed_cost f ON f.id=s.stream_id LEFT JOIN spend_category c ON c.id=s.category_id '
                    'WHERE s.date>=? AND s.date<=? ORDER BY s.date DESC,s.at_time DESC,s.id DESC',
                    (start, end)):
        mid = methods.names.get(nfkc(row['method'] or ''))
        if mid is not None:
            by_method[methods.canon(mid)].append(row)
    # Only leaf -> root edges are meaningful here. Canonical aliases and the
    # roster's card links participate, including debit cards with no bill row.
    rank = {'wallet': 0, 'card': 1, 'account': 2}
    children = collections.defaultdict(set)
    sources = collections.defaultdict(set)
    # Use exactly the links shown by the roster: it filters obsolete wallet
    # observations and gives an explicit current card account precedence.
    from ledger_review import roster
    edges = []
    for link in roster(conn, today)['links']:
        child, parent = methods.names.get(nfkc(link['wallet'])), methods.names.get(nfkc(link['source']))
        if child is not None and parent is not None:
            edges.append((child, parent))
    for child, parent in edges:
        child, parent = methods.canon(child), methods.canon(parent)
        if rank.get(_method_layer(methods.by_id[child]), 3) < rank.get(_method_layer(methods.by_id[parent]), -1):
            children[parent].add(child)
            sources[child].add(parent)

    # Some exports put a wallet's purchases under its funding method and name
    # only the wallet. Keep their real id/method; the same row can be encountered
    # through the wallet and directly on its source, but is counted only once.
    for wid, parents in sources.items():
        if _method_layer(methods.by_id[wid]) != 'wallet':
            continue
        names = {nfkc(n) for n, mid in methods.names.items() if methods.canon(mid) == wid}
        for parent in parents:
            for row in list(by_method[parent]):
                if row['kind'] == 'expense' and nfkc(row['name']) in names:
                    by_method[wid].append(dict(row, wallet_merchant=True))

    def unique(pool):
        seen, origins, pairs, result = set(), {}, set(), []
        for row in pool:
            # A split into several answered purposes is intentional; repeated
            # paths through the funding graph are not.
            key = (row['id'], row.get('inherited_purpose'))
            origin = (row.get('answered_by'), row.get('via_card_id'), row.get('via_wallet_id'))
            pair = row.get('pair_id') if row['kind'] == 'expense' and non_plumbing(row) else None
            if (key in seen or (row['id'] in origins and origins[row['id']] != origin)
                    or (pair is not None and (pair, row['kind']) in pairs and row['id'] not in origins)):
                continue
            seen.add(key)
            origins[row['id']] = origin
            if pair is not None:
                pairs.add((pair, row['kind']))
            result.append(row)
        return result

    def answered(pool, mid):
        method = methods.by_id[mid]
        tags = json.loads(method['purpose_tags'] or '[]') if method['purpose_origin'] in ('user', 'owner-stated') else []
        tags = [t for t in tags if t in TAGS]
        result = []
        for row in pool:
            tag = purpose(row, method['scope_default'] == 'business')
            # Keep observed proportions for selected purposes; where the owner
            # changed a row's inferred purpose, divide it evenly among their
            # selected purposes (there is no owner-provided money allocation).
            selected = [tag] if tag in tags else tags
            if not tags or not non_plumbing(row):
                result.append(dict(row, inherited_purpose=tag))
            else:
                for selected_tag in selected:
                    result.append(dict(row, inherited_purpose=selected_tag, answered_by=mid,
                                       purpose_amount=row.get('purpose_amount', abs(row['amount'])) / len(selected)))
        combined = {}
        for row in result:
            key = (row['id'], row.get('inherited_purpose'))
            if key in combined:
                combined[key]['purpose_amount'] += row.get('purpose_amount', abs(row['amount']))
            else:
                combined[key] = dict(row, purpose_amount=row.get('purpose_amount', abs(row['amount'])))
        return list(combined.values())

    pools, items = {}, []
    ordered = sorted((m for m in methods.by_id.values() if not m['canonical_id'] and not m['dust']),
                     key=lambda m: (rank.get(_method_layer(m), 3), m['id']))
    for method in ordered:
        mid, layer = method['id'], _method_layer(method)
        own = unique(by_method[mid])
        candidates, inherited, replaced_payments = [], [], set()
        linked = set(children[mid])
        # Settlement ids are explanatory metadata only. Shares count actual
        # linked spend, never bill + spend or a bill-weighted proxy.
        payments, payment_ids = collections.Counter(), collections.defaultdict(list)
        if layer == 'account':
            for row in own:
                if is_dust(row['amount']) or not card_payment(row):
                    continue
                cid = payment_card(row, mid, methods, sources)
                if cid is not None and (not sources[cid] or mid in sources[cid]):
                    linked.add(cid)
                    payments[cid] += abs(row['amount'])
                    payment_ids[cid].append(row['id'])

        for child in sorted(linked, key=lambda cid: (rank.get(_method_layer(methods.by_id[cid]), 3), cid)):
            child_method = methods.by_id[child]
            spending = [r for r in pools.get(child, []) if non_plumbing(r)]
            spending_total = sum(r.get('purpose_amount', abs(r['amount'])) for r in spending)
            if not spending_total:
                continue
            card = _method_layer(child_method) == 'card'
            detail = dict(spending_total=spending_total, row_ids=sorted({r['id'] for r in spending}),
                          roster_link=child in children[mid])
            if card:
                replaced_payments.update(payment_ids[child])
                detail.update(card_id=child, paid_amount=payments[child], payment_ids=payment_ids[child])
            else:
                detail.update(wallet_id=child)
            inherited.append(detail)
            for row in answered(spending, child):
                r = dict(row)
                if not r.get('via_wallet_id'):
                    r.update({('via_card_id' if card else 'via_wallet_id'): child,
                              ('via_card_name' if card else 'via_wallet_name'): child_method['name']})
                candidates.append(r)
        # Leaves take precedence over the source's duplicate copy of a purchase.
        candidates = unique(candidates + [r for r in own if r['id'] not in replaced_payments])
        business = method['scope_default'] == 'business'
        candidates = [dict(r, purpose=purpose(r, business)) for r in candidates]
        pools[mid] = candidates
        count = len({r['id'] for r in candidates})
        total = sum(r.get('purpose_amount', abs(r['amount'])) for r in candidates)
        saving = method['kind'] in ('savings', 'securities') or bool(SAVINGS.search(method['name']))
        reason = ('savings' if saving else 'kind' if layer not in rank or method['kind'] == 'cash'
                  else 'low_activity' if count <= 10 and total <= 50000 else None)
        inferred = guess(candidates, business)
        recent = [r for tag in inferred['tags'] for r in representative(candidates, [tag])]
        items.append(dict(id=mid, name=method['name'], kind=method['kind'], layer=layer,
                          instrument_facts=facts.get(mid),
                          count=count, total=total, qualifies=reason is None, skip_reason=reason,
                          recent=recent, guess=inferred, inherited=inherited,
                          row_ids=sorted({r['id'] for r in candidates if non_plumbing(r)})))
    return dict(since=start, until=end, items=items)
