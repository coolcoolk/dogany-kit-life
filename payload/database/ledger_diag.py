"""ledger_diag.py -- recurring streams, the one-month diagnosis, and its levers
(ledger redesign v6, migration 075; owner 2026-10-12, decisions D1-D5).

READS landed rows only (spend_entry + its class). A pending intake item is
never counted (G-A). Every number the onboarding or a follow-up says comes
from `diagnosis()`; the model never sums.

STREAM (G-C): one recurring money line on fixed_cost -- income, fixed, loan,
saving, subscription. current_amount = the LAST month's occurrence, never a
window mean. An expected but unseen stream is 'late', or 'stop_suspected'
after three months. These states never manufacture a historical payment.
Only the owner's word stops a stream (stream-stop). Historical actuals and
future obligations have separate meanings (owner rehearsal 2026-10-30).

DIAGNOSIS: income (regular streams + other) | money out = regular streams
(housing / loan / insurance / utility / telecom / subscription / saving ...)
+ variable spend. Money put into savings / securities ("saved money") is a
line INSIDE money out; money pulled back out of them is a line of its own,
never income (D3). Own-account moves, top-ups, card bills, company
pass-through and employer meal points are in neither side.
"""
import collections
import datetime
import json
import os
import re
import statistics

import ledger_profile as LP

HERE = os.path.dirname(os.path.abspath(__file__))
NON_MONEY = ('own_transfer', 'topup', 'card_bill', 'pass_through', 'meal_point', 'saving_back')
OUT_CLASSES = ('spend', 'saving', 'loan')
CHANGE_TOL = 0.10
LATE_MONTHS = 1
SUSPECT_MONTHS = 3
MIN_COVERAGE = 0.6
DAY_TOL = 4
SAME_MONEY_TOL = 0.01
REG_MATCH_TOL = 0.10
# Generic product words (mobile/internet) also occur in transit and shopping.
TELECOM_WORDS = tuple(w for w in LP.TELECOM if w not in ('통신', '모바일', '인터넷'))
TRANSIT_WORDS = ('티머니', 'tmoney', 't-money', 't money', '캐시비')


def is_transit(name):
    return LP._has((name or '').casefold(), TRANSIT_WORDS)


def is_taxi(name):
    return bool(re.search(r'택시|카카오모빌리티|taxi|uber', name or '', re.I))


PG_WORDS = ('\ud398\uc774\uba3c\uce20', 'payments', '\uc815\ubcf4\ud1b5\uc2e0', '\uacb0\uc81c\ub300\ud589', 'kcp', 'nice', '\ub098\uc774\uc2a4', 'tosspayments', '\ud1a0\uc2a4\ud398\uc774\uba3c\uce20', 'nhn')
SAVING_WORDS = LP.SAVING + ('\ub178\ub780',)
FIXED_CLASSES = ('housing', 'loan', 'insurance', 'utility', 'telecom', 'subscription', 'saving', 'tax', 'other', 'income')
CLASS_ORDER = ('housing', 'loan', 'insurance', 'saving', 'utility', 'telecom', 'tax', 'subscription', 'other')


def _data(name):
    with open(os.path.join(HERE, name), encoding='utf-8') as f:
        return json.load(f)


def rows(c, sql, args=()):
    cur = c.execute(sql, args)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur]


def norm(s):
    return LP._norm(s) if hasattr(LP, '_norm') else ''.join(ch for ch in (s or '').casefold() if ch.isalnum())


def month_of(iso):
    return iso[:7]


def prev_month(ym, n=1):
    y, m = int(ym[:4]), int(ym[5:7])
    t = y * 12 + (m - 1) - n
    return '%04d-%02d' % (t // 12, t % 12 + 1)


def month_index(ym):
    return int(ym[:4]) * 12 + int(ym[5:7])


def basis_month(conn):
    """The last FULL month of the landed ledger: the latest month before the month of the newest row, or that
    month itself when the newest row is on its last day."""
    last = conn.execute("SELECT MAX(date) FROM spend_entry WHERE kind IN ('expense','income','transfer')").fetchone()[0]
    if not last:
        return None
    d = datetime.date.fromisoformat(last)
    nxt = d + datetime.timedelta(days=1)
    return month_of(last) if nxt.month != d.month else prev_month(month_of(last))


# ---- stream detection --------------------------------------------------------------------

def _landed(conn):
    landed = rows(conn, "SELECT s.id, s.date, s.amount, s.name, s.method, s.kind, COALESCE(s.class,CASE WHEN s.kind='income' THEN 'income' ELSE 'spend' END) AS class, "
                      "s.pair_id, s.src_cat, s.stream_id, s.at_time FROM spend_entry s WHERE s.kind IN ('expense','income') "
                      "AND COALESCE(s.class,'spend') IN ('spend','saving','loan','income') AND NOT EXISTS(SELECT 1 FROM ledger_preference p WHERE p.topic='ledger_review_item' AND p.status='active' AND CAST(p.item AS INTEGER)=s.id AND json_extract(p.detail,'$.role')='one_off') AND s.amount > 0 AND s.scope <> 'business' ORDER BY s.date, s.id")

    from ledger_review import annotations
    facts = annotations(conn)
    for r in landed:
        if r['class']=='income' and (facts.get(r['id'],{}).get('salary_evidence')
                                     or re.search(r'급여|\bsalary\b',r['name'],re.I)):
            r['_stream_key'] = 'cat:salary'
    # Identity is learned from all recorded bills, never from payee alone.
    # Existing unambiguous keys stay valid; only collisions gain a qualifier.
    groups = collections.defaultdict(list)
    for r in landed:
        groups[stream_key(r, {})].append(r)
    services = set(LP.services())
    legacy_classes = {k:cls for k,cls in conn.execute('SELECT k.key,f.class FROM fixed_cost_key k JOIN fixed_cost f ON f.id=k.fixed_id')}
    for key, bills in groups.items():
        if key.startswith(('cat:', 'to:')) or key in services:
            continue
        classification = {r['src_cat']: _class_of([r],key) for r in {r['src_cat']:r for r in bills}.values()}
        categories = {r['src_cat'] or '' for r in bills}
        by_category = collections.defaultdict(list)
        for r in bills:
            by_category[r['src_cat'] or ''].append(r)
        for category, members in by_category.items():
            clusters = [[]]
            for r in sorted(members, key=lambda r:r['amount']):
                if clusters[-1] and r['amount'] > clusters[-1][-1]['amount'] * 3:
                    clusters.append([])
                clusters[-1].append(r)
            for cluster in clusters:
                identity = key
                if len(categories)>1:
                    identity += '|cat:' + norm(category)
                if len(clusters)>1:
                    identity += '|band:' + str(min(r['amount'] for r in cluster))
                for r in cluster:
                    if r['class'] != 'saving':
                        r['_stream_key'] = identity
                        r['_split_stream'] = key in legacy_classes and legacy_classes[key] not in (None, 'other', classification[r['src_cat']])
    return landed


def _partner_methods(conn):
    out = {}
    for r in conn.execute("SELECT a.id, b.method FROM spend_entry a JOIN spend_entry b ON b.pair_id = a.pair_id "
                          "AND b.id <> a.id WHERE a.pair_id IS NOT NULL AND a.class = 'saving'"):
        out[r[0]] = r[1]
    return out


CATEGORY_KEYS = (('\uc6d4\uc138', 'cat:rent'), ('\uae09\uc5ec/', 'cat:salary'), ('\uc804\uae30\uc138', 'cat:electric'), ('\uac00\uc2a4\ube44', 'cat:gas'),
                 ('\uc218\ub3c4', 'cat:water'))


def stream_key(r, partner):
    if r.get('_stream_key'):
        return r['_stream_key']
    if r['class'] == 'saving' and r['id'] in partner and partner[r['id']]:
        return 'to:' + norm(partner[r['id']])
    sc = r['src_cat'] or ''
    for frag, key in CATEGORY_KEYS:
        if frag in sc and (key != 'cat:salary' or r['kind'] == 'income'):
            return key
    k = LP.merchant_key(r['name'])
    return k or norm(r['name'])


def _day_ok(days, tol):
    if len(days) < 2:
        return True
    med = statistics.median(days)
    good = sum(1 for d in days if min(abs(d - med), 31 - abs(d - med)) <= tol)
    return good / len(days) >= 0.6


def _class_of(rs, key):
    name = rs[-1]['name']
    k = norm(name) + key
    cats = ' '.join(sorted({r['src_cat'] or '' for r in rs}))
    if rs[-1]['kind'] == 'income':
        return 'income'
    if any(r['class'] == 'loan' for r in rs):
        return 'loan'
    if any(r['class'] == 'saving' for r in rs):
        return 'saving'
    if LP._CATFIX.get(key)==LP.SUB_CAT:
        return 'subscription'
    if is_transit(name):
        return 'transport'
    if '인터넷상거래' in name:
        return 'other'
    if '\uc6d4\uc138' in cats or LP._has(k, LP.RENT):
        return 'housing'
    if '\uc138\uae08' in cats or LP._has(k, ('\uad6d\uc138', '\uc9c0\ubc29\uc138', '\uc138\uae08')):
        return 'tax'
    if '\ubcf4\ud5d8' in cats or LP._has(k, LP.INSURANCE) or LP.INSURANCE_RX.search(key):
        return 'insurance'
    if '\ud1b5\uc2e0/\uc778\ud130\ub137' in cats or LP._has(k, TELECOM_WORDS):
        return 'telecom'
    if key in ('cat:electric', 'cat:gas', 'cat:water') or '\uad00\ub9ac\ube44' in cats or '\uac00\uc2a4' in cats or LP._has(k, LP.UTILITY):
        return 'utility'
    if '\uc11c\ube44\uc2a4\uad6c\ub3c5' in cats or LP._has(key, LP.SUBSCRIPTION) or LP._has(k, LP.MEMBERSHIP) or key in LP.services():
        return 'subscription'
    return 'other'


def _candidates(landed, partner, end_month, short_history=False):
    by = collections.defaultdict(list)
    for r in landed:
        by[stream_key(r, partner)].append(r)
    svc = LP.services()
    out = []
    for key, rs in by.items():
        if not key or len(key) < 2 or any(w in key for w in PG_WORDS) or is_taxi(rs[-1]['name']):
            continue
        if key in svc:                              # a tiered service: the PLAN charges are the stream (usage is not)
            plan = [r for r in rs if LP.tier_of({'kind': r['kind'], 'amount': r['amount'], 'merchant': r['name']}, svc[key])]
            if plan:
                rs = plan
        months = sorted({month_of(r['date']) for r in rs})
        cls = _class_of(rs, key)
        transit = cls == 'transport'
        if transit:
            cls = 'other'  # schema-safe recurring recharge, not telecom
        # A three-month onboarding window can contain two bills and one missed
        # month. Strong fixed-cost classes still qualify; everyday spend and
        # income retain the existing three-observation guard.
        minimum = 2 if short_history and cls not in ('other', 'income') else 3
        if len(months) < minimum:
            continue
        span = month_index(months[-1]) - month_index(months[0]) + 1
        if len(months) / span < 0.5:
            continue
        if len(months) / span < MIN_COVERAGE and cls not in ('utility', 'loan', 'insurance', 'telecom', 'saving', 'tax', 'income'):
            continue
        per_month = collections.defaultdict(list)
        for r in rs:
            per_month[month_of(r['date'])].append(r)
        if not transit and cls != 'subscription' and max(len(v) for v in per_month.values()) > 3 and cls != 'income':
            continue
        cats = {(r['src_cat'] or '').split('/')[0] for r in rs}
        if not transit and cls == 'other' and cats & set(LP.EVERYDAY_CATS) and not LP._has(key, LP.MEMBERSHIP):
            continue
        tot = {m: sum(r['amount'] for r in v) for m, v in per_month.items()}
        if key in svc:                              # a switch inside a month: the plan in force is the LAST charge
            tot = {m: v[-1]['amount'] for m, v in per_month.items()}
        if cls == 'income':
            per_month = {m: [r for r in v if r['amount'] > 10] for m, v in per_month.items()}
            per_month = {m: v for m, v in per_month.items() if v}
            months = sorted(per_month)
            if len(months) < 3:
                continue
            tot = {m: sum(r['amount'] for r in v) for m, v in per_month.items()}
            if statistics.median(tot.values()) < LP.INCOME_MIN:
                continue
        lo, hi = min(tot.values()), max(tot.values())
        stable = lo > 0 and hi / lo <= 1.3
        variable_ok = transit or cls in ('utility', 'loan', 'insurance', 'telecom', 'saving', 'subscription', 'tax', 'income') \
            and len(months) / span >= 0.5
        if not (stable or variable_ok):
            continue
        days = [int(r['date'][8:10]) for r in rs]
        if not transit and cls != 'income' and not key.startswith('cat:') and not _day_ok(days, 7 if cls in ('utility', 'tax') else DAY_TOL):
            continue
        if cls == 'other' and statistics.median(tot.values()) < LP.TINY_FIXED and len(months) / span < 0.8:
            continue
        out.append({'keys': [key], 'rows': rs, 'class': cls, 'months': months, 'tot': tot, 'span': span})
    # One recorded charge is not recurrence evidence. Owner-confirmed streams
    # enter through the review writer instead.
    return out


def _same_money(a, b):
    """two candidate keys paying the same money on the same days -> one stream (L8)"""
    if (a['class'] != b['class'] or a['class'] in ('subscription', 'income')
            or is_transit(a['rows'][-1]['name']) != is_transit(b['rows'][-1]['name'])):
        return False
    shared = sorted(set(a['tot']) & set(b['tot']))
    if len(shared) < 3:
        return False
    close = sum(1 for m in shared if abs(a['tot'][m] - b['tot'][m]) <= SAME_MONEY_TOL * max(a['tot'][m], b['tot'][m])
                and max(a['tot'][m], b['tot'][m]) >= 50000)
    if close < 3:
        return False
    # same money in the SAME month = two bills; one bill rarely repeats under two names in the same month
    return True


def _merge_groups(cands):
    parent = list(range(len(cands)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(cands)):
        for j in range(i + 1, len(cands)):
            if _same_money(cands[i], cands[j]):
                parent[find(j)] = find(i)
    groups = collections.defaultdict(list)
    for i in range(len(cands)):
        groups[find(i)].append(cands[i])
    out = []
    for g in groups.values():
        if len(g) == 1:
            out.append(g[0])
            continue
        # two names, same money in DIFFERENT months is one bill renamed; same month -> two bills: keep apart
        months = collections.Counter(m for c in g for m in c['months'])
        if max(months.values()) > 1:
            out.extend(g)
            continue
        rs = sorted((r for c in g for r in c['rows']), key=lambda r: (r['date'], r['id']))
        tot = {}
        for c in g:
            tot.update(c['tot'])
        out.append({'keys': [k for c in g for k in c['keys']], 'rows': rs, 'class': g[0]['class'],
                    'months': sorted(months), 'tot': tot, 'span': g[0]['span']})
    return out


CATEGORY_NAMES = {'cat:rent': '\uc6d4\uc138', 'cat:salary': '\uae09\uc5ec', 'cat:electric': '\uc804\uae30\uc694\uae08', 'cat:gas': '\uac00\uc2a4\uc694\uae08', 'cat:water': '\uc218\ub3c4\uc694\uae08'}


def _clean_name(raw, *, merchant=False):
    s = LP.display_name(LP.VOLATILE.sub('', raw or ''), merchant=merchant)
    for w in LP.NAME_NOISE:
        s = s.replace(w, '')
    s = re.sub(r'[\s\-_]*\d+([\s\-_]*\d+)*$', '', s).strip(' -_')
    return s or LP.display_name(raw)


def _member_name(raw, conn=None):
    if conn is not None:
        from ledger_names import Names
        fact = Names(conn).fact(raw)
        if fact:
            return fact['display_name']
    # Reuse the kit's display cleanup after removing volatile bank memo tails.
    name = _clean_name(LP.VOLATILE.sub('', raw or ''))
    if is_transit(name):
        return name
    return LP.fixed_name(name) if any(ch.isalpha() for ch in name) else ''


def _display(cand, partner_name):
    rs = cand['rows']
    if cand['keys'][0] in CATEGORY_NAMES:
        return CATEGORY_NAMES[cand['keys'][0]]
    svc = LP.services()
    for k in cand['keys']:
        if k in svc and svc[k].get('name'):
            return svc[k]['name']
    if cand['class'] == 'saving' and cand['keys'][0].startswith('to:') and partner_name:
        return partner_name
    return _clean_name(rs[-1]['name']) or cand['keys'][0]


def _nearest_registered(conn):
    return rows(conn, "SELECT f.*, (SELECT COUNT(*) FROM fixed_cost_key k WHERE k.fixed_id = f.id) AS nkeys FROM fixed_cost f")


def stream_reconcile(conn, month=None, today=None, short_history=False):
    from ledger_review import classify_insurance, annotations
    classify_insurance(conn)
    from ledger_v6 import reconcile_categories
    reconcile_categories(conn)
    month = month or basis_month(conn)
    if month is None:
        return {'count': 0, 'month': None, 'streams': []}
    LP._review(conn)
    declared_subscriptions={r[0] for r in conn.execute("SELECT id FROM fixed_cost WHERE class='subscription' AND source IN ('owner','chat','interview')")}
    from ledger_plan import facts
    declared_subscriptions.update(int(key[20:]) for key,fact in facts(conn).items()
        if key.startswith('subscription:stream:') and key[20:].isdigit() and fact.get('kind')=='subscription')
    subscription_links=[(r[0],r[1]) for r in conn.execute('SELECT id,stream_id FROM spend_entry WHERE stream_id IS NOT NULL') if r[1] in declared_subscriptions]
    landed = [r for r in _landed(conn) if month_of(r['date']) <= month]
    partner = _partner_methods(conn)
    pname = {}
    for r in conn.execute("SELECT a.id, b.method FROM spend_entry a JOIN spend_entry b ON b.pair_id = a.pair_id "
                          "AND b.id <> a.id WHERE a.pair_id IS NOT NULL AND a.class = 'saving'"):
        pname[r[0]] = r[1]
    # Declared subscription membership is stronger than another recurrence guess.
    # Build one candidate per recorded stream, including its receipt variants.
    from ledger_plan import _subscription_identities
    identities=_subscription_identities(conn)
    declared_rows=collections.defaultdict(list)
    remaining=[]
    for row in landed:
        identity=identities.get(' '.join(row['name'].split()).casefold(),'')
        fid=row.get('stream_id') or (int(identity[7:]) if identity.startswith('stream:') else None)
        if fid in declared_subscriptions:
            declared_rows[fid].append(dict(row,stream_id=fid))
        else:
            remaining.append(row)
    cands = _merge_groups(_candidates(remaining, partner, month, short_history=short_history))
    for fid,members in declared_rows.items():
        totals=collections.Counter()
        for row in members: totals[month_of(row['date'])]+=row['amount']
        months=sorted(totals)
        cands.append(dict(keys=sorted({stream_key(row,partner) for row in members}),
            rows=sorted(members,key=lambda row:(row['date'],row['id'])),class_='subscription',
            months=months,tot=dict(totals),span=month_index(months[-1])-month_index(months[0])+1))
        cands[-1]['class']=cands[-1].pop('class_')
    fixed = _nearest_registered(conn)
    keyed = {k: fid for fid, k in conn.execute('SELECT fixed_id, key FROM fixed_cost_key')}
    aliases = LP.fixed_aliases(conn)
    reg_alias = {}
    for fid, al in aliases.items():
        for a in al:
            reg_alias[a] = fid
    explicit_subs = {r[0] for r in conn.execute("SELECT fixed_id FROM fixed_cost_key WHERE key LIKE 'owner-subscription:%'")}
    used = set()
    results = []
    conn.execute("UPDATE spend_entry SET stream_id=NULL WHERE stream_id IS NOT NULL")
    for cand in sorted(cands, key=lambda c: -statistics.median(c['tot'].values())):
        declared={r['stream_id'] for r in cand['rows'] if r.get('stream_id') in declared_subscriptions}
        fid = next(iter(declared)) if len(declared)==1 else None
        for k in cand['keys']:
            if fid is not None: break
            if k in keyed:
                fid = keyed[k]
                break
        cur = cand['tot'][cand['months'][-1]]
        if fid is None:
            for key in cand['keys']:
                legacy = keyed.get(key.split('|',1)[0])
                f = next((f for f in fixed if f['id'] == legacy), None)
                if f and legacy not in used and stream_compatible(f, cand['rows'][-1]):
                    fid = legacy
                    break
        if fid is None:
            best = None
            for f in fixed:
                if f['id'] in used or f['id'] in explicit_subs or f['source'] == 'ledger_v6' or f['state'] == 'stopped':
                    continue
                if (f['direction'] == 'income') != (cand['class'] == 'income'):
                    continue
                if not stream_compatible(f, cand['rows'][-1]):
                    continue
                fk = LP.merchant_key(f['name'])
                score = 0
                if (fk and any(k.split('|',1)[0] == fk for k in cand['keys'])) or any(k in cand['keys'] for k in _category_keys(f)):
                    score = 3
                elif any(a in cand['keys'] for a in aliases.get(f['id'], [])):
                    score = 3
                elif f['amount'] >= 3000 and abs(f['amount'] - cur) <= REG_MATCH_TOL * f['amount'] and \
                        len(cand['months']) >= STREAM_MIN_MONTHS_FOR_AMOUNT and (f['cycle'] or 'monthly') == 'monthly' and \
                        (f['class'] if f['source'] == 'ledger_v6' else (_guess_class(f['name']) if not f['class'] or f['class'] == 'other' else f['class'])) in (cand['class'], 'other'):
                    score = 2
                if score and (best is None or score > best[0]):
                    best = (score, f['id'])
            fid = best[1] if best else None
        if fid is not None:
            used.add(fid)
        results.append((cand, fid))
    # create / update
    out = []
    for cand, fid in results:
        keys = cand['keys']
        last_row = cand['rows'][-1]
        direction = 'income' if cand['class'] == 'income' else 'expense'
        name = _display(cand, pname.get(last_row['id']) if cand['class'] == 'saving' else None)
        if fid is None:
            cur_ = conn.execute("INSERT INTO fixed_cost(name, amount, direction, cycle, source, class) "
                                "VALUES(?,?,?,?,?,?)", (name, cand['tot'][cand['months'][-1]], direction, 'monthly', 'ledger_v6',
                                                        cand['class']))
            fid = cur_.lastrowid
        for k in keys:
            conn.execute('INSERT OR REPLACE INTO fixed_cost_key(fixed_id, key) VALUES(?,?)', (fid, k))
        out.append(_update_stream(conn, fid, cand, month))
    # registered rows no detection covered: no member in 3 months -> stop_suspected, STILL counted (L10)
    conn.executemany('UPDATE spend_entry SET stream_id=? WHERE id=?',[(fid,sid) for sid,fid in subscription_links])
    assigned = _landed(conn)
    for f in conn.execute("SELECT id FROM fixed_cost").fetchall():
        fid = f[0]
        if fid in {o['id'] for o in out}:
            continue
        r = rows(conn, 'SELECT * FROM fixed_cost WHERE id=?', (fid,))[0]
        if r['state'] == 'stopped':
            continue
        out.append(_update_registered(conn, r, month, assigned, partner))
    # Explicit membership survives plan-tier inference and its attachment reset.
    for sid,detail in annotations(conn).items():
        fid=detail.get('fixed_stream_id') or detail.get('subscription_stream_id')
        if detail.get('role') in ('housing','loan','tax','subscription','telecom','insurance','utility','other') and fid and conn.execute("SELECT 1 FROM fixed_cost WHERE id=? AND state<>'stopped'",(fid,)).fetchone():
            conn.execute('UPDATE spend_entry SET stream_id=?,is_fixed=1 WHERE id=?',(fid,sid))
    _propose_keep(conn, month)
    conn.execute("UPDATE spend_entry SET stream_id=NULL WHERE stream_id IS NOT NULL AND stream_id NOT IN (SELECT id FROM fixed_cost)")
    from fixed_pay_mode import refresh
    refresh(conn)
    return {'month': month, 'count': len(out), 'streams': out}


STREAM_MIN_MONTHS_FOR_AMOUNT = 3
DORMANT_MONTHS = 6


def _category_keys(f):
    ks = set()
    if LP._has(f['name'] or '', LP.RENT):
        ks.add('cat:rent')
    if f['direction'] == 'income' and '\uae09\uc5ec' in (f['name'] or ''):
        ks.add('cat:salary')
    if LP._has(f['name'] or '', ('\ud55c\uad6d\uc804\ub825', '\uc804\uae30', '\ud55c\uc804')):
        ks.add('cat:electric')
    if LP._has(f['name'] or '', ('\uac00\uc2a4', '\uc5d0\ub108\uc9c0')):
        ks.add('cat:gas')
    if '\uc218\ub3c4' in (f['name'] or ''):
        ks.add('cat:water')
    return ks


def _update_stream(conn, fid, cand, month):
    old = rows(conn, 'SELECT * FROM fixed_cost WHERE id=?', (fid,))[0]
    rs = cand['rows']
    for r in rs:
        conn.execute('UPDATE spend_entry SET stream_id=? WHERE id=?', (fid, r['id']))
    months = cand['months']
    tot = cand['tot']
    last_m = months[-1]
    gap = month_index(month) - month_index(last_m)
    cur = tot[last_m]
    last_seen = max(r['date'] for r in rs if month_of(r['date']) == last_m)
    prev = tot[months[-2]] if len(months) > 1 else None
    state = old['state']
    changed_from, changed_at = old['changed_from'], old['changed_at']
    if state != 'stopped':
        if gap <= 0:
            state = 'active'
            if prev and prev > 0 and abs(cur - prev) / prev > CHANGE_TOL:
                state, changed_from, changed_at = 'changed', prev, last_seen
            elif changed_at and last_m and month_of(changed_at) == last_m:
                state = 'changed'
        elif gap <= LATE_MONTHS + 1 and cand['class'] != 'income':
            state = 'late'
        elif gap >= SUSPECT_MONTHS:
            state = 'stop_suspected'
        else:
            state = 'late'
        if cand['class'] == 'income' and gap >= 1:
            state = 'late' if gap < SUSPECT_MONTHS else 'stop_suspected'
    d = datetime.date.fromisoformat(last_seen)
    nxt_m = month_index(last_m) + 1
    day = min(d.day, 28)
    expected = '%04d-%02d-%02d' % ((nxt_m - 1) // 12, (nxt_m - 1) % 12 + 1, day)
    if cand['class'] == 'income':
        from ledger_payday import read as payday
        from ledger_v6 import Methods, nfkc
        methods = Methods(conn)
        mid = methods.names.get(nfkc(rs[-1].get('method') or ''))
        schedule = payday(conn, rs, last_seen, methods.canon(mid) if mid is not None else None)
        if schedule:
            expected = schedule['next_payday']
    conn.execute("UPDATE fixed_cost SET class=?, current_amount=?, amount=CASE WHEN source='ledger_v6' THEN ? ELSE amount END, "
                 "last_seen=?, expected_next=?, state=?, changed_from=?, changed_at=?, direction=? WHERE id=?",
                 (cand['class'], cur, cur, last_seen, expected, state, changed_from, changed_at,
                  'income' if cand['class'] == 'income' else 'expense', fid))
    return {'id': fid, 'name': old['name'], 'class': cand['class'], 'current_amount': cur, 'state': state,
            'last_seen': last_seen, 'keys': cand['keys']}


def stream_compatible(stream, bill):
    cls = _class_of([bill], stream_key(bill, {}))
    declared = stream.get('class') or _guess_class(stream['name'])
    if declared not in ('other', cls):
        return False
    amount = stream['amount'] or stream.get('current_amount')
    if amount and bill['amount']:
        tolerance = 1.3 if stream.get('cycle') not in (None, 'monthly') else 3
        if max(amount, bill['amount']) / min(amount, bill['amount']) > tolerance:
            return False
    return True


def _update_registered(conn, r, month, landed=None, partner=None):
    """A registered row the detection did not cover. Its occurrences (landed rows under its key / alias, or the same
    bill by amount for a yearly one) are attached; with none it is record-first: it stays in the total until the
    owner's word (stop_suspected), or -- never seen at all -- is listed apart (ledger_diag.diagnosis, dormant)."""
    state = r['state']
    cls = r['class'] or _guess_class(r['name'])
    cycle = r['cycle'] or 'monthly'
    keys = {LP.merchant_key(r['name'])} | set(LP.fixed_aliases(conn).get(r['id'], [])) | _category_keys(r)
    # Stream keys survive cleaned display names and the reconciliation reset.
    keys |= {k[0] for k in conn.execute('SELECT key FROM fixed_cost_key WHERE fixed_id=?', (r['id'],))}
    keys.discard('')
    partner = _partner_methods(conn) if partner is None else partner
    occ = []
    for x in [y for y in (_landed(conn) if landed is None else landed) if month_of(y['date']) <= month]:
        if x['stream_id'] is not None and x['stream_id'] != r['id']:
            continue
        if x['kind'] == ('income' if r['direction'] == 'income' else 'expense') \
                and (x['stream_id']==r['id'] or (stream_key(x, partner) in keys or stream_key(x, partner).split('|',1)[0] in keys) and stream_compatible(r, x)) and (x['class'] != 'saving' or cls == 'saving'):
            occ.append(x)
    cur = r['current_amount'] if r['current_amount'] is not None else r['amount']
    last = r['last_seen']
    if occ:
        for x in occ:
            conn.execute('UPDATE spend_entry SET stream_id=? WHERE id=?', (r['id'], x['id']))
            x['stream_id'] = r['id']
        last_row = max(occ, key=lambda x: (x['date'], x['id']))
        last = last_row['date']
        same_day = [x['amount'] for x in occ if x['date'] == last]
        cur = sum(same_day)
        for k in keys:
            conn.execute('INSERT OR IGNORE INTO fixed_cost_key(fixed_id, key) VALUES(?,?)', (r['id'], k))
    if state != 'stopped':
        if cycle != 'monthly':
            state = 'active'
        elif occ:
            gap = month_index(month) - month_index(month_of(last))
            state = 'active' if gap <= 0 else 'late' if gap < SUSPECT_MONTHS else 'stop_suspected'
        else:
            state = 'stop_suspected'
    conn.execute('UPDATE fixed_cost SET class=?, current_amount=?, state=?, last_seen=? WHERE id=?',
                 (cls, cur, state, last, r['id']))
    return {'id': r['id'], 'name': r['name'], 'class': cls, 'current_amount': cur, 'state': state, 'last_seen': last,
            'keys': sorted(keys)}


def _guess_class(name):
    k = LP.merchant_key(name) + (name or '')
    if LP._has(k, LP.RENT):
        return 'housing'
    if LP._has(k, LP.LOAN):
        return 'loan'
    if LP._has(k, LP.INSURANCE):
        return 'insurance'
    if LP._has(k, LP.TELECOM):
        return 'telecom'
    if LP._has(k, LP.UTILITY):
        return 'utility'
    if LP._has(k, LP.SUBSCRIPTION) or LP._has(k, LP.MEMBERSHIP):
        return 'subscription'
    return 'other'


def _propose_keep(conn, month):
    rules = _data('ledger_keep_rules.json')
    out_total = conn.execute("SELECT COALESCE(SUM(amount),0) FROM spend_entry WHERE kind='expense' AND "
                             "COALESCE(class,'spend') IN ('spend','saving','loan') AND substr(date,1,7)=?", (month,)).fetchone()[0] or 1
    svc = LP.services()
    for f in rows(conn, "SELECT * FROM fixed_cost WHERE direction='expense' AND state<>'stopped'"):
        if f['keep_by'] == 'owner':
            continue
        cls = f['class'] or 'other'
        keys = [k[0] for k in conn.execute('SELECT key FROM fixed_cost_key WHERE fixed_id=?', (f['id'],))]
        if cls in rules['must']:
            keep = 'must'
        elif cls == 'subscription':
            has_ladder = any(k in svc for k in keys)
            amt = f['current_amount'] or f['amount'] or 0
            keep = 'reducible' if has_ladder else ('droppable' if amt < rules['subscription']['droppable_share'] * out_total
                                                   else 'reducible')
        else:
            keep = rules['default']
        conn.execute("UPDATE fixed_cost SET keep_class=?, keep_by='engine' WHERE id=?", (keep, f['id']))


# ---- stream verbs (the owner's words, stored) -------------------------------------------------

def _stream(conn, sid):
    r = rows(conn, 'SELECT * FROM fixed_cost WHERE id=?', (sid,))
    if not r:
        raise ValueError('stream %s not found' % sid)
    return r[0]


def stream_list(conn, show_all=False):
    out = []
    for f in rows(conn, "SELECT * FROM fixed_cost ORDER BY direction DESC, COALESCE(current_amount, amount) DESC"):
        if f['state'] == 'stopped' and not show_all:
            continue
        if not (f['current_amount'] or f['amount']):
            continue
        f['keys'] = [k[0] for k in conn.execute('SELECT key FROM fixed_cost_key WHERE fixed_id=?', (f['id'],))]
        from ledger_obligation import term
        f.update(term(conn,f['id']))
        out.append({k: f[k] for k in ('id', 'name', 'class', 'direction', 'amount', 'current_amount', 'state', 'last_seen',
                                      'keep_class', 'keep_by', 'changed_from', 'changed_at', 'stopped_at', 'source', 'keys', 'ends_on', 'end_kind', 'end_asked', 'pay_mode', 'pay_mode_source')})
    return {'streams': out}


def stream_stop(conn, sid, said=None):
    f = _stream(conn, sid)
    today = datetime.date.today().isoformat()
    conn.execute("UPDATE fixed_cost SET state='stopped', stopped_by='owner', stopped_at=? WHERE id=?", (today, sid))
    if said:
        conn.execute("INSERT INTO ledger_preference(ulid, item, polarity, note, status, source, recorded_at, topic) "
                     "VALUES(?,?,?,?,?,?,?,?)", ('stp-%d-%s' % (sid, today), 'stream:%d' % sid, 'negotiable', said, 'active',
                                                 'owner', today + 'T00:00:00Z', 'stream_stop_words'))
    return {'id': sid, 'name': f['name'], 'state': 'stopped', 'stopped_at': today}


def stream_keep(conn, sid, keep):
    f = _stream(conn, sid)
    conn.execute("UPDATE fixed_cost SET keep_class=?, keep_by='owner' WHERE id=?", (keep, sid))
    return {'id': sid, 'name': f['name'], 'keep_class': keep, 'keep_by': 'owner'}


def stream_set(conn, sid, field, value):
    f = _stream(conn, sid)
    if field == 'pay_mode':
        from fixed_pay_mode import owner_set
        owner_set(conn, sid, value)
    elif field == 'amount':
        n = int(re.sub(r'[^\d]', '', value))
        conn.execute('UPDATE fixed_cost SET amount=?, current_amount=? WHERE id=?', (n, n, sid))
    elif field == 'name':
        keys = [k[0] for k in conn.execute('SELECT key FROM fixed_cost_key WHERE fixed_id=?', (sid,))]
        conn.execute('UPDATE fixed_cost SET name=? WHERE id=?', (value, sid))
        for a in {LP.merchant_key(f['name'])} - set(keys):
            if a:
                conn.execute('INSERT OR IGNORE INTO fixed_cost_key(fixed_id, key) VALUES(?,?)', (sid, a))
        if f['direction']=='expense':
            from ledger_names import set_names
            set_names(conn, dict(items=[dict(name=value, display_name=value,
                                            source='owner_said', said=value)]))
    elif field == 'class':
        if value not in FIXED_CLASSES:
            raise ValueError('class must be one of ' + ', '.join(FIXED_CLASSES))
        conn.execute('UPDATE fixed_cost SET class=? WHERE id=?', (value, sid))
        if f['keep_by'] != 'owner':
            conn.execute('UPDATE fixed_cost SET keep_class=NULL WHERE id=?', (sid,))
            _propose_keep(conn, basis_month(conn) or '')
    elif field == 'state':
        if value not in ('active', 'late', 'changed', 'stop_suspected'):
            raise ValueError('state: a stream is stopped only by stream-stop')
        conn.execute('UPDATE fixed_cost SET state=? WHERE id=?', (value, sid))
    return {'id': sid, field: value}


def stream_merge(conn, sid, into):
    a, b = _stream(conn, sid), _stream(conn, into)
    if sid == into:
        raise ValueError('a stream cannot merge into itself')
    if a.get('pay_mode_source') == 'owner' and b.get('pay_mode_source') != 'owner':
        from fixed_pay_mode import owner_set
        owner_set(conn, into, a['pay_mode'])
    conn.execute('UPDATE fixed_cost_key SET fixed_id=? WHERE fixed_id=?', (into, sid))
    conn.execute('UPDATE spend_entry SET stream_id=? WHERE stream_id=?', (into, sid))
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='contract_record'").fetchone():
        conn.execute('UPDATE contract_record SET fixed_id=? WHERE fixed_id=?', (into, sid))
    conn.execute('DELETE FROM fixed_cost WHERE id=?', (sid,))
    k = LP.merchant_key(a['name'])
    if k:
        conn.execute('INSERT OR IGNORE INTO fixed_cost_key(fixed_id, key) VALUES(?,?)', (into, k))
    return {'merged': sid, 'into': into, 'name': b['name']}


# ---- diagnosis ----------------------------------------------------------------------------------

def _month_rows(conn, month):
    from ledger_review import reimbursement_sql
    payments = rows(conn, f"SELECT s.id, s.date, s.amount-COALESCE(r.amount,0)-{reimbursement_sql('s')} AS amount, s.name, s.method, s.kind, CASE WHEN EXISTS(SELECT 1 FROM ledger_preference p WHERE p.topic='ledger_review_item' AND p.status='active' AND CAST(p.item AS INTEGER)=s.id AND json_extract(p.detail,'$.insurance_receipt')=1) THEN 'insurance_reimbursement' WHEN EXISTS(SELECT 1 FROM ledger_preference p WHERE p.topic='ledger_review_item' AND p.status='active' AND CAST(p.item AS INTEGER)=s.id AND (json_extract(p.detail,'$.deposit_answer')='received' OR json_extract(p.detail,'$.history_received')=1)) THEN 'reimbursement' ELSE COALESCE(s.class,CASE WHEN s.kind='income' THEN 'income' ELSE 'spend' END) END AS class, "
                      "s.src_cat, s.stream_id, s.at_time, s.pair_id, s.is_fixed, s.note FROM spend_entry s LEFT JOIN v_ledger_reimbursement r ON r.spend_id=s.id WHERE substr(s.date,1,7)=? "
                      "AND s.scope <> 'business' ORDER BY s.date, s.id", (month,))

    # A single incoming leg on a registered savings instrument is a saved
    # contribution. Keep its stored transfer direction and project it once.
    for row in payments:
        if row['kind'] == 'transfer' and row['class'] == 'saving' and row['amount'] > 0:
            row['kind'] = 'expense'
        elif row['kind'] == 'transfer' and row['class'] == 'saving_back':
            row['amount'] = abs(row['amount'])

    # A legacy guessed settlement is still cash spending. Other pass-through
    # classes (employer money, explicit receipt allocations) remain untouched.
    settlements={int(item[5:]) for item, in conn.execute(
        "SELECT item FROM ledger_preference WHERE topic='ledger_plan_fact' AND status='active' "
        "AND item LIKE 'item:%' AND json_extract(detail,'$.answer')='settlement'")}
    for row in payments:
        if row['class']=='pass_through' and row['id'] in settlements:
            row['class']='spend'
    from ledger_actual import apply_categories
    from ledger_cash import apply
    return apply(conn, apply_categories(conn, payments))


def method_count(conn, month=None):
    """canonical payment methods by layer (dust and aliases are never shown); with a month: the ones that month used."""
    out = collections.Counter()
    used = None
    if month:
        used = set()
        for (m,) in conn.execute("SELECT DISTINCT method FROM spend_entry WHERE substr(date,1,7)=? AND method IS NOT NULL", (month,)):
            used.add(m)
    # savings / securities products and vaults are where money is KEPT, not ways to pay: not counted as a method
    for name, layer, aliases in conn.execute("SELECT name, COALESCE(layer,'account'), aliases FROM pay_method "
                                             "WHERE canonical_id IS NULL AND dust=0 AND kind NOT IN ('savings','securities')"):
        if used is not None and name not in used:
            continue
        out[layer] += 1
    return dict(out)


def _group_of(r):
    from ledger_actual import category
    merchant = r.get('category_override') or category(r['name'])
    if merchant:
        return merchant[1]
    m = _data('ledger_category_map.json')
    sc = r['src_cat'] or ''
    if is_transit(r['name']):
        return m['major']['교통'][1]
    if sc in m['sub_group']:
        return m['sub_group'][sc]
    return m['group'].get(sc.split('/')[0], m['default_group'])


def stream_evidence(conn, stream, month):
    """Engine monthly streams need repeated months; explicit owner facts survive."""
    if stream['source'] != 'ledger_v6' or stream['keep_by'] == 'owner' or stream['cycle'] != 'monthly':
        return True
    months = [r[0] for r in conn.execute("SELECT DISTINCT substr(date,1,7) FROM spend_entry WHERE stream_id=? AND date<? ORDER BY date", (stream['id'],prev_month(month,-1)+'-01'))]
    # A pair of old investment moves is not a standing monthly obligation.
    # Imported short windows can establish two consecutive months; longer
    # histories require three months and the detector's coverage floor.
    if len(months)<2:
        return False
    span=month_index(months[-1])-month_index(months[0])+1
    return (len(months)>=3 or span==2) and len(months)/span>=MIN_COVERAGE



def spend_profile(conn, month):
    """Three calendar months, split by registered stream link, with zero months retained.

    A repeated subscription charge is an identity question, not a confirmed
    obligation. Keep every charge visible and outside the confirmed total.
    """
    from ledger_review import CYCLE_MONTHS
    months = [prev_month(month, n) for n in (2, 1, 0)]
    personal = {r[0] for r in conn.execute("SELECT id FROM spend_entry WHERE scope='personal'")}
    ledger = {ym: [r for r in _month_rows(conn, ym) if r['id'] in personal] for ym in months}
    categories = {r['id']: r for r in rows(conn, 'SELECT * FROM spend_category')}
    raw = {r['id']: r for r in rows(conn, 'SELECT id,category_id,is_fixed,scope FROM spend_entry '
                                   'WHERE date>=? AND date<?',
                                   (months[0] + '-01', prev_month(month, -1) + '-01'))}
    fixed = []
    streams = rows(conn, "SELECT * FROM fixed_cost WHERE direction='expense' AND state<>'stopped' "
                         "AND COALESCE(class,'other')<>'saving' ORDER BY id")
    linked = {f['id'] for f in streams}
    for f in streams:
        members = [[r for r in ledger[ym] if r['stream_id'] == f['id'] and
                    r['kind'] == 'expense' and r['class'] in ('spend', 'loan')]
                   for ym in months]
        values = [sum(r['amount'] for r in rs) for rs in members]
        cycle = f['cycle'] or 'monthly'
        amount = f['current_amount'] if f['current_amount'] is not None else f['amount']
        variable = f['class'] == 'subscription' and len(set(values)) > 1 and sum(v != 0 for v in values) >= 2
        # Same-minute pieces are one payment observation. Several separate
        # observations may need contract review, even on the same date.
        ambiguous = f['class'] == 'subscription' and any(len({
            (r['date'], r.get('at_time') or ('id:%s' % r['id']))
            for r in rs if r['amount'] > 0}) > 1 for rs in members)
        monthly = round(sum(values) / 3) if variable else round(amount / CYCLE_MONTHS.get(cycle, 1))
        unresolved = ambiguous or not stream_evidence(conn, f, month) or f['state'] in ('late', 'stop_suspected') or cycle not in CYCLE_MONTHS
        from ledger_obligation import term, ended
        end=term(conn,f['id'])
        if ended(end,month): monthly=0
        fixed.append(dict(**end, id=f['id'], name=_member_name(f['name'], conn) or f['name'], amount=amount,
                          cycle=cycle, monthly=monthly, values=values, variable=variable,
                          unresolved=unresolved, ambiguous=ambiguous,
                          charges=[dict(id=r['id'], date=r['date'], name=r['name'], amount=r['amount'])
                                   for rs in members for r in rs]))
    buckets = collections.defaultdict(lambda: dict(values=[0, 0, 0], fixed_like=False))
    for n, ym in enumerate(months):
        for r in ledger[ym]:
            if r['kind'] != 'expense' or r['class'] != 'spend' or r['stream_id'] in linked:
                continue
            cat = categories.get(raw[r['id']]['category_id'], {})
            key = cat.get('name') or _group_of(r)
            name = cat.get('display_name') or cat.get('name') or key
            g = buckets[name]
            g['values'][n] += r['amount']
            # Flag mixed categories without treating the entire category as fixed.
            g['fixed_like'] |= bool(raw[r['id']]['is_fixed']) or key in ('housing', 'subscription', 'tax', 'insurance', 'telecom', 'utility')
    total = sum(sum(g['values']) for g in buckets.values())
    variable_rows = []
    complete = all(ledger[ym] for ym in months)
    for name, g in buckets.items():
        average = sum(g['values']) / 3
        if average <= 0:
            continue
        cv = statistics.pstdev(g['values']) / average
        variable_rows.append(dict(name=name, values=g['values'], monthly=round(average),
                                  share=round(sum(g['values']) / max(total, 1) * 100, 1),
                                  bar_units=min(10, max(0, round(sum(g['values']) / max(total, 1) * 10))),
                                  cv=round(cv, 4), group='similar' if complete and cv <= 0.2 else 'varying',
                                  budget_start=int(average), fixed_like=g['fixed_like']))
    from ledger_installment import listing
    return dict(installments=listing(conn,month)['items'], months=months, complete=complete, fixed=fixed,
                confirmed_monthly=sum(r['monthly'] for r in fixed if not r['unresolved']),
                historical_fixed=sum(r['values'][-1] for r in fixed),
                categories=sorted(variable_rows, key=lambda r: -r['monthly']))


def diagnosis(conn, month=None, include_history=True, totals_only=False):
    month = month or basis_month(conn)
    if month is None:
        raise ValueError('the ledger has no rows yet')
    mrows = _month_rows(conn, month)
    streams = rows(conn, "SELECT * FROM fixed_cost WHERE state<>'stopped'")
    by_stream = collections.defaultdict(list)
    for r in mrows:
        if r['stream_id']:
            by_stream[r['stream_id']].append(r)
    # income
    inc_streams, exp_streams, dormant = [], [], []
    late_added = 0
    for f in streams:
        if not stream_evidence(conn, f, month):
            continue
        amt = f['current_amount'] if f['current_amount'] is not None else f['amount']
        occ = [r for r in by_stream.get(f['id'], []) if
               (r['class']=='income' if f['direction']=='income' else r['class'] in OUT_CLASSES and r['kind']=='expense')]
        seen_in_month = bool(occ)
        if not seen_in_month and f['source']=='ledger_v6' and f['last_seen'] and month_of(f['last_seen'])>month:
            continue  # detected future bills are not evidence of a past obligation
        if seen_in_month:
            amt = sum(r['amount'] for r in occ)
        else:
            # Historical actuals never include an expected or late payment.
            dormant.append({'id': f['id'], 'name': f['name'], 'amount': amt, 'last_seen': f['last_seen']})
            continue
        from ledger_obligation import term
        line = {**term(conn,f['id']), 'id': f['id'], 'name': f['name'], 'display_name': _member_name(f['name'], conn), 'class': f['class'] or 'other', 'amount': amt, 'state': f['state'],
                'seen': seen_in_month, 'keep_class': f['keep_class'], 'keep_by': f['keep_by'],
                'changed_from': f['changed_from'] if f['state'] == 'changed' else None,
                'last_seen': f['last_seen']}
        (inc_streams if f['direction'] == 'income' else exp_streams).append(line)
    eligible = {f['id'] for f in streams if stream_evidence(conn, f, month)}
    in_stream = {r['id'] for sid, rs in by_stream.items() if sid in eligible for r in rs}
    income_stream_ids = {f['id'] for f in inc_streams}
    income_other = [r for r in mrows if r['class'] == 'income' and r['stream_id'] not in income_stream_ids]
    income_regular = sum(l['amount'] for l in inc_streams)
    income_other_total = sum(r['amount'] for r in income_other)
    # money out
    var_rows = [r for r in mrows if r['class'] in OUT_CLASSES and r['id'] not in in_stream and r['kind'] == 'expense']
    saved_var = [r for r in var_rows if r['class'] == 'saving']
    loan_var = [r for r in var_rows if r['class'] == 'loan']
    spend_var = [r for r in var_rows if r['class'] == 'spend']
    groups = collections.defaultdict(lambda: {'amount': 0, 'count': 0})
    delivery = {'count': 0, 'amount': 0, 'group': None}
    for r in spend_var:
        g = groups[_group_of(r)]
        g['amount'] += r['amount']
        g['count'] += 1 if r['amount'] > 0 else -1
    from ledger_actual import order_metrics
    delivery_rows = [r for r in spend_var if LP._has(r['name'].casefold(), ('쿠팡이츠', '요기요', '배달의민족', '배민', 'coupang eats'))]
    delivery.update(order_metrics(delivery_rows))
    if delivery_rows:
        delivery['group'] = _group_of(delivery_rows[0])
    regular_total = sum(l['amount'] for l in exp_streams)
    variable_total = sum(r['amount'] for r in var_rows)
    saved_regular = sum(l['amount'] for l in exp_streams if l['class'] == 'saving')
    saved_total = saved_regular + sum(r['amount'] for r in saved_var)
    money_out = regular_total + variable_total
    income_total = income_regular + income_other_total
    pulled = sum(r['amount'] for r in mrows if r['class'] == 'saving_back')
    passthrough = sum(abs(r['amount']) for r in mrows if r['class'] == 'pass_through' and r['amount'] > 0)
    people = {} if totals_only else _people(conn, month)
    diag = {
        'month': month,
        'income': {'total': income_total, 'regular': income_regular, 'other': income_other_total,
                   'regular_lines': inc_streams, 'other_lines': _top(income_other, 5, conn),
                   'other_members': _top(income_other, len(income_other), conn)},
        'pass_through': passthrough,
        'out': {'total': money_out, 'regular': regular_total, 'variable': variable_total, 'saved': saved_total,
                'late_added': late_added,
                'regular_lines': sorted(exp_streams, key=lambda l: (CLASS_ORDER.index(l['class']) if l['class'] in CLASS_ORDER else 99,
                                                                   -l['amount'])),
                'variable_groups': {k: v for k, v in sorted(groups.items(), key=lambda kv: -kv[1]['amount'])},
                'loan_other': sum(r['amount'] for r in loan_var), 'saved_other': sum(r['amount'] for r in saved_var),
                'saved_lines': _top(saved_var, len(saved_var), conn),
                'delivery': delivery},
        'pulled_back': pulled,
        'balance': income_total - money_out,
        'people': people,
        'dormant': dormant,
        'owner_unconfirmed': sum(1 for l in exp_streams if l['keep_by'] != 'owner'),
    }
    # One actual partition feeds both the diagnosis and the review. A stream
    # is an observation/contract link, not authority to turn spend into saving
    # or to put the same payment in another category on a different screen.
    import ledger_review
    review = ledger_review.build(conn, month, totals_only=totals_only)
    actual_lines = {}
    fixed_by_id = {f['id']: f for f in rows(conn,'SELECT * FROM fixed_cost')}
    keep_rules = _data('ledger_keep_rules.json')
    regular_saving = [r for r in review['saving'] if r['stream_id'] in fixed_by_id
                      and stream_evidence(conn,fixed_by_id[r['stream_id']],month)]
    regular_saving_ids = {r['id'] for r in regular_saving}
    other_saving = [r for r in review['saving'] if r['id'] not in regular_saving_ids]
    for cls, members in [*review['fixed'].items(), ('saving', regular_saving)]:
        for r in members:
            f = fixed_by_id.get(r['stream_id'], {})
            key = (r['stream_id'] or -r['id'], cls)
            if key not in actual_lines:
                name = f.get('name') or r['name']
                from ledger_obligation import term
                actual_lines[key] = dict(**term(conn,f.get('id')), id=key[0], name=name, display_name=_member_name(name, conn),
                                         **{'class':cls}, amount=0, state=f.get('state','active'),
                                         stream_id=f.get('id'), payment_ids=[],
                                         seen=True, keep_class=f.get('keep_class') or ('must' if cls in keep_rules['must'] else 'reducible'),
                                         keep_by=f.get('keep_by') or 'engine',
                                         changed_from=f.get('changed_from'), last_seen=f.get('last_seen',r['date']))
            actual_lines[key]['amount'] += r['amount']
            actual_lines[key]['payment_ids'].append(r['id'])
    actual = review['totals']
    regular_total = actual['fixed'] + sum(r['amount'] for r in regular_saving)
    other_saving_total = sum(r['amount'] for r in other_saving)
    variable_total = actual['living'] + other_saving_total
    money_out = regular_total + variable_total
    diag['out'].update(total=money_out, regular=regular_total, variable=variable_total,
                       saved=actual['saving'], regular_lines=list(actual_lines.values()), late_added=0,
                       saved_lines=_top(other_saving,len(other_saving),conn), saved_other=other_saving_total, loan_other=0,
                       variable_groups={g['category'] or '생활·기타':dict(amount=g['amount'], count=len(g['items']))
                                        for g in review['living']})
    delivery_rows = [r for g in review['living'] for r in g['items']
                     if LP._has(r['name'].casefold(), ('쿠팡이츠','요기요','배달의민족','배민','coupang eats'))]
    diag['out']['delivery'].update(order_metrics(delivery_rows))
    diag['balance'] = diag['income']['total'] - money_out
    diag['owner_unconfirmed'] = sum(l['keep_by'] != 'owner' for l in actual_lines.values())
    if totals_only:
        return diag
    diag['gap_line'] = (diag['balance'] < 0 and -diag['balance'] > 0.10 * max(money_out, 1))
    diag['levers'] = levers(conn, diag, month)
    diag['methods'] = method_count(conn, month)
    diag['biz_drift'] = biz_drift(conn, month)
    diag['suspects'] = [l for l in exp_streams + inc_streams if l['state'] in ('late', 'stop_suspected') and not l['seen']]
    diag['review'] = review
    diag['spend_profile'] = spend_profile(conn, month)
    if include_history:
        present = {r[0] for r in conn.execute("SELECT DISTINCT substr(date,1,7) FROM spend_entry WHERE date>=? AND date<?", (prev_month(month,2)+'-01', prev_month(month,-1)+'-01'))}
        history = []
        for ym in sorted(present):
            conn.execute('SAVEPOINT history_month')
            try:
                stream_reconcile(conn, ym)
                actual = ledger_review.build(conn,ym)['totals']
                # History answers "what happened", so no late or suspected
                # obligation can project a payment into a month without a row.
                history.append(dict(month=ym,income=actual['income'],regular=actual['fixed'],
                                    variable=actual['living'],saved=actual['saving']-sum(
                                        r['amount'] for r in _month_rows(conn, ym) if r['class']=='saving_back'),
                                    own_inflow=sum(r['amount'] for r in _month_rows(conn, ym)
                                                   if r['class']=='own_transfer'),
                                    out=actual['fixed']+actual['living']))
            finally:
                conn.execute('ROLLBACK TO history_month')
                conn.execute('RELEASE history_month')
        diag['history'] = history
    diag['question'] = s2_candidate(conn, diag, month)
    return diag


def _top(rs, n, conn=None):
    out = collections.OrderedDict()
    for r in sorted((r for r in rs if not LP.is_dust(r['amount'])), key=lambda r: -abs(r['amount'])):
        out[r['id']] = {'id': r['id'], 'name': r['name'], 'display_name': _member_name(r['name'], conn), 'class': r['class'], 'amount': r['amount'], 'date': r['date']}
    return list(out.values())[:n]


def _people(conn, month):
    out = collections.defaultdict(lambda: {'amount': 0, 'count': 0, 'relation': None})
    for r in rows(conn, "SELECT v.entity_ref, v.amount, p.name, p.relation FROM v_spend_link v "
                        "JOIN persons p ON p.id = CAST(v.entity_ref AS INTEGER) WHERE v.entity_kind IN ('person','gift') "
                        "AND substr(v.date,1,7)=?", (month,)):
        o = out[r['name']]
        o['amount'] += r['amount']
        o['count'] += 1
        o['relation'] = r['relation']
    return {k: v for k, v in sorted(out.items(), key=lambda kv: -kv[1]['amount'])}


# ---- levers (NS3, D4) ------------------------------------------------------------------------------------

def levers(conn, diag, month):
    """The lever rules (ledger_levers.json): every lever is {rule, id, monthly, params}; the owner-facing words are the
    service's (ledgerlib/diagtext.py). None without a number."""
    cat = _data('ledger_levers.json')
    out = []
    # A currency amount or payment date does not establish the plan, usage,
    # work-tool value or ability to downgrade. Contract review owns that.
    # 2. delivery
    dl = cat['delivery_cut']
    d = diag['out']['delivery']
    if d['count'] >= dl['min_count']:
        allowed = int(dl['per_week'] * 30 / 7.0)
        if d['count'] > allowed:
            save = int(round(d['counted_amount'] * (1 - allowed / float(d['count'])), -2))
            if save > 0:
                out.append({'rule': 'delivery_cut', 'id': 'delivery_cut', 'monthly': save,
                            'params': {'per_week': dl['per_week'], 'count': d['count'], 'amount': d['amount']}})
    # A keep/cut preference is not proof of cancellation terms or timing.
    # Subscription savings wait for the explicit contract/target review.
    # Saving less is not a consumption cut. A remaining deficit belongs to
    # the final plan round, where the owner can choose extra income.
    return [l for l in out if l['monthly'] > 0]


# ---- business account drift (R-biz, spec 2.3) -------------------------------------------------------------

BIZ_SHARE = 0.15


def _biz_names(conn):
    out = set()
    for name, aliases, scope in conn.execute("SELECT name, aliases, scope_default FROM pay_method WHERE canonical_id IS NULL"):
        if scope == 'personal' and LP._has(name.casefold(), LP.BUSINESS_WORDS):
            out.add(name)
    return out


def biz_drift(conn, month):
    """A business-looking account counted as personal (the default): when its spend passes 15% of the personal money
    out two months in a row, ONE line proposes the split (method-scope NAME business). Never an onboarding step."""
    names = _biz_names(conn)
    if not names:
        return None
    shares = []
    for m in (prev_month(month), month):
        tot = conn.execute("SELECT COALESCE(SUM(amount),0) FROM spend_entry WHERE kind='expense' AND "
                           "COALESCE(class,'spend') IN ('spend','saving','loan') AND substr(date,1,7)=? AND scope='personal'",
                           (m,)).fetchone()[0] or 0
        biz = conn.execute("SELECT COALESCE(SUM(amount),0) FROM spend_entry WHERE kind='expense' AND "
                           "COALESCE(class,'spend') IN ('spend') AND substr(date,1,7)=? AND scope='personal' AND method IN (%s)"
                           % ','.join('?' * len(names)), (m,) + tuple(sorted(names))).fetchone()[0] or 0
        shares.append(biz / float(tot) if tot else 0)
    if all(s > BIZ_SHARE for s in shares):
        return {'methods': sorted(names), 'shares': [round(s, 3) for s in shares]}
    return None


# ---- the one plumbing question (S2) ------------------------------------------------------------------------

def s2_candidate(conn, diag, month):
    floor = 0.03 * max(diag['out']['total'], 1)
    regs = []
    for r in conn.execute("SELECT name, aliases FROM pay_method WHERE kind IN ('credit_card','corporate_card') AND canonical_id IS NULL"):
        regs.append(r[0])
    fams = {LP_family(n) for n in regs} - {''}
    best = None
    for r in rows(conn, "SELECT id, date, amount, name, method FROM spend_entry WHERE class='card_bill' AND substr(date,1,7)=? "
                        "AND pair_id IS NULL", (month,)):
        amt = abs(r['amount'])
        if amt < floor or LP.is_dust(amt):
            continue
        if LP_family(r['name']) in fams and LP_family(r['name']):
            continue
        if best is None or amt > best['amount']:
            best = {'kind': 'unknown_card', 'spend_id': r['id'], 'date': r['date'], 'amount': amt, 'name': r['name'],
                    'method': r['method']}
    return best


def LP_family(name):
    import ledger_v6
    return ledger_v6.family(name)


def apply_link_rules(conn):
    """Standing rules (past and future rows) and the silent remit link: a money transfer to a name that resolves to ONE
    persons row is linked 'for' that person (spec 2.6); a name that is not a person (or not one person) is left alone."""
    import ledger_v6
    n = 0
    for r in conn.execute('SELECT id FROM spend_link_rule WHERE active=1').fetchall():
        n += ledger_v6.apply_rule(conn, r[0])
    rails = re.compile('^(\ud1a0\uc2a4|\ud1a0\ubc45|\uce74\uce74\uc624|\ub124\uc774\ubc84)\\s+')
    for r in rows(conn, "SELECT s.id, s.name FROM spend_entry s WHERE s.kind='expense' AND s.src_cat LIKE '\uc774\uccb4/%' "
                        "AND NOT EXISTS (SELECT 1 FROM spend_link l WHERE l.spend_id=s.id AND l.entity_kind='person')"):
        who = rails.sub('', (r['name'] or '').strip())
        if not who or '*' in who:
            continue
        try:
            ledger_v6.spend_link(conn, r['id'], 'person', who, 'for', 'rule', _remit_rule(conn))
            n += 1
        except ValueError:
            continue
    return n


def _remit_rule(conn):
    """The one standing rule behind silent remit links (so a rule-made link always has its rule)."""
    got = conn.execute("SELECT id FROM spend_link_rule WHERE entity_kind='person' AND entity_ref='0' AND said='remit'").fetchone()
    if got:
        return got[0]
    return conn.execute("INSERT INTO spend_link_rule(entity_kind, entity_ref, match_json, said, active) "
                        "VALUES('person','0','{\"remit\": true}','remit',0)").lastrowid


# ---- state for the flow -------------------------------------------------------------------------------------------

def ledger_state(conn, month=None):
    n = conn.execute("SELECT COUNT(*) FROM spend_entry").fetchone()[0]
    first, last = conn.execute("SELECT MIN(date), MAX(date) FROM spend_entry").fetchone()
    pend = conn.execute("SELECT COUNT(*) FROM intake_item WHERE state='pending'").fetchone()[0]
    return {'rows': n, 'first': first, 'last': last, 'basis_month': basis_month(conn),
            'streams': conn.execute("SELECT COUNT(*) FROM fixed_cost WHERE state<>'stopped'").fetchone()[0],
            'unexecuted': 0, 'pending_intake': pend, 'methods': method_count(conn)}
