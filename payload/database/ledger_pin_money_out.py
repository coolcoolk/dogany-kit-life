"""Pending monthly cash checklist; card settlement estimates never reserve budget.

Payment days are the mode of the last four payment months (latest wins ties).
Without explicit statement dates, fit a monthly cutoff to historical bills by
mean relative error against that card's recorded net charges. These are estimates,
not bank statement readings. Keep the selected window and training evidence.
"""
import calendar
import collections
import json

import ledger_diag as D
from money_format import comma


def _date(month, day):
    return '%s-%02d' % (month, min(day, calendar.monthrange(int(month[:4]), int(month[5:]))[1]))


def _window(month, lag, day):
    return _date(D.prev_month(month, lag+1), day), _date(D.prev_month(month, lag), day)


def _bill_card(row, cards, accounts):
    """A unique card identity; the funding account only disambiguates siblings."""
    names = lambda m: [m['name']] + m['aliases']
    matches = [m for m in cards if D.norm(row['name']) in {D.norm(n) for n in names(m)}]
    if not matches:
        family = D.LP_family(row['name'])
        matches = [m for m in cards if family and any(D.LP_family(n) == family for n in names(m))]
    if len(matches) != 1:
        matches = [m for m in (matches or cards) if row['method'] in accounts.get(m['linked_account_id'], ())]
    return matches[0]['method_id'] if len(matches) == 1 else None


def _estimate(usage, terms, start, end):
    selected = [r for r in usage if start <= r['date'] < end]
    replaced = set()
    charges = 0
    for item in terms:
        if item.get('superseded_by'):
            continue
        charges += item['this_month']
        # A known full purchase is replaced by its monthly bill. For billed
        # installments replace the observed charge, never the purchase total.
        if item.get('bill_only'):
            if not item['this_month']:
                continue
            ids = set(item.get('observed_ids', ())) | {item.get('latest_source_id')}
            matching = [r for r in selected if r['id'] in ids or (
                r['name'] == item.get('source_name', item['name']) and r['amount'] == item['monthly'])]
        else:
            matching = [r for r in selected if r['id'] == item.get('spend_id')]
        replaced.update(r['id'] for r in matching)
    net = sum(r['amount'] for r in selected)
    removed = sum(r['amount'] for r in selected if r['id'] in replaced)
    return dict(amount=max(0, net-removed+charges), net_spend=net, replaced=removed,
                installments=charges, spend_ids=[r['id'] for r in selected], replaced_ids=sorted(replaced))


def card_bills(c, today):
    import ledger_balance as B
    import ledger_installment as I

    month = today[:7]
    methods = B._methods(c)
    canonical = {r[0] for r in c.execute('SELECT id FROM pay_method WHERE canonical_id IS NULL')}
    cards = [m for m in methods if m['kind'] == 'credit_card' and m['method_id'] in canonical]
    accounts = {m['method_id']: [m['name']] + m['aliases'] for m in methods}
    bills = collections.defaultdict(list)
    for row in D.rows(c, "SELECT * FROM spend_entry WHERE class='card_bill' AND scope<>'business' "
                        "AND date<=? AND ((kind='transfer' AND amount<0) OR (kind='expense' AND amount>0)) "
                        "ORDER BY date DESC,id DESC", (today,)):
        mid = _bill_card(row, cards, accounts)
        if mid is not None:
            bills[mid].append(row)
    first = c.execute('SELECT MIN(date) FROM spend_entry WHERE date<=?', (today,)).fetchone()[0]
    terms = {}
    def installments(ym, card):
        if ym not in terms:
            terms[ym] = I.listing(c, ym, include_all=True)['items']
        return [i for i in terms[ym] if i['card'] == card['name']]
    result = []
    for card in cards:
        history = bills[card['method_id']]
        if any(r['date'][:7] == month for r in history):
            continue
        monthly = {}
        for row in history:
            if row['date'][:7] < month:
                monthly.setdefault(row['date'][:7], row)
        recent = list(monthly.values())[:4]
        if not recent:
            continue  # No dated history: never fabricate a payment day.
        days = [int(r['date'][8:10]) for r in recent]
        counts = collections.Counter(days)
        day = max(days, key=lambda d: (counts[d], -days.index(d)))
        usage = [r for r in D.rows(c, "SELECT * FROM spend_entry WHERE date<=? AND scope<>'business' "
                                     "AND kind='expense'", (today,))
                 if r['method'] in [card['name']] + card['aliases']]
        statement = c.execute("SELECT detail FROM ledger_preference WHERE topic='card_statement' "
                              "AND item=? AND status='active' ORDER BY recorded_at DESC LIMIT 1",
                              (card['name']+':'+month,)).fetchone()
        evidence = dict(payment_ids=[r['id'] for r in recent], payment_days=days, payment_day=day)
        if statement:
            period = json.loads(statement[0])
            start, end = period['start'], period['end']
            evidence['cycle_basis'] = 'statement'
        else:
            # Leave the oldest partial import month out of every candidate's
            # training set. Never train on bills predating this card's spend.
            training = [r for r in list(monthly.values())[:12]
                        if first and D.prev_month(r['date'][:7])+'-01' >= first
                        and usage and r['date'] > min(u['date'] for u in usage)]
            candidates = []
            for lag in (0, 1):
                for cutoff in range(1, 32):
                    pairs = []
                    for bill in training:
                        lo, hi = _window(bill['date'][:7], lag, cutoff)
                        if lo < first or hi >= bill['date']:
                            break
                        estimated = _estimate(usage, installments(bill['date'][:7], card), lo, hi)['amount']
                        pairs.append(dict(id=bill['id'], start=lo, end=hi, amount=abs(bill['amount']), estimated=estimated))
                    if pairs and len(pairs) == len(training):
                        score = sum(abs(p['estimated']-p['amount'])/p['amount'] for p in pairs)/len(pairs)
                        candidates.append((score, lag, cutoff, pairs))
            if not candidates:
                continue  # Insufficient cycle evidence; no invented window.
            score, lag, cutoff, pairs = min(candidates, key=lambda x: x[:3])
            start, end = _window(month, lag, cutoff)
            evidence.update(cycle_basis='history_fit', cutoff_day=cutoff, month_lag=lag,
                            mean_relative_error=score, training=pairs)
        estimate = _estimate(usage, installments(month, card), start, end)
        result.append(dict(identity='card:'+str(card['method_id']), date=_date(month, day),
                           name=card['name'], amount=estimate['amount'], estimated=True,
                           derivation=dict(evidence, start=start, end=end, **estimate)))
    return result


def rows(c, today, snapshot):
    from ledger_v6 import _existing_method

    entries = [dict(row) for row in snapshot.get('money_out', ())]
    entries.extend(card_bills(c, today))
    for row in entries:
        row['group'] = 'account'
        if row.get('estimated'):
            continue  # Card settlement leaves an account, regardless of card layer.
        method = row.get('method')
        if row['identity'].startswith('stream:'):
            fid = int(row['identity'].split(':', 1)[1])
            fixed = c.execute('SELECT method_id FROM fixed_cost WHERE id=?', (fid,)).fetchone()
            method = fixed[0] if fixed else None
            if not method:
                observed = c.execute("SELECT method FROM spend_entry WHERE stream_id=? AND date<=? "
                                     "AND scope<>'business' ORDER BY date DESC,id DESC LIMIT 1",
                                     (fid, today)).fetchone()
                method = observed[0] if observed else None
        if method:
            try:
                resolved = _existing_method(c, method)
            except ValueError:
                continue  # Unknown payment rail stays in the account group.
            row['method'] = resolved['name']
            if resolved['layer'] in ('card', 'wallet'):
                row['group'] = 'charge'
    # Stable source order for same-day bills; each scheduled identity once.
    unique = {row['identity']: row for row in entries}
    return sorted(unique.values(), key=lambda row: row['date'])


def render(entries, translate):
    if not entries:
        return ''
    # Match day-pin's sanitizer-input convention: defuse dynamic tag
    # openers, then let the transport escape literal &, < and > exactly once.
    from kit_life import _day_pin_defuse
    groups = {'account': [], 'charge': []}
    for row in entries:
        amount = translate('ledger.pin.money_out.estimate' if row.get('estimated') else 'ledger.pin.money_out.amount',
                           amount=comma(row['amount']))
        name = ' '.join(str(row['name']).split())
        if row.get('estimated'):
            suffix = translate('ledger.pin.money_out.card_name_suffix')
            if suffix and name.endswith(suffix):
                name = name[:-len(suffix)]
            name = translate('ledger.pin.money_out.card', name=name)
        line = '[ ] '+row['date'][5:10].replace('-', '/')+' '+_day_pin_defuse(name)+' '+amount
        group = row.get('group', 'account')
        if group == 'charge' and row.get('method'):
            method = _day_pin_defuse(' '.join(str(row['method']).split()))
            line += translate('ledger.pin.money_out.method', name=method)
        groups[group].append(line)
    lines = []
    for group, items in groups.items():
        if items:
            lines.append(translate('ledger.pin.money_out.'+group))
            lines.extend(items)
    return translate('ledger.pin.money_out.header')+'\n<blockquote expandable>'+'\n'.join(lines)+'</blockquote>'
