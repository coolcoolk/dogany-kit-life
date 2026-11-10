"""Card issuers as DATA: issuers/<issuer>.json (schema 1).

One file per issuer holds two things:
  guide  -- how the owner turns on that card's payment SMS: availability,
            app menu path, fee, alternative route, SMS sender numbers. Every
            field is a record {value, status, verified_at, source[], note?}.
            A value is never invented: a non-null value names at least one
            source, and `verified` additionally carries verified_at. A field
            nobody could check stays {value: null, status: unverified} and the
            guide text says "find it in the app" instead of making a path up.
  sms    -- how a card SMS is recognised (`markers`, regexes on the
            text) and parsed (`templates`, deterministic, only from a real
            sample -- tools/sms-template.py writes them). Optional
            records in the same {value, status, ...} shape:
              shortcut_keyword -- text that really appears in the SMS body,
                                  for the Shortcut "message contains" trigger
              cancel_template  -- what is known about the cancel SMS before
                                  a real sample exists

`validate()` is the schema; tests/test_issuers.py runs it over every shipped
file.
"""
import datetime
import json
import re
from pathlib import Path

from . import paths

SCHEMA = 1
GUIDE_FIELDS = ('sms_available', 'menu_path', 'fee', 'alt_route', 'sender_numbers')
STATUSES = ('verified', 'unverified')
SOURCE_RE = re.compile(r'^(https://\S+|owner:\S.*)$')
TEMPLATE_KINDS = ('approval', 'cancel')
TEMPLATE_GROUPS = {'amount', 'merchant', 'date'}
TEMPLATE_OPTIONAL_GROUPS = {'time', 'product', 'installment'}
SMS_RECORDS = ('shortcut_keyword', 'cancel_template')


def issuers_dir():
    return paths.SERVICE_DIR / 'issuers'


def load_all(d=None):
    d = Path(d or issuers_dir())
    out = {}
    for p in sorted(d.glob('*.json')):
        doc = json.loads(p.read_text(encoding='utf-8'))
        out[doc['issuer']] = doc
    return out


def _field_problems(name, rec):
    if not isinstance(rec, dict) or not {'value', 'status', 'verified_at', 'source'} <= set(rec):
        return ['%s: needs value/status/verified_at/source' % name]
    out = []
    if rec['status'] not in STATUSES:
        out.append('%s: status must be verified|unverified' % name)
    src = rec['source']
    if not isinstance(src, list) or any(not isinstance(s, str) or not SOURCE_RE.match(s) for s in src):
        out.append('%s: source must be a list of https URLs or owner: notes' % name)
        src = []
    if rec['value'] is not None and not src:
        out.append('%s: a value without a source is an invented value' % name)
    if rec['status'] == 'verified':
        if rec['value'] is None:
            out.append('%s: verified field has no value' % name)
        try:
            datetime.date.fromisoformat(rec['verified_at'] or '')
        except ValueError:
            out.append('%s: verified field needs verified_at (ISO date)' % name)
    elif rec['verified_at'] is not None:
        out.append('%s: unverified field carries verified_at' % name)
    if name == 'menu_path' and rec['value'] is not None and (
            not isinstance(rec['value'], list) or not all(isinstance(s, str) and s.strip() for s in rec['value'])):
        out.append('menu_path: value must be a list of menu labels')
    if name == 'sms_available' and rec['value'] not in (None, True, False):
        out.append('sms_available: value must be true|false|null')
    return out


def validate_template(t):
    out = []
    if t.get('kind') not in TEMPLATE_KINDS:
        out.append('template kind must be approval|cancel')
    try:
        rx = re.compile(t.get('pattern', ''))
        missing = TEMPLATE_GROUPS - set(rx.groupindex)
        if missing:
            out.append('template pattern lacks groups %s' % sorted(missing))
        extra = set(rx.groupindex) - TEMPLATE_GROUPS - TEMPLATE_OPTIONAL_GROUPS
        if extra:
            out.append('template pattern has unknown groups %s' % sorted(extra))
        if '{product}' in (t.get('method') or '') and 'product' not in rx.groupindex:
            out.append('template method uses {product} but the pattern has no product group')
    except re.error as e:
        out.append('template pattern does not compile: %s' % e)
    if not t.get('fixture'):
        out.append('template must name the real-sample fixture it was built from')
    if 'merchant_truncated' in t and not isinstance(t['merchant_truncated'], bool):
        out.append('template merchant_truncated must be true|false')
    if 'status' in t:
        if t['status'] not in STATUSES:
            out.append('template status must be verified|unverified')
        src = t.get('source')
        if not isinstance(src, list) or any(not isinstance(s, str) or not SOURCE_RE.match(s) for s in src):
            out.append('template source must be a list of https URLs or owner: notes')
        if t['status'] == 'verified':
            if not src:
                out.append('verified template needs a source')
            try:
                datetime.date.fromisoformat(t.get('verified_at') or '')
            except ValueError:
                out.append('verified template needs verified_at (ISO date)')
    return out


def validate(doc):
    """Schema check for one issuer file. Returns problems (empty = valid)."""
    out = []
    if doc.get('schema') != SCHEMA:
        out.append('schema must be %d' % SCHEMA)
    if not re.match(r'^[a-z0-9]+$', doc.get('issuer', '')):
        out.append('issuer key must be lowercase alnum')
    if not str(doc.get('display_ko', '')).strip():
        out.append('display_ko required')
    if not isinstance(doc.get('method_stems'), list) or not doc['method_stems']:
        out.append('method_stems must be a nonempty list')
    guide = doc.get('guide') or {}
    if set(guide) != set(GUIDE_FIELDS):
        out.append('guide fields must be exactly %s' % (GUIDE_FIELDS,))
    for name in GUIDE_FIELDS:
        if name in guide:
            out += _field_problems(name, guide[name])
    sms = doc.get('sms') or {}
    marks = sms.get('markers')
    if not isinstance(marks, list) or not marks:
        out.append('sms.markers must be a nonempty list')
    else:
        for m in marks:
            try:
                re.compile(m)
            except re.error:
                out.append('sms.markers: bad regex %r' % m)
    if not isinstance(sms.get('templates'), list):
        out.append('sms.templates must be a list (empty until a real sample)')
    else:
        for t in sms['templates']:
            out += validate_template(t)
    for name in SMS_RECORDS:
        if name in sms:
            out += _field_problems('sms.' + name, sms[name])
    return out


def sms_offerable(doc):
    """Can the onboarding offer SMS automation for this issuer? Only when no
    source says SMS is unavailable (false, verified or not)."""
    return doc['guide']['sms_available']['value'] is not False


def shortcut_keyword(doc):
    """Body text for the Shortcut 'message contains' trigger, or None."""
    rec = doc['sms'].get('shortcut_keyword') or {}
    return rec.get('value')


def identify(text, docs):
    """The one issuer whose marker appears in the SMS text, else None
    (none or several -> the model lane decides, the owner confirms)."""
    hits = [iss for iss, d in docs.items() if any(re.search(m, text) for m in d['sms']['markers'])]
    return hits[0] if len(hits) == 1 else None


def for_method(name, aliases, docs):
    """Issuer of a registered payment method, by name/alias stem. None when
    zero or several issuers fit."""
    hay = [name] + [a for a in aliases if a]
    hits = {iss for iss, d in docs.items()
            for stem in d['method_stems'] for h in hay if stem.casefold() in h.casefold()}
    return hits.pop() if len(hits) == 1 else None
