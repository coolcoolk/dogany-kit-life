"""One owner-purpose vocabulary and mapper; raw evidence never becomes UI copy.

Legacy instrument notes are projected through the same mapper on read. Reads
stay read-only; every new answer persists labels on pay_method.
"""
import json
import re
import unicodedata
from pathlib import Path

LABELS = json.loads(Path(__file__).with_name('ledger_purpose_lexicon.json').read_text(encoding='utf-8'))['labels']
VOCABULARY = tuple(item['label'] for item in LABELS)
COPY_KEYS = {item['label']: item['key'] for item in LABELS}


def compact(words):
    return re.sub(r'[\W_]+', '', unicodedata.normalize('NFKC', words).casefold())


def normalize(words='', tags=None, button=None):
    """Explicit semantic tags win; otherwise match deterministic lexicon data."""
    if tags is not None:
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(',') if t.strip()]
        if not isinstance(tags, (list, tuple)) or any(t not in VOCABULARY for t in tags):
            raise ValueError('choose purpose tags from the fixed vocabulary')
        return list(dict.fromkeys(tags))
    value = compact(words or '')
    selected = [item['label'] for item in LABELS if button in item['buttons']]
    return selected or [item['label'] for item in LABELS if
                        any(compact(term) in value for term in [item['label'], *item['words']])]


def view(tags, needs_clarification=False):
    return dict(tags=tags, keys=[COPY_KEYS[t] for t in tags],
                needs_clarification=needs_clarification,
                vocabulary=list(VOCABULARY), vocabulary_keys=[COPY_KEYS[t] for t in VOCABULARY])


def owner_purpose(conn, method):
    """Canonical owner labels, including pre-upgrade instrument answers."""
    own = json.loads(method['purpose_tags'] or '[]')
    if own and method['purpose_origin'] in ('owner-stated', 'user'):
        # Historical SDK labels are not printed as arbitrary free text.
        tags = own if all(t in VOCABULARY for t in own) else normalize(','.join(own))
        return view(tags, not tags)
    if method['purpose_origin'] in ('owner-stated', 'user'):
        tags = normalize(method['purpose_memo'] or '')
        return view(tags, bool(method['purpose_memo']) and not tags)
    hit = conn.execute("SELECT detail FROM ledger_preference WHERE topic='ledger_plan_fact' "
                       "AND item=? AND status='active'", ('instrument:'+str(method['id']),)).fetchone()
    if not hit:
        return view([])
    fact = json.loads(hit[0])
    if fact.get('status') != 'answered' or fact.get('answer') in ('skip', 'capture'):
        return view([])
    tags = normalize(fact.get('note') or fact.get('said') or '',
                     tags=fact.get('purpose_tags'), button=fact.get('answer'))
    return view(tags, not tags)


def store(conn, method, words, tags=None, button=None):
    if tags == '' or tags == []:
        tags = None
    tags = normalize(words, tags=tags, button=button)
    conn.execute('UPDATE pay_method SET purpose_tags=?,purpose_memo=?,purpose_origin=? WHERE id=?',
                 (json.dumps(tags, ensure_ascii=False) if tags else None,
                  words or None, 'owner-stated', method['id']))
    return view(tags, not tags)
