"""Opaque payments: the merchant is a payment intermediary (Coupay, Naver Pay,
KG Inicis ...), so the record says who took the money, not what was bought.

Owner design (2026-09-26): never ask at payment time. The DAILY retro asks
once, in one grouped block under the review table ("2 tissues, 3 lunch"); what
is still unnamed rolls into the WEEKLY retro once; what the weekly retro
does not name is written as etc (gita) (no third ask). An answer lands as the spend
row's `note` + `category` through `spend-upd`.

Recurring memory: an answer is remembered per (intermediary, amount). The
next payment of the same intermediary and amount 25..35 days after the last
one (a monthly membership) is filled from memory and not asked.

Data:   intermediaries.json (schema 1, match corpus; validate() pins it).
State:  files/ledger/opaque.json (module state, no ledger data):
          items   {intake item id: {asked: [lane..], status, answer?}}
                  status open | named | auto | twin | etc | excluded
          memory  {"<key>|<amount>": {name, category, last_date}}
The ledger itself is written only through kit verbs (`spend-upd`).
"""
import datetime
import json
import unicodedata

from . import paths

SCHEMA = 1
STATE = 'opaque.json'
RECUR_DAYS = (25, 35)
ETC = '\uae30\ud0c0'
MIN_TRUNCATED = 3


def norm(s):
    return ''.join(ch for ch in unicodedata.normalize('NFKC', s or '').casefold() if ch.isalnum())


def data_path():
    return paths.SERVICE_DIR / 'intermediaries.json'


def load(path=None):
    return json.loads((path or data_path()).read_text(encoding='utf-8'))


def validate(doc):
    """-> list of problems ([] = valid)."""
    out = []
    if doc.get('schema') != SCHEMA:
        out.append('schema must be %d' % SCHEMA)
    seen_keys, seen_pat = set(), {}
    for e in doc.get('intermediaries') or []:
        k = e.get('key')
        if not k or k in seen_keys:
            out.append('missing or duplicate key: %r' % k)
        seen_keys.add(k)
        if not e.get('display_ko'):
            out.append('%s: display_ko required' % k)
        pats = e.get('patterns') or []
        if not pats:
            out.append('%s: patterns required' % k)
        for p in pats:
            if not norm(p) or norm(p) != p:
                out.append('%s: pattern %r must be written normalised (%r)' % (k, p, norm(p)))
            if seen_pat.get(p, k) != k:
                out.append('%s: pattern %r already belongs to %s' % (k, p, seen_pat[p]))
            seen_pat[p] = k
    if not doc.get('intermediaries'):
        out.append('no intermediaries')
    return out


_CACHE = {}


def entries():
    if 'e' not in _CACHE:
        _CACHE['e'] = load()['intermediaries']
    return _CACHE['e']


def match(merchant, truncated=False):
    """The intermediary entry a merchant names, else None. A truncated SMS
    merchant (a few leading characters) also matches a pattern it starts."""
    m = norm(merchant)
    if not m:
        return None
    for e in entries():
        for p in e['patterns']:
            if p in m or (truncated and len(m) >= MIN_TRUNCATED and p.startswith(m)):
                return e
    return None


# ---- module state ----------------------------------------------------------

def state_path():
    return paths.state_dir() / STATE


def load_state():
    try:
        d = json.loads(state_path().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        d = {}
    d = d if isinstance(d, dict) else {}
    d.setdefault('items', {})
    d.setdefault('memory', {})
    return d


def save_state(d):
    p = state_path()
    tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    tmp.replace(p)


def item_state(st, item_id):
    return st['items'].get(str(item_id)) or {'asked': [], 'status': 'open'}


def recall(st, key, amount, date, *, answered_pattern=False):
    """Monthly intermediary answer, or a future owner-answered group pattern."""
    mem = st['memory'].get('%s|%d' % (key, amount))
    if not mem:
        return None
    gap = (datetime.date.fromisoformat(date) - datetime.date.fromisoformat(mem['last_date'])).days
    # Owner 2026-11-09: an answered settlement group is remembered as a
    # pattern, even when the following month's rehearsal changes day/price.
    # Ordinary intermediary memories retain their original 25..35 day gate.
    if answered_pattern:
        return mem if gap > 0 else None
    return mem if RECUR_DAYS[0] <= gap <= RECUR_DAYS[1] else None


def remember(st, key, amount, date, name, category):
    k = '%s|%d' % (key, amount)
    prev = st['memory'].get(k)
    if prev and prev['last_date'] > date:
        date = prev['last_date']
    st['memory'][k] = {'name': name, 'category': category, 'last_date': date}


def parse_answers(specs):
    """['2=tissues:household', '3=lunch'] -> {2: ('tissues', 'household'), 3: ('lunch', None)}
    (names and categories are the owner's Korean words in real use).
    The session model maps the owner's one line ("2 tissues, 3 lunch") to these."""
    out = {}
    for spec in specs or ():
        no, sep, rest = spec.partition('=')
        name, _, cat = rest.partition(':')
        if not sep or not no.strip().isdigit() or not name.strip():
            raise ValueError('answer must be N=name[:category], got %r' % spec)
        out[int(no)] = (name.strip(), cat.strip() or None)
    return out
