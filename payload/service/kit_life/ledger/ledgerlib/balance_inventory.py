"""Conservative identity matching for explicitly labelled balance inventories."""
import re
import unicodedata

from . import dashboard, sources


BALANCE_KINDS = ('account', 'savings', 'securities', 'cash', 'easy_pay')
# Bank names may be omitted by an aggregator's account list. Only a leading
# bank name can be removed; arbitrary suffix/substring matching is unsafe.
BANK_PREFIXES = tuple(sources.load()['balance_bank_prefixes'])


def _normalize(name):
    return ''.join(c for c in unicodedata.normalize('NFKC', name).casefold() if c.isalnum())


def _without_bank(name):
    for prefix in sorted(map(_normalize, BANK_PREFIXES), key=len, reverse=True):
        if name.startswith(prefix) and len(name) > len(prefix):
            return name[len(prefix):]
    # Other explicitly named banks need no growing list of product names.
    match = re.match(r'.+?(?:은행|뱅크)(.+)', name)
    return match[1] if match else name


def match(named, methods):
    """Recognize one canonical instrument, including its aliases and twins.

    A short name must identify exactly one instrument. Two different bank
    prefixes are never both stripped to make unrelated accounts match.
    """
    by_id = {m['id']: m for m in methods}

    def canonical(item):
        seen = set()
        while item.get('canonical_id') in by_id and item['id'] not in seen:
            seen.add(item['id'])
            item = by_id[item['canonical_id']]
        return item

    known = {}
    for item in methods:
        target = canonical(item)
        if target['kind'] not in BALANCE_KINDS:
            continue
        names = [item['name'], *(item.get('aliases') or []), *(item.get('twins') or [])]
        variants = known.setdefault(target['id'], set())
        for name in names:
            variants.update(filter(None, (_normalize(name), _normalize(dashboard.roster_display(name)))))

    recognized, unknown = {}, []
    for name in dict.fromkeys(named):
        key = _normalize(name)
        matches = {mid for mid, variants in known.items() if key and any(
            key == v or key == _without_bank(v) or _without_bank(key) == v for v in variants)}
        if len(matches) != 1:
            unknown.append(name)
        else:
            recognized[name] = canonical(by_id[next(iter(matches))])
    return recognized, unknown


def missing(named, methods):
    return match(named, methods)[1]
