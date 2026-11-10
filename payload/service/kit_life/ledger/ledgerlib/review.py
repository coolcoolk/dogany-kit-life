"""Pending-intake review: one table, one answer (design A4 operating budget).

`build()` turns the kit's pending queue into a numbered proposal list. The
proposal is computed, not chosen by the model:

  reversal (approval cancel) -> pair with the confirmed original: same source
                               and approval number, else same amount + merchant
                               + method on or before the cancel date. One hit =
                               propose it; none or several = ask.
  partial / ambiguous        -> one candidate block = propose attaching to it;
                               several = ask which.
  unmatched                  -> method resolved = propose a new record;
                               method unresolved = ask which card it was.
  scope=unknown              -> flagged "business spend?" on the same line.

`apply()` executes the proposals, minus the numbers the owner excluded or
deferred, and only if the plan digest still matches what was shown.

Lanes (owner design 2026-09-24): the DAILY retro shows only items from the
automated path (card SMS from the app, `sms:*` sources) -- short; the WEEKLY
retro runs the sweep and shows everything pending (screenshot, CSV, SMS).

Same payment from two sources, both pending (an SMS on Tuesday, the weekly
screenshot on Sunday): the later row is proposed as "same as N" when card,
amount and direction agree within the match window and the merchant names
agree loosely (one contains the other, or same day). It then lands ON the
block the earlier row created (`--block-id`), so merchant spellings that
differ between an SMS and an app list still give one spend with two links.

Truncated merchants: some issuers cut the store name in the SMS to a few
characters (Hyundai, first real sample 2026-09-24). Such a reading carries merchant_truncated, and every merchant
comparison involving it is a prefix match (the short name starts the full
one) instead of equality. When exactly one earlier record's merchant
extends it, the proposal line shows that full name as a guess.

Opaque payments (owner design 2026-09-26, `opaque.py`): a row whose merchant
is a payment intermediary (Coupay, Naver Pay, PG names) is not shown in the
table but in ONE grouped block under it that asks what each was, answered
on the same line as the table ("ok, 2 tissues, 3 lunch" -> `what`). The weekly
lane adds the ones already written but still unnamed (numbered after the
pending rows); unnamed after the weekly retro -> etc (gita). A monthly repeat of a
named (intermediary, amount) is filled from memory and stays in the table.
"""
import datetime
import hashlib
import html
import json
import unicodedata

from money_format import comma
from . import kit, opaque, text
from .markup import quote_lists


def norm(s):
    return ''.join(ch for ch in unicodedata.normalize('NFKC', s or '').casefold() if ch.isalnum())


def won(n):
    return comma(n) + '\uc6d0'


def mmdd(iso):
    return '%d/%d' % (int(iso[5:7]), int(iso[8:10]))


def _data(item):
    return json.loads(item['data_json'])


def same_merchant(a, b, truncated=False):
    """Equal after normalisation; with a truncated side, one is a prefix."""
    a, b = norm(a), norm(b)
    if not truncated:
        return a == b
    return bool(a and b) and (a.startswith(b) or b.startswith(a))


def _truncated(*ds):
    return any(x.get('merchant_truncated') for x in ds)


def merchant_guess(d, all_items):
    """The one full merchant name an earlier record has for a truncated
    reading, else None."""
    if not d.get('merchant_truncated') or not norm(d['merchant']):
        return None
    short = norm(d['merchant'])
    names = {_data(i)['merchant'] for i in all_items if not _data(i).get('merchant_truncated')}
    full = {n for n in names if norm(n).startswith(short) and norm(n) != short}
    return full.pop() if len(full) == 1 else None


def _reversal_target(item, d, all_items):
    confirmed = [i for i in all_items if i['state'] == 'confirmed' and i['block_id']
                 and i['reversal_of'] is None]
    reversed_ = {i['reversal_of'] for i in all_items if i['reversal_of']}
    hits = []
    if d.get('approval_no'):
        hits = [i for i in confirmed if i['source'] == item['source']
                and _data(i).get('approval_no') == d['approval_no']]
    if not hits:
        hits = [i for i in confirmed
                if _data(i).get('operation', 'payment') == 'payment'
                and _data(i)['amount'] == d['amount']
                and same_merchant(_data(i)['merchant'], d['merchant'], _truncated(_data(i), d))
                and _data(i).get('method') == d.get('method')
                and _data(i)['date'] <= d['date']]
    blocks = sorted({i['block_id'] for i in hits} - reversed_)
    return blocks


def _pending_original(item, d, rows):
    hits = []
    for prev in rows:
        pd = prev['_d']
        if prev['operation'] != 'payment' or prev['action'][0] == 'drop_pair' or prev['source'] != item['source']:
            continue
        if d.get('approval_no') and pd.get('approval_no') == d['approval_no']:
            return prev
        if (pd['amount'] == d['amount'] and same_merchant(pd['merchant'], d['merchant'], _truncated(pd, d))
                and pd.get('method') == d.get('method') and pd['date'] <= d['date']):
            hits.append(prev)
    return hits[0] if len(hits) == 1 else None


def _loosely_same_merchant(a, b, same_day):
    a, b = norm(a), norm(b)
    return a == b or (a and b and (a in b or b in a)) or same_day


def _same_method(a, b):
    if a == b:
        return bool(a)
    for m in kit.pay_methods():
        names = {m['name']} | set(m.get('aliases') or [])
        if a in names and b in names:
            return True
    return False


def _pending_twin(row, d, rows, window):
    for prev in rows:
        pd = prev['_d']
        if prev['action'][0] != 'new' or prev['source'] == row['source']:
            continue
        days = abs((datetime.date.fromisoformat(pd['date']) - datetime.date.fromisoformat(d['date'])).days)
        same_method = (pd.get('entity_id') == d.get('entity_id') if pd.get('entity_id') and d.get('entity_id')
                       else _same_method(pd.get('method'), d.get('method')))
        if (same_method and pd['amount'] == d['amount']
                and pd.get('kind', 'expense') == d.get('kind', 'expense')
                and pd.get('operation', 'payment') == 'payment'
                and days <= window and _loosely_same_merchant(pd['merchant'], d['merchant'], days == 0)):
            return prev['no']
    return None


LANES = ('daily', 'weekly')


def in_lane(item, lane):
    return lane == 'weekly' or item['source'].startswith('sms:')


def build(lane='weekly', today=None):
    if lane not in LANES:
        raise ValueError('lane must be daily or weekly')
    pending = [i for i in kit.intake_list(pending=True) if in_lane(i, lane)]
    window = (kit.guard_value('dup_window') or 0) if pending else 0
    all_items = kit.intake_list() if pending or lane == 'weekly' else []
    rows = []
    for item in sorted(pending, key=lambda i: i['id']):
        d = _data(item)
        row = {'no': len(rows) + 1, 'item_id': item['id'], 'source': item['source'],
               'date': d['date'], 'amount': d['amount'], 'merchant': d['merchant'],
               'method': d.get('method'), 'operation': d.get('operation', 'payment'),
               'business_suspect': d.get('scope') == 'unknown',
               'extraction': d.get('extraction'), 'channel': d.get('channel'), 'reason': item['reason'],
               'merchant_guess': merchant_guess(d, all_items)}
        if row['operation'] == 'reversal':
            blocks = _reversal_target(item, d, all_items)
            pair = None if blocks else _pending_original(item, d, rows)
            if pair:
                # Approved and cancelled before review: neither is a spend
                # (design A2-3). Both rows are excluded, the ledger gets 0.
                pair['action'] = ('drop_pair', len(rows) + 1)
                row['action'] = ('drop_pair', pair['no'])
            else:
                row['action'] = ('reversal', blocks[0]) if len(blocks) == 1 else ('ask', blocks)
        elif d.get('entity_id') is None:
            row['action'] = ('ask_method', None)
        elif item['reason'] in ('partial', 'ambiguous', 'held'):
            cands = item['candidate_blocks']
            if len(cands) == 1:
                row['action'] = ('attach', cands[0])
            elif cands:
                row['action'] = ('ask', cands)
            else:
                row['action'] = ('new', None)
        else:
            row['action'] = ('new', None)
        if row['action'][0] == 'new':
            twin = _pending_twin(row, d, rows, window)
            if twin:
                # Same payment seen by another source, both still pending. The
                # earlier row lands first; the kit then attaches this one as
                # its unique exact match (a second link, not a second spend).
                row['action'] = ('same_as', twin)
        row['_d'] = d
        rows.append(row)
    st = opaque.load_state()
    _mark_opaque(rows, st)
    for r in rows:
        del r['_d']
    if lane == 'weekly':
        rows += _landed_opaque(rows, all_items, st, today or datetime.date.today())
    digest = hashlib.sha256(json.dumps([lane] + [_digest_key(r) for r in rows],
                                       sort_keys=True).encode()).hexdigest()[:12]
    plan = {'rows': rows, 'digest': digest, 'lane': lane}
    if rows:
        from . import merchant_names
        merchant_names.refresh(plan)
        merchant_names.attach(plan, rows, plan)
    if any(r.get('channel') == 'screenshot' for r in rows):
        plan['card'] = {'text': render(plan), 'parse_mode': 'HTML', 'buttons': [[{
            'text': text.t('ledger.review.accept'), 'action': 'apply',
            'data': {'expect': digest, 'lane': lane}}]]}
    return plan


def _digest_key(r):
    o = r.get('opaque')
    if not o:
        return (r['item_id'], r['action'])
    return (r['item_id'], r['action'], o['key'], bool(o.get('fill')), bool(o.get('twin')))


def _opaque_info(st, item_id, d):
    """{key, display, asked, fill?} when the merchant is an intermediary."""
    if d.get('operation', 'payment') != 'payment' or d.get('kind', 'expense') != 'expense':
        return None
    e = opaque.match(d['merchant'], d.get('merchant_truncated'))
    if not e:
        return None
    info = {'key': e['key'], 'display': e['display_ko'], 'asked': opaque.item_state(st, item_id)['asked']}
    mem = opaque.recall(st, e['key'], d['amount'], d['date'])
    if mem:
        info['fill'] = {'name': mem['name'], 'category': mem['category']}
    return info


def _mark_opaque(rows, st):
    for r in rows:
        if r['action'][0] != 'drop_pair':
            r['opaque'] = _opaque_info(st, r['item_id'], r['_d'])
    by_no = {r['no']: r for r in rows}
    for r in rows:
        if r['action'][0] != 'same_as':
            continue
        twin = by_no[r['action'][1]]
        # The same payment seen by a source that names the store: nothing to ask.
        if r.get('opaque') and not twin.get('opaque'):
            r['opaque']['twin'] = twin['merchant']
        elif twin.get('opaque') and not r.get('opaque'):
            twin['opaque']['twin'] = r['merchant']


def asks(r):
    """Row goes to the grouped "what was it" block."""
    o = r.get('opaque')
    return bool(o) and not o.get('fill') and not o.get('twin')


def _landed_opaque(rows, all_items, st, today):
    """Weekly: opaque payments already written and still unnamed -- the ones
    the daily retro asked (tracked open) plus untracked ones of the last 7
    days (auto-accepted or written before this lane existed)."""
    clear_blocks = {i['block_id'] for i in all_items if i['block_id'] and i['state'] == 'confirmed'
                    and not opaque.match(_data(i)['merchant'], _data(i).get('merchant_truncated'))}
    clear_blocks |= {r['action'][1] for r in rows if r['action'][0] == 'attach' and not r.get('opaque')}
    since = (today - datetime.timedelta(days=7)).isoformat()
    out, seen = [], set()
    for i in all_items:
        if i['state'] != 'confirmed' or not i['block_id'] or i['reversal_of'] is not None:
            continue
        if i['block_id'] in clear_blocks or i['block_id'] in seen:
            continue
        d = _data(i)
        info = _opaque_info(st, i['id'], d)
        if not info:
            continue
        tracked = st['items'].get(str(i['id']))
        if tracked and tracked['status'] != 'open' or not tracked and d['date'] < since:
            continue
        try:
            sid = kit.spend_for_block(i['block_id'])
        except (kit.KitError, KeyError, TypeError, ValueError):
            continue
        if kit.spend_category(sid, d['date']) != '(\ubbf8\ubd84\ub958)':
            continue                                    # the owner already sorted it
        seen.add(i['block_id'])
        out.append({'no': len(rows) + len(out) + 1, 'item_id': i['id'], 'source': i['source'],
                    'date': d['date'], 'amount': d['amount'], 'merchant': d['merchant'],
                    'method': d.get('method'), 'operation': 'payment', 'business_suspect': False,
                    'extraction': None, 'channel': None, 'reason': 'landed', 'merchant_guess': None,
                    'action': ('landed', i['block_id']), 'opaque': info})
    return out


def _describe_block(bid, all_items):
    for i in all_items:
        if i['block_id'] == bid and i['reversal_of'] is None:
            d = _data(i)
            from . import merchant_names
            return '%s %s %s' % (mmdd(d['date']), merchant_names.resolve(d['merchant']), won(d['amount']))
    return '\uae30\uc874 \uae30\ub85d'


MODEL_NOTE = {'sms': ' (\ubb38\uc790\ub97c \uc81c\uac00 \uc77d\uc5c8\uc5b4\uc694)', 'screenshot': ' (\ucea1\ucc98\ub97c \uc81c\uac00 \uc77d\uc5c8\uc5b4\uc694)'}


def render(plan):
    """Owner-facing Korean. No verb, table, flag or source key appears."""
    rows = plan['rows']
    if not rows:
        return '\uac00\uacc4\ubd80\uc5d0 \ud655\uc778\ud560 \uac8c \uc5c6\uc5b4\uc694.'
    all_items = kit.intake_list()
    table = [r for r in rows if not asks(r)]
    group = [r for r in rows if asks(r)]
    # One source for every row (a capture): say it once in the header, not per row.
    shot_only = bool(rows) and not group and all(r.get('channel') == 'screenshot' and r['extraction'] == 'model' for r in rows)
    lines = ['\uac00\uacc4\ubd80 \ud655\uc778\ud560 \uac83 %d\uac74' % len(table)] if table else []
    if shot_only:
        lines = ['\ucea1\ucc98\uc5d0\uc11c \uc77d\uc740 %d\uac74\uc774\uc5d0\uc694.' % len(table)]
    for r in table:
        merchant = r.get('display_name', r['merchant'])
        if r.get('merchant_guess'):
            merchant += '(%s?)' % r['merchant_guess']
        head = '%d. %s %s %s' % (r['no'], mmdd(r['date']), merchant,
                                 won(r['amount']) if r['operation'] != 'reversal' else '-' + won(r['amount']) + ' \ucde8\uc18c')
        if r['method']:
            head += ' / ' + r['method']
        kind, arg = r['action']
        if kind == 'new':
            tail = '\uc0c8\ub85c \uc801\uae30'
        elif kind == 'attach':
            tail = '\uc774\ubbf8 \uc801\ud78c %s \uae30\ub85d\uacfc \uac19\uc740 \uac74\uc73c\ub85c \ubcf4\uae30' % _describe_block(arg, all_items)
        elif kind == 'reversal':
            tail = '%s \uacb0\uc81c \ucde8\uc18c\ub85c \uc801\uae30' % _describe_block(arg, all_items)
        elif kind == 'drop_pair':
            tail = ('%d\ubc88 \uacb0\uc81c\uc758 \ucde8\uc18c\ub77c\uc11c \ub458 \ub2e4 \uc548 \uc801\uae30' if r['operation'] == 'reversal'
                    else '%d\ubc88\uc5d0\uc11c \ubc14\ub85c \ucde8\uc18c\ub3fc\uc11c \ub458 \ub2e4 \uc548 \uc801\uae30') % arg
        elif kind == 'same_as':
            tail = '%d\ubc88\uacfc \uac19\uc740 \uacb0\uc81c (\ud55c \ubc88\ub9cc \uc801\ud600\uc694)' % arg
        elif kind == 'ask_method':
            tail = '\uc5b4\ub290 \uce74\ub4dc\uc778\uc9c0 \uc54c\ub824\uc8fc\uc138\uc694'
        else:
            tail = '\uc5b4\ub290 \uae30\ub85d\uacfc \uac19\uc740 \uac74\uc9c0 \uc54c\ub824\uc8fc\uc138\uc694' if r['operation'] != 'reversal' else '\uc5b4\ub290 \uacb0\uc81c\uc758 \ucde8\uc18c\uc778\uc9c0 \uc54c\ub824\uc8fc\uc138\uc694'
        if r['extraction'] == 'model' and not shot_only:
            tail += MODEL_NOTE.get(r['channel'], ' (\uc81c\uac00 \uc77d\uc5c8\uc5b4\uc694)')
        if r['business_suspect']:
            tail += ' / \uc0ac\uc5c5 \uc9c0\ucd9c\uc778\uac00\uc694?'
        if (r.get('opaque') or {}).get('fill'):
            tail += ' (\uc9c0\ub09c\ub2ec\ucc98\ub7fc %s)' % r['opaque']['fill']['name']
        lines.append('%s: %s' % (head, tail))
    if group:
        if lines:
            lines.append('')
        lines.append('\uacb0\uc81c\ub300\ud589\uc774\ub77c \ubb58 \uc0c0\ub294\uc9c0 \uc548 \ubcf4\uc774\ub294 \uacb0\uc81c %d\uac74%s'
                     % (len(group), ' (\ubaa8\ub974\ub294 \uac74 \uae30\ud0c0\ub85c \ub458\uac8c\uc694)' if plan['lane'] == 'weekly' else ''))
        for r in group:
            lines.append(_group_line(r, all_items))
    pending = [r for r in rows if r['action'][0] != 'landed']
    ask = (['\ube84 \uac74 \ubc88\ud638\ub85c \uc54c\ub824 \uc8fc\uc138\uc694.'] if shot_only
           else ['\ub2e4 \ub9de\uc73c\uba74 "\uc88b\uc544", \ube84 \uac74 \ubc88\ud638\ub85c \uc54c\ub824\uc8fc\uc138\uc694.']) if pending else []
    if group:
        eg = ', '.join('%d %s' % (r['no'], w) for r, w in zip(group, ('\ud734\uc9c0', '\uc810\uc2ec')))
        ask.append('%s \ubb50\uc600\ub294\uc9c0 "%s"\ucc98\ub7fc \ud55c \uc904\ub85c%s \uc54c\ub824\uc8fc\uc138\uc694.'
                   % ('\ub300\ud589 \uacb0\uc81c\ub294' if pending else '\ub300\ud589 \uacb0\uc81c\uac00', eg, ' \uac19\uc774' if pending else ''))
    lines.append(' '.join(ask))
    escaped = [html.escape(line) for line in lines]
    for n, line in enumerate(lines[:-1]):
        if line and not line[0].isdigit() and lines[n+1][:1].isdigit():
            escaped[n] = '<b>'+escaped[n]+'</b>'
    return quote_lists('\n'.join(escaped))


def _group_line(r, all_items):
    """One grouped opaque row. No em-dash in new owner copy (owner rule
    2026-09-26); the table line above keeps its pinned separator."""
    merchant = r.get('display_name', r['merchant'])
    if opaque.norm(r['opaque']['display']) not in opaque.norm(merchant):
        merchant += '(%s)' % r['opaque']['display']
    parts = ['%d. %s %s %s' % (r['no'], mmdd(r['date']), merchant, won(r['amount']))]
    if r['method']:
        parts.append(r['method'])
    kind, arg = r['action']
    if kind == 'landed':
        parts.append('\uc774\ubbf8 \uc801\uc5b4\ub454 \uacb0\uc81c')
    elif kind == 'attach':
        parts.append('\uc774\ubbf8 \uc801\ud78c %s \uae30\ub85d\uacfc \uac19\uc740 \uac74\uc73c\ub85c \ubcf4\uae30' % _describe_block(arg, all_items))
    elif kind == 'same_as':
        parts.append('%d\ubc88\uacfc \uac19\uc740 \uacb0\uc81c' % arg)
    elif kind == 'ask_method':
        parts.append('\uc5b4\ub290 \uce74\ub4dc\uc778\uc9c0 \uc54c\ub824\uc8fc\uc138\uc694')
    elif kind == 'ask':
        parts.append('\uc5b4\ub290 \uae30\ub85d\uacfc \uac19\uc740 \uac74\uc9c0 \uc54c\ub824\uc8fc\uc138\uc694')
    line = ' / '.join(parts)
    if r['extraction'] == 'model':
        line += MODEL_NOTE.get(r['channel'], ' (\uc81c\uac00 \uc77d\uc5c8\uc5b4\uc694)')
    return line


def apply(expect, exclude=(), defer=(), new=(), business=(), personal=(), attach=None, reversal=None,
          lane='weekly', what=None, today=None):
    """Execute the plan. exclude: drop (not a spend). defer: leave pending.
    new: force a new record despite candidates. attach/reversal: {no: block_id}
    answers to 'ask' rows. business/personal: scope answers for suspects.
    what: {no: (name, category|None)} answers to the grouped opaque block."""
    plan = build(lane, today=today)
    if plan['digest'] != expect:
        raise ValueError('the pending list changed since it was shown; render it again')
    attach, reversal = attach or {}, reversal or {}
    answers = set(what or {})
    unknown = answers - {r['no'] for r in plan['rows']}
    if unknown:
        raise ValueError('no row numbered %s in the shown list' % ','.join(map(str, sorted(unknown))))
    invalid = answers - {r['no'] for r in plan['rows'] if asks(r)}
    if invalid:
        raise ValueError('row %s is not asking what was bought' % ','.join(map(str, sorted(invalid))))
    conflicting = answers & (set(exclude) | set(defer))
    if conflicting:
        raise ValueError('row %s cannot be answered and excluded or deferred' %
                         ','.join(map(str, sorted(conflicting))))
    out = []
    landed = {}
    for r in plan['rows']:
        no, iid = r['no'], r['item_id']
        kind, arg = r['action']
        if kind == 'landed':
            continue                                    # already written; only `what` applies
        if no in defer:
            out.append((no, 'deferred'))
            continue
        if no in exclude or (kind == 'drop_pair' and no not in new):
            kit.intake_resolve(iid, 'exclude')
            out.append((no, 'excluded'))
            continue
        scope = 'business' if no in business else 'personal' if no in personal else None
        def attach_to(target):
            target_kind, target_id = target
            return kit.intake_resolve(iid, 'confirm', scope=scope,
                                      **{target_kind + '_id': target_id})
        try:
            if no in attach:
                res = attach_to(attach[no])
            elif no in reversal or kind == 'reversal':
                res = kit.intake_resolve(iid, 'confirm', reversal_of=reversal.get(no, arg), scope=scope)
            elif no in new:
                res = kit.intake_resolve(iid, 'confirm', new=True, scope=scope)
            elif kind == 'attach':
                res = kit.intake_resolve(iid, 'confirm', block_id=arg, scope=scope)
            elif kind == 'same_as' and landed.get(arg):
                res = kit.intake_resolve(iid, 'confirm', block_id=landed[arg], scope=scope)
            elif kind in ('new', 'same_as'):
                res = kit.intake_resolve(iid, 'confirm', scope=scope)
            else:
                out.append((no, 'needs-answer'))
                continue
        except kit.KitError as e:
            out.append((no, 'refused: ' + e.message))
            continue
        out.append((no, res['status']))
        landed[no] = res['item'].get('block_id')
    return out + settle_opaque(plan, dict(out), landed, what or {})


def settle_opaque(plan, status, landed, what):
    """After landing: write answers (note + category via spend-upd), fill
    monthly repeats from memory, count the ask, and close what the weekly
    retro left unnamed as etc (gita). -> extra (no, outcome) results."""
    lane = plan['lane']
    todo = [r for r in plan['rows'] if r.get('opaque') or r['no'] in what]
    if not todo:
        return []
    st = opaque.load_state()
    out = []
    for r in todo:
        o, no = r.get('opaque'), r['no']
        ist = st['items'].setdefault(str(r['item_id']), {'asked': [], 'status': 'open'})
        bid = r['action'][1] if r['action'][0] == 'landed' else landed.get(no)
        if not bid:
            if status.get(no) == 'excluded':
                ist['status'] = 'excluded'
            else:
                if no in what:
                    ist['answer'] = list(what[no])      # lands with it later
                if asks(r) and lane not in ist['asked']:
                    ist['asked'].append(lane)
            continue
        sid = kit.spend_for_block(bid)
        ans = what.get(no) or (tuple(ist['answer']) if ist.get('answer') else None)
        if ans:
            name, cat = ans
            kit.spend_upd(sid, **dict({'note': name}, **({'category': cat} if cat else {})))
            if o:
                opaque.remember(st, o['key'], r['amount'], r['date'], name, cat)
            ist['status'], outcome = 'named', 'named'
        elif not o:
            continue
        elif o.get('fill'):
            f = o['fill']
            kit.spend_upd(sid, **dict({'note': f['name']}, **({'category': f['category']} if f['category'] else {})))
            opaque.remember(st, o['key'], r['amount'], r['date'], f['name'], f['category'])
            ist['status'], outcome = 'auto', 'filled'
        elif o.get('twin'):
            kit.spend_upd(sid, note=o['twin'])
            ist['status'], outcome = 'twin', 'twin'
        elif lane == 'weekly' or 'weekly' in ist['asked']:
            kit.spend_upd(sid, category=opaque.ETC)     # asked twice: no third ask
            ist['status'], outcome = 'etc', 'etc'
        else:
            if lane not in ist['asked']:
                ist['asked'].append(lane)
            ist['status'], outcome = 'open', 'ask-weekly'
        ist.pop('answer', None)
        ist['block_id'] = bid
        out.append((no, outcome))
    opaque.save_state(st)
    return out
