"""Read-only goal-spend proposals and reversible owner-confirmed merchant links.

Proposals never change payment classification. The owner confirmation is kept
in the existing restorable ledger preferences, with payment identity anchors.
Fitness selections belong to the domain and retain their member goal anchors.
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


FITNESS = 'domain:health'


def _group_key(goal):
    return FITNESS if goal['domain'] in ('health', 'training') else goal['id']


def _member(goal):
    return dict(goal_id=goal['id'], goal_ulid=goal['ulid'])


def _selections(c, active):
    """Migrate legacy fitness picks in memory; an explicit domain pick wins.

    Legacy goal identities must still exist unchanged. Domain picks deliberately
    survive member replacement, but every payment rule keeps its own anchor.
    """
    facts = [f for f in P.facts(c).values() if f.get('kind') == 'goal_spend_selection']
    selected = {FITNESS: f for f in facts if f.get('domain') == 'health' and f['goal_id'] == FITNESS}
    legacy = []
    active_ids = {g['id'] for g in active}
    # A replaced goal can be archived before the first upgraded read. Its
    # original identity still proves which domain the owner approved.
    by_id = {r[0]: dict(id=r[0], ulid=r[1], domain=r[2]) for r in c.execute(
        'SELECT id,ulid,domain FROM ledger_goal')}
    for fact in facts:
        goal = by_id.get(fact['goal_id'])
        if not goal or fact.get('goal_ulid') != goal['ulid']:
            continue
        if _group_key(goal) == FITNESS:
            legacy.append((goal, fact))
        elif goal['id'] in active_ids:
            selected[goal['id']] = fact
    if FITNESS not in selected and legacy:
        selected[FITNESS] = dict(kind='goal_spend_selection', goal_id=FITNESS,
            goal_ulid=FITNESS, domain='health', members=[_member(g) for g, _ in legacy],
            rules=[r for _, f in legacy for r in f['rules']],
            excluded=[a for _, f in legacy for a in f['excluded']],
            said=legacy[-1][1]['said'])
    return selected


def selections_by_goal(c, active):
    selections = _selections(c, active)
    return {g['id']: selections[_group_key(g)] for g in active if _group_key(g) in selections}


def confirmed(c, payments, today):
    """Current owner-approved links, including future exact-merchant payments."""
    active = {g['id']: g for g in G.goals(c, today)}
    result = {}
    selections = selections_by_goal(c, list(active.values()))
    for gid, selection in selections.items():
        goal = active[gid]
        for row in payments:
            if not _eligible(row, today, None if selection.get('domain') == 'health' else goal):
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


def _eligible(row, today, goal=None):
    return (row['kind'] == 'expense' and row['class'] == 'spend' and row['amount'] > 0
            and row.get('scope') != 'business' and row['date'] <= today
            and (not goal or not goal['starts_on'] or goal['starts_on'] <= row['date'])
            and (not goal or not goal['ends_on'] or goal['ends_on'] >= row['date']))


def candidates(c, payments, today):
    """Fitness shares one domain group; other active goals stay separate."""
    active = G.goals(c, today)
    purposes = G.classify(c, payments, today)
    places = list(_places(c))
    selections = selections_by_goal(c, active)
    groups = {}
    for goal in active:
        rows = []
        selection = selections.get(goal['id'], {})
        excluded = selection.get('excluded', [])
        for row in sorted(payments, key=lambda r: (r['date'], r['id'])):
            # A domain confirmation outlives the dates of a replacement goal.
            confirmed_domain = (selection.get('domain') == 'health' and any(
                e['kind'] == 'confirmed_merchant' and e['goal_id'] == goal['id']
                for e in purposes[row['id']]['evidence']))
            if not _eligible(row, today, None if confirmed_domain else goal):
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
        key = _group_key(goal)
        if key not in groups:
            groups[key] = dict(goal_id=key, goal_ulid=FITNESS if key == FITNESS else goal['ulid'],
                               title=goal_name(goal['title']), rows=[])
            if key == FITNESS:
                groups[key].update(domain='health', members=[], goal_names=[])
        group = groups[key]
        if key == FITNESS:
            group['members'].append(_member(goal))
            name = goal_name(goal['title'])
            if name not in group['goal_names']:
                group['goal_names'].append(name)
        by_id = {r['id']: r for r in group['rows']}
        for row in rows:
            if row['id'] not in by_id or row['confirmed']:
                by_id[row['id']] = row
        group['rows'] = sorted(by_id.values(), key=lambda r: (r['date'], r['id']))
    return list(groups.values())


def confirm(c, data, today):
    """Validate the issued snapshot in full before replacing any selection."""
    from ledger_diag import _month_rows
    months = data.get('months', [])
    if not months or any(not re.fullmatch(r'\d{4}-\d{2}', m) for m in months):
        raise ValueError('issued spending months required')
    payments = [r for month in months for r in _month_rows(c, month)]
    current = {g['goal_id']: g for g in candidates(c, payments, today)}
    selections = _selections(c, G.goals(c, today))
    pending = []
    seen = set()
    for group in data.get('groups', []):
        gid = group['goal_id']
        live = current.get(gid)
        if (not live or gid in seen or live['goal_ulid'] != group['goal_ulid']
                or live.get('members') != group.get('members')):
            raise ValueError('issued active goal required')
        seen.add(gid)
        available = {r['id']: r for r in live['rows']}
        old = selections.get(gid, {})
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
        fact = dict(kind='goal_spend_selection', goal_id=gid, goal_ulid=live['goal_ulid'],
                    rules=rules, excluded=excluded, said=data['said'])
        if live.get('domain') == 'health':
            fact.update(domain='health', members=old.get('members', [])[:])
            for member in live['members']:
                if member not in fact['members']:
                    fact['members'].append(member)
        pending.append(fact)
    if not pending:
        raise ValueError('issued goal-spend groups required')
    for fact in pending:
        P.put(c, 'goal-spend:'+str(fact['goal_id']), fact)
    return dict(saved=True, groups=len(pending))
