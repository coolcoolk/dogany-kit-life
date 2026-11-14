"""UI-PENDING: render review candidates only; no production caller.

Owner 2026-11-09: no new visible wording approved. Keep these functions out
of the delivered cards until the owner picks each screen. Replay imports
them to show exact real-data renders, including masks and actual amounts.
"""
import datetime
import html

from . import dashboard, reduction_flow as R
from .markup import quote_lists


def matched_line(row, today):
    name = html.escape(row.get('display_name', row['name']))
    senders = ', '.join('%s %s' % (html.escape(r['name']), R._money(r['amount']))
                        for r in row['receipts'])
    people = '%d명이 나눔 · ' % row['people'] if row['people'] else ''
    return '• %s %s %s: %s보낸 사람 %s · 받은 돈 %s · 본인 몫 %s' % (
        dashboard.question_date(row['date'], today), name, R._money(row['amount']),
        people, senders, R._money(row['received']), R._money(row['amount'] - row['received']))


def matched_card(plan, today):
    first, last = R.text.month_bounds(plan['months'][0], plan['months'][-1])
    lines = [R.t('import_read', first=first, last=last), '', R.t('matched_title', owner=R.t('owner_slot'))]
    month = None
    for row in plan['matched_payments']:
        if row['date'][:7] != month:
            month = row['date'][:7]
            lines += ['', '<b>' + R.t('matched_month', month=int(month[5:])) + '</b>']
        lines.append(matched_line(row, today))
    return quote_lists('\n'.join(lines))


def undo_reply(snapshot):
    r = snapshot['receipt']
    return '%s님이 보낸 %s은 이 결제와 연결을 풀었어요. 같은 결제에 다시 연결하지 않을게요.' % (
        html.escape(r['name']), R._money(r['amount']))


def own_group(group, today):
    from . import deposit_groups as G, diagtext
    lines = [G.text.t('ledger.unmatched_deposit.group.own_transfer', n=len(group['items']))]
    for row in group['items']:
        lines.append('• %s %s %s → %s' % (dashboard.question_date(row['date'], today),
            html.escape(row['counterparty']), diagtext.money(row['amount']),
            html.escape(dashboard.roster_display(row.get('method') or ''))))
    return quote_lists('\n'.join(lines))


def allocation_card(snapshot):
    from . import allocation
    body = allocation.candidate_text(snapshot).replace(
        allocation.t('received', amount=allocation.won(snapshot['receipt']['amount'])),
        '받은 돈 · %s원' % allocation.won(snapshot['receipt']['amount']))
    return '보낸 사람 %s\n\n%s' % (html.escape(snapshot['receipt']['name']), body)


def legacy_ending(diag, plan, today):
    rows = [r for r in plan['matched_payments'] if r['date'][:7] == diag['month']]
    return dashboard.review_screen(diag, 'ending', today=today) + ('\n\n' + quote_lists(
        R.t('matched_title', owner=R.t('owner_slot')) + '\n' +
        '\n'.join(matched_line(r, today) for r in rows)) if rows else '')


def allocation_result(snapshot):
    from . import allocation, diagtext
    date = datetime.date.fromisoformat(snapshot['allocations'][0]['date'])
    line = allocation.t('applied_single' if len(snapshot['allocations']) == 1 else 'applied',
        m=date.month, d=date.day, names=html.escape('·'.join(a['name'] for a in snapshot['allocations'])),
        amount=diagtext.money(snapshot['allocated']))
    return '보낸 사람 %s\n%s' % (html.escape(snapshot['receipt']['name']), line)


def remembered_note(rows, today):
    payment = rows[0]['split_payment']
    received = sum(r['amount'] for r in rows)
    return quote_lists('%s %s %s\n지난번 정산 답을 기억해서 같은 모임의 입금을 연결했어요.\n%s\n본인 몫 %s만 지출로 남겼어요.' % (
        dashboard.question_date(payment['date'], today), html.escape(payment['name']), R._money(payment['amount']),
        '\n'.join('• %s %s' % (html.escape(r['name']), R._money(r['amount'])) for r in rows),
        R._money(payment['amount'] - received)))
