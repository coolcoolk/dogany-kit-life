"""Kit-owned resource intake. No channel readers or module match policies."""
import argparse
import csv
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import unicodedata

VERBS = ('source-set', 'source-list', 'intake-add', 'intake-list',
         'intake-resolve', 'match-rule-set', 'match-rule-list', 'import-csv',
         'ledger-archive-reset', 'intake-forget', 'intake-count', 'spend-block')
# Reads never take the shared database's write lock
# (ledger-archive-reset --dry-run is read too; see cli).
READ_VERBS = ('source-list', 'intake-list', 'match-rule-list', 'intake-count', 'spend-block')
SCOPES = ('personal', 'business', 'unknown')
FIELDS = {'amount', 'date', 'entity_id', 'merchant', 'unit', 'kind'}


def rows(c, sql, args=()):
    cur = c.execute(sql, args)
    return [dict(zip([d[0] for d in cur.description], r)) for r in cur]


def one(c, sql, args=()):
    result = rows(c, sql, args)
    if not result:
        raise ValueError('row not found')
    return result[0]


def norm(s):
    return ''.join(ch for ch in unicodedata.normalize('NFKC', s).casefold()
                   if ch.isalnum())


# Korean operator messages are escaped to keep executable source ASCII.
DUP_WINDOW_ERR = ('dup_window \uac00\ub4dc\uac00 \uc77c(day) \ub2e8\uc704\uc758 0 \uc774\uc0c1 '
                  '\uc815\uc218\uac00 \uc544\ub2d9\ub2c8\ub2e4 (value=%r, unit=%r). \uc911\ubcf5 '
                  '\ud310\uc815 \ucc3d\uc774 \uc5c6\uc5b4 intake \ub97c \uba48\ucd94\uc5c8\uc2b5\ub2c8\ub2e4. '
                  'guard-set dup_window <\uc77c\uc218> --unit day \ub85c \uace0\uce5c \ub4a4 '
                  '\ub2e4\uc2dc \uc2dc\ub3c4\ud558\uc138\uc694.')


def dup_window(c):
    """The one match window. A configured non-day value is never guessed at."""
    g = rows(c, "SELECT value,unit FROM ledger_guard WHERE guard_key='dup_window' AND scope='global'")
    if not g or g[0]['unit'] != 'day' or type(g[0]['value']) is not int or g[0]['value'] < 0:
        raise ValueError(DUP_WINDOW_ERR % ((g[0]['value'], g[0]['unit']) if g else (None, None)))
    return g[0]['value']


def signed(d):
    return d['amount'] if d['kind'] == 'income' else -d['amount']


def shift(day, n):
    return (datetime.date.fromisoformat(day) + datetime.timedelta(days=n)).isoformat()


def rule_validate(rule):
    if not isinstance(rule, dict) or set(rule) != {'version', 'exact', 'partial', 'date_window_guard', 'merchant_normalization'}:
        raise ValueError('invalid rule keys')
    if rule['version'] != 1 or rule['date_window_guard'] != 'dup_window' or rule['merchant_normalization'] != 'nfkc_casefold_alnum':
        raise ValueError('unsupported rule version or operators')
    for key in ('exact', 'partial'):
        val = rule[key]
        if not isinstance(val, list) or not val or any(not isinstance(x, str) or x not in FIELDS for x in val) or len(set(val)) != len(val):
            raise ValueError('rule fields must be a nonempty unique list')
    if not {'amount', 'date', 'entity_id', 'merchant', 'unit', 'kind'} <= set(rule['exact']):
        raise ValueError('exact rule must include all identity fields')
    if not {'amount', 'date'} <= set(rule['partial']):
        raise ValueError('partial rule must include amount and date')
    return rule


def source_set(c, a):
    if not a.key.strip() or not a.module.strip():
        raise ValueError('source and module must be nonempty')
    origin = one(c, 'SELECT writable FROM provenance WHERE token=?', (a.provenance,))
    if not origin['writable']:
        raise ValueError('provenance is not writable')
    old = rows(c, 'SELECT * FROM intake_source WHERE key=?', (a.key,))
    if old and (old[0]['resource'], old[0]['owning_module'], old[0]['value_origin']) != (a.resource, a.module, a.provenance):
        if c.execute('SELECT 1 FROM intake_item WHERE source=? LIMIT 1', (a.key,)).fetchone():
            raise ValueError('used source identity is immutable; register a new key')
    # Omitted --enabled keeps the current value on update; new sources start off.
    enabled = a.enabled if a.enabled is not None else old[0]['enabled'] if old else 0
    c.execute('INSERT INTO intake_source(key,resource,owning_module,enabled,value_origin) VALUES(?,?,?,?,?) '
              'ON CONFLICT(key) DO UPDATE SET resource=excluded.resource, owning_module=excluded.owning_module, '
              'enabled=excluded.enabled,value_origin=excluded.value_origin',
              (a.key, a.resource, a.module, enabled, a.provenance))
    return one(c, 'SELECT * FROM intake_source WHERE key=?', (a.key,))


def money_data(K, c, data):
    if not isinstance(data, dict):
        raise ValueError('data must be an object')
    d = dict(data)
    datetime.date.fromisoformat(d['date'])
    if type(d['amount']) is not int or d['amount'] <= 0:
        raise ValueError('amount must be positive integer KRW; use reversal for cancellation')
    if not isinstance(d['merchant'], str) or not norm(d['merchant']):
        raise ValueError('merchant required')
    d.setdefault('kind', 'expense')
    d.setdefault('unit', 'krw')
    d.setdefault('operation', 'payment')
    d.setdefault('scope', 'personal')
    if d['scope'] not in SCOPES:
        raise ValueError('invalid spend scope')
    if d['kind'] not in ('expense', 'income') or d['unit'] != 'krw' or d['operation'] not in ('payment', 'reversal', 'transfer'):
        raise ValueError('unsupported kind, unit or operation')
    ref = d.get('method', '')
    if not isinstance(ref, str):
        raise ValueError('method must be a name or alias')
    ref = d['method'] = ref.strip()
    candidates = set()
    for name, aliases in c.execute('SELECT name,aliases FROM pay_method'):
        # Existing pay_method aliases are free text; accept JSON lists or pipes/commas.
        try:
            alias_list = json.loads(aliases or '[]')
            if not isinstance(alias_list, list):
                raise ValueError()
        except (ValueError, TypeError):
            alias_list = re.split(r'[|,]', aliases or '')
        if ref in [name] + [str(x).strip() for x in alias_list]:
            candidates.add(name)
    if not candidates:
        candidates.add(ref)
    resolved = {(K._spend_entity_id(c, name), name) for name in candidates}
    ids = {eid for eid, name in resolved if eid is not None}
    if len(ids) != 1 or any(eid is None for eid, name in resolved):
        d['entity_id'] = None
    else:
        d['entity_id'] = next(iter(ids))
        d['method'] = sorted(name for eid, name in resolved)[0]
    return d


def matches(c, resource, d, source):
    """(exact, partial) candidate block ids. Exact is SAME DATE only; a match
    that needs the dup_window is partial and waits for review. Blocks already
    linked to this source are never exact (two real same-source payments stay
    apart) but still surface as partial, so a changed re-export cannot
    confirm a duplicate without --new."""
    rule_rows = rows(c, 'SELECT rule_json FROM intake_match_rule WHERE resource=? AND enabled=1', (resource,))
    if not rule_rows or d.get('operation') != 'payment':
        return [], []
    rule = rule_validate(json.loads(rule_rows[0]['rule_json']))
    win = dup_window(c)
    exact, partial = [], []
    # Every rule includes amount and date, so the indexed range lookup is exact.
    candidates = rows(c, "SELECT b.id,b.date,b.title AS merchant,d.amount,d.entity_id,d.unit,"
                        "EXISTS(SELECT 1 FROM intake_link l JOIN intake_item i ON i.id=l.item_id "
                        "WHERE l.block_id=b.id AND i.source=?) AS same_source "
                        "FROM block_delta d JOIN block b ON b.id=d.block_id "
                        "WHERE d.resource=? AND d.amount=? AND b.date BETWEEN ? AND ? "
                        "AND b.settled_outcome='done' "
                        "AND NOT EXISTS(SELECT 1 FROM intake_link l WHERE l.reversal_of=b.id) "
                        "AND NOT EXISTS(SELECT 1 FROM intake_link l WHERE l.block_id=b.id AND l.reversal_of IS NOT NULL)",
                      (source, resource, signed(d), shift(d['date'], -win), shift(d['date'], win)))
    for r in candidates:
        equal = {'amount': True, 'date': True, 'unit': r['unit'] == d['unit'],
                 'kind': (r['amount'] > 0) == (d['kind'] == 'income'),
                 'entity_id': d['entity_id'] is not None and r['entity_id'] == d['entity_id'],
                 'merchant': norm(r['merchant'] or '') == norm(d['merchant'])}
        if r['date'] == d['date'] and not r['same_source'] and all(equal[x] for x in rule['exact']):
            exact.append(r['id'])
        elif all(equal[x] for x in rule['partial']):
            partial.append(r['id'])
    return sorted(set(exact)), sorted(set(partial))


def refund_candidates(c, d):
    """Existing opposite deltas that may already record this cancellation
    (a refund entered by chat, say): same amount and entity, within dup_window
    of the cancellation, not already paired with another reversal."""
    win = dup_window(c)
    return [r['id'] for r in rows(
        c, "SELECT DISTINCT b.id FROM block_delta d JOIN block b ON b.id=d.block_id "
           "WHERE d.resource='money' AND d.amount=? AND b.date BETWEEN ? AND ? AND b.settled_outcome='done' "
           "AND (? IS NULL OR d.entity_id=?) "
           "AND NOT EXISTS(SELECT 1 FROM intake_link l WHERE l.reversal_of=b.id) "
           "AND NOT EXISTS(SELECT 1 FROM intake_link l JOIN intake_item i ON i.id=l.item_id "
           "WHERE l.block_id=b.id AND (l.reversal_of IS NOT NULL OR json_extract(i.data_json,'$.operation')='reversal')) "
           "ORDER BY b.id",
        (-signed(d), shift(d['date'], -win), shift(d['date'], win), d['entity_id'], d['entity_id']))]


def spend_id_of(ulid):
    m = re.fullmatch(r'x-spend-(\d+)', ulid or '')
    return int(m.group(1)) if m else None


def block_card(c, bid):
    """One candidate/landing block as the reviewer sees it (G4): the block,
    its spend row id when it is a ledger shadow, and the signed money delta."""
    r = rows(c, "SELECT b.id AS block_id,b.ulid,b.date,b.title,d.amount,d.entity_id FROM block b "
                "LEFT JOIN block_delta d ON d.block_id=b.id AND d.resource='money' WHERE b.id=? "
                "ORDER BY d.id LIMIT 1", (bid,))
    if not r:
        raise ValueError('block %s not found' % bid)
    r = r[0]
    r['spend_id'] = spend_id_of(r.pop('ulid'))
    return r


def item(c, iid):
    r = one(c, 'SELECT i.*,l.block_id,l.measure_id,l.reversal_of FROM intake_item i '
            'LEFT JOIN intake_link l ON l.item_id=i.id WHERE i.id=?', (iid,))
    r['candidate_blocks'] = []
    if r['state'] == 'pending' and r['reason'] in ('partial', 'ambiguous', 'held'):
        exact, partial = matches(c, 'money', json.loads(r['data_json']), r['source'])
        r['candidate_blocks'] = sorted(set(exact + partial))
    elif r['state'] == 'pending' and r['reason'] == 'possible_existing_refund':
        r['candidate_blocks'] = refund_candidates(c, json.loads(r['data_json']))
    # candidate_blocks stays the integer contract; candidates carries the
    # renderable objects {block_id, spend_id, date, title, amount, entity_id}.
    r['candidates'] = [block_card(c, b) for b in r['candidate_blocks']]
    return r


def attach(c, iid, bid, reversal=None):
    c.execute("INSERT INTO intake_link(item_id,target,block_id,reversal_of) VALUES(?,'block',?,?)", (iid, bid, reversal))
    c.execute("UPDATE intake_item SET state='confirmed',reason=? WHERE id=?", ('reversal' if reversal else 'attached', iid))


def add(K, c, source, ref, target, data, raw, hold=False):
    found = rows(c, 'SELECT id FROM intake_item WHERE source=? AND source_ref=?', (source, ref))
    if found:
        return {'status': 'duplicate', 'item': item(c, found[0]['id'])}
    s = one(c, 'SELECT * FROM intake_source WHERE key=?', (source,))
    if not s['enabled']:
        raise ValueError('source disabled')
    if target == 'measure':
        raise ValueError('state intake verbs are reserved for a later release')
    if s['resource'] != 'money':
        if not isinstance(data, dict):
            raise ValueError('data must be an object')
        d = data
    else:
        dup_window(c)
        d = money_data(K, c, data)
    iid = c.execute('INSERT INTO intake_item(source,source_ref,target,raw_payload,payload_hash,data_json) VALUES(?,?,?,?,?,?)',
                    (source, ref, target, raw, hashlib.sha256(raw.encode('utf-8')).hexdigest(), json.dumps(d, sort_keys=True))).lastrowid
    status = 'pending'
    if s['resource'] == 'money':
        exact, partial = matches(c, s['resource'], d, source)
        if hold and d['operation'] != 'reversal':
            # G1: a model-read item is always held -- no automatic exclusion,
            # attach or creation, whatever import_auto_accept says. A reversal
            # is already always pending and keeps its more specific reason.
            c.execute("UPDATE intake_item SET reason='held' WHERE id=?", (iid,))
        elif d['operation'] == 'transfer':
            c.execute("UPDATE intake_item SET state='excluded',reason='transfer' WHERE id=?", (iid,))
            status = 'excluded'
        elif d['operation'] == 'reversal':
            reason = 'possible_existing_refund' if refund_candidates(c, d) else 'unmatched'
            c.execute('UPDATE intake_item SET reason=? WHERE id=?', (reason, iid))
        elif len(exact) == 1:
            attach(c, iid, exact[0])
            status = 'attached'
        else:
            reason = 'ambiguous' if exact else 'partial' if partial else 'unmatched'
            guard = one(c, "SELECT value FROM ledger_guard WHERE guard_key='import_auto_accept' AND scope='global'")
            has_rule = c.execute('SELECT 1 FROM intake_match_rule WHERE resource=? AND enabled=1', ('money',)).fetchone()
            auto = guard['value'] == 1 and has_rule and reason == 'unmatched' and d['entity_id'] is not None
            # Auto-accept reuses the entry_amount_ceiling hold; a manual confirm is the release.
            ceiling = rows(c, "SELECT value,enforcement FROM ledger_guard WHERE guard_key='entry_amount_ceiling' AND scope='global'")
            if auto and ceiling and ceiling[0]['value'] is not None and ceiling[0]['enforcement'] in ('hold', 'block') \
                    and d['amount'] > ceiling[0]['value']:
                reason, auto = 'amount_hold', False
            c.execute('UPDATE intake_item SET reason=? WHERE id=?', (reason, iid))
            if auto:
                resolve(K, c, iid, 'confirm')
                status = 'confirmed'
    c.execute("UPDATE intake_source SET last_read_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE key=?", (source,))
    return {'status': status, 'item': item(c, iid)}


def spend_of_block(c, bid):
    """The spend_entry row behind a ledger shadow block, or None."""
    b = rows(c, 'SELECT ulid FROM block WHERE id=?', (bid,))
    sid = spend_id_of(b[0]['ulid']) if b else None
    if sid is None or not c.execute('SELECT 1 FROM spend_entry WHERE id=?', (sid,)).fetchone():
        return None
    return sid


def block_of_spend(c, sid):
    b = rows(c, 'SELECT id FROM block WHERE ulid=?', ('x-spend-%d' % int(sid),))
    if not b:
        raise ValueError('spend %s has no ledger block' % sid)
    return b[0]['id']


def resolve(K, c, iid, action, bid=None, reversal=None, new=False, scope=None):
    r = item(c, iid)
    if scope is not None and scope not in SCOPES:
        raise ValueError('invalid spend scope')
    if r['state'] != 'pending':
        if (action == 'exclude' and r['state'] == 'excluded') or (action == 'confirm' and r['state'] == 'confirmed' and (bid is None or bid == r['block_id']) and (reversal is None or reversal == r['reversal_of'])):
            if scope is not None:
                sid = spend_of_block(c, r['block_id'])
                if sid is None or c.execute('SELECT scope FROM spend_entry WHERE id=?', (sid,)).fetchone()[0] != scope:
                    raise ValueError('item already resolved; change scope with spend-upd')
            return {'status': 'unchanged', 'item': r}
        raise ValueError('item already resolved')
    if action == 'exclude':
        if bid or reversal or new or scope:
            raise ValueError('exclude accepts no landing options')
        c.execute("UPDATE intake_item SET state='excluded',reason='user' WHERE id=?", (iid,))
        return {'status': 'excluded', 'item': item(c, iid)}
    s = one(c, 'SELECT * FROM intake_source WHERE key=?', (r['source'],))
    if s['resource'] != 'money' or r['target'] != 'block':
        raise ValueError('only money delta confirmation is supported in this release')
    d = money_data(K, c, json.loads(r['data_json']))
    if d['entity_id'] is None:
        raise ValueError('payment method must resolve to exactly one existing active entity')
    if d['operation'] == 'transfer':
        raise ValueError('transfers are excluded')
    if bid and new:
        raise ValueError('--block-id and --new are mutually exclusive')
    if d['operation'] == 'reversal':
        if not reversal:
            raise ValueError('reversal requires --reversal-of BLOCK_ID')
        original = one(c, "SELECT d.* FROM block_delta d WHERE d.block_id=? AND d.resource='money'", (reversal,))
        if original['amount'] != signed(d) or original['entity_id'] != d['entity_id'] or original['unit'] != d['unit']:
            raise ValueError('reversal must match original amount, direction, entity and unit')
        if c.execute('SELECT 1 FROM intake_link WHERE reversal_of=? OR (block_id=? AND reversal_of IS NOT NULL)', (reversal, reversal)).fetchone():
            raise ValueError('block already reversed or is itself a reversal')
        if r['block_id'] is not None:
            # Its original was deleted; the refund block stands. Re-pair only.
            if bid or new or scope:
                raise ValueError('this reversal keeps its refund block; re-pair with --reversal-of only')
            c.execute('UPDATE intake_link SET reversal_of=? WHERE item_id=?', (reversal, iid))
            c.execute("UPDATE intake_item SET state='confirmed',reason='reversal' WHERE id=?", (iid,))
            return {'status': 'confirmed', 'item': item(c, iid)}
        if bid:
            check_block(c, bid, d, -signed(d), exact_amount=True)
        elif not new:
            existing = refund_candidates(c, d)
            if existing:
                raise ValueError('possible existing refund %s: pair it with --block-id or create with --new' % existing)
    elif reversal:
        raise ValueError('payment is not a reversal')
    elif bid:
        check_block(c, bid, d, signed(d))
    else:
        if not c.execute('SELECT 1 FROM intake_match_rule WHERE resource=? AND enabled=1', ('money',)).fetchone():
            raise ValueError('register an enabled money match rule before confirmation')
        exact, partial = matches(c, 'money', d, r['source'])
        if len(exact) == 1:
            bid = exact[0]
        elif (exact or partial) and not new:
            raise ValueError('review candidates with --block-id or explicitly --new')
    if bid is None:
        sid = K.spend_add(d['date'], -d['amount'] if reversal else d['amount'], d['merchant'],
                          method=d['method'], scope=scope or d['scope'], source='intake', kind=d['kind'], conn=c, commit=False)
        bid = c.execute('SELECT id FROM block WHERE ulid=?', ('x-spend-%d' % sid,)).fetchone()[0]
        c.execute('UPDATE block_delta SET value_origin=? WHERE block_id=?', (s['value_origin'], bid))
        # Like chat spends, no record('block') row: record rows accrue when
        # something references the block (record_upsert is find-first).
    elif scope is not None:
        # G5: the reviewer's scope lands on the existing spend row in the
        # same transaction (scope is not a match field; no intake trigger).
        sid = spend_of_block(c, bid)
        if sid is None:
            raise ValueError('--scope needs a ledger spend block; block %s is not one' % bid)
        c.execute('UPDATE spend_entry SET scope=? WHERE id=?', (scope, sid))
    attach(c, iid, bid, reversal)
    return {'status': 'confirmed', 'item': item(c, iid)}


def check_block(c, bid, d, want, exact_amount=False):
    """An explicit landing must carry the same entity/unit and direction, and
    must not already belong to a reversal pair."""
    linked = one(c, "SELECT d.entity_id,d.unit,d.amount FROM block_delta d WHERE d.block_id=? AND d.resource='money'", (bid,))
    if linked['entity_id'] != d['entity_id'] or linked['unit'] != d['unit']:
        raise ValueError('attach must use same resource, entity and unit')
    if (linked['amount'] > 0) != (want > 0) or (exact_amount and linked['amount'] != want):
        raise ValueError('attach target has the opposite direction' if (linked['amount'] > 0) != (want > 0)
                         else 'refund block must carry the exact opposite amount')
    if c.execute("SELECT 1 FROM intake_link l JOIN intake_item i ON i.id=l.item_id WHERE l.reversal_of=? "
                 "OR (l.block_id=? AND (l.reversal_of IS NOT NULL OR json_extract(i.data_json,'$.operation')='reversal'))",
                 (bid, bid)).fetchone():
        raise ValueError('block belongs to a reversal pair')


def durable_file(path, body):
    """Atomic local publication; a failed write never truncates an older dump."""
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=str(path.parent), delete=False) as f:
            tmp = f.name
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)


def archive(K, c, expected, dry_run=False):
    live = c.execute('SELECT COUNT(*) FROM spend_entry').fetchone()[0]
    if dry_run:
        # G2: what a reset would archive; read-only, no lock, no files.
        if expected is not None:
            raise ValueError('--dry-run takes no --confirm')
        return {'status': 'dry_run', 'count': live}
    if expected is None:
        raise ValueError('--confirm COUNT is required; live spend_entry count is %d' % live)
    if expected != live:
        raise ValueError('--confirm %d does not match live spend_entry count %d' % (expected, live))
    data = rows(c, 'SELECT * FROM spend_entry ORDER BY id')
    if not data:
        return {'status': 'empty', 'count': 0, 'archive': None}
    # Complete the durable archive before changing the database. A content
    # addressed file makes a retry after a crash safe and preserves old dumps.
    root = Path(K.DB_PATH).absolute().parent.parent / 'files' / '_archive'
    root.mkdir(parents=True, exist_ok=True)
    body = json.dumps(data, ensure_ascii=True, sort_keys=True, indent=2) + '\n'
    path = root / ('spend_entry-' + hashlib.sha256(body.encode()).hexdigest() + '.json')
    durable_file(path, body)
    fd = os.open(str(root), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    # A full SQL recovery snapshot retains the related graph, including
    # external record/event links, before deleting mirrored spend history.
    # iterdump omits PRAGMA user_version; stamp it so a restore is current.
    snapshot_body = '\n'.join(c.iterdump()) + '\nPRAGMA user_version = %d;\n' % (
        c.execute('PRAGMA user_version').fetchone()[0])
    snapshot = root / ('ledger-recovery-' + hashlib.sha256(snapshot_body.encode()).hexdigest() + '.sql')
    durable_file(snapshot, snapshot_body)
    fd = os.open(str(root), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    for row in data:
        sid = row['id']
        shadow = c.execute('SELECT id FROM block WHERE ulid=?', ('x-spend-%d' % sid,)).fetchone()
        if shadow:
            affected = [r[0] for r in c.execute('SELECT item_id FROM intake_link WHERE block_id=? OR reversal_of=?', (shadow[0], shadow[0]))]
            for iid in affected:
                c.execute('DELETE FROM intake_link WHERE item_id=?', (iid,))
                c.execute("UPDATE intake_item SET state='excluded',reason='archived' WHERE id=?", (iid,))
            K._spend_shadow_del(c, sid)
        c.execute("DELETE FROM record WHERE source_table='spend_entry' AND source_id=?", (sid,))
    # Deleting raises spend_id_floor (schema trigger): new ids never reuse these.
    c.execute('DELETE FROM spend_entry')
    return {'status': 'archived', 'count': len(data), 'archive': str(path), 'snapshot': str(snapshot)}


def forget(c, a):
    """Drop excluded, unlinked evidence so the same REF can be staged again
    (e.g. a CSV re-import after ledger-archive-reset)."""
    one(c, 'SELECT key FROM intake_source WHERE key=?', (a.source,))
    if (a.source_ref is None) == (not a.archived):
        raise ValueError('give exactly one of --source-ref REF or --archived')
    if a.source_ref is not None:
        found = rows(c, 'SELECT i.id,i.state,l.item_id AS linked FROM intake_item i LEFT JOIN intake_link l '
                        'ON l.item_id=i.id WHERE i.source=? AND i.source_ref=?', (a.source, a.source_ref))
        if not found:
            raise ValueError('row not found')
        if found[0]['state'] != 'excluded' or found[0]['linked'] is not None:
            raise ValueError('only excluded, unlinked items can be forgotten')
        ids = [found[0]['id']]
    else:
        ids = [r['id'] for r in rows(c, "SELECT id FROM intake_item i WHERE source=? AND state='excluded' "
                                        "AND reason='archived' AND NOT EXISTS(SELECT 1 FROM intake_link l "
                                        "WHERE l.item_id=i.id) ORDER BY id", (a.source,))]
    c.executemany('DELETE FROM intake_item WHERE id=?', [(i,) for i in ids])
    return {'forgotten': len(ids), 'ids': ids}


# Banksalad export headers are escaped to keep executable source ASCII.
CSV_HEADERS = {'date': '\ub0a0\uc9dc', 'kind': '\ud0c0\uc785', 'merchant': '\ub0b4\uc6a9',
               'amount': '\uae08\uc561', 'method': '\uacb0\uc81c\uc218\ub2e8'}
CSV_TIME = '\uc2dc\uac04'
CSV_KINDS = {'\uc9c0\ucd9c': 'expense', '\uc218\uc785': 'income', '\uc774\uccb4': 'transfer'}


def import_csv(K, c, a):
    parsed = []
    with open(a.file, encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        if not set(CSV_HEADERS.values()) <= set(reader.fieldnames or []):
            raise ValueError('expected Banksalad headers: ' + ', '.join(CSV_HEADERS.values()))
        width = len(reader.fieldnames)
        occurrence = {}
        for row in reader:
            found = width + len(row.get(None) or []) - sum(v is None for k, v in row.items() if k is not None)
            if found != width:
                # A malformed line is rejected on its own; it never stages.
                parsed.append({'status': 'rejected', 'row': reader.line_num,
                               'error': 'row %d: expected %d fields, found %d' % (reader.line_num, width, found)})
                continue
            raw = json.dumps(row, ensure_ascii=False, sort_keys=True)
            kind = CSV_KINDS.get(row[CSV_HEADERS['kind']].strip())
            if not kind:
                raise ValueError('unsupported CSV classification')
            currency = (row.get('\ud654\ud3d0') or 'KRW').strip()
            if currency not in ('KRW', '\uc6d0'):
                raise ValueError('CSV currency must be KRW')
            amount = int(row[CSV_HEADERS['amount']].replace(',', '').strip())
            d = {'date': row[CSV_HEADERS['date']].strip().replace('.', '-').replace('/', '-')[:10],
                 'amount': abs(amount), 'merchant': row[CSV_HEADERS['merchant']].strip(),
                 'method': row[CSV_HEADERS['method']].strip(), 'kind': 'income' if kind == 'income' else 'expense',
                 'operation': 'transfer' if kind == 'transfer' else 'reversal' if amount < 0 else 'payment'}
            # Identity is the normalized payment (spec A2-4 #5), not the raw
            # row: a re-export with an edited memo/category replays silently.
            delta = signed(d) * (-1 if d['operation'] == 'reversal' else 1)
            key = '|'.join((d['date'], (row.get(CSV_TIME) or '').strip(), str(delta), norm(d['merchant']),
                            unicodedata.normalize('NFKC', d['method'])))
            digest = hashlib.sha256(key.encode()).hexdigest()
            # Multiplicity is preserved; replay of the same export is silent.
            occurrence[digest] = occurrence.get(digest, 0) + 1
            parsed.append(('banksalad:' + digest + ':' + str(occurrence[digest]), d, raw))
    if one(c, 'SELECT resource FROM intake_source WHERE key=?', (a.source,))['resource'] != 'money':
        raise ValueError('CSV import requires money source')
    results = [x if isinstance(x, dict) else add(K, c, a.source, x[0], 'block', x[1], x[2]) for x in parsed]
    return {'count': len(results), 'results': results}


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def cli(K, verb, argv):
    p = Parser(prog='kit_life.sh ' + verb)
    if verb == 'source-set':
        p.add_argument('key')
        for flag in ('resource', 'module', 'provenance'):
            p.add_argument('--' + flag, required=True)
        p.add_argument('--enabled', type=int, choices=(0, 1))
    elif verb == 'source-list':
        p.add_argument('--resource')
    elif verb == 'match-rule-set':
        p.add_argument('resource'); p.add_argument('--module', required=True)
        p.add_argument('--rule', required=True); p.add_argument('--enabled', type=int, choices=(0, 1), default=1)
    elif verb == 'intake-add':
        p.add_argument('--source', required=True); p.add_argument('--source-ref', required=True)
        p.add_argument('--target', choices=('block', 'measure'), default='block')
        p.add_argument('--data', required=True); p.add_argument('--raw')
        p.add_argument('--hold', action='store_true')
    elif verb in ('intake-list', 'intake-count'):
        p.add_argument('--pending', action='store_true'); p.add_argument('--resource')
    elif verb == 'intake-resolve':
        p.add_argument('id', type=int); p.add_argument('action', choices=('confirm', 'exclude'))
        g = p.add_mutually_exclusive_group()
        g.add_argument('--block-id', type=int); g.add_argument('--spend-id', type=int)
        p.add_argument('--reversal-of', type=int)
        p.add_argument('--new', action='store_true'); p.add_argument('--scope', choices=SCOPES)
    elif verb == 'spend-block':
        g = p.add_mutually_exclusive_group(required=True)
        g.add_argument('--spend-id', type=int); g.add_argument('--block-id', type=int)
    elif verb == 'import-csv':
        p.add_argument('--source', required=True); p.add_argument('--file', required=True)
    elif verb == 'ledger-archive-reset':
        p.add_argument('--confirm', type=int, metavar='COUNT')
        p.add_argument('--dry-run', action='store_true')
    elif verb == 'intake-forget':
        p.add_argument('--source', required=True); p.add_argument('--source-ref')
        p.add_argument('--archived', action='store_true')
    c = None
    try:
        a = p.parse_args(argv)
        if not os.path.isfile(K.DB_PATH):
            raise ValueError("database not found")
        c = K.get_conn()
        if verb not in READ_VERBS and not getattr(a, 'dry_run', False):
            c.execute('BEGIN IMMEDIATE')
        if verb == 'source-set':
            result = source_set(c, a)
        elif verb == 'source-list':
            result = rows(c, 'SELECT * FROM intake_source WHERE (? IS NULL OR resource=?) ORDER BY key', (a.resource, a.resource))
        elif verb == 'match-rule-set':
            rule = rule_validate(json.loads(a.rule))
            c.execute('INSERT INTO intake_match_rule VALUES(?,?,?,?) ON CONFLICT(resource) DO UPDATE SET owning_module=excluded.owning_module,rule_json=excluded.rule_json,enabled=excluded.enabled', (a.resource, a.module, json.dumps(rule, sort_keys=True), a.enabled))
            result = one(c, 'SELECT * FROM intake_match_rule WHERE resource=?', (a.resource,))
        elif verb == 'match-rule-list':
            result = rows(c, 'SELECT * FROM intake_match_rule ORDER BY resource')
        elif verb == 'intake-add':
            result = add(K, c, a.source, a.source_ref, a.target, json.loads(a.data), a.raw if a.raw is not None else a.data, a.hold)
        elif verb == 'intake-list':
            dup_window(c)
            result = rows(c, 'SELECT i.*,l.block_id,l.measure_id,l.reversal_of FROM intake_item i JOIN intake_source s ON s.key=i.source LEFT JOIN intake_link l ON l.item_id=i.id WHERE (?=0 OR i.state=\'pending\') AND (? IS NULL OR s.resource=?) ORDER BY i.id', (a.pending, a.resource, a.resource))
            result = [item(c, r['id']) for r in result]
        elif verb == 'intake-count':
            result = {'count': c.execute('SELECT COUNT(*) FROM intake_item i JOIN intake_source s ON s.key=i.source '
                                         "WHERE (?=0 OR i.state='pending') AND (? IS NULL OR s.resource=?)",
                                         (a.pending, a.resource, a.resource)).fetchone()[0]}
        elif verb == 'spend-block':
            bid = block_of_spend(c, a.spend_id) if a.spend_id is not None else a.block_id
            result = block_card(c, bid)
            sid = spend_of_block(c, bid)
            if sid is None:
                raise ValueError('block %s is not a ledger spend block' % bid)
            result.update(rows(c, 'SELECT kind,scope,method,source FROM spend_entry WHERE id=?', (sid,))[0])
        elif verb == 'intake-resolve':
            bid = block_of_spend(c, a.spend_id) if a.spend_id is not None else a.block_id
            result = resolve(K, c, a.id, a.action, bid, a.reversal_of, a.new, a.scope)
        elif verb == 'import-csv':
            result = import_csv(K, c, a)
        elif verb == 'intake-forget':
            result = forget(c, a)
        else:
            result = archive(K, c, a.confirm, a.dry_run)
        if c.in_transaction:
            c.commit()
        print(json.dumps(result, ensure_ascii=True))
    except (ValueError, KeyError, TypeError, sqlite3.Error, OSError) as e:
        if c and c.in_transaction:
            c.rollback()
        print(json.dumps({'error': str(e)}, ensure_ascii=True))
        raise SystemExit(2)
    finally:
        if c:
            c.close()
