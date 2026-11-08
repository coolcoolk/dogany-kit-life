"""Read-only goal-spend proposals and reversible owner-confirmed merchant links.

Proposals never change payment classification. The owner confirmation is kept
in the existing restorable ledger preferences, with payment and goal identity
anchors; a merchant alone cannot resurrect a deleted or changed anchor.
"""
import json
import re

import ledger_goal_purpose as G
import ledger_plan as P
import near_match


BRANDS = {'허닭': 'chicken', '랭킹닭컴': 'chicken', '아임닭': 'chicken',
          '맛있닭': 'chicken', '마이프로틴': 'supplement', 'myprotein': 'supplement'}
MARKETPLACE = re.compile(r'쿠팡|coupang|네이버|naver|11번가|지마켓|gmarket|옥션|auction|알리|amazon|테무', re.I)


def goal_name(title):
    # A metric annotation belongs to the goal engine, not this spending card.
    return re.split(r'\s*[:：]\s*', title, maxsplit=1)[0].strip()


def _gym_key(name):
    key = near_match.compact(name)
    # Korean card slips spell the M in 휘트니스M as 엠 and put it first.
    key = re.sub(r'^엠(?=휘트니스|피트니스)', 'm', key)
    return re.sub(r'^([a-z]+)(휘트니스|피트니스)', r'\2\1', key)


def _places(c):
    for pid, name, aliases in c.execute("SELECT id,name,aliases FROM place WHERE status='active'"):
        names = [name] + json.loads(aliases or '[]')
        if any(G.GYM.search(n) for n in names):
            yield dict(id=pid, name=name, names=names)


def _proposal(name, places):
    if MARKETPLACE.search(name):
        return None
    key = near_match.compact(name)
    if key in BRANDS:
        return dict(tag=BRANDS[key], kind='diet_brand', brand=key)
    merchant = _gym_key(name)
    # A generic word such as 휘트니스 cannot identify a saved gym.
    if not re.sub(r'휘트니스|피트니스|fitness|헬스장|gym', '', merchant):
        return None
    hits = [p for p in places if any(
        merchant == _gym_key(n) or near_match.compare(merchant, _gym_key(n), 'place')
        for n in p['names'])]
    if len(hits) == 1:
        return dict(tag='gym', kind='saved_place', place_id=hits[0]['id'], place_name=hits[0]['name'])
    return None


def _selections(c):
    return [f for f in P.facts(c).values() if f.get('kind') == 'goal_spend_selection']


def confirmed(c, payments, today):
    """Current owner-approved links, including future exact-merchant payments."""
    active = {g['id']: g for g in G.goals(c, today)}
    result = {}
    for selection in _selections(c):
        goal = active.get(selection['goal_id'])
        if not goal or goal['ulid'] != selection['goal_ulid']:
            continue
        for row in payments:
            if not _eligible(row, today, goal):
                continue
            if any(G._anchor(c, row['id']) == a for a in selection['excluded'] if a[0] == row['id']):
                continue
            for rule in selection['rules']:
                if G._anchor(c, rule['anchor'][0]) != rule['anchor']:
                    continue
                if (rule['anchor'][0] == row['id'] or
                        not MARKETPLACE.search(row['name']) and rule['merchant'] == P.merchant_key(row['name'])):
                    result.setdefault(row['id'], {})[goal['id']] = dict(
                        kind='confirmed_merchant', tag=rule['tag'], source='owner',
                        evidence=selection['said'], anchor_id=rule['anchor'][0])
                    break
    return result


def _eligible(row, today, goal):
    return (row['kind'] == 'expense' and row['class'] == 'spend' and row['amount'] > 0
            and row.get('scope') != 'business' and row['date'] <= today
            and (not goal['starts_on'] or goal['starts_on'] <= row['date'])
            and (not goal['ends_on'] or goal['ends_on'] >= row['date']))


def candidates(c, payments, today):
    """One group per active goal. Only observed, positive personal spending."""
    active = G.goals(c, today)
    purposes = G.classify(c, payments, today)
    places = list(_places(c))
    selections = {f['goal_id']: f for f in _selections(c)}
    groups = []
    for goal in active:
        rows = []
        selection = selections.get(goal['id'], {})
        excluded = selection.get('excluded', []) if selection.get('goal_ulid') == goal['ulid'] else []
        for row in sorted(payments, key=lambda r: (r['date'], r['id'])):
            if not _eligible(row, today, goal):
                continue
            anchor = G._anchor(c, row['id'])
            if anchor in excluded:
                continue
            evidence = [e for e in purposes[row['id']]['evidence'] if e['goal_id'] == goal['id']
                        and e.get('source') == 'owner'
                        and e['kind'] in ('transaction', 'person', 'repetition', 'confirmed_merchant')]
            proposal = _proposal(row['name'], places) if goal['domain'] in ('health', 'training') else None
            if not evidence and not proposal:
                continue
            tag = next((e['tag'] for e in evidence if e.get('tag')), None)
            if not tag and proposal:
                tag = proposal['tag']
            rows.append(dict(id=row['id'], date=row['date'], name=row['name'], amount=row['amount'],
                             anchor=anchor, tag=tag, confirmed=bool(evidence),
                             evidence=evidence or [proposal]))
        groups.append(dict(goal_id=goal['id'], goal_ulid=goal['ulid'], title=goal_name(goal['title']), rows=rows))
    return groups


def confirm(c, data, today):
    """Validate the issued snapshot in full before replacing any selection."""
    from ledger_diag import _month_rows
    months = data.get('months', [])
    if not months or any(not re.fullmatch(r'\d{4}-\d{2}', m) for m in months):
        raise ValueError('issued spending months required')
    payments = [r for month in months for r in _month_rows(c, month)]
    current = {g['goal_id']: g for g in candidates(c, payments, today)}
    pending = []
    seen = set()
    for group in data.get('groups', []):
        gid = group['goal_id']
        live = current.get(gid)
        if not live or gid in seen or live['goal_ulid'] != group['goal_ulid']:
            raise ValueError('issued active goal required')
        seen.add(gid)
        available = {r['id']: r for r in live['rows']}
        old = P.facts(c).get('goal-spend:'+str(gid), {})
        rules, excluded = [], []
        ids = set()
        for row in group['rows']:
            hit = available.get(row['id'])
            if (not hit and row.get('removed') and old.get('goal_ulid') == live['goal_ulid']
                    and row['anchor'] in old.get('excluded', [])
                    and G._anchor(c, row['id']) == row['anchor'] and row['id'] not in ids):
                ids.add(row['id'])
                excluded.append(row['anchor'])
                continue
            if not hit or row['id'] in ids or hit['anchor'] != row['anchor'] or hit['tag'] != row['tag']:
                raise ValueError('goal-spend proposal changed; render again')
            ids.add(row['id'])
            rule = dict(anchor=hit['anchor'], merchant=P.merchant_key(hit['name']), tag=hit['tag'])
            (excluded if row.get('removed') else rules).append(hit['anchor'] if row.get('removed') else rule)
        if old.get('goal_ulid') == live['goal_ulid']:
            # Preserve confirmed history outside this screen's window. A row
            # removal also revokes its merchant rule, until approved anew.
            rules = [r for r in old.get('rules', []) if r['anchor'][0] not in ids] + rules
            excluded = [a for a in old.get('excluded', []) if a[0] not in ids] + excluded
        pending.append(dict(kind='goal_spend_selection', goal_id=gid, goal_ulid=live['goal_ulid'],
                            rules=rules, excluded=excluded, said=data['said']))
    if not pending:
        raise ValueError('issued goal-spend groups required')
    for fact in pending:
        P.put(c, 'goal-spend:'+str(fact['goal_id']), fact)
    return dict(saved=True, groups=len(pending))
