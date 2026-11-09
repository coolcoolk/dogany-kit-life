"""Read-only instrument → module/person projection, separate from plan money.

Ledger dates are local; timestamp inputs use Seoul dates. Never reads areas.
"""
import collections
import datetime
import re
import statistics
from zoneinfo import ZoneInfo

import ledger_actual as A
import ledger_diag as D
import ledger_review as R
import ledger_payday as P
from ledger_profile import VOLATILE, EXCHANGE
from ledger_v6 import Methods, nfkc, _method_layer

VERSION = 6


def window(today):
    if isinstance(today, str):
        today = datetime.datetime.fromisoformat(today.replace('Z', '+00:00')) if 'T' in today else datetime.date.fromisoformat(today)
    if isinstance(today, datetime.datetime):
        today = today.astimezone(ZoneInfo('Asia/Seoul')).date() if today.tzinfo else today.date()
    from ledger_window import window as display_window
    w = display_window(today)
    return w['average_months'][0]+'-01', w['history_through']


# Only the owner-approved destinations. Display names come from module i18n.
MODULES = ('module_dining', 'module_meal', 'module_person', 'module_workout',
           'module_health', 'module_outing', 'module_shopping', 'module_admin',
           'module_work', 'module_transport', 'module_invest', 'module_business')


def display_merchant(name):
    """Clean examples only; classification and source evidence keep raw names."""
    raw = (name or '').strip()
    name = ' '.join(raw.split())
    for pattern, label in (
            (r'^(?:anthropic|claude)\b', 'Claude'),
            (r'^(?:openai|chatgpt)\b', 'ChatGPT'),
            (r'^google\b', 'Google'),
            (r'^amazon(?:\.com)?\b', 'Amazon'),
            (r'^노란(?:\d+|우산공제)$', '노란우산공제'),
            (r'월세$', '월세')):
        if re.search(pattern, name, re.I):
            return label
    country = (r'UNITED STATES|UNITED KINGDOM|USA|US|HKG|HK|SGP|SG|'
               r'GBR|GB|UK|IRL|IE|DEU|DE|FRA|FR|JPN|JP|KOR|KR|'
               r'CAN|CA|AUS|AU|MYS|MY|CHN|CN|TWN|TW')
    city = (r'SAN FRANCISCO|SEATTLE|SHEUNG WAN|HONG KONG|NEW YORK|'
            r'LOS ANGELES|MOUNTAIN VIEW|SINGAPORE|LONDON|DUBLIN|'
            r'BERLIN|PARIS|TOKYO|SEOUL')
    location = city+'|'+country
    if re.search(r'\s(?:'+city+r')(?:\s+(?:'+country+r'))?$', name, re.I):
        return re.sub(r'(?:\s+(?:'+location+r'))+$', '', name, flags=re.I)
    # Padded network columns identify the city even when its name is new.
    columns = re.split(r'\s{2,}', raw)
    if len(columns)>1:
        if re.fullmatch(country, columns[-1], re.I):
            name = ' '.join(' '.join(columns[:-2] if len(columns)>2 else columns[:-1]).split())
        elif re.search(r'\s(?:'+country+r')$', columns[-1], re.I):
            name = ' '.join(' '.join(columns[:-1]).split())
    # Unpadded descriptors need a recognized suffix; keep unknown words
    # rather than guessing that part of the merchant itself is a city.
    return re.sub(r'(?:\s+(?:'+location+r'))+$', '', name, flags=re.I)


def _people(c, rows):
    """Read explicit links and unique exact names/aliases; never guess a person ID."""
    from ledger_v6 import _json_list
    names = collections.defaultdict(set)
    for person in D.rows(c, 'SELECT id,name,display_name,aliases FROM persons'):
        for name in [person['name'], person['display_name'], *_json_list(person['aliases'])]:
            if name:
                names[nfkc(name)].add(person['id'])
    linked = collections.defaultdict(set)
    for link in D.rows(c, "SELECT l.spend_id,p.id FROM spend_link l JOIN persons p ON CAST(p.id AS TEXT)=l.entity_ref WHERE l.entity_kind IN ('person','gift')"):
        linked[link['spend_id']].add(link['id'])
    for row in rows:
        who = re.sub(r'^(토스|토뱅|카카오|네이버)\s+', '', row['name'] or '').strip()
        exact = names.get(nfkc(who), set())
        row['person_ids'] = sorted(linked[row['id']] or (exact if len(exact)==1 else set()))


def _meaning(row, cache=None, meta=None):
    """Our merchant rules only. src_cat is deliberately never consulted."""
    from ledger_plan import merchant_kind
    name = row['name'] or ''
    if not name.strip():
        return None, 'unknown'
    linked = A.module_fact(row, meta or {})
    if linked.get('module') in MODULES:
        return linked['module'], 'ambiguous' if linked.get('inferred') else None
    if linked.get('module') in ('unknown', 'self_transfer'):
        return None, 'unknown'
    # Durable person/gift links describe who the money was for.
    if row.get('person_ids'):
        return 'module_person', None
    if row['kind']=='income':
        return None, 'salary' if re.search(r'급여|salary|payroll',name,re.I) else None
    if (row.get('category_override') or [None])[0]=='unknown_merchant':
        return None, 'unknown'
    if re.search(r'송금|선물|증여', name) or re.fullmatch(r'(?:토스\s+)?[가-힣]\*[가-힣]',name) or (row['kind']=='expense' and re.fullmatch(r'토스\s+[가-힣]{3}',name)):
        return 'module_person', None
    kind = merchant_kind(name,cache)
    if kind=='payment_processor':
        return None, 'unknown'
    # Specific merchant evidence precedes broad rules (Coupang Eats != shop).
    rules = (
        ('module_invest', '|'.join(re.escape(x) for x in EXCHANGE)+r'|증권|securities|brokerage'),
        ('module_business', r'노란(?:\d+|우산공제)'),
        ('module_work', r'anthropic|claude|openai|chatgpt|solapi|github|cursor|jetbrains|figma|slack|notion'),
        ('module_admin', r'월세|한전|한국전력|전기요금|전기세|^가스$|가스요금|가스비|도시가스|코원에너지|보험|한화손|KB손|DB손|세금|세무서|부가세|서울특별시|통신요금|통신비|통신납부|SK텔레콤|SK브로드밴드|인터넷제휴납부|세븐모바일|SKT|KT인터넷|LG유플러스|apple.*services'),
        ('module_transport', r'지하철|택시|티머니|캐시비|t[ -]?money|cashbee|주차|파킹|모빌리티|taxi|uber'),
        ('module_workout', r'휘트니스|피트니스|헬스장|체육관|gym'),
        ('module_health', r'치과|의원|약국|병원$|clinic|pharmacy'),
        ('module_outing', r'합주실|CGV|메가박스|시네마|오락실|아케이드|방탈출|마스터키|나들이|수피\s*포레스트'),
        ('module_dining', r'쿠팡이츠|테이블오더|배달의민족|요기요|술담화|테라브루|닭집|닭발|닭한마리|알요리|샐러링|샐러디|솥|양조장'),
        ('module_shopping', r'쿠팡|쿠페이|와우멤버십|무신사|스파오|에잇세컨즈|헤네스|ZARA|백화점|월드몰|의류|옷가게|현대카드M몰|amazon|네이버플러스멤버십'),
        ('module_meal', r'컬리|요마트|식재료|이마트|홈플러스|슈퍼마켓|허닭'),
    )
    for module, pattern in rules:
        if re.search(pattern,name,re.I):
            return module, 'fixed' if module in ('module_admin','module_work','module_business') else None
    if kind in ('delivery_service','alcohol','cafe_bakery','restaurant'):
        return 'module_dining', None
    if kind=='grocery':
        return 'module_meal', None
    if re.search(r'창우수산|장생건강원|일심돈체|삼신가로수|반티엔야오|로쏘주식회사|우스블랑|칙피스|잇프레쉬|피핌|멘노아지|미도인|프로티너|가조쿠|키치노야|마마문스|대월\(|옥소반|우몽|인디테이블|슈퍼타이|페이딤|플링크|포비|정동문화사|레드텅부티크|나즈드라비|숯토리|토리광|칠점팔|한성돼|황재벌|콩마루|연하동|더갓갓잇|츄럽|뿡뿡스크림|푸드',name,re.I):
        return 'module_dining', 'ambiguous'
    if row['kind']=='expense' and re.fullmatch(r'[김박윤이문엄조소신임][가-힣]{2}',name):
        return 'module_person', None
    # A meal-point transaction is independent food evidence, unlike src_cat.
    if row['class']=='meal_point':
        return 'module_dining', 'ambiguous'
    return None, None


def _regular_saving(direct):
    """Monthly contributions, after same-account same-day withdrawals.

    Require a stable stream on one day in at least two window months, within
    seven days of its usual calendar day and 20% of its typical amount.
    Interest and coin/round-up sweeps are never contributions. Keep the
    observed monthly amount rather than dividing two deposits by three.
    """
    days = collections.defaultdict(list)
    withdrawals = collections.Counter()
    withdrawal_amounts = collections.defaultdict(collections.Counter)
    for row in direct:
        if re.search(r'이자|interest', row['name'] or '', re.I):
            continue
        if row['class'] not in ('saving', 'own_transfer', 'saving_back'):
            continue
        if (row['amount'] < 0 or row['class']=='saving_back'
                or row['class']=='saving' and row['kind']=='expense'):
            withdrawals[row['date']] += abs(row['amount'])
            withdrawal_amounts[row['date']][abs(row['amount'])] += 1
        elif row['amount'] > 0:
            days[row['date']].append(row)
    streams = collections.defaultdict(lambda: collections.defaultdict(list))
    for day, deposits in days.items():
        remaining = withdrawals[day]
        unpaired = []
        # Cancel equal same-day pairs before allocating any partial withdrawal;
        # input order and differing transfer descriptions must not affect it.
        for row in deposits:
            if withdrawal_amounts[day][row['amount']]:
                withdrawal_amounts[day][row['amount']] -= 1
                remaining -= row['amount']
            else:
                unpaired.append(row)
        for row in unpaired:
            amount = max(0, row['amount'] - remaining)
            remaining = max(0, remaining - row['amount'])
            if not amount or re.search(r'동전|잔돈|round[ -]?up|sweep', row['name'] or '', re.I):
                continue
            streams[nfkc(row['name'] or '')][day].append((amount, row['id']))
    monthly_amount, total, ids, variable = 0, 0, [], False
    for days in streams.values():
        months = [day[:7] for day in days]
        if len(set(months)) < 2 or len(months) != len(set(months)):
            continue
        calendar_days = [min(int(day[8:10]), 28) for day in days]
        if max(calendar_days) - min(calendar_days) > 7:
            continue
        amounts = [sum(amount for amount, _ in rows) for rows in days.values()]
        typical = statistics.median(amounts)
        if any(abs(amount - typical) > typical * .2 for amount in amounts):
            continue
        variable = variable or len(set(amounts)) > 1
        monthly_amount += round(typical)
        total += sum(amounts)
        ids += [sid for rows in days.values() for _, sid in rows]
    return (dict(detail='saving', amount=total, monthly_amount=monthly_amount,
                 ids=ids, salary_day=None, variable=variable) if ids else None)


def read(c, today, cache=None, meta=None):
    since, until = window(today)
    if meta is None:
        from ledger_plan import facts
        meta = facts(c)
    methods = Methods(c)
    canonical = {mid:m for mid,m in methods.by_id.items() if methods.canon(mid) == mid and m['kind'] != 'cash'}
    rows = D.rows(c, "SELECT * FROM spend_entry WHERE date BETWEEN ? AND ? ORDER BY date,id", (since, until))
    for row in rows:
        row['class'] = row['class'] or ('income' if row['kind'] == 'income' else 'spend')
    rows = A.apply_categories(c, rows)
    streams = {f['id']:f for f in D.rows(c, 'SELECT * FROM fixed_cost')}
    stopped = {fid for fid,f in streams.items() if f['state'] in ('stop_suspected','stopped')}
    stopped_pairs = {r['pair_id'] for r in rows if r['stream_id'] in stopped and r['pair_id'] is not None}
    def current(row):
        return row['stream_id'] not in stopped and (row['pair_id'] is None or row['pair_id'] not in stopped_pairs)
    _people(c, rows)
    pools = collections.defaultdict(dict)
    own = collections.defaultdict(list)
    for row in rows:
        mid = methods.names.get(nfkc(row.get('method') or ''))
        if mid is None:
            continue
        mid = methods.canon(mid)
        own[mid].append(row)
        payment = (row['kind']=='expense' and row['class']=='spend' and row['amount']!=0)
        meal = row['class']=='meal_point' and row['amount'] != 0
        if payment or meal:
            pools[mid][row['id']] = dict(row, amount=abs(row['amount']))
    by_name = {m['name']:methods.canon(mid) for mid,m in methods.by_id.items()}
    links = []
    # Reuse the funding reader's stale-single-observation guard and latest
    # card-bill resolution. Canonical ids, never product-name guesses.
    for edge in R.roster(c, until)['links']:
        wallet, source = by_name.get(edge['wallet']), by_name.get(edge['source'])
        if wallet in canonical and source in canonical and wallet != source and (wallet, source) not in links:
            historical = c.execute('SELECT is_default,last_seen FROM pay_funding WHERE wallet_id=? AND source_id=?', (wallet,source)).fetchall()
            if canonical[wallet]['kind']=='easy_pay' and edge['origin']!='owner-stated' and historical and all(not r[0] and (r[1] or '')<since for r in historical):
                observations = R._funding_observations(c,methods)[(wallet,source)]
                if not any(since<=day<=until for day in observations.values()):
                    continue
            links.append((wallet, source))
    # The old correction roster omits dust products. A real link keeps those
    # products here, even when neither endpoint has any observed spending.
    for mid, method in canonical.items():
        source = method.get('linked_account_id')
        if source is not None:
            source = methods.canon(source)
            if source in canonical and source != mid and (mid, source) not in links:
                links.append((mid, source))
    for wallet, source in c.execute('SELECT wallet_id,source_id FROM pay_funding'):
        wallet, source = methods.canon(wallet), methods.canon(source)
        if wallet in canonical and source in canonical and wallet != source and (wallet, source) not in links:
            # Do not resurrect stale, rejected historical sources on a card.
            if canonical[wallet]['kind'] in ('debit_card','credit_card','corporate_card') and any(w==wallet for w,s in links):
                continue
            if canonical[wallet].get('dust') or canonical[source].get('dust'):
                links.append((wallet, source))
    # Deposits are also linked instruments. A stored transfer pair proves
    # the source even when the savings product is quiet in this window.
    # Never discover this edge from equal amounts or matching bank names.
    deposit_sources = collections.defaultdict(list)
    for pair in D.rows(c, "SELECT a.method AS target,b.method AS source,a.date "
                         "FROM spend_entry a JOIN spend_entry b ON a.pair_id=b.pair_id AND a.id<>b.id "
                         "WHERE a.class='own_transfer' AND a.amount>0 AND a.date<=? "
                         "AND ((b.class='own_transfer' AND b.amount<0) OR (b.class='saving' AND b.kind='expense' AND b.amount>0)) "
                         "ORDER BY a.date DESC,a.id DESC", (until,)):
        target = methods.names.get(nfkc(pair['target'] or ''))
        source = methods.names.get(nfkc(pair['source'] or ''))
        if target is None or source is None:
            continue
        target, source = methods.canon(target), methods.canon(source)
        if target in canonical and source in canonical and canonical[target]['kind'] in ('savings','securities') and canonical[source]['kind']=='account':
            deposit_sources[target].append((pair['date'],source))
    for target, observations in deposit_sources.items():
        if any(w==target for w,s in links):
            continue
        latest=observations[0][0]
        links.extend((target,source) for source in sorted({source for date,source in observations if date==latest}))
    result, omitted = [], []
    for mid, method in canonical.items():
        payments = list(pools[mid].values())
        direct = own[mid]
        saving = method['kind'] in ('savings', 'securities')
        # Every canonical instrument stays visible, including quiet/dust ones.
        sources = [dict(id=s, name=canonical[s]['name']) for w,s in sorted(links) if w==mid]
        drains = [dict(id=w, name=canonical[w]['name'], kind=canonical[w]['kind']) for w,s in sorted(links) if s==mid]
        module_rows = dict(pools[mid])
        # Accounts describe linked debit/wallet use as well as their own flows.
        # Do not roll credit-card spending into the bill-paying bank's modules.
        if method['kind']=='account':
            for drain in drains:
                if drain['kind']=='debit_card' or drain['kind']=='easy_pay' and len([s for w,s in links if w==drain['id']])==1:
                    module_rows.update(pools[drain['id']])
            # The approved investment activity may be a withdrawal, but never
            # let a savings/top-up/own-transfer/bill row become module spending.
            module_rows.update({r['id']:r for r in direct if r['class']=='saving_back'
                                and _meaning(r, cache, meta or {})[0]=='module_invest'})
        # Transit recharge recurrence is a projection, not a new fixed_cost
        # enum. The existing transport module supplies its display label.
        transit_months = collections.defaultdict(set)
        for row in module_rows.values():
            if D.is_transit(row['name']):
                transit_months[D.norm(row['name'])].add(row['date'][:7])
        if method['kind']=='account':
            module_rows = {sid:r for sid,r in module_rows.items() if current(r)}
        flows = collections.OrderedDict()
        for row in direct:
            if not current(row):
                continue
            detail = None
            key = None
            if method['kind']=='account' and row['kind']=='income' and _meaning(row, cache, meta or {})[1]=='salary':
                key = detail = 'salary'
            elif method['kind']=='account' and row['class']=='loan' and row['kind']=='expense':
                # Preserve the bank's loan number; merchant_key deliberately
                # strips digits and would merge different loans at one bank.
                family = ''.join(ch for ch in VOLATILE.sub('', nfkc(row['name'])).casefold() if ch.isalnum())
                key, detail = ('loan', family), 'loan'
            elif method['kind']=='account' and row['class']=='saving' and row['kind']=='expense':
                stream = streams.get(row['stream_id'], {})
                if stream.get('class')=='saving':
                    key, detail = ('saving', row['stream_id']), 'saving'
            if detail is None or not row['amount']:
                continue
            flow = flows.setdefault(key, dict(detail=detail, amount=0, ids=[], salary_days=[]))
            flow['amount'] += abs(row['amount'])
            flow['ids'].append(row['id'])
            if detail=='saving':
                flow['examples'] = [streams[row['stream_id']]['name']]
            if detail=='salary':
                flow['salary_days'].append(int(row['date'][8:10]))
        for flow in flows.values():
            flow['monthly_amount'] = round(flow['amount']/3)
            payments = [r for r in direct if r['id'] in flow['ids']]
            monthly = collections.Counter()
            for payment in payments:
                monthly[payment['date'][:7]] += abs(payment['amount'])
            flow['variable'] = len(set(monthly.values())) > 1
            flow['amount_note'] = P.notes_for(c, ['fixed:'+str(r['stream_id']) for r in payments if r['stream_id']])
            days = collections.Counter(flow.pop('salary_days'))
            flow['salary_day'] = days.most_common(1)[0][0] if days else None
        salary = flows.get('salary')
        if salary:
            from ledger_payday import read as payday
            history = D.rows(c, "SELECT * FROM spend_entry WHERE kind='income' AND date<=? ORDER BY date,id", (until,))
            history = [r for r in history if methods.canon(methods.names.get(nfkc(r['method'] or ''))) == mid
                       and _meaning(r, cache, meta or {})[1]=='salary']
            salary.update(payday(c, history, today, mid))
        if method['kind']=='account':
            for drain in drains:
                if drain['kind'] not in ('savings','securities'):
                    continue
                if any(drain['name'] in f.get('examples',[]) for f in flows.values()):
                    continue
                contribution = _regular_saving([r for r in own[drain['id']] if current(r)])
                if contribution:
                    flows[('saving',drain['id'])] = dict(contribution, examples=[drain['name']])
        if saving:
            contribution = _regular_saving([r for r in direct if current(r)])
            if contribution:
                flows['saving'] = contribution
        grouped = collections.OrderedDict()
        unknown, ambiguous, unresolved_people = [], [], []
        for row in module_rows.values():
            module, detail = _meaning(row, cache, meta or {})
            # One remainder per instrument, with the top three merchants.
            fixed = not D.is_taxi(row['name']) and (detail=='fixed' or bool(row.get('is_fixed')) or
                     row.get('stream_id') in streams and streams[row['stream_id']]['direction']=='expense' or
                     D.is_transit(row['name']) and len(transit_months[D.norm(row['name'])])>=2)
            key = (module or ('unknown' if detail=='unknown' else 'other'), fixed)
            if module is None and detail!='unknown':
                detail = 'other'
            group = grouped.setdefault(key, dict(token=module, name=row['name'], detail=detail,
                                       amount=0, examples=[], ids=[], person_ids=[], inferred=False, area_links=[], explicit=False, fixed=fixed))
            module_link = A.module_fact(row, meta)
            group['explicit'] = group['explicit'] or module_link.get('module') in MODULES
            group['inferred'] = group['inferred'] or module_link.get('inferred', False)
            if module_link.get('area_module'):
                group['area_links'].append(dict(id=row['id'], module=module_link['area_module'],
                                                inferred=module_link.get('area_inferred', False)))
            group['amount'] += abs(row['amount'])
            group['ids'].append(row['id'])
            group['person_ids'] = sorted(set(group['person_ids']) | set(row.get('person_ids',[])))
            if module is None:
                unknown.append(dict(id=row['id'], merchant=row['name'], amount=abs(row['amount']), reason='unknown' if detail=='unknown' else 'unmapped'))
            if detail=='ambiguous':
                ambiguous.append(row['name'])
            if module=='module_person' and not row.get('person_ids'):
                unresolved_people.append(row['name'])
        for group in grouped.values():
            group['monthly_amount'] = round(group['amount']/3)
            amounts = collections.Counter()
            for row in module_rows.values():
                if row['id'] in group['ids']:
                    merchant = ('노란우산공제' if group['token']=='module_business' and re.search(r'노란(?:\d+|우산공제)',row['name'])
                                else display_merchant(row['name']))
                    amounts[merchant] += abs(row['amount'])
            group['examples'] = [name for name,_ in amounts.most_common(3)]
            # Different fixed merchants can appear in different months (tax,
            # annual premiums). Only linked/explicit recurring amounts prove
            # variation; one-off taxes must not label stable rent as variable.
            monthly = collections.defaultdict(collections.Counter)
            for r in module_rows.values():
                if r['id'] in group['ids'] and (r['stream_id'] or r.get('is_fixed')):
                    key = ('stream', r['stream_id']) if r['stream_id'] else ('merchant', D.norm(r['name']))
                    monthly[key][r['date'][:7]] += abs(r['amount'])
            group['variable'] = (group['fixed'] and
                                 (any(len(set(m.values()))>1 for m in monthly.values()) or
                                  any(D.is_transit(r['name']) for r in module_rows.values() if r['id'] in group['ids'])))
            group['amount_note'] = P.notes_for(c, ['fixed:'+str(r['stream_id'])
                for r in module_rows.values() if r['id'] in group['ids'] and r['stream_id']])
            if group['token']=='module_transport':
                group['examples'] = [re.sub(r'^(?:주식회사|\(주\))\s*', '', name) for name in group['examples']]

        visible_groups = sorted(grouped.values(),key=lambda a:-a['amount'])
        if method['kind'] in ('credit_card','corporate_card','debit_card','easy_pay') and visible_groups:
            other = [a for a in visible_groups if a['detail']=='other']
            visible_groups = [a for a in visible_groups if a['detail']!='other' and a['amount']>=visible_groups[0]['amount']*0.15][:3-len(other)] + other
        # An owner-resolved payment must remain visible even below the usual
        # top-module threshold; keep the existing merchant summaries too.
        visible_groups += [a for a in grouped.values() if a['explicit'] and a not in visible_groups]
        visible_groups.sort(key=lambda a:(a['detail']=='other', -a['amount']))
        guard_direct = [r for r in direct if current(r)]
        guard = any(r['kind']!='income' and (r['class'] in ('card_bill','loan') or r.get('is_fixed')
                    or _meaning(r, cache, meta or {})[1]=='fixed') for r in guard_direct) or any(d['kind'] in ('credit_card','corporate_card') for d in drains)
        guard_names = list(dict.fromkeys(['월세' for r in guard_direct if '월세' in r['name']] +
                           [(re.match(r'^(현대|삼성|KB국민|롯데|우리|하나|신한|NH농협|BC)',d['name']).group(1)+'카드값'
                             if re.match(r'^(현대|삼성|KB국민|롯데|우리|하나|신한|NH농협|BC)',d['name']) else d['name']+' 카드값')
                            for d in drains if d['kind'] in ('credit_card','corporate_card')]))
        count = len(payments)
        result.append(dict(id=mid, name=method['name'], kind=method['kind'],
                           layer='saving' if saving else _method_layer(method),
                           count=count, activity_count=len(direct), amount=sum(r['amount'] for r in payments),
                           monthly_count=count/3, monthly_amount=round(sum(r['amount'] for r in payments)/3),
                           modules=visible_groups, all_modules=list(grouped.values()), flows=list(flows.values()), unresolved=unknown,
                           ambiguous_merchants=sorted(set(ambiguous)), unresolved_people=sorted(set(unresolved_people)),
                           sources=sources, drains=drains, balance_guard=guard and method['kind']=='account',
                           guard_names=guard_names,
                           sparse_count=((len({sid for d in drains if d['kind'] in ('debit_card','easy_pay') for sid in pools[d['id']]}) or count)
                                         if method['kind']=='account' else count)))
    loans = [f for instrument in result for f in instrument['flows'] if f['detail']=='loan']
    for number, loan in enumerate(sorted(loans, key=lambda f:-f['amount']), 1):
        loan['number'] = number
    return dict(version=VERSION, since=since, until=until, months=3,
                instruments=result, omitted=omitted)
