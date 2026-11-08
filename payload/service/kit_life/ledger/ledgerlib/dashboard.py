# -*- coding: utf-8 -*-
"""Approved month-flow card. Copy and layout tokens live in i18n.

The example is explicitly labelled. The real card uses the kit diagnosis;
existing installment charges are displayed separately, never planned here.
"""
import datetime
import html
import re
import sys
from pathlib import Path

# Direct service entry points can import this renderer before onboarding.
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from money_format import decimal_man, float_whole_man
from . import diagtext, text
from .markup import quote_card, quote_items
from .whole_block import block as whole_block, lines as whole_block_lines
from service.kit_life import bar_inline, group

V = 'ledger.onboarding.v6.dashboard.'


def t(key, **kw):
    return text.t(V + key, **kw)


def _name(row):
    name = row.get('display_name', row.get('name'))
    if name:
        return name
    kind = row.get('class', 'other')
    if kind == 'income':
        return t('income')
    if kind not in diagtext.CLASS_ORDER:
        kind = 'other'
    return text.t('ledger.diag.class.' + kind)


def _members(rows, installment=False):
    rows = [dict(r, name=_name(r)) for r in rows]
    rows = sorted((r for r in rows if r.get('name') and r['amount']),
                  key=lambda r: -r['amount'])
    from . import end_dates
    names = [t('installment.note', name=end_dates.label(html.escape(r.get('display_name', r['name'])),r.get('end')), k=r['n'], n=r['months'])
             if installment else end_dates.label(html.escape(r.get('display_name', r['name'])),r.get('ends_on')) for r in rows[:3]]
    items = t('items.sep').join(names)
    if len(rows) > 3:
        items += t('items.more', n=len(rows) - 3)
    return t('note', note=items) if items else ''


def _row(label, amount, members='', bold=False, deduction=False):
    value = diagtext.money(abs(amount))
    if deduction or amount < 0:
        value = t('minus', amount=value)
    return t('row', label=t('bold', value=label) if bold else label, note=members,
             amount=t('bold', value=value) if bold else value)


def available(income, fixed, saving, installments=()):
    return income - fixed - saving - sum(row['this_month'] for row in installments)


def _card(income, fixed, saving, installments=(), income_rows=(), fixed_rows=(), saving_rows=(), example=False):
    lines = []
    for kind, amount, rows, bold in (('income', income, income_rows, True),
                                     ('fixed', fixed, fixed_rows, False),
                                     ('saving', saving, saving_rows, False)):
        members = _members(rows)
        if amount or members:
            lines.append(_row(t(kind), amount, members, bold=bold, deduction=not bold))
    active = [dict(row, amount=row['this_month']) for row in installments if row['this_month']]
    charges = sum(row['amount'] for row in active)
    if active:
        lines.append(_row(t('installment'), charges, _members(active, installment=True), deduction=True))
    budget = available(income, fixed, saving, installments)
    lines += [t('separator'), _row(t('available'), budget, bold=True)]
    return t('head') + (t('example') if example else '') + '\n<blockquote>' + '\n'.join(lines) + '</blockquote>', budget


@quote_card
def card(*args, **kw):
    return _card(*args, **kw)[0]


def example():
    def rows(kind, amounts):
        return [dict(name=t('example.' + kind + '.' + str(n)), amount=amount)
                for n, amount in enumerate(amounts, 1)]

    return card(3200000, 1000000, 500000,
                installments=[dict(name=t('example.installment'), this_month=200000, n=3, months=6)],
                income_rows=rows('income', [3000000, 200000]),
                fixed_rows=rows('fixed', [700000, 100000, 80000, 60000, 40000, 20000]),
                saving_rows=rows('saving', [300000, 200000]), example=True)


def _parts(d):
    income = d['income']['total']
    fixed = sum(r['amount'] for r in d['out']['regular_lines'] if r['class'] != 'saving')
    return income, fixed, d['out']['saved']


def budget_amount(d, installments=()):
    """The month's spendable money the card proposes as the budget (0 when there is no income to budget from)."""
    if 'review' in d:
        r = d['review']['totals']
        return max(0, available(r['income'], r['monthly_fixed'], r['saving'], installments)) if r['income'] else 0
    income, fixed, saving = _parts(d)
    return available(income, fixed, saving, installments) if income else 0


@quote_card
def render(d, installments=(), period=None):
    income, fixed, saving = _parts(d)
    candidates = [r for r in d['out']['regular_lines']
                  if r['class'] == 'subscription' and r.get('keep_class') in ('droppable', 'reducible')]
    rendered, budget = _card(income, fixed, saving, installments,
                income_rows=d['income'].get('regular_lines', []) + d['income'].get('other_members', d['income'].get('other_lines', [])),
                fixed_rows=[r for r in d['out']['regular_lines'] if r['class'] != 'saving'],
                saving_rows=[r for r in d['out']['regular_lines'] if r['class'] == 'saving'] + d['out'].get('saved_lines', []))
    basis = t('basis', month=d['month'])
    if period:
        basis = t('basis.' + period['kind'], month=d['month'], since=period['since'], until=period['until'])
    out = [rendered, basis]
    if candidates:
        out += ['', t('subscriptions.head')]
        out += [quote_items(t('subscriptions.row', name='<b>'+html.escape(_name(r))+'</b>',
                                   amount=diagtext.money(r['amount'])) for r in candidates)]
        out += [t('subscriptions.check')]
    else:
        out += ['', t('subscriptions.head'), whole_block(t('subscriptions.none'))]
    proposal = t('budget.propose', amount=diagtext.money(budget)) if income else t('budget.no_income')
    out += ['', t('budget.head'), whole_block(proposal)]
    return '\n'.join(out)


R = 'ledger.onboarding.v6.review.'
QUOTE = '> '


def rt(key, **kw):
    return text.t(R + key, **kw)


def fixed_pay_tag(item):
    mode = item.get('pay_mode')
    if mode not in ('auto', 'manual'):
        return ''
    label = rt('pay_mode.' + mode)
    if item.get('pay_mode_source') == 'inferred':
        label += rt('pay_mode.inferred')
    return '  ' + label


@quote_card
def review_screen(d, topic, item=None, today=None):
    """Owner layout: title/explanation, grouped numeric quotes, then questions."""
    if topic == 'aim':
        return aim_result(d)
    if topic == 'nature':
        return spend_nature(d)
    if topic == 'fixed' and d.get('spend_profile'):
        return fixed_summary(d)
    if topic == 'ending' and d.get('history'):
        return history_card(d['history'])
    r = d['review']
    if topic in ('roster', 'living', 'cards', 'card'):
        return picked_screen(d, topic, item, today=today)
    total = r['totals']
    lines, questions = [], []

    def row(name, amount):
        return rt('row', name=html.escape(name), amount=diagtext.money(amount))

    def member(v):
        return rt('item', date=question_date(v['date'], today, short=False), name=html.escape(v['display_name']), amount=diagtext.money(v['amount']))

    def details(items, interest=False):
        # Dust rows (< DUST_KRW) are listed nowhere; the section total still sums them.
        shown = [v for v in items if not v.get('dust')]
        small = [v for v in shown if interest and v.get('small_interest')]
        for v in shown:
            if v not in small:
                value = member(v)
                # item token includes the primary bullet; subbullet owns indentation.
                yield rt('subbullet', value=value.removeprefix('• '))
        if small:
            yield rt('subbullet', value=rt('income.interest_small', n=len(small), amount=diagtext.money(sum(v['amount'] for v in small))))

    if topic == 'income':
        heading = lambda name, amount: '<b>'+rt('row.plain',name=name,amount=diagtext.money(amount))+'</b>'
        lines.append(heading(rt('total.income'), total['income']))
        for key in ('salary', 'received', 'other'):
            items = r['income'][key]
            if not items:
                continue
            lines.append(heading(rt('income.' + key), sum(v['amount'] for v in items)))
            lines.extend(details(items, interest=key == 'other'))
        questions.append(rt('confirm'))
    elif topic == 'person':
        lines.append(member(item))
        questions.append(rt('person.ask', name=html.escape(item['display_name'])))
    elif topic == 'pair':
        lines.append(member(item))
        questions.append(rt('pair.ask' if r['pair_candidates'].get(str(item['id'])) else 'pair.empty'))
    elif topic == 'fixed':
        # Owner 2026-11-02: category headings outside; one quote per payment.
        def block(name, amount, items):
            return '\n'.join(['<b>'+rt('row.plain', name=name, amount=diagtext.money(amount))+'</b>'] +
                             ['<blockquote>• '+rt('item.plain', date=question_date(v['date'], today, short=False), name=html.escape(v['display_name']), amount=diagtext.money(v['amount']))+fixed_pay_tag(v)+'</blockquote>'
                              for v in items if not v.get('dust')])
        lines.append(rt('row.plain', name=rt('total.fixed'), amount=diagtext.money(total.get('monthly_fixed',total['fixed']))))
        for key in ('housing', 'tax', 'subscription', 'telecom', 'insurance', 'utility', 'other'):
            items = r['fixed'][key] + (r['fixed']['loan'] if key == 'housing' else [])
            amount = sum(round(v['amount']/v.get('cycle_months',1)) for v in items)
            if not amount:
                continue
            lines.append(block(rt('fixed.' + key), amount, items))
        if r['saving']:
            lines.append(block(rt('total.saving'), total['saving'], r['saving']))
        return '\n'.join([rt('title.fixed', month=d['month']), rt('explain.fixed'), ''] +
                          ['\n\n'.join(lines), '', rt('confirm')])
    elif topic == 'odd':
        if item.get('owner_class'):
            lines.append(row(rt('fixed.' + item['owner_class']), sum(v['amount'] for v in item.get('items', [item]))))
        lines.append(row(item['display_name'], sum(v['amount'] for v in item.get('items', [item]))))
        lines.extend(details(item.get('items', [item])))
        questions.append(rt('odd.' + item['odd']))
    elif topic == 'refund':
        lines.append(row(item['display_name'], sum(v['amount'] for v in item.get('items', [item]))))
        lines.extend(details(item.get('items', [item])))
        questions.append(rt('refund.ask'))
    elif topic == 'ending':
        lines.append(rt('equation', **{k: diagtext.money(v) for k, v in total.items()}))
        if r['converted']:
            conversions = ' / '.join(rt('conversion', name=html.escape(v['display_name']), amount=diagtext.money(v['amount']),
                                         months=v['cycle_months'], monthly=diagtext.money(v['monthly_amount'])) for v in r['converted'])
            lines.append(rt('monthly_fixed', amount=diagtext.money(total['monthly_fixed']), conversions=conversions))
        lines.append(row(rt('usual'), total['usual']))
    if topic == 'ending':
        allocations = r.get('receipt_allocations', [])
        counts = {}
        for a in allocations:
            counts[a['receipt_id']] = counts.get(a['receipt_id'], 0) + 1
        seen = set()
        for a in allocations:
            # Single expenses already show their net cost in category rows.
            # Keep the additional venue breakdown for grouped settlements.
            if counts[a['receipt_id']] < 2 or a['expense_id'] in seen:
                continue
            seen.add(a['expense_id'])
            lines += [rt('bullet', value=text.t('ledger.allocation.expense', name=html.escape(a.get('display_name', a['name'])), amount=format(a['payment'], ','))),
                      rt('bullet', value=text.t('ledger.allocation.owner', amount=format(a['owner_cost'], ',')))]
    if topic in ('income', 'ending'):
        for receipt in r.get('unallocated_receipts', []):
            lines.append(rt('bullet', value=text.t('ledger.split.remaining', name=html.escape(receipt['name']),
                                amount=text.t('ledger.split.money', amount=format(receipt['unallocated'], ',')))))
    return '\n'.join([rt('title.' + topic, month=d['month']), rt('explain.' + topic),
                      whole_block_lines(lines)] + questions)


def generic_account_name(name):
    # Importers may append a row identifier to an otherwise unnamed account.
    bare = re.sub(r'\s*\(#\d+\)\s*$', '', name).strip()
    names = {n.casefold() for n in rt('roster.generic_names').split('|')}
    return bare if bare.casefold() in names else None


def roster_display(name):
    generic = generic_account_name(name)
    if generic is not None:
        return rt('roster.bank_unknown', name=generic)
    return rt('roster.ibk.display') if name == rt('roster.ibk.raw') else name


def topic_particle(name):
    """Korean topic marker, computed before escaping the displayed name."""
    last = name.rstrip()[-1:] or ' '
    return '은' if '가' <= last <= '힣' and (ord(last) - ord('가')) % 28 else '는'


@quote_card
def funding_screen(target, choices, wallet=False):
    display = roster_display(target.get('display_name', target['name']))
    name = html.escape(display)
    blocks = [rt('funding.ask', wallet=name, topic=topic_particle(display)) if wallet else
              rt('roster.ask', name=name)]
    for kind in ('card', 'account'):
        items = [(n, c) for n, c in enumerate(choices, 1) if c.get('source_kind') == kind]
        if items:
            blocks.append(('💳 ' if kind == 'card' else '🏦 ') + '<b>' + rt('funding.' + kind) + '</b>')
            blocks.append(whole_block_lines('%d. %s' % (n, html.escape(c['label']))
                                            for n, c in items))
    blocks.append(rt('funding.reply' if wallet else 'funding.reply.single'))
    return '\n'.join(blocks)


@quote_card
def purpose_screen(items):
    blocks = [rt('purpose.title', n=len(items))]
    for layer in ('wallet', 'card', 'account'):
        members = [(n, m) for n, m in enumerate(items, 1) if m['layer'] == layer]
        if members:
            blocks.append(rt('purpose.group.' + layer))
            blocks.append(whole_block_lines('%d. %s' %
                          (n, html.escape(roster_display(m['name']))) for n, m in members))
    blocks.append(rt('purpose.explain'))
    return '\n'.join(blocks)


def purpose_question(item, index, total, today=None):
    tags = ('월급·고정비', '생활비', '저축', '사업', '안 따짐')
    actions = [rt('purpose.tag.' + str(tags.index(tag))) for tag in item['guess']['tags']]
    purposes = rt('purpose.action_sep').join(actions)
    if text.lang() == 'ko' and len(actions) > 1:
        purposes = ', '.join(actions[:-1]) + ('과 ' if actions[-2] in ('저축', '사업') else '와 ') + actions[-1]
    guess = rt('purpose.guess', actions=purposes)
    lines = []
    for share in item['guess']['shares']:
        tag = share['tag']
        if tag not in item['guess']['tags']:
            continue
        lines.append('<b>' + rt('purpose.tag.' + str(tags.index(tag))) + '</b> ' +
                     str(int(share['share'] * 100 + 0.5)) + '%')
        for row in (r for r in item['recent'] if r['purpose'] == tag):
            date = datetime.date.fromisoformat(row['date'])
            amount = -row['amount'] if row['kind'] == 'expense' else row['amount']
            evidence = rt('purpose.evidence', date=question_date(date, today),
                          name=html.escape(row.get('display_name', row['name'])), sign='−' if amount < 0 else '+',
                          amount=diagtext.money(abs(amount)))
            category = row.get('src_cat') or ''
            if category.startswith('이체/') or row['kind'] == 'transfer':
                evidence += ' (' + rt('purpose.transfer') + ')'
            elif row.get('wallet_merchant'):
                sub = category.rsplit('/', 1)[-1]
                if sub:
                    evidence += ' (' + html.escape(sub) + ')'
            for key in ('via_wallet_name', 'via_card_name'):
                if row.get(key):
                    evidence += ' (' + html.escape(row[key]) + ')'
                    break
            lines.append(whole_block(evidence))
    return rt('purpose.question', name=html.escape(roster_display(item['name'])), i=index, n=total) + \
           '\n' + '\n'.join(lines) + '\n' + guess


@quote_card
def picked_screen(d, topic, item=None, today=None):
    """Owner-selected grouping, with no diagnostic detail below the proposal."""
    r = d['review']
    bullet = lambda value: rt('bullet', value=value)
    sub = lambda value: rt('cards.subbullet', value=value)
    quote = whole_block_lines
    month = int(d['month'][5:])
    if topic == 'roster':
        names = {m['name']: m.get('display_name', m['name'])
                 for methods in r['roster']['groups'].values() for m in methods}
        def display(name):
            return html.escape(roster_display(names.get(name, name)))
        blocks = []
        number = 0
        for group in ('card', 'account', 'saving', 'wallet'):
            methods = r['roster']['groups'][group]
            if not methods:
                continue
            blocks.append(rt('roster.' + group, n=len(methods)))
            lines = []
            for method in methods:
                name = display(method.get('display_name', method['name']))
                mains = {edge['source'] for edge in r['roster'].get('funding', [])
                         if edge['wallet'] == method['name'] and edge['main']}
                sources = ', '.join(rt('funding.primary', name=display(link['source']))
                                    if link['source'] in mains else display(link['source']) for link in r['roster']['links']
                                    if link['wallet'] == method['name'])
                if sources:
                    key = 'roster.paying' if method['kind'] in ('credit_card', 'corporate_card') else 'roster.funding'
                    name = rt(key, name=name, sources=sources)
                elif method['kind'] == 'easy_pay' and method['id'] in r['roster'].get('funding_questions', []):
                    name = rt('roster.unlinked', name=name)
                elif method['kind'] != 'easy_pay' and any(item['id'] == method['id'] for item in r['roster'].get('unresolved', [])):
                    name = rt('roster.unlinked_account', name=name)
                clue = r['roster'].get('evidence', {}).get(str(method['id']))
                if clue:
                    name += rt('roster.evidence', clue=html.escape(clue))
                number += 1
                lines.append('%d. %s' % (number, name))
            blocks.append(quote(lines))
        return '\n'.join([rt('title.roster', n=number), rt('explain.roster'), ''] +
                         blocks + [rt('reply.roster')])
    if topic == 'living':
        proposal = r['living_proposal']
        blocks = [rt('living.head', amount=diagtext.money(proposal['usual'])),
                  rt('living.basis', first=int(proposal['months'][0][5:]), month=month,
                     amount=diagtext.money(r['totals']['living']))]
        for key in ('one_off', 'subscriptions'):
            if proposal[key]:
                lines = [rt('living.propose.' + key)]
                for row in proposal[key]:
                    date = datetime.date.fromisoformat(row['date'])
                    lines.append(bullet(rt('living.purchase', date=question_date(date, today),
                                        name=html.escape(row['display_name']), amount=diagtext.money(row['amount']))))
                blocks.append(quote(lines))
        blocks.extend(monthly_cap_card(cap) for cap in r.get('monthly_caps', []))
        return '\n'.join(blocks)
    if topic == 'cards':
        refunds = ['<b>'+rt('row', name=rt('refunds'), amount=diagtext.money(r['refunds']))+'</b>'] if r['refunds'] else []
        for row in r.get('refund_items', []):
            refunds.append(bullet(rt('returned.item', date=question_date(row['date'], today, short=False), name=html.escape(row['display_name']),
                                  method=html.escape(row.get('method') or ''), amount=diagtext.money(abs(row['amount'])))))
        for row in r.get('unverified_refunds', []):
            refunds += [bullet(rt('refund.unverified')), sub(rt('returned.item', date=question_date(row['date'], today, short=False),
                        name=html.escape(row['display_name']), method=html.escape(row.get('method') or ''),
                        amount=diagtext.money(row['amount'])))]
        for claim in r.get('insurance', []):
            if not claim.get('counted'):
                refunds += [bullet(rt('insurance.label') + ' / ' + diagtext.money(claim['amount'])),
                            sub(rt('insurance.not_linked' if claim.get('receipt_id') else 'insurance.not_counted'))]
        for receipt in r.get('insurance_receipts', []):
            refunds += ['<b>'+rt('insurance.receipt')+'</b>', bullet(rt('returned.item', date=question_date(receipt['date'], today, short=False),
                        name=html.escape(receipt.get('display_name') or receipt['name']),
                        method=html.escape(receipt.get('method') or ''), amount=diagtext.money(receipt['amount'])))]
        blocks = [rt('title.cards', month=month), rt('explain.cards')]
        gaps = []
        for gap in r['gaps']:
            signed = ('+' if gap['gap'] >= 0 else '-') + diagtext.money(abs(gap['gap']))
            gaps += [bullet(html.escape(gap['card'])), sub(rt('cards.bill', amount=diagtext.money(gap['bill']))),
                     sub(rt('cards.usage', month=int(gap['prior'][5:]), amount=diagtext.money(gap['usage']))),
                     sub(rt('cards.difference', amount=signed))]
        if gaps:
            blocks.append(quote([rt('cards.differences')] + gaps))
        if refunds:
            blocks.append(rt('cards.returned')+'\n'+'\n'.join(refunds))
        return '\n'.join(blocks)
    end = datetime.date.fromisoformat(item['period_end']) - datetime.timedelta(days=1)
    start = datetime.date.fromisoformat(item['period_start'])
    installments = sum(v['this_month'] for v in item['installments'])
    lines = [bullet(rt('cards.month_bill', month=month, amount=diagtext.money(item['bill']))),
             bullet(rt('cards.usage', month=int(item['prior'][5:]), amount=diagtext.money(item['usage']))),
             sub(rt('cards.period', start=question_date(start, today), end=question_date(end, today))),
             bullet(rt('cards.known_installments', amount=diagtext.money(sum(v['this_month'] for v in item['installments'])))),
             bullet(rt('cards.residual', amount=diagtext.money(item['gap'])))]
    return '\n'.join([rt('title.card', card=html.escape(item['card']), month=month),
                       quote(lines), rt('cards.causes', installment=diagtext.money(installments), residual=diagtext.money(item['gap']))] + [
                           '%d. %s / %s / %s' % (n, html.escape(v['display_name']), question_date(v['date'], today, short=False), diagtext.money(v['amount']))
                           for n, v in enumerate(item.get('purchases', []), 1)])


@quote_card
def history_card(history):
    """Actual monthly spending and savings, with the owner's compact averages."""
    def number(n):
        return float_whole_man(n)
    def months(key):
        return rt('history.sep').join(rt('history.month', month=int(row['month'][5:]), amount=number(row.get(key,0)))
                                      for row in history)
    lines = []
    for key in ('income', 'out', 'saved'):
        if len(history) == 1:
            # Owner pick 2026-10-24: one month has no average; show the month value inline.
            lines.append(rt('history.row_one', label=rt('history.' + key), values=months(key)))
            if key == 'out':
                for part in ('regular', 'variable'):
                    lines.append(rt('subbullet', value=rt('history.part', label=rt('history.' + part), values=months(part))))
            continue
        average = sum(row.get(key,0) for row in history) / len(history)
        lines.append(rt('history.row', label=rt('history.' + key), amount=number(average)))
        if key == 'out':
            for part in ('regular', 'variable'):
                lines.append(rt('subbullet', value=rt('history.part', label=rt('history.' + part), values=months(part))))
        else:
            lines.append(rt('subbullet', value=months(key)))
    if any('own_inflow' in row for row in history):
        lines.append(rt('history.row', label=rt('history.own_inflow'),
                        amount=number(sum(row.get('own_inflow', 0) for row in history) / len(history))))
        lines.append(rt('subbullet', value=months('own_inflow')))
    if any(row['out'] > row['income'] for row in history):
        lines.append(rt('history.shortfall'))
    return '\n'.join([rt('history.title', n=len(history)), rt('history.explain'),
                       whole_block_lines(lines)])


def man_amount(amount):
    """Exact ten-thousand-won units, without rounding away a shortfall."""
    return decimal_man(amount)


def progress_bar(spent, target, nearest=False):
    cells = max(0, min(10, (spent * 10 + (target // 2 if nearest else 0)) // target)) if target else 10
    return text.t('ledger.progress.filled') * cells + text.t('ledger.progress.empty') * (10 - cells)


def monthly_cap_card(cap):
    difference = cap['target'] - cap['actual']
    return text.t('ledger.monthly_cap.card', bar=progress_bar(cap['actual'], cap['target']),
                  spent=man_amount(cap['actual']), target=man_amount(cap['target'])) + '\n' + text.t(
                      'ledger.monthly_cap.over' if difference < 0 else 'ledger.monthly_cap.left',
                      amount=man_amount(abs(difference)))


def question_date(value, today=None, short=True):
    try:
        day = datetime.date.fromisoformat(str(value)[:10])
    except ValueError:
        return str(value)
    current = datetime.date.fromisoformat(str(today)[:10]) if today else datetime.date.today()
    if day.year != current.year:
        return text.t('ledger.question_date.old', y=day.year, m=day.month, d=day.day)
    return '%d/%d' % (day.month, day.day) if short else str(value)


def unmatched_deposit_question(deposit, today=None):
    sender = deposit.get('counterparty')
    sender = text.t('ledger.unmatched_deposit.sender', name=html.escape(sender)) if sender else text.t('ledger.unmatched_deposit.sender_missing')
    return text.t('ledger.unmatched_deposit.question', date=question_date(deposit['date'], today),
                  amount=diagtext.money(deposit['amount']), sender=sender, account=(text.t('ledger.unmatched_deposit.account', account=html.escape(deposit['method'])) if deposit.get('method') else ''))


# Keep existing callers on the approved renderers.
monthly_cap_draft = monthly_cap_card
unmatched_deposit_draft = unmatched_deposit_question


def _profile_values(profile, row):
    return ', '.join(rt('profile.month', month=int(ym[5:]), amount=diagtext.money(value))
                     for ym, value in zip(profile['months'], row['values']))


def aim_result(d):
    """The real counterpart of the opening habit preview, from kit numbers."""
    profile = d['spend_profile']
    lines = [rt('profile.aim'), rt('profile.basis', start=profile['months'][0], end=profile['months'][-1])]
    if not profile['complete']:
        lines.append(rt('profile.incomplete'))
    rows = []
    for row in profile['categories']:
        name = html.escape(row.get('display_name', row['name'])) + (rt('profile.fixed_like') if row['fixed_like'] else '')
        bar = bar_inline('█' * row['bar_units'] + '░' * (10 - row['bar_units']))
        rows.append(rt('profile.share', name=name, bar=bar, share=row['share'],
                       amount=diagtext.money(row['monthly'])))
    if rows:
        lines += [rt('profile.aim_heading'), quote_items(rows)]
    if not profile['categories']:
        lines.append(rt('profile.empty'))
    lines.append(rt('profile.excluded'))
    return '\n'.join(lines)


def spend_nature(d):
    profile = d['spend_profile']
    lines = [rt('profile.nature'), rt('profile.basis', start=profile['months'][0], end=profile['months'][-1])]
    if not profile['complete']:
        lines.append(rt('profile.incomplete'))
    for group in ('fixed', 'similar', 'varying'):
        lines.append(rt('profile.group.' + group))
        items = profile['fixed'] if group == 'fixed' else [r for r in profile['categories'] if r['group'] == group]
        if not items:
            lines.append(rt('profile.none'))
        blocks = []
        for row in items:
            from . import end_dates
            name = end_dates.label(html.escape(row.get('display_name', row['name'])),row.get('ends_on'))
            if row.get('unresolved'):
                name += rt('profile.unresolved_mark')
            elif row.get('fixed_like'):
                name += rt('profile.fixed_like')
            body = rt('profile.item_fixed' if group == 'fixed' else 'profile.item', name=name, amount=diagtext.money(row['monthly']),
                      values=_profile_values(profile, row))
            if group == 'fixed':
                body += '\n' + rt('profile.terms', amount=diagtext.money(row['amount']),
                                      cycle=rt('profile.cycle.' + row['cycle']) if row['cycle'] in ('monthly', 'bimonthly', 'quarterly', 'semiannual', 'yearly') else rt('profile.cycle.unknown'), monthly=diagtext.money(row['monthly']))
                if row['variable']:
                    body += '\n' + rt('profile.variable')
            else:
                body += '\n' + rt('profile.budget', amount=diagtext.money(row['budget_start']))
            blocks.append(body)
        if blocks:
            lines.append(quote_items(blocks))
    return '\n'.join(lines)


def fixed_summary(d):
    from . import end_dates
    profile = d['spend_profile']
    installments=[item for item in profile.get('installments',[]) if item['this_month']] if end_dates.enabled() else []
    confirmed=profile['confirmed_monthly']+sum(item['this_month'] for item in installments)
    lines = [rt('profile.fixed_title'), rt('profile.confirmed', amount=diagtext.money(confirmed))]
    blocks = []
    for row in profile['fixed']:
        if row['unresolved']:
            continue
        body = end_dates.label(html.escape(row.get('display_name', row['name'])),row.get('ends_on')) + '\n' + rt('profile.terms', amount=diagtext.money(row['amount']),
                cycle=rt('profile.cycle.' + row['cycle']) if row['cycle'] in ('monthly', 'bimonthly', 'quarterly', 'semiannual', 'yearly') else rt('profile.cycle.unknown'), monthly=diagtext.money(row['monthly']))
        if row['variable']:
            body += '\n' + rt('profile.variable')
        blocks.append(body)
    for item in installments:
        body=end_dates.label(html.escape(item.get('display_name', item['name'])),item['end'])+'\n'+rt('profile.terms',amount=diagtext.money(item['this_month']),cycle=rt('profile.cycle.monthly'),monthly=diagtext.money(item['this_month']))
        blocks.append(body)
    if blocks:
        lines.append(quote_items(blocks))
    lines.append(rt('profile.historical', month=profile['months'][-1], amount=diagtext.money(profile['historical_fixed'])))
    blocks = []
    for row in profile['fixed']:
        if row['unresolved']:
            body = rt('profile.unresolved', name=end_dates.label(html.escape(row.get('display_name', row['name'])),row.get('ends_on')))
            for charge in [v for v in row['charges'] if v['date'].startswith(profile['months'][-1])]:
                body += '\n' + rt('profile.charge', date=charge['date'], name=html.escape(charge.get('display_name', charge['name'])),
                                    amount=diagtext.money(charge['amount']))
            blocks.append(body)
    if blocks:
        lines.append(quote_items(blocks))
    return '\n'.join(lines)


def completion(d, flow, state):
    """One standalone bubble; all pending facts precede the final usage line."""
    lines = [rt('closing.title'), fixed_summary(d), rt('closing.from_now'), rt('closing.weekly'),
             rt('closing.send'), rt('closing.auto_on' if state.get('auto') else 'closing.auto_off'),
             rt('closing.schedule')]
    pending = []
    for key, answer in flow.get('answers', {}).items():
        if answer.get('status') == 'unresolved' and answer.get('card'):
            # Older saved answers may contain a rendered card instead of its
            # identity. Recover the identity from the original review item.
            card = answer['card']
            if isinstance(card, dict):
                card = flow.get('questions', {}).get(key, {}).get('item', {}).get('card')
                if not card:
                    index = int(key.rsplit('_', 1)[1])
                    card = flow['review_card_gaps'][index]['card']
            pending.append(rt('closing.card', card=html.escape(card), amount=diagtext.money(answer['residual'])))
        elif key.startswith('review_refund_') and answer.get('ok'):
            if any(r['id'] == flow['questions'].get(key, {}).get('item', {}).get('id')
                   for r in d['review'].get('unverified_refunds', [])):
                pending.append(rt('closing.deposit'))
        elif key.startswith('review_funding_') and answer.get('skipped'):
            pending.append(rt('closing.funding', name=html.escape(flow['questions'].get(key, {}).get('target', {}).get('name', key))))
        elif key.startswith(('review_person_', 'review_bank_')) and answer.get('skipped'):
            question = flow['questions'].get(key, {})
            item = question.get('item') or question.get('target') or {}
            pending.append(html.escape(item.get('display_name') or item.get('name') or key))
    if flow.get('skip_remaining_received'):
        pending.append(rt('closing.received'))
    if state.get('backfill', {}).get('pending'):
        pending.append(rt('closing.backfill'))
    lines += [rt('closing.pending'), '\n'.join('• ' + value for value in pending) or rt('profile.none'),
              rt('closing.deferred'), rt('closing.later'), rt('closing.usage')]
    from .markup import onboarding_quotes, quote_lists
    return onboarding_quotes(quote_lists('\n'.join(lines)), qid='completion')
