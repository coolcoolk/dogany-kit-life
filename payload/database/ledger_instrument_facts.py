"""Read-only instrument authority. Memory is attributable evidence, never a setter.

Business purpose, accounting scope and employer reimbursement are independent
facts. In particular a business-use card is not an employer-reimbursed card.
"""
import json
import os
from pathlib import Path
import re
import unicodedata


def _compact(value):
    return ''.join(c for c in unicodedata.normalize('NFKC', value).casefold() if c.isalnum())


def _memory_root(conn):
    for key in ('LEDGER_ROOT', 'DOGANY_INSTANCE_ROOT', 'PROJECT_ROOT'):
        if os.environ.get(key):
            return Path(os.environ[key]) / 'memories'
    db = next((r[2] for r in conn.execute('PRAGMA database_list') if r[1] == 'main'), '')
    return Path(db).parent.parent / 'memories' if db else None


def memory_evidence(conn, methods):
    """Resolve mentions conservatively, including issuer-only historical aliases.

    An issuer-only mention remains ambiguous even with one registered card:
    another (e.g. the employer's) card may be absent from the current roster.
    """
    from ledger_v6 import FAMILIES, family
    root = _memory_root(conn)
    out = {m['id']: [] for m in methods}
    if root is None or not root.is_dir():
        return out
    families = {}
    for m in methods:
        fam = family(m['name'])
        if fam:
            families.setdefault(fam, []).append(m)
    for path in sorted(root.rglob('*.md')):
        try:
            lines = path.read_text(encoding='utf-8').splitlines()
        except (OSError, UnicodeError):
            continue
        for line_no, line in enumerate(lines, 1):
            compact = _compact(line)
            for fam, words in FAMILIES:
                members = families.get(fam, [])
                if not members or not any(_compact(w) in compact for w in words):
                    continue
                generic = {_compact(w+s) for w in words for s in ('', '카드', 'card')}
                matched = []
                for m in members:
                    aliases = m.get('aliases') or []
                    if isinstance(aliases, str):
                        try:
                            aliases = json.loads(aliases)
                        except ValueError:
                            aliases = re.split(r'[|,]', aliases)
                    names = [m['name'], *aliases]
                    # The optional generic suffix is often omitted in owner prose.
                    names += [re.sub(r'\s*(카드|card)$', '', n, flags=re.I) for n in names]
                    if any(_compact(n) not in generic and _compact(n) in compact for n in names if _compact(n)):
                        matched.append(m['id'])
                if not matched:
                    # Samsung Electronics and ordinary words such as Korean
                    # "we/one" (우리/하나) are not issuer-only instrument names.
                    raw = unicodedata.normalize('NFKC', line).casefold()
                    mention = r'(?<!\w)(?:%s)\s*(?:카드|card|은행|bank|뱅크|계좌|통장|[은는이가의을를]|\b)' % '|'.join(re.escape(w) for w in words)
                    financial = ('카드', '계좌', '은행', '뱅크', '통장', '정산', '환급', '결제',
                                 '사업용', '생활비', 'card', 'account', 'bank', 'reimburse')
                    if not re.search(mention, raw) or not any(w in compact for w in financial):
                        continue
                evidence = dict(source=str(Path('memories') / path.relative_to(root)), line=line_no,
                                text=line, authority='evidence_only', may_override_record=False,
                                status='matched_evidence' if matched else 'ambiguous_instrument',
                                candidate_ids=matched or [m['id'] for m in members])
                if not matched:
                    evidence.update(reason='issuer_only', may_refer_to_unregistered_instrument=True)
                for mid in evidence['candidate_ids']:
                    out[mid].append(evidence)
    return out


def records(conn):
    """One facts payload for profile, reduction and ad-hoc instrument reads."""
    from ledger_profile import method_roles, method_memos
    from ledger_purpose_labels import owner_purpose
    from ledger_v6 import rows, KIND_LAYER, _json_list
    all_methods = rows(conn, 'SELECT * FROM pay_method ORDER BY id')
    by_id = {m['id']: m for m in all_methods}
    methods = [m for m in all_methods if m['canonical_id'] is None]

    def canonical(mid):
        visited = set()
        while mid in by_id and mid not in visited:
            visited.add(mid)
            row = by_id[mid]
            if row['canonical_id'] is None:
                return row
            mid = row['canonical_id']
        return None

    for m in methods:
        m['aliases'] = _json_list(m['aliases'])
    for m in all_methods:
        if m['canonical_id'] is not None:
            target = canonical(m['id'])
            if target:
                target['aliases'] += [m['name'], *_json_list(m['aliases'])]
    roles, memos = method_roles(conn), method_memos(conn)
    evidence = memory_evidence(conn, methods)

    def own(m):
        purpose = owner_purpose(conn, m)
        return dict(id=m['id'], name=m['name'], kind=m['kind'],
                    aliases=list(dict.fromkeys(m['aliases'])),
                    layer=m['layer'] or KIND_LAYER.get(m['kind']),
                    scope_default=m['scope_default'], owner_roles=roles.get(m['id'], []),
                    purpose=purpose, purpose_origin=m['purpose_origin'],
                    purpose_memo=m['purpose_memo'], role_memo=memos.get(m['id']))

    result = {}
    for m in methods:
        fact = own(m)
        linked = canonical(m['linked_account_id'])
        fact.update(authority='instrument_record', memory_may_override=False,
                    linked_account=own(linked) if linked else None,
                    reimbursement='employer' if m['kind'] == 'corporate_card' else 'not_recorded',
                    spending_policy=dict(scope_default=m['scope_default'],
                                         count_in_personal_spending=m['scope_default'] == 'personal',
                                         exclude_from_memory=False,
                                         reimbursement_requires_payment_evidence=True),
                    memory_evidence=evidence[m['id']])
        fact['confirmed_roles'], fact['role_from'] = confirmed_roles(fact)
        result[m['id']] = fact
    for m in all_methods:
        target = canonical(m['id'])
        if target:
            result[m['id']] = result[target['id']]
    return result


def confirmed_roles(fact):
    """Canonical owner purpose, then legacy roles, before any account guess."""
    purpose_roles = {'생활비': 'living', '월급·고정비': 'salary_fixed', '저축': 'saving', '사업': 'business'}
    for basis, source in ((fact, 'owner'), (fact.get('linked_account') or {}, 'account')):
        roles = [purpose_roles[t] for t in (basis.get('purpose') or {}).get('tags', [])
                 if t in purpose_roles] or basis.get('owner_roles')
        if roles:
            return list(dict.fromkeys(roles)), source
    return [], None
