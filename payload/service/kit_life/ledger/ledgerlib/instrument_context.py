"""Refresh identity facts on saved cards without changing their frozen money."""
from . import kit


def current():
    return {row['id']: row['instrument_facts'] for row in kit.run('purpose-get')}


def attach(row, facts):
    fact = facts.get(row.get('id'))
    if fact is None:
        return row
    row = dict(row, instrument_facts=fact, purpose=fact['purpose'])
    if fact['confirmed_roles']:
        row.update(role=fact['confirmed_roles'][0], role_sure=True)
    else:
        row['role_sure'] = False
    return row


def retired_purpose_payment_ids(plan, seen=None):
    """Keep the retired interview's historical rows out of payment recall.

    The former three-card interview covered these rows even on skip. Removing
    the cards must not turn their old payments into a new onboarding interview.
    Freeze only IDs in flow state; do not invent owner answers or purpose facts.
    New payments and separate transfer review retain their existing paths.
    """
    rows = plan.get('instrument_review', plan.get('instruments', []))
    total = sum(r.get('amount', 0) for r in rows if r['layer'] != 'wallet')
    total = total or sum(r.get('amount', 0) for r in rows)
    selected = []
    for row in rows:
        fact = plan.get('facts', {}).get('instrument:'+str(row['id']))
        clarify = (row.get('purpose') or {}).get('needs_clarification') and not (fact or {}).get('clarification_asked')
        amount = row.get('amount', 0)
        if row.get('count', 0) < 5 or amount <= 0 or amount < total * 0.03 or (fact and not clarify):
            continue
        eligible = (row['id'] not in seen if seen is not None else
                    ((row.get('marketplace') or {}).get('amount', 0) >= amount * 0.30
                     or row.get('unresolved_amount', 0) >= amount * 0.30))
        if eligible or clarify:
            selected.append(row)
    selected.sort(key=lambda r: (-r['amount'], r['id']))
    return sorted({sid for row in selected[:3] for sid in row.get('payment_ids', [])})
