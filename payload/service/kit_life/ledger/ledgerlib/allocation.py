# -*- coding: utf-8 -*-
"""Revisitable Candidate A editor; the kit owns every financial write.

A separate issuance suspends onboarding without changing its cursor. Issuance
and DB revision bind callbacks. A committed operation can be recovered after
state persistence fails, without replacing or crediting the receipt twice.
"""
import datetime
import hashlib
import html
import json
import re
import uuid

from . import kit, text
from .markup import quote_card
from money_format import comma


def call(action, **data):
    return kit.run('ledger-allocation', action, '--data', json.dumps(data, ensure_ascii=False))


def t(key, **values):
    return text.t('ledger.allocation.' + key, **values)


def won(amount):
    return comma(amount)


@quote_card
def candidate_text(plan):
    allocations = plan['allocations']
    date = datetime.date.fromisoformat(allocations[0]['date'])
    single = len(allocations) == 1
    quote = lambda value: '<blockquote>'+value+'</blockquote>'
    lines = [t('head_single' if single else 'head', m=date.month, d=date.day), '']
    lines += [quote(t('expense', name=html.escape(a['name']), amount=won(a['payment']))) for a in allocations]
    mixed = any(a['payer'] == 'friend' for a in allocations)
    if mixed:
        lines += [quote(t('paid', owner=won(sum(a['payment'] for a in allocations if a['payer']=='owner')),
                    friend=won(sum(a['payment'] for a in allocations if a['payer']=='friend'))))]
    refunded = sum(a.get('refunded',0) for a in allocations)
    total = sum(a['payment'] for a in allocations)-refunded
    if refunded:
        from . import dashboard
        lines += [quote(dashboard.rt('row',name=dashboard.rt('refunds'),amount=text.t('ledger.split.money',amount=won(refunded))))]
    lines += ['', '<b>'+t('total_single' if single else 'total', amount=won(total))+'</b>']
    cost = sum(a['owner_cost'] for a in allocations)
    if mixed:
        # A net receipt is not half the evening. Keep the half statement next
        # to the owner's actual half, and show exactly one net transfer line.
        lines += [quote(t('owner', amount=won(cost)))]
        if cost*2 == total:
            lines += [t('half')]
        lines += [quote(t('net', amount=won(plan['receipt']['amount'])))]
    else:
        lines += [quote(t('received', amount=won(plan['receipt']['amount'])))]
        if plan['receipt']['amount']*2 == total:
            lines += [t('half')]
        lines += [quote(t('owner', amount=won(cost)))]
    if not mixed and len(allocations) == 2:
        a,b = allocations
        lines += ['', t('explain', first=html.escape(a.get('summary_name') or a['name']), a=won(a['amount']),
                        second=html.escape(b.get('summary_name') or b['name']), b=won(b['amount']))]
    if plan.get('unallocated'):
        # Reuse the kit's remainder copy; an unallocated receipt is never income.
        lines += ['', text.t('ledger.split.remaining', name=html.escape(plan['receipt']['name']), amount=text.t('ledger.split.money', amount=won(plan['unallocated'])))]
    return '\n'.join(lines).replace('<blockquote>\n', '<blockquote>').replace('\n</blockquote>', '</blockquote>')


def token(q):
    return hashlib.sha256(json.dumps(q, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def question(editor):
    from . import onboarding as O
    qid = 'review_allocation_' + str(editor.get('receipt_id', 'choose')) + '_' + editor['issuance'][:12]
    mode = editor['mode']
    choices = []
    if mode == 'candidate':
        ask = candidate_text(editor['plan'])
        choices = [{'label':t(key), 'value':value} for key,value in [('confirm','confirm'),('select','select'),('amount','amount')]]
        if len(editor['plan']['allocations']) == 3:
            choices[0]['label'] = t('confirm_three')
        elif len(editor['plan']['allocations']) == 1:
            choices[0]['label'] = t('confirm_single')
    elif mode == 'receipts':
        ask = O.dashboard.rt('pair.ask') + '\n\n' + '\n'.join('%d. %s \xb7 %s \xb7 %s' % (n,r['date'],html.escape(r['name']),text.t('ledger.split.money', amount=won(r['amount']))) for n,r in enumerate(editor['receipts'],1))
        choices = [{'label':O._pair_label(dict(r, display_name=r['name'])), 'value':str(r['id'])} for r in editor['receipts']]
    elif mode == 'groups':
        ask = O.dashboard.rt('pair.ask') + '\n\n' + '\n\n'.join('%d.\n%s' % (n,candidate_text(p)) for n,p in enumerate(editor['groups'],1))
        choices = [{'label':O._pair_label(dict(date=p['allocations'][0]['date'],display_name='/'.join(a['name'][:3] for a in p['allocations']))), 'value':str(n)} for n,p in enumerate(editor['groups'])]
        choices += [{'label':t('select'), 'value':'select'}]
    elif mode == 'expenses':
        selected = set(editor.get('selected', []))
        expenses = editor['expenses']
        ask = O.dashboard.rt('pair.ask') + '\n\n' + '\n'.join('%d. %s \xb7 %s \xb7 %s' % (n,e['date'],html.escape(e['name']),text.t('ledger.split.money', amount=won(e['amount']))) for n,e in enumerate(expenses,1))
        choices = [{'label':('\u2713 ' if e['id'] in selected else '')+O._pair_label(dict(e,display_name=e['name'])), 'value':str(e['id'])} for e in expenses]
        choices += [{'label':t('confirm'), 'value':'selected'}]
    else:
        # The agent maps named venues/amounts/payers to this structured reply;
        # the public card never displays implementation keys or raw JSON.
        ask = candidate_text(editor['plan']) if editor.get('plan') else O.dashboard.rt('pair.ask')
    q = O._q(qid, ask, choices, free=True, issuance=editor['issuance'], revision=editor.get('revision'),
             answer_format={'allocations':[{'expense_id':'integer','amount':'integer won','payer':'owner|friend'}]})
    stamp = token(q)
    q['card'] = {'text':ask, 'parse_mode':'HTML', 'buttons':[[{'text':'%d. %s' % (n,c['label']), 'callback_data':'opt:la:%s:%d' % (stamp,n-1)}] for n,c in enumerate(choices,1)]}
    return q


def _issue(editor):
    editor['issuance'] = uuid.uuid4().hex
    editor['question'] = question(editor)
    return {'question':editor['question'], 'done':False, 'correction':True}


def open_editor(receipt_id=None):
    from . import onboarding as O
    st = O.load_state()
    flow = st.get('flow') or {}
    if receipt_id is None:
        pending = flow.get('pending') or ''
        if pending.startswith(('review_pair_', 'review_person_')):
            receipt_id = int(pending.rsplit('_',1)[1])
        else:
            receipt_id = st.get('last_settlement_receipt')
            if receipt_id is None:
                answered = [qid for qid in flow.get('answers',{}) if qid.startswith('review_pair_') and flow['answers'][qid].get('ok')]
                if answered:
                    receipt_id = int(answered[-1].rsplit('_',1)[1])
    if receipt_id is None:
        receipts = call('receipts')
        if len(receipts) == 1:
            receipt_id = receipts[0]['id']
        else:
            editor = {'mode':'receipts', 'receipts':receipts}
            out = _issue(editor)
            st['correction'] = editor
            O.save_state(st)
            return out
    snap = call('show', receipt_id=int(receipt_id))
    if snap['allocations']:
        return preview(receipt_id, [{k:a[k] for k in ('expense_id','amount','payer','share_id')}
                                    for a in snap['allocations']], partial=bool(snap['unallocated']))
    data = call('candidates', receipt_id=int(receipt_id))
    editor = dict(receipt_id=int(receipt_id), revision=data['revision'], mode='groups', groups=data['candidates'])
    if len(data['candidates']) == 1:
        editor.update(mode='candidate', plan=data['candidates'][0])
    elif not data['candidates']:
        editor.update(mode='expenses', expenses=call('expenses')[:15], selected=[])
    out = _issue(editor)
    st['correction'] = editor
    O.save_state(st)
    return out


def say(message, receipt_id=None):
    return open_editor(receipt_id)


def current():
    """Recover a committed issuance before serving any persisted old callback."""
    from . import onboarding as O
    st = O.load_state()
    editor = st.get('correction')
    if not editor:
        return None
    if editor.get('receipt_id'):
        snap = call('show', receipt_id=editor['receipt_id'])
        if snap.get('operation') == editor['issuance'] and snap['revision'] == editor['revision']+1 and snap['status'] != 'released':
            return _finish(st, snap)
        if snap['revision'] != editor['revision']:
            return open_editor(editor['receipt_id'])
    return {'question':editor['question'], 'done':False, 'correction':True}


def _finish(st, snap):
    from . import onboarding as O
    sid = snap['receipt']['id']
    flow = st.get('flow')
    if flow:
        flow['has_allocations'] = True
        # Preserve unrelated answers; refresh all financial screens from the kit.
        flow['questions'] = {k:v for k,v in flow.get('questions',{}).items() if not k.startswith('review_') and k!='diagnosis'}
        for qid in ('review_person_'+str(sid), 'review_pair_'+str(sid)):
            if any(r['id']==sid for r in flow.get('review_people',[])):
                flow['answers'][qid] = {'pair':True} if qid.startswith('review_person_') else {'ok':True}
        if flow.get('pending') in ('review_person_'+str(sid),'review_pair_'+str(sid)):
            flow['pending'] = None
            flow['asked_at'] = None
        flow.pop('pair_typing',None)
    st['last_settlement_receipt'] = sid
    st.pop('correction',None)
    edit_token = uuid.uuid4().hex
    st['settlement_result'] = dict(receipt_id=sid, revision=snap['revision'], token=edit_token)
    O.save_state(st)
    date = datetime.date.fromisoformat(snap['allocations'][0]['date'])
    line = t('applied_single' if len(snap['allocations']) == 1 else 'applied',
             m=date.month, d=date.day, names=html.escape('\u00b7'.join(a['name'] for a in snap['allocations'])),
             amount=O.diagtext.money(snap['allocated']))
    return {'recorded':'review_allocation_'+str(sid), 'allocation':snap, 'correction':False,
            'done':bool(st.get('completed_at')), 'card':{'text':line, 'parse_mode':'HTML',
            'buttons':[[{'text':t('edit'), 'callback_data':'opt:la:edit:'+edit_token}]]}}


def answer(qid, value):
    from . import onboarding as O
    st = O.load_state()
    editor = st.get('correction')
    if not editor or editor['question']['id'] != qid:
        raise ValueError('stale or unissued allocation question')
    if editor.get('receipt_id'):
        snap = call('show', receipt_id=editor['receipt_id'])
        if snap.get('operation') == editor['issuance'] and snap['revision'] == editor['revision']+1 and snap['status'] != 'released':
            return _finish(st,snap)
        if snap['revision'] != editor['revision']:
            raise ValueError('stale receipt revision: reopen correction')
    q = editor['question']
    value = str(value).strip()
    matches = [c for n,c in enumerate(q['choices'],1) if value in (c['label'],'%d. %s' % (n,c['label']))]
    if len(matches)==1:
        value = matches[0]['value']
    mode = editor['mode']
    if mode=='receipts':
        if not any(str(r['id'])==value for r in editor['receipts']):
            raise ValueError('choose an issued receipt')
        return open_editor(int(value))
    if value=='select':
        editor.update(mode='expenses',expenses=call('expenses')[:15],selected=[])
    elif mode=='groups' and value.isdigit():
        index = int(value)
        if index>=len(editor['groups']):
            raise ValueError('choose an issued candidate')
        editor.update(mode='candidate',plan=editor['groups'][index])
    elif mode=='expenses' and value.isdigit():
        sid = int(value)
        if not any(e['id']==sid for e in editor['expenses']):
            raise ValueError('choose an issued expense')
        selected = editor['selected']
        if sid in selected:
            selected.remove(sid)
        else:
            selected.append(sid)
    elif mode=='expenses' and value=='selected':
        selected = [e for e in editor['expenses'] if e['id'] in editor['selected']]
        if len(selected) not in (1, 2, 3):
            raise ValueError('choose one to three expenses')
        allocations = [dict(expense_id=e['id'],amount=(call('show',receipt_id=editor['receipt_id'])['receipt']['amount']
                       if len(selected)==1 else e['amount']//2),payer='owner') for e in selected]
        return preview(editor['receipt_id'], allocations)
    elif mode=='candidate' and value=='amount':
        editor['mode']='amounts'
    elif mode=='candidate' and value=='confirm':
        plan = editor['plan']
        snap = call('replace',receipt_id=editor['receipt_id'],revision=editor['revision'],operation=editor['issuance'],
                    fingerprint=plan['fingerprint'],allocations=plan.get('input_allocations') or [{k:a[k] for k in ('expense_id','amount','payer','share_id')} for a in plan['allocations']],
                    partial=bool(plan['unallocated']))
        return _finish(st,snap)
    else:
        try:
            data=json.loads(value)
        except ValueError:
            values=[int(n.replace(',','')) for n in re.findall(r'\d[\d,]*',value)]
            if not editor.get('plan') or len(values)!=len(editor['plan']['allocations']):
                raise ValueError('resolve the named expenses, payers and amounts before preview')
            base = editor['plan'].get('input_allocations') or editor['plan']['allocations']
            data={'allocations':[dict(a,amount=n) for a,n in zip(base,values)]}
        if not isinstance(data,dict) or not isinstance(data.get('allocations'),list):
            raise ValueError('allocation reply must identify two venue shares')
        return preview(editor['receipt_id'],data['allocations'],partial=data.get('partial',False))
    out = _issue(editor)
    st['correction']=editor
    O.save_state(st)
    return out


def preview(receipt_id, allocations, partial=False):
    from . import onboarding as O
    if not isinstance(allocations,list):
        raise ValueError('Candidate A requires two explicit venue shares')
    if allocations and all(isinstance(a,dict) and type(a.get('expense_id')) is int and a.get('payer','owner')=='owner' and 'expense' not in a for a in allocations):
        receipt = call('show',receipt_id=int(receipt_id))['receipt']
        expenses = {e['id']:e for e in call('expenses')}
        ids = {a.get('expense_id') for a in allocations}
        if ids <= expenses.keys():
            selected = sum(expenses[sid]['amount'] for sid in ids)
            if selected < receipt['amount'] and not partial:
                return {'refused':True,'card':{'text':t('refusal',received=won(receipt['amount']),selected=won(selected)),
                                             'parse_mode':'HTML','buttons':[]}}
    if len(allocations) not in (1, 2, 3):
        raise ValueError('Candidate A requires one to three explicit venue shares')
    st=O.load_state()
    rev=call('show',receipt_id=int(receipt_id))['revision']
    plan=call('preview',receipt_id=int(receipt_id),revision=rev,operation=uuid.uuid4().hex,allocations=allocations,partial=partial)
    editor=dict(receipt_id=int(receipt_id),revision=rev,mode='candidate',plan=plan)
    out=_issue(editor)
    st['correction']=editor
    O.save_state(st)
    return out


def choose(receipt_id, choice, received):
    """The owner's candidate pick is the decision when its split is known."""
    allocations = choice.get('allocations') or [dict(expense_id=int(choice['value']), amount=received)]
    out = preview(receipt_id, allocations)
    if out.get('refused'):
        raise ValueError(out['card']['text'])
    fit = choice.get('share_fit') or {}
    exact = fit.get('amount') == received * fit.get('denominator', 0)
    if 'question' in out and (len(allocations) == 1 or exact):
        return answer(out['question']['id'], 'confirm')
    return out


def tap(callback):
    from . import onboarding as O
    if (callback or '').startswith('opt:la:edit:'):
        result = O.load_state().get('settlement_result') or {}
        if callback != 'opt:la:edit:' + result.get('token', '') or not result:
            raise ValueError('stale or invalid settlement result')
        if call('show', receipt_id=result['receipt_id'])['revision'] != result['revision']:
            raise ValueError('stale receipt revision: reopen correction')
        return open_editor(result['receipt_id'])
    editor=O.load_state().get('correction')
    match=re.fullmatch(r'opt:la:([0-9a-f]{16}):(\d+)',callback or '')
    if not match or not editor:
        raise ValueError('stale or invalid allocation callback')
    q=editor['question']
    original={k:v for k,v in q.items() if k!='card'}
    if match[1]!=token(original) or int(match[2])>=len(q['choices']):
        raise ValueError('stale or invalid allocation callback')
    return answer(q['id'],q['choices'][int(match[2])]['value'])
