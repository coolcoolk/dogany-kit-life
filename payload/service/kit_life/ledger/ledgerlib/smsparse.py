"""Card SMS text and sender, as the Dogany app hands them over (receiver.py).
No model, no network.

Rule lane: `apply_templates()` tries the issuer's deterministic templates
(issuers/<issuer>.json sms.templates). A template is added only from a real
sample (tools/sms-template.py; first one: Hyundai approval, 2026-09-24), and
a template that stops matching degrades to the model lane, never to a wrong
number. Issuers without a template still go to the model lane and stay
pending.

Template groups: amount, merchant, date (required); time (HH:MM), product
(card product token, fills `{product}` in the template's `method`) and
installment (lump sum -> 0, N months -> N) are optional. A template marked
`merchant_truncated` (the issuer cuts the store name to a few characters)
flags every reading, so review matches it by prefix, not by equality.

Model lane check: `verify_model_reading()` re-reads the same text and refuses
a reading whose amount / merchant / date is not actually in it.

Sender: `sender_key()` is the one canonical key per SMS sender value. The
same issuer arrives as '+82 1577-6200', '1577-6200', '15776200' (one key:
'15776200') or as an RCS brand name such as 'Hyundai Card' (in Hangul) (trimmed text).
"""
import datetime
import hashlib
import re
import unicodedata

KST = datetime.timezone(datetime.timedelta(hours=9))
AMOUNT_RE = re.compile(r'[0-9][0-9,]*\s*\uc6d0')
MODEL_DATE_WINDOW_DAYS = 7
PHONE_CHARS_RE = re.compile(r'^\+?[0-9 ()\-.]+$')
SENDER_MAX = 40


def norm(s):
    return ''.join(ch for ch in unicodedata.normalize('NFKC', s or '').casefold() if ch.isalnum())


def normalize_text(text):
    """One stable shape: per-line whitespace collapsed, CRLF to LF, blank
    lines dropped (the app hands the text over byte for byte)."""
    lines = [re.sub(r'[ \t\u00a0]+', ' ', ln).strip() for ln in (text or '').replace('\r\n', '\n').replace('\r', '\n').split('\n')]
    return '\n'.join(ln for ln in lines if ln)


def sender_key(raw):
    """Canonical sender key, or None for a value that cannot be a sender:
    empty, multi-line, longer than a sender name, a number of impossible
    length, or a number glued to other text (live 2026-09-28: the Shortcut's
    Sender field held the message body, body + number, or the number twice).

    Phone numbers: spaces, dashes, dots and parentheses dropped, +82 becomes
    the domestic leading 0 ('+82 10-1234-5678' -> '01012345678'); a service
    number keeps no leading 0 ('+82 1577-6200' -> '15776200'). Anything else
    (an RCS brand sender such as 'Hyundai Card' (in Hangul)) compares as NFKC-trimmed text with
    inner whitespace collapsed."""
    s = unicodedata.normalize('NFKC', raw or '').strip()
    if not s or '\n' in s or '\r' in s or len(s) > SENDER_MAX:
        return None
    if PHONE_CHARS_RE.match(s) and re.search(r'\d', s):
        digits = re.sub(r'\D', '', s)
        if s.startswith('+'):
            if not digits.startswith('82'):
                return '+' + digits if 7 <= len(digits) <= 15 else None
            digits = digits[2:]
            # +82 drops the domestic trunk 0 of area and mobile numbers;
            # 15xx/16xx/18xx service numbers never had one.
            if not re.match(r'^1[5-8]\d{2}', digits) or len(digits) > 8:
                digits = '0' + digits.lstrip('0')
        return digits if 3 <= len(digits) <= 11 else None
    # The app also reports RCS service addresses (e.g. a numeric mailbox
    # at botplatform.maapservice.com). This is one sender, not a number
    # glued to SMS text; it still requires the owner's explicit approval.
    if re.fullmatch(r'[A-Za-z0-9._+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', s):
        return s.casefold()
    if re.search(r'\d[\d\s\-]{5,}\d', s):
        return None                     # a phone-like run inside text: not one sender
    return re.sub(r'\s+', ' ', s)


def sms_ref(text):
    """Text reference = the SMS text itself (hashed). The same SMS handed over
    twice (the automation fired twice: two receiver events, one text) is one
    item. Card SMS carry the minute, so two real payments differ in text."""
    return 'sms:' + hashlib.sha256(normalize_text(text).encode('utf-8')).hexdigest()[:32]


def has_amount(text):
    return bool(AMOUNT_RE.search(text))


def mask(text):
    """Before any model or fixture sees it: card/account digit runs."""
    return re.sub(r'\d{4}([- ]?)[\d*]{2,4}\1[\d*]{2,4}\1\d{2,4}', '[\ubc88\ud638 \uac00\ub9bc]', text)


def _amount(s):
    return int(re.sub(r'[^0-9]', '', s))


def parse_date(s, ref_dt):
    """2026/09/24, 2026.09.24, 09/24 or 09 wol 24 il. A year-less date takes the
    SMS's year (when the phone got it), stepping back one year when that would
    land after it."""
    m = re.search(r'(\d{4})\s*[./\-\ub144]\s*(\d{1,2})\s*[./\-\uc6d4]\s*(\d{1,2})', s)
    if m:
        return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r'(\d{1,2})\s*[./\-\uc6d4]\s*(\d{1,2})', s)
    if m and ref_dt:
        d = datetime.date(ref_dt.year, int(m.group(1)), int(m.group(2)))
        if d > ref_dt.date():
            d = d.replace(year=d.year - 1)
        return d
    raise ValueError('no date in %r' % s)


def apply_templates(doc, text, sms_dt):
    """Deterministic lane. Returns the kit money --data or None."""
    for t in doc['sms']['templates']:
        m = re.fullmatch(t['pattern'], text, re.S)
        if not m:
            continue
        g = m.groupdict()
        try:
            data = {'date': parse_date(g['date'], sms_dt).isoformat(), 'amount': _amount(g['amount']),
                    'merchant': g['merchant'].strip(),
                    'method': (t.get('method') or doc['display_ko']).format_map(g),
                    'kind': 'expense', 'unit': 'krw',
                    'operation': 'reversal' if t['kind'] == 'cancel' else 'payment',
                    'scope': 'personal', 'issuer': doc['issuer'], 'channel': 'sms',
                    'extraction': 'template', 'template': t['fixture']}
            if g.get('time'):
                hh, mm = (int(x) for x in g['time'].split(':'))
                data['time'] = datetime.time(hh, mm).strftime('%H:%M')
            if g.get('installment'):
                data['installment_months'] = 0 if g['installment'] == '\uc77c\uc2dc\ubd88' else _amount(g['installment'])
            if t.get('merchant_truncated'):
                data['merchant_truncated'] = True
        except (ValueError, KeyError):
            continue
        if data['amount'] > 0 and data['merchant']:
            return data
    return None


def verify_model_reading(text, data, sms_dt):
    """The session model read an SMS no template knows. Check it against the
    SAME text: amount literally present, merchant literally present (after
    normalisation), date within a week before the SMS arrived. Problems, or []."""
    problems = []
    amt = data.get('amount')
    if type(amt) is not int or amt <= 0:
        problems.append('amount must be a positive integer')
    elif str(amt) not in re.sub(r'[^0-9]', ' ', text.replace(',', '')).split():
        problems.append('amount not found in the SMS')
    merchant = str(data.get('merchant', '')).strip()
    if not norm(merchant):
        problems.append('merchant missing')
    elif norm(merchant) not in norm(text):
        problems.append('merchant not found in the SMS')
    try:
        d = datetime.date.fromisoformat(data.get('date', ''))
        if sms_dt and not (0 <= (sms_dt.date() - d).days <= MODEL_DATE_WINDOW_DAYS):
            problems.append('date outside the SMS window')
    except (TypeError, ValueError):
        problems.append('date is not ISO')
    if data.get('operation', 'payment') not in ('payment', 'reversal'):
        problems.append('operation must be payment or reversal')
    return problems
