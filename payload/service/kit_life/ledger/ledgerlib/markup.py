"""Ledger's quote rules, shared by fresh and resumed cards."""
import re
from functools import wraps
from item_grouping import quote_items

CONT = '\x01'
ITEM = '\x02'
_ITEM = re.compile(r'(?:\u2022 |\d+\. |\[[ v-]\] |'+ITEM+r'|(?=.+ <code>[█░]))')
_SUB = re.compile(r'(?:\u2514|\u3000|[ \t]+|'+CONT+r')')
_QUOTED = re.compile(r'(<blockquote>.*?</blockquote>)', re.S)


def _items(body):
    lines, items, item = [], [], []
    def flush():
        if item:
            items.append('\n'.join(item))
            item.clear()
        if items:
            lines.append(quote_items(items))
            items.clear()
    for line in body.split('\n'):
        if _ITEM.match(line):
            if item:
                items.append('\n'.join(item))
                item.clear()
            item.append(line.replace(ITEM, ''))
        elif item and _SUB.match(line):
            item.append(line.replace(CONT, ''))
        else:
            flush()
            lines.append(line.replace(CONT, ''))
    flush()
    return '\n'.join(lines)


def _short_quotes(body):
    """Merge marked short items only; prose/table quotes delimit groups."""
    def family(value):
        if value.startswith('• '): return 'bullet'
        if re.match(r'\d+\. ', value): return 'number'
        if re.match(r'\[[ v-]\] ', value): return 'status'
    def short(value):
        rows = value.split('\n')
        starts = [n for n, row in enumerate(rows) if _ITEM.match(row)]
        return starts and starts[0] == 0 and all(
            b-a <= 2 for a,b in zip(starts, starts[1:] + [len(rows)]))
    body = re.sub(r'</blockquote>\n(?:[ \t]*\n)+(?=<blockquote>)', '</blockquote>\n', body)
    parts = []
    for part in _QUOTED.split(body):
        if part.startswith('<blockquote>'):
            content = part[12:-13]
            if len(parts) >= 2 and parts[-1] == '\n' and parts[-2].startswith('<blockquote>'):
                previous = parts[-2][12:-13]
                if family(content) and family(content) == family(previous) and short(content) and short(previous):
                    parts[-2:] = ['<blockquote>'+previous+'\n'+content+'</blockquote>']
                    continue
        if part:
            parts.append(part)
    return ''.join(parts)


def quote_lists(body, *, qid=None):
    """Group marked items by rendered size; retain headings, tables and prose."""
    # Owner-approved multi-payment card uses plain bullet lines.
    if (qid or '').startswith('review_deposit_group_multi_'):
        return body
    # The roster already supplies one quote per instrument.
    if qid in ('reduce_purposes', 'reduce_cap', 'reduce_subscriptions', 'reduce_subscription_ai', 'reduce_subscription_cuts'):
        return body
    parts = []
    for part in _QUOTED.split(body):
        if part.startswith('<blockquote>'):
            content = part[len('<blockquote>'):-len('</blockquote>')]
            if qid == 'reduce_subscriptions' and parts:
                from . import text
                if parts[-1].endswith(text.t('ledger.onboarding.reduce.oneoffs_title')+'\n'):
                    # The owner's dropped-charge list is one self-contained block.
                    parts.append(part)
                    continue
            if qid == 'reduce_overview' and content.startswith('<b>'):
                # The goal name, evidence and total are one owner-picked card.
                parts.append(part)
                continue
            starts = [n for n, line in enumerate(content.split('\n')) if _ITEM.match(line)]
            if not starts:
                heading, sep, rest = content.partition('\n')
                if sep and re.match(r'^[^\w<]*<b>', heading):
                    parts.append(heading+'\n<blockquote>'+rest+'</blockquote>')
                else:
                    parts.append(part)
                continue
            if starts and starts[0] == 0:
                rows = content.split('\n')
                parts.append(quote_items('\n'.join(rows[a:b]).replace(ITEM, '').replace(CONT, '')
                    for a,b in zip(starts, starts[1:] + [len(rows)])))
                continue
            prefix = content.split('\n')
            first = starts[0]
            if first:
                heading = '\n'.join(prefix[:first])
                if '<code>' in heading or '<pre>' in heading:
                    heading = '<blockquote>'+heading+'</blockquote>'
                elif '<b>' not in heading and heading.strip():
                    heading = '<b>'+heading+'</b>'
                parts.append(heading+'\n')
                content = '\n'.join(prefix[first:])
            part = content
        # Month captions are group headings in saved as well as fresh cards.
        part = re.sub(r'(?m)^(\d{1,2}월|\d{4}-\d{2} 합계 [^\n<]+)$', r'<b>\1</b>', part)
        parts.append(_items(part))
    return _short_quotes(''.join(parts))


def quote_card(render):
    @wraps(render)
    def quoted(*args, **kwargs):
        return quote_lists(render(*args, **kwargs))
    return quoted


def _complete_item_quotes(body):
    def regroup(match):
        items = [part[12:-13] for part in _QUOTED.findall(match[0])]
        return quote_items(items)
    return re.sub(r'<blockquote>.*?</blockquote>(?:\n<blockquote>.*?</blockquote>)*',
                  regroup, body, flags=re.S)


def onboarding_quotes(body, *, qid):
    """Apply grouping to fresh and held cards without changing their controls."""
    from . import text
    if qid in ('reduce_cap', 'reduce_subscriptions', 'reduce_subscription_ai', 'reduce_subscription_cuts'):
        return body
    if qid == 'reduce_purposes':
        for group in ('credit', 'debit', 'account', 'wallet'):
            heading = text.t('ledger.onboarding.reduce.roster_'+group)
            body = body.replace('\n'+heading+'\n', '\n<b>'+heading+'</b>\n')
    # Owner 2026-11-04 09:41: section headers (emoji + bold title) sit outside
    # the quote. Plain bold instrument names belong inside their item quotes.
    def header_outside(match):
        content = match[0][len('<blockquote>'):-len('</blockquote>')]
        heading, sep, rest = content.partition('\n')
        section = re.fullmatch(r'[\U0001f000-\U0001faff\u2600-\u27bf][^\w<\s]*[ \t]+<b>[^\n]+?</b>[^\n]*', heading)
        if section:
            return heading+('\n<blockquote>'+rest+'</blockquote>' if sep else '')
        return match[0]

    body = _QUOTED.sub(header_outside, body)
    title_key = {'reduce_big': 'big_title', 'reduce_cap': 'cap_title',
                 'reduce_subscriptions': 'subscriptions_title', 'reduce_habits': 'habits_title'}.get(qid)
    title = text.t('ledger.onboarding.reduce.'+title_key) if title_key else None

    parts = []
    for part in _QUOTED.split(body):
        if part.startswith('<blockquote>'):
            content = part[len('<blockquote>'):-len('</blockquote>')]
            # English reduction titles are numbered too; they are headings,
            # whereas numbered payment rows begin with a date or an amount.
            if content == title:
                parts.append('<b>'+content+'</b>')
                continue
        if part:
            parts.append(part)
    result = _short_quotes(''.join(parts))
    if qid in ('reduce_purposes', 'reduce_overview'):
        result = _complete_item_quotes(result)
    return result
