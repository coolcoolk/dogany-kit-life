"""Evidence-backed second spend layer (owner picks 2026-10-28 21:35/21:36).

Labels are current goal titles, never the payment-method purpose vocabulary.
Existing owner statements can bind a transaction, person or recurring stream
to a real goal. A narrow exercise-payment bridge also works without a binding.
No binding is solicited by the onboarding flow.
"""
import collections
import datetime
import re


# Exercise evidence supports exercise/body-composition goals, not every goal
# in the health domain (a medical bill, meal or shop is not exercise evidence).
EXERCISE_METRICS = frozenset(('body_fat_pct',))
GYM = re.compile(r'헬스장|피트니스|휘트니스|\bgym\b|\bfitness\b', re.I)
GYM_GOAL = re.compile(r'헬스|피트니스|휘트니스|\bgym\b|\bfitness\b', re.I)


def goals(c, today):
    """Use the goal engine's active owner commitments, with their live titles.

    ledger_goal is the engine's write model; goal is its shared projection.
    Do not count that projection a second time, or admit proposed/retired rules.
    """
    return [dict(table='ledger_goal', id=r[0], ulid=r[1], title=r[2], metric=r[3],
                 starts_on=r[4], ends_on=r[5], domain=r[6]) for r in c.execute(
        "SELECT id,ulid,title,metric,starts_on,ends_on,domain FROM ledger_goal WHERE status='active' "
        "AND commitment_kind='goal' "
        "AND (starts_on IS NULL OR starts_on<=?) AND (ends_on IS NULL OR ends_on>=?) "
        "ORDER BY id", (today, today))]


def _anchor(c, sid):
    row = c.execute("SELECT id,date,name,amount,method,created_at,import_key FROM spend_entry "
                    "WHERE id=? AND kind='expense' AND COALESCE(class,'spend')='spend' "
                    "AND scope<>'business' AND amount>0", (sid,)).fetchone()
    return list(row) if row else None


def record(c, data, today):
    """Ingest an ALREADY stated connection, never request an owner's purpose.

    The existing ledger-plan-answer API carries the original words. Scope is
    one transaction by default. Broader person/repetition evidence requires a
    real for-link or fixed stream on the anchor payment, never a merchant guess.
    """
    import ledger_plan
    active = {g['id']:g for g in goals(c,today)}
    gid, sid, via = data.get('goal_id'), data.get('id'), data.get('via','transaction')
    if type(gid) is not int or gid not in active:
        raise ValueError('existing active owner goal required')
    anchor = _anchor(c,sid) if type(sid) is int else None
    if not anchor or anchor[1] > today:
        raise ValueError('existing positive owner spend required')
    goal = active[gid]
    if ((goal['starts_on'] and goal['starts_on']>anchor[1])
            or (goal['ends_on'] and goal['ends_on']<anchor[1])):
        raise ValueError('payment must fall within the goal dates')
    if via == 'transaction':
        ref = str(sid)
    elif via == 'person':
        from ledger_v6 import link_ref
        ref = link_ref(c,'person',data.get('ref',''))
        if not c.execute("SELECT 1 FROM spend_link WHERE spend_id=? AND entity_kind='person' "
                         "AND entity_ref=? AND role='for'", (sid,ref)).fetchone():
            raise ValueError('person evidence needs an existing for-link')
    elif via == 'repetition':
        row = c.execute('SELECT s.stream_id FROM spend_entry s JOIN fixed_cost f ON f.id=s.stream_id '
                        'WHERE s.id=?', (sid,)).fetchone()
        if not row:
            raise ValueError('repetition evidence needs an existing fixed stream')
        ref = str(row[0])
    else:
        raise ValueError('evidence via is transaction, person or repetition')
    if not isinstance(data.get('said'),str) or not data['said'].strip():
        raise ValueError('original owner words required')
    fact = dict(kind='goal_evidence',goal_id=gid,goal_ulid=active[gid]['ulid'],
                anchor=anchor,via=via,ref=ref,said=data['said'])
    ledger_plan.put(c,'goal-evidence:%s:%s:%s' % (gid,via,ref),fact)
    return dict(saved=True,**fact)


def _binding_live(c, fact):
    anchor = fact.get('anchor')
    if not anchor or _anchor(c,anchor[0]) != anchor:
        return False
    if fact['via']=='person':
        return bool(c.execute("SELECT 1 FROM spend_link l JOIN persons p ON l.entity_ref=CAST(p.id AS TEXT) "
                              "WHERE l.spend_id=? AND l.entity_kind='person' AND l.role='for' AND l.entity_ref=?",
                              (anchor[0],fact['ref'])).fetchone())
    if fact['via']=='repetition':
        return bool(c.execute('SELECT 1 FROM spend_entry s JOIN fixed_cost f ON f.id=s.stream_id '
                              'WHERE s.id=? AND CAST(s.stream_id AS TEXT)=?',
                              (anchor[0],fact['ref'])).fetchone())
    return fact['via']=='transaction'


def classify(c, payments, today):
    """Return purpose data keyed by real payment id, without writes or prompts.

    A verified gym merchant or an explicit spend->workout link supplies the
    transaction evidence. Merchant buckets, import categories, card tags,
    amount, repetition alone and a person link alone never establish a goal.
    An already stated goal connection is scoped to its durable evidence.
    """
    datetime.date.fromisoformat(today)
    active = goals(c, today)
    from ledger_goal_spend import confirmed, selections_by_goal
    owner_matches = confirmed(c, payments, today)
    import ledger_plan
    facts = ledger_plan.facts(c)
    bindings = [f for f in facts.values() if f.get('kind')=='goal_evidence'
                and _binding_live(c,f)]
    exclusions = selections_by_goal(c, active)
    people = collections.defaultdict(set)
    for sid, ref in c.execute("SELECT spend_id,entity_ref FROM spend_link "
                              "WHERE entity_kind='person' AND role='for'"):
        people[sid].add(ref)
    workouts = collections.defaultdict(list)
    for sid, wid, date in c.execute(
            "SELECT l.spend_id,w.id,w.date FROM spend_link l JOIN workouts w "
            "ON l.entity_ref=CAST(w.id AS TEXT) WHERE l.entity_kind='workout' AND l.role='for'"):
        workouts[sid].append(dict(kind='workout', id=wid, date=date))
    # A substring in a marketplace order is insufficient. Gym name evidence
    # must have an independently recorded merchant-kind fact (owner or lookup).
    merchants = {}
    for key, fact in ledger_plan.merchant_kinds(c).items():
        if (fact['kind'] == 'transport_health' and fact['source'] in ('owner', 'web')
                and GYM.search(fact['name']) and fact.get('evidence')):
            merchants[key] = fact
    result = {}
    for row in payments:
        proof = []
        eligible = (row['kind']=='expense' and row['class']=='spend' and row['amount']>0
                    and row['date']<=today and row.get('scope')!='business')
        if eligible:
            proof = [p for p in workouts[row['id']] if p['date'] <= today]
            fact = merchants.get(ledger_plan.merchant_key(row['name']))
            if fact:
                proof.append(dict(kind='merchant', name=fact['name'], source=fact['source'],
                                  evidence=fact['evidence']))
        by_goal = collections.defaultdict(list)
        for g in active:
            if (g['id'] not in owner_matches.get(row['id'], {}) and
                    ((g['starts_on'] and g['starts_on']>row['date'])
                     or (g['ends_on'] and g['ends_on']<row['date']))):
                continue
            exclusion = exclusions.get(g['id'], {})
            if any(a[0] == row['id'] and _anchor(c, row['id']) == a
                   for a in exclusion.get('excluded', [])):
                continue
            if g['id'] in owner_matches.get(row['id'], {}):
                by_goal[g['id']].append(owner_matches[row['id']][g['id']])
            if (proof and g['domain'] in ('health','training')
                    and (g['metric'] in EXERCISE_METRICS or GYM_GOAL.search(g['title']))):
                by_goal[g['id']].extend(proof)
            if not eligible:
                continue
            for fact in bindings:
                if fact['goal_id'] != g['id'] or fact['goal_ulid'] != g['ulid']:
                    continue
                via, ref = fact['via'], fact['ref']
                hit = (via=='transaction' and ref==str(row['id'])
                       or via=='person' and ref in people[row['id']]
                       or via=='repetition' and ref==str(row.get('stream_id')))
                if hit:
                    by_goal[g['id']].append(dict(kind=via,ref=ref,anchor_id=fact['anchor'][0],
                                                source='owner',evidence=fact['said']))
        matched = [g for g in active if by_goal[g['id']]]
        result[row['id']] = dict(status='known' if matched else 'unknown',
                                 labels=[g['title'] for g in matched],
                                 goals=matched,
                                 evidence=[dict(goal_id=g['id'],**p) for g in matched for p in by_goal[g['id']]])
    return result
