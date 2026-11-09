"""The ledger's part of the daily / weekly retro (G7 provider).

  daily   only the automated path: card-SMS items pending + automation
          lines (first SMS arrived, the one sender question, app update).
          Short. Nothing -> nothing (exit 3).
  weekly  the sweep: ask for this week's screenshot / export file (when
          the owner chose the retro as the sweep time; the apps named come
          from sources.json by region, or the app the owner named), then every pending
          item (SMS, screenshot, CSV) as one table.
  both    payment-intermediary rows (Coupay, Naver Pay ...) as one grouped
          "what was it" block under the table (review.py / opaque.py);
          the weekly one also carries what the daily left unnamed.

The kit invokes this provider from service/kit_life/retro-sections.tsv
(row module_ledger -> ledger/retro-section --poll).
"""
import datetime

from . import automation, diagtext, issuers, kit, onboarding, review, sources, text, tiers, settlement

KIT_RETRO_SEAM = True


def poll_allowed():
    """Only completed onboarding grants the retro provider a polling lane."""
    return bool(onboarding.load_state().get('completed_at'))


def sweep_window(today):
    """This week Monday..today; on a Monday, last week Monday..Sunday."""
    monday = today - datetime.timedelta(days=today.weekday())
    if today.weekday() == 0:
        return monday - datetime.timedelta(days=7), monday - datetime.timedelta(days=1)
    return monday, today


def sweep_ask(today, tail=True):
    """The weekly sweep ask. tail: the retro's "already received is below"
    (the separate-time nudge has no table under it)."""
    since, until = sweep_window(today)
    src = sources.current()
    kw = {'since': '%d/%d' % (since.month, since.day), 'until': '%d/%d' % (until.month, until.day)}
    shots, csv = sources.screenshot_phrase(src), sources.csv_phrase(src)
    selected = sources.weekly_source()
    if selected['export']:
        msg = text.t('ledger.reconcile.sweep.file', app=selected['app'], **kw)
    elif selected['app']:
        msg = text.t('ledger.reconcile.sweep.capture', app=selected['app'], **kw)
    elif shots:
        msg = (text.t('ledger.sweep.ask.named', apps=shots, csv=csv, **kw) if csv
               else text.t('ledger.sweep.ask.named_nocsv', apps=shots, **kw))
    elif src['spend_app']:
        msg = text.t('ledger.sweep.ask.app', app=src['spend_app'], **kw)
    else:
        msg = text.t('ledger.sweep.ask.open', **kw)
    if tail:
        msg += ' ' + text.t('ledger.sweep.ask.tail')
    return msg, since, until


def month_block(today, mark=True):
    """Ledger v6: the first weekly retro of a month carries last month's diagnosis card, once (state key
    diag_shown). Nothing when the ledger has no rows for it."""
    month = tiers.prev_month(today)
    st = onboarding.load_state()
    if today.day > 7 or not st.get('completed_at') or st.get('diag_shown') == month:
        return ''
    try:
        card = diagtext.render(kit.ledger_diagnosis(month))
    except (kit.KitError, KeyError):
        return ''
    if mark:
        st['diag_shown'] = month
        onboarding.save_state(st)
    return card


def section(lane, today=None, mark=True):
    """-> (text, plan) ; text '' = nothing to show."""
    today = today or datetime.date.today()
    if not (onboarding.load_state().get('completed_at') or onboarding.load_state().get('recording_from')):
        return '', {'rows': [], 'digest': '', 'lane': lane}
    docs = issuers.load_all()
    notes = automation.lines(today, names={k: d['display_ko'] for k, d in docs.items()}, docs=docs)
    if mark and notes:
        automation.mark_announced([k for k, _ in notes])
    parts = [t for _, t in notes]
    plan = review.build(lane, today=today)
    if lane == 'weekly':
        sweep = (onboarding.load_state().get('sweep') or {}).get('mode', 'retro')
        if sweep == 'retro':
            # "already received is below" only when a table follows (trial
            # caution 2.46.0-dev.2: the footer pointed at nothing).
            parts.insert(0, sweep_ask(today, tail=bool(plan['rows']))[0])
    if plan['rows']:
        parts.append(review.render(plan))
    if lane == 'weekly':
        # onboarding v5: the after-a-month proposal (new start / "one more
        # month"), one trailing line, offered at most twice (D9 attachment)
        reminder = onboarding.backfill_reminder(mark=mark)
        if reminder:
            parts.append(reminder)
        due = onboarding.proposal_due(today)
        if due:
            parts.append(due)
            if mark:
                onboarding.proposal_offered()
        # last month's subscription tier change, once (month-close shares the
        # mark), and the fixed-cost rename offer (k <= 2) -- owner 2026-10-10
        parts += tiers.change_lines(tiers.prev_month(today), mark=mark)
        since, until = sweep_window(today)
        if mark:
            cards = settlement.sweep(since,until)
            plan['settlement_cards'] = cards
            parts += [settlement.plain(c) for c in cards]
        recorded = settlement.call('notices',since=str(since),until=str(until))
        if recorded:
            parts.append(text.t('ledger.split.rollup',rows='\n'.join(settlement.notice_line(r) for r in recorded)))
        block = month_block(today, mark=mark)
        if block:
            parts.append(block)
        unsettled = settlement.weekly(today)
        if unsettled:
            parts.append(settlement.plain(unsettled))
            plan['settlement'] = unsettled
    return '\n\n'.join(parts), plan
