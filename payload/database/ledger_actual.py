# -*- coding: utf-8 -*-
"""Shared actual-payment arithmetic for history and diagnosis.

Amounts and order counts are different facts. Dust stays in the amount;
same-minute split charges are an ambiguous payment group, not two orders.
No price band or payment day establishes a subscription contract.
"""
import collections
import datetime
import hashlib
import json

import ledger_profile as LP


def order_metrics(payments):
    """Net all rows, count only unambiguous, uncancelled material payments.

    Cancellation requires the same merchant/method, amount and either a
    pair id or nearby timestamps. An unrelated negative row does not erase
    an order. Split groups remain visible separately, outside the lever's
    denominator, while their amounts are retained in the actual total.
    """
    payments = list(payments)
    groups = collections.defaultdict(list)
    for r in payments:
        groups[(LP.merchant_key(r['name']), r.get('method'))].append(r)
    counted, ambiguous, cancelled = [], [], []
    for rs in groups.values():
        positives = [r for r in rs if r['amount'] > 0]
        used = set()
        for refund in sorted((r for r in rs if r['amount'] < 0), key=lambda r: (r['date'], r['id'])):
            candidates = []
            for r in positives:
                if r['id'] in used or r['amount'] != -refund['amount']:
                    continue
                paired = r.get('pair_id') is not None and r['pair_id'] == refund.get('pair_id')
                timed = r.get('at_time') and refund.get('at_time')
                if timed:
                    start = datetime.datetime.fromisoformat(r['date']+'T'+r['at_time'])
                    end = datetime.datetime.fromisoformat(refund['date']+'T'+refund['at_time'])
                    timed = datetime.timedelta(0) <= end-start <= datetime.timedelta(minutes=10)
                if paired or timed:
                    candidates.append(r)
            if len(candidates) == 1:
                used.add(candidates[0]['id'])
                cancelled.append([candidates[0]['id'], refund['id']])
        by_time = collections.defaultdict(list)
        for r in positives:
            if r['id'] in used:
                continue
            # Unknown timestamps never collapse separate payments.
            key = (r['date'], r.get('at_time') or ('id:%s' % r['id']))
            by_time[key].append(r)
        for members in by_time.values():
            if len(members) > 1:
                if sum(r['amount'] for r in members) >= LP.DUST_KRW:
                    ambiguous.append(dict(ids=[r['id'] for r in members], amount=sum(r['amount'] for r in members)))
            elif members[0]['amount'] >= LP.DUST_KRW:
                counted.append(members[0])
    amount = sum(r['amount'] for r in counted)
    return dict(count=len(counted), amount=sum(r['amount'] for r in payments),
                counted_amount=amount, unit_price=round(amount/len(counted)) if counted else None,
                ambiguous=ambiguous, cancelled=cancelled)


def category(name):
    """Only strong merchant evidence overrides an imported category here.

    Hidden-merchant payment rails deliberately return unknown. An owner
    annotation is applied by the caller before this merchant rule.
    """
    n = (name or '').casefold().replace(' ', '')
    if any(w in n for w in ('테이블오더', 'tableorder')):
        return ('food', '식비')
    if any(w in n for w in ('openai', 'chatgpt', 'anthropic', 'claude')):
        return ('subscription', '구독')
    if any(w in n for w in ('인터넷상거래', '결제대행', '토스페이', 'tosspayments')) or n == '송금':
        return ('unknown_merchant', '사용처 모름')
    return None


CATEGORIES = dict(food='식비', food_cafe='외식·카페', cafe_snack='카페/간식', shopping='쇼핑', subscription='구독',
                  unknown_merchant='사용처 모름', transport='교통', household='생활용품',
                  health='건강', leisure='콘텐츠와 취미', housing='주거', tax='세금', etc='기타')


def save_category(c, data):
    """An explicit owner fact, scoped to a named merchant and optional rail/range."""
    name, key, said = data.get('name'), data.get('category'), data.get('said')
    lo, hi = data.get('min_amount',0), data.get('max_amount')
    method = data.get('method')
    if (not isinstance(name,str) or not name.strip() or key not in CATEGORIES
            or not isinstance(said,str) or not said.strip() or type(lo) is not int or lo<0
            or hi is not None and (type(hi) is not int or hi<=lo)
            or method is not None and (not isinstance(method,str) or not method.strip())):
        raise ValueError('category fact needs a merchant, category, valid amount range and owner words')
    selector=dict(name=name,min_amount=lo,max_amount=hi,method=method)
    token=hashlib.sha256(json.dumps(selector,sort_keys=True).encode()).hexdigest()[:24]
    detail=dict(selector,category=key,said=said)
    c.execute("INSERT INTO ledger_preference(ulid,item,polarity,status,source,recorded_at,topic,detail) "
              "VALUES(?,?,'negotiable','active','owner',?,'ledger_merchant_category',?) "
              "ON CONFLICT(ulid) DO UPDATE SET detail=excluded.detail,recorded_at=excluded.recorded_at,status='active'",
              ('merchant-category:'+token,token,datetime.datetime.now().isoformat(),json.dumps(detail)))
    return dict(saved=True,fact=detail)


# Budget categories are an internal projection of the owner's module link.
MODULE_CATEGORIES = dict(module_dining='food_cafe', module_meal='food',
    module_person='etc', module_workout='health', module_health='health',
    module_outing='leisure', module_shopping='shopping', module_admin='housing',
    module_work='etc', module_transport='transport', module_invest='etc',
    module_business='etc')


def module_fact(row, meta):
    """An exact payment beats a stream; amount rails never bleed into peers."""
    item = meta.get('item:'+str(row.get('id')), {})
    if 'module' in item:
        return item
    stream = meta.get('hidden:'+str(row.get('method'))+':'+row['name'], {})
    if 'module' not in stream:
        return {}
    amount = abs(row['amount'])
    if (amount >= stream.get('min_amount', 0)
            and (stream.get('max_amount') is None or amount < stream['max_amount'])):
        return stream
    return {}


def apply_categories(c, payments):
    facts=[json.loads(r[0]) for r in c.execute("SELECT detail FROM ledger_preference WHERE topic='ledger_merchant_category' AND status='active' ORDER BY recorded_at,id")]
    from ledger_plan import facts as plan_facts
    meta = plan_facts(c)
    if not facts and not any('module' in fact for fact in meta.values()):
        return payments
    amounts=dict(c.execute('SELECT id,amount FROM spend_entry'))
    for r in payments:
        for fact in facts:
            amount=abs(amounts[r['id']])
            if (r['name']==fact['name'] and (fact['method'] is None or fact['method']==r['method'])
                    and amount>=fact['min_amount'] and (fact['max_amount'] is None or amount<fact['max_amount'])):
                r['category_override']=[fact['category'],CATEGORIES[fact['category']]]
        linked = module_fact(r, meta)
        module = linked.get('module')
        if module:
            key = MODULE_CATEGORIES.get(module, 'unknown_merchant')
            r['category_override'] = [key, CATEGORIES[key]]
            if module == 'self_transfer':
                if r.get('kind') == 'expense':
                    r['amount'] = -r['amount']
                r.update(kind='transfer', **{'class': 'own_transfer'})
    return payments
