"""Monthly soft targets are preferences, never expected fixed charges."""
import datetime
import json
import re

import ledger_diag as D
import ledger_profile as LP

TOPIC = 'ledger_budget'
ITEM = 'ai_subscriptions'


def save(c, data):
    said = data.get('said', '')
    amount = data.get('amount')
    if type(amount) is not int or amount <= 0 or not isinstance(said, str):
        raise ValueError('target must be a positive integer with owner words')
    if data.get('item', ITEM) != ITEM or data.get('currency', 'KRW') != 'KRW':
        raise ValueError('only the AI subscription KRW target is supported')
    detail = dict(period='month', amount=amount, currency='KRW',
                  merchants=['anthropic', 'openai'], selector='subscription-only', note=said)
    c.execute("INSERT INTO ledger_preference(ulid,item,polarity,status,source,recorded_at,topic,detail) "
              "VALUES(?,?,'negotiable','active','owner',?,?,?) ON CONFLICT(ulid) DO UPDATE SET "
              "detail=excluded.detail,status='active',recorded_at=excluded.recorded_at",
              ('budget-'+ITEM, ITEM, datetime.datetime.now().isoformat(), TOPIC, json.dumps(detail)))
    return dict(saved=True, item=ITEM, target=detail, needs_owner_pick=False)


def read(c, month):
    from ledger_review import annotations, fixed_class
    fact = c.execute("SELECT detail FROM ledger_preference WHERE topic=? AND item=? AND status='active'",
                     (TOPIC, ITEM)).fetchone()
    if not fact:
        return []
    target = json.loads(fact[0])
    streams = {r['id']: r for r in D.rows(c, 'SELECT * FROM fixed_cost')}
    raw = {r['id']: r for r in D.rows(c, 'SELECT id,is_fixed FROM spend_entry WHERE substr(date,1,7)=?', (month,))}
    meta = annotations(c)
    members = []
    for row in D._month_rows(c, month):
        if row['kind'] != 'expense' or row['class'] != 'spend':
            continue
        words = (LP.merchant_key(row['name']) + ' ' + row['name']).casefold()
        if not any(re.search(r'(?<![a-z])'+m+r'(?![a-z])', words) for m in target['merchants']):
            continue
        a = meta.get(row['id'], {})
        if a.get('role') == 'one_off':
            continue
        if fixed_class(row, streams.get(row['stream_id'], {}), a, raw[row['id']]['is_fixed']) == 'subscription':
            members.append(row)
    actual = sum(r['amount'] for r in members)
    return [dict(item=ITEM, month=month, target=target['amount'], actual=actual,
                 difference=target['amount']-actual, currency=target['currency'], members=members,
                 soft=True, needs_owner_pick=False)]
