"""Merchant presentation facts, separate from receipt matching and classification.

No vocabulary or translation rules live here. The agent supplies a name once;
an owner's correction has priority. Existing explicit fixed aliases and stream
links connect receipt spellings without fuzzy-merging merchants.
"""
import datetime
import json

TOPIC = 'ledger_merchant_display_name'
SOURCES = ('agent_inferred', 'owner_said')
RESOLUTION = dict(
    verb='ledger-merchant-names-set',
    instructions='Before relaying the card, resolve display_name_missing merchants from your own '
                 'knowledge, batch-save their names, then render again. Do not invent a code mapping '
                 'or ask the owner about names you know. For an owner correction use owner_said '
                 'and preserve the exact owner words in said. Never replace an owner_said name '
                 'with agent inference. For split-payment cards, optionally supply settlement_label '
                 'with the activity/cost kind for the title; keep display_name as the merchant name.',
    format={'items': [{'name': 'raw receipt matching key', 'display_name': 'agent chosen name',
                        'source': 'agent_inferred'}]})


def key(raw):
    return ' '.join((raw or '').split()).casefold()


class Names:
    def __init__(self, c):
        self.facts = {r[0]: dict(json.loads(r[1]), source=r[2], recorded_at=r[3])
                      for r in c.execute('SELECT item,detail,source,recorded_at FROM ledger_preference '
                                         "WHERE topic=? AND status='active'", (TOPIC,))}
        self.links = {}
        # Reuse explicit owner names already taught through the plan. They
        # must not become "missing" merely because this storage topic is new.
        for item, detail, stamp in c.execute(
                "SELECT item,detail,recorded_at FROM ledger_preference WHERE topic='ledger_plan_fact' "
                "AND status='active' AND source='owner' AND item LIKE 'subscription:%'"):
            fact = json.loads(detail)
            if isinstance(fact.get('name'), str) and fact['name'].strip():
                identity = key(item[len('subscription:'):])
                if self.facts.get(identity, {}).get('source') != 'owner_said':
                    self.facts[identity] = dict(name=item[len('subscription:'):], display_name=fact['name'],
                                                source='owner_said', recorded_at=stamp)
        fixed = {str(fid): name for fid, name in c.execute('SELECT id,name FROM fixed_cost')}
        # The plan already groups provider receipt variants into subscription
        # identities. Reuse that identity rule, never infer aliases from labels.
        from ledger_plan import _subscription_key, _subscription_identities
        identities = _subscription_identities(c)
        raw_names = [r[0] for r in c.execute("SELECT DISTINCT name FROM spend_entry WHERE kind='expense' AND class='spend'")]
        for name in raw_names:
            self.link(name, _subscription_key(name, identities))
        from ledger_profile import _base
        for item, target in c.execute("SELECT item,note FROM ledger_preference WHERE topic='ledger_merchant_merge' AND status='active'"):
            if (item or '').startswith('key:') and target:
                for raw in raw_names:
                    if _base(raw) in (item[4:], target):
                        self.link(raw, target)
        for name, fid in c.execute('SELECT DISTINCT name,stream_id FROM spend_entry WHERE stream_id IS NOT NULL'):
            if str(fid) in fixed:
                self.link(name, fixed[str(fid)])
        for item, raw, source, stamp in c.execute("SELECT item,note,source,recorded_at FROM ledger_preference WHERE topic='ledger_fixed_alias' AND status='active'"):
            fid = (item or '').partition('/')[0].removeprefix('fixed:')
            if fid in fixed and raw:
                self.link(raw, fixed[fid])
                identity = key(fixed[fid])
                if source=='owner' and key(raw)!=identity and self.facts.get(identity, {}).get('source')!='owner_said':
                    self.facts[identity] = dict(name=fixed[fid], display_name=fixed[fid],
                                                source='owner_said', recorded_at=stamp)

    def link(self, left, right):
        a, b = key(left), key(right)
        self.links.setdefault(a, set()).add(b)
        self.links.setdefault(b, set()).add(a)

    def identities(self, raw):
        found, todo = set(), [key(raw)]
        while todo:
            item = todo.pop()
            if item not in found:
                found.add(item)
                todo.extend(self.links.get(item, set()) - found)
        return found

    def fact(self, raw):
        candidates = [self.facts[k] for k in self.identities(raw) if k in self.facts]
        def rank(fact):
            try:
                stamp = datetime.datetime.fromisoformat(fact['recorded_at'].replace('Z', '+00:00')).timestamp()
            except (ValueError, TypeError, AttributeError):
                stamp = 0
            return fact['source']=='owner_said', stamp
        return max(candidates, key=rank, default=None)

    def fields(self, raw, fallback=None):
        fact = self.fact(raw)
        return dict(**({'settlement_label': fact['settlement_label']} if fact and fact.get('settlement_label') else {}),
                    display_name=fact['display_name'] if fact else fallback or raw,
                    display_name_missing=fact is None,
                    display_name_source=fact['source'] if fact else None)


def set_names(c, data):
    items = data.get('items') if isinstance(data, dict) else None
    if not isinstance(items, list) or not items:
        raise ValueError('nonempty items batch required')
    # Validate the entire batch before the first write, including direct callers.
    for item in items:
        if not isinstance(item, dict) or any(not isinstance(item.get(k), str) or not item[k].strip()
                                             for k in ('name', 'display_name')):
            raise ValueError('raw name and display_name required')
        if item.get('source') not in SOURCES:
            raise ValueError('source must be agent_inferred or owner_said')
        if '\n' in item['display_name'] or '\r' in item['display_name']:
            raise ValueError('display_name must be one line')
        if 'settlement_label' in item and (not isinstance(item['settlement_label'], str)
                or not item['settlement_label'].strip() or any(ch in item['settlement_label'] for ch in '\r\n')):
            raise ValueError('settlement_label must be one nonempty line')
        if item['source']=='owner_said' and (not isinstance(item.get('said'), str) or not item['said'].strip()):
            raise ValueError('owner correction requires said')
    names, results = Names(c), []
    for item in items:
        old = names.fact(item['name'])
        if old and item['source']=='agent_inferred':
            if (old['source']=='agent_inferred' and not old.get('settlement_label')
                    and item.get('settlement_label')):
                item = dict(item, display_name=old['display_name'])
            else:
                results.append(dict(old, name=item['name'], saved=False))
                continue
        identity = key(item['name'])
        detail = {k: item[k].strip() for k in ('name', 'display_name')}
        if item.get('settlement_label') or old and old.get('settlement_label'):
            detail['settlement_label'] = (item.get('settlement_label') or old['settlement_label']).strip()
        if item.get('said'):
            detail['said'] = item['said']
        stamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
        c.execute("INSERT INTO ledger_preference(ulid,item,polarity,status,source,recorded_at,topic,detail) "
                  "VALUES(?,?,'negotiable','active',?,?,?,?) ON CONFLICT(ulid) DO UPDATE SET "
                  "detail=excluded.detail,status='active',source=excluded.source,recorded_at=excluded.recorded_at",
                  ('merchant-display:'+identity, identity, item['source'], stamp, TOPIC,
                   json.dumps(detail, ensure_ascii=False)))
        names.facts[identity] = dict(detail, source=item['source'], recorded_at=stamp)
        results.append(dict(saved=True, **names.facts[identity]))
    return dict(items=results)


def catalog(c):
    names = Names(c)
    raw = [r[0] for r in c.execute("SELECT DISTINCT name FROM spend_entry WHERE kind='expense' AND class='spend' ORDER BY name")]
    raw += [r[0] for r in c.execute("SELECT name FROM fixed_cost WHERE direction='expense'")]
    raw += [fact['name'] for fact in names.facts.values()]
    return dict(items=[dict(name=n, **names.fields(n)) for n in dict.fromkeys(raw)],
                display_name_resolution=RESOLUTION)


def present(c, payload):
    """Annotate structured ledger evidence; never rewrite matching fields."""
    names = Names(c)
    merchants = {key(r[0]) for r in c.execute(
        "SELECT DISTINCT name FROM spend_entry WHERE kind='expense' AND class='spend'")}
    merchants.update(key(r[0]) for r in c.execute("SELECT name FROM fixed_cost WHERE direction='expense'"))
    missing = {}

    def visit(value):
        if isinstance(value, list):
            for row in value:
                visit(row)
        elif isinstance(value, dict):
            raw = value.get('last', {}).get('name') if isinstance(value.get('last'), dict) else None
            raw = raw or value.get('name') or value.get('merchant')
            instrument = value.get('kind') in ('account', 'credit_card', 'corporate_card', 'debit_card', 'easy_pay', 'cash')
            if isinstance(raw, str) and key(raw) in merchants and not instrument:
                value.update(names.fields(raw, value.get('display_name')))
                if value['display_name_missing']:
                    missing[key(raw)] = dict(name=raw, display_name_missing=True)
            for field, row in list(value.items()):
                if field not in ('facts', 'display_name_missing', 'display_name_resolution'):
                    visit(row)

    visit(payload)
    if isinstance(payload, dict):
        payload['display_name_missing'] = list(missing.values())
        payload['display_name_resolution'] = RESOLUTION
    return payload
