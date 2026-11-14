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


def readings(rows, methods):
    """Map raw labelled screen rows, retaining collisions for explicit review.

    Bank branding may qualify a product name. Conflicting readings of a
    single registered product never overwrite each other or get summed.
    """
    if not isinstance(rows, list) or not rows:
        raise ValueError('balance capture needs labelled reading rows')
    candidates, unresolved = {}, []
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get('name'), str)
                or not row['name'].strip() or not isinstance(row.get('bank', ''), str)):
            raise ValueError('balance capture needs a name and whole KRW amount per row')
        raw = re.sub(r'[,원\s]', '', str(row.get('amount', '')))
        if not re.fullmatch(r'-?\d+', raw) or abs(int(raw)) > 10**12:
            raise ValueError('balance capture needs a whole KRW amount in range')
        label = row['name']
        bank = row.get('bank', '').strip()
        if bank and not _normalize(label).startswith(_normalize(bank)):
            label = bank + ' ' + label
        found, _ = match([label], methods)
        if label not in found:
            unresolved.append(dict(row))
            continue
        mid = str(found[label]['id'])
        candidates.setdefault(mid, []).append((int(raw), row))
    values = {}
    for mid, found in candidates.items():
        if len({amount for amount, _ in found}) != 1:
            unresolved.extend(dict(row) for _, row in found)
        else:
            values[mid] = found[0][0]
    return values, unresolved
