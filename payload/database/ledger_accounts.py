"""Account evidence and owner-confirmed merges. All writes use the kit transaction.

Product terms live in ledger_bank_markers.json. Card issuers, transfer destinations
and interest dates are suggestions, never proof of the paying account's bank.
"""
import collections
import datetime
import json
from pathlib import Path
import re

import ledger_profile as LP


def _table():
    return json.loads(Path(__file__).with_name('ledger_bank_markers.json').read_text())


def _names(methods, mid):
    from ledger_v6 import _json_list
    return {name for item in methods.by_id.values() if methods.canon(item['id']) == mid
            for name in [item['name'], *_json_list(item['aliases'])]
            if name in methods.names and methods.canon(methods.names[name]) == mid}


def evidence(K, conn, ref):
    from ledger_v6 import Methods, _existing_method, rows
    from ledger_instrument_facts import records
    table = _table()
    method = _existing_method(conn, ref)
    methods = Methods(conn)
    names = _names(methods, method['id'])
    own = rows(conn, 'SELECT id,date,name,note,amount,kind,class,is_fixed,stream_id,src_cat FROM spend_entry '
                    'WHERE method IN (%s) ORDER BY date DESC,id DESC' % ','.join('?' for _ in names), tuple(names))
    proof, hints = [], []
    for rule in table['markers']:
        matched = [r for r in own if (r['kind'] in rule['kinds'] or r['class'] in rule['classes'])
                   and re.search(rule['pattern'], (r['name'] or '') + ' ' + (r['note'] or ''))]
        if matched:
            proof.append(dict(bank=rule['bank'], clue=rule['marker'], row_ids=[r['id'] for r in matched],
                              source=rule['source'], kind='product'))
    # Only actual edges count; issuer-name similarity never invents a link.
    linked = rows(conn, 'SELECT DISTINCT m.* FROM pay_method m LEFT JOIN pay_funding f ON f.wallet_id=m.id '
                       "WHERE m.canonical_id IS NULL AND m.kind IN ('debit_card','credit_card','corporate_card') "
                       'AND (m.linked_account_id=? OR f.source_id=?)', (method['id'], method['id']))
    for card in linked:
        bank = table['banks'].get(LP._bank(card['name']))
        if bank:
            hints.append(dict(bank=bank, clue=card['name'], kind='linked_card'))
    for r in own:
        if r['class'] != 'own_transfer':
            continue
        bank = table['banks'].get(LP._bank(r['name']))
        if bank:
            hints.append(dict(bank=bank, clue=r['name'], row_ids=[r['id']], kind='counterpart'))
    interest = [r for r in own if r['kind'] == 'income' and re.search('이자|interest', r['name'], re.I)]
    for rule in table['interest_cadences']:
        dates = [datetime.date.fromisoformat(r['date']) for r in interest]
        matched = [d for d in dates if d.weekday() == rule['weekday'] and rule['day_min'] <= d.day <= rule['day_max']]
        if len({d.strftime('%Y-%m') for d in matched}) >= rule['min_months']:
            hints.append(dict(bank=rule['bank'], clue='interest_cadence', kind='interest', source=rule['source']))
    salary = [r for r in own if r['kind'] == 'income' and re.search('급여|월급|salary',
              ' '.join(r.get(k) or '' for k in ('name', 'note', 'src_cat')), re.I)]
    # An explicit salary-bank fact is relevant only to rows that actually carry salary.
    if salary:
        for fact in rows(conn, "SELECT id,item,detail FROM ledger_preference WHERE topic='salary_bank' AND status='active' AND source IN ('owner','owner-stated','chat')"):
            bank = table['banks'].get(LP._bank(fact['item']))
            if bank:
                proof.append(dict(bank=bank, clue='salary', row_ids=[r['id'] for r in salary],
                                  kind='owner_fact', source='owner-stated', fact_id=fact['id']))
    banks = sorted({p['bank'] for p in proof})
    # Counterparty and card banks can differ from this account's bank. They do
    # not overrule a product bound to this account by its own terms.
    guessed = banks or list(dict.fromkeys(h['bank'] for h in hints))
    balance = None
    for name in [method['name'], *sorted(names - {method['name']})]:
        eid = K._spend_entity_id(conn, name)
        if eid is not None:
            readings = rows(conn, "SELECT value AS amount,observed_at AS as_of FROM measure WHERE entity_id=? "
                            "AND metric='balance_krw' AND basis='actual' ORDER BY observed_at DESC,seq,id DESC LIMIT 1", (eid,))
            if readings and (balance is None or readings[0]['as_of'] > balance['as_of']):
                balance = readings[0]
    return dict(id=method['id'], name=method['name'], bank=banks[0] if len(banks) == 1 else None,
                instrument_facts=records(conn).get(method['id']),
                proof=proof, hints=hints, guesses=guessed, conflict=len(banks) > 1,
                row_count=len(own), recent=[r for r in own if not LP.is_dust(r['amount'])][:3], balance=balance, salary=bool(salary),
                fixed_in=any((r['is_fixed'] or r['stream_id'] is not None) and r['kind'] == 'income' for r in own),
                fixed_out=any((r['is_fixed'] or r['stream_id'] is not None or r['class'] in ('card_bill','loan')) and r['kind'] != 'income' for r in own))


def funding_evidence(conn, ref):
    """Read-only funding proof; ties and conflicting evidence stay questions."""
    from ledger_v6 import Methods, _existing_method, rows
    from ledger_instrument_facts import records
    card = _existing_method(conn, ref)
    if card['kind'] == 'easy_pay':
        return _wallet_funding_evidence(conn, card)
    if card['kind'] not in ('credit_card', 'corporate_card', 'debit_card'):
        raise ValueError('funding evidence requires a card')
    methods = Methods(conn)
    accounts = {m['id']: dict(m) for m in methods.by_id.values()
                if m['kind'] == 'account' and m['canonical_id'] is None and not m['dust']}
    names = _names(methods, card['id'])
    proof = []
    # A product shared with exactly one account is stronger than a bank-only
    # match. Ordinary issuer labels cannot establish credit-card funding.
    if card['kind'] == 'debit_card':
        from ledger_v6 import _json_list
        core = LP.name_core(card['name']).replace(LP.CHECK_WORD, '')
        hits = []
        for mid, account in accounts.items():
            if not LP._brand_fit(card, account, {}):
                continue
            cores = [LP.name_core(name) for name in [account['name'], *_json_list(account['aliases'])]]
            if any(c and c == core for c in cores):
                hits.append(mid)
        if len(hits) == 1:
            proof.append(dict(source_id=hits[0], kind='product_name'))
        if not proof:
            bank = LP._bank(card['name'])
            hits = [mid for mid, a in accounts.items() if bank and LP._bank(a['name']) == bank]
            if len(hits) == 1:
                proof.append(dict(source_id=hits[0], kind='only_bank_account'))
    # Resolve only the latest observed settlement date. Conflicting accounts
    # on that date stay unresolved; older funding accounts are historical.
    family = LP._card_brand(card['name'])
    unique_family = family and sum(LP._card_brand(m['name']) == family
        for m in methods.by_id.values() if m['canonical_id'] is None and m['kind'] in ('credit_card','corporate_card','debit_card')) == 1
    bills = rows(conn, "SELECT id,date,name,method FROM spend_entry WHERE class='card_bill' ORDER BY date DESC,id DESC")
    matches = [r for r in bills if r['name'] in names or (unique_family and LP._card_brand(r['name']) == family)]
    if matches:
        latest = matches[0]['date']
        for row in matches:
            if row['date'] != latest:
                continue
            mid = methods.names.get(row['method'])
            mid = methods.canon(mid) if mid else None
            if mid in accounts:
                proof.append(dict(source_id=mid, kind='card_bill', row_id=row['id']))
    hits = {p['source_id'] for p in proof}
    source_id = next(iter(hits)) if len(hits) == 1 else None
    return dict(id=card['id'], source_id=source_id,
                instrument_facts=records(conn).get(card['id']),
                source=accounts[source_id]['name'] if source_id else None,
                proof=proof, conflict=len(hits) > 1)


def _wallet_funding_evidence(conn, wallet):
    """A mirrored purchase proves a source; top-ups and meal points do not."""
    from ledger_v6 import Methods, rows, minutes, PAIR_MINUTES
    methods = Methods(conn)
    eligible = {mid: m for mid, m in methods.by_id.items() if m['canonical_id'] is None
                and not m['dust'] and m['kind'] in ('account', 'credit_card', 'corporate_card', 'debit_card')}
    purchases = rows(conn, "SELECT id,date,at_time,name,amount,method,kind,class,pair_id FROM spend_entry "
                           "WHERE scope<>'business' AND amount>0 AND ("
                           "kind='expense' AND class='spend' OR class='own_transfer')")
    def mid(row):
        value = methods.names.get(row['method'])
        return methods.canon(value) if value else None
    by_payment = collections.defaultdict(list)
    for row in purchases:
        if mid(row) in eligible:
            by_payment[(row['date'], row['amount'])].append(row)
    wallets = collections.defaultdict(list)
    for row in purchases:
        method = methods.by_id.get(mid(row))
        if method and method['kind'] == 'easy_pay' and row['kind'] == 'expense' and row['class'] == 'spend':
            wallets[(row['date'], row['amount'])].append(row)
    def mirrors(row, other):
        paired = row['pair_id'] is not None and row['pair_id'] == other['pair_id']
        a, b = minutes(row['at_time']), minutes(other['at_time'])
        timed = (row['pair_id'] is None and other['pair_id'] is None and a is not None and b is not None
                 and abs(a-b) <= PAIR_MINUTES and row['name'] == other['name']
                 and other['kind'] == 'expense' and other['class'] == 'spend')
        return paired or timed
    proof = []
    for row in purchases:
        if mid(row) != wallet['id'] or row['kind'] != 'expense' or row['class'] != 'spend':
            continue
        key = (row['date'], row['amount'])
        matches = [other for other in by_payment[key] if mirrors(row, other)]
        # Both sides must be unique: one source row cannot prove two wallets.
        if len(matches) == 1 and sum(mirrors(w, matches[0]) for w in wallets[key]) == 1:
            proof.append(dict(source_id=mid(matches[0]), kind='wallet_mirror',
                              row_ids=[row['id'], matches[0]['id']]))
        elif matches:
            return dict(id=wallet['id'], source_id=None, source=None, proof=proof, conflict=True)
    hits = {p['source_id'] for p in proof}
    source = next(iter(hits)) if len(hits) == 1 else None
    return dict(id=wallet['id'], source_id=source, source=eligible[source]['name'] if source else None,
                proof=proof, conflict=len(hits) > 1)


def rename(K, conn, ref, name):
    from ledger_v6 import Methods, _existing_method, _json_list
    item = _existing_method(conn, ref)
    name = name.strip()
    if not name:
        raise ValueError('method name must be nonempty')
    others = Methods(conn)
    if name in others.names and others.canon(others.names[name]) != item['id']:
        raise ValueError('method name already exists; confirm method-merge instead')
    if item['name'] == name:
        return dict(id=item['id'], name=name)
    aliases = sorted(set(_json_list(item['aliases']) + [item['name']]) - {name})
    eid = K._spend_entity_id(conn, item['name'])
    conn.execute('UPDATE spend_entry SET method=? WHERE method=?', (name, item['name']))
    conn.execute('UPDATE pay_method SET name=?,aliases=? WHERE id=?', (name, json.dumps(aliases, ensure_ascii=False), item['id']))
    # Preserve the instrument entity so balance readings and money deltas stay attached.
    if eid is not None:
        conn.execute("UPDATE label SET display=? WHERE namespace='entity' AND token=(SELECT token FROM entity WHERE id=?) AND display=?",
                     (name, eid, item['name']))
    return dict(id=item['id'], name=name, aliases=aliases)


def merge(K, conn, ref, into):
    from ledger_v6 import Methods, _existing_method, _json_list, _method_layer, rows
    source, target = _existing_method(conn, ref), _existing_method(conn, into)
    sid, tid = source['id'], target['id']
    if sid == tid:
        return dict(merged=sid, into=tid, repointed=0)
    if _method_layer(source) != _method_layer(target) or source['kind'] != target['kind']:
        raise ValueError('merge requires the same payment method kind and layer')
    if source['scope_default'] != target['scope_default']:
        raise ValueError('resolve personal/business scope before merging')
    methods = Methods(conn)
    names = _names(methods, sid)
    aliases = sorted((set(_json_list(target['aliases'])) | names) - {target['name']})
    count = conn.execute('UPDATE spend_entry SET method=? WHERE method IN (%s)' % ','.join('?' for _ in names),
                         (target['name'], *names)).rowcount
    conn.execute('UPDATE fixed_cost SET method_id=? WHERE method_id=?', (tid, sid))
    conn.execute('UPDATE pay_method SET linked_account_id=? WHERE linked_account_id=?', (tid, sid))
    conn.execute('UPDATE pay_method SET canonical_id=? WHERE canonical_id=?', (tid, sid))
    # Rebuild only affected wallets, combining duplicate edges without losing
    # a main source or the observed date range. A surviving wallet's main wins.
    wallets = {tid, sid} | {r[0] for r in conn.execute('SELECT wallet_id FROM pay_funding WHERE source_id=?', (sid,))}
    marks = ','.join('?' for _ in wallets)
    edges = rows(conn, 'SELECT * FROM pay_funding WHERE wallet_id IN (%s) '
                       'ORDER BY (wallet_id=?) DESC,is_default DESC' % marks, (*wallets, tid))
    conn.execute('DELETE FROM pay_funding WHERE wallet_id IN (%s)' % marks, tuple(wallets))
    combined = {}
    for edge in edges:
        w = tid if edge['wallet_id'] == sid else edge['wallet_id']
        s = tid if edge['source_id'] == sid else edge['source_id']
        key = (w, s, edge['sub'])
        if w == s:
            continue
        if key in combined:
            current = combined[key]
            current['is_default'] = max(current['is_default'], edge['is_default'])
            for col, choose in (('first_seen', min), ('last_seen', max)):
                dates = [d for d in (current[col], edge[col]) if d]
                current[col] = choose(dates) if dates else None
        else:
            combined[key] = dict(edge, wallet_id=w, source_id=s)
    mains = set()
    for (w, s, sub), edge in combined.items():
        default = int(bool(edge['is_default']) and w not in mains)
        conn.execute('INSERT INTO pay_funding(wallet_id,source_id,sub,is_default,first_seen,last_seen,source_kind) VALUES(?,?,?,?,?,?,?)',
                     (w, s, sub, default, edge['first_seen'], edge['last_seen'], edge['source_kind']))
        if default:
            mains.add(w)
    for pref in rows(conn, "SELECT * FROM ledger_preference WHERE topic='pay_funding_source'"):
        parts = pref['item'].split(':')
        if len(parts) == 3 and str(sid) in parts[:2]:
            parts[:2] = [str(tid) if p == str(sid) else p for p in parts[:2]]
            item = ':'.join(parts)
            ulid = 'pay-funding-' + item
            existing = rows(conn, 'SELECT * FROM ledger_preference WHERE ulid=?', (ulid,))
            if existing:
                if pref['source'] == 'owner-stated' and pref['status'] == 'active':
                    conn.execute('UPDATE ledger_preference SET source=?,detail=?,status=? WHERE ulid=?',
                                 (pref['source'], pref['detail'], pref['status'], ulid))
                conn.execute('DELETE FROM ledger_preference WHERE id=?', (pref['id'],))
            else:
                conn.execute('UPDATE ledger_preference SET item=?,ulid=? WHERE id=?', (item, ulid, pref['id']))
    tags = list(dict.fromkeys(_json_list(target['purpose_tags']) + _json_list(source['purpose_tags'])))
    memo = '\n'.join(dict.fromkeys(m for m in (target['purpose_memo'], source['purpose_memo']) if m)) or None
    conn.execute('UPDATE pay_method SET aliases=?,purpose_tags=?,purpose_memo=?,purpose_origin=COALESCE(purpose_origin,?) WHERE id=?',
                 (json.dumps(aliases, ensure_ascii=False), json.dumps(tags, ensure_ascii=False) if tags else None,
                  memo, source['purpose_origin'], tid))
    se, te = K._spend_entity_id(conn, source['name']), K._spend_entity_id(conn, target['name'])
    if se is not None and se != te:
        if te is None:
            _, te = K._method_entity_accrue(conn, target['name'])
        if te is None:
            raise ValueError('target has no usable instrument entity')
        # A conflicting reading refuses the whole merge rather than dropping data.
        conn.execute('UPDATE measure SET entity_id=? WHERE entity_id=?', (te, se))
        conn.execute("UPDATE block_delta SET entity_id=? WHERE entity_id=? AND resource='money'", (te, se))
    conn.execute('DELETE FROM pay_method WHERE id=?', (sid,))
    return dict(merged=sid, into=tid, name=target['name'], aliases=aliases, repointed=count)
