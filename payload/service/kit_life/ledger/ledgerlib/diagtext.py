"""The owner-facing words of the ledger diagnosis (flow v6, S3).

The kit's `ledger-diagnosis` returns numbers and classes only; every sentence
here is an i18n key (`ledger.diag.*`) with the kit's numbers as placeholders.
Money is EXACT here (the diagnosis is the one place the owner reads to the
won); lever amounts are the only rounded numbers ("about 110,000 won").
"""
import collections

from . import text
from item_grouping import quote_items
from money_format import comma, split_man

CLASS_ORDER = ('housing', 'loan', 'insurance', 'saving', 'utility', 'telecom', 'tax', 'subscription', 'other')
KEEP_ORDER = ('must', 'reducible', 'droppable', None)


def money(n):
    """Exact won in the owner's style (ko: man + rest, e.g. 3,662,550 -> 366 man 2,550 won; other: plain)."""
    n = int(round(n))
    if text.t('ledger.money.style') != 'man' or abs(n) < 10000:
        return text.t('ledger.money.won', n=comma(n))
    man, rest = split_man(n)
    sign = '-' if n < 0 else ''
    if rest == 0:
        return sign + text.t('ledger.money.man', man=comma(man))
    return sign + text.t('ledger.money.man_rest', man=comma(man), rest=comma(rest))


def approx(n):
    """A lever amount: about N man (ko) / about N won."""
    n = int(round(n))
    if text.t('ledger.money.style') == 'man' and n >= 10000:
        return text.t('ledger.money.about', v=text.t('ledger.money.man', man=format(int(round(n / 10000.0)), ',')))
    return text.t('ledger.money.about', v=text.t('ledger.money.won', n=format(int(round(n, -2)) or n, ',')))


def month_no(ym):
    return int(ym[5:7])


def lever_title(lv):
    return text.t('ledger.diag.lever.%s.title' % lv['rule'], **lv['params'])


def lever_label(lv):
    return text.t('ledger.diag.lever.%s.btn' % lv['rule'], **lv['params'])


def _item(l):
    mark = text.t('ledger.diag.item.mark') if not l['seen'] else ''
    if l['state'] == 'changed' and l.get('changed_from'):
        return text.t('ledger.diag.item.changed', name=l.get('display_name', l['name']), amount=money(l['amount']),
                      was=money(l['changed_from'])) + mark
    return text.t('ledger.diag.item.plain', name=l.get('display_name', l['name']), amount=money(l['amount'])) + mark


def render(d):
    """The S3 card for one kit diagnosis dict."""
    t = text.t
    m = month_no(d['month'])
    L = [t('ledger.diag.title', m=m), '']
    inc = d['income']
    L.append(t('ledger.diag.income', total=money(inc['total'])))
    items = []
    for ln in inc['regular_lines']:
        items.append(t('ledger.diag.income.regular', name=ln.get('display_name', ln['name']), amount=money(ln['amount']),
                   mark='' if ln['seen'] else t('ledger.diag.item.mark')))
    if inc['other']:
        items.append(t('ledger.diag.income.other', amount=money(inc['other'])))
    if items:
        L.append(quote_items(row.lstrip() for row in items))
    if d['pass_through']:
        L.append(t('ledger.diag.passthrough', amount=money(d['pass_through'])))
    out = d['out']
    L += ['', t('ledger.diag.out', total=money(out['total']))]
    if out['total']:
        L.append(t('ledger.diag.out.regular', amount=money(out['regular']), pct=round(100.0 * out['regular'] / out['total'])))
    by_cls = collections.OrderedDict()
    for ln in sorted(out['regular_lines'], key=lambda l: (CLASS_ORDER.index(l['class']) if l['class'] in CLASS_ORDER else 99,
                                                          -l['amount'])):
        label = t('ledger.diag.class.' + ln['class']) if ln['class'] in CLASS_ORDER else t('ledger.diag.class.other')
        by_cls.setdefault(label, []).append(ln)
    fixed_items = []
    for label, lines in by_cls.items():
        keeps = collections.OrderedDict((k, []) for k in KEEP_ORDER)
        for ln in lines:
            keeps[ln['keep_class'] if ln['keep_class'] in KEEP_ORDER else None].append(ln)
        first = True
        for kc, ls in keeps.items():
            if not ls:
                continue
            kw = (t('ledger.diag.keep.sep') + t('ledger.diag.keep.' + kc)) if kc else ''
            items = ' / '.join(_item(l) for l in ls)
            if first:
                fixed_items.append(t('ledger.diag.out.line', label=label, items=items, keep=kw))
            else:
                fixed_items.append(t('ledger.diag.out.line_more', pad=' ' * (len(label) * 2), items=items, keep=kw))
            first = False
    if fixed_items:
        L.append(quote_items(fixed_items))
    if d['dormant']:
        L.append(t('ledger.diag.dormant', names=text.josa(text.join([x.get('display_name', x['name']) for x in d['dormant']]), 'eun')))
    if any(not l['seen'] for l in out['regular_lines'] + inc['regular_lines']):
        L.append(t('ledger.diag.late_note', m=m))
    if out['total']:
        L += ['', t('ledger.diag.var', amount=money(out['variable']), pct=round(100.0 * out['variable'] / out['total']))]
    items = []
    for g, v in out['variable_groups'].items():
        extra = ''
        if g == out['delivery'].get('group') and out['delivery']['count'] > 0:
            extra = t('ledger.diag.var.delivery', n=out['delivery']['count'], amount=money(out['delivery']['amount']))
        items.append(t('ledger.diag.var.group', group=g, amount=money(v['amount']), extra=extra))
    for name, v in list(d['people'].items())[:3]:
        items.append(t('ledger.diag.people', name=name, amount=money(v['amount'])))
    if items:
        L.append(quote_items(items))
    if out['saved']:
        L.append(t('ledger.diag.saved', amount=money(out['saved'])))
    if d['pulled_back']:
        L.append(t('ledger.diag.pulled', amount=money(d['pulled_back'])))
    bal = d['balance']
    saved = t('ledger.diag.saved_in', amount=money(out['saved'])) if out['saved'] else ''
    L.append('')
    L.append(t('ledger.diag.short', m=m, amount=money(-bal), saved=saved) if bal < 0
             else t('ledger.diag.left', m=m, amount=money(bal), saved=saved))
    if d['gap_line']:
        L.append(t('ledger.diag.gap'))
    if d.get('biz_drift'):
        L.append(t('ledger.diag.biz', names=text.join(d['biz_drift']['methods']),
                   pct=round(100 * min(d['biz_drift']['shares']))))
    if d['levers']:
        L += ['', t('ledger.diag.levers')]
        L.append(quote_items(t('ledger.diag.lever.item', n=i, title=lever_title(lv), amount=approx(lv['monthly'])).lstrip()
                             for i, lv in enumerate(d['levers'], 1)))
        if len(d['levers']) > 1:
            L.append(t('ledger.diag.lever.sum', amount=approx(sum(l['monthly'] for l in d['levers']))))
    L += ['', t('ledger.diag.tail' if d['levers'] else 'ledger.diag.tail_nolever')]
    mc = d['methods']
    if mc:
        parts = text.join([t('ledger.diag.layer.%s' % k) + ' %d' % v for k, v in sorted(mc.items(), key=lambda kv: kv[0])])
        L.append(t('ledger.diag.methods', n=sum(mc.values()), parts=parts))
    return '\n'.join(L)
