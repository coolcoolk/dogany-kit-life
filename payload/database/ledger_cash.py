"""Owner points allocations preserve purchase totals and reduce cash actuals."""
import json
from decimal import Decimal, ROUND_HALF_UP


def allocation(purchase, share):
    if type(share) not in (int,float) or not 0 <= share <= 1 or purchase <= 0:
        raise ValueError('points share requires a positive purchase and a fraction from zero to one')
    points=int((Decimal(str(purchase))*Decimal(str(share))).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
    return dict(purchase_amount=purchase,points_amount=points,cash_amount=purchase-points)


def apply(c, payments):
    facts={int(key[5:]):json.loads(detail) for key,detail in c.execute(
        "SELECT item,detail FROM ledger_preference WHERE topic='ledger_plan_fact' "
        "AND status='active' AND item LIKE 'item:%' AND json_type(detail,'$.points_share') IS NOT NULL")}
    purchases=dict(c.execute('SELECT id,amount FROM spend_entry')) if facts else {}
    for row in payments:
        fact=facts.get(row['id'])
        if fact and row['kind']=='expense' and row['class']=='spend':
            amounts=allocation(purchases[row['id']],fact['points_share'])
            # Reimbursement netting has already happened; subtract points once.
            row.update(amount=max(0,row['amount']-amounts['points_amount']),**amounts)
    return payments
