"""Weekly-sweep screenshot intake: rows the session model read off a
spend-list screenshot (an app named in sources.json, or any other app),
staged for review.

The model reads the image; this code never sees it. What this code does is
refuse readings that cannot be right and stage the rest ALWAYS pending
(kit.intake_add_held -- the G1 adapter). Nothing here writes a spend.

Row: {"date": ISO, "amount": int > 0, "merchant": str, "card": str?,
      "type": "payment" | "cancel" | "transfer" | "income"}   (type default payment)

Re-validation, per row:
  amount    a positive int (not a string, not a bool); a cancel is type=cancel
            with the POSITIVE original amount, never a negative number
  date      an ISO date inside the sweep window [since, until] and not after today
  merchant  1..60 characters after trimming, at least one letter
  card      the row's card or the screenshot-wide --card; required. An
            unregistered card still stages -- the review asks which card
  identity  shot:<sha256(date|amount|merchant|card|type)>:<occurrence>, so the
            same screenshot sent twice is `duplicate`, while two identical
            coffees in one screenshot stay two
"""
import collections
import datetime
import hashlib
import json

from . import kit, seed, sources
from .smsparse import norm

TYPES = {'payment': ('expense', 'payment'), 'cancel': ('expense', 'reversal'),
         'transfer': ('expense', 'transfer'), 'income': ('income', 'payment')}
APPS = sources.all_apps()        # every region's screenshot apps + 'other'
MERCHANT_MAX = 60


def validate_row(row, since, until, today, default_card=None):
    problems = []
    if not isinstance(row, dict):
        return ['row must be an object'], None
    amt = row.get('amount')
    if type(amt) is not int or amt <= 0:
        problems.append('amount must be a positive integer (a cancel is type=cancel)')
    try:
        d = datetime.date.fromisoformat(str(row.get('date', '')))
        if not (since <= d <= until):
            problems.append('date outside the sweep window')
        if d > today:
            problems.append('date in the future')
    except ValueError:
        problems.append('date is not ISO')
    merchant = str(row.get('merchant') or '').strip()
    if not merchant or len(merchant) > MERCHANT_MAX or not any(ch.isalpha() for ch in merchant):
        problems.append('merchant must be 1..%d characters with a letter' % MERCHANT_MAX)
    card = str(row.get('card') or default_card or '').strip()
    if not card:
        problems.append('card missing (row or screenshot-wide)')
    typ = row.get('type', 'payment')
    if typ not in TYPES:
        problems.append('type must be one of %s' % sorted(TYPES))
    if problems:
        return problems, None
    kind, op = TYPES[typ]
    return [], {'date': d.isoformat(), 'amount': amt, 'merchant': merchant, 'method': card,
                'kind': kind, 'unit': 'krw', 'operation': op, 'scope': 'personal'}


def row_key(data):
    return '|'.join([data['date'], str(data['amount']), norm(data['merchant']), norm(data['method']),
                     data['kind'], data['operation']])


def stage(rows, since, until, app='other', card=None, today=None):
    today = today or datetime.date.today()
    if app not in APPS:
        raise ValueError('app must be one of %s' % (APPS,))
    if since > until:
        raise ValueError('since after until')
    seed.ensure()
    out = {'staged': 0, 'duplicate': 0, 'attached': 0, 'excluded': 0, 'held': 0, 'confirmed': 0,
           'rejected': [], 'items': []}
    occurrence = collections.Counter()
    for n, row in enumerate(rows, 1):
        problems, data = validate_row(row, since, until, today, card)
        if problems:
            out['rejected'].append({'row': n, 'problems': problems,
                                    'merchant': str((row or {}).get('merchant', ''))[:MERCHANT_MAX]
                                    if isinstance(row, dict) else ''})
            continue
        key = row_key(data)
        occurrence[key] += 1
        ref = 'shot:%s:%d' % (hashlib.sha256(key.encode('utf-8')).hexdigest()[:32], occurrence[key])
        data.update({'channel': 'screenshot', 'app': app, 'extraction': 'model'})
        raw = json.dumps({'app': app, 'row': n, 'data': data}, ensure_ascii=False, sort_keys=True)
        # A verified transfer is never a ledger spend. Do not hold it: the kit
        # deterministically excludes it. Model-read money rows stay held.
        res = (kit.intake_add(seed.SHOT_SOURCE, ref, data, raw=raw)
               if data['operation'] == 'transfer'
               else kit.intake_add_held(seed.SHOT_SOURCE, ref, data, raw=raw))
        status = res['status']
        out['staged' if status == 'pending' else status] += 1
        out['items'].append({'row': n, 'status': status, 'item_id': res['item']['id']})
    out['say'] = say(out)
    return out


def say(out):
    new = out['staged']
    parts = []
    if new:
        parts.append('\ucea1\ucc98\uc5d0\uc11c %d\uac74 \uc77d\uc5b4\ub480\uc5b4\uc694. \uc544\ub798 \ud45c\ub85c \ud55c \ubc88\uc5d0 \ud655\uc778\ud574\uc694' % new)
    if out['attached']:
        parts.append('%d\uac74\uc740 \uc774\ubbf8 \uc801\ud78c \uae30\ub85d\uacfc \uac19\uc544\uc11c \ud569\ucce4\uc5b4\uc694' % out['attached'])
    if out['duplicate']:
        parts.append('%d\uac74\uc740 \uc804\uc5d0 \ubcf4\ub0b4\uc8fc\uc2e0 \uac70\ub77c \uac74\ub108\ub6f0\uc5c8\uc5b4\uc694' % out['duplicate'])
    if out['excluded']:
        parts.append('\uc774\uccb4 %d\uac74\uc740 \uc9c0\ucd9c\uc774 \uc544\ub2c8\ub77c \ube90\uc5b4\uc694' % out['excluded'])
    if out['held']:
        parts.append('%d\uac74\uc740 "\ubc14\ub85c \uc801\uae30" \uc124\uc815\uc774 \ucf1c\uc838 \uc788\uc5b4\uc11c \ub123\uc9c0 \uc54a\uc558\uc5b4\uc694' % out['held'])
    if out['rejected']:
        names = ', '.join(r['merchant'] or '%d\ubc88\uc9f8 \uc904' % r['row'] for r in out['rejected'])
        parts.append('%d\uc904\uc740 \uc81c\uac00 \ud655\uc2e4\ud788 \ubabb \uc77d\uc5b4\uc11c \ube90\uc5b4\uc694 (%s). \ub9d0\uc500\uc73c\ub85c \uc54c\ub824\uc8fc\uc2dc\uba74 \uc801\uc744\uac8c\uc694' % (len(out['rejected']), names))
    return ', '.join(parts) + '.' if parts else '\ucea1\ucc98\uc5d0\uc11c \uc77d\uc744 \uacb0\uc81c\uac00 \uc5c6\uc5c8\uc5b4\uc694.'
