"""078 settlement verbs. All mutations run in one kit transaction.

Accounting follows docs/ledger/onboarding-v6-spec.md owner rulings (12:04)
+ migration 075 v_spend_link: paying for another person is the owner's spend.
A receivable is not income. Only actually received shares reduce month spend;
waiving an unpaid remainder never invents a reimbursement. Raw payment and
transfer evidence stays intact. Readers subtract v_ledger_reimbursement.
"""
import argparse
import datetime as dt
import json
from zoneinfo import ZoneInfo

import agent_conf
import ledger_profile as LP


def rows(c, sql, args=()):
    cur = c.execute(sql, args)
    return [dict(zip([d[0] for d in cur.description], r)) for r in cur]


def one(c, table, key, value):
    out = rows(c, 'SELECT * FROM '+table+' WHERE '+key+'=?', (value,))
    if not out:
        raise ValueError(table+' row not found')
    return out[0]


def people(c, aid):
    # The owner is implicit: event_persons contains the other participants.
    return rows(c, 'SELECT p.id,p.name,p.display_name,p.ledger_no_split FROM persons p JOIN event_persons e ON p.id=e.person_id WHERE e.event_id=? ORDER BY p.id', (aid,))


def snapshot(c, sid):
    l = one(c, 'ledger_split', 'spend_id', sid)
    s = one(c, 'spend_entry', 'id', sid)
    b = one(c, 'block', 'id', l['appointment_id'])
    return dict(l, merchant=s['name'], amount=s['amount'], date=s['date'], appointment=b['title'],
                people=people(c, b['id']), shares=rows(c, 'SELECT * FROM v_ledger_share WHERE spend_id=? ORDER BY person_id', (sid,)))


def link(c, sid, aid):
    s = one(c, 'spend_entry', 'id', sid)
    b = one(c, 'block', 'id', aid)
    if s['kind'] != 'expense' or s['amount'] <= 0 or s['class'] not in (None, 'spend'):
        raise ValueError('only a positive spend can be split')
    if b['kind_token'] != 'appointment':
        raise ValueError('appointment block required')
    ps = people(c, aid)
    if not ps:
        return None
    old = rows(c, 'SELECT * FROM ledger_split WHERE spend_id=?', (sid,))
    if old and old[0]['appointment_id'] != aid:
        raise ValueError('payment already linked to a different appointment')
    state = 'skip' if all(p['ledger_no_split'] for p in ps) else 'ask'
    c.execute('INSERT OR IGNORE INTO ledger_split(spend_id,appointment_id,state,owner_share) VALUES(?,?,?,?)', (sid, aid, state, s['amount'] if state=='skip' else None))
    return snapshot(c, sid)


def allocate(c, sid, amounts):
    snap = snapshot(c, sid)
    if snap['state'] == 'split':
        raise ValueError('already split')
    eligible = [p for p in snap['people'] if not p['ledger_no_split']]
    keys = {'owner'} | {str(p['id']) for p in eligible}
    if set(amounts) != keys or any(type(v) is not int or v < 0 for v in amounts.values()):
        raise ValueError('one nonnegative integer amount per participant including owner required')
    if sum(amounts.values()) != snap['amount']:
        raise ValueError('amounts must sum to payment')
    for p in eligible:
        c.execute('INSERT INTO ledger_share(spend_id,person_id,amount) VALUES(?,?,?)', (sid, p['id'], amounts[str(p['id'])]))
    c.execute("UPDATE ledger_split SET state='split', owner_share=?, amounts_json=? WHERE spend_id=?", (amounts['owner'], json.dumps(amounts), sid))
    for t in rows(c, "SELECT id FROM spend_entry WHERE kind='transfer' AND amount>0 AND date>=? ORDER BY date,id", (snap['date'],)):
        match_transfer(c,t['id'])
    return snapshot(c, sid)


def equal_amounts(snap, base):
    # A standing treat is paid by the owner; it never increases someone
    # else's equal share at a mixed appointment.
    amounts={'owner':base}
    for p in snap['people']:
        if p['ledger_no_split']:
            amounts['owner']+=base
        else:
            amounts[str(p['id'])]=base
    return amounts


def answer(c, d):
    sid, action = int(d['spend_id']), d['answer']
    snap = snapshot(c, sid)
    state = snap['state']
    if action == 'split' and state == 'ask':
        c.execute("UPDATE ledger_split SET state='how' WHERE spend_id=?", (sid,))
    elif action == 'no' and state == 'ask':
        c.execute("UPDATE ledger_split SET state='declined',owner_share= ? WHERE spend_id=?", (snap['amount'], sid))
    elif action in ('always_no', 'once') and state == 'declined' and len(snap['people']) == 1:
        if action == 'always_no':
            c.execute('UPDATE persons SET ledger_no_split=1 WHERE id=?', (snap['people'][0]['id'],))
        c.execute("UPDATE ledger_split SET state='skip' WHERE spend_id=?", (sid,))
    elif action == 'equal' and state == 'how':
        base, remainder = divmod(snap['amount'], len(snap['people'])+1)
        amounts = equal_amounts(snap,base)
        if not remainder:
            return allocate(c, sid, amounts)
        c.execute("UPDATE ledger_split SET state='remainder' WHERE spend_id=?", (sid,))
    elif action == 'remainder' and state == 'remainder':
        base, remainder = divmod(snap['amount'], len(snap['people'])+1)
        amounts = equal_amounts(snap,base)
        if str(d['person']) not in amounts:
            raise ValueError('remainder payer must be a participant')
        amounts[str(d['person'])] += remainder
        return allocate(c, sid, amounts)
    elif action == 'different' and state == 'how':
        c.execute("UPDATE ledger_split SET state='amounts',amounts_json='{}' WHERE spend_id=?", (sid,))
    elif action == 'amount' and state == 'amounts':
        amounts = json.loads(snap['amounts_json'])
        keys = ['owner']+[str(p['id']) for p in snap['people'] if not p['ledger_no_split']]
        person, amount = str(d['person']), d['amount']
        if person not in keys or type(amount) is not int or amount < 0:
            raise ValueError('invalid participant amount')
        amounts[person] = amount
        if set(amounts) == set(keys):
            return allocate(c, sid, amounts)
        if sum(amounts.values()) > snap['amount']:
            raise ValueError('amounts exceed payment')
        c.execute('UPDATE ledger_split SET amounts_json=? WHERE spend_id=?', (json.dumps(amounts), sid))
    else:
        raise ValueError('answer is not valid for this step')
    return snapshot(c, sid)


def match_snapshot(c, tid):
    m = one(c, 'ledger_settlement_match', 'transfer_id', tid)
    t = one(c, 'spend_entry', 'id', tid)
    h = one(c, 'v_ledger_share', 'id', m['share_id']) if m['share_id'] else None
    return dict(m, transfer=t, share=h,
                shares=rows(c, 'SELECT * FROM v_ledger_share WHERE spend_id=? ORDER BY person_id', (h['spend_id'],)) if h else [])


def credit(c, tid, finish=False):
    from ledger_review import annotations, reimbursement_sql
    if annotations(c).get(tid, {}).get('allocation_mode'):
        raise ValueError('receipt uses allocations; reopen its correction')
    m = one(c, 'ledger_settlement_match', 'transfer_id', tid)
    h = one(c, 'v_ledger_share', 'id', m['share_id'])
    t = one(c, 'spend_entry', 'id', tid)
    if m['credited'] or m['state'] not in ('ask',):
        raise ValueError('transfer already answered')
    available = c.execute('SELECT s.amount-COALESCE(r.amount,0)-' + reimbursement_sql('s') +
                          ' FROM spend_entry s LEFT JOIN v_ledger_reimbursement r ON r.spend_id=s.id WHERE s.id=?', (h['spend_id'],)).fetchone()[0]
    n = min(t['amount'], h['remaining'], available)
    if not n:
        raise ValueError('share is no longer open')
    c.execute('UPDATE ledger_share SET received=received+?, closed=? WHERE id=?', (n, int(finish or n == h['remaining']), h['id']))
    c.execute("UPDATE ledger_settlement_match SET state='matched',credited=?,previous_closed=? WHERE transfer_id=?", (n, h['closed'], tid))


def match_transfer(c, tid):
    t = one(c, 'spend_entry', 'id', tid)
    if t['kind'] != 'transfer' or t['amount'] <= 0 or t['class'] not in (None, 'own_transfer'):
        return None
    if LP.is_dust(t['amount']):
        return None                         # never asked about a few won
    if rows(c, 'SELECT 1 FROM ledger_settlement_match WHERE transfer_id=?', (tid,)):
        return None                         # ask once, including unrelated answers
    from ledger_review import annotations
    meta = annotations(c).get(tid, {})
    if meta.get('allocation_mode') or meta.get('spend_id'):
        return None
    candidates = []
    for h in rows(c, 'SELECT * FROM v_ledger_share WHERE remaining>0 AND date<=? ORDER BY date,id', (t['date'],)):
        p = one(c, 'persons', 'id', h['person_id'])
        from ledger_v6 import _json_list, norm
        names = [p['name'], p['display_name']] + _json_list(p['aliases'])
        if norm(t['name']) in {norm(n) for n in names if n}:
            candidates.append(h)
    if not candidates:
        return None
    exact = [h for h in candidates if h['remaining'] == t['amount']]
    # More than one equally good debt: do not pick one on the owner's behalf.
    chosen = exact[0] if len(exact) == 1 else candidates[0] if len(candidates) == 1 else None
    c.execute("INSERT INTO ledger_settlement_match(transfer_id,share_id,state) VALUES(?,?,'ask')", (tid, chosen['id'] if chosen else None))
    if chosen and len(exact) == 1:
        credit(c, tid)
    return match_snapshot(c, tid)


def transfer_answer(c, d):
    tid, action = int(d['transfer_id']), d['answer']
    m = one(c, 'ledger_settlement_match', 'transfer_id', tid)
    if action == 'unrelated' and m['state'] in ('ask', 'matched', 'confirmed'):
        if m['credited']:
            c.execute('UPDATE ledger_share SET received=received-?,closed=? WHERE id=?', (m['credited'], m['previous_closed'], m['share_id']))
        c.execute("UPDATE ledger_settlement_match SET state='unrelated',credited=0 WHERE transfer_id=?", (tid,))
    elif action == 'yes' and m['state'] == 'matched':
        c.execute("UPDATE ledger_settlement_match SET state='confirmed' WHERE transfer_id=?", (tid,))
    elif action in ('finish','remaining') and m['state'] == 'ask' and m['share_id']:
        credit(c, tid, action == 'finish')
        c.execute("UPDATE ledger_settlement_match SET state='confirmed' WHERE transfer_id=?", (tid,))
    elif action == 'choose' and m['state'] == 'ask' and m['share_id'] is None:
        h = one(c, 'v_ledger_share', 'id', int(d['share_id']))
        t = one(c, 'spend_entry', 'id', tid)
        p = one(c, 'persons', 'id', h['person_id'])
        from ledger_v6 import norm, _json_list
        if h['remaining'] <= 0 or h['date'] > t['date'] or norm(t['name']) not in {norm(n) for n in [p['name'],p['display_name']]+_json_list(p['aliases']) if n}:
            raise ValueError('share is not a candidate for this transfer')
        c.execute('UPDATE ledger_settlement_match SET share_id=? WHERE transfer_id=?', (h['id'],tid))
        if h['remaining'] == t['amount']:
            credit(c, tid)
    else:
        raise ValueError('invalid transfer answer')
    return match_snapshot(c, tid)


def weekly(c, today):
    cutoff = (dt.date.fromisoformat(today)-dt.timedelta(days=3)).isoformat()
    return rows(c, 'SELECT * FROM v_ledger_share WHERE remaining>0 AND date<? ORDER BY person_id,date,id', (cutoff,))


def appointment_day(b):
    if b['date']:
        return b['date']
    if not b['start_at']:
        return None
    instant=dt.datetime.fromisoformat(b['start_at'].replace('Z','+00:00'))
    if instant.tzinfo is None: instant=instant.replace(tzinfo=dt.timezone.utc)
    off=agent_conf.conf_int('AGENT_TZ_OFFSET_HOURS',None)
    zone=(dt.timezone(dt.timedelta(hours=off)) if off is not None and -12<=off<=14
          else ZoneInfo(b['display_tz'] or agent_conf.conf_str('AGENT_TZ','Asia/Seoul')))
    return instant.astimezone(zone).date().isoformat()


def sweep(c, since, until):
    appointments={}
    for b in rows(c, "SELECT b.id,b.title,b.date,b.start_at,(SELECT value FROM block_attr WHERE block_id=b.id AND attr_token='display_tz' ORDER BY source LIMIT 1) AS display_tz FROM block b WHERE b.kind_token='appointment' AND EXISTS(SELECT 1 FROM event_persons e WHERE e.event_id=b.id)"):
        day=appointment_day(b)
        if day and since<=day<=until:
            appointments.setdefault(day,[]).append({'id':b['id'],'title':b['title']})
    out = {'splits': [], 'transfers': [], 'ambiguous': []}
    for s in rows(c, 'SELECT * FROM spend_entry WHERE date BETWEEN ? AND ? ORDER BY date,id', (since, until)):
        if s['kind'] == 'transfer':
            m = match_transfer(c, s['id'])
            if m:
                out['transfers'].append(m)
        elif s['kind'] == 'expense' and s['amount'] > 0 and s['class'] in (None, 'spend'):
            old = rows(c, 'SELECT 1 FROM ledger_split WHERE spend_id=?', (s['id'],))
            if old:
                continue
            bs = appointments.get(s['date'],[])
            if len(bs) == 1:
                out['splits'].append(link(c, s['id'], bs[0]['id']))
            elif len(bs) > 1:
                out['ambiguous'].append({'spend_id':s['id'], 'appointments':bs})
    return out


def capture(c, sid, clear=True, mark=True):
    s = one(c, 'spend_entry', 'id', sid)
    if not clear or s['kind'] != 'expense' or s['amount'] <= 0:
        return None
    old = rows(c, 'SELECT * FROM ledger_capture_notice WHERE spend_id=?', (sid,))
    c.execute('INSERT OR IGNORE INTO ledger_capture_notice(spend_id) VALUES(?)', (sid,))
    setting = c.execute("SELECT value FROM config WHERE key='ledger_immediate_notice'").fetchone()
    if (setting and setting[0] == '0') or old and old[0]['immediate_shown']:
        return None
    if mark:
        c.execute('UPDATE ledger_capture_notice SET immediate_shown=1 WHERE spend_id=?', (sid,))
    return notice_row(c, sid)


def notice_row(c, sid):
    return rows(c, 'SELECT s.id,s.name AS merchant,s.amount,s.date,COALESCE(c.display_name,c.name,?) AS category FROM spend_entry s LEFT JOIN spend_category c ON c.id=s.category_id WHERE s.id=?', ('',sid))[0]


def correction_snapshot(c, sids, tids=()):
    sids, tids = sorted(set(sids)), sorted(set(tids))
    shares = [h for sid in sids for h in rows(c, 'SELECT * FROM ledger_share WHERE spend_id=? ORDER BY id', (sid,))]
    hids = {h['id'] for h in shares}
    matches = [m for m in rows(c, 'SELECT * FROM ledger_settlement_match ORDER BY transfer_id')
               if m['share_id'] in hids or m['transfer_id'] in tids]
    ps = sorted({p['id'] for sid in sids for p in snapshot(c,sid)['people']})
    return dict(sids=sids, tids=tids,
                payments=[one(c,'spend_entry','id',sid) for sid in sorted(set(sids+tids))],
                splits=[one(c,'ledger_split','spend_id',sid) for sid in sids], shares=shares, matches=matches,
                people=[dict(id=pid, ledger_no_split=one(c,'persons','id',pid)['ledger_no_split']) for pid in ps])


def restore_correction(c, state, current):
    hids = {h['id'] for h in current['shares']}
    if any(a['share_id'] in hids for a in rows(c,'SELECT share_id FROM ledger_receipt_allocation')):
        raise ValueError('release receipt allocations with ledger-allocation undo before changing the split')
    for m in current['matches']:
        c.execute('DELETE FROM ledger_settlement_match WHERE transfer_id=?',(m['transfer_id'],))
    for sid in current['sids']:
        c.execute('DELETE FROM ledger_share WHERE spend_id=?',(sid,))
    for table, key in (('ledger_split','splits'),('ledger_share','shares'),('ledger_settlement_match','matches')):
        for r in state[key]:
            if table=='ledger_split':
                vals={k:v for k,v in r.items() if k!='spend_id'}
                c.execute('UPDATE ledger_split SET '+','.join(k+'=?' for k in vals)+' WHERE spend_id=?', [*vals.values(),r['spend_id']])
            else:
                c.execute('INSERT INTO '+table+'('+','.join(r)+') VALUES('+','.join('?' for _ in r)+')', list(r.values()))
    for p in state['people']:
        c.execute('UPDATE persons SET ledger_no_split=? WHERE id=?',(p['ledger_no_split'],p['id']))


def execute(c, action, d):
    from ledger_correction import history, save, pop
    if action not in ('answer','change','transfer-answer','weekly-answer','undo'):
        return _execute(c, action, d)
    if 'spend_id' in d:
        key='split:'+str(int(d['spend_id']))
        sids,tids=[int(d['spend_id'])],[]
    elif 'transfer_id' in d:
        tid=int(d['transfer_id'])
        key='split-transfer:'+str(tid)
        match=one(c,'ledger_settlement_match','transfer_id',tid)
        hid=match['share_id'] or d.get('share_id')
        sids=[one(c,'ledger_share','id',int(hid))['spend_id']] if hid else []
        tids=[tid]
    elif action in ('weekly-answer','undo'):
        key='split-weekly'
        sids=sorted({one(c,'ledger_share','id',int(h))['spend_id'] for h in d.get('shares',{})})
        tids=[]
    else:
        raise ValueError('split or transfer required')
    entries=history(c,key)
    if action=='undo':
        if not entries:
            raise ValueError('no split answer to undo')
        last=entries[-1]['after']
        current=correction_snapshot(c,last['sids'],last['tids'])
        before=pop(c,key,current)
        restore_correction(c,before,current)
        return dict(restored=True)
    before=correction_snapshot(c,sids,tids)
    if action=='change':
        sid=int(d['spend_id'])
        # Clear derived shares/matches before replaying the ordinary answer
        # state machine; retained snapshots make the reset itself reversible.
        blank=dict(before, shares=[], matches=[], splits=[dict(before['splits'][0],state='ask',owner_share=None,amounts_json=None)])
        restore_correction(c,blank,before)
        result=snapshot(c,sid)
    else:
        result=_execute(c,action,d)
    save(c,key,before,correction_snapshot(c,sids,tids))
    return result


def _execute(c, action, d):
    if action == 'link': return link(c, int(d['spend_id']), int(d['appointment_id']))
    if action == 'get': return snapshot(c, int(d['spend_id']))
    if action == 'answer': return answer(c, d)
    if action == 'no-split':
        if type(d['enabled']) is not bool: raise ValueError('enabled must be boolean')
        one(c, 'persons', 'id', int(d['person_id']))
        c.execute('UPDATE persons SET ledger_no_split=? WHERE id=?', (int(d['enabled']),int(d['person_id'])))
        return {'saved':True}
    if action == 'transfer': return match_transfer(c, int(d['transfer_id']))
    if action == 'transfer-answer': return transfer_answer(c,d)
    if action == 'sweep': return sweep(c,d['since'],d['until'])
    if action == 'transfer-candidates':
        t=one(c,'spend_entry','id',int(d['transfer_id']))
        from ledger_v6 import norm, _json_list
        out=[]
        for h in rows(c,'SELECT * FROM v_ledger_share WHERE remaining>0 AND date<=? ORDER BY date,id',(t['date'],)):
            p=one(c,'persons','id',h['person_id'])
            if norm(t['name']) in {norm(n) for n in [p['name'],p['display_name']]+_json_list(p['aliases']) if n}: out.append(h)
        return out
    if action == 'transfer-shown':
        c.execute('UPDATE ledger_settlement_match SET shown=1 WHERE transfer_id=?',(int(d['transfer_id']),))
        return {'shown':True}
    if action == 'pending-transfers':
        return [match_snapshot(c,r['transfer_id']) for r in rows(c,"SELECT transfer_id FROM ledger_settlement_match WHERE state IN ('ask','matched') AND shown=0 ORDER BY transfer_id")]
    if action == 'weekly': return weekly(c,d['today'])
    if action == 'weekly-answer':
        # Caller sends the exact share ids/remaining amounts shown to the owner.
        shown = {int(k):v for k,v in d['shares'].items()}
        eligible = {r['id']:r['remaining'] for r in weekly(c,d['today'])}
        if any(eligible.get(k) != v for k,v in shown.items()): raise ValueError('review changed; render again')
        if d['answer'] == 'received':
            from ledger_review import reimbursement_sql
            by_expense = {}
            for hid, amount in shown.items():
                sid = one(c, 'ledger_share', 'id', hid)['spend_id']
                by_expense[sid] = by_expense.get(sid, 0) + amount
            for sid, amount in by_expense.items():
                available = c.execute('SELECT s.amount-COALESCE(r.amount,0)-' + reimbursement_sql('s') +
                                      ' FROM spend_entry s LEFT JOIN v_ledger_reimbursement r ON r.spend_id=s.id WHERE s.id=?', (sid,)).fetchone()[0]
                if amount > available:
                    raise ValueError('share credit exceeds available expense capacity')
            for hid in shown:
                c.execute('UPDATE ledger_share SET received=amount,closed=1 WHERE id=?', (hid,))
        elif d['answer'] != 'keep': raise ValueError('invalid weekly answer')
        return weekly(c,d['today'])
    if action == 'capture': return capture(c,int(d['spend_id']),d.get('clear',True))
    if action == 'notices':
        ids = rows(c,'SELECT n.spend_id FROM ledger_capture_notice n JOIN spend_entry s ON s.id=n.spend_id WHERE s.date BETWEEN ? AND ? ORDER BY s.date,s.id',(d['since'],d['until']))
        return [notice_row(c,r['spend_id']) for r in ids]
    if action == 'setting':
        if type(d['enabled']) is not bool: raise ValueError('enabled must be boolean')
        c.execute("INSERT INTO config(key,value) VALUES('ledger_immediate_notice',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(int(d['enabled'])),))
        return {'enabled':d['enabled']}
    raise ValueError('unknown settlement action')


def cli(K, argv):
    p=argparse.ArgumentParser(prog='ledger-split')
    p.add_argument('action'); p.add_argument('--data',default='{}')
    a=p.parse_args(argv)
    c=None
    try:
        d=json.loads(a.data)
        c=K.get_conn()
        c.execute('BEGIN IMMEDIATE')
        out=execute(c,a.action,d)
        c.commit()
        print(json.dumps(out,ensure_ascii=False))
    except (ValueError, KeyError, TypeError) as e:
        if c: c.rollback()
        print(json.dumps({'error':str(e)})); return 2
    finally:
        if c: c.close()
    return 0
