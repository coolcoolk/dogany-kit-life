"""Ledger onboarding entry plus the resumable legacy v6 conversation.

New starts and explicit restarts delegate to history_flow: parts, history,
month table and conclusion. Already-started v6 flows retain their aim/source,
review and completion contract. The service owns screens; the kit owns money.
"""
import datetime
import copy
import hashlib
import html
import json
import os
import re
import subprocess
import unicodedata

import sys
from pathlib import Path

# The service CLI also runs directly, outside an importable payload root.
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from service.kit_life import sub_line, bar_inline, group

from . import diagtext, guide, issuers, kit, lookup, paths, seed, sources, text, automation, dashboard, shot, balance_inventory

STATE = 'onboarding.json'
DECLINE_EXHAUSTED = 2
ONBOARDING_MARKER = '<!-- ONBOARDING_PENDING -->'
IDENTITY_CANDIDATES = (os.path.join('identity', 'hot.custom.agent.md'), 'PROFILE.md', 'AGENT.md')
ENTRY = 'ledger-setup skill (module onboarding)'
DOW = ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun')


def state_path():
    return paths.state_dir() / STATE


def load_state():
    try:
        d = json.loads(state_path().read_text(encoding='utf-8'))
        if not isinstance(d, dict):
            return {}
        # Owner 2026-11-04: retire instrument-purpose interviews, including
        # parked cards. Learned purpose facts and historical answers stay put.
        d.pop('instrument_weekly', None)
        d.pop('instrument_weekly_queue', None)
        flow = d.get('flow') or {}
        queue = flow.pop('instrument_queue', None)
        if queue is not None and 'retired_purpose_payment_ids' not in flow:
            flow['retired_purpose_payment_ids'] = sorted({
                sid for row in queue for sid in row.get('payment_ids', [])})
        questions = flow.get('questions', {})
        for qid in list(questions):
            if qid.startswith('instrument_'):
                questions.pop(qid)
        if str(flow.get('resume_of', '')).startswith('instrument_'):
            flow.pop('resume_of')
            questions.pop('resume', None)
            if flow.get('pending') == 'resume':
                flow['pending'] = None
        if str(flow.get('pending', '')).startswith('instrument_'):
            flow['pending'] = None
        if str(flow.get('resume_hold', '')).startswith('instrument_'):
            flow.pop('resume_hold')
            flow.pop('resume_hold_token', None)
        return d
    except (OSError, ValueError):
        return {}


def save_state(d):
    p = state_path()
    tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    tmp.replace(p)


def identity_state(root=None):
    root = root or paths.root()
    for rel in IDENTITY_CANDIDATES:
        p = root / rel
        if p.is_file():
            return 'pending' if ONBOARDING_MARKER in p.read_text(encoding='utf-8', errors='replace') else 'set'
    return 'absent'


def verdict():
    st = load_state()
    ident = identity_state()
    if ident != 'set':
        return 'NO-OFFER', 'identity-' + ident
    if not paths.kit_sh().is_file():
        return 'NO-OFFER', 'kit-missing'
    try:
        rules = {r['resource']: r for r in kit.rule_list()}
    except kit.KitError:
        return 'NO-OFFER', 'kit-too-old'
    rule = rules.get(kit.RESOURCE)
    if st.get('completed_at') and rule and rule['owning_module'] == kit.MODULE:
        return 'NO-OFFER', 'complete'
    if st.get('recording_from') and not st.get('completed_at'):
        return 'NO-OFFER', 'recording-active'
    try:
        if kit.manage_state() == ('off', 'declined'):
            return 'NO-OFFER', 'management-declined'
    except (kit.KitError, ValueError, KeyError, IndexError):
        pass  # a kit before the management check: unchanged behaviour
    if st.get('declines', 0) >= DECLINE_EXHAUSTED:
        return 'NO-OFFER', 'declined-exhausted'
    return 'OFFER', 'open' if not st.get('declines') else 'reoffer'


def record(event):
    st = load_state()
    key = {'offered': 'offers', 'declined': 'declines'}[event]
    st[key] = st.get(key, 0) + 1
    save_state(st)
    return st


# ---- flow v6 ----------------------------------------------------------------------------

FLOW_VERSION = 6
SCREEN_REVISION = 202610272100
V = 'ledger.onboarding.v6.'
AXIS = {'file': 'cost_completeness', 'diagnosis': 'direction'}
RESUME_HOURS = 6
STALE_DAYS = 14
PROPOSE_DAYS = 30
OFFER_MAX = 2
LEVER_BUTTONS = 3
AIMS = ('save_more', 'save_for', 'flow')
FIX_KEEP = ('must', 'reducible', 'droppable')

# Keep phase copy keys and their id families together: changing a
# draft name is deliberately a one-line data edit.
ONBOARDING_PHASES = (
    ('start', ('aim', 'source', 'file', 'confirm')),
    ('accounts', ('review_bank_*', 'review_roster', 'review_funding_*', 'review_balance_*')),
    ('income', ('review_deposit_*', 'review_income', 'review_person_*', 'review_pair_*')),
    ('spending', ('review_fixed', 'review_odd_*', 'review_living', 'review_refund_*', 'review_cards', 'review_card_*')),
    ('finish', ('review_aim', 'review_nature', 'diagnosis', 'target')),
)


def _phase_matches(qid, pattern):
    return qid.startswith(pattern[:-1]) if pattern.endswith('*') else qid == pattern


def _resume_phase_copy(flow, pending):
    ids, answers = _ids(flow), flow.get('answers', {})
    done = []
    current = next((name for name, patterns in ONBOARDING_PHASES
                    if any(_phase_matches(pending, p) for p in patterns)), ONBOARDING_PHASES[0][0])
    for name, patterns in ONBOARDING_PHASES:
        members = [qid for qid in ids if any(_phase_matches(qid, p) for p in patterns)]
        if members and all(qid in answers for qid in members):
            done.append(name)
    return '/'.join(_t('phase.' + name) for name in done), _t('phase.' + current)


def _now():
    return datetime.datetime.now().isoformat(timespec='seconds')


def _t(key, **kw):
    return text.t(V + key, **kw)


def _date_word(iso, today=None):
    d = datetime.date.fromisoformat(iso[:10])
    if d.year != (today or datetime.date.today()).year:
        return dashboard.question_date(iso, today)
    return _t('date', y=d.year, m=d.month, d=d.day)


def import_card(path, res, used_password=False):
    """The result message after a file import: a bullet list, no prose. The password lines only when one was used."""
    zipped = str(path).lower().endswith('.zip')
    lines = [_t('import.ack'),
             _t('import.file', source=_t('import.source.zip' if zipped else 'import.source.file')),
             _t('import.period', first=_date_word(res['first']), last=_date_word(res['last'])),
             _t('import.count', n=res['events'])]
    if used_password:
        lines += [_t('import.password'), _t('import.request')]
    return '\n'.join(lines)


def _flow(st):
    """The v6 flow in this state; an older flow (v1-v5) is set aside: the owner starts the one-file flow,
    rows it landed stay (the import merges with them, nothing doubles)."""
    flow = st.get('flow')
    if flow and flow.get('controller') == 'history_v1':
        return flow
    if flow and flow.get('v') != FLOW_VERSION:
        if st.get('legacy_flow'):
            st.setdefault('legacy_flows', []).append(st['legacy_flow'])
        st['legacy_flow'] = dict(flow, dropped_at=_now())
        st.pop('flow')
        return None
    if flow and flow.get('screen_revision') != SCREEN_REVISION:
        # Preserve literal purpose tokens before replacing an older screen.
        for q in flow.get('questions', {}).values():
            if q.get('id') == 'review_purpose':
                for row in q.get('card', {}).get('buttons', []):
                    for button in row:
                        match = re.fullmatch(r'opt:lr:([0-9a-f]{16}):\d+', button.get('callback_data', ''))
                        if match and match[1] not in flow.setdefault('purpose_tokens', []):
                            flow['purpose_tokens'].append(match[1])
        flow['screen_revision'] = SCREEN_REVISION
        flow['questions'].pop('review_autolinks', None)
        flow['answers'].pop('review_autolinks', None)
        if flow.get('pending') in ('review_autolinks', 'review_purpose'):
            flow['pending'] = None
        for key in list(flow):
            if key.startswith('purpose_') and key != 'purpose_tokens':
                flow.pop(key)
        flow['answers'].pop('review_purpose', None)
        flow.pop('bank_notices', None)
        flow.pop('bank_notice_screens', None)
        flow['roster_details'] = not bool(st.get('completed_at'))
        if flow['roster_details']:
            flow.pop('funding_review_complete', None)
        if flow['roster_details'] and 'review_roster' in flow.get('answers', {}):
            if 'review_income' not in flow['answers']:
                flow['pending'] = None
        # Preserve the app the owner named. File selection/import is a transport
        # choice and must never rewrite the independently answered source.
        flow['questions'] = {k: v for k, v in flow['questions'].items() if k in flow['answers']}
        if flow.get('pending') == 'resume':
            flow['pending'] = flow.pop('resume_of', None)
        if flow.get('path') == 'imported' and 'review_income' not in flow['answers']:
            flow['pending'] = None
        if flow.get('path') == 'imported' and 'review_odd' in flow and not st.get('completed_at'):
            fresh = kit.ledger_diagnosis(flow.get('diag_month'))['review']
            flow['review_odd'] = fresh['odd']
            flow['review_refunds'] = fresh.get('refund_candidates', [])
            flow['review_deposits'] = fresh.get('unmatched_deposits', [])
            if (flow.get('pending') or '').startswith(('review_odd_', 'review_refund_')):
                flow['pending'] = None
        if flow.get('pending') == 'diagnosis' and 'review_nature' not in flow['answers']:
            flow['pending'] = None
        if flow.get('pending') == 'start':
            flow['pending'] = None
    return flow


def _new_flow(today, offer=False, restart=False):
    return {'v': FLOW_VERSION, 'screen_revision': SCREEN_REVISION, 'answers': {}, 'questions': {}, 'pending': None, 'asked_at': None,
            'started_at': _now(), 'today': today.isoformat(), 'offer_lane': bool(offer), 'restart': bool(restart),
            'path': None, 'import': None, 'how': False, 'said_facts': [], 'chosen': [], 'executed': [], 'notes': []}


def _drop_stale_deposit_questions(flow, deposits):
    from . import deposit_groups
    eligible = set(deposit_groups.ids(flow, deposits))
    rows = {'review_deposit_' + str(r['id']): r for r in deposits}
    stale = set()
    changed_receipts = set()
    for key, q in flow['questions'].items():
        if not key.startswith('review_deposit_') or key in flow['answers']:
            continue
        if q.get('deposit_kind') == 'split':
            for issued in q.get('items', []):
                single = 'review_deposit_' + str(issued['id'])
                current = rows.get(single)
                if single in eligible and current and any(
                        issued.get(field) != current.get(field) for field in ('date', 'name', 'amount', 'method')):
                    changed_receipts.add(issued['id'])
        if key not in eligible:
            stale.add(key)
        elif key.startswith(deposit_groups.PREFIX):
            expected = deposit_groups.question(key, flow)
            if (q.get('items') != expected['items'] or
                    q.get('deposit_today') != flow['today'] or
                    q.get('choices') != expected['choices'] or
                    q.get('ask') != expected['ask']):
                stale.add(key)
        elif (flow.get('deposit_typing') != key and q.get('choices') != deposit_groups.choices(single=True)):
            stale.add(key)
        elif ((q.get('item') or {}).get('split_payment') or rows[key].get('split_payment')) and q.get('item') != rows[key]:
            stale.add(key)
        elif (rows[key].get('date') and rows[key]['date'][:4] != flow['today'][:4]
              and dashboard.question_date(rows[key]['date'], flow['today']) not in q.get('ask', '')):
            stale.add(key)
    for key in stale:
        flow['questions'].pop(key, None)
    # If a bank edit dissolves a split, reopen the changed receipt first.
    deposits.sort(key=lambda row: row['id'] not in changed_receipts)
    if (flow.get('pending') or '').startswith('review_deposit_') and (flow['pending'] not in eligible or flow['pending'] in stale):
        flow['pending'] = None
    if (flow.get('resume_of') or '').startswith('review_deposit_') and (flow['resume_of'] not in eligible or flow['resume_of'] in stale):
        flow.pop('resume_of', None)
        if flow.get('pending') == 'resume':
            flow['pending'] = None
            flow['questions'].pop('resume', None)


def _ids(flow):
    ids = []
    if not flow.get('proposal_only'):
        # owner 2026-10-12 20:06: the aim comes back at the start; 19:55: where he sees his money, BEFORE any file
        ids += ['aim', 'source', 'file']
    if flow.get('path') == 'imported':
        if (flow.get('import') or {}).get('question'):
            ids.append('confirm')
        if not flow.get('proposal_only'):
            if flow.get('roster_details', True):
                ids += ['review_bank_' + str(item['id']) for item in flow.get('review_banks', [])]
            ids.append('review_roster')
            ids += ['review_funding_' + str(item['id']) for item in flow.get('review_funding', [])]
            ids += ['review_balance_' + str(item['id']) for item in flow.get('review_balances', [])]
            if flow.get('balance_gap'):
                ids.append('review_balance_gap')
        from . import deposit_groups
        ids += deposit_groups.ids(flow, flow.get('review_deposits', []))
        ids.append('review_income')
        for item in ([] if flow.get('skip_remaining_received') else flow.get('review_people', [])):
            ids.append('review_person_' + str(item['id']))
            if flow.get('answers', {}).get('review_person_' + str(item['id']), {}).get('pair'):
                ids.append('review_pair_' + str(item['id']))
        ids.append('review_fixed')
        ids += ['review_odd_' + str(item['id']) for item in flow.get('review_odd', [])]
        ids.append('review_living')
        ids += ['review_refund_' + str(item['id']) for item in flow.get('review_refunds', [])]
        ids.append('review_cards')
        ids += ['review_card_' + str(n) for n, gap in enumerate(flow.get('review_card_gaps', [])) if gap.get('material')]
        if _aim(flow) == 'flow':
            ids.append('review_aim')
        ids += ['review_nature', 'diagnosis']
    if _aim(flow) == 'save_for':
        ids.append('target')
    return ids


def _aim(flow):
    return ((flow.get('answers') or {}).get('aim') or {}).get('aim')


def _pending_id(flow):
    ids = _ids(flow)
    pending = flow['pending']
    if (pending and pending not in flow['answers'] and
            (pending in ids or not pending.startswith(('review_person_', 'review_pair_')))):
        return pending
    return next((i for i in ids if i not in flow['answers']), None)


# ---- questions --------------------------------------------------------------------------

def _choice(key, value, **kw):
    return {'label': _t(key, **kw), 'value': value}


def _q(qid, ask, choices=(), free=False, **extra):
    choices = list(choices)
    ask = '\n'.join('\u2022 ' + line[2:] if line.startswith('- ') else line for line in ask.split('\n'))
    from .markup import quote_lists
    ask = quote_lists(ask, qid=qid)
    # Ledger onboarding screens carry no 'pick a button or type' footer (owner pick 2026-10-17);
    # free typing is still accepted, it is just not announced.
    return dict(extra, id=qid, ask=ask, choices=choices, free_input=bool(free), axis=AXIS.get(qid),
                button_rows=[[c] for c in choices], parse_mode='HTML')


def _opener():
    return _t('opener')


def _method_choices(flow):
    src = sources.current()
    app = flow.get('answers', {}).get('source', {}).get('source')
    choices = []
    # An export-capable app uses its file path (owner: Banksalad -> file only).
    if app in src['screenshot'] and app not in src['csv']:
        choices.append(_choice('method.capture', 'capture:' + app, app=text.t('ledger.app.' + app)))
    elif src['ask_app'] and src['spend_app']:
        choices.append(_choice('method.capture', 'capture:other', app=src['spend_app']))
    choices += [_choice('method.file', 'file:' + key, app=text.t('ledger.app.' + key)) for key in src['csv']]
    return choices


def history_period(today, months):
    until = today.replace(day=1) - datetime.timedelta(days=1)
    index = until.year * 12 + until.month - months
    since = datetime.date(index // 12, index % 12 + 1, 1)
    return since, until


def _how_text(today, flow):
    kind, app = flow['method'].split(':', 1)
    what = 'capture' if kind == 'capture' else 'how'
    v = lookup.get(app, what, today)
    name = sources.spend_app() if app == 'other' else text.t('ledger.app.' + app)
    since, until = history_period(today, 1)
    lines = [_t('method.head', app=html.escape(name or '')),
             _t('method.period.' + kind, since=since.isoformat(), until=until.isoformat())]
    if v and v.get('verdict') == 'found' and v.get('source') in lookup.SOURCES and v.get('url'):
        checked = datetime.date.fromisoformat(v['checked'])
        lines += ['<blockquote>' + '\n'.join(_t('method.step', step=html.escape(step)) for step in v['steps']) + '</blockquote>',
                  _t('method.checked' if v['source'] == 'official' else 'method.checked.posts',
                     m=checked.month, d=checked.day),
                  _t('method.link' if v['source'] == 'official' else 'method.link.posts',
                     url=html.escape(v['url'], quote=True))]
    else:
        lines.append(_t('method.generic.' + kind))
    lines += ['', _t('method.different'), _t('method.send.' + kind)]
    return '\n'.join(lines)


def _file_ask(flow, today):
    if flow.get('method'):
        kind, app = flow['method'].split(':', 1)
        return _q('file', _how_text(today, flow), free=True, method=kind, app=app,
                  capture_review='onboarding capture --rows JSON --since D --until D [--card NAME]; '
                                 'review show -> review apply -> onboarding answer file captured'
                  if kind == 'capture' else None)
    return _q('file', _t('intro') + '\n\n' + dashboard.example(), _method_choices(flow), free=True)


def _confirm_ask(flow):
    c = flow['import']['question']
    return _q('confirm', _t('confirm.ask', date=_date_word(c['date'], datetime.date.fromisoformat(flow['today'])), account=html.escape(c.get('method') or ''),
                            issuer=html.escape(c['name'].replace(_t('words.settle'), '').strip() or c['name']), amount=diagtext.money(c['amount'])),
              [_choice('confirm.company', 'company'), _choice('confirm.mine', 'mine')], free=True, spend_id=c['spend_id'])


def _diagnosis_ask(flow, prefix=None):
    month = flow.get('diag_month')
    d = kit.ledger_diagnosis(month)
    flow['diag_month'] = d['month']
    levers = d['levers']
    installments = kit.run('installment-list', '--month', d['month'], '--json')['items']
    ask = dashboard.review_screen(d, 'ending')
    budget = dashboard.budget_amount(d, installments)
    if prefix:
        ask = prefix + '\n\n' + ask
    aim = _aim(flow)
    if aim in ('save_for', 'flow'):               # the card is the answer; levers are for the save-more aim only
        choices = [{'label': dashboard.rt('button.cards'), 'value': 'ok'}]
        if budget and aim != 'flow':
            choices.insert(0, _choice('diag.budget', 'budget'))
    else:
        choices = [{'label': diagtext.lever_label(lv), 'value': lv['id']} for lv in levers[:LEVER_BUTTONS]]
        if len(levers) > 1:
            choices.append(_choice('diag.all', 'all'))
        if budget:
            choices.append(_choice('diag.budget', 'budget'))
        choices.append(_choice('diag.none', 'none'))
    if aim != 'flow' and budget:
        ask += '\n- ' + dashboard.rt('budget', amount=diagtext.money(budget))
    return _q('diagnosis', ask, choices, free=True, month=d['month'], budget=budget,
              levers=[{'id': lv['id'], 'rule': lv['rule'], 'title': diagtext.lever_title(lv), 'monthly': lv['monthly'],
                       'last_resort': bool(lv.get('last_resort'))} for lv in levers])


def _pair_label(candidate, today=None):
    """Thin token: M/D + first eight Unicode characters, ellipsis if cut.

    NFC composes Korean syllables before slicing; never slice UTF-8 bytes.
    Even eight full-width characters plus date, ellipsis and the bridge's
    numeric prefix fit its 31-unit budget (verified by the installed bridge
    in tools/ledger-review-card-replay.py). Full facts stay in the body.
    """
    name = ' '.join(unicodedata.normalize('NFC', candidate['display_name']).split())
    short = name[:8] + ('\u2026' if len(name) > 8 else '')
    date = datetime.date.fromisoformat(candidate['date'])
    if candidate.get('parts'):
        first = candidate['parts'][0]['display_name']
        return dashboard.rt('pair.group_label', date=dashboard.question_date(date, today),
                            name=first[:4], count=len(candidate['parts'])-1)
    return '%s %s' % (dashboard.question_date(date, today), short)


def _generic_account(item):
    return item['kind'] == 'account' and dashboard.generic_account_name(item['name']) is not None


def _resolve_banks(flow, d, roster_only=False):
    """Resolve before issuing any bank screen, including an already pending one."""
    changed = False
    names = {m['name'] for group in d['review']['roster']['groups'].values() for m in group}
    for target in d['review']['roster']['groups']['account']:
        if not _generic_account(target):
            continue
        qid = 'review_bank_' + str(target['id'])
        if qid in flow['answers']:
            continue
        evidence = kit.run('bank-evidence', target['id'])
        flow.setdefault('bank_evidence', {})[str(target['id'])] = evidence
        flow['questions'].pop(qid, None)
        if not evidence['bank']:
            continue
        # Naming does not establish that two accounts at one bank are the same.
        base = evidence['bank'] + ' ' + dashboard.generic_account_name(target['name'])
        name, ordinal = base, 2
        while name in names:
            name = base + ' (%s)' % ordinal
            ordinal += 1
        names.add(name)
        result = kit.run('ledger-review-answer', '--month', d['month'], '--data',
                         json.dumps(dict(kind='roster', corrections=[dict(id=target['id'], name=name)])))
        clue = '·'.join(dict.fromkeys(dashboard.rt('roster.bank_hint_salary') if p['clue'] == 'salary' else p['clue']
                                     for p in evidence['proof']))
        flow['answers'][qid] = dict(inferred=True, bank=evidence['bank'], evidence=evidence['proof'], result=result,
                                   resolved=dict(id=target['id'], kind='bank', name=dashboard.generic_account_name(target['name']),
                                                 source=evidence['bank'], clue=clue))
        changed = True
    if changed and roster_only:
        return kit.ledger_diagnosis(d['month'], roster_only=True)
    return kit.ledger_diagnosis(d['month']) if changed else d


def _bank_ask(qid, flow, d):
    target = next(m for m in flow['review_banks'] if qid == 'review_bank_' + str(m['id']))
    evidence = flow.get('bank_evidence', {}).get(str(target['id'])) or kit.run('bank-evidence', target['id'])
    name = html.escape(dashboard.generic_account_name(target['name']) or target['name'])
    yn = lambda yes: dashboard.rt('roster.clue_yes' if yes else 'roster.clue_no')
    lines = [dashboard.rt('roster.bank_clues', name=name, count=evidence['row_count'])]
    if evidence['balance']:
        lines.append(dashboard.rt('roster.bank_balance', amount=diagtext.money(evidence['balance']['amount']),
                                  date=dashboard.question_date(evidence['balance']['as_of'][:10], flow['today'], short=False)))
    lines.append(dashboard.rt('roster.bank_activity', salary=yn(evidence['salary']),
                              fixed_in=yn(evidence['fixed_in']), fixed_out=yn(evidence['fixed_out'])))
    for row in evidence['recent']:
        lines.append(dashboard.rt('roster.bank_row', date=dashboard.question_date(row['date'], flow['today'], short=False), name=html.escape(row['name']),
                                  amount=diagtext.money(row['amount'])))
    choices = [dict(label=dashboard.rt('roster.guess', name=bank), value=bank, guessed=True)
               for bank in evidence['guesses']]
    if evidence['proof'] or evidence['hints']:
        clues = list(dict.fromkeys(p['clue'] for p in evidence['proof'] + evidence['hints']))
        clues = [dashboard.rt('roster.bank_hint_' + c) if c in ('interest_cadence','salary') else c for c in clues]
        lines.append(dashboard.rt('roster.bank_evidence', evidence=html.escape(' / '.join(clues))))
    lines.append(dashboard.rt('roster.bank_ask', name=name))
    for item in d['review']['roster']['groups']['account']:
        if item['id'] != target['id'] and item['kind'] == target['kind'] and not _generic_account(item):
            choices.append(dict(label=dashboard.rt('roster.bank_same', name=dashboard.roster_display(item['name'])),
                                value='merge:' + str(item['id'])))
    choices.append(dict(label=dashboard.rt('roster.unknown'), value='unknown'))
    return _interactive(_q(qid, '\n'.join(lines), choices, free=True, target=target, evidence=evidence, month=d['month']))


def _resolve_funding(flow, d, roster_only=False):
    changed = False
    roster = d['review']['roster']
    edges = kit.run('funding-get')
    linked = {e['wallet_id'] for e in edges}
    direct_names = {link['wallet'] for link in roster['links'] if link['origin'] != 'card-bill'}
    linked.update(card['id'] for card in roster['groups']['card'] if card['name'] in direct_names)
    for target in roster['groups']['card'] + roster['groups']['wallet']:
        qid = 'review_funding_' + str(target['id'])
        if qid in flow['answers'] or target['id'] in linked:
            continue
        evidence = kit.run('funding-evidence', target['id'])
        if not evidence['source_id']:
            continue
        result = kit.run('funding-set', target['id'], evidence['source_id'], '--origin', 'learned')
        flow['answers'][qid] = dict(inferred=True, evidence=evidence['proof'], result=result,
                                   resolved=dict(id=target['id'], kind='wallet' if target['kind'] == 'easy_pay' else 'card', name=target['name'], source=evidence['source']))
        flow['questions'].pop(qid, None)
        changed = True
    if changed and roster_only:
        flow['questions'].pop('review_roster', None)
        return kit.ledger_diagnosis(d['month'], roster_only=True)
    return kit.ledger_diagnosis(d['month']) if changed else d


def _funding_targets(roster):
    """Unlinked wallets always ask; cards ask only about conflicting evidence."""
    targets = []
    for item in roster['unresolved']:
        if item['kind'] not in ('credit_card', 'corporate_card', 'debit_card', 'easy_pay'):
            continue
        evidence = kit.run('funding-evidence', item['id'])
        if item['kind'] == 'easy_pay' or evidence.get('conflict'):
            targets.append(dict(item, funding_evidence=evidence))
    return targets


def _balance_targets(flow, roster):
    """Pick at most three material accounts from onboarding-imported evidence only."""
    ranked = []
    for item in roster['groups']['account']:
        if item['kind'] != 'account':
            continue
        evidence = kit.run('bank-evidence', item['id'])
        score = (bool(evidence.get('salary')), evidence.get('row_count', 0))
        ranked.append((score, bool(evidence.get('fixed_out')), item))
    ranked.sort(key=lambda row: row[0], reverse=True)
    chosen = []
    if ranked:
        chosen.append(ranked[0][2])
    fixed = sorted((row for row in ranked if row[1]), key=lambda row: row[0], reverse=True)
    if fixed and fixed[0][2]['id'] not in {item['id'] for item in chosen}:
        chosen.append(fixed[0][2])
    for _score, _fixed, item in ranked:
        if len(chosen) >= 3:
            break
        if item['id'] not in {value['id'] for value in chosen}:
            chosen.append(item)
    return chosen


def _balance_aggregator(flow):
    """Honor opt-out and current app configuration before retained answers."""
    answer = (flow.get('answers') or {}).get('source')
    aggregators = sources.current().get('aggregators') or []
    if answer and answer.get('source') == 'none':
        return None
    configured = sources.spend_app()
    if configured:
        return next((app for app in sources.current().get('aggregators', [])
                     if configured.casefold() in (app.casefold(), text.t('ledger.app.' + app).casefold(),
                         text._bundle(text.DEFAULT_LANG).get('ledger.app.' + app, '').casefold())), None)
    if answer:
        app = answer.get('source')
        return app if app in aggregators else None
    origins = kit.records_info().get('import_origins', [])
    if len(origins) == 1 and origins[0] in aggregators:
        app = origins[0]
        return app
    return None


def _balance_source_known(flow):
    return bool(_balance_aggregator(flow) or sources.spend_app()
                or (flow.get('answers') or {}).get('source'))


def _balance_plan(flow, roster):
    """Per-account branch: up to three material accounts, one question each. Aggregator branch: one
    capture question; its answer may carry every account, so the accounts stay listed in balance_all."""
    chosen = _balance_targets(flow, roster)
    # Ranking establishes materiality; stored readings establish whether a
    # question is needed. Reuse the balance module's existing freshness rule.
    readings = kit.run('balance-show', '--today', flow['today'], '--json')
    fresh = {r['method_id'] for r in readings if r.get('state') == 'fresh'}
    chosen = [item for item in chosen if item['id'] not in fresh]
    flow['balance_all'] = [dict(id=item['id'], name=item['name']) for item in roster['groups']['account']
                           if item['kind'] == 'account']
    return chosen[:1] if _balance_aggregator(flow) else chosen


def _balance_ask(qid, flow, d):
    target = next(item for item in flow['review_balances'] if qid == 'review_balance_' + str(item['id']))
    name = dashboard.roster_display(target['name'])
    app = _balance_aggregator(flow)
    if app:
        ask = dashboard.rt('balance.ask_aggregator', app=text.t('ledger.app.' + app), name=html.escape(name))
    else:
        ask = dashboard.rt('balance.ask', name=html.escape(name), i=flow['review_balances'].index(target) + 1,
                           n=len(flow['review_balances']))
    return _q(qid, ask, [dict(label=dashboard.rt('balance.skip'), value='skip')],
              free=True, target=target, month=d['month'], aggregator=app,
              accounts=flow.get('balance_all', []),
              answer_format={'<account id or recognized name>': 'whole KRW amount'})


def balance_answer(value, flow, q, said=None):
    """Record all unambiguously recognized capture readings in either branch."""
    if value == 'skip':
        return dict(skipped=True)
    if value.lstrip().startswith('{'):
        readings = json.loads(value)
        if not isinstance(readings, dict) or not readings:
            raise ValueError('balance capture answer: an object of account ids or names to whole KRW amounts')
        methods = kit.pay_methods(canonical=True)
        by_id = {str(m['id']): m for m in methods}
        named, unknown = balance_inventory.match(readings, methods)
        parsed = {}
        for label, amount in readings.items():
            target = by_id.get(label) if label.isdecimal() else named.get(label)
            if target:
                target = by_id.get(str(target.get('canonical_id')), target)
            if not target or target['kind'] not in balance_inventory.BALANCE_KINDS:
                if label.isdecimal():
                    raise ValueError('balance capture answer: unknown account id')
                continue
            mid = str(target['id'])
            amount = str(int(_balance_amount(amount)))
            if abs(int(amount)) > 10 ** 12:
                raise ValueError('balance answer: amount out of range')
            if mid in parsed and parsed[mid] != amount:
                raise ValueError('balance capture answer: conflicting readings for one account')
            parsed[mid] = amount
        unknown = [n for n in unknown if not n.isdecimal()]
        unknown += _screen_accounts_not_in_roster(flow, said)
        unknown = list(dict.fromkeys(unknown))
        # Validate the complete capture before the first write. Unknown names
        # remain unknown; they do not create accounts or zero readings.
        results = {mid: kit.run('balance-set', mid, amount, '--as-of', flow['today'], '--json')
                   for mid, amount in parsed.items()}
        rows = []
        for mid, amount in parsed.items():
            rows.append(text.t('ledger.balance.say.reading', name=by_id[mid]['name'],
                        amount=diagtext.money(int(amount)), date=flow['today'],
                        prov=text.t('ledger.balance.prov.hand')))
            key = 'review_balance_' + mid
            flow.setdefault('answers', {})[key] = dict(ok=True, result=results[mid])
        if unknown:
            rows += [text.t('contract.unknown'), *unknown]
        say = '\n'.join(rows)
        return dict(ok=True, captured=True, results=results, unknown=unknown, say=say,
                    card=dict(text=html.escape(say), parse_mode='HTML', buttons=[]),
                    **({'pending_input': True} if str(q['target']['id']) not in parsed else {}))
    result = kit.run('balance-set', q['target']['id'], _balance_amount(value), '--as-of', flow['today'], '--json')
    return dict(ok=True, result=result)


_balance_answer = balance_answer


def _balance_amount(value):
    amount = re.sub(r'[,\uc6d0\s]', '', str(value))
    if not re.fullmatch(r'-?\d+', amount):
        raise ValueError('balance answer: give a whole KRW amount or skip')
    return amount


def _screen_accounts_not_in_roster(flow, said):
    """Read an explicit capture inventory without guessing account identity.

    Relays describe captures as ``화면 계좌: A, B`` (or ``screen accounts:``).
    Only that labelled list is consumed; arbitrary balance prose is not mined.
    """
    if not said:
        return []
    match = re.search(_t('balance_gap.inventory_pattern'), said, re.I)
    if not match:
        return []
    named = [v.strip() for v in re.split(r'[,，·|]', match.group(1)) if v.strip()]
    # balance_all is the issued reading scope, not the roster: savings and
    # hidden small balances still exist, and resumed flows can hold old names.
    return balance_inventory.missing(named, kit.pay_methods(canonical=True))


def _interactive(q, rows=None):
    if rows is not None:
        q['button_rows'] = rows
    q['card'] = review_card(q)
    return q


PURPOSE_TAGS = ('월급·고정비', '생활비', '저축', '사업', '안 따짐')


def _purpose_card(flow, q):
    q = _interactive(q)
    tokens = flow.setdefault('purpose_tokens', [])
    token = _review_token(q)
    if token not in tokens:
        tokens.append(token)
    flow['purpose_tokens'] = tokens[-256:]
    return q


def _purpose_items(flow):
    if 'purpose_items' not in flow:
        data = kit.run('purpose-evidence', '--today', flow['today'])
        flow['purpose_scope'] = data
        flow['purpose_items'] = [m for m in data['items'] if m['qualifies']]
    return flow['purpose_items']


def _purpose_ask(flow, d):
    items = _purpose_items(flow)
    index = flow.get('purpose_index', 0)
    if index >= len(items):
        raise ValueError('purpose review already complete')
    item = items[index]
    editing = flow.get('purpose_editing', False) or not item['guess']['tags']
    if editing:
        choices = [dict(label=dashboard.rt('purpose.tag.' + str(n)), value='tag:' + str(n),
                        selected=tag in flow.get('purpose_selected', [])) for n, tag in enumerate(PURPOSE_TAGS)]
        choices.append(dict(label=dashboard.rt('purpose.done'), value='done'))
        name = dashboard.roster_display(item['name'])
        ask = dashboard.rt('purpose.edit', name=html.escape(name), topic=dashboard.topic_particle(name))
    else:
        ask = dashboard.purpose_question(item, index + 1, len(items), today=flow['today'])
        choices = [dict(label=dashboard.rt('purpose.yes'), value='yes'),
                   dict(label=dashboard.rt('purpose.change'), value='change')]
    q = _q('review_purpose', ask, choices, item=item, index=index, total=len(items),
           editing=editing, selected=flow.get('purpose_selected', []), multi_select=editing, month=d['month'])
    q['button_rows'] = [choices[:2]] if not editing else [choices[:2], choices[2:4], choices[4:5], choices[5:]]
    return _purpose_card(flow, q)


def _funding_ask(qid, flow, d):
    target = next(item for item in flow['review_funding'] if qid == 'review_funding_' + str(item['id']))
    wallet = target['kind'] == 'easy_pay'
    groups = d['review']['roster']['groups']
    accounts = [m for m in groups['account'] if m['kind'] == 'account' and not _generic_account(m)]
    accounts.sort(key=lambda m: m['id'] != target.get('guess_id'))
    candidates = (groups['card'] if wallet else []) + accounts
    sources = []
    for item in candidates:
        label = dashboard.roster_display(item.get('display_name', item['name']))
        if not wallet and item['id'] == target.get('guess_id'):
            label = dashboard.rt('roster.guess', name=label)
        sources.append(dict(label=label, value=str(item['id']), source=item['name'],
                            source_kind='card' if item in groups['card'] else 'account',
                            guessed=not wallet and item['id'] == target.get('guess_id')))
    ask = dashboard.funding_screen(target, sources, wallet=wallet)
    # Sources are data for resolving owner words, never relay buttons.
    q = _q(qid, ask, [], target=target, month=d['month'], sources=sources,
           numbered=True, free=True)
    return _interactive(q, rows=[])


def _purpose_answer(value, flow, q):
    item = q['item']
    if value == 'skip':  # Explicit owner text only; no skip button or automatic answer.
        return dict(ok=True, skipped=True)
    if not q['editing']:
        if value == 'change':
            flow['purpose_editing'] = True
            flow['purpose_selected'] = []
            return dict(pending_input=True)
        if value != 'yes':
            raise ValueError('choose an issued purpose answer')
        tags = item['guess']['tags']
    else:
        if re.fullmatch(r'tag:[0-4]', value):
            tag = PURPOSE_TAGS[int(value.split(':')[1])]
            tags = list(flow.get('purpose_selected', []))
            if tag in tags:
                tags.remove(tag)
            elif tag == '안 따짐':
                tags = [tag]
            else:
                tags = [t for t in tags if t != '안 따짐'] + [tag]
            flow['purpose_selected'] = tags
            return dict(pending_input=True)
        if value != 'done' or not flow.get('purpose_selected'):
            raise ValueError('choose at least one purpose, then done')
        tags = flow['purpose_selected']
    # The legacy fifth button remains valid; persist its current fixed label.
    if PURPOSE_TAGS[-1] in tags:
        mixed = kit.run('purpose-get', item['id'])['normalized']['vocabulary'][-1]
        tags = [mixed if tag == PURPOSE_TAGS[-1] else tag for tag in tags]
    result = kit.run('purpose-set', item['id'], '--tags', ','.join(tags), '--origin', 'owner-stated')
    flow.setdefault('purpose_answers', {})[str(item['id'])] = result
    # Refresh only future items; an issued unanswered question stays stable.
    refreshed = {m['id']: m for m in kit.run('purpose-evidence', '--today', flow['today'])['items']}
    flow['purpose_items'] = [m if n <= q['index'] else refreshed.get(m['id'], m)
                             for n, m in enumerate(_purpose_items(flow))]
    flow['purpose_index'] = q['index'] + 1
    flow.pop('purpose_editing', None)
    flow.pop('purpose_selected', None)
    if flow['purpose_index'] < len(_purpose_items(flow)):
        return dict(pending_input=True, result=result)
    return dict(ok=True, results=flow['purpose_answers'])


def _roster_answer(value, flow, q):
    if value == 'ok':
        return dict(kind='roster')
    if value.startswith('{'):
        data = dict(kind='roster', **json.loads(value))
        for correction in data.get('corrections', []):
            if 'number' in correction:
                n = correction.pop('number')
                if not isinstance(n, int) or not 1 <= n <= len(q['items']):
                    raise ValueError('choose a roster number')
                correction['id'] = q['items'][n-1]['id']
        return data
    match = re.match(r'^(\d+)\s*(?:번(?:은|는)?)?\s*[.:→]?\s*(.+)$', value)
    if not match or not 1 <= int(match[1]) <= len(q['items']):
        raise ValueError('give a roster number and correction')
    item = q['items'][int(match[1])-1]
    name = match[2].strip()
    if item['kind'] in ('credit_card', 'corporate_card', 'debit_card', 'easy_pay'):
        candidates = [m for m in q['items'] if m['id'] != item['id'] and
                      (m['name'] == name or dashboard.roster_display(m['name']) == name)]
        if len(candidates) != 1:
            raise ValueError('give one exact payment source name from the roster')
        result = kit.run('funding-set', item['id'], candidates[0]['id'], '--origin', 'owner-stated')
    else:
        result = kit.run('ledger-review-answer', '--month', q['month'], '--data',
                         json.dumps(dict(kind='roster', corrections=[dict(id=item['id'], name=name)])))
        rec = flow['answers'].get('review_bank_' + str(item['id']), {})
        rec['corrected'] = True
    return dict(pending_input=True, corrected=item['id'], result=result)


def _funding_words(value, q):
    """Resolve displayed numbers or exact names before making any writes."""
    grammar = lambda key: dashboard.rt('funding.parse.' + key)
    if value in grammar('unknown').split('|'):
        return [], None
    sources = q['sources']
    # Longest names first: a source may contain another source's name or digits.
    names = {}
    for n, c in enumerate(sources, 1):
        for name in (c['source'], c['label']):
            names.setdefault(name.casefold(), set()).add(n)
    pattern = '|'.join(re.escape(name) for name in sorted(names, key=len, reverse=True))
    def replace_name(match):
        numbers = names[match[0].casefold()]
        if len(numbers) != 1:
            raise ValueError('funding name is ambiguous; use its displayed number')
        return ' ' + str(next(iter(numbers))) + ' '
    raw = re.sub(pattern, replace_name, value, flags=re.I) if pattern else value
    selected, primary = [], None
    for part in re.split(grammar('separator'), raw):
        part = part.strip()
        if not part:
            continue
        main = bool(re.search(grammar('main'), part, re.I))
        part = re.sub(grammar('usage'), '', part, flags=re.I).strip()
        if not re.fullmatch(grammar('numbers'), part):
            raise ValueError('funding answer: give numbers or names from the issued list')
        numbers = [int(n) for n in re.findall(r'\d+', part)]
        if main and len(numbers) != 1:
            raise ValueError('mark exactly one primary funding source')
        for n in numbers:
            if not 1 <= n <= len(sources):
                raise ValueError('choose numbers from the issued funding list')
            c = sources[n-1]
            if c not in selected:
                selected.append(c)
            if main:
                if primary is not None and primary != c['value']:
                    raise ValueError('mark exactly one primary funding source')
                primary = c['value']
    if not selected:
        raise ValueError('give a funding source or say unknown')
    if q['target']['kind'] != 'easy_pay' and len(selected) != 1:
        raise ValueError('give one paying account for this card')
    return selected, primary or (selected[0]['value'] if len(selected) == 1 else None)


def _funding_answer(value, flow, q, said):
    sources, primary = _funding_words(value, q)
    if not sources:
        return dict(skipped=True, status='unconfirmed' if q['target']['kind'] == 'easy_pay' else 'unlinked')
    # Main last: earlier successful writes remain safe to retry after a failure.
    sources.sort(key=lambda c: c['value'] == primary)
    results = [kit.run('funding-set', q['target']['id'], c['value'], '--sub', c['source_kind'],
                       *(['--main'] if c['value'] == primary else []), '--origin', 'owner-stated',
                       '--said', said) for c in sources]
    flow.get('funding_selected', {}).pop(q['id'], None)
    flow.pop('funding_main', None)
    names = ', '.join(c['source'] for c in sorted(sources, key=lambda c: c['value'] != primary))
    say = dashboard.rt('funding.connected', name=html.escape(q['target']['name']),
                       sources=html.escape(names),
                       direction=text.josa(names, 'ro')[len(names):])
    flow.setdefault('funding_confirmations', []).append(say)
    return dict(ok=True, results=results, said=said, say=say)


def _review_ask(qid, flow, d=None):
    if d is None:
        d = kit.ledger_diagnosis(flow.get('diag_month'))
    flow['diag_month'] = d['month']
    if d['review'].get('receipt_allocations') or d['review'].get('unallocated_receipts'):
        flow['has_allocations'] = True
    kind = qid.split('_')[1]
    if kind in ('aim', 'nature'):
        return _q(qid, dashboard.review_screen(d, kind),
                  [dict(label=dashboard.rt('button.cards'), value='ok')], month=d['month'])
    if kind == 'purpose':
        return _purpose_ask(flow, d)
    if kind == 'bank':
        return _bank_ask(qid, flow, d)
    if kind == 'funding':
        return _funding_ask(qid, flow, d)
    if kind == 'balance':
        if qid == 'review_balance_gap':
            gap = flow['balance_gap']
            return _q(qid, _t('balance_gap.ask', names=', '.join(html.escape(n) for n in gap['names'])),
                      [_choice('balance_gap.add', 'add'), _choice('balance_gap.exclude', 'exclude')], names=gap['names'])
        return _balance_ask(qid, flow, d)
    if kind == 'deposit':
        from . import deposit_groups
        if qid.startswith(deposit_groups.PREFIX):
            return deposit_groups.question(qid, flow)
        sid = int(qid.rsplit('_', 1)[1])
        item = next(v for v in d['review']['unmatched_deposits'] if v['id'] == sid)
        typing = flow.get('deposit_typing') == qid
        choices = [] if typing else deposit_groups.choices(single=True)
        ask = dashboard.unmatched_deposit_question(item, today=flow['today'])
        if not item.get('counterparty') and not typing:
            ask += '\n' + dashboard.rt('refund.ask')
        if typing:
            ask += '\n' + text.t('ledger.unmatched_deposit.input')
        else:
            ask += '\n' + deposit_groups.hint()
        return _interactive(_q(qid, ask, choices, free=True, item=item, month=d['month']))
    if kind == 'roster':
        flow.pop('funding_review_complete', None)
        d['review']['roster']['funding'] = kit.run('funding-get')
        flow['review_funding'] = _funding_targets(d['review']['roster'])
        d['review']['roster']['funding_questions'] = [m['id'] for m in flow['review_funding']
            if 'review_funding_' + str(m['id']) not in flow['answers']]
        flow['review_balances'] = _balance_plan(flow, d['review']['roster'])
        flow['review_banks'] = [m for m in d['review']['roster']['groups']['account'] if _generic_account(m)]
        d['review']['roster']['evidence'] = {
            str(m['id']): '·'.join(dict.fromkeys(p['clue'] for p in rec.get('evidence', [])))
            for m in d['review']['roster']['groups']['account']
            for rec in [flow['answers'].get('review_bank_' + str(m['id']), {})]
            if rec.get('inferred') and not rec.get('corrected') and rec.get('evidence')}
    item = None
    choices = [{'label': dashboard.rt('button.cards'), 'value': 'ok'}]
    if kind == 'card':
        item = flow['review_card_gaps'][int(qid.rsplit('_',1)[1])]
    if kind in ('person', 'pair', 'odd', 'refund'):
        sid = int(qid.rsplit('_', 1)[1])
        item = next(v for v in flow['review_refunds' if kind == 'refund' else 'review_odd' if kind == 'odd' else 'review_people'] if v['id'] == sid)
        if kind in ('person', 'pair'):
            item = next((v for v in d['review']['people'] if v['id'] == sid), item)
    if kind == 'person':
        choices = [{'label': dashboard.rt('yes'), 'value': 'yes'}, {'label': dashboard.rt('no'), 'value': 'no'}]
    elif kind == 'pair':
        def detail(v):
            if v.get('parts'):
                parts = ' + '.join(dashboard.question_date(p['date'], flow['today'], short=False) + (' ' + p['at_time'] if p.get('at_time') else '') + ' ' +
                                   p['display_name'] + ' ' + diagtext.money(p['amount']) for p in v['parts'])
                return dashboard.rt('pair.group', parts=parts, total=diagtext.money(v['amount']))
            return dashboard.question_date(v['date'], flow['today'], short=False) + ' ' + v['display_name'] + ' ' + diagtext.money(v['amount'])
        choices = [{'label': _pair_label(v, flow['today']),
                    'detail': detail(v),
                    'recommended': v['recommended'], 'share_fit': v['share_fit'],
                    **({'allocations': v['allocations']} if v.get('parts') else {}),
                    'value': str(v['id'])} for v in d['review']['pair_candidates'].get(str(item['id']), [])]
        recovery = [{'label': dashboard.rt('pair.none'), 'value': 'none'},
                    {'label': text.t('retro.daily.btn.say'), 'value': 'type'}]
        choices += recovery if choices else list(reversed(recovery))
        if flow.get('pair_typing') == qid:
            choices = []
        choices.append({'label': dashboard.rt('pair.cash'), 'value': 'cash'})
    elif kind == 'odd':
        if item['odd'] == 'cycle':
            choices = [{'label': dashboard.rt('cycle.' + cycle), 'value': cycle}
                       for cycle in d['review']['cycles']]
        else:
            choices.insert(0, {'label': dashboard.rt('choice.transit'), 'value': 'transit'})
    elif kind == 'refund':
        choices = [{'label': dashboard.rt('refund.' + key), 'value': key} for key in ('insurance', 'refund', 'unknown')]
    elif kind == 'card':
        choices = [dict(label=dashboard.rt('installment.' + value), value=value)
                   for value in ('purchases', 'type', 'capture', 'skip')]
    if kind in ('roster', 'living', 'cards'):
        choices = [{'label': dashboard.rt('button.' + kind), 'value': 'ok'}]
    if kind in ('person', 'pair'):
        choices += [{'label': dashboard.rt('received.skip'), 'value': 'skip'},
                    {'label': dashboard.rt('received.skip_all'), 'value': 'skip_all'}]
    ask = dashboard.review_screen(d, kind, item, today=flow['today'])
    from . import end_dates
    if kind=='odd' and end_dates.enabled() and item['id'] not in flow.get('end_asked_fixed',[]):
        stream=next((r for r in kit.run('stream-list')['streams'] if r['id']==item.get('stream_id')),None)
        if not stream or not stream.get('end_asked'): ask += '\n'+end_dates.t('end_ask')
    owner_said = None
    if kind == 'pair':
        owner_said = flow['answers'].get('review_person_' + str(item['id']), {}).get('said')
        if owner_said:
            # These words already describe the payment. Give the relay that
            # evidence instead of asking the owner to name a merchant again.
            ask = dashboard.rt('pair.said', said=html.escape(owner_said))
    recommendation = next((dict(**({'expense_ids': [a['expense_id'] for a in c['allocations']]} if c.get('allocations')
                                   else {'spend_id': int(c['value'])}), reason=c['share_fit'])
                           for c in choices if c.get('recommended')), None)
    if recommendation and paths.conf()['LEDGER_PAIR_RECOMMENDATION_COPY'] == 'on':
        candidate = next(c for c in choices if c.get('recommended'))
        reason = recommendation['reason']
        # Draft owner-visible copy: deliberately unwired by default.
        ask += '\n' + html.escape(dashboard.rt('pair.recommendation',
            expense=candidate['detail'], denominator=reason['denominator'],
            received=diagtext.money(reason['received_amount'])))
    if kind == 'pair' and choices:
        ask += '\n\n' + '\n'.join('%d. %s' % (n, html.escape(c.get('detail', c['label'])))
                                   for n, c in enumerate(choices, 1))
    if kind == 'card' and flow.get('installment_intake') == qid:
        fields = flow.get('installment_fields', [])
        ask += '\n' + (dashboard.rt('installment.missing', fields=', '.join(
            dashboard.rt('installment.field.' + key) for key in fields)) if fields else dashboard.rt('installment.input'))
    if kind == 'card' and flow.get('installment_capture_pending') == qid:
        ask += '\n• ' + dashboard.rt('capture.wait')
    q = _q(qid, ask, choices, free=True, month=d['month'], item=item,
              card=item['card'] if kind == 'card' else None,
              review=d['review'] if kind == 'living' else None,
              answer_format={'one_off': ['spend_id'], 'subscriptions': ['spend_id']} if kind == 'living' else None)
    if kind in ('person', 'pair'):
        if owner_said:
            q['owner_said'] = owner_said
        # Both literal rows and the generic OPTIONS renderer use thin labels.
        # Pair facts live in the numbered body list, aligned with either path.
        q['card'] = review_card(q)
        if kind == 'pair':
            q['recommendation'] = recommendation
            q['card']['recommendation'] = recommendation
    if kind == 'roster':
        q['items'] = [m for group in ('card', 'account', 'saving', 'wallet')
                      for m in d['review']['roster']['groups'][group]]
    return q


def _review_token(q):
    identity = q['quote_source'] if q['ask'] == q.get('quote_rendered') else q['ask']
    # A merchant label refresh retains the issued action. Other body edits,
    # changed choices and new nonces still invalidate the callback.
    if q['ask'] == q.get('merchant_name_rendered'):
        identity = q['merchant_name_identity']
    payload = [SCREEN_REVISION, q['id'], identity, q['choices']]
    if q.get('nonce'):
        payload.append(q['nonce'])
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def review_card(q):
    from .markup import quote_lists, onboarding_quotes
    names_only = q['ask'] == q.get('merchant_name_rendered')
    identity = q['quote_source'] if q['ask'] == q.get('quote_rendered') else q['ask']
    source = (q['quote_source'] if q['ask'] == q.get('quote_rendered')
              else quote_lists(q['ask'], qid=q['id']))
    rendered = onboarding_quotes(source, qid=q['id'])
    # Grouping changes presentation only: keep an issued callback's exact
    # pre-grouping identity. A later content edit must still get a new token.
    if q['ask'] != q.get('quote_rendered'):
        q.pop('quote_source', None)
        q.pop('quote_rendered', None)
        if rendered != identity:
            q['quote_source'] = identity
            q['quote_rendered'] = rendered
    else:
        # A held card can acquire new quote boundaries after a layout upgrade.
        # Its source still identifies the issued callback; track the new body.
        q['quote_rendered'] = rendered
    q['ask'] = rendered
    if names_only:
        q['merchant_name_rendered'] = rendered
    token = _review_token(q)
    indices = {c['value']: n for n, c in enumerate(q['choices'])}
    return {'text': q['ask'], 'parse_mode': 'HTML',
            'buttons': [[{'text': ('✓ ' if c.get('selected') else '') + ('%d. %s' % (indices[c['value']] + 1, c['label']) if q['id'].startswith('review_pair_') or (q['id'].startswith('review_funding_') and c.get('source')) else c['label']),
                          'callback_data': 'opt:lr:%s:%d' % (token, indices[c['value']])} for c in row]
                        for row in q['button_rows']]}


def tap(token, today=None):
    """Resolve a literal review-card callback against the issued question only.

    No Telegram calls here. Relays may pass raw callback_data to this command,
    or pass the bridge-resolved full label to answer instead.
    """
    if (token or '').startswith('opt:la:'):
        from . import allocation
        return allocation.tap(token)
    match = re.fullmatch(r'opt:lr:([0-9a-f]{16}):(\d+)', token or '')
    state = load_state()
    flow = _flow(state)
    # A repeated surplus/zero-gap selection returns the original receipt even after
    # the next screen was issued. Preserve other cards' stale-tap behavior.
    saved = (flow or {}).get('questions', {}).get('conclusion', {})
    receipt = (flow or {}).get('answer_receipts', {}).get('conclusion', {})
    if (match and (saved.get('saving_surplus') is not None or saved.get('reduction_variable') is not None)
            and receipt.get('question_token') == match[1]
            and match[1] == _review_token(saved)
            and int(match[2]) < len(saved['choices'])
            and receipt.get('value') == saved['choices'][int(match[2])]['value']
            and (flow or {}).get('answers', {}).get('conclusion', {}).get('callback') == token):
        return copy.deepcopy(receipt['receipt'])
    q = (flow or {}).get('questions', {}).get((flow or {}).get('pending'), {})
    if (not match or not q.get('id') or
            match[1] != _review_token(q) or int(match[2]) >= len(q['choices'])):
        if match and (q.get('id') == 'review_purpose' or match[1] in (flow or {}).get('purpose_tokens', [])):
            current = next_question(today=today)
            card = (current.get('question') or {}).get('card')
            return dict(current, stale=True, recorded=None, **({'card': card} if isinstance(card, dict) else {}))
        raise ValueError('stale or invalid review callback')
    return answer(q['id'], q['choices'][int(match[2])]['value'], today=today, callback=token)


def debt_known(loan_id):
    """Stage a kit-recorded owner value; accept no remembered values."""
    from . import known_values
    st = load_state(); flow = st.get('flow') or {}
    loan = next((row for row in (flow.get('history') or {}).get('debts', [])
                 if row.get('id') == loan_id), None)
    if loan is None:
        raise ValueError('unknown loan id')
    value = known_values.debt(st, flow, loan)
    if value:
        flow.setdefault('debt_known', {})[str(loan_id)] = value
    else:
        flow.get('debt_known', {}).pop(str(loan_id), None)
    flow.get('questions', {}).pop('reduce_debts', None)
    st['flow'] = flow; save_state(st)
    return dict(staged='debt_balance' if value else None, id=loan_id,
                recorded=False, available=bool(value))


def debt_reopen(loan_id, today=None):
    """Explicitly reopen one loan while retaining the prior answer history."""
    from . import known_values
    st = load_state(); flow = st.get('flow') or {}
    if not flow:
        import copy
        from . import history_flow
        today = today or datetime.date.today()
        history = kit.run('ledger-history', '--today', today.isoformat())
        current = next((row for row in history['debts'] if row['id'] == loan_id), None)
        saved = next((old for old in known_values._flows(st, {}) if current and any(
            known_values._identity(row) == known_values._identity(current)
            for row in old.get('history', {}).get('debts', []))), None)
        if saved is None:
            raise ValueError('no recorded onboarding for this loan')
        flow = copy.deepcopy(saved)
        flow.update(today=today.isoformat(), history=history,
                    nonce=history_flow.new_flow(today)['nonce'], questions={})
        for key in ('answer_receipts', 'completion_prepared_at', 'completion_card'):
            flow.pop(key, None)
    debts = (flow.get('history') or {}).get('debts') or []
    if not any(row.get('id') == loan_id for row in debts):
        raise ValueError('unknown loan id')
    loan = next(row for row in debts if row['id'] == loan_id)
    value = known_values.debt(st, flow, loan)
    if value:
        flow.setdefault('debt_known', {})[str(loan_id)] = value
    else:
        flow.get('debt_known', {}).pop(str(loan_id), None)
    flow['debt_reopen_count'] = flow.get('debt_reopen_count', 0) + 1
    flow.get('answer_receipts', {}).pop('reduce_debts', None)
    flow['debt_reopen_id'] = loan_id
    flow['revisit'] = 'reduce_debts'
    flow.setdefault('answers', {}).pop('reduce_debts', None)
    flow.setdefault('questions', {}).pop('reduce_debts', None)
    flow['pending'] = None
    st['flow'] = flow; save_state(st)
    return {'reopened': 'reduce_debts', 'id': loan_id, 'next': 'onboarding next'}


def _insurance_fact(rec, q, said):
    """Compatibility hook; the agent records insurance with a typed amount."""
    return rec


def _review_answer(qid, value, flow, q, said=None):
    kind = qid.split('_')[1]
    data = None
    if kind == 'deposit':
        from . import deposit_groups
        if qid.startswith(deposit_groups.PREFIX):
            return deposit_groups.answer(q, value, flow, said)
        if value == 'skip':
            flow.pop('deposit_typing', None)
            return dict(skipped=True)
        if value == 'type' and flow.get('deposit_typing') != qid:
            flow['deposit_typing'] = qid
            return dict(pending_input=True)
        if flow.get('deposit_typing') == qid:
            data = dict(kind='deposit', id=q['item']['id'], answer='custom', said=value)
        elif value in ('salary', 'income', 'received', 'own_transfer', 'insurance'):
            data = dict(kind='deposit', id=q['item']['id'], answer=value, said=said or '')
        elif q.get('free_input') and value.strip() and not value.startswith('{'):
            data = dict(kind='deposit', id=q['item']['id'], answer='custom', said=said or value)
        else:
            raise ValueError('choose an issued deposit answer or open direct input')
        if q['item'].get('split_payment'):
            data.update(spend_id=q['item']['split_payment']['id'], payment=q['item']['split_payment'],
                        receipt={key: q['item'][key] for key in ('id', 'date', 'name', 'amount', 'method')})
        result = kit.run('ledger-review-answer', '--month', q['month'], '--data', json.dumps(data))
        flow.pop('deposit_typing', None)
        return dict(ok=True, result=result)
    if kind in ('person', 'pair') and value in ('skip', 'skip_all'):
        if value == 'skip_all':
            flow['skip_remaining_received'] = True
        flow.pop('pair_typing', None)
        return {'skipped': True}
    if kind == 'person':
        if value not in ('yes', 'no'):
            raise ValueError('person answer: yes | no')
        if value == 'yes':
            return dict(pair=True, **({'said': said} if said else {}))
        data = dict(kind='person', id=q['item']['id'], income=True)
    elif kind == 'pair':
        if value == 'none':
            # The owner contradicted the reimbursement premise. Ask person
            # again; never silently turn a rejected candidate into income.
            person = qid.replace('review_pair_', 'review_person_')
            flow['answers'].pop(person, None)
            flow['questions'].pop(person, None)
            flow['questions'].pop(qid, None)
            flow.pop('pair_typing', None)
            flow['pending'] = None
            return {'revisit_person': True}
        if value == 'type':
            flow['pair_typing'] = qid
            return {'pending_input': True}
        data = dict(kind='person', id=q['item']['id'])
        if value == 'cash':
            words = said or flow['answers'].get('review_person_' + str(q['item']['id']), {}).get('said')
            data.update(cash=True, note=words)
        elif value.isdigit():
            data['spend_id'] = int(value)
        else:
            typed = re.fullmatch(r'(\d{4}-\d{2}-\d{2})\s*[/, ]\s*(\S.*)', value)
            if not typed:
                raise ValueError('pair answer: spend id or YYYY-MM-DD / merchant')
            data.update(date=typed[1], name=typed[2].strip())
    elif kind == 'odd':
        from . import end_dates
        detail=json.loads(value) if end_dates.enabled() and value.startswith('{') else dict(answer=value)
        if end_dates.enabled() and q['item']['id'] not in flow.get('end_asked_fixed',[]) and 'ends_on' not in detail and 'end_kind' not in detail:
            stream=next((r for r in kit.run('stream-list')['streams'] if r['id']==q['item'].get('stream_id')),None)
            if not stream or not stream.get('end_asked'): detail['end_kind']='unknown'
        detail=dict(flow.get('pending_end_fixed',{}).get(qid,{}),**detail)
        data = dict(detail,kind='fixed', id=q['item']['id'],said=said or value)
    elif kind == 'refund':
        data = dict(kind='refund', id=q['item']['id'], answer=value)
    elif kind == 'roster':
        data = _roster_answer(value, flow, q)
        if data.get('pending_input'):
            return data
    elif kind == 'purpose':
        return _purpose_answer(value, flow, q)
    elif kind == 'bank':
        if value.startswith('merge:'):
            if value not in [c['value'] for c in q['choices']]:
                raise ValueError('choose an issued same-account confirmation')
            return kit.run('method-merge', q['target']['id'], value.split(':', 1)[1])
        if value == 'unknown':
            return dict(skipped=True)
        if not value or _generic_account(dict(q['target'], name=value)):
            raise ValueError('give a bank or a distinguishable account name')
        old = dashboard.generic_account_name(q['target']['name']) or q['target']['name']
        name = value if old.casefold() in value.casefold() else value + ' ' + old
        # Existing roster correction path renames, preserves the old alias,
        # and updates historical method strings atomically.
        data = dict(kind='roster', corrections=[dict(id=q['target']['id'], name=name)])
    elif kind == 'funding':
        return _funding_answer(value, flow, q, said)
    elif kind == 'balance':
        if qid == 'review_balance_gap':
            if value not in ('add', 'exclude'):
                raise ValueError('balance gap answer: add | exclude')
            added = []
            if value == 'add':
                for name in q['names']:
                    mid = kit.method_add(name, 'account')
                    added.append(dict(id=mid, name=name))
                flow['balance_all'] = list(flow.get('balance_all', [])) + added
            return {'add': value == 'add', 'names': q['names'], 'added': added}
        return _balance_answer(value, flow, q, said=said)
    elif kind == 'living':
        if value.startswith('{'):
            typed = json.loads(value)
            if 'monthly_cap' in typed:
                cap = dict(typed['monthly_cap'], kind='monthly_cap', said=said or '')
                result = kit.run('ledger-review-answer', '--month', q['month'], '--data', json.dumps(cap))
                return dict(pending_input=True, result=result)
        elif value != 'ok':
            return dict(pending_input=True, fields=['monthly_cap.amount'])
        proposal = q['review']['living_proposal']
        data = {k:[r['id'] for r in proposal[k]] for k in ('one_off','subscriptions')} if value == 'ok' else json.loads(value)
        if not isinstance(data, dict):
            raise ValueError('living answer is ok or an object with one_off and subscriptions ids')
        data['kind'] = 'living'
    elif kind == 'cards' and value in ('ok','skip'):
        return {'ok':True}
    elif kind == 'card':
        card_name = q['item']['card']
        if value in ('purchases', 'type', 'capture', 'skip', 'ok'):
            flow.pop('installment_words', None)
            flow.pop('installment_fields', None)
        if value in ('purchases', 'type'):
            flow['installment_intake'] = qid
            return dict(pending_input=True)
        if value not in ('capture', 'captured', 'skip', 'ok') and not value.startswith('{'):
            words = value
            if flow.get('installment_words'):
                words = flow['installment_words'] + ' ' + value
            result = kit.run('ledger-review-answer', '--month', q['month'], '--data',
                             json.dumps(dict(kind='installment_said', card=card_name, said=words)))
            if result.get('pending_input'):
                flow['installment_intake'] = qid
                flow['installment_words'] = words
                flow['installment_fields'] = result['fields']
                return dict(pending_input=True, result=result)
            flow.pop('installment_words', None)
            flow.pop('installment_fields', None)
            updated = next(v for v in result['gaps'] if v['card'] == card_name)
            flow['review_card_gaps'][int(qid.rsplit('_', 1)[1])] = updated
            flow.pop('installment_intake', None)
            return dict(pending_input=True, result=result)
        if value.startswith('{'):
            evidence = json.loads(value)
            evidence.update(kind='card',card=card_name)
            result = kit.run('ledger-review-answer','--month',q['month'],'--data',json.dumps(evidence))
            # New evidence revises the comparison; confirmation remains on this
            # card so the owner sees the remaining residual before continuing.
            updated = next((v for v in result['gaps'] if v['card']==card_name),dict(q['item'],gap=0,material=False))
            flow['review_card_gaps'][int(qid.rsplit('_',1)[1])] = updated
            if not updated['material']:
                return dict(ok=True,resolved=True,result=result)
            return dict(pending_input=True,result=result)
        if value == 'capture':
            # Keep the card pending; confirmed installment intake is the existing
            # capture/review path, not inferred months from the export.
            flow['installment_capture_pending'] = qid
            flow['installments_before'] = kit.run('installment-list', '--month', q['month'], '--json')['items']
            return {'pending_capture': True}
        if value == 'captured':
            current = kit.run('installment-list', '--month', q['month'], '--json')['items']
            before = [v for v in flow.get('installments_before', []) if v.get('card') == card_name]
            after = [v for v in current if v.get('card') == card_name]
            if not flow.get('installment_capture_pending') or not after or before == after:
                raise ValueError('review and save the installment capture first')
            current_gap = next((v for v in kit.ledger_diagnosis(q['month'])['review']['gaps'] if v['card']==card_name),dict(q['item'],gap=0,material=False))
            flow['review_card_gaps'][int(qid.rsplit('_',1)[1])] = current_gap
            flow.pop('installment_capture_pending',None)
            if current_gap['material']:
                return dict(pending_input=True,residual=current_gap['gap'])
        elif value not in ('skip', 'ok'):
            raise ValueError('cards answer: capture | captured | skip')
        if value in ('skip','ok'):
            return dict(ok=True,skipped=True,card=card_name,residual=q['item']['gap'],status='unresolved')
    elif value != 'ok':
        raise ValueError('confirm with ok')
    if data:
        result = kit.run('ledger-review-answer', '--month', q['month'], '--data', json.dumps(data))
        if kind=='odd':
            if result.get('pending_end'): flow.setdefault('pending_end_fixed',{})[qid]=result['pending_end']
            elif not result.get('pending_cycle'): flow.get('pending_end_fixed',{}).pop(qid,None)
            from . import end_dates
            if end_dates.enabled() and q['item']['id'] not in flow.get('end_asked_fixed',[]):
                flow.setdefault('end_asked_fixed',[]).append(q['item']['id'])
        if result.get('pending_cycle'):
            fresh = kit.ledger_diagnosis(q['month'])['review']
            flow['review_odd'] = fresh['odd']
            return dict(pending_input=True, result=result)
        return dict(ok=True, result=result)
    return {'ok': True}


def _source_ask(flow):
    src = sources.current()
    if src['ask_app']:
        return _q('source', _t('source.open'), free=True, ask_app=True)
    choices = [{'label': text.t('ledger.app.' + app), 'value': app} for app in src['screenshot']]
    choices.append(_choice('source.none', 'none'))
    return _q('source', _t('source.ask'), choices, free=True)


def _aim_ask(flow):
    head = ''
    # Chat capture precedes module setup. Read the shared rows through the
    # kit verb and present them as saved facts, never a registration question.
    state = kit.ledger_state()
    eligible = None
    if state['rows']:
        d = kit.ledger_diagnosis()
        # The opener lists saved facts, not projected or overdue detections.
        eligible = {r['id'] for r in d['out']['regular_lines'] if r['seen']}
    detected = {r['id'] for r in kit.run('stream-list')['streams'] if r['source']=='ledger_v6'} if state['rows'] else set()
    fixed = [r for r in kit.fixed_list() if r['direction'] == 'expense'
             and (eligible is None or r['id'] not in detected or r['id'] in eligible)]
    if fixed:
        from . import end_dates
        terms={r['id']:r.get('ends_on') for r in kit.run('stream-list')['streams']} if end_dates.enabled() else {}
        lines = []
        for row in fixed:
            cycle = row['cycle']
            if cycle in ('monthly', 'yearly'):
                cycle = _t('saved_fixed.' + cycle)
            elif cycle in ('quarterly', 'semiannual'):
                cycle = dashboard.rt('cycle.' + cycle)
            lines.append(_t('saved_fixed.row', name='<b>'+end_dates.label(html.escape(row['name']),terms.get(row['id']))+'</b>', cycle=cycle,
                            amount=diagtext.money(row['amount'])))
        # Short saved streams share one quote; each retains its bullet.
        head += (_t('saved_fixed.head') + '\n' +
                 dashboard.quote_items(lines) + '\n\n')
    # Approved preview intentionally keeps each option heading inside its quote.
    previews = [
        group(['💰 <b>' + _t('aim.save_more') + '</b>', _t('aim.preview.save_more'),
               sub_line(_t('aim.preview.surplus')).lstrip()], trusted_html=True),
        group(['🎯 <b>' + _t('aim.save_for') + '</b>', _t('aim.preview.target'),
               bar_inline('▓▓▓░░░░░░░') + ' ' + _t('aim.preview.timeline')], trusted_html=True),
        group(['📊 <b>' + _t('aim.flow') + '</b>'] + [
            _t('aim.preview.' + key) + ' ' + bar_inline(bar) + ' ' + percent
            for key, bar, percent in [('food', '███████░░░', '42%'),
                                      ('shopping', '████░░░░░░', '23%'),
                                      ('transport', '██░░░░░░░░', '11%')]], trusted_html=True)]
    ask = _opener() + '\n\n' + _t('aim.example') + '\n' + _t('aim.ask') + '\n' + '\n'.join(previews)
    if head:
        ask += '\n\n' + head.rstrip()
    return _q('aim', ask, [_choice('aim.' + a, a) for a in AIMS], free=True)


def _target_ask(flow):
    return _q('target', _t('target.ask'), [], free=True)


def _ask(qid, flow, today, prefix=None, review_data=None):
    if qid.startswith('review_'):
        return _review_ask(qid, flow, review_data)
    if qid == 'aim':
        return _aim_ask(flow)
    if qid == 'target':
        return _target_ask(flow)
    if qid == 'source':
        return _source_ask(flow)
    if qid == 'start':
        return _q(qid, _opener(), [_choice('start.go', 'yes'), _choice('start.later', 'no')])
    if qid == 'file':
        return _file_ask(flow, today)
    if qid == 'confirm':
        return _confirm_ask(flow)
    if qid == 'diagnosis':
        return _diagnosis_ask(flow, prefix)
    raise ValueError('unknown question %s' % qid)


# ---- lookups (the export check, the steps on [how]) -------------------------------------------

def _lookup_need(qid, flow, today):
    """Resolve current instructions only after the owner chooses an import method."""
    if qid != 'file' or not flow.get('method'):
        return None
    kind, app = flow['method'].split(':', 1)
    if not lookup.app_conf(app):
        return None
    return lookup.need(app, 'capture' if kind == 'capture' else 'how', today)


def lookup_status(today=None):
    """`onboarding lookup`: the lookups due now for the pending step."""
    today = today or datetime.date.today()
    st = load_state()
    flow = _flow(st)
    qid = _pending_id(flow) if flow else None
    need = _lookup_need(qid, flow, today) if qid and qid not in flow['questions'] else None
    return {'needs': [need] if need else [], 'cache': lookup.load()}


def lookup_set(app, what, verdict, source=None, url=None, steps_json=None, today=None):
    today = today or datetime.date.today()
    if app not in (sources.load().get('lookup') or {}).get('apps', {}):
        raise ValueError('lookup: unknown app %r (sources.json lookup.apps)' % app)
    steps = None
    if steps_json is not None:
        try:
            steps = json.loads(steps_json)
        except ValueError:
            raise ValueError('--steps-json must be a JSON list of step texts')
    return lookup.put(app, what, verdict, today, source=source, url=url, steps=steps)


def product_note(note, today=None):
    return lookup.note(note, today or datetime.date.today())


# ---- next ---------------------------------------------------------------------------------------

def _render(flow, qid, prefix=None):
    q = dict(flow['questions'][qid])
    from .markup import onboarding_quotes
    if q['ask'] != onboarding_quotes(q['ask'], qid=qid):
        q['card'] = review_card(q)
        flow['questions'][qid] = dict(q)
        state = load_state()
        state['flow'] = flow
        save_state(state)
    elif q.get('choices') and not isinstance(q.get('card'), dict):
        q['card'] = review_card(q)
    if prefix:
        q['ask'] = prefix + '\n\n' + q['ask']
        if isinstance(q.get('card'), dict):
            q['card'] = dict(q['card'], text=q['ask'])
    return q


def fixed_correct(ref, fields, direction=None, today=None):
    """Land a mid-flow correction and reissue the pending question with fresh figures.

    A correction is never an answer to that question. Resolution/validation
    failures leave the flow untouched and expose the kit's candidate payload.
    """
    st = load_state()
    flow = _flow(st)
    if not flow or not flow.get('pending'):
        raise ValueError('no pending onboarding question: run `onboarding next` first')
    try:
        result = kit.fixed_correct(ref, fields, direction=direction)
    except kit.KitError as exc:
        if exc.code not in (2, 3):
            raise
        # KitError retains the full JSON when the result has no error key.
        try:
            result = json.loads(exc.message)
        except ValueError:
            result = {'status': 'invalid', 'say': exc.message}
        return dict(result, corrected=False)
    # Cached questions carry figures and callback tokens. Rebuild those
    # screens from kit reads, including a question parked behind resume.
    flow.pop('completion_prepared_at', None)
    flow.pop('completion_card', None)
    flow['questions'] = {key: q for key, q in flow['questions'].items()
                         if key != 'aim' and key != 'diagnosis' and not key.startswith('review_')}
    if flow.get('path') == 'imported':
        review = kit.ledger_diagnosis(flow.get('diag_month'))['review']
        for key, kind in (('review_people', 'people'), ('review_odd', 'odd')):
            fresh = {row['id']: row for row in review[kind]}
            flow[key] = [fresh.get(row['id'], row) for row in flow.get(key, [])]
    # The owner's correction is fresh activity on the same question. Reset
    # idle timing so the next call also retains it instead of showing resume.
    flow['asked_at'] = _now()
    st['flow'] = flow
    save_state(st)
    resumed = next_question(today=today)
    return dict(resumed, correction=result, say=result['say'], corrected=True)


def next_question(today=None, offer=False, now=None):
    """Hand out ONE message. While a handed-out question is unanswered the same one comes back (a resume line first
    after RESUME_HOURS for an unanswered opener; otherwise a resume card). -> {question|None, step, of, done}."""
    from . import allocation
    correction = allocation.current()
    if correction is not None:
        return correction
    today = today or datetime.date.today()
    now = now or datetime.datetime.now()
    st = load_state()
    flow = _flow(st)
    if not flow:
        from . import history_flow
        flow = history_flow.new_flow(today, offer=offer)
    if flow.get('funding_confirmations'):
        say = flow['funding_confirmations'].pop(0)
        st['flow'] = flow
        save_state(st)
        q = _interactive(_q('review_funding_confirmation', say, [], auto_advance=True))
        return dict(question=q, step=None, of=None if flow.get('controller') == 'history_v1' else len(_ids(flow)), done=False)
    if flow.get('controller') == 'history_v1':
        from . import history_flow
        resumed = history_flow.resume(st, flow, now)
        if resumed is not None:
            return resumed
        previous = flow.get('pending')
        out = history_flow.next_question(st, flow, today)
        if out.get('question') and (previous != flow.get('pending') or not flow.get('asked_at')):
            flow['asked_at'] = now.isoformat(timespec='seconds')
            history_flow._save(st, flow)
        return out
    if flow.get('has_allocations'):
        stamp = allocation.call('versions')['stamp']
        if flow.get('allocation_stamp') != stamp:
            flow['questions'] = {k:v for k,v in flow['questions'].items() if not k.startswith('review_') and k!='diagnosis'}
            flow['allocation_stamp'] = stamp
    if flow.get('answers', {}).get('source') and not st.get('recording_from'):
        seed.ensure()
        st['recording_from'] = today.isoformat()
        _ensure_backfill(st)
    prefix = None
    if flow['pending'] and flow.get('asked_at') and flow['pending'] != 'resume':
        idle = now - datetime.datetime.fromisoformat(flow['asked_at'])
        if idle >= datetime.timedelta(days=STALE_DAYS):
            flow['resume_of'] = flow['pending']
            flow['pending'] = 'resume'
            flow['questions']['resume'] = _q('resume', _t('resume.stale', days=idle.days),
                                             [_choice('resume.go', 'continue'), _choice('resume.over', 'restart')])
        elif idle >= datetime.timedelta(hours=RESUME_HOURS):
            # A fresh entry is separated by inactivity, not by process lifetime:
            # the relay invokes next repeatedly and after partial answers.
            if flow.get('answers') and not flow.get('resume_offered'):
                pending = _pending_id(flow)
                done, current = _resume_phase_copy(flow, pending)
                flow['resume_of'] = pending
                flow['pending'] = 'resume'
                flow['resume_offered'] = True
                flow['questions']['resume'] = _q(
                    'resume', _t('resume.fresh' if done else 'resume.fresh.partial',
                                 done_phases=text.josa(done, 'eun') if text.lang() == 'ko' else done, current_phase=current),
                    [_choice('resume.continue', 'continue'), _choice('resume.restart', 'restart')])
            else:
                prefix = _t('resume.day') if idle.days <= 1 else _t('resume.days', days=idle.days)
    review_data = None
    if (flow.get('path') == 'imported' and not flow.get('proposal_only')
            and not st.get('completed_at')
            and (not flow.get('funding_review_complete') or any(
                'review_bank_' + str(m['id']) not in flow['answers'] for m in flow.get('review_banks', [])))):
        # Re-read after list corrections or recalled owner facts. Evidence
        # learned between turns must never produce a redundant question.
        review_data = kit.ledger_diagnosis(flow.get('diag_month'))
        review_data = _resolve_funding(flow, _resolve_banks(flow, review_data))
        roster = review_data['review']['roster']
        flow['review_funding'] = _funding_targets(roster)
        flow['review_banks'] = [m for m in roster['groups']['account'] if _generic_account(m)]
        if all('review_funding_' + str(item['id']) in flow['answers'] for item in flow['review_funding']):
            flow['funding_review_complete'] = True
        if (flow.get('pending') or '').startswith(('review_funding_', 'review_bank_')) and flow['pending'] not in _ids(flow):
            flow['pending'] = None
        for item in flow['review_funding']:
            key = 'review_funding_' + str(item['id'])
            if key not in flow['answers']:
                flow['questions'].pop(key, None)
    if flow.get('pending') == 'review_roster' and any(
            'review_bank_' + str(m['id']) not in flow['answers'] for m in flow.get('review_banks', [])):
        flow['pending'] = None
    if flow.get('path') == 'imported' and 'review_deposits' not in flow:
        review_data = review_data or kit.ledger_diagnosis(flow.get('diag_month'))
        flow['review_deposits'] = review_data['review'].get('unmatched_deposits', [])
    qid = _pending_id(flow)
    if flow.get('path') == 'imported' and 'review_people' not in flow and qid != 'confirm':
        d = review_data if review_data is not None else kit.ledger_diagnosis(flow.get('diag_month'))
        d = _resolve_funding(flow, _resolve_banks(flow, d)) if not st.get('completed_at') and not flow.get('proposal_only') else d
        review_data = d
        flow['review_people'] = d['review']['people']
        flow['review_deposits'] = d['review'].get('unmatched_deposits', [])
        flow['review_odd'] = d['review']['odd']
        flow['review_refunds'] = d['review'].get('refund_candidates', [])
        flow['review_card_gaps'] = d['review']['gaps']
        flow['review_banks'] = [m for m in d['review']['roster']['groups']['account'] if _generic_account(m)]
        flow['review_funding'] = _funding_targets(d['review']['roster'])
        flow['review_balances'] = _balance_plan(flow, d['review']['roster'])
        qid = _pending_id(flow)
    if flow.get('path') == 'imported':
        deposits = flow.get('review_deposits', [])
        if any('review_deposit_' + str(r['id']) not in flow['answers'] for r in deposits):
            # Legacy flows also hold month-specific questions across turns.
            # Use the kit's current window and classification, as new starts do.
            plan = kit.run('ledger-plan', '--today', today.isoformat())
            month = flow.get('diag_month') or (review_data or {}).get('month')
            deposits = [r for r in plan.get('deposits', []) if r['month'] == month]
        from . import deposit_groups
        if deposit_groups.apply_remembered(deposits):
            plan = kit.run('ledger-plan', '--today', today.isoformat())
            month = flow.get('diag_month') or (review_data or {}).get('month')
            deposits = [r for r in plan.get('deposits', []) if r['month'] == month]
        _drop_stale_deposit_questions(flow, deposits)
        flow['review_deposits'] = deposits
        qid = _pending_id(flow)
    if (qid or '').startswith('review_balance_') and qid != 'review_balance_gap':
        if not _balance_source_known(flow):
            qid = 'source'
        else:
            app = _balance_aggregator(flow)
            if app and len(flow['review_balances']) > 1:
                # Upgrade a saved per-account queue when its import reveals
                # the aggregator. Preserve already recorded/skipped answers.
                unanswered = [m for m in flow['review_balances']
                              if 'review_balance_' + str(m['id']) not in flow['answers']]
                flow['review_balances'] = unanswered[:1]
                flow['pending'] = None
                qid = _pending_id(flow)
            if (flow['questions'].get(qid) or {}).get('aggregator') != app:
                flow['questions'].pop(qid, None)
    if qid == 'file' and flow.get('method') and _lookup_need(qid, flow, today):
        flow['questions'].pop('file', None)
    if qid is not None and (qid == 'review_roster' or qid not in flow['questions']):
        need = _lookup_need(qid, flow, today)
        if need:
            # the agent looks it up and records the verdict (`onboarding lookup-set`), then asks `next` again;
            # nothing is handed out
            st['flow'] = flow
            save_state(st)
            ids = _ids(flow)
            return {'question': None, 'lookup': need, 'step': ids.index(qid) + 1 if qid in ids else None,
                    'of': len(ids), 'done': False}
        flow['questions'][qid] = _ask(qid, flow, today, review_data=review_data)
    if qid != flow['pending'] or not flow.get('asked_at'):
        flow['asked_at'] = now.isoformat(timespec='seconds')
    flow['pending'] = qid
    st['flow'] = flow
    save_state(st)
    ids = _ids(flow)
    if qid is None:
        # The installed relay sends apply.ready when next.done is true.
        # Prepare here; emit the standalone completion through that existing path.
        month = flow.get('diag_month') or history_period(today, 1)[1].isoformat()[:7]
        closing = dashboard.completion(kit.ledger_diagnosis(month), flow, st)
        flow['completion_prepared_at'] = _now()
        flow['completion_card'] = dict(text=closing, parse_mode='HTML', buttons=[])
        st['flow'] = flow
        save_state(st)
        return {'question': None, 'step': len(ids), 'of': len(ids), 'done': True,
                'completion_required': True}
    q = _render(flow, qid, prefix)
    step = ids.index(qid) + 1 if qid in ids else len([i for i in ids if i in flow['answers']]) + 1
    return {'question': q, 'step': step, 'of': len(ids), 'done': False}


# ---- import (S1: silent) ----------------------------------------------------------------------------

def _said_covers(c, facts):
    """L4: a candidate the owner's own words already name is not asked. -> 'company' | 'mine' | None (named, no verdict)
    | False (not named)."""
    name = re.sub(r'\s+', '', c['name'].replace(_t('words.settle'), ''))
    for f in facts:
        w = re.sub(r'\s+', '', f)
        if name and name in w or (len(name) >= 2 and name[:2] in w):
            return 'company' if _t('words.company') in w else None
    return False


def _pick_question(flow, d):
    c = d.get('question')
    if not c:
        return None
    cov = _said_covers(c, flow.get('said_facts') or [])
    if cov is False:
        return c
    if cov == 'company':
        kit.card_answer(c['spend_id'], 'company')
    return None


def import_file(path, today=None, password=None):
    """The owner's file for the `file` step: the kit reads a Banksalad CSV, xlsx or the mailed zip, classifies and
    lands every payment (no pending, no double with rows already there), then the diagnosis is built. A refusal
    keeps the step pending and comes back as its reason. An encrypted zip -> error 'password': the step asks for the
    password once; the agent re-runs the import with it on stdin (used for this call only, never stored)."""
    today = today or datetime.date.today()
    st = load_state()
    flow = _flow(st)
    if flow and flow.get('controller') == 'history_v1':
        from . import history_flow
        return history_flow.import_file(st, flow, path, today, password)
    if not flow or flow.get('pending') != 'file':
        save_state(st)
        raise ValueError('not-asked: import goes with the file step')
    err, res = None, None
    try:
        seed.ensure()
        since, until = history_period(today, 3)
        res = kit.ledger_import(path, password=password)
    except kit.KitError as e:
        err = kit.import_error(e)       # password / password_wrong / locked / format
    except OSError:
        err = 'format'
    if res is not None and not res.get('events'):
        err = 'empty'
    if err:
        base = _file_ask(flow, today)
        if err in ('password', 'password_wrong'):
            base = _q('file', _t('file.err.' + err), base['choices'], free=True, password=True)
        else:
            base['ask'] = _t('file.err.' + err) + '\n\n' + base['ask']
        flow['questions']['file'] = base
        st['flow'] = flow
        save_state(st)
        out = {'imported': False, 'error': err}
        if err in ('password', 'password_wrong'):
            out['password_stdin'] = True        # re-run: onboarding import FILE --password-stdin
        return out
    flow['history_period'] = {'since': res['first'], 'until': res['last'], 'kind': 'file'}
    d = kit.ledger_diagnosis(until.isoformat()[:7])
    say = import_card(path, res, password is not None)
    if (today - datetime.date.fromisoformat(res['last'])).days > 7:
        say += '\n' + _t('import.gap', last=_date_word(res['last']))
    flow['import'] = {'events': res['events'], 'new': res['new'], 'attached': res['attached'], 'same': res['same'],
                      'first': res['first'], 'last': res['last'], 'say': say, 'question': _pick_question(flow, d)}
    flow['diag_month'] = d['month']
    flow['answers']['file'] = {'imported': res['events']}
    flow['pending'] = None
    flow['asked_at'] = None
    flow['path'] = 'imported'
    kit.answer_note('records', 'file', 'records')
    _complete_backfill(st)
    st['flow'] = flow
    save_state(st)
    return {'imported': True, 'say': say, 'count': res['events'], 'first': res['first'], 'last': res['last'],
            'next': 'onboarding next'}


# ---- answer ----------------------------------------------------------------------------------------------

def _parse_fixes(raw):
    """['7=stop', '3=keep:reducible', '5=amount:274120'] -> [(7, 'stop', None), ...]"""
    out = []
    for f in raw or ():
        ref, _, rest = str(f).partition('=')
        key, _, val = rest.partition(':')
        key = key.strip()
        if not ref.strip().isdigit() or key not in ('stop', 'keep', 'amount', 'class', 'name', 'person'):
            raise ValueError('fix must be N=stop | N=keep:must|reducible|droppable | N=amount:WON | N=class:C | '
                             'N=name:TEXT | N=person:NAME (%r)' % f)
        if key != 'stop' and not val.strip():
            raise ValueError('fix %r needs a value' % f)
        out.append((int(ref), key, val.strip() or None))
    return out


def _apply_fixes(flow, fixes, said):
    done = []
    for sid, key, val in fixes:
        if key == 'stop':
            kit.stream_stop(sid, said)
        elif key == 'keep':
            if val not in FIX_KEEP:
                raise ValueError('keep is one of ' + '|'.join(FIX_KEEP))
            kit.stream_keep(sid, val)
        elif key == 'person':
            kit.stream_link(sid, 'person', val)
        else:
            kit.stream_set(sid, key, val)
        done.append('%d=%s' % (sid, key))
    flow.setdefault('fixes', []).extend(done)
    return done


def _land_levers(flow, q, picked, said):
    """Each chosen lever becomes a ledger goal (direction down, target = its monthly amount); the weekly retro is
    the default sweep. Executed one by one and recorded, so a failure leaves the rest chosen-not-executed."""
    by = {lv['id']: lv for lv in q['levers']}
    unknown = [i for i in picked if i not in by]
    if unknown:
        raise ValueError('not a lever of this card: %s (levers: %s)' % (', '.join(unknown), ', '.join(by) or 'none'))
    flow['chosen'] = list(flow.get('chosen') or []) + list(picked)
    goals = []
    for i in picked:
        lv = by[i]
        why = kit.why_add(_t('goal.why.lever', title=lv['title']), said=said or lv['title'], label=lv['title'])
        gid = kit.goal_put(None, lv['title'], 'monthly_spend', 'down',
                           {'ledger_aim': 'save_more', 'lever': i, 'rule': lv['rule'], 'monthly': lv['monthly'],
                            'month': q['month']}, said or lv['title'], new=True)
        kit.goal_amount(gid, lv['monthly'], said or lv['title'])
        goals.append(gid)
        flow['executed'].append(i)
    return {'levers': list(picked), 'goal_ids': goals}


def _land_flow_only(flow, q, said):
    """[just the diagnosis]: the aim 'flow' (hold), no goal to chase -- a real choice, not an exit."""
    st_why = kit.why_add(_t('goal.why.flow'), said=said or _t('goal.why.flow'), label=_t('goal.title.flow'))
    gid = kit.goal_put(None, _t('goal.title.flow'), None, 'hold', {'ledger_aim': 'flow', 'month': q['month']},
                       said or _t('goal.title.flow'))
    flow['chosen'] = list(flow.get('chosen') or []) + ['flow']
    flow['executed'] = list(flow.get('executed') or []) + ['flow']
    return {'aim': 'flow', 'goal_ids': [gid], 'why_id': st_why}


def _land_budget(flow, q, said):
    """Income less monthly equivalent fixed costs and savings becomes the budget."""
    if not q.get('budget'):
        raise ValueError('budget: this card has no income to budget from')
    title = _t('goal.title.budget')
    why = kit.why_add(_t('goal.why.lever', title=title), said=said or title, label=title)
    gid = kit.goal_put(None, title, 'monthly_spend', 'down', {'ledger_aim': _aim_of(flow), 'budget': True, 'month': q['month']},
                       said or title, new=True)
    kit.goal_amount(gid, q['budget'], said or title)
    flow['chosen'] = list(flow.get('chosen') or []) + ['budget']
    flow['executed'] = list(flow.get('executed') or []) + ['budget']
    return {'budget': q['budget'], 'goal_ids': [gid], 'why_id': why}


def _aim_of(flow):
    return _aim(flow) or 'flow'


def _land_saving(flow, value, said):
    """[save for something]: one free-text answer (what, how much) -> one 'up' goal; a JSON {"item","amount"} is read,
    plain words become the item. The amount, when given, is the goal's target."""
    raw = (value or '').strip()
    d = {}
    if raw.startswith('{'):
        try:
            d = json.loads(raw)
        except ValueError:
            raise ValueError('target: words, or JSON {"item": ..., "amount": WON}')
    item = str(d.get('item') or (raw if not d else '')).strip()[:30]
    amount = d.get('amount')
    if not item and not amount:
        raise ValueError('target needs what to save for (and how much)')
    amount = int(re.sub(r'[^0-9]', '', str(amount))) if amount not in (None, '') else None
    title = _t('goal.title.for_item', item=item) if item else _t('goal.title.saving')
    flow['chosen'] = list(flow.get('chosen') or []) + ['saving']
    kit.why_add(_t('goal.why.saving', title=title), said=said or title, label=title)
    gid = kit.goal_put(None, title, None, 'up', {'ledger_aim': 'save_for', 'item': item, 'amount': amount}, said or title, new=True)
    if amount:
        kit.goal_amount(gid, amount, said or title)
    flow['executed'] = list(flow.get('executed') or []) + ['saving']
    return {'item': item, 'amount': amount, 'goal_ids': [gid]}


def answer(qid, value, app=None, said=None, label=None, today=None, fixes=(), drops=(), callback=None, tags=None):
    """Record (and land) the owner's answer to the message `next` handed out -- that one only. Nothing is recorded
    when a write fails: the same message stays pending."""
    if not (said and said.strip()) and not callback:
        raise ValueError('owner provenance required: show the issued onboarding screen; pass the owner callback to `onboarding tap` or quote the owner answer with --said. Never answer or skip on the agent behalf')
    st = load_state()
    if qid == 'debt_balance':
        # A typed correction is independent of the currently pending question.
        data = json.loads(value)
        if type(data.get('id')) is not int:
            raise ValueError('stable loan id required')
        day = (today or datetime.date.today()).isoformat()
        result = kit.run('ledger-plan-answer', '--today', day, '--data', json.dumps(
            dict(data, kind='debt_balance', said=said), ensure_ascii=False))
        from . import reduction_flow
        reduction_flow.refresh_debts(st.get('flow') or {})
        save_state(st)
        return dict(recorded='debt_balance', pending=(st.get('flow') or {}).get('pending'), result=result)
    if qid.startswith('instrument_'):
        raise ValueError('not-asked: instrument purpose interview retired')
    if (st.get('flow') or {}).get('controller') == 'history_v1':
        from . import history_flow
        return history_flow.answer(st, st['flow'], qid, value, said, callback, today or datetime.date.today(), tags=tags)
    if callback:
        flow = load_state().get('flow') or {}
        q = flow.get('questions', {}).get(qid, {})
        match = re.fullmatch(r'opt:lr:([0-9a-f]{16}):(\d+)', callback)
        if (not match or flow.get('pending') != qid or not q or match[1] != _review_token(q)
                or int(match[2]) >= len(q['choices']) or q['choices'][int(match[2])]['value'] != value
                or (q.get('numbered') and not qid.startswith('review_funding_'))):
            raise ValueError('stale or invalid owner callback; show the issued onboarding screen')
    from . import allocation
    if qid.startswith('review_allocation_'):
        return allocation.answer(qid, value)
    st = load_state()
    flow = _flow(st)
    if not flow:
        save_state(st)
        raise ValueError('no onboarding in progress: `onboarding next` hands out the first question')
    if st.get('correction'):
        raise ValueError('answer the issued allocation correction first')
    if (qid.startswith('review_pair_') and flow['pending'] is None and _pending_id(flow) == qid
            and flow['answers'].get(qid.replace('review_pair_', 'review_person_'), {}).get('said')
            and (value == 'cash' or (value or '').isdigit() or (value or '').startswith('group:'))):
        # A relay may resolve an already answered pair from the person's words
        # without first issuing an owner-facing pair question.
        flow['questions'][qid] = _review_ask(qid, flow)
        flow['pending'] = qid
    if qid == 'review_living' and qid in flow['answers'] and (value or '').startswith('{'):
        # Corrections to an issued review are immediate, including after moving
        # to the next card. Never ask the owner to finish an unrelated step.
        pending = flow['pending']
        q = _review_ask(qid,flow)
        rec = _insurance_fact(_review_answer(qid,value,flow,q,said),q,said)
        flow['answers'][qid]=rec
        flow['questions']={k:v for k,v in flow['questions'].items() if not k.startswith('review_') and k!='diagnosis'}
        flow['pending']=pending
        st['flow']=flow;save_state(st)
        return dict(rec,corrected=qid,recorded=qid,remaining=None)
    if flow['pending'] != qid:
        raise ValueError('not-asked: %s -- answer only the question `onboarding next` handed out (%s)'
                         % (qid, flow['pending'] or 'none pending: run `onboarding next`'))
    if (app or label or drops) and qid != 'file':
        raise ValueError('--app / --label / --drop have no place in flow v6')
    if fixes and qid != 'diagnosis':
        raise ValueError('--fix goes with the diagnosis answer')
    q = flow['questions'][qid]
    # A successful answer that keeps this question pending (direct input,
    # multi-select, capture, correction) is activity in the current session.
    # This timestamp is saved only along a successful answer path.
    flow['asked_at'] = _now()
    flow.setdefault('owner_answers', []).append(dict(question=qid, value=value, said=said, callback=callback))
    value = (value or '').strip()
    if qid.startswith(('review_purpose', 'review_funding_')) and value.startswith('✓ '):
        value = value[2:]
    if qid.startswith(('review_person_', 'review_pair_', 'review_deposit_', 'review_purpose', 'review_autolinks', 'review_bank_', 'review_funding_', 'review_balance_')):
        matches = [c for n, c in enumerate(q['choices'], 1)
                   if value in (c['label'], '%d. %s' % (n, c['label']))]
        if len(matches) == 1:
            value = ('source:' if q.get('numbered') and matches[0].get('source') else '') + matches[0]['value']
        elif callback and qid.startswith('review_funding_') and value.isdigit():
            value = 'source:' + value
        if qid.startswith('review_pair_') and (value.startswith('group:') or value.isdigit()):
            choice = next((c for c in q['choices'] if c['value'] == value), None)
            if value.startswith('group:') and (choice is None or not choice.get('allocations')):
                raise ValueError('choose an issued expense group')
            if choice is not None:
                if said and said.strip():
                    flow.setdefault('said_facts', []).append(said.strip())
                st['flow'] = flow
                save_state(st)
                return allocation.choose(q['item']['id'], choice, q['item']['amount'])
    said = (said or '').strip() or None
    today = today or datetime.date.today()
    if said:
        flow.setdefault('said_facts', []).append(said)
    if qid == 'resume':
        if value not in ('continue', 'restart'):
            raise ValueError('resume answer is continue|restart')
        flow['questions'].pop('resume')
        if value == 'restart':
            save_state(st)
            restart(today=today)
            return {'recorded': qid, 'restart': True, 'remaining': None}
        flow['pending'] = flow.pop('resume_of')
        flow['asked_at'] = _now()
        st['flow'] = flow
        save_state(st)
        return {'recorded': qid, 'resumed': flow['pending'], 'remaining': None}
    if qid == 'start':
        if value not in ('yes', 'no'):
            raise ValueError('start answer must be yes or no')
        if value == 'no':
            st.pop('flow')
            st['declines'] = st.get('declines', 0) + 1
            save_state(st)
            return {'recorded': qid, 'declined': True, 'remaining': 0}
        kit.answer_note('start', said or value, 'start')
        rec = {'start': True}
    elif qid == 'aim':
        if value not in AIMS:
            raise ValueError('aim answer is ' + '|'.join(AIMS))
        kit.answer_note('aim', said or value, 'aim')
        rec = {'aim': value}
    elif qid == 'target':
        rec = _land_saving(flow, value, said)
    elif qid == 'source':
        src = sources.current()
        if src['ask_app']:
            paths.conf_set(sources.SPEND_APP_KEY, sources.clean_app(value))
            value = 'other'
        else:
            # Accept old callback values from messages sent before this redesign.
            if value not in src['screenshot'] + ['none', 'other', 'app']:
                raise ValueError('source must be a regional app or none')
            if value in src['screenshot']:
                paths.conf_set(sources.SPEND_APP_KEY, text.t('ledger.app.' + value))
        kit.answer_note('source', said or value, 'source')
        seed.ensure()
        st.setdefault('recording_from', today.isoformat())
        rec = {'source': value}
        _ensure_backfill(st)
    elif qid == 'file':
        methods = {c['value'] for c in _method_choices(flow)}
        # Keep old how/new callbacks functional without displaying defer buttons.
        if value == 'how' and sources.current()['csv']:
            value = 'file:' + sources.current()['csv'][0]
        if value in methods:
            flow['method'] = value
            flow['questions'].pop('file', None)
            flow['asked_at'] = _now()
            st['flow'] = flow
            save_state(st)
            return {'recorded': None, 'method': value, 'remaining': None}
        if value == 'captured':
            return capture_done(st, flow, today)
        if value in ('new', 'continue'):
            flow['path'] = 'new'
            rec = {'mode': 'new'}
            _ensure_backfill(st)
            kit.answer_note('records', said or 'new', 'records')
        else:
            raise ValueError('file: choose a method, import FILE, or continue recording')
    elif qid == 'confirm':
        if value not in ('company', 'mine'):
            raise ValueError('confirm answer is company|mine')
        c = flow['import']['question']
        flow['chosen'].append('confirm')
        kit.card_answer(c['spend_id'], value)
        flow['executed'].append('confirm')
        flow['import']['question'] = None
        rec = {'confirm': value, 'spend_id': c['spend_id']}
    elif qid.startswith('review_'):
        rec = _review_answer(qid, value, flow, q, said)
        if qid.startswith('review_balance_') and qid != 'review_balance_gap':
            missing = _screen_accounts_not_in_roster(flow, said)
            if missing and not rec.get('captured'):
                flow['balance_gap'] = {'names': missing}
        if not qid.startswith(('review_deposit_', 'review_purpose', 'review_autolinks', 'review_bank_', 'review_funding_', 'review_balance_')):
            rec = _insurance_fact(rec,q,said)
        if rec.get('revisit_person'):
            st['flow'] = flow
            save_state(st)
            return dict(rec, recorded=None, remaining=None)
        if rec.get('pending_capture') or rec.get('pending_input'):
            flow['questions'][qid] = _review_ask(qid, flow)
            st['flow'] = flow
            save_state(st)
            current = flow['questions'][qid]
            return dict(rec, recorded=None, remaining=None, question=current,
                        **({'card_update': dict(action='edit_current', fallback='resend_once')}
                           if (qid == 'review_purpose' and q.get('editing') and value.startswith('tag:'))
                           or (qid.startswith('review_funding_') and q.get('multi') and value not in ('done', 'unknown')) else {}),
                        **({'card': rec['card'] if rec.get('captured') else current['card']}
                           if isinstance(current.get('card'), dict) else {}))
    elif qid == 'diagnosis':
        done = _apply_fixes(flow, _parse_fixes(fixes), said)
        if value == 'fix' or (done and value in ('', 'fix')):
            if not done:
                raise ValueError('fix: give at least one --fix N=...')
            flow['questions'][qid] = _diagnosis_ask(flow, _t('diag.fixed'))
            flow['asked_at'] = _now()
            st['flow'] = flow
            save_state(st)
            return {'recorded': None, 'fixed': done, 'remaining': None}
        if done:                                    # a fix with a pick in one turn: the pick is on the CORRECTED card
            q = _diagnosis_ask(flow)
            flow['questions'][qid] = q
        aim = _aim(flow)
        if value == 'budget' and aim == 'flow':
            raise ValueError('flow aim has no budget question')
        if value == 'budget':
            rec = _land_budget(flow, q, said)
        elif aim in ('save_for', 'flow'):
            if value != 'ok':
                raise ValueError('diagnosis answer for this aim: ok | budget | fix')
            rec = _land_flow_only(flow, q, said) if aim == 'flow' else {'ok': True}
        elif value == 'none':
            rec = _land_flow_only(flow, q, said)
        elif value == 'all':
            rec = _land_levers(flow, q, [lv['id'] for lv in q['levers']], said)
        else:
            picked = [x.strip() for x in re.split(r'[,\s]+', value.replace('pick:', '')) if x.strip()]
            if not picked:
                raise ValueError('diagnosis answer: none | all | a lever id (or ids) | fix')
            rec = _land_levers(flow, q, picked, said)
        rec['fixed'] = flow.get('fixes') or []
    else:
        raise ValueError('unknown question %s' % qid)
    if qid.startswith('review_pair_') and rec.get('ok'):
        st['last_settlement_receipt'] = int(qid.rsplit('_',1)[1])
    flow.pop('completion_prepared_at', None)
    flow.pop('completion_card', None)
    flow['answers'][qid] = rec
    flow['pending'] = None
    flow['asked_at'] = None
    st['flow'] = flow
    save_state(st)
    left = [i for i in _ids(flow) if i not in flow['answers']]
    out = dict(rec, recorded=qid, remaining=len(left))
    if qid.startswith('review_pair_') and value == 'cash' and rec.get('ok'):
        out['card'] = {'text': dashboard.rt('pair.cash_done'), 'parse_mode': 'HTML', 'buttons': []}
    return out


def known(qid):
    """Stage an earlier kit-recorded answer; accept no agent-supplied value."""
    from . import known_values
    st = load_state(); flow = _flow(st)
    if not flow:
        raise ValueError('no onboarding in progress')
    value = known_values.subscription(st, flow, qid)
    if value:
        flow.setdefault('known_answers', {})[qid] = value
    else:
        flow.get('known_answers', {}).pop(qid, None)
    flow.get('questions', {}).pop(qid, None)
    st['flow'] = flow; save_state(st)
    return dict(known=qid, available=bool(value))


def restart(today=None):
    """The owner's "start over" -- the flow is set aside; rows it landed stay (a new import merges, nothing doubles)."""
    st = load_state()
    flow = st.get('flow')
    if flow:
        st.setdefault('legacy_flows', []).append(dict(flow, dropped_at=_now(), reason='restart'))
    for key in ('correction','completed_at','last_settlement_receipt'):
        st.pop(key,None)
    from . import history_flow
    st['flow'] = history_flow.new_flow(today or datetime.date.today(), restart=True)
    save_state(st)
    return {'restarted': True}


# ---- after a month (weekly retro line, D9 attachment point) ---------------------------------------------

def proposal_due(today=None):
    """The weekly retro's one trailing line: on/after propose_on, with >= 28 days of records, offered at most
    OFFER_MAX times."""
    today = today or datetime.date.today()
    st = load_state()
    on = st.get('propose_on')
    if not on or not st.get('completed_at') or st.get('propose_offers', 0) >= OFFER_MAX:
        return None
    if today.isoformat() < on:
        return None
    info = kit.records_info()
    if not info['count']:
        return None
    span = (datetime.date.fromisoformat(info['last']) - datetime.date.fromisoformat(info['first'])).days
    if span < 28:
        return None
    return _t('propose.line')


def proposal_offered():
    st = load_state()
    st['propose_offers'] = st.get('propose_offers', 0) + 1
    save_state(st)
    return st['propose_offers']


def propose(today=None):
    """`onboarding propose`: the diagnosis of the last full month on the ledger as it stands -- a fresh one-step flow
    (a record-from-now start gets its first diagnosis here)."""
    today = today or datetime.date.today()
    st = load_state()
    flow = _new_flow(today)
    flow['path'] = 'imported'
    flow['proposal_only'] = True
    flow['diag_month'] = history_period(today, 1)[1].isoformat()[:7]
    flow['import'] = {}
    st['flow'] = flow
    st['propose_offers'] = OFFER_MAX
    save_state(st)
    return next_question(today=today)


# ---- apply -----------------------------------------------------------------------------------------------------

def _unexecuted(flow):
    return [i for i in flow.get('chosen') or [] if i not in (flow.get('executed') or [])]


def _finish(st, flow, today):
    if not flow.get('completion_prepared_at') or not flow.get('completion_card'):
        raise ValueError('completion screen not prepared: run `onboarding next`, then deliver apply.ready as one standalone message')
    if flow.get('controller') == 'history_v1':
        from . import reconciliation
        flow['completion_card'] = dict(text=reconciliation.closing(st, reconciliation.completion_time(today)),
                                       parse_mode='HTML', buttons=[])
    seeded = seed.ensure()
    st = load_state()
    st.update({'completed_at': _now(), 'answers': dict(st.get('answers') or {}, **flow['answers']),
               'sweep': st.get('sweep') or {'mode': 'retro', 'dow': None, 'at': None},
               'propose_on': (today + datetime.timedelta(days=PROPOSE_DAYS)).isoformat() if flow.get('path') == 'new' else None})
    st.setdefault('auto', [])
    st['completion_emitted_at'] = _now()
    st['completion_card'] = flow['completion_card']
    import copy
    st.setdefault('legacy_flows', []).append(dict(flow=copy.deepcopy(flow),
        completed_at=st['completed_at'], reason='completed'))
    st.pop('flow', None)
    save_state(st)
    done = {'steps': [('seed', seeded)], 'completion_emitted': True, 'standalone': True, 'parse_mode': 'HTML'}
    done['summary'] = None
    done['notes'] = []
    done['guide'] = None
    done['ready'] = flow['completion_card']['text']
    return done


def run_apply(archive=None, sweep=None, auto=None, alias_writes=(), today=None, spend_app=None):
    """During onboarding: all answers/actions and the standalone completion screen prepared -> emit it as ready.
    The installed relay delivers ready alone, using its existing completion contract. Before the first completion without a flow: refused. After completion: the flags (sweep change, SMS
    opt-in/out)."""
    st = load_state()
    if st.get('correction'):
        raise ValueError('unanswered allocation correction')
    legacy = st.get('flow')
    flow = _flow(st)
    if legacy and not flow:
        save_state(st)
    flags = archive is not None or sweep is not None or auto is not None or spend_app is not None
    if flow and flow.get('controller') == 'history_v1':
        if flags or not flow.get('completion_prepared_at') or flow.get('pending'):
            raise ValueError('unanswered: finish the issued history flow before apply')
        return _finish(st, flow, today or datetime.date.today())
    if flow:
        if flags:
            raise ValueError('answers-per-turn: during onboarding the answers come from `onboarding answer`, '
                             'not apply flags')
        left = [i for i in _ids(flow) if i not in flow['answers']]
        if left or flow['pending']:
            raise ValueError('unanswered: %s -- `onboarding next` asks them one at a time'
                             % ','.join(left or [flow['pending'] or 'file']))
        undone = _unexecuted(flow)
        if undone:
            raise ValueError('unexecuted: the owner chose %s but it was not carried out -- run it first'
                             % ','.join(undone))
        return _finish(st, flow, today or datetime.date.today())
    if not st.get('completed_at'):
        raise ValueError('ask-first: run `onboarding next` and ask one question per message, '
                         'recording each with `onboarding answer`')
    if auto is None:
        auto = st.get('auto', []) if spend_app is not None or sweep is not None else []
    return apply(archive=archive, sweep=sweep if sweep is not None else _sweep_str(st),
                 auto=auto, alias_writes=alias_writes, today=today, spend_app=spend_app)


def _sweep_str(st):
    sw = st.get('sweep') or {}
    return '%s@%s' % (sw['dow'], sw['at']) if sw.get('mode') == 'time' else 'retro'


def planned_ids(today=None):
    flow = _new_flow(today or datetime.date.today())
    return _ids(flow)


def parse_sweep(val):
    """'retro' | '<mon..sun>@HH:MM' -> ('retro', None, None) | ('time', dow, 'HH:MM')."""
    if val in (None, '', 'retro'):
        return 'retro', None, None
    dow, _, hhmm = val.partition('@')
    try:
        t = datetime.datetime.strptime(hhmm, '%H:%M').strftime('%H:%M')
    except ValueError:
        t = None
    if dow not in DOW or t is None:
        raise ValueError('sweep must be retro or <mon..sun>@HH:MM')
    return 'time', dow, t


def _routine(action, name, *args):
    ctl = paths.routine_ctl()
    if not ctl.is_file():
        if action == 'enable':
            raise ValueError('routine-ctl missing: cannot enable %s' % name)
        return {'routine': name, 'action': action, 'status': 'routine-ctl missing'}
    p = subprocess.run(['bash', str(ctl), action, name] + list(args),
                       capture_output=True, text=True, cwd=str(paths.root()), timeout=120)
    if action == 'enable' and p.returncode:
        raise ValueError('routine enable failed: %s rc=%d' % (name, p.returncode))
    return {'routine': name, 'action': action, 'rc': p.returncode}


def apply(archive=None, sweep='retro', auto=(), alias_writes=(), today=None, spend_app=None):
    """After completion: the sweep change and the card-SMS opt-in/out (explicit request only). archive: None = keep
    the rows; an int = the row count the owner said yes to -- the reset runs only if the ledger still holds exactly
    that many rows. Callers go through run_apply (the one-question-per-turn gate)."""
    conf = paths.conf()
    today = today or datetime.date.today()
    if spend_app is not None:
        spend_app = sources.clean_app(spend_app)
    auto = list(auto)
    docs = issuers.load_all()
    for iss in auto:
        if iss not in docs or not issuers.sms_offerable(docs[iss]):
            raise ValueError('no SMS automation for %s' % iss)
    mode, dow, at = parse_sweep(sweep)
    done = {'steps': []}
    st = load_state()
    # 1. archive BEFORE anything is staged. Needs the explicit yes the caller passes in.
    if archive is not None:
        now = kit.spend_row_count()
        if now != archive:
            raise ValueError('ledger now holds %d rows, owner agreed to archive %d -- ask again' % (now, archive))
        res = kit.archive_reset(archive)
        st['archive'] = {'at': datetime.datetime.now().isoformat(timespec='seconds'),
                         'count': res['count'], 'archive': res.get('archive'), 'snapshot': res.get('snapshot')}
        done['steps'].append(('archive', res['status'], res['count']))
    # Revoke before disabling sources: retro and held replay must stop too.
    if not auto:
        done['steps'].append(('consent', kit.sms_consent(False)))
    # 2. sources + match rule as data (csv + screenshot always, sms as chosen).
    done['steps'].append(('seed', seed.ensure(sms_issuers=auto, enable_sms=set(auto))))
    if spend_app:
        paths.conf_set(sources.SPEND_APP_KEY, spend_app)
        done['steps'].append(('spend_app', sources.SPEND_APP_KEY))
    # 3. card aliases the SMS lane emits (one fitting card, no question).
    for a in alias_writes:
        if a['issuer'] in auto:
            kit.method_alias_set(a['method_id'], a['aliases'])
            done['steps'].append(('alias', a['method'], a['aliases'][-1]))
    # 4. schedules: the SMS poll only with automation; the sweep nudge only for a separate sweep time.
    if auto:
        done['steps'].append(('schedule', _routine('enable', 'ledger-sms-poll', 'routines/bundle/ledger-sms-poll.sh',
                                                   conf['LEDGER_SMS_POLL_AT'])))
    else:
        done['steps'].append(('schedule', _routine('disable', 'ledger-sms-poll')))
    if mode == 'time':
        done['steps'].append(('schedule', _routine('enable', 'ledger-sweep', 'routines/bundle/ledger-sweep.sh', at, dow)))
    else:
        done['steps'].append(('schedule', _routine('disable', 'ledger-sweep')))
    # 5. verification watch for the chosen cards.
    automation.start(auto, today)
    # 6. Approve only after setup succeeds; repeated yes preserves the floor.
    if auto:
        done['steps'].append(('consent', kit.sms_consent(True)))
    st.update({'completed_at': datetime.datetime.now().isoformat(timespec='seconds'),
               'sweep': {'mode': mode, 'dow': dow, 'at': at}, 'auto': auto,
               'poll_at': conf['LEDGER_SMS_POLL_AT'] if auto else None})
    save_state(st)
    # 7. the guide (instructions) and the ready line.
    done['guide'] = guide.render(auto, docs=docs) if auto else None
    from . import reconciliation
    done['ready'] = reconciliation.closing(st, reconciliation.completion_time(today))
    done['parse_mode'] = 'HTML'
    st['completion_card'] = dict(text=done['ready'], parse_mode='HTML', buttons=[])
    save_state(st)
    return done


def ready_line(mode, dow, at, auto_names):
    parts = [text.t('ledger.onboarding.ready')]
    if mode == 'retro':
        parts.append(text.t('ledger.onboarding.ready.retro'))
    else:
        parts.append(text.t('ledger.onboarding.ready.time', dow=text.dow(dow), at=at))
    if auto_names:
        parts.append(text.t('ledger.onboarding.ready.auto', names='/'.join(auto_names)))
    return ' '.join(parts)


def _backfill_tasks(title):
    rows = []
    for line in kit.run('task-find', title, parse=False).splitlines():
        fields = line.split('\t')
        if len(fields) >= 4 and fields[0].isdigit() and fields[1] == title:
            rows.append({'id': fields[0], 'state': fields[3]})
    return rows


def _ensure_backfill(st):
    if st.get('backfill', {}).get('pending'):
        task_id = st['backfill'].get('task_id')
        if task_id:
            kit.run('task-origin-set', task_id, 'module_ledger', parse=False)
        return
    title = _t('backfill.todo')
    rows = _backfill_tasks(title)
    # A same-title owner task is not proof of workflow origin. When state
    # has no saved task id, create and stamp this producer's own identity.
    created = kit.run('task-add', title, *(['--new'] if rows else []), parse=False)
    task_id = created.split('\t', 1)[0].strip()
    pending = [r for r in _backfill_tasks(title)
               if r['id'] == task_id and r['state'] == 'todo']
    if not pending:
        raise ValueError('backfill task was not created')
    kit.run('task-origin-set', task_id, 'module_ledger', parse=False)
    st['backfill'] = {'pending': True, 'title': title, 'task_id': pending[0]['id'], 'mentioned': False}


def _complete_backfill(st):
    item = st.get('backfill') or {}
    if item.get('pending'):
        rows = _backfill_tasks(item['title'])
        pending = [r for r in rows if r['id'] == item.get('task_id') and r['state'] == 'todo']
        if pending:
            kit.run('task-done', pending[0]['id'], parse=False)
        item['pending'] = False


def backfill_reminder(mark=True):
    st = load_state()
    item = st.get('backfill') or {}
    if not item.get('pending') or item.get('mentioned'):
        return ''
    if mark:
        item['mentioned'] = True
        save_state(st)
    return _t('backfill.weekly')


def capture_done(st, flow, today):
    """After the existing held screenshot review, advance using confirmed kit records only."""
    method = flow.get('method', '')
    if not method.startswith('capture:'):
        raise ValueError('choose a capture method first')
    app = method.split(':', 1)[1]
    ids = flow.get('capture_items') or []
    items = [r for r in kit.intake_list() if r['id'] in ids and r['source'] == seed.SHOT_SOURCE
             and json.loads(r['data_json']).get('app') == app]
    if not items or any(r['state'] == 'pending' for r in items):
        raise ValueError('review the captured rows before continuing')
    confirmed = [r for r in items if r['state'] == 'confirmed']
    if not confirmed:
        raise ValueError('no confirmed captured records')
    # The card is for the month the owner just captured, not the ledger's default basis (the last full month
    # before the newest row, which is the month before a capture that ends mid-month).
    dates = [json.loads(r['data_json']).get('date') or '' for r in confirmed]
    month = flow.get('capture_month') or max(dates)[:7] or None
    d = kit.ledger_diagnosis(month)
    flow['diag_month'] = d['month']
    first = datetime.date.fromisoformat(d['month'] + '-01')
    next_month = (first.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
    since, until = history_period(next_month, 1)
    flow['history_period'] = {'since': since.isoformat(), 'until': until.isoformat(), 'kind': 'capture'}
    flow['import'] = {'events': len(confirmed), 'question': _pick_question(flow, d)}
    flow['path'] = 'imported'
    flow['answers']['file'] = {'captured': len(confirmed)}
    flow['pending'] = None
    flow['asked_at'] = None
    _complete_backfill(st)
    st['flow'] = flow
    save_state(st)
    return {'imported': True, 'count': len(confirmed), 'next': 'onboarding next'}


def capture(rows, since, until, card=None, today=None):
    """Stage only; the existing review must confirm model-read screenshot rows."""
    st = load_state()
    flow = _flow(st)
    if not flow or flow.get('pending') != 'file' or not flow.get('method', '').startswith('capture:'):
        raise ValueError('not-asked: choose a capture method first')
    if not isinstance(rows, list):
        raise ValueError('rows must be a JSON array')
    out = shot.stage(rows, since, until, app=flow['method'].split(':', 1)[1], card=card, today=today)
    flow['capture_items'] = sorted(set(flow.get('capture_items', []) + [r['item_id'] for r in out['items']]))
    if out['items']:
        flow['capture_month'] = max(flow.get('capture_month') or '', str(until)[:7])
    st['flow'] = flow
    save_state(st)
    out['next'] = 'review show; review apply --expect DIGEST; onboarding answer file captured'
    return out
