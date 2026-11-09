"""Approved 2026-11-04 module roster; one quote per instrument."""
import datetime
import html
from decimal import Decimal
from . import text
from .markup import quote_items

VERSION = 6

PREFIX = 'ledger.onboarding.module_roster.'


def t(key, **kw):
    return text.t(PREFIX+key, **kw)


def variable_note(row):
    reasons = row.get('amount_note') or []
    if isinstance(reasons, str):
        reasons = [reasons]
    return t('variable') + (': '+', '.join(reasons) if reasons else '')


def account_block(row):
    """Owner-confirmed 2026-11-06 09:18 account quote; no extra badges."""
    from . import dashboard, history_flow as F
    esc, money = html.escape, F._man
    block = ['<b>'+esc(row.get('display_name') or dashboard.roster_display(row['name']))+'</b>']
    drains = [d for d in row.get('drains',[]) if d['kind'] in
              ('credit_card','corporate_card','debit_card','easy_pay')]
    if drains:
        block.append(t('connected', names=esc(', '.join(d['name'] for d in drains))))
    salary = next((f for f in row.get('flows',[]) if f['detail']=='salary'), None)
    if salary and salary.get('next_payday'):
        due = datetime.date.fromisoformat(salary['next_payday'])
        dow = text.t('ledger.dow.'+('mon','tue','wed','thu','fri','sat','sun')[due.weekday()])
        rule = t('payday_'+(salary.get('shift') or 'unknown'), day=salary['salary_day'])
        block.append(t('salary_account', rule=rule,
                       date=t('payday_date',month=due.month,day=due.day,dow=dow)))
        lo, hi = salary['lo']/10000, salary['hi']/10000
        amount = format(lo,'.0f')+'~'+format(hi,'.0f') if lo!=hi else format(lo,'.0f')
        if text.t('ledger.money.style') != 'man':
            amount = (F._won(round(salary['lo']))+'~'+F._won(round(salary['hi']))
                      if lo != hi else F._won(round(salary['lo'])))
        note = ' ('+esc(variable_note(salary))+')' if salary.get('variable') else ''
        block.append(t('salary_range', amount=amount)+note)
    entries = [g for g in row.get('modules',[]) if format(g['monthly_amount']/10000,'.1f')!='0.0']
    flows = [f for f in row.get('flows',[]) if f['detail']!='salary' and
             format(f['monthly_amount']/10000,'.1f')!='0.0']
    fixed = [g for g in entries if g.get('fixed',g.get('detail')=='fixed')]+flows
    other = [g for g in entries if g not in fixed]
    fixed.sort(key=lambda g:({'loan':1,'saving':2}.get(g.get('detail'),3 if g.get('token')=='module_transport' else 0),-g['monthly_amount']))
    other.sort(key=lambda g:(g.get('detail')=='other',-g['monthly_amount']))
    for label, groups in (('fixed',fixed),('non_fixed',other)):
        # Add the amounts printed in each locale: one-decimal man in Korean,
        # whole won in English. Keep exact won values in the source data.
        total = sum((Decimal(format(g['monthly_amount']/10000,'.1f')) for g in groups), Decimal(0))*10000
        if text.t('ledger.money.style') != 'man':
            total = sum(round(g['monthly_amount']) for g in groups)
        block.append('• <b>'+t(label)+': '+money(total)+'</b>')
        for g in groups:
            token, detail = g.get('token'), g.get('detail')
            if detail=='loan':
                n = g['number']
                name = t('loan', number=chr(0x2460+n-1) if 1<=n<=20 else str(n))
            elif detail=='saving':
                name = t('automatic_transfer')
            else:
                name = text.t('module.'+token+'.name') if token else (
                    text.t('ledger.onboarding.reduce.unknown') if detail=='unknown' else t('other'))
            examples = list(g.get('examples',[]))
            if token=='module_transport' and not g.get('fixed'):
                examples = [n+' '+t('taxi') if '카카오모빌리티' in n and '택시' not in n else n for n in examples]
            if g.get('variable'):
                examples.append(variable_note(g))
            description = money(g['monthly_amount'])
            if examples:
                description += ' ('+esc(', '.join(examples))+')'
            block.append(' └ '+esc(name)+': '+description)
    if row.get('balance_guard'):
        names = t('guard_join').join(row.get('guard_names',[]))
        guard = t('guard_named',names=esc(names)) if names else t('guard')
        block.append(guard.removeprefix('• '))
    return '<blockquote>'+'\n'.join(block)+'</blockquote>'


def render(roster):
    from . import history_flow as F
    esc = html.escape
    money = F._man
    lines = [t('title')]
    instruments = roster['instruments']
    for group, emoji, predicate in (
            ('credit','💳',lambda r:r['kind'] in ('credit_card','corporate_card')),
            ('debit','💳',lambda r:r['kind']=='debit_card'),
            ('wallet','📱',lambda r:r['kind']=='easy_pay'),
            ('account','🏦',lambda r:r['kind']=='account'),
            ('saving','💰',lambda r:r['kind'] in ('savings','securities'))):
        rows = [r for r in instruments if predicate(r)]
        if not rows:
            continue
        lines += ['', emoji+' <b>'+t(group)+'</b>']
        blocks = []
        for row in rows:
            if group=='account':
                lines.append(account_block(row))
                continue
            head = '<b>'+esc(row.get('display_name',row['name']))+'</b>'
            if group in ('credit','debit','wallet'):
                if not row.get('count',row['monthly_count']*3):
                    head += t('unused')
                elif format(row['monthly_amount']/10000, '.1f')!='0.0':
                    head += t('average',count=text.monthly_count(row['monthly_count']),amount=money(row['monthly_amount']))
            if group in ('account','saving') and row.get('activity_count')==0 and not row.get('modules'):
                head += t('unused')
            block = [head]
            drains = row.get('drains',[])
            if drains:
                block.append(t('drains',names=esc(', '.join(d['name'] for d in drains))))
            entries = row.get('modules',[])
            if row.get('flows'):
                entries = sorted([*entries, *row['flows']],
                                 key=lambda r:(r.get('detail')!='salary', -r.get('amount',r['monthly_amount']*3)))
            entries = sorted(entries, key=lambda r:r.get('detail')=='other')
            for group_row in entries:
                token = group_row.get('token')
                detail = group_row.get('detail')
                if detail=='salary':
                    block.append('• '+t('salary',day=group_row['salary_day']))
                    continue
                # Match the one-decimal money formatter: tiny positive flows
                # must not turn into a visible zero line.
                if format(group_row['monthly_amount']/10000, '.1f')=='0.0':
                    continue
                if detail=='loan':
                    n = group_row['number']
                    number = chr(0x2460+n-1) if 1<=n<=20 else str(n)
                    name = t('loan',number=number)
                elif detail=='saving':
                    name = t('saving_flow')
                else:
                    name = text.t('module.'+token+'.name') if token else (
                        text.t('ledger.onboarding.reduce.unknown') if detail=='unknown' else
                        t('other') if detail=='other' else group_row['name'])
                description = money(group_row['monthly_amount'])
                examples = ', '.join(esc(n) for n in group_row.get('examples',[]))
                if examples and (token or detail in ('unknown','other')):
                    description += ' ('+examples+')'
                if token in ('module_admin','module_work','module_business') or detail=='loan':
                    description += ' → '+t('fixed')
                block.append('• '+esc(name)+': '+description)
            if row.get('balance_guard'):
                names = t('guard_join').join(row.get('guard_names',[]))
                block.append(t('guard_named',names=esc(names)) if names else t('guard'))
            count = row.get('sparse_count',0)
            if 0<count<=12 and (row.get('sources') or drains):
                block.append(t('sparse',name=esc(row['name']),n=count))
            sources = row.get('sources',[])
            if sources:
                block.append(t('card_source' if group=='credit' else 'source',names=esc(', '.join(s['name'] for s in sources))))
            blocks.append('\n'.join(block))
        lines.append(quote_items(blocks))
    return '\n'.join(lines)


def legacy_rows(rows):
    """Old in-memory fixtures have no module evidence; never reuse role guesses."""
    return dict(instruments=[dict(r, count=r.get('count',r['monthly_count']*3), modules=r.get('modules',[]))
                             for r in rows], omitted=[])
