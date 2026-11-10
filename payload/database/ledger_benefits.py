"""Conservative benefit comparison from versioned owner-confirmed facts.

Existing ledger_preference holds terms, coverage and payment context. This
supports one explicit rule per product; unsupported issuer conditions stay
unknown. No network lookup, inferred coverage or mutable reward balances.
"""
import datetime
import json
from urllib.parse import urlparse

import ledger_diag as D

TERMS = 'pay_benefit_terms'
COVERAGE = 'pay_history_coverage'
CONTEXT = 'pay_spend_context'
RULE_FIELDS = {'rate_bps', 'fixed', 'minimum', 'monthly_cap', 'per_payment_cap',
               'previous_minimum', 'exclude_categories', 'merchants', 'categories',
               'channel', 'rounding', 'unit'}


def integer(value, name, maximum=None):
    if type(value) is not int or value < 0 or (maximum is not None and value > maximum):
        raise ValueError(name + ' must be a nonnegative integer')
    return value


def date(value):
    if not isinstance(value, str) or datetime.date.fromisoformat(value).isoformat() != value:
        raise ValueError('use an ISO date')
    return value


def method(c, mid):
    if type(mid) is not int:
        raise ValueError('method_id must be an integer')
    rows = D.rows(c, 'SELECT * FROM pay_method WHERE id=? AND canonical_id IS NULL AND dust=0', (mid,))
    if not rows or rows[0]['kind'] not in ('credit_card', 'corporate_card', 'debit_card', 'account', 'easy_pay'):
        raise ValueError('choose an existing canonical payment method')
    return rows[0]


def facts(c, topic):
    return [dict(json.loads(detail), fact_key=key) for key, detail in c.execute(
        "SELECT ulid,detail FROM ledger_preference WHERE topic=? AND status='active' ORDER BY id", (topic,))]


def put(c, key, topic, item, detail):
    c.execute("INSERT INTO ledger_preference(ulid,item,polarity,status,source,recorded_at,topic,detail) "
              "VALUES(?,?,'negotiable','active','owner',?,?,?) ON CONFLICT(ulid) DO UPDATE SET "
              "detail=excluded.detail,status='active',recorded_at=excluded.recorded_at",
              (key, str(item), datetime.datetime.now().isoformat(), topic, json.dumps(detail)))


def write(c, data):
    kind = data['kind']
    if kind == 'benefit_terms':
        mid = data['method_id']
        method(c, mid)
        source = data['official_source']
        if not isinstance(source, str) or urlparse(source).scheme != 'https' or not urlparse(source).netloc:
            raise ValueError('terms require an official HTTPS source')
        start, end = date(data['valid_from']), date(data['valid_to'])
        checked = date(data['checked_at'])
        if start > end or type(data.get('confirmed')) is not bool:
            raise ValueError('terms need an ordered validity period and confirmed flag')
        rule = data['rule']
        if not isinstance(rule, dict):
            raise ValueError('rule must be an object')
        # Unknown keys are stored as unsupported, never silently discarded.
        supported = not (set(rule) - RULE_FIELDS)
        supported = supported and rule.get('rounding') == 'floor' and rule.get('unit') == 'KRW'
        supported = supported and rule.get('channel') in ('direct', 'wallet')
        if ('rate_bps' in rule) == ('fixed' in rule):
            raise ValueError('choose rate_bps or fixed')
        for key in ('rate_bps', 'fixed', 'minimum', 'monthly_cap', 'per_payment_cap', 'previous_minimum'):
            if key in rule:
                integer(rule[key], key, 10000 if key == 'rate_bps' else None)
        for key in ('merchants', 'categories', 'exclude_categories'):
            if key in rule and (not isinstance(rule[key], list) or any(not isinstance(v, str) or not v for v in rule[key])):
                raise ValueError(key + ' must be a list of exact names')
        product = data['product_key']
        if not isinstance(product, str) or not product.strip():
            raise ValueError('an exact issuer product key is required')
        statement = data.get('statement_source')
        statement_rate = data.get('statement_rate_bps')
        if statement_rate is not None:
            integer(statement_rate, 'statement_rate_bps', 10000)
            if not isinstance(statement, str) or not statement or 'rate_bps' not in rule:
                raise ValueError('statement rate requires statement evidence and a rate rule')
        performance = data.get('previous_statement')
        if performance is not None:
            if not isinstance(performance, dict):
                raise ValueError('previous_statement needs month, amount and source')
            date(performance['month'] + '-01')
            integer(performance['amount'], 'statement performance')
            if not isinstance(performance.get('source'), str) or not performance['source'].strip():
                raise ValueError('statement performance needs evidence')
        version = 1 + max((f['version'] for f in facts(c, TERMS) if f['method_id'] == mid), default=0)
        detail = dict(method_id=mid, product_key=product, version=version, valid_from=start,
                      valid_to=end, checked_at=checked, official_source=source,
                      confirmed=data['confirmed'], rule=rule, supported=bool(supported),
                      statement_source=statement, statement_rate_bps=statement_rate, previous_statement=performance)
        key = 'benefit-terms:%d:%d' % (mid, version)
        put(c, key, TERMS, mid, detail)
        return dict(saved=True, terms_key=key, version=version, supported=bool(supported))
    if kind == 'benefit_coverage':
        mid = data['method_id']
        method(c, mid)
        start, end = date(data['start']), date(data['end'])
        source = data['source']
        if start > end or data.get('status') not in ('partial', 'complete') or not isinstance(source, str) or not source:
            raise ValueError('coverage requires dates, evidence and partial/complete status')
        detail = dict(method_id=mid, start=start, end=end, status=data['status'], source=source)
        put(c, 'benefit-coverage:%d:%s:%s' % (mid, start, end), COVERAGE, mid, detail)
        return dict(saved=True, coverage=detail)
    if kind == 'benefit_context':
        sid = data['spend_id']
        if type(sid) is not int or not c.execute('SELECT 1 FROM spend_entry WHERE id=?', (sid,)).fetchone():
            raise ValueError('choose an existing spend row')
        mid = data['method_id']
        chosen = method(c, mid)
        channel = data['channel']
        if channel not in ('direct', 'wallet') or (channel == 'wallet') != (chosen['kind'] == 'easy_pay'):
            raise ValueError('channel must describe the confirmed method')
        funding = data.get('funding_source_id')
        if channel == 'wallet':
            funding = confirmed_funding(c, mid, funding)
        elif funding is not None:
            raise ValueError('direct payments have no wallet funding source')
        original = D.rows(c, 'SELECT method,date,name,amount FROM spend_entry WHERE id=?', (sid,))[0]
        detail = dict(spend_id=sid, method_id=mid, channel=channel, funding_source_id=funding, original=original)
        put(c, 'benefit-context:%d' % sid, CONTEXT, sid, detail)
        return dict(saved=True, context=detail)
    raise ValueError('unknown benefit fact kind')


def confirmed_funding(c, wallet, source):
    from ledger_v6 import funding_get
    method(c, source)
    if not any(f['wallet_id'] == wallet and f['source_id'] == source and
               f['origin'] == 'owner-stated' for f in funding_get(c)):
        raise ValueError('wallet funding path must be owner-confirmed')
    return source


def covered(c, mid, start, end):
    intervals = sorted((f['start'], f['end']) for f in facts(c, COVERAGE)
                       if f['method_id'] == mid and f['status'] == 'complete')
    cursor = start
    for lo, hi in intervals:
        if hi < cursor:
            continue
        if lo > cursor:
            return False
        if hi >= end:
            return True
        cursor = (datetime.date.fromisoformat(hi) + datetime.timedelta(days=1)).isoformat()
    return False


def matches(rule, row):
    return ((not rule.get('merchants') or row['name'].casefold() in [v.casefold() for v in rule['merchants']])
            and (not rule.get('categories') or row['src_cat'] in rule['categories'])
            and row['src_cat'] not in rule.get('exclude_categories', []))


def reward(rule, amount, rate=None):
    if amount < rule.get('minimum', 0) or amount <= 0:
        return 0
    value = amount * (rule['rate_bps'] if rate is None else rate) // 10000 if 'rate_bps' in rule else rule['fixed']
    return min(value, rule.get('per_payment_cap', value))


def compare(c, purchase):
    when = date(purchase['date'])
    amount = integer(purchase['amount'], 'amount')
    if amount == 0:
        raise ValueError('purchase amount must be positive')
    if not isinstance(purchase.get('merchant'), str) or not isinstance(purchase.get('category'), str):
        raise ValueError('purchase needs merchant and category')
    month = when[:7]
    start, prior = month+'-01', D.prev_month(month)+'-01'
    prior_end = (datetime.date.fromisoformat(start)-datetime.timedelta(days=1)).isoformat()
    if purchase.get('currency', 'KRW') != 'KRW':
        raise ValueError('only KRW purchases are supported')
    terms = facts(c, TERMS)
    contexts = {f['spend_id']: f for f in facts(c, CONTEXT)}
    # Ignore stale contexts when a spend is removed or its method changes.
    history = D.rows(c, 'SELECT * FROM spend_entry WHERE date>=? AND date<=? ORDER BY date,id', (prior, when))
    excluded = purchase.get('spend_id')
    if excluded is not None:
        if type(excluded) is not int:
            raise ValueError('spend_id must be an integer')
        row = next((r for r in history if r['id'] == excluded), None)
        if not row or row['date'] != when or row['amount'] != amount or row['name'] != purchase['merchant']:
            raise ValueError('recorded purchase must identify the same payment')
        order = lambda r: (r['date'], r['at_time'] or '', r['id'])
        history = [r for r in history if order(r) <= order(row)]
    candidates = []
    for m in D.rows(c, "SELECT * FROM pay_method WHERE canonical_id IS NULL AND dust=0 "
                     "AND kind IN ('credit_card','corporate_card','debit_card','account','easy_pay') ORDER BY id"):
        mid = m['id']
        result = dict(method_id=mid, name=m['name'], status='unknown', benefit=None)
        candidates.append(result)
        versions = [f for f in terms if f['method_id'] == mid and f['valid_from'] <= when <= f['valid_to']]
        if not versions:
            result['reason'] = 'terms'
            continue
        t = max(versions, key=lambda f: f['version'])
        result.update(official_source=t['official_source'], statement_source=t.get('statement_source'),
                      terms_key=t['fact_key'])
        age = (datetime.date.fromisoformat(when)-datetime.date.fromisoformat(t['checked_at'])).days
        if not t['confirmed'] or not t['supported'] or not 0 <= age <= 30:
            result['reason'] = 'unconfirmed_or_unsupported'
            continue
        rule = t['rule']
        wallet = m['kind'] == 'easy_pay'
        if wallet != (rule['channel'] == 'wallet'):
            result['reason'] = 'channel'
            continue
        funding = None
        if wallet:
            try:
                funding = confirmed_funding(c, mid, purchase.get('funding', {}).get(str(mid)))
            except (ValueError, TypeError):
                result['reason'] = 'funding'
                continue
        aliases = json.loads(m['aliases'] or '[]')
        if wallet:
            fmethod = method(c, funding)
            aliases += [fmethod['name']] + json.loads(fmethod['aliases'] or '[]')
        performance_rows = [r for r in history if r['id'] != excluded and
                            (r['method'] in [m['name']] + aliases or
                             contexts.get(r['id'], {}).get('method_id') == mid) and
                            r['kind'] == 'expense' and r['class'] in (None, 'spend') and
                            r['date'] < start and r['src_cat'] not in rule.get('exclude_categories', [])]
        # Use original payment amounts, net of excluded payments and cancels.
        # A closed prior month is measured through its final day. Imported
        # history stays an estimate even when coverage is complete.
        previous = max(0, sum(r['amount'] for r in performance_rows))
        statement = t.get('previous_statement')
        confirmed = bool(statement and statement['month'] == prior[:7])
        if confirmed:
            previous = statement['amount']
        result.update(rule=rule, performance=dict(spent=previous, need=rule.get('previous_minimum', 0),
                      confirmed=confirmed, source=statement['source'] if confirmed else None))
        # Coverage is explicit. An empty database never proves zero use.
        if not covered(c, mid, prior, prior_end) or not covered(c, mid, start, when):
            result['reason'] = 'coverage'
            continue
        if t['valid_from'] > prior:
            result['reason'] = 'terms_history'
            continue
        rows = []
        uncertain = any(f['method_id'] == mid and f['confirmed'] and
                        f['valid_from'] <= when and f['valid_to'] >= start and
                        (f['rule'] != t['rule'] or
                         (f.get('statement_rate_bps') if f.get('statement_rate_bps') is not None else f['rule'].get('rate_bps')) !=
                         (t.get('statement_rate_bps') if t.get('statement_rate_bps') is not None else t['rule'].get('rate_bps')))
                        for f in terms)
        for row in history:
            if row['id'] == excluded:
                continue
            ctx = contexts.get(row['id'])
            named = row['method'] in [m['name']] + aliases
            attributed = ctx and ctx['method_id'] == mid
            if not named and not attributed:
                continue
            if attributed and ctx.get('original') != {k: row[k] for k in ('method','date','name','amount')}:
                uncertain = True
            if row['kind'] != 'expense' or row['class'] not in (None, 'spend'):
                continue
            # Original cancellation links, FX and installment recognition need
            # richer issuer rules; never treat those cases as known zero.
            if row['amount'] < 0 or any(w in (row['note'] or '').casefold() for w in ('installment', '\ud560\ubd80')):
                uncertain = True
            if ctx and not attributed and named:
                # Confirmed wallet purchases still count for the funding card
                # only when direct-channel rules explicitly support that route.
                uncertain = True
            if attributed and (ctx['channel'] != rule['channel'] or
                               (wallet and ctx['funding_source_id'] != funding)):
                uncertain = True
            if wallet and not attributed:
                uncertain = True
            rows.append(row)
        if uncertain:
            result['reason'] = 'payment_context'
            continue
        if previous < rule.get('previous_minimum', 0):
            result['reason'] = 'previous_minimum'
            continue
        rate = t.get('statement_rate_bps')
        used = sum(reward(rule, r['amount'], rate) for r in rows if r['date'] >= start and matches(rule, r))
        raw = reward(rule, amount, rate) if matches(rule, dict(name=purchase['merchant'], src_cat=purchase['category'])) else 0
        remaining = max(0, rule['monthly_cap']-used) if 'monthly_cap' in rule else raw
        benefit = min(raw, remaining) if previous >= rule.get('previous_minimum', 0) else 0
        result.update(status='known', benefit=benefit, previous=previous, used=used,
                      remaining=remaining if 'monthly_cap' in rule else None, funding_source_id=funding,
                      basis='statement' if rate is not None else 'official',
                      applied_rate_bps=rate if rate is not None else rule.get('rate_bps'), reason=None)
    known = [r for r in candidates if r['status'] == 'known']
    best = max((r['benefit'] for r in known), default=0)
    winners = [r['method_id'] for r in known if r['benefit'] == best and best > 0]
    return dict(purchase=purchase, candidates=candidates, recommended=winners,
                partial=any(r['status'] != 'known' for r in candidates), read_only=True)


def forget_method(c, mid):
    """Do not let a deleted instrument's facts follow a recycled numeric id."""
    c.execute("DELETE FROM ledger_preference WHERE topic IN (?,?,?) AND "
              "(json_extract(detail,'$.method_id')=? OR json_extract(detail,'$.funding_source_id')=?)",
              (TERMS, COVERAGE, CONTEXT, mid, mid))
