"""Owner-picked settlement cards. Kit verbs own all data; i18n owns all copy.
Each button row contains one action with stable identifiers for chat routing.
"""
import datetime
import html
import json
import importlib.util
import sys
from money_format import comma
from . import kit, paths, text
from item_grouping import quote_items


def _record_notice():
    # The shared record frame lives with the kit core (database/record_notice.py).
    name = '_record_notice'
    mod = sys.modules.get(name)
    if mod is None:
        path = paths.root() / 'database' / 'record_notice.py'
        spec = importlib.util.spec_from_file_location(name, str(path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules[name] = mod
    return mod


def call(action, **data):
    return kit.run('ledger-split', action, '--data', json.dumps(data, ensure_ascii=False))


def esc(s):
    return html.escape(str(s or ''))


def won(n):
    return text.t('ledger.split.money', amount=comma(int(n)))


def name(p):
    return p.get('display_name') or p.get('name') or p.get('person')


def card(key, buttons=(), **kw):
    rows = kw.get('rows')
    if isinstance(rows, list):
        kw['rows'] = quote_items(rows)
    body = text.t('ledger.split.'+key, **kw)
    if isinstance(rows, list):
        body = body.replace('<blockquote>'+kw['rows']+'</blockquote>',kw['rows'])
    return {'text':body,
            'buttons':[[{'text':label,'action':action,'data':data}] for label,action,data in buttons]}


def button(key, action, data, **kw):
    return text.t('ledger.split.'+key, **kw),action,data


def render(s):
    if not s: return None
    sid=s['spend_id']; d={'spend_id':sid}
    ps=s['people']; state=s['state']
    if state == 'skip': return None
    if state == 'ask':
        return card('q1', [button('yes_split','answer',dict(d,answer='split')),button('no_split','answer',dict(d,answer='no'))],
                    merchant=esc(s['merchant']),amount=won(s['amount']),appointment=esc(s['appointment']),names='/'.join(esc(name(p)) for p in ps))
    if state == 'how':
        return card('how',[button('equal','answer',dict(d,answer='equal')),button('different','answer',dict(d,answer='different'))])
    if state == 'declined':
        if len(ps) != 1: return None
        return card('future',[button('always_no','answer',dict(d,answer='always_no')),button('once','answer',dict(d,answer='once'))], name=esc(name(ps[0])))
    eligible=[p for p in ps if not p['ledger_no_split']]
    if state == 'remainder':
        bs=[button('owner','answer',dict(d,answer='remainder',person='owner'))]
        bs += [(name(p),'answer',dict(d,answer='remainder',person=str(p['id']))) for p in eligible]
        return card('remainder',bs,amount=won(s['amount'] % (len(ps)+1)))
    if state == 'amounts':
        amounts=json.loads(s['amounts_json'] or '{}')
        pending=[('owner',text.t('ledger.split.owner'))]+[(str(p['id']),name(p)) for p in eligible]
        who_id,who=next(((k,n) for k,n in pending if k not in amounts),pending[-1])
        out=card('owner_amount' if who_id=='owner' else 'amount',name=esc(who),total=won(s['amount']))
        out['reply']={'action':'answer','data':dict(d,answer='amount',person=who_id),'field':'amount','type':'integer'}
        return out
    return card('allocated',owner=won(s['owner_share']),rows=[text.t('ledger.split.owed',name=esc(name(p)),amount=won(p['remaining'])) for p in s['shares']])


def render_match(m):
    if not m or m['state'] == 'unrelated': return None
    d={'transfer_id':m['transfer_id']}; h=m['share']; t=m['transfer']
    call('transfer-shown',transfer_id=m['transfer_id'])
    if h is None:
        candidates=call('transfer-candidates',transfer_id=m['transfer_id'])
        bs=[(text.t('ledger.split.candidate',appointment=r['appointment'],date=r['date'],amount=won(r['remaining'])),'transfer-answer',dict(d,answer='choose',share_id=r['id'])) for r in candidates]
        bs.append(button('unrelated','transfer-answer',dict(d,answer='unrelated')))
        return card('choose_share',bs,name=esc(t['name']),amount=won(t['amount']))
    if m['state'] in ('matched','confirmed'):
        lines=[]
        for r in m['shares']:
            lines.append(text.t('ledger.split.settled' if not r['remaining'] else 'ledger.split.remaining',name=esc(name(r)),amount=won(r['remaining'])))
        bs=([button('correct','transfer-answer',dict(d,answer='yes'))] if m['state']=='matched' else [])+[button('other_money','transfer-answer',dict(d,answer='unrelated'))]
        return card('matched',bs,
                    name=esc(name(h)),amount=won(t['amount']),appointment=esc(h['appointment']),rows=lines)
    return card('mismatch',[button('finish','transfer-answer',dict(d,answer='finish')),button('diff','transfer-answer',dict(d,answer='remaining'),amount=won(max(0,h['remaining']-t['amount']))),button('unrelated','transfer-answer',dict(d,answer='unrelated'))],
                name=esc(name(h)),amount=won(t['amount']),appointment=esc(h['appointment']),share=won(h['remaining']))


def weekly(today):
    shares=call('weekly',today=str(today))
    if not shares: return None
    d={'today':str(today),'shares':{str(r['id']):r['remaining'] for r in shares}}
    grouped={}
    for r in shares:
        group=grouped.setdefault(r['person_id'],{'name':name(r),'amount':0,'contexts':[]})
        group['amount']+=r['remaining']
        date=datetime.date.fromisoformat(r['date'])
        context=text.t('ledger.split.context',date='%d/%d'%(date.month,date.day),appointment=esc(r['appointment']))
        if context not in group['contexts']: group['contexts'].append(context)
    lines=[text.t('ledger.split.weekly_person',name=esc(g['name']),amount=won(g['amount']),contexts=text.t('ledger.split.context_sep').join(g['contexts'])) for g in grouped.values()]
    return card('weekly',[button('received','weekly-answer',dict(d,answer='received')),button('keep','weekly-answer',dict(d,answer='keep'))],rows=lines,total=won(sum(r['remaining'] for r in shares)))


def _notice_parts(r):
    category = r['category'] or text.t('ledger.split.unclassified')
    key = 'ledger.category.' + category
    if key in text._bundle(text.lang()) or key in text._bundle(text.DEFAULT_LANG):
        category = text.t(key)
    return esc(r['merchant']), won(r['amount']), esc(category)


def notice_line(r):
    """One-line form, for the weekly rollup list only."""
    if not r: return None
    merchant, amount, category = _notice_parts(r)
    return text.t('ledger.split.notice',merchant=merchant,amount=amount,category=category)


def notice(r, question=None):
    """The payment-recorded notice in the shared record frame."""
    if not r: return None
    merchant, amount, category = _notice_parts(r)
    return _record_notice().render(
        text.t('record.notice.payment.title'),
        [(text.t('record.notice.payment.label.merchant'), merchant),
         (text.t('record.notice.payment.label.amount'), amount),
         (text.t('record.notice.payment.label.category'), category)],
        question)


def captured(sid, merchant):
    from . import opaque
    return notice(call('capture',spend_id=sid,clear=not bool(opaque.match(merchant))))


def sweep(since,until):
    out=call('sweep',since=str(since),until=str(until))
    cards=[render(s) for s in out['splits']]+[render_match(m) for m in out['transfers']]
    for r in out['ambiguous']:
        cards.append(card('choose_appointment',[(b['title'],'link',{'spend_id':r['spend_id'],'appointment_id':b['id']}) for b in r['appointments']]))
    return [c for c in cards if c]


def plain(c):
    if not c: return ''
    return '\n'.join([c['text']]+['['+b['text']+']' for row in c['buttons'] for b in row])


def notifications(result):
    """Runtime delivery envelopes. Never sends; keeps each keyboard row singular."""
    out = []
    for item in result.get('items', []):
        if item.get('notice'):
            out.append({'text':item['notice'], 'buttons':[]})
        for c in item.get('settlement', []):
            buttons=[]
            for row in c['buttons']:
                b=row[0]; d=b['data']; action=b['action']
                if action == 'answer':
                    words=['answer',str(d['spend_id']),d['answer']]
                    if 'person' in d: words.append(str(d['person']))
                elif action == 'link':
                    words=['link',str(d['spend_id']),str(d['appointment_id'])]
                elif action == 'transfer-answer':
                    words=['transfer-answer',str(d['transfer_id']),d['answer']]
                    if 'share_id' in d: words.append(str(d['share_id']))
                else:
                    raise ValueError('unsupported capture callback action')
                token='opt:ledger-split '+' '.join(words)
                if len(token.encode('utf-8')) > 64:
                    raise ValueError('settlement callback exceeds Telegram limit')
                buttons.append([{'text':b['text'],'callback_data':token}])
            out.append({'text':c['text'],'buttons':buttons})
    return out


def dispatch_token(token):
    """An opt callback's text -> the same structured service action/data."""
    words=token.removeprefix('opt:').split()
    if not words or words.pop(0) != 'ledger-split':
        raise ValueError('invalid settlement callback')
    if len(words) < 3: raise ValueError('incomplete settlement callback')
    action, identifier, answer=words[:3]
    if action == 'link' and len(words)==3:
        return action, {'spend_id':int(identifier),'appointment_id':int(answer)}
    if action == 'answer':
        data={'spend_id':int(identifier),'answer':answer}
        if len(words)==4: data['person']=words[3]
    elif action == 'transfer-answer':
        data={'transfer_id':int(identifier),'answer':answer}
        if len(words)==4: data['share_id']=int(words[3])
    else: raise ValueError('unknown settlement callback action')
    if len(words)>4: raise ValueError('invalid settlement callback payload')
    return action,data
