"""Contract/warranty capture, owner cards and existing one-off task reminders.

Facts commit before proof/schedule enrichment. The normal scheduler owns
notification selection, delivery, snooze and settlement. No polling lane.
"""
import argparse
import calendar
import datetime as dt
import hashlib
import html
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import uuid
from zoneinfo import ZoneInfo
from locale_store import read_json

FIELDS = {'starts_on', 'ends_on', 'renews_on', 'cancel_by', 'auto_renew',
          'amount', 'cycle', 'bought_on', 'term_months', 'start_basis',
          'cancel_terms', 'effective_on', 'fee', 'refund', 'avoidable',
          'unavoidable', 'reminder_days', 'reminder_at', 'reminder_enabled',
          'status', 'quote_on', 'end_kind'}
DATES = {'starts_on', 'ends_on', 'renews_on', 'cancel_by', 'bought_on', 'effective_on', 'quote_on'}
MONEY = {'amount', 'fee', 'refund', 'avoidable', 'unavoidable'}
SOURCE = {'owner', 'document', 'screen', 'calculated', 'estimated'}


def rows(c, sql, args=()):
    cur = c.execute(sql, args)
    return [dict(zip([x[0] for x in cur.description], r)) for r in cur]


def get(c, owner, identifier):
    rs = rows(c, 'SELECT * FROM contract_record WHERE id=? AND owner=?', (identifier, owner))
    if not rs:
        raise ValueError('contract not found for this owner')
    r = rs[0]
    for k in ('facts', 'sources'):
        r[k] = json.loads(r[k])
    r['proofs'] = rows(c, 'SELECT id,label,digest FROM contract_proof WHERE contract_id=? ORDER BY id', (r['id'],))
    r['reminder'] = next(iter(rows(c, 'SELECT r.*,t.start_at,t.settled_outcome,t.notify_policy FROM contract_reminder r '
                                 'LEFT JOIN timeblock t ON t.id=r.block_id WHERE r.contract_id=?', (r['id'],))), None)
    return r


def validate(facts):
    if not isinstance(facts, dict) or set(facts) - FIELDS:
        raise ValueError('unknown contract fields')
    for k, v in facts.items():
        if v is None:
            continue
        if k in DATES:
            if not isinstance(v, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', v):
                raise ValueError(k + ' requires YYYY-MM-DD')
            dt.date.fromisoformat(v)
        if k in MONEY | {'term_months', 'reminder_days'}:
            if type(v) is not int or v < 0 or (k == 'term_months' and v == 0):
                raise ValueError(k + ' requires a nonnegative integer')
        if k in {'auto_renew', 'reminder_enabled'} and type(v) is not bool:
            raise ValueError(k + ' requires boolean or null')
        if k == 'reminder_at' and (not isinstance(v, str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', v)):
            raise ValueError('reminder_at requires HH:MM')
        if k == 'cycle' and v not in ('monthly', 'yearly', 'weekly'):
            raise ValueError('cycle requires monthly|yearly|weekly')
        if k == 'end_kind' and v not in ('date', 'none', 'unknown'):
            raise ValueError('end_kind requires date|none|unknown')
        if k == 'status' and v not in ('active', 'cancelled', 'returned', 'disposed'):
            raise ValueError('unknown contract status')
        if k == 'start_basis' and v not in ('purchase', 'stated'):
            raise ValueError('start_basis requires purchase|stated')
        if k == 'cancel_terms' and not isinstance(v, str):
            raise ValueError('cancel_terms requires text')
    if facts.get('starts_on') and facts.get('ends_on') and facts['starts_on'] > facts['ends_on']:
        raise ValueError('contract ends before it starts')


def derive(f, sources):
    # A term alone never supplies its own start basis. Purchase date is used
    # only when explicitly selected by the owner/extractor, and labelled.
    if sources.get('ends_on') == 'calculated':
        f['ends_on'] = None
        sources.pop('ends_on', None)
    start = f.get('starts_on')
    if not start and f.get('start_basis') == 'purchase':
        start = f.get('bought_on')
    if f.get('term_months') and start and not f.get('ends_on'):
        d = dt.date.fromisoformat(start)
        n = d.year * 12 + d.month - 1 + f['term_months']
        y, m = divmod(n, 12)
        try:
            end = dt.date(y, m + 1, min(d.day, calendar.monthrange(y, m + 1)[1])) - dt.timedelta(days=1)
        except (ValueError, OverflowError):
            return  # Keep the supplied term; an unrepresentable end stays unknown.
        f['ends_on'] = end.isoformat()
        sources['ends_on'] = 'calculated'


def capture(K, c, owner, d):
    facts = dict(d.get('facts', {}))
    validate(facts)
    source = d.get('source', 'owner')
    sources = d.get('sources', {})
    if source not in SOURCE or not isinstance(sources, dict) or set(sources) - set(facts) or any(v not in SOURCE for v in sources.values()):
        raise ValueError('invalid field provenance')
    c.execute('BEGIN IMMEDIATE')
    try:
        r = None
        if d.get('id') is not None:
            r = get(c, owner, d['id'])
        ref = d.get('source_ref')
        slot = str(d.get('source_slot', '0'))
        seen = c.execute('SELECT contract_id FROM contract_evidence WHERE owner=? AND source_ref=? AND source_slot=?',
                         (owner, ref, slot)).fetchone() if ref else None
        if seen:
            if r and r['id'] != seen[0]:
                raise ValueError('source is already bound to another contract')
            # Identical screenshot retransmission must not undo later corrections.
            r = get(c, owner, seen[0])
            c.commit()
            return r
        key = d.get('record_key')
        if r is None and key:
            hit = c.execute('SELECT id FROM contract_record WHERE owner=? AND record_key=?', (owner, key)).fetchone()
            if hit:
                r = get(c, owner, hit[0])
        if r is not None and d.get('revision') is not None and d['revision'] != r['revision']:
            raise ValueError('card changed; read the current contract again')
        if r is None:
            title = d.get('title')
            if not isinstance(title, str) or not title.strip():
                raise ValueError('capture needs a title; other facts may be unknown')
            key = key or ('source:' + ref + ':' + slot if ref else str(uuid.uuid4()))
            cur = c.execute('INSERT INTO contract_record(owner,record_key,kind,title,provider) VALUES(?,?,?,?,?)',
                            (owner, key, d.get('kind', 'contract'), title.strip(), d.get('provider')))
            r = get(c, owner, cur.lastrowid)
        merged = dict(r['facts'], **facts)
        origins = dict(r['sources'])
        for k, v in facts.items():
            if v is None:
                origins.pop(k, None)
            else:
                origins[k] = sources.get(k, source)
        derive(merged, origins)
        validate(merged)
        title, provider, kind = d.get('title', r['title']), d.get('provider', r['provider']), d.get('kind', r['kind'])
        if not isinstance(title, str) or not title.strip() or not isinstance(kind, str) or not kind.strip():
            raise ValueError('title and kind must be nonempty text')
        fixed = d.get('fixed_id', r['fixed_id'])
        owned = d.get('owned_id', r['owned_id'])
        if owned is not None:
            item = c.execute('SELECT bought_on,status FROM owned_item WHERE id=?', (owned,)).fetchone()
            if not item:
                raise ValueError('possession not found')
            if item[0] and ('bought_on' not in merged or (owned != r['owned_id'] and 'bought_on' not in facts)):
                merged['bought_on'] = item[0]
                origins['bought_on'] = 'document' if source in ('document', 'screen') else 'owner'
                derive(merged, origins)
        if fixed is None and kind != 'warranty' and merged.get('amount') is not None and merged.get('cycle'):
            # Provider alone is NEVER an identity. Do not coalesce two lines at
            # one provider, nor reuse a stream already linked to another term.
            name = (provider + ' / ' if provider else '') + title
            hits = c.execute('SELECT f.id FROM fixed_cost f WHERE f.name=? AND f.amount=? AND f.cycle=? '
                             "AND f.direction='expense' AND f.state<>'stopped' "
                             'AND NOT EXISTS(SELECT 1 FROM contract_record r WHERE r.fixed_id=f.id AND r.id<>?)',
                             (name, merged['amount'], merged['cycle'], r['id'])).fetchall()
            if len(hits) == 1:
                fixed = hits[0][0]
            else:
                # Transaction-aware insert: fixed_add commits even with a supplied
                # connection. This fact capture never enables automatic spends.
                fixed = c.execute("INSERT INTO fixed_cost(name,amount,cycle,source) VALUES(?,?,?,'chat')",
                                  (name, merged['amount'], merged['cycle'])).lastrowid
        if fixed is not None and r['fixed_id'] == fixed and any(k in facts for k in ('amount', 'cycle')):
            other = c.execute('SELECT 1 FROM contract_record WHERE fixed_id=? AND id<>?', (fixed, r['id'])).fetchone()
            if not other and merged.get('amount') is not None and merged.get('cycle'):
                c.execute('UPDATE fixed_cost SET amount=?,cycle=? WHERE id=?', (merged['amount'], merged['cycle'], fixed))
        for field in ('title', 'provider'):
            if field in d and d[field] is not None:
                origins[field] = source
        changed = (merged != r['facts'] or origins != r['sources'] or title != r['title'] or provider != r['provider']
                   or kind != r['kind'] or fixed != r['fixed_id'] or owned != r['owned_id'])
        c.execute("UPDATE contract_record SET title=?,provider=?,kind=?,facts=?,sources=?,fixed_id=?,owned_id=?,"
                  "revision=revision+?,updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?",
                  (title.strip(), provider, kind, json.dumps(merged), json.dumps(origins), fixed, owned, int(changed), r['id']))
        if ref:
            c.execute('INSERT INTO contract_evidence VALUES(?,?,?,?,?,?)',
                      (owner, ref, slot, r['id'], d.get('words'), json.dumps(facts)))
        c.commit()
    except Exception:
        c.rollback()
        raise
    return get(c, owner, r['id'])


def proof_root(K, owner):
    return Path(K.SCRIPT_DIR).parent / 'files' / 'contracts' / hashlib.sha256(owner.encode()).hexdigest()[:24]


def keep_proof(K, c, owner, identifier, path, label):
    get(c, owner, identifier)
    src = Path(path)
    digest = hashlib.sha256()
    base = proof_root(K, owner)
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Copy to an owner-only temporary file; rename only after the full stream
    # and fsync succeed. The reference is relative to the instance root.
    fd, tmp = tempfile.mkstemp(prefix='.proof-', dir=str(base))
    try:
        with os.fdopen(fd, 'wb') as out, src.open('rb') as inp:
            for block in iter(lambda: inp.read(1024 * 1024), b''):
                out.write(block)
                digest.update(block)
            out.flush()
            os.fsync(out.fileno())
        sha = digest.hexdigest()
        suffix = src.suffix.lower()
        if suffix not in ('.png', '.jpg', '.jpeg', '.pdf', '.webp', '.heic', '.txt'):
            suffix = '.bin'
        dest = base / (sha + suffix)
        os.replace(tmp, dest)
        ref = str(dest.relative_to(Path(K.SCRIPT_DIR).parent))
        c.execute('INSERT OR IGNORE INTO contract_proof(contract_id,digest,ref,label) VALUES(?,?,?,?)',
                  (identifier, sha, ref, label))
        c.commit()
        return sha
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def resolve_proofs(K, c, owner, identifier):
    get(c, owner, identifier)
    out = []
    base = proof_root(K, owner).resolve()
    for r in rows(c, 'SELECT * FROM contract_proof WHERE contract_id=? ORDER BY id', (identifier,)):
        p = (Path(K.SCRIPT_DIR).parent / r['ref']).resolve()
        if p.parent != base or not p.is_file():
            raise ValueError('saved proof is missing or outside owner store')
        if hashlib.sha256(p.read_bytes()).hexdigest() != r['digest']:
            raise ValueError('saved proof failed integrity check')
        out.append({'path': str(p), 'label': r['label'], 'digest': r['digest']})
    return out


def owner_tz():
    import agent_conf
    return ZoneInfo(os.environ.get('AGENT_TZ') or agent_conf._raw('AGENT_TZ') or 'Asia/Seoul')


def plan(r, now, tz):
    f = r['facts']
    if f.get('status', 'active') != 'active' or f.get('reminder_enabled') is False:
        return None
    event, date = next(((k, f[k]) for k in ('cancel_by', 'renews_on', 'ends_on') if f.get(k)), (None, None))
    if not date:
        return None
    days = f.get('reminder_days')
    if days is None:
        days = 7 if event == 'cancel_by' or (r.get('kind')=='subscription' and f.get('cycle')=='yearly') else 30
    at = f.get('reminder_at') or '09:00'
    due = dt.datetime.fromisoformat(date + 'T' + at).replace(tzinfo=tz) - dt.timedelta(days=days)
    # Late capture creates one current check, never a historical alert burst.
    if due <= now:
        due = now.replace(second=0, microsecond=0) + dt.timedelta(minutes=1)
    signature = json.dumps([event, date, days, at, str(tz)])
    return event, due.astimezone(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), signature


def task_note(K, r, event):
    if r['kind']=='subscription' and r['record_key'].startswith('ledger:yearly:') and event=='renews_on':
        date=dt.date.fromisoformat(r['facts'][event])
        return copy(K,'yearly_reminder',name=r['title'],amount=format(r['facts']['amount'],','),
                    date=copy(K,'yearly_date',month=date.month,day=date.day))
    return copy(K, 'task_note', title=r['title'], event=copy(K, event),
                date=r['facts'][event], terms=r['facts'].get('cancel_terms') or copy(K, 'unknown'))


def reconcile(K, c, owner, identifier, now=None):
    r = get(c, owner, identifier)
    import agent_conf
    tz = owner_tz()
    now = now or dt.datetime.now(tz)
    planned = plan(r, now, tz)
    if not K._manage_on(c, 'module_contracts') and r['facts'].get('reminder_enabled') is not True:
        planned = None
    rem = r['reminder']
    if rem and rem['block_id']:
        task = rows(c, 'SELECT * FROM timeblock WHERE id=?', (rem['block_id'],))[0]
        if task['settled_at']:
            return r  # completing/cancelling a check never cancels a contract
        if planned is None:
            K.cancel(c, task['id'], task['version'], owner)
        elif rem['signature'] != planned[2]:
            event, due, sig = planned
            result = K.event_move(c, task['id'], task['version'], due, K._iso_shift_min(due, 10))
            if result != K.MutationResult.APPLIED:
                raise ValueError('reminder changed; retry from current card')
            c.execute('UPDATE contract_reminder SET signature=?,event=?,due_at=? WHERE contract_id=?',
                      (sig, event, due, identifier))
            c.commit()
        if planned:
            live = rows(c, 'SELECT * FROM timeblock WHERE id=?', (task['id'],))[0]
            note = task_note(K, r, planned[0])
            if live['note'] != note:
                result = K.timeblock_set_meta(c, task['id'], live['version'], {'note': note})
                if result != K.MutationResult.APPLIED:
                    raise ValueError('reminder note changed; retry')
        return get(c, owner, identifier)
    if rem or not planned:
        return r  # a deleted check stays deleted; never silently resurrect it
    event, due, sig = planned
    title = r['title'] + (' \ubcf4\uc99d \uc885\ub8cc \uc804 \ud655\uc778' if r['kind'] == 'warranty' else ' \uacc4\uc57d \ud655\uc778')
    def linked(conn, eid):
        current = get(conn, owner, identifier)
        if current['revision'] != r['revision']:
            raise ValueError('contract changed during reminder creation; retry')
        conn.execute('INSERT INTO contract_reminder VALUES(?,?,?,?,?)', (identifier, eid, sig, event, due))
    try:
        K.timeblock_add(c, 'task', title, 'timed', start_at=due, end_at=K._iso_shift_min(due, 10),
                        owning_agent=owner, created_by=owner, display_tz=str(tz), notify_policy='start_only',
                        exclusive_override=0, note=task_note(K, r, event),
                        post_insert=linked)
    except sqlite3.IntegrityError:
        # Concurrent capture may have won the one reminder link in post_insert.
        if not get(c, owner, identifier)['reminder']:
            raise
    return get(c, owner, identifier)


def copy(K, key, **kw):
    data = read_json(Path(__file__).with_name('contracts_copy.json'))
    import agent_conf
    lang = agent_conf.conf_str('AGENT_LANG', 'ko').split('-')[0].split('_')[0]
    fallback = data.get(lang, data['en']).get(key, data['en'].get(key, key))
    return K._i18n('contract.' + key, fallback).format(**kw)


def esc(s):
    return html.escape(str(s), quote=False)


def value(K, r, key, date=False, money=False, tag=True):
    v = r['facts'].get(key) if key not in ('title', 'provider') else r.get(key)
    if v is None:
        return copy(K, 'unknown')
    if money:
        v = copy(K, 'won', amount=format(v, ','))
    elif date:
        v = str(v).replace('-', '.' if r['kind'] != 'warranty' else '/')
    elif type(v) is bool:
        v = copy(K, 'yes' if v else 'no')
    origin = r['sources'].get(key)
    if origin == 'estimated':
        origin = 'calculated'  # Reuse the existing calculation label.
    return esc(v) + (' ' + copy(K, 'source_' + origin) if origin and tag else '')


def button(K, r, key, action):
    token = 'opt:contract %s %d %d' % (action, r['id'], r['revision'])
    if len(token.encode()) > 64:
        raise ValueError('callback exceeds Telegram limit')
    return [{'text': copy(K, key), 'callback_data': token}]


def reminder_text(K, r):
    rem = r['reminder']
    if rem:
        if rem['settled_outcome']:
            return copy(K, 'reminder_done' if rem['settled_outcome'] == 'done' else 'reminder_cancelled')
        if rem['block_id'] is None:
            return copy(K, 'reminder_cancelled')
        # Read the live task time, so snooze is reflected without changing terms.
        import agent_conf
        tz = owner_tz()
        stamp = dt.datetime.fromisoformat(rem['start_at'].replace('Z', '+00:00')).astimezone(tz)
        return stamp.strftime('%Y.%m.%d %H:%M') + ' ' + copy(K, 'source_calculated')
    if r['facts'].get('reminder_enabled') is False:
        return copy(K, 'reminder_off')
    return copy(K, 'reminder_unarmed' if any(r['facts'].get(k) for k in ('cancel_by', 'renews_on', 'ends_on')) else 'need_date')


def render(K, r, view='overview'):
    v = lambda k, **kw: value(K, r, k, **kw)
    if view == 'cost':
        f = r['facts']
        known = (all(f.get(k) is not None for k in ('effective_on', 'quote_on', 'fee', 'refund', 'avoidable', 'unavoidable'))
                 and f['quote_on'][:7] == dt.datetime.now(owner_tz()).strftime('%Y-%m')
                 and f['effective_on'][:7] <= f['quote_on'][:7])
        effect = copy(K, 'won', amount=format(f['avoidable'] + f['refund'] - f['fee'] - f['unavoidable'], ',')) if known else copy(K, 'unknown')
        if known:
            effect += ' ' + copy(K, 'source_calculated')
        amount = v('amount', money=True) if f.get('cycle') == 'monthly' else copy(K, 'unknown')
        text = copy(K, 'cost', title=esc(r['title']), amount=amount,
                    effective=v('effective_on', date=True), fee=v('fee', money=True), refund=v('refund', money=True),
                    effect=effect, terms=v('cancel_terms'))
        buttons = [button(K, r, 'edit', 'edit'), button(K, r, 'back', 'get')]
    elif r['kind'] == 'warranty':
        f = r['facts']
        end = v('ends_on', date=True, tag=False)
        if not f.get('ends_on'):
            summary = copy(K, 'warranty_unknown')
        else:
            today = dt.datetime.now(owner_tz()).date().isoformat()
            summary = copy(K, 'warranty_expired' if f['ends_on'] < today else 'warranty_active', end=end)
            if r['sources'].get('ends_on'):
                summary += ' ' + copy(K, 'source_' + r['sources']['ends_on'])
        term = copy(K, 'term_months', n=f['term_months']) if f.get('term_months') else copy(K, 'unknown')
        if f.get('term_months') and f['term_months'] % 12 == 0:
            term = copy(K, 'term_years', n=f['term_months'] // 12)
        if f.get('term_months'):
            term += ' ' + copy(K, 'source_' + r['sources'].get('term_months', 'owner'))
        proof = ' / '.join(esc(p['label']) for p in r['proofs']) or copy(K, 'unknown')
        text = copy(K, 'warranty', title=esc(r['title']), summary=summary, term=term,
                    bought=v('bought_on', date=True), proof=proof, reminder=reminder_text(K, r),
                    proof_note=copy(K, 'proof_pending' if r['proofs'] else 'proof_unknown'))
        buttons = [button(K, r, 'proof_button', 'proof'), button(K, r, 'warranty_reminder', 'reminder'), button(K, r, 'warranty_edit', 'edit')]
    else:
        amount = v('amount', money=True)
        if r['facts'].get('cycle') != 'monthly':
            amount = copy(K, 'unknown') if not r['facts'].get('cycle') else copy(K, 'cycle_amount', amount=amount, cycle=copy(K, r['facts']['cycle']))
        linkage = copy(K, 'linked' if r['fixed_id'] else 'unlinked')
        renewal = copy(K, 'unknown')
        if r['facts'].get('auto_renew') is True:
            renewal = v('renews_on', date=True)
        elif r['facts'].get('auto_renew') is False:
            renewal = copy(K, 'no_renew') + ' ' + copy(K, 'source_' + r['sources'].get('auto_renew', 'owner'))
        cutoff = copy(K, 'cancel_date', date=v('cancel_by', date=True, tag=False)) if r['facts'].get('cancel_by') else copy(K, 'unknown')
        if r['facts'].get('cancel_by') and r['sources'].get('cancel_by'):
            cutoff += ' ' + copy(K, 'source_' + r['sources']['cancel_by'])
        heading = (esc(r['provider']) + ' / ' if r['provider'] else '') + v('title')
        text = copy(K, 'overview', title=heading, provider=v('provider'), amount=amount, linkage=linkage,
                    cost_label=copy(K, 'monthly_label' if r['facts'].get('cycle') == 'monthly' else 'cost_label'),
                    start=v('starts_on', date=True), end=v('ends_on', date=True), renewal=renewal,
                    cutoff=cutoff, reminder=reminder_text(K, r), fee=v('fee', money=True))
        buttons = [button(K, r, 'cost_button', 'cost'), button(K, r, 'reminder_button', 'reminder'), button(K, r, 'edit', 'edit')]
    status = r['facts'].get('status', 'active')
    if status != 'active':
        text += '\n\n' + copy(K, 'status_line', status=copy(K, 'status_' + status))
    return {'text': text, 'buttons': buttons}


def execute(K, c, action, d, owner=None, now=None):
    owner = owner or K._facade_agent()
    if action == 'dispatch':
        m = re.fullmatch(r'(?:opt:)?contract (get|cost|edit|reminder|proof|rearm) (\d+) (\d+)', d.get('token', ''))
        if not m:
            raise ValueError('invalid contract callback')
        action, identifier, rev = m.groups()
        d = {'id': int(identifier), 'revision': int(rev)}
    if action == 'list':
        # Ledger yearly terms are staged before their copy is approved. They
        # must not surface through the contract list while that UI gate is off.
        rs = [get(c, owner, r['id']) for r in rows(c, "SELECT id FROM contract_record WHERE owner=? "
                                                "AND kind NOT IN ('ledger_yearly_pending','ledger_obligation') ORDER BY id", (owner,))]
        if d.get('query'):
            rs = [r for r in rs if d['query'].casefold() in (r['title'] + ' ' + (r['provider'] or '')).casefold()]
        return {'records': rs, 'cards': [render(K, r) for r in rs]}
    if action in ('capture', 'update'):
        if action == 'update' and ('id' not in d or 'revision' not in d):
            raise ValueError('update needs current id and revision')
        r = capture(K, c, owner, d)
        errors = []
        if d.get('proof_path'):
            try:
                keep_proof(K, c, owner, r['id'], d['proof_path'], d.get('proof_label') or copy(K, 'proof_generic'))
            except (OSError, ValueError, sqlite3.Error) as e:
                c.rollback()
                errors.append('proof: ' + str(e))
        try:
            r = reconcile(K, c, owner, r['id'], now)
        except (ValueError, OverflowError, sqlite3.Error) as e:
            c.rollback()
            errors.append('reminder: ' + str(e))
        r = get(c, owner, r['id'])
        return {'record': r, 'card': render(K, r), 'enrichment_errors': errors}
    r = get(c, owner, d['id'])
    if d.get('revision') is not None and d['revision'] != r['revision']:
        return {'record': r, 'card': render(K, r), 'stale': True}
    if action == 'rearm':
        rem = r['reminder']
        if rem and rem['block_id'] and rem['settled_outcome'] is None:
            return {'record': r, 'card': render(K, r)}
        r = capture(K, c, owner, {'id': r['id'], 'revision': r['revision'], 'facts': {'reminder_enabled': True}})
        c.execute('DELETE FROM contract_reminder WHERE contract_id=?', (r['id'],))
        # Advance even if the preference was already true: repeated rearm taps
        # on the old card cannot make a second current task.
        c.execute('UPDATE contract_record SET revision=revision+1 WHERE id=?', (r['id'],))
        c.commit()
        r = reconcile(K, c, owner, r['id'], now)
    elif action == 'reconcile':
        r = reconcile(K, c, owner, r['id'], now)
    elif action == 'resolve-proof':
        # Machine-only resolver. A future framework attachment sender must consume
        # this result, not relay the local paths as chat URLs.
        return {'attachments': resolve_proofs(K, c, owner, r['id'])}
    elif action in ('edit', 'reminder'):
        text = copy(K, 'edit_prompt' if action == 'edit' else 'reminder_prompt')
        buttons = [button(K, r, 'back', 'get')]
        if action == 'reminder' and r['reminder'] and (r['reminder']['settled_outcome'] or not r['reminder']['block_id']):
            buttons.insert(0, button(K, r, 'rearm_button', 'rearm'))
        return {'record': r, 'card': {'text': text, 'buttons': buttons},
                'reply': {'action': 'update', 'data': {'id': r['id'], 'revision': r['revision']}}}
    elif action == 'proof':
        try:
            files = resolve_proofs(K, c, owner, r['id'])
            text = copy(K, 'proof_pending' if files else 'proof_unknown')
        except ValueError:
            text = copy(K, 'proof_missing')
        return {'record': r, 'card': {'text': text, 'buttons': [button(K, r, 'back', 'get')]}}
    elif action not in ('get', 'cost'):
        raise ValueError('unknown contract action')
    return {'record': r, 'card': render(K, r, 'cost' if action == 'cost' else 'overview')}


def sync_owned(K, c):
    if not c.execute("SELECT 1 FROM sqlite_master WHERE name='contract_record'").fetchone():
        return
    for row in rows(c, 'SELECT r.id,r.owner,o.bought_on,o.status FROM contract_record r '
                       "LEFT JOIN owned_item o ON o.id=r.owned_id WHERE r.kind='warranty'"):
        r = get(c, row['owner'], row['id'])
        facts = {}
        if row['bought_on'] and r['facts'].get('bought_on') != row['bought_on']:
            facts['bought_on'] = row['bought_on']
        if row['status'] in ('returned', 'gone'):
            facts['status'] = 'returned' if row['status'] == 'returned' else 'disposed'
        if facts:
            capture(K, c, row['owner'], {'id': r['id'], 'facts': facts})
        reconcile(K, c, row['owner'], row['id'])


def cli(K, argv):
    p = argparse.ArgumentParser(prog='contract')
    p.add_argument('action')
    p.add_argument('--data', default='{}')
    a = p.parse_args(argv)
    c = None
    try:
        c = K.event_conn()
        d = json.loads(a.data)
        if not isinstance(d, dict):
            raise ValueError('data must be an object')
        out = execute(K, c, a.action, d)
        print(json.dumps(out, ensure_ascii=False))
        return 0
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error, K.MigrationRequired) as e:
        if c:
            c.rollback()
        print(json.dumps({'error': str(e)}, ensure_ascii=False))
        return 2
    finally:
        if c:
            c.close()
