"""Owner-picked ranked comparison with reasons and sourced performance."""
import html
from urllib.parse import urlparse

from money_format import comma
from . import dashboard, text
from .markup import quote_items


def t(key, **kw):
    return text.t('ledger.benefit.' + key, **kw)


def won(amount):
    return t('won', amount=comma(amount))


def linked(value, source):
    if source and urlparse(source).scheme == 'https' and urlparse(source).netloc:
        return '<a href="%s">%s</a>' % (html.escape(source, quote=True), value)
    return value


def performance_lines(performance):
    if not performance['need']:
        return [t('no_condition')]
    spent, need = performance['spent'], performance['need']
    status = t('achieved') if spent >= need else ''
    if not performance['confirmed']:
        status += (' ' if status else '') + t('estimated')
    value = t('performance', bar=dashboard.progress_bar(spent, need, nearest=True),
              spent=dashboard.man_amount(spent), need=dashboard.man_amount(need), status=status)
    value = linked(value, performance.get('source'))
    lines = [value]
    if spent < need:
        lines.append(t('shortfall', amount=dashboard.man_amount(need - spent)))
    source = performance.get('source')
    if source and linked('', source) == '':
        lines.append(t('statement', source=html.escape(source)))
    return lines


def render(comparison):
    purchase = comparison['purchase']
    candidates = sorted(comparison['candidates'], key=lambda r: (r['status'] != 'known', -(r['benefit'] or 0)))
    blocks = [t('purchase', merchant=html.escape(purchase['merchant']), amount=won(purchase['amount']))]
    rank = 0
    for row in candidates:
        name = linked(html.escape(row['name']), row.get('official_source'))
        if row['status'] == 'known':
            rank += 1
            glyph = t('rank.' + str(rank)) if rank <= 3 else t('rank.other')
            lines = [t('option', rank=glyph, name=name, amount=won(row['benefit']))]
            rule = row['rule']
            if 'rate_bps' in rule:
                rate = format(row['applied_rate_bps'] / 100, '.2f').rstrip('0').rstrip('.')
                reason = t('discount', rate=rate)
            else:
                # A fixed reward is shown as its effective purchase yield.
                rate = format(row['benefit'] * 100 / purchase['amount'], '.1f').rstrip('0').rstrip('.')
                reason = t('accrual', rate=rate)
            if row['remaining'] is not None:
                reason = t('with_cap', reason=reason, amount=won(row['remaining']))
            lines.append(reason)
        else:
            lines = [t('not_comparable', name=name)]
            performance = row.get('performance')
            if not performance or performance['spent'] >= performance['need']:
                lines.append(t('unknown'))
        if row.get('performance'):
            lines.extend(performance_lines(row['performance']))
        if row.get('basis') == 'statement' and row.get('statement_source'):
            lines.append(t('statement', source=html.escape(row['statement_source'])))
        blocks.append('\n'.join(lines))
    return blocks[0] + ('\n\n' + quote_items(blocks[1:]) if len(blocks) > 1 else '')
