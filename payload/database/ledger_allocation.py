"""Receipt replacement, including offsetting costs paid by the other person.

Raw bank/merchant evidence is preserved. Allocation amounts are economic
shares; their signed sum is the actual transfer. Friend-paid evidence is
excluded from owner cash expenses, then its owner's share is added back.
The kit transaction and a receipt revision serialize replacement and undo.
"""
import datetime
import hashlib
import itertools
import json
import sqlite3

import ledger_diag as D
import ledger_review as R


def row(c, sid):
    found = D.rows(c, 'SELECT * FROM spend_entry WHERE id=?', (sid,))
    if not found:
        raise ValueError('ledger row not found')
    return found[0]


def revision(c, sid):
    return R.annotations(c).get(sid, {}).get('allocation_revision', 0)


def fingerprint(c, sid, ids):
    """Bind confirmation to payment facts, other receipts and share balances."""
    facts = [row(c, n) for n in sorted({sid, *ids})]
    facts += [D.rows(c, 'SELECT * FROM ledger_receipt_allocation ORDER BY receipt_id,expense_id'),
              D.rows(c, 'SELECT * FROM ledger_share ORDER BY id'),
              D.rows(c, "SELECT item,detail,status FROM ledger_preference WHERE topic=? ORDER BY item", (R.TOPIC,)),
              D.rows(c, 'SELECT * FROM ledger_settlement_match ORDER BY transfer_id')]
    return hashlib.sha256(json.dumps(facts, sort_keys=True).encode()).hexdigest()


def snapshot(c, sid):
    receipt = row(c, sid)
    meta = R.annotations(c).get(sid, {})
    allocations = D.rows(c, 'SELECT a.*,s.name,s.date,s.amount AS payment,s.method,s.category_id '
                           'FROM ledger_receipt_allocation a JOIN spend_entry s ON s.id=a.expense_id '
                           'WHERE receipt_id=? ORDER BY s.date,(s.at_time IS NULL),s.at_time,s.id', (sid,))
    net = sum(a['amount'] * (1 if a['payer'] == 'owner' else -1) for a in allocations)
    labels = R.annotations(c)
    for a in allocations:
        a['summary_name'] = labels.get(a['expense_id'], {}).get('settlement_label', a['name'])
        e = row(c, a['expense_id'])
        a['refunded'] = c.execute("SELECT COALESCE(-SUM(amount),0) FROM spend_entry WHERE pair_id=? AND kind='expense' AND amount<0", (e['pair_id'],)).fetchone()[0] if e['pair_id'] is not None else 0
        a['owner_cost'] = c.execute('SELECT s.amount-COALESCE(r.amount,0)-' + R.reimbursement_sql('s') +
                                    ' FROM spend_entry s LEFT JOIN v_ledger_reimbursement r ON r.spend_id=s.id WHERE s.id=?', (e['id'],)).fetchone()[0]
        if labels.get(e['id'], {}).get('settlement_payer') != 'friend':
            a['owner_cost'] -= a['refunded']
    return dict(receipt=receipt, revision=meta.get('allocation_revision', 0), allocations=allocations,
                allocated=net, unallocated=receipt['amount']-net,
                status=meta.get('allocation_status', 'legacy'), operation=meta.get('allocation_operation'))


def _release(c, sid):
    """First release this receipt, retaining every other receipt's credit."""
    c.execute('DELETE FROM ledger_receipt_allocation WHERE receipt_id=?', (sid,))
    match = D.rows(c, 'SELECT * FROM ledger_settlement_match WHERE transfer_id=?', (sid,))
    if match:
        m = match[0]
        if m['credited']:
            c.execute('UPDATE ledger_share SET received=received-?,closed=? WHERE id=?',
                      (m['credited'], m['previous_closed'], m['share_id']))
        c.execute("UPDATE ledger_settlement_match SET credited=0,state='unrelated' WHERE transfer_id=?", (sid,))
    R.put(c, sid, dict(allocation_mode=True))


def _normalize(allocations):
    if not isinstance(allocations, list):
        raise ValueError('allocations must be a list')
    out = []
    for index, a in enumerate(allocations):
        if not isinstance(a, dict) or type(a.get('amount')) is not int or a['amount'] <= 0:
            raise ValueError('allocation requires expense id and positive integer won')
        if a.get('payer', 'owner') not in ('owner', 'friend'):
            raise ValueError('payer must be owner or friend')
        if a.get('share_id') is not None and type(a['share_id']) is not int:
            raise ValueError('invalid share id')
        expense = a.get('expense')
        sid = a.get('expense_id')
        if expense is not None:
            if not isinstance(expense, dict) or a.get('payer') != 'friend' or a.get('share_id') is not None:
                raise ValueError('new venue evidence must be an explicit friend payment')
            if type(expense.get('amount')) is not int or expense['amount']<=0 or not isinstance(expense.get('name'),str) or not expense['name'].strip():
                raise ValueError('new venue requires merchant and positive integer payment')
            datetime.date.fromisoformat(expense['date'])
            if expense.get('category_id') is not None and type(expense['category_id']) is not int:
                raise ValueError('invalid venue category')
            if expense.get('method') is not None and not isinstance(expense['method'],str):
                raise ValueError('invalid venue method')
            sid = -index-1
        elif type(sid) is not int or sid<=0:
            raise ValueError('positive expense id required')
        value = dict(expense_id=sid, amount=a['amount'], payer=a.get('payer', 'owner'), share_id=a.get('share_id'))
        if expense is not None:
            value['expense'] = {k:expense.get(k) for k in ('date','name','amount','category_id','method')}
        out.append(value)
    if len({a['expense_id'] for a in out}) != len(out):
        raise ValueError('one allocation per expense required')
    return out


def replace(c, sid, allocations, expected_revision, operation, *, partial=False, expected_fingerprint=None, preview=False):
    allocations = _normalize(allocations)
    if type(expected_revision) is not int or not isinstance(operation, str) or not operation:
        raise ValueError('receipt revision and issuance required')
    digest = hashlib.sha256(json.dumps([allocations, partial], sort_keys=True).encode()).hexdigest()
    old = R.annotations(c).get(sid, {})
    if not preview and old.get('allocation_operation') == operation and old.get('allocation_revision') == expected_revision+1 and old.get('allocation_status') != 'released':
        if old.get('allocation_digest') != digest:
            raise ValueError('issuance already used for a different allocation')
        return snapshot(c, sid)
    if revision(c, sid) != expected_revision:
        raise ValueError('stale receipt revision: reopen correction')
    ids = [a['expense_id'] for a in allocations if 'expense' not in a]
    before = fingerprint(c, sid, ids)
    if expected_fingerprint is not None and expected_fingerprint != before:
        raise ValueError('payment facts changed: reopen correction')
    receipt = row(c, sid)
    if receipt['amount'] <= 0 or receipt['scope'] == 'business' or receipt['kind'] not in ('income', 'transfer') or receipt['class'] not in (None, 'income', 'own_transfer'):
        raise ValueError('positive personal receipt required')
    net = sum(a['amount'] * (1 if a['payer'] == 'owner' else -1) for a in allocations)
    if type(partial) is not bool:
        raise ValueError('partial must be boolean')
    if net < 0 or net > receipt['amount'] or (not partial and net != receipt['amount']):
        raise ValueError('allocations must reconcile to the receipt; mark a positive remainder partial')
    input_allocations = json.loads(json.dumps(allocations))
    c.execute('SAVEPOINT receipt_replace')
    try:
        _release(c, sid)
        # Receipt classification is changed before inserting allocations so an
        # evidence-edit trigger cannot invalidate the newly written allocation.
        c.execute("UPDATE spend_entry SET kind='transfer',class='own_transfer' WHERE id=?", (sid,))
        meta = R.annotations(c)
        for a in allocations:
            if 'expense' in a:
                evidence = a['expense']
                if evidence['category_id'] is not None and not c.execute('SELECT 1 FROM spend_category WHERE id=?',(evidence['category_id'],)).fetchone():
                    raise ValueError('venue category not found')
                a['expense_id'] = c.execute("INSERT INTO spend_entry(date,name,amount,category_id,method,source,kind,class) VALUES(?,?,?,?,?,'chat','expense','spend')",
                                            tuple(evidence[k] for k in ('date','name','amount','category_id','method'))).lastrowid
            e = row(c, a['expense_id'])
            if e['id'] == sid or e['kind'] != 'expense' or e['amount'] <= 0 or e['scope'] == 'business' or e['class'] not in (None, 'spend'):
                raise ValueError('positive personal expense required')
            refunds = c.execute("SELECT COALESCE(-SUM(amount),0) FROM spend_entry WHERE kind='expense' AND amount<0 AND pair_id=?", (e['pair_id'],)).fetchone()[0] if e['pair_id'] is not None else 0
            if a['payer'] == 'friend':
                if a['share_id'] is not None or c.execute('SELECT 1 FROM ledger_share WHERE spend_id=?', (e['id'],)).fetchone():
                    raise ValueError('friend payment cannot settle an owner-paid share')
                if c.execute('SELECT 1 FROM ledger_receipt_allocation WHERE expense_id=?', (e['id'],)).fetchone():
                    raise ValueError('friend payment already belongs to another receipt')
                if any(v.get('spend_id') == e['id'] and not v.get('allocation_mode') and c.execute('SELECT 1 FROM spend_entry WHERE id=?', (key,)).fetchone() for key,v in meta.items()):
                    raise ValueError('friend payment has an existing owner-payment credit')
                capacity = e['amount']-refunds
            else:
                if meta.get(e['id'], {}).get('settlement_payer') == 'friend':
                    if c.execute("SELECT 1 FROM ledger_receipt_allocation WHERE expense_id=? AND payer='friend'", (e['id'],)).fetchone():
                        raise ValueError('friend payment already belongs to another receipt')
                    R.put(c, e['id'], dict(settlement_payer='owner'))
                used = c.execute('SELECT COALESCE((SELECT amount FROM v_ledger_reimbursement WHERE spend_id=s.id),0)+' + R.reimbursement_sql('s') + ' FROM spend_entry s WHERE id=?', (e['id'],)).fetchone()[0]
                capacity = e['amount']-used-refunds
            if a['amount'] > capacity:
                raise ValueError('allocation exceeds available expense capacity: %d' % max(0, capacity))
            previous_closed = 0
            if a['share_id'] is not None:
                shares = D.rows(c, 'SELECT * FROM ledger_share WHERE id=? AND spend_id=?', (a['share_id'], e['id']))
                if not shares or a['amount'] > shares[0]['amount']-shares[0]['received']:
                    raise ValueError('allocation exceeds remaining share')
                h = shares[0]
                previous_closed = h['closed']
                c.execute('UPDATE ledger_share SET received=received+?,closed=CASE WHEN received+?=amount THEN 1 ELSE closed END WHERE id=?', (a['amount'], a['amount'], h['id']))
            elif a['payer'] == 'owner' and c.execute('SELECT 1 FROM ledger_share WHERE spend_id=?', (e['id'],)).fetchone():
                raise ValueError('choose the existing share for this expense')
            if a['payer'] == 'friend':
                R.put(c, e['id'], dict(settlement_payer='friend'))
            c.execute('INSERT INTO ledger_receipt_allocation(receipt_id,expense_id,amount,payer,share_id,previous_closed) VALUES(?,?,?,?,?,?)',
                      (sid, e['id'], a['amount'], a['payer'], a['share_id'], previous_closed))
        R.put(c, sid, dict(allocation_mode=True, income_confirmed=True, allocation_revision=expected_revision+1,
                          allocation_status='partial' if net != receipt['amount'] else 'settled',
                          allocation_operation=operation, allocation_digest=digest, unallocated=receipt['amount']-net))
        result = snapshot(c, sid)
        result['fingerprint'] = before
        result['input_allocations'] = input_allocations
        if preview:
            c.execute('ROLLBACK TO receipt_replace')
        c.execute('RELEASE receipt_replace')
        return result
    except Exception:
        c.execute('ROLLBACK TO receipt_replace')
        c.execute('RELEASE receipt_replace')
        raise


def candidates(c, sid):
    """Same-day combinations, ranked as possibilities; never writes a guess."""
    receipt = row(c, sid)
    meta = R.annotations(c)
    anchor = meta.get(sid, {}).get('spend_id')
    current = snapshot(c, sid)['allocations']
    anchors = {a['expense_id'] for a in current} or ({anchor} if anchor else set())
    expenses = D.rows(c, "SELECT * FROM spend_entry WHERE kind='expense' AND amount>0 "
                         "AND COALESCE(class,'spend')='spend' AND scope<>'business' AND date<=? "
                         "AND date>=date(?,'-14 days') ORDER BY date DESC,at_time,id", (receipt['date'], receipt['date']))
    streams = {r['id']:r for r in D.rows(c, 'SELECT * FROM fixed_cost')}
    expenses = [e for e in expenses if R.fixed_class(e, streams.get(e['stream_id'],{}), meta.get(e['id'],{}), e['is_fixed']) not in R.FIXED]
    from ledger_v6 import Methods, nfkc, minutes
    methods = Methods(c)
    def method_id(raw):
        mid = methods.names.get(nfkc(raw or ''))
        return methods.canon(mid) if mid else None
    links = {}
    for link in D.rows(c, "SELECT spend_id,entity_kind,entity_ref FROM spend_link WHERE entity_kind='person'"):
        links.setdefault(link['spend_id'],set()).add(link['entity_ref'])
    appointments = {r['spend_id']:r['appointment_id'] for r in D.rows(c, 'SELECT spend_id,appointment_id FROM ledger_split')}
    groups = []
    for pair in itertools.combinations(expenses, 2):
        if pair[0]['date'] != pair[1]['date'] or anchors and not anchors.intersection(e['id'] for e in pair):
            continue
        # Ratio is suggestion evidence only. Explicit allocations need no ratio.
        for denominator in R.PAIR_SHARE_DENOMINATORS:
            for payers in (('owner', 'owner'), ('owner', 'friend'), ('friend', 'owner')):
                amounts = [e['amount']//denominator if p == 'owner' else e['amount']-e['amount']//denominator for e, p in zip(pair, payers)]
                net = sum(n*(1 if p == 'owner' else -1) for n, p in zip(amounts, payers))
                if net != receipt['amount'] or any(e['amount'] % denominator for e in pair):
                    continue
                allocations = [dict(expense_id=e['id'], amount=n, payer=p) for e, n, p in zip(pair, amounts, payers)]
                for a in allocations:
                    shares = D.rows(c, 'SELECT h.*,p.name,p.display_name,p.aliases FROM ledger_share h JOIN persons p ON p.id=h.person_id WHERE spend_id=?', (a['expense_id'],))
                    if shares:
                        from ledger_v6 import norm, _json_list
                        matches = [h for h in shares if norm(receipt['name']) in {norm(n) for n in [h['name'],h['display_name']]+_json_list(h['aliases']) if n}]
                        if len(matches) == 1:
                            a['share_id'] = matches[0]['id']
                try:
                    plan = replace(c, sid, allocations, revision(c, sid), 'preview', preview=True)
                except ValueError:
                    continue
                plan.update(denominator=denominator, total=sum(e['amount'] for e in pair),
                            owner_cost=sum(e['amount']-n if p == 'owner' else n for e,n,p in zip(pair,amounts,payers)))
                x,y = pair
                times = [minutes(e['at_time'] or '') for e in pair]
                plan['rank'] = [int(bool(method_id(x['method'])) and method_id(x['method']) == method_id(y['method'])),
                                int(bool(links.get(x['id'],set()) & links.get(y['id'],set()))),
                                int(bool(appointments.get(x['id'])) and appointments.get(x['id'])==appointments.get(y['id'])),
                                -abs(times[0]-times[1]) if all(n is not None for n in times) else -1440]
                groups.append(plan)
    groups.sort(key=lambda g: (int(all(a['payer']=='owner' for a in g['allocations'])), int(g['denominator']==2),
                               g['allocations'][0]['date'], *g['rank']), reverse=True)
    return dict(receipt=receipt, revision=revision(c, sid), candidates=groups[:R.PAIR_LIMIT], candidate_count=len(groups))



def execute(c, action, data):
    if action == 'versions':
        facts = D.rows(c, "SELECT item,detail,status FROM ledger_preference WHERE topic=? AND json_extract(detail,'$.allocation_mode')=1 ORDER BY item", (R.TOPIC,))
        return {'stamp':hashlib.sha256(json.dumps(facts,sort_keys=True).encode()).hexdigest()}
    if action == 'receipts':
        meta = R.annotations(c)
        found = D.rows(c, "SELECT * FROM spend_entry WHERE amount>0 AND scope<>'business' "
                         "AND kind IN ('income','transfer') AND COALESCE(class,'income') IN ('income','own_transfer') ORDER BY date DESC,id DESC")
        return [r for r in found if r['id'] in meta or R.received_person(c, r)]
    if action == 'expenses':
        return D.rows(c, "SELECT * FROM spend_entry WHERE amount>0 AND kind='expense' "
                        "AND COALESCE(class,'spend')='spend' AND scope<>'business' ORDER BY date DESC,at_time,id")
    sid = data['receipt_id']
    if type(sid) is not int:
        raise ValueError('integer receipt id required')
    if action == 'show':
        return snapshot(c, sid)
    if action == 'candidates':
        return candidates(c, sid)
    if action in ('preview', 'replace', 'undo'):
        return replace(c, sid, [] if action == 'undo' else data['allocations'], data['revision'], data['operation'],
                       partial=True if action == 'undo' else data.get('partial', False),
                       expected_fingerprint=data.get('fingerprint'), preview=action == 'preview')
    raise ValueError('unknown allocation action')


def cli(K, argv):
    import argparse
    import os
    p = argparse.ArgumentParser(prog='ledger-allocation')
    p.add_argument('action', choices=('show','candidates','preview','replace','undo','receipts','expenses','versions'))
    p.add_argument('--data', required=True)
    args = p.parse_args(argv)
    c = None
    try:
        if not os.path.isfile(K.DB_PATH):
            raise ValueError('database not found')
        c = K.get_conn()
        c.execute('BEGIN IMMEDIATE' if args.action in ('replace','undo') else 'BEGIN')
        result = execute(c, args.action, json.loads(args.data))
        c.commit()
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    except (ValueError, KeyError, TypeError, sqlite3.Error) as e:
        if c:
            c.rollback()
        print(json.dumps({'error': str(e)}, ensure_ascii=False))
        raise SystemExit(2)
    finally:
        if c:
            c.close()
