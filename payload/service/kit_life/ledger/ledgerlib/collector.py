"""Card SMS from the Dogany app -> kit intake. The automated path, optional
per card.

The owner's iPhone hands each card payment SMS to the Dogany app (Shortcut
message automation, triggered by message CONTENT only), which sends it to the
host's transporter receiver (receiver.py). Per poll:

  require active shortcut/card_sms consent; no receipt or event before approval
  read rows after the persisted cursor (received_at, id), starting at approval
  per eligible row, the sender gate:
    app below 0.4                  -> held (unusable: app_old), no sender
    no / unusable payload.sender   -> held (unusable: no_sender)
    sender key refused by owner    -> dropped (count only)
    sender unknown, no amount      -> ignored (nothing to ask about)
    sender key not yet registered  -> held by event id, asked once per key
    registered                     -> ingest
  ingest:
    the same text already ingested (automation fired twice) -> duplicate
    issuer = the one issuer whose marker is in the text
    issuer's sms source not enabled -> skipped
    no "<amount>won" in the text    -> ignored (not a payment SMS)
    a deterministic template fits  -> intake-add (rule lane)
    otherwise                      -> unread queue (model lane, sms-stage;
                                      always pending)
  successful intake -> shared ingest_ref(source, kind, id), before cursor save
  source_ref = 'rcv:' + the receiver event id. The kit's intake is
  idempotent on (source, source_ref), so re-reading an event (cursor
  overlap, a replay after register, a resend the receiver upserted) is a kit
  `duplicate`, never a second row.

The SMS text is never stored by this module: the kit raw payload is the
extracted fields plus the receiver event id; the unread queue and the held
list hold event ids; the poll log holds counts.
"""
import datetime
import json
import sys

from . import automation, issuers, kit, paths, receiver, seed
from .smsparse import (apply_templates, has_amount, mask, normalize_text, sender_key, sms_ref,
                       verify_model_reading)

QUEUE = 'sms-unread.jsonl'
CURSOR = 'sms-cursor.json'
LOG = 'sms-poll.log'
TEXT_REFS_KEEP = 500


def _today():
    return datetime.date.today()


def business_hints(conf):
    return [h.strip() for h in conf.get('LEDGER_BUSINESS_HINTS', '').split(',') if h.strip()]


def source_ref(row):
    return 'rcv:' + row['id']


def raw_payload(row, data):
    """What the kit keeps as evidence: the extraction, not the SMS."""
    return json.dumps({'receiver_event': row['id'], 'received_at': row['received_at'], 'data': data},
                      ensure_ascii=False, sort_keys=True)


# ---- module-owned instance state (no SMS text) ------------------------------

def queue_load():
    path = paths.state_dir() / QUEUE
    out = {}
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            if line.strip():
                r = json.loads(line)
                out[r['ref']] = r
    return out


def queue_save(entries):
    path = paths.state_dir() / QUEUE
    tmp = path.with_suffix('.tmp')
    tmp.write_text(''.join(json.dumps(r, ensure_ascii=False, sort_keys=True) + '\n'
                           for r in sorted(entries.values(), key=lambda r: r['ref'])),
                   encoding='utf-8')
    tmp.replace(path)


def cursor_load():
    try:
        d = json.loads((paths.state_dir() / CURSOR).read_text(encoding='utf-8'))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def cursor_save(d):
    path = paths.state_dir() / CURSOR
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    tmp.replace(path)


def timestamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def log_line(summary, warning=False):
    # Counts/reason only: no sender, SMS text, or amount.
    line = timestamp() + ' ' + json.dumps(
        {k: v for k, v in summary.items() if k != 'items'},
        ensure_ascii=False, sort_keys=True)
    with open(paths.state_dir() / LOG, 'a', encoding='utf-8') as f:
        f.write(line + '\n')
    if warning:
        print(line, file=sys.stderr)


# ---- poll -------------------------------------------------------------------

def _summary():
    return {'status': 'ok', 'read': 0, 'staged': 0, 'duplicate': 0, 'attached': 0, 'excluded': 0,
            'confirmed': 0, 'unread': 0, 'ignored': 0, 'not_enabled': 0, 'refused': 0,
            'before_consent': 0, 'sender_unconfirmed': 0, 'app_old': 0, 'no_sender': 0, 'items': []}


def poll(today=None, dry_run=False, docs=None, event_ids=None, db=None):
    """event_ids: replay exactly these receiver events (after the owner's
    register), ignoring and not moving the cursor."""
    today = today or _today()
    conf = paths.conf()
    docs = docs or issuers.load_all()
    summary = _summary()
    sources = seed.enabled_sms_sources()
    if not sources:
        summary['status'] = 'off'
        return summary
    consent = kit.sms_cursor()
    if consent['consent'] != 'on':
        summary['status'] = 'consent_off'
        return summary
    floor = consent['floor']
    enabled = {seed.issuer_of(s['key']) for s in sources}
    cur = cursor_load()
    if event_ids is not None:
        rows = receiver.rows_by_id(event_ids, path=db)
    else:
        rows = receiver.rows_after(cur.get('last'), since=floor, path=db)
    hints = business_hints(conf)
    queue = queue_load()
    state = automation.load()
    text_refs = list(cur.get('text_refs', []))
    for row in rows:
        summary['read'] += 1
        # Saved only after every row and ingest-ref write succeeds.
        if event_ids is None:
            cur['last'] = {'received_at': row['received_at'], 'id': row['id']}
        if not receiver.eligible(row, floor):
            summary['before_consent'] += 1
            continue
        at = receiver.occurred_kst(row)
        key = sender_key(row['sender'])
        if key is None and not dry_run:
            log_line({'level': 'WARNING', 'reason': 'missing_or_invalid_sender',
                      'action': 'quarantine', 'count': 1}, warning=True)
        if not receiver.app_ok(row['app_version']):
            summary['app_old'] += 1
            automation.hold_unusable(state, 'app_old', row['id'], at)
        elif key is None:
            summary['no_sender'] += 1
            automation.hold_unusable(state, 'no_sender', row['id'], at)
        else:
            status = automation.sender_status(state, key)
            text = normalize_text(row['text'])
            if status == 'refused':
                summary['refused'] += 1
            elif status == 'unknown' and not has_amount(text):
                summary['ignored'] += 1     # not a payment SMS: nothing to ask about
            elif status == 'unknown':
                summary['sender_unconfirmed'] += 1
                automation.hold_sender(state, key, row['sender'], issuers.identify(text, docs), row['id'], at)
            else:
                _ingest(row, text, at, docs, enabled, hints, queue, state, text_refs, summary, dry_run)
    if not dry_run:
        cur['text_refs'] = text_refs[-TEXT_REFS_KEEP:]
        queue_save(queue)
        automation.save(state)
        cursor_save(cur)
        log_line(summary)
    return summary


def _ingest(row, text, at, docs, enabled, hints, queue, state, text_refs, summary, dry_run):
    ref = source_ref(row)
    if kit.sms_ref_get(row) is not None:
        summary['duplicate'] += 1
        return
    tref = sms_ref(text)
    if tref in text_refs and ref not in queue:
        summary['duplicate'] += 1           # same SMS handed over twice: another event id, one text
        return
    issuer = issuers.identify(text, docs)
    automation.seen(state, issuer, at)
    if issuer is not None and issuer not in enabled:
        summary['not_enabled'] += 1
        return
    if not has_amount(text):
        summary['ignored'] += 1
        return
    data = apply_templates(docs[issuer], text, at) if issuer else None
    if data is None:
        if ref not in queue:
            queue[ref] = {'ref': ref, 'event': row['id'], 'issuer': issuer,
                          'date': at.date().isoformat() if at else None,
                          'problem': 'no template' if issuer else 'issuer not recognised'}
            text_refs.append(tref)
        summary['unread'] += 1
        return
    if data['operation'] == 'payment' and any(h.casefold() in data['merchant'].casefold() for h in hints):
        data['scope'] = 'unknown'
    if dry_run:
        summary['items'].append({'data': data})
        return
    kit.sms_check(row)
    from . import opaque
    add = kit.intake_add_held if opaque.match(data['merchant'],data.get('merchant_truncated')) else kit.intake_add
    res = add(seed.sms_source(issuer), ref, data, raw=raw_payload(row, data))
    kit.sms_ref_put(row, res['item']['id'])
    status = res['status']
    summary['staged' if status == 'pending' else status] += 1
    item_out = {'status': status, 'item_id': res['item']['id']}
    if status == 'confirmed' and res['item'].get('block_id'):
        from . import settlement
        sid = kit.spend_for_block(res['item']['block_id'])
        item_out['notice'] = settlement.captured(sid, data['merchant'])
        item_out['settlement'] = settlement.sweep(data['date'],data['date'])
    summary['items'].append(item_out)
    if tref not in text_refs:
        text_refs.append(tref)


def decide_sender(raw, approved, today=None, docs=None, db=None):
    """Apply the owner's one answer. register replays only that sender's held
    events; do-not-register drops them (the count is logged, nothing else)."""
    docs = docs or issuers.load_all()
    key, issuer, held = automation.decide_sender(raw, approved,
                                                 issuer=automation.known_sender_issuer(sender_key(raw), docs))
    base = {'sender': key, 'issuer': issuer, 'held': len(held)}
    if not approved:
        log_line({'status': 'sender_refused', 'dropped': len(held)})
        return dict(base, status='refused')
    out = poll(today=today, docs=docs, event_ids=set(held), db=db) if held else _summary()
    out.update(base, registered=True)
    return out


# ---- model lane -------------------------------------------------------------

def find_text(entry, db=None):
    rows = receiver.rows_by_id([entry['event']], path=db)
    if not rows:
        return None, None
    return normalize_text(rows[0]['text']), receiver.occurred_kst(rows[0])


def show(ref, db=None):
    """SMS text for the model to read, printed to the session only."""
    entry = queue_load().get(ref)
    if entry is None:
        raise ValueError('not in the unread queue')
    text, _ = find_text(entry, db)
    if text is None:
        raise ValueError('SMS not found in the receiver')
    return {'ref': ref, 'issuer': entry['issuer'], 'date': entry.get('date'), 'text': mask(text)[:2000]}


def stage_model(ref, data, docs=None, db=None):
    """Stage the model's reading of one unread SMS. Re-verified against the
    same text, then intake-add through the force-pending adapter."""
    docs = docs or issuers.load_all()
    queue = queue_load()
    entry = queue.get(ref)
    if entry is None:
        raise ValueError('not in the unread queue')
    rows = receiver.rows_by_id([entry['event']], path=db)
    consent = kit.sms_cursor()
    if (consent['consent'] != 'on' or not rows
            or not receiver.eligible(rows[0], consent['floor'])):
        return {'status': 'rejected', 'problems': ['no active consent or before approval']}
    row = rows[0]
    if (not receiver.app_ok(row['app_version']) or sender_key(row['sender']) is None
            or automation.sender_status(automation.load(), sender_key(row['sender'])) != 'registered'):
        return {'status': 'rejected', 'problems': ['sender not registered or unusable']}
    text, at = normalize_text(row['text']), receiver.occurred_kst(row)
    if text is None:
        raise ValueError('SMS not found in the receiver')
    data = dict(data)
    issuer = entry['issuer'] or data.get('issuer')
    problems = verify_model_reading(text, data, at)
    enabled = {seed.issuer_of(s['key']) for s in seed.enabled_sms_sources()}
    if issuer not in docs:
        problems.append('issuer unknown: name one of the automated cards')
    elif entry['issuer'] and data.get('issuer') not in (None, entry['issuer']):
        problems.append('issuer differs from the SMS text')
    elif issuer not in enabled:
        problems.append('issuer has no enabled SMS source')
    if problems:
        return {'status': 'rejected', 'problems': problems}
    data.update({'issuer': issuer, 'channel': 'sms', 'extraction': 'model', 'unit': 'krw'})
    data.setdefault('method', docs[issuer]['display_ko'])
    data.setdefault('kind', 'expense')
    data.setdefault('operation', 'payment')
    data.setdefault('scope', 'personal')
    kit.sms_check(row)
    res = kit.intake_add_held(seed.sms_source(issuer), ref, data, raw=raw_payload(row, data))
    kit.sms_ref_put(row, res['item']['id'])
    if res['status'] != 'held':
        del queue[ref]
        queue_save(queue)
    return res
