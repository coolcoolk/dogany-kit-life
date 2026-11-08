"""The goal section on the spend overview; drafts live only in the flow."""
import copy
import html
import json
import re

from . import kit, text
from .markup import quote_items

PREFIX = 'ledger.onboarding.goal_spend.'


def t(key, **kw):
    return text.t(PREFIX + key, **kw)


def groups(flow):
    if 'goal_spend_draft' not in flow:
        flow['goal_spend_draft'] = copy.deepcopy(flow['plan'].get('goal_spend', []))
    return flow['goal_spend_draft']


def refresh_window(flow):
    """Rebuild stale candidates while retaining the owner's in-window removals."""
    removed = {r['id'] for g in flow.get('goal_spend_draft', []) for r in g['rows']
               if r.get('removed') and r.get('id') is not None}
    fresh = copy.deepcopy(flow['plan'].get('goal_spend', []))
    for group in fresh:
        for row in group['rows']:
            if row['id'] in removed:
                row['removed'] = True
    flow['goal_spend_draft'] = fresh


def render(flow):
    from .history_flow import _won
    draft = groups(flow)
    lines = [t('title'), t('explanation')]
    if not any(not r.get('removed') for g in draft for r in g['rows']):
        return '\n'.join(lines + [t('empty')])
    blocks = []
    for group in draft:
        rows = [r for r in group['rows'] if not r.get('removed')]
        block = ['<b>' + html.escape(group['title']) + '</b>']
        for row in rows:
            block.append(t('row', date='%d/%d' % (int(row['date'][5:7]), int(row['date'][8:])),
                           name=html.escape(row['name']), amount=_won(row['amount'])) +
                         (' · ' + t('tag.' + row['tag']) if row.get('tag') else ''))
        if rows:
            total = sum(r['amount'] for r in rows)
            n = max(1, flow['plan']['n'])
            period = t('three_months') if n == 3 else t('months', n=n)
            block.append(t('total', period=period, total=_won(total), average=_won(round(total/n))))
        else:
            block.append(t('empty'))
        blocks.append('\n'.join(block))
    lines.append(quote_items(blocks))
    lines.append(t('closing'))
    if flow.get('goal_spend_editing'):
        lines.append(t('which_row'))
    return '\n'.join(lines)


def choices(flow):
    if any(g['rows'] for g in groups(flow)):
        return [dict(label=t('yes'), value='goal_spend:yes'),
                dict(label=t('change'), value='goal_spend:change')]
    return []


def answer(flow, value, said):
    """Return True for goal actions, leaving the overview pending."""
    value = {t('yes'): 'goal_spend:yes', t('change'): 'goal_spend:change'}.get(value, value)
    if value == 'goal_spend:change':
        flow['goal_spend_editing'] = True
    elif value == 'goal_spend:yes':
        kit.run('ledger-plan-answer', '--today', flow['today'], '--data', json.dumps(dict(
            kind='goal_spend_confirm', months=flow['plan']['months'], groups=groups(flow),
            said=said or t('yes')), ensure_ascii=False))
        flow.pop('goal_spend_editing', None)
    elif (flow.get('goal_spend_editing') or value.lstrip().startswith('{')) and value != 'next':
        issued = [r for g in groups(flow) for r in g['rows']]
        rows = [r for r in issued if not r.get('removed')]
        if value.lstrip().startswith('{'):
            data = json.loads(value)
            ids = data.get('ids', [data['id']] if 'id' in data else [])
            if (not isinstance(ids, list) or not ids or any(type(i) is not int for i in ids)
                    or len(set(ids)) != len(ids) or set(ids) - {r['id'] for r in issued}):
                raise ValueError(t('which_row'))
            selected = [r for r in issued if r['id'] in ids]
        else:
            key = re.sub(r'\s+', '', value).casefold()
            selected = rows
            constrained = False
            # Every supplied constraint must hold together, even when all
            # receipts from the same merchant have the same amount.
            names = [r for r in rows if re.sub(r'\s+', '', r['name']).casefold() in key]
            if names:
                selected = names
                constrained = True
            dates = re.findall(r'(?<!\d)(?:(\d{4})-)?(\d{1,2})[/-](\d{1,2})(?!\d)', value)
            if dates:
                selected = [r for r in selected if any(
                    (not y or int(y) == int(r['date'][:4])) and
                    (int(m), int(d)) == (int(r['date'][5:7]), int(r['date'][8:])) for y,m,d in dates)]
                constrained = True
            amount_text = re.sub(r'(?<!\d)(?:\d{4}-)?\d{1,2}[/-]\d{1,2}(?!\d)', '', value)
            amounts = re.findall(r'(?<!\d)(\d[\d,]*)\s*(?:원|won|krw)?', amount_text, re.I)
            if amounts:
                selected = [r for r in selected if r['amount'] in {int(a.replace(',', '')) for a in amounts}]
                constrained = True
            if not names:
                # A supplied but unrecognized merchant must not silently
                # degrade into a date-only or amount-only selection.
                remainder = re.sub(r'\d[\d,]*\s*(?:원|won|krw)?', '', amount_text, flags=re.I)
                remainder = re.sub(r'빼줘|빼주세요|제외|삭제|지워줘|remove|exclude|please', '', remainder, flags=re.I)
                if re.search(r'[가-힣a-z]', remainder, re.I):
                    raise ValueError(t('which_row'))
            if not constrained or len(selected) != 1:
                raise ValueError(t('which_row'))
        for row in selected:
            row['removed'] = True
        flow.pop('goal_spend_editing', None)
    else:
        return False
    flow['questions'].pop('reduce_overview', None)
    return True
