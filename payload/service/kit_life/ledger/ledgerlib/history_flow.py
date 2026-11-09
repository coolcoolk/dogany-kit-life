# -*- coding: utf-8 -*-
"""History-first ledger starts; saved earlier flows remain resumable.

All ledger reads/writes use kit verbs. Recording is enabled on either opener
choice; planning is asked only after analysis of the provided completed months.
"""
import datetime
import copy
import html
import json
import re
import unicodedata
import uuid

from money_format import comma, decimal_man, man_one_decimal, man_whole
from . import kit, lookup, paths, seed, sources, text, dashboard
from .markup import CONT, ITEM

CONTROLLER = 'history_v1'
PREFIX = 'ledger.onboarding.history.'


def t(key, **kw):
    return text.t(PREFIX + key, **kw)


def new_flow(today, offer=False, restart=False):
    return dict(v=7, controller=CONTROLLER, answers={}, questions={}, pending=None,
                today=today.isoformat(), offer_lane=bool(offer), restart=bool(restart),
                history_first=True, nonce=uuid.uuid4().hex, started_at=datetime.datetime.now().isoformat(timespec='seconds'))


def _save(st, flow):
    from . import onboarding as O
    st['flow'] = flow
    O.save_state(st)


def _q(qid, body, choices=(), **kw):
    from . import onboarding as O
    return O._interactive(O._q(qid, body, [dict(label=t(label), value=value)
                                            for label, value in choices], **kw))


def _parts(flow):
    if flow.get('history_first'):
        return _q('parts', t('parts'), [('look_back', 'past'), ('start_recording', 'record')])
    return _q('parts', t('parts'), [('record_only', 'record'), ('past_only', 'past'),
                                   ('plan_only', 'plan'), ('all_parts', 'all')])


def _intake_choices(flow):
    # Owner UI gate: no approved no-history budget screen exists yet.
    # Past-only can still finish recording; planning must receive a file.
    return [] if 'plan' in flow.get('parts', []) else [('record_today', 'continue')]


def _needs_source(flow):
    # Owner 2026-10-16 15:44: the weekly capture source is asked before any
    # history intake. Existing history defers source lookup to the balance anchor.
    return not kit.records_info().get('count')


def _source(flow):
    from . import onboarding as O
    return O._interactive(O._source_ask(flow))


def _export_app(src):
    return src['csv'][0] if src['csv'] and not src['ask_app'] else None


def _method_buttons(flow, how=True):
    """Capture/file buttons follow the source answer (sources.json, never a hardcoded list)."""
    from . import onboarding as O
    choices = O._method_choices(flow)
    if how and _export_app(sources.current()):
        choices.append(O._choice('file.how', 'how'))
    return choices


def _qc(qid, body, choices, **kw):
    from . import onboarding as O
    return O._interactive(O._q(qid, body, choices, **kw))


def _how_text(flow, today):
    """v6 [how]: dated, sourced export steps from the live lookup; generic fallback otherwise."""
    from . import onboarding as O
    src = sources.current()
    app = _export_app(src)
    csv = sources.csv_phrase(src) or ''
    v = lookup.get(app, 'how', today) if app else None
    if v and v.get('verdict') == 'found' and v.get('source') in lookup.SOURCES:
        when = (O._t('how.today') if v['checked'] == today.isoformat()
                else O._t('how.on', date=O._date_word(v['checked'])))
        lines = [O._t('how.head', csv=csv),
                 O._t('how.src_official' if v['source'] == 'official' else 'how.src_posts', csv=csv, when=when)]
        lines += ['%d. %s' % (k, html.escape(step)) for k, step in enumerate(v['steps'], 1)]
        lines.append(O._t('how.tail'))
        return '\n'.join(lines)
    fallback, export_how = O._t('how.fallback'), O._t('file.export_how', csv=csv)
    # Both approved keys open with the same quoted line; show it once.
    return export_how if export_how.startswith(fallback) else fallback + '\n' + export_how


def _intake(flow, today=None):
    info = kit.records_info()
    count = info.get('total_count', info.get('count', 0))
    flow['existing_records'] = count
    if count and not flow.get('needs_history_basis'):
        first, last = text.date_bounds(info.get('total_first', info['first']),
                                       info.get('total_last', info['last']), today or flow['today'])
        return _q('file', t('existing', first=first, last=last, count=count), [('view_history', 'existing')], free=True)
    from . import onboarding as O
    if flow.get('how'):
        today = today or datetime.date.fromisoformat(flow['today'])
        return _qc('file', _how_text(flow, today), _method_buttons(flow, how=False), free=True)
    # Reuse only the approved file-request paragraph while planning is selected.
    body = t('intake').split('\n\n', 1)[0] if 'plan' in flow.get('parts', []) else t('intake')
    choices = _method_buttons(flow)
    if 'source' not in flow.get('answers', {}) and not any(c['value'].startswith('capture') for c in choices):
        # The app question was not asked (history already on file): keep the approved generic capture button.
        choices.insert(0, dict(label=t('capture'), value='capture'))
    choices += [dict(label=t(label), value=value) for label, value in _intake_choices(flow)]
    return _qc('file', body, choices, free=True)


def window_args(flow):
    return ['--all-months'] if flow.get('history_first') else []


def _history(flow):
    return kit.run('ledger-history', '--today', flow['today'], *window_args(flow))


def _prepare(st, flow, today):
    from . import onboarding as O
    result = kit.run('ledger-history-reconcile', '--today', today.isoformat(), '--review-deposits', *window_args(flow), timeout=300)
    flow['today'] = today.isoformat()
    flow['history'] = result['history']
    flow['deposit_match_version'] = 2
    flow.pop('plan', None)
    flow['path'] = 'imported' if any(m['recorded'] for m in flow['history']['months']) else 'new'
    window = flow['history']['window']['average_months']
    if 'plan' in flow.get('parts', []) and not any(
            m['recorded'] and m['month'] in window for m in flow['history']['months']):
        flow['needs_history_basis'] = True
    else:
        flow.pop('needs_history_basis', None)
    O._complete_backfill(st)


def _history_ids(flow):
    n = max(1, (len(flow['history']['months']) + 17) // 18)
    return ['history' if i == 0 else 'history_%d' % (i+1) for i in range(n)]


def _accounts_question(st, flow):
    """Reuse the approved account roster and balance cards in redesigned starts.

    Record-only never enters imported history. Past/plan starts retain the
    account anchor, including when existing rows bypass the source question.
    The month analysis follows this balance anchor.
    """
    from . import onboarding as O
    if flow.get('accounts_complete') and flow.get('wallet_funding_version') == 1:
        return None
    # Account reads must not reconcile streams or rewrite approved annual cycles.
    d = O._resolve_banks(flow, kit.ledger_diagnosis(flow.get('diag_month'), roster_only=True),
                         roster_only=True)
    d = O._resolve_funding(flow, d, roster_only=True)
    roster = d['review']['roster']
    accounts = [m for m in roster['groups']['account'] if m['kind'] == 'account']
    flow['review_banks'] = [m for m in accounts if O._generic_account(m)]
    ids = ['review_bank_' + str(m['id']) for m in flow['review_banks']]
    # The redesigned instrument card already showed this roster. Repeat it
    # only when bank naming changed after that card, or it was never shown.
    renamed = any(key.startswith('review_bank_') and
                  (rec.get('inferred') or rec.get('value') not in (None, 'unknown'))
                  for key, rec in flow['answers'].items())
    flow['review_funding'] = O._funding_targets(roster)
    pending_wallet = any(m['kind'] == 'easy_pay' and 'review_funding_' + str(m['id']) not in flow['answers']
                         for m in flow['review_funding'])
    if (accounts or pending_wallet) and ('reduce_purposes' not in flow['answers'] or renamed):
        ids.append('review_roster')
    ids += ['review_funding_' + str(m['id']) for m in flow['review_funding']]
    flow['review_balances'] = O._balance_plan(flow, roster)
    ids += ['review_balance_' + str(m['id']) for m in flow['review_balances']]
    if flow.get('balance_gap') and 'review_balance_gap' not in flow['answers']:
        ids.insert(0, 'review_balance_gap')
    qid = next((key for key in ids if key not in flow['answers']), None)
    if ((qid or '').startswith('review_balance_') and qid != 'review_balance_gap'
            and not O._balance_source_known(flow)):
        qid = 'source'
    if qid is None:
        flow['accounts_complete'] = True
        flow['wallet_funding_version'] = 1
        return None
    flow.pop('accounts_complete', None)
    flow['wallet_funding_version'] = 1
    q = None if qid == 'review_roster' else flow['questions'].get(qid)
    if (q and qid.startswith('review_balance_') and qid != 'review_balance_gap'
            and q.get('aggregator') != O._balance_aggregator(flow)):
        q = None
    if q is None:
        q = _source(flow) if qid == 'source' else O._interactive(O._review_ask(qid, flow, d))
        flow['questions'][qid] = q
    q['nonce'] = flow['nonce']
    q['card'] = O.review_card(q)
    flow['pending'] = qid
    _save(st, flow)
    return dict(question=q, done=False, step=None, of=None)


def _deposits_question(st, flow):
    from . import onboarding as O
    # Revalidate even an issued queue: its window can move, and another
    # import or owner answer can classify a previously unresolved receipt.
    from . import reduction_flow
    plan = reduction_flow._read(flow)
    deposits = reduction_flow._recall_rows(plan.get('deposits', []), reduction_flow._recall_today(flow))
    O._drop_stale_deposit_questions(flow, deposits)
    flow['deposit_queue'] = [dict(item=r, month=r['month']) for r in deposits]
    from . import deposit_groups
    qid = next((key for key in deposit_groups.ids(flow, deposits) if key not in flow['answers']), None)
    if qid is None:
        return None
    q = flow['questions'].get(qid)
    if q is None:
        row = next((r for r in deposits if qid == 'review_deposit_' + str(r['id'])), deposits[0])
        d = dict(month=row['month'], review=dict(unmatched_deposits=deposits))
        q = O._review_ask(qid, flow, d)
        q['nonce'] = flow['nonce']
        q['card'] = O.review_card(q)
        flow['questions'][qid] = q
    flow['pending'] = qid
    _save(st, flow)
    return dict(question=q, done=False, step=None, of=None)


def resume(st, flow, now):
    """Reuse the picked opener and existing phase labels; preserve the held card."""
    from . import onboarding as O
    pending = flow.get('pending')
    if pending == 'resume':
        return dict(question=flow['questions']['resume'], done=False, step=None, of=None)
    activity = flow.get('asked_at') or flow.get('started_at')
    if not pending or not activity or not flow.get('answers'):
        return None
    idle = now - datetime.datetime.fromisoformat(activity)
    if idle < datetime.timedelta(hours=O.RESUME_HOURS):
        return None
    if pending in ('parts', 'source', 'file'):
        phase = 'start'
    elif pending.startswith(('review_bank_', 'review_balance_')) or pending in ('review_roster', 'reduce_purposes'):
        phase = 'accounts'
    elif pending.startswith('review_deposit_'):
        phase = 'income'
    elif pending == 'reduce_goal':
        phase = 'finish'
    else:
        phase = 'spending'
    done = []
    if phase != 'start' and 'file' in flow['answers']:
        done.append(O._t('phase.start'))
    if phase not in ('start', 'accounts') and flow.get('accounts_complete'):
        done.append(O._t('phase.accounts'))
    done, current = '/'.join(done), O._t('phase.'+phase)
    q = O._interactive(O._q('resume', O._t('resume.fresh' if done else 'resume.fresh.partial',
        done_phases=text.josa(done, 'eun') if text.lang() == 'ko' else done, current_phase=current),
        [O._choice('resume.continue', 'continue'), O._choice('resume.restart', 'restart')], nonce=flow['nonce']))
    flow['resume_of'], flow['pending'] = pending, 'resume'
    flow['questions']['resume'] = q
    _save(st, flow)
    return dict(question=q, done=False, step=None, of=None)


def next_question(st, flow, today):
    flow['recall_today'] = today.isoformat()
    flow.setdefault('nonce', uuid.uuid4().hex)
    answers = flow['answers']
    from . import reduction_flow
    reduction_flow.refresh_debts(flow)
    from ledger_window import window as display_window
    expected_window = display_window(today)
    saved_window = flow.get('history', {}).get('window', {})
    needs_window_upgrade = bool(flow.get('history')) and (
        saved_window.get('display_revision') != 1 or
        saved_window.get('average_months') != expected_window['average_months'])
    if needs_window_upgrade:
        flow['questions'] = {key: q for key, q in flow['questions'].items() if key in answers}
        flow.pop('resume_hold', None)
        flow.pop('resume_hold_token', None)
    if (flow.get('history') and (needs_window_upgrade or flow.get('path') == 'imported'
            and (flow.get('deposit_match_version', 0) < 2 or 'money_flow' not in flow['history']))):
        # Saved imports receive the current reconcile rules before any old
        # summary or deposit card is reissued. Durable owner answers stay.
        _prepare(st, flow, today)
        from . import reduction_flow
        reduction_flow._read(flow)
        if needs_window_upgrade:
            from . import goal_spend
            goal_spend.refresh_window(flow)
        retired = {key for key in flow['questions'] if key not in answers
                   and (key.startswith('review_deposit_') or key == 'reduce_refunds')}
        for key in retired:
            flow['questions'].pop(key)
        if flow.get('pending') in retired:
            flow['pending'] = None
        if flow.get('resume_hold') in retired:
            flow.pop('resume_hold', None)
            flow.pop('resume_hold_token', None)
    held = flow.get('resume_hold')
    from . import onboarding as O
    if held and held.startswith('review_balance_') and held != 'review_balance_gap':
        saved = flow['questions'].get(held) or {}
        if not O._balance_source_known(flow) or saved.get('aggregator') != O._balance_aggregator(flow):
            # A resume must also upgrade the old per-account card after its
            # retained export identifies an aggregator (or ask the source).
            held = None
            flow.pop('resume_hold', None)
            flow.pop('resume_hold_token', None)
    if held == 'reduce_refunds':
        from . import reduction_flow
        plan = flow.get('plan', {})
        if not (reduction_flow._recall_rows(plan.get('refunds', []), flow['recall_today'])
                or plan.get('matched_payments')):
            held = None
    if (held and (held != 'conclusion' or flow['questions'].get(held, {}).get('money_flow_revision') == 2)
            and not held.startswith('review_deposit_')
            and (held not in ('reduce_refunds', 'reduce_transfers') or flow.get('today') == today.isoformat())
            and flow.get('pending') == held and held not in answers and held in flow['questions']
            and O._review_token(flow['questions'][held]) == flow.get('resume_hold_token')):
        if held.startswith('reduce_'):
            from . import reduction_flow
            return reduction_flow.issue(st, flow, held)
        return dict(question=flow['questions'][held], done=False, step=None, of=None)
    flow.pop('resume_hold', None)
    flow.pop('resume_hold_token', None)
    if ('plan' in flow.get('parts', []) and flow.get('path') != 'imported'
            and 'file' in answers):
        answers.pop('file')
        flow['questions'].pop('file', None)
    if (flow.get('history') and 'conclusion' not in answers
            and (flow['today'] != today.isoformat() or not flow['history'].get('window'))):
        # Refresh the local-date cutoff and projection on a resumed screen.
        _prepare(st, flow, today)
        flow['questions'] = {key: q for key, q in flow['questions'].items() if key in answers}
    if 'parts' not in answers:
        qid = 'parts'
    elif (not flow.get('history_first') and flow.get('parts') and 'file' not in answers and 'source' not in answers
            and _needs_source(flow)):
        qid = 'source'
    elif 'file' not in answers and flow.get('parts'):
        qid = 'file'
    elif flow.get('path') == 'imported':
        from . import reduction_flow
        intake = reduction_flow.intake_question(st, flow, stage='roster')
        if intake is not None:
            return intake
        accounts = _accounts_question(st, flow)
        if accounts is not None:
            return accounts
        if 'retired_purpose_payment_ids' not in flow:
            from . import instrument_context
            flow['retired_purpose_payment_ids'] = instrument_context.retired_purpose_payment_ids(
                flow['plan'], seen=st.get('instrument_seen'))
        deposits = _deposits_question(st, flow)
        if deposits is not None:
            return deposits
        intake = reduction_flow.intake_question(st, flow, stage='payments')
        if intake is not None:
            return intake
        ids = (_history_ids(flow) if 'past' in flow['parts'] else []) + ['conclusion']
        qid = next((key for key in ids if key not in answers), None)
        if qid is None:
            from . import reduction_flow
            if flow.get('history_first') and 'plan_choice' not in answers:
                qid = 'plan_choice'
            elif flow.get('planning') == 'saving':
                if 'target' in answers:
                    return _completion(st, flow)
                qid = 'target'
            else:
                return reduction_flow.next_question(st, flow, today) if 'plan' in flow['parts'] else _completion(st, flow)
    elif 'plan' in flow.get('parts', []):
        return _handoff(st, flow)
    else:
        return _completion(st, flow)
    q = flow['questions'].get(qid)
    if qid == 'parts' and q:
        # Upgrade recognized opener copy; preserve other saved cards for markup repair.
        old_title = q['ask'].startswith('💰 <b>가계부를 시작해요</b>')
        missing_links = q['ask'].startswith(('💰 <b>가계부 설정을 시작해요</b>',
                                             '💰 <b>Start your ledger</b>')) and '🔗' not in q['ask']
        if old_title or missing_links or (flow.get('history_first') and q['choices'] != _parts(flow)['choices']):
            q = None
    if (qid == 'conclusion' and 'plan' in flow['parts'] and q
            and (q.get('money_flow_revision') != 2 or [c['value'] for c in q['choices']] == ['reduce'])):
        # Reissue a saved pre-upgrade summary with the approved amount choices.
        q = _conclusion(flow)
        flow['questions'][qid] = q
    if qid == 'file' and q and 'plan' in flow.get('parts', []) and any(
            choice['value'] == 'continue' for choice in q['choices']):
        q = None
    if q is None:
        if qid == 'file' and flow.get('how'):
            app = _export_app(sources.current())
            need = lookup.need(app, 'how', today) if app and lookup.app_conf(app) else None
            if need:
                # The agent looks the export steps up and records the verdict
                # (`onboarding lookup-set`), then asks `next` again.
                _save(st, flow)
                return dict(question=None, lookup=need, step=2, of=None, done=False)
        if qid == 'parts':
            q = _parts(flow)
        elif qid == 'source':
            q = _source(flow)
        elif qid == 'file':
            q = _intake(flow, today)
        elif qid == 'plan_choice':
            q = _plan_question(flow)
        elif qid == 'target':
            q = O._interactive(O._target_ask(flow))
        elif qid == 'conclusion':
            q = _conclusion(flow)
        else:
            q = _history_question(flow, qid)
        flow['questions'][qid] = q
    from .markup import onboarding_quotes
    if q.get('nonce') != flow['nonce'] or q['ask'] != onboarding_quotes(q['ask'], qid=qid):
        from . import onboarding as O
        q['nonce'] = flow['nonce']
        q['card'] = O.review_card(q)
    flow['pending'] = qid
    _save(st, flow)
    return dict(question=q, done=False, step=1 if qid == 'parts' else 2 if qid == 'file' else None, of=None)


def _completion(st, flow):
    flow['completion_prepared_at'] = datetime.datetime.now().isoformat(timespec='seconds')
    from . import reconciliation
    flow['completion_card'] = dict(text=reconciliation.closing(
        st, reconciliation.completion_time(datetime.date.fromisoformat(flow['today']))), parse_mode='HTML', buttons=[])
    _save(st, flow)
    return dict(question=None, done=True, completion_required=True)


def _handoff(st, flow):
    # Recover an older saved flow (or an import outside the averaging window)
    # to the approved file request, never to a null unimplemented response.
    flow['path'] = 'new'
    flow['needs_history_basis'] = True
    flow['answers'] = {k: v for k, v in flow['answers'].items() if k in ('parts', 'source')}
    flow['questions'] = {k: v for k, v in flow['questions'].items() if k in ('parts', 'source')}
    flow.pop('how', None)
    return next_question(st, flow, datetime.date.fromisoformat(flow['today']))


def _won(amount):
    return t('won', n=comma(amount))


def _man(amount):
    return t('man', n=man_one_decimal(amount) if text.t('ledger.money.style') == 'man'
             else _won(round(amount)))


def _whole_man(amount, part=False):
    return t('part_man' if part else 'man',
             n=man_whole(amount) if text.t('ledger.money.style') == 'man' else _won(round(amount)))


def _estimate(value, projected):
    # Reuse the existing ledger estimate marker; no new visible copy.
    return value + (' '+text.t('ledger.benefit.estimated') if projected else '')


def _pad(word, width):
    return ' ' * max(0, width - sum(2 if unicodedata.east_asian_width(c) in ('W', 'F') else 1
                                  for c in word)) + word


def _history_question(flow, qid):
    h = flow['history']
    index = 0 if qid == 'history' else int(qid.split('_')[1])-1
    months = h['months'][index*18:(index+1)*18]
    headers = [t('month'), t('income'), t('out'), t('gap')]
    table = [[r['month'], *[man_one_decimal(r[k]) if r['recorded'] else t('unrecorded_cell')
                           for k in ('income', 'out', 'gap')]] for r in months]
    width = lambda word: sum(2 if unicodedata.east_asian_width(c) in ('W', 'F') else 1 for c in word)
    widths = [max(minimum, *(width(row[n]) for row in [headers, *table]))
              for n, minimum in enumerate((7, 8, 8, 9))]
    # Owner 2026-11-07 10:28: one table, then separate monthly quote blocks.
    lines = [' '.join(_pad(v, size) for v, size in zip(headers, widths))]
    details = []
    for i, (r, cells) in enumerate(zip(months, table)):
        lines.append(_estimate(' '.join(_pad(v, size) for v, size in zip(cells, widths)), r.get('projected')))
        if r['month'] in h.get('window',{}).get('average_months',[]):
            from . import reduction_flow as RF
            amount=lambda v: _estimate(_man(v),r.get('projected'))
            details.append(RF.t('flow_month',month=int(r['month'][5:]))+'\n<blockquote>'+'\n'.join([
                RF.t('flow_money',income=amount(r['income']),out=amount(r['out'])),
                RF.t('flow_parts',**{k:amount(r['parts'][k]) for k in ('spend','loan','saving')}),
                RF.t('flow_gap',gap=amount(r['gap']),without=amount(r['gap']+r['parts']['saving']))])+'</blockquote>')
        elif i == 0:
            parts = []
            for key in ('spend', 'loan', 'saving'):
                parts.append(t('part', label=t(key), amount=_estimate(_man(r['parts'][key]), r.get('projected'))) if r['recorded']
                             else t('part', label=t(key), amount=t('unrecorded')))
            details.append('<blockquote>'+'\n'.join(parts)+'</blockquote>')
    notes = []
    for r in months:
        if not r['recorded']:
            notes.append(t('missing_month', month=r['month']))
        elif r['partial']:
            notes.append(t('current_partial' if r['month'] == flow['today'][:7] else 'partial',
                           month=r['month'], first=r['observed_from'][8:], last=r['through'][8:]))
    body = t('history_title') + '\n' + t('table_unit') + '\n<pre>\n' + '\n'.join(lines) + '\n</pre>'
    if details:
        body += '\n' + '\n\n'.join(details)
    if notes:
        body += '\n' + '\n'.join(notes)
    last = qid == _history_ids(flow)[-1]
    items = [dict(r, number=n) for n, r in enumerate(h.get('anomalies', []), 1)] if last else []
    if last:
        reads = []
        for r in h.get('salary_reads', []):
            reads.append(t('salary_read', date=dashboard.question_date(r['date'], flow['today'], short=False), amount=_won(r['amount'])))
        for r in h['debts']:
            if r.get('principal') is None or r['start_date'][:7] not in h['window']['average_months']: continue
            reads.append(t('loan_read', date=dashboard.question_date(r['start_date'], flow['today'], short=False), name=html.escape(r['name']), amount=_won(r['principal'])))
            if r.get('repayment_stream_id'):
                after = [m for m in h['months'] if m['month'] > r['start_date'][:7] and m['parts']['loan'] > 0]
                before = [m for m in h['months'] if m['month'] <= r['start_date'][:7] and m['parts']['loan'] > 0]
                if before and after and after[0]['parts']['loan'] > before[-1]['parts']['loan']:
                    reads.append(t('repayment_read', before_month=before[-1]['month'], before=_man(before[-1]['parts']['loan']),
                                   after_month=after[0]['month'], after=_man(after[0]['parts']['loan'])))
        if reads:
            body += '\n\n' + t('own_reads') + '\n' + '\n'.join(reads)
    if items:
        body += '\n\n' + t('fact_ask') + '\n' + '\n'.join(
            t('fact_item', n=r['number'], date=dashboard.question_date(r['date'], flow['today'], short=False), name=html.escape(r['name']), amount=_won(r['amount']))
            for r in items) + '\n' + t('fact_footer')
        return _q(qid, body, [('unknown', 'unknown')], free=True, items=items,
                  answer_format=dict(items=[dict(number=1, kind='debt|spend|unknown', said='owner words')]))
    body += '\n\n' + t('to_conclusion' if last else 'more_history')
    return _q(qid, body, [('conclusion_button' if last else 'next_button', 'next')], items=[])


def _monthly_flow_card(money, money_formatter=None):
    """The approved monthly rows shared by the planning conclusions."""
    whole = money_formatter or _whole_man
    installment = money_formatter or _man
    income, debt, saving = (money[k] for k in ('income','loan','saving'))
    out = money['out']
    changes = [t('flow_stopped', month=int(r['since'][5:]), name=html.escape(r['name']))
               for r in money['changes']]
    kind_keys = dict(housing='1', subscription='3', telecom='2', utility='5', insurance='4')
    examples = [text.t('ledger.onboarding.v6.dashboard.example.fixed.'+kind_keys[k])
                for k in money['fixed_kinds'] if k in kind_keys][:4]
    lines = [t('flow_in', amount=whole(income)),
             t('flow_out', amount=whole(out)),
             t('flow_fixed', amount=whole(money['fixed']),
               examples=', '+', '.join(examples) if examples else ''),
             t('flow_living', amount=whole(money['variable']))]
    for it in money['installments']:
        lines.append(t('flow_installment', amount=installment(it['amount']), name=html.escape(it['name']),
                       month=int(it['ends_on'][5:7]), day=int(it['ends_on'][8:])))
    lines += [t('flow_debt', amount=whole(debt)),
             t('flow_saving', amount=whole(saving),
               change=', '+', '.join(changes) if changes else '')]
    if money['investment']:
        lines.append(t('flow_investment', amount=whole(money['investment']),
                       names=html.escape(', '.join(money['investment_names']))))
    first, last = money['months'][0], money['months'][-1]
    period = (str(int(first[5:]))+'~'+str(int(last[5:]))) if first[:4]==last[:4] else first+'~'+last
    body = t('flow_title', period=period) + '\n<blockquote>' + '\n'.join(lines) + '</blockquote>\n'
    return body


def _conclusion(flow):
    # Purpose answers happen after the first history read. Refresh here so an
    # account the owner just marked as business is absent from this card.
    if 'plan' in flow['parts']:
        flow['history'] = _history(flow)
    h = flow['history']
    money = h.get('money_flow', {})
    recent = [r for r in h['months'] if r['recorded'] and r['month'] in money.get('months', [])]
    if ('plan' in flow['parts'] and recent
            and money['out'] > money['income']):
        gap = money['out'] - money['income']
        body = _monthly_flow_card(money) + t('flow_short', gap=_whole_man(gap))
        if h.get('business_present'):
            body += ' ' + t('business_separate')
        body += '\n\n' + t('target_ask')
        from . import onboarding as O
        choices = [dict(label=t('target_100'), value='1000000'),
                   dict(label=t('target_200'), value='2000000'),
                   dict(label=t('target_gap', gap=_whole_man(gap)), value=str(round(gap))),
                   dict(label=t('target_custom'), value='custom')]
        return O._interactive(O._q('conclusion', body, choices, free=True,
                              answer_format={'amount': 1000000}, money_flow_revision=2))
    if 'plan' in flow['parts'] and recent and money['income'] > money['out']:
        surplus = int(money['income'] - money['out'])
        saving_amount = _won if text.lang() == 'en' else lambda value: t('man', n=decimal_man(value))
        body = _monthly_flow_card(money) + t('flow_surplus', amount=saving_amount(surplus))
        body += '\n\n' + t('saving_ask')
        choices, amounts = [], set()
        for numerator in (1, 2):
            amount = int(surplus * numerator // 3 // 100000) * 100000
            if 0 < amount < surplus and amount not in amounts:
                choices.append(dict(label=t('saving_monthly', amount=saving_amount(amount)), value=str(amount)))
                amounts.add(amount)
        choices += [dict(label=t('saving_all', amount=saving_amount(surplus)), value=str(surplus)),
                    dict(label=t('target_custom'), value='custom')]
        from . import onboarding as O
        return O._interactive(O._q('conclusion', body, choices, free=True,
                              answer_format={'amount': surplus}, money_flow_revision=2,
                              saving_surplus=surplus))
    if 'plan' in flow['parts'] and recent and money['income'] == money['out']:
        variable = max(0, int(money['variable']))
        zero_money = (lambda value: _won(int(value))) if text.lang() == 'en' else text.money
        body = _monthly_flow_card(money, zero_money if text.lang() == 'en' else None)
        body += t('flow_balanced') + '\n\n' + t('zero_target_ask')
        choices, amounts = [], set()
        for percent in (10, 30, 50):
            amount = variable * percent // 100
            step = 100000 if amount >= 100000 else 10000
            amount = amount // step * step
            if 0 < amount <= variable and amount not in amounts:
                choices.append(dict(label=t('zero_monthly', amount=zero_money(amount)), value=str(amount)))
                amounts.add(amount)
        choices.append(dict(label=t('target_custom'), value='custom'))
        from . import onboarding as O
        return O._interactive(O._q('conclusion', body, choices, free=True,
                              answer_format={'amount': int(choices[0]['value']) if amounts else 0},
                              money_flow_revision=2, reduction_variable=variable))
    previous = [r for r in h['months'] if r['month'] in h['window']['average_months']]
    # Missing or partially imported months never bring older history into the
    # denominator. All three complete calendar months remain on the card.
    full = recent = previous
    lines = []
    if full:
        gap = sum(r['gap'] for r in full)
        lines.append(t('whole_gap', first=full[0]['month'], last=full[-1]['month'], n=sum(r['recorded'] for r in full),
                       gap=_man(gap), average=_man(gap/len(full))))
        lines.append(t('without_saving', gap=_man(sum(r['gap']+r['parts']['saving'] for r in full)/len(full))))
    if recent:
        lines.append(t('trend', trend=' → '.join(r['month'] + ' ' + (_man(r['gap']) if r['recorded'] else t('unrecorded')) for r in recent)))
        n = len(recent)
        lines.append(t('recent_average', n=n, income=_man(sum(r['income'] for r in recent)/n),
                       out=_man(sum(r['out'] for r in recent)/n), gap=_man(sum(r['gap'] for r in recent)/n)))
    if any(r.get('projected') for r in full):
        lines = [_estimate(line, True) for line in lines]
    if h['debts']:
        unknown = [r for r in h['debts'] if r.get('remaining') is None]
        known = [r for r in h['debts'] if r.get('remaining') is not None]
        for r in unknown:
            lines.append(ITEM+html.escape(r['name'])+'\n'+CONT+t('remaining_unknown').removeprefix('• '))
        for r in known:
            lines.append(ITEM+t('remaining_known',name=html.escape(r['name']),amount=_won(r['remaining']),date=dashboard.question_date(r['remaining_as_of'], flow['today'], short=False)))
        for r in h['debts']:
            if r.get('principal') is None or r['start_date'][:7] not in h['window']['average_months']: continue
            lines.append(t('debt_line', date=dashboard.question_date(r['start_date'], flow['today'], short=False), name=html.escape(r['name']), amount=_won(r['principal'])))
        lines.append(t('debt_caution'))
    else:
        lines.append(t('no_debt_evidence'))
    for fact in flow.get('history_facts', {}).values():
        if fact['kind'] == 'debt' and fact['date'][:7] in h['window']['average_months']:
            lines.append(t('repayment_fact', date=dashboard.question_date(fact['date'], flow['today'], short=False), name=html.escape(fact['name']), amount=_won(fact['amount'])))
    body = t('conclusion_title') + '\n<blockquote>' + '\n'.join(lines) + '</blockquote>'
    if flow.get('history_first') and 'plan_choice' not in flow['answers']:
        choices = [('next_button', 'record')]
    elif 'plan' in flow['parts']:
        body += '\n\n' + t('to_reduce')
        choices = [('reduce_button', 'reduce')]
    else:
        body += '\n\n' + t('to_record')
        choices = [('record_button', 'record')]
    return _q('conclusion', body, choices)


def _plan_question(flow):
    # Owner 2026-11-03 07:53: no loan option here; loans are handled only
    # when the user brings them up.
    choices = [('plan_reduce', 'reduce'), ('plan_saving', 'saving'), ('plan_record', 'record')]
    return _q('plan_choice', t('plan_question'), choices)


def _facts(flow, qid, value, said):
    q = flow['questions'][qid]
    items = q.get('items', [])
    if not items:
        if value != 'next':
            raise ValueError('choose next')
        return
    if value == 'unknown':
        facts = [dict(number=r['number'], kind='unknown', said=said or t('unknown')) for r in items]
    elif value.startswith('{'):
        facts = json.loads(value).get('items')
    elif len(items) == 1:
        # Only explicit borrowing/repayment words, never a person's name or
        # a vague business label. Negation needs a structured interpretation.
        loan = value == 'debt' or (re.search(t('loan_words'), value, re.I) and not re.search(t('negative_words'), value, re.I))
        facts = [dict(number=items[0]['number'], kind='debt' if loan else 'spend', said=said)]
    else:
        raise ValueError('answer numbered items with items: [{number, kind, said}]')
    if not isinstance(facts, list) or not facts:
        raise ValueError('answer at least one issued numbered item')
    seen = set()
    for fact in facts:
        if (not isinstance(fact, dict) or type(fact.get('number')) is not int
                or fact['number'] not in {r['number'] for r in items} or fact['number'] in seen
                or fact.get('kind') not in ('debt', 'spend', 'unknown')
                or not isinstance(fact.get('said'), str) or not fact['said'].strip()):
            raise ValueError('choose issued numbers once, one owner fact per item')
        seen.add(fact['number'])
    # Validate the entire batch before the first write. Unanswered rows stay
    # on this screen with their original numbers until answered or skipped.
    answered = flow.setdefault('history_facts', {})
    for fact in facts:
        item = next(r for r in items if r['number'] == fact['number'])
        data = dict(kind='debt' if fact['kind']=='debt' else 'history_fact', id=item['id'], said=fact['said'])
        if data['kind'] == 'history_fact':
            data['answer'] = fact['kind']
        kit.run('ledger-review-answer', '--month', item['date'][:7], '--data', json.dumps(data, ensure_ascii=False))
        answered[str(item['id'])] = dict(fact, date=item['date'], name=item['name'], amount=item['amount'])
    flow['history'] = _history(flow)
    remaining = [r for r in items if str(r['id']) not in answered]
    if remaining:
        body = t('fact_ask') + '\n' + '\n'.join(
            t('fact_item', n=r['number'], date=dashboard.question_date(r['date'], flow['today'], short=False), name=html.escape(r['name']), amount=_won(r['amount']))
            for r in remaining) + '\n' + t('fact_footer')
        flow['questions'][qid] = _q(qid, body, [('unknown', 'unknown')], free=True, items=remaining)
    return not remaining


def _validate(flow, qid, value, said, callback):
    from . import onboarding as O
    if qid.startswith('instrument_'):
        raise ValueError('not-asked: instrument purpose interview retired')
    q = flow['questions'].get(qid)
    if flow.get('pending') != qid or not q:
        raise ValueError('not-asked: answer only the issued history screen')
    if callback:
        match = re.fullmatch(r'opt:lr:([0-9a-f]{16}):(\d+)', callback)
        if (not match or match[1] != O._review_token(q) or int(match[2]) >= len(q['choices'])
                or q['choices'][int(match[2])]['value'] != value):
            raise ValueError('stale or invalid owner callback')
    elif not said or not said.strip():
        raise ValueError('owner provenance required')
    labels = [c['value'] for c in q['choices'] if c['label'] == value]
    return labels[0] if len(labels) == 1 else value.strip()


def answer(st, flow, qid, value, said, callback, today, tags=None):
    from . import onboarding as O
    from . import reduction_flow, known_values
    input_value = value
    issued = flow['questions'].get(qid)
    question_token = O._review_token(issued) if issued else None
    duplicate = (flow.get('answer_receipts') or {}).get(qid)
    # A stage can issue another payment; retries belong to the issued card.
    if (duplicate and question_token is not None
            and duplicate.get('question_token') == question_token
            and (duplicate.get('value') == value
                 or ((issued.get('saving_surplus') is not None or issued.get('reduction_variable') is not None)
                     and duplicate.get('input_value') == value))
            and duplicate.get('said') == said):
        return copy.deepcopy(duplicate['receipt'])
    if issued and issued.get('known') and not issued['known'].get('kit_source'):
        flow['questions'].pop(qid, None)
        fresh = reduction_flow.issue(st, flow, qid)
        return dict(recorded=None, pending=qid, next='onboarding next', **fresh)
    if reduction_flow.refresh_debts(flow):
        fresh = next_question(st, flow, today)
        return dict(recorded=None, pending=flow.get('pending'), next='onboarding next', **fresh)
    if (qid == 'conclusion' and 'plan' in flow.get('parts', []) and issued
            and issued.get('saving_surplus') is None
            and issued.get('money_flow_revision') != 2):
        # An old surplus/zero-gap "reduce" tap can arrive before the first post-upgrade
        # read. Refresh its basis too; the intake cache can predate corrections.
        current = _history(flow)
        money = current.get('money_flow', {})
        recent = any(r['recorded'] and r['month'] in money.get('months', []) for r in current['months'])
        if (money.get('income', 0) > money.get('out', 0)
                or recent and money.get('income', 0) == money.get('out', 0)):
            flow['history'] = current
            flow['questions'].pop(qid, None)
            flow.pop('resume_hold', None)
            flow.pop('resume_hold_token', None)
            fresh = next_question(st, flow, today)
            return dict(recorded=None, pending=flow.get('pending'), next='onboarding next', **fresh)
    value = _validate(flow, qid, value, said, callback)
    if qid == 'reduce_installments' or (qid in ('reduce_big','reduce_cap') and
            flow['questions'].get(qid,{}).get('big_classification_version')!=1):
        from . import reduction_flow
        fresh=reduction_flow.issue(st,flow,'reduce_cap' if qid=='reduce_installments' else qid)
        return dict(recorded=None,pending=flow['pending'],next='onboarding next',**fresh)
    if flow.get('history') and (qid == 'conclusion' or qid.startswith(('history', 'reduce_'))):
        from ledger_window import window as display_window
        saved = flow['history'].get('window', {})
        expected = display_window(today)
        if saved.get('display_revision') != 1 or saved.get('average_months') != expected['average_months']:
            # A callback can arrive before `next` after deployment. Reissue
            # the current scoped card before accepting any old selection.
            fresh = next_question(st, flow, today)
            return dict(recorded=None, pending=flow.get('pending'), next='onboarding next', **fresh)
    flow['recall_today'] = today.isoformat()
    if qid in ('reduce_refunds', 'reduce_transfers') or qid.startswith('review_deposit_'):
        from . import reduction_flow
        q = flow['questions'][qid]
        rows = q.get('items') or ([q['item']] if q.get('item') else [])
        if reduction_flow._recall_rows(rows, today.isoformat()) != rows:
            # A tap on a pre-cutoff card must not write unknown/skip facts for
            # old money. The next read refreshes its question scope.
            raise ValueError('stale or invalid owner callback')
    receipt = {}
    flow['asked_at'] = O._now()
    if qid == 'resume':
        if value not in ('continue', 'restart'):
            raise ValueError('resume answer is continue|restart')
        if value == 'restart':
            O.restart(today=today)
            return dict(recorded=qid, restart=True, remaining=None)
        flow['pending'] = flow.pop('resume_of')
        flow['resume_hold'] = flow['pending']
        flow['resume_hold_token'] = O._review_token(flow['questions'][flow['pending']])
        flow['questions'].pop('resume')
        _save(st, flow)
        return dict(recorded=qid, resumed=flow['pending'], remaining=None)
    if qid == 'parts':
        if value not in ({'record', 'past'} if flow.get('history_first') else {'record', 'past', 'plan', 'all'}):
            raise ValueError('choose record, past, plan or all')
        seed.ensure()
        st.setdefault('recording_from', today.isoformat())
        flow['path'] = 'new'
        flow['parts'] = {'record': [], 'past': ['past'], 'plan': ['plan'], 'all': ['past', 'plan']}[value]
        if flow['parts']:
            O._ensure_backfill(st)
    elif qid == 'plan_choice':
        if value not in {c['value'] for c in flow['questions'][qid]['choices']}:
            raise ValueError('choose the issued plan option')
        flow['planning'] = value
        if value in ('reduce', 'debt'):
            flow['parts'].append('plan')
            # Reuse the approved reduction target screen after this decision.
            flow['answers'].pop('conclusion', None)
            flow['questions'].pop('conclusion', None)
        elif value == 'saving':
            flow['answers']['aim'] = dict(aim='save_for')
    elif qid == 'target':
        result = O._land_saving(flow, value, said)
        flow['saving_goal'] = result
    elif qid == 'source':
        src = sources.current()
        if src['ask_app']:
            paths.conf_set(sources.SPEND_APP_KEY, sources.clean_app(value))
            chosen = 'other'
        else:
            # Exactly an issued regional app or 'none'. 'app' is never remapped to the csv app.
            if value not in src['screenshot'] + ['none']:
                raise ValueError('source must be an issued regional app or none')
            if value in src['screenshot']:
                paths.conf_set(sources.SPEND_APP_KEY, text.t('ledger.app.' + value))
            chosen = value
        kit.answer_note('source', said or value, 'source')
        flow['answers']['source'] = dict(value=value, source=chosen, said=said, callback=callback)
        flow.setdefault('owner_answers', []).append(known_values.owner_answer(qid, value, said, callback, issued))
        flow['pending'] = None
        flow['questions'].pop('file', None)
        _save(st, flow)
        return dict(recorded=qid, next='onboarding next')
    elif qid == 'file':
        issued = {c['value'] for c in flow['questions'][qid].get('choices', [])}
        if value in issued and (value == 'how' or value.startswith('file:')):
            if not _export_app(sources.current()):
                raise ValueError('there is no named export to show steps for in this region')
            if value == 'how':
                flow['how'] = True
            else:
                flow['method'] = value
                flow['how'] = False
            flow['questions'].pop('file', None)
            _save(st, flow)
            if value == 'how':
                return dict(recorded=None, how=True, next='onboarding next')
            flow['questions']['file'] = _qc('file', O._t('file.ask'), _method_buttons(flow), free=True)
            _save(st, flow)
            return dict(recorded=None, method=value, next='onboarding next')
        if value == 'existing':
            if not kit.records_info().get('count'):
                raise ValueError('no history received')
            _prepare(st, flow, today)
        elif value == 'capture' or (value.startswith('capture:') and value in issued):
            flow['method'] = 'capture:other' if value == 'capture' else value
            flow['questions']['file'] = _q('file', t('capture_ask'), _intake_choices(flow), free=True)
            _save(st, flow)
            return dict(recorded=None, capture=True, next='onboarding capture')
        elif value == 'captured':
            # Reuse the held screenshot review's consent and validation path.
            result = O.capture_done(st, flow, today)
            _prepare(st, flow, today)
            _save(st, flow)
            return result
        elif value == 'continue':
            if 'plan' in flow.get('parts', []):
                raise ValueError('send a history file or choose an issued intake option')
            flow['path'] = 'new'
        else:
            raise ValueError('send a history file or choose an issued intake option')
    elif qid.startswith(('review_bank_', 'review_balance_', 'review_funding_', 'review_deposit_')) or qid == 'review_roster':
        result = O._review_answer(qid, value, flow, flow['questions'][qid], said=said)
        if result.get('pending_input'):
            flow['questions'].pop(qid, None)
            _save(st, flow)
            return dict(result, recorded=None, pending=qid, next='onboarding next')
        if qid.startswith('review_bank_') or qid == 'review_roster':
            flow['questions'].pop('review_roster', None)
        if qid.startswith('review_balance_') and qid != 'review_balance_gap':
            missing = O._screen_accounts_not_in_roster(flow, said)
            if missing and not result.get('captured'):
                flow['balance_gap'] = dict(names=missing)
            if result.get('captured'):
                receipt.update(result)
        if qid.startswith('review_deposit_'):
            # Deposit kinds affect the month table and the reduction basis.
            _prepare(st, flow, today)
            flow['questions'] = {key: q for key, q in flow['questions'].items()
                                 if not key.startswith(('history', 'conclusion')) or key in flow['answers']}
    elif qid.startswith('history'):
        if _facts(flow, qid, value, said) is False:
            _save(st, flow)
            return dict(recorded=None, pending=qid, next='onboarding next')
    elif qid == 'conclusion':
        issued = {c['value'] for c in flow['questions'][qid]['choices']}
        if 'plan' in flow['parts'] and 'custom' in issued:
            if value == 'custom' and callback:
                # Keep the issued card while the owner types an amount.
                return dict(recorded=None, pending=qid, pending_input=True,
                            next='onboarding next')
            raw = said if value == 'custom' else value
            if callback and flow['questions'][qid].get('saving_surplus') is not None:
                raw += '\uc6d0'  # Issued numeric values are exact won, including under 10,000.
            if (raw or '').lstrip().startswith('{'):
                data = json.loads(raw)
                if not isinstance(data, dict) or type(data.get('amount')) is not int:
                    raise ValueError('monthly reduction amount must be integer won')
                raw = str(data['amount']) + '\uc6d0'
            match = re.fullmatch(r'\s*([\d,]+(?:\.\d+)?)\s*(\ub9cc(?:\s*\uc6d0)?|\uc6d0)?\s*', raw or '')
            if not match:
                raise ValueError('monthly reduction target required')
            amount = float(match.group(1).replace(',', ''))
            # Ported from 0169f240: bare 150 means 150 ten-thousands of won.
            # Explicit won and full won amounts keep their units.
            unit = match.group(2) or ''
            amount = round(amount * (10000 if unit.startswith('\ub9cc') or not unit and amount < 10000 else 1))
            if amount <= 0:
                raise ValueError('monthly reduction target must be positive')
            surplus = flow['questions'][qid].get('saving_surplus')
            if surplus is not None:
                if amount > surplus:
                    raise ValueError('monthly saving amount must not exceed surplus')
                # Reuse the deficit's goal writer and existing goal table. The
                # nonce also recovers a write completed before the state save.
                goals = kit.run('goal-list', '--domain', kit.GOAL_DOMAIN, '--json')
                existing = next((r for r in goals if
                    (json.loads(r['detail']) if isinstance(r.get('detail'), str)
                     else r.get('detail') or {}).get('saving_nonce') == flow['nonce']), None)
                provenance = said or next(c['label'] for c in flow['questions'][qid]['choices']
                                          if c['value'] == value)
                detail = dict(saving_nonce=flow['nonce'], period='month', amount=amount,
                              currency='KRW', surplus=surplus)
                gid = kit.goal_put(existing['id'] if existing else None,
                                   O._t('goal.title.saving'), None, 'up', detail,
                                   provenance, new=existing is None)
                kit.goal_amount(gid, amount, provenance)
                flow['saving_goal_id'] = gid
                flow.setdefault('targets', {})['saving_target'] = amount
                receipt['saving_goal_id'] = gid
            else:
                variable = flow['questions'][qid].get('reduction_variable')
                if variable is not None and amount > variable:
                    raise ValueError('monthly reduction amount must not exceed variable spending')
                # History corrections can exclude business money after intake.
                from . import reduction_flow
                plan = reduction_flow._read(flow)
                flow.setdefault('targets', {})['reduce_target'] = amount
                deficit = max(0, plan.get('out', 0) - plan.get('income', 0))
                flow['targets']['accepted_deficit'] = max(0, deficit - amount)
            value = str(amount)
        elif value != ('reduce' if 'plan' in flow['parts'] else 'record'):
            raise ValueError('choose the issued next step')
    elif qid.startswith('reduce_'):
        from . import reduction_flow
        result = reduction_flow.answer(flow,qid,value,said,callback=callback)
        if qid == 'reduce_subscriptions' and isinstance(result, dict) and result.get('annual_subscription'):
            flow.setdefault('owner_answers', []).append(known_values.owner_answer(qid, value, said, callback, issued))
            issued = reduction_flow.issue(st, flow, qid)
            question_token = O._review_token(flow['questions'][qid])
            answer_receipt=dict(recorded=None, pending=qid, fact=result['annual_subscription'],
                                question=issued['question'], next='onboarding next')
            flow.setdefault('answer_receipts', {})[qid]=dict(value=value,said=said,
                question_token=question_token,receipt=copy.deepcopy(answer_receipt))
            _save(st, flow)
            return answer_receipt
        if qid == 'reduce_refunds':
            receipt = {key: val for key, val in result.items() if key != 'complete'}
            if not result['complete']:
                if result['saved_refunds']:
                    flow.setdefault('owner_answers', []).append(known_values.owner_answer(qid, value, said, callback, issued))
                _save(st, flow)
                # A saved receipt frame already names what remains. Relay it
                # alone; the persisted question is for the next owner answer.
                next_action = ('Relay ack verbatim as the entire reply, without an intro, question or buttons; '
                               'wait for the owner before continuing.' if result['saved_refunds'] else 'onboarding next')
                return dict(recorded=None, pending=qid, question=flow['questions'][qid],
                            next=next_action, **receipt)
        if result is False:
            if qid == 'reduce_overview':
                result = reduction_flow.issue(st, flow, qid)
                return dict(recorded=None, pending=qid, next='onboarding next', question=result['question'])
            if qid == 'reduce_debts':
                flow.setdefault('owner_answers', []).append(known_values.owner_answer(qid, value, said, callback, issued))
                _save(st, flow)
                return dict(recorded=None, pending=qid, next='onboarding next')
            # Unrelated words/questions are context for a read-only reply,
            # never permission to apply the relay's selected budget cut.
            return dict(recorded=None,pending=qid,read_only=True,question=flow['questions'][qid],
                        context=flow['plan'].get('subscriptions',[]),next='onboarding next')
    else:
        raise ValueError('not-asked: unknown history screen')
    flow['answers'][qid] = dict(value=value, said=said, callback=callback)
    flow.pop('resume_hold', None)
    flow.pop('resume_hold_token', None)
    flow.setdefault('owner_answers', []).append(known_values.owner_answer(qid, value, said, callback, issued))
    flow['pending'] = None
    _save(st, flow)
    answer_receipt=dict(recorded=qid, next='onboarding next', **receipt)
    flow.setdefault('answer_receipts', {})[qid]=dict(value=value,said=said,input_value=input_value,
                question_token=question_token,receipt=copy.deepcopy(answer_receipt))
    _save(st, flow)
    return answer_receipt


def import_file(st, flow, path, today, password=None):
    from . import onboarding as O
    if flow.get('pending') != 'file':
        raise ValueError('not-asked: import goes with history intake')
    try:
        result = kit.ledger_import(path, password=password)
        if not result.get('events'):
            raise ValueError('the file has no payments')
    except (kit.KitError, OSError) as error:
        reason = kit.import_error(error)
        q = _intake(flow)
        q['ask'] = O._t('file.err.' + reason) + '\n\n' + q['ask']
        q['card'] = O.review_card(q)
        flow['questions']['file'] = q
        _save(st, flow)
        return dict(imported=False, error=reason, password_stdin=reason in ('password', 'password_wrong'))
    _prepare(st, flow, today)
    flow['answers']['file'] = dict(imported=result['events'])
    flow['import'] = {key: result[key] for key in ('events', 'new', 'same', 'attached', 'first', 'last')}
    flow['pending'] = None
    _save(st, flow)
    return dict(imported=True, count=result['events'], first=result['first'], last=result['last'],
                next='onboarding next')
