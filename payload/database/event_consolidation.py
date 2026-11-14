"""Bounded, read-only planning for the owner-approved Nov 5 dinner.

This is an inspection verb, not an apply engine. Before-images are evidence,
not a sufficient rollback journal for a future migration after new writes.
"""
import hashlib
import json
import sqlite3

IDS = (1672, 1673, 1674)
ULIDS = ('01M6NDVDWRE78MX588E7FGRBQW', '01M6NEVD2P6EJBXEY9XYKPDQN6', '01M6NEVS4K1TCER8N3ZVN0V95Q')
TITLE = '가영이랑 저녁 · 연어구이와 와사비마요 레몬마요, 새우 바질 파스타'


def _rows(conn, sql, args=()):
    cur = conn.execute(sql, args)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur]


def inspect_dinner(conn, before_images=False):
    # A caller cannot accidentally turn inspection into a mutation. No commit,
    # config update, migration, transport, cache invalidation or callback runs.
    conn.execute('PRAGMA query_only=ON')
    before = {}
    before['events'] = _rows(conn, 'SELECT * FROM event WHERE id IN (1672,1673,1674) ORDER BY id')
    before['record_memberships'] = _rows(conn, 'SELECT re.*,r.ulid,r.source_table,r.source_id,r.ts FROM record_event re JOIN record r ON r.id=re.record_id WHERE re.event_id IN (1672,1673,1674) ORDER BY re.event_id,r.id')
    before['foreign_references'] = {}
    # Discover all declared event references, not only the known happy path.
    for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
        quoted = '"' + table.replace('"', '""') + '"'
        cols = sorted({r[3] for r in conn.execute('PRAGMA foreign_key_list(' + quoted + ')') if r[2] == 'event'})
        if cols:
            where = ' OR '.join('"' + col.replace('"', '""') + '" IN (1672,1673,1674)' for col in cols)
            before['foreign_references'][table] = _rows(conn, 'SELECT * FROM ' + quoted + ' WHERE ' + where + ' ORDER BY rowid')
    before['capture_keys'] = _rows(conn, 'SELECT * FROM experience_capture_key WHERE event_id IN (1672,1673,1674) ORDER BY capture_key')
    before['configs'] = _rows(conn, "SELECT * FROM config WHERE key LIKE 'activity.%' OR key LIKE 'cooking.%' OR key LIKE 'retro_walk.%2026-11-0%' OR key LIKE '%meal_time%' OR key LIKE '%satiety%' ORDER BY key")
    before['insights'] = _rows(conn, "SELECT * FROM insight WHERE json_valid(evidence) AND EXISTS (SELECT 1 FROM json_each(evidence) WHERE value IN (?,?,?)) ORDER BY id", ULIDS)
    before['blocks'] = _rows(conn, 'SELECT * FROM block WHERE id IN (35857,35858,35859,35860) ORDER BY id')
    before['block_attrs'] = _rows(conn, 'SELECT * FROM block_attr WHERE block_id IN (35857,35858,35859,35860) ORDER BY block_id,attr_token,source')
    before['dishes'] = _rows(conn, 'SELECT * FROM made_dish WHERE id IN (3,4) ORDER BY id')
    before['portions'] = _rows(conn, 'SELECT * FROM made_dish_meal WHERE dish_id IN (3,4) ORDER BY dish_id,block_id')
    before['artifact'] = _rows(conn, 'SELECT * FROM artifact WHERE id=31')
    before['spends'] = _rows(conn, "SELECT * FROM spend_entry WHERE date='2026-11-05' ORDER BY id")
    before['spend_links'] = _rows(conn, "SELECT * FROM spend_link WHERE spend_id IN (SELECT id FROM spend_entry WHERE date='2026-11-05') OR (entity_kind='meal' AND entity_ref IN ('35859','35860')) ORDER BY spend_id,entity_kind,entity_ref")
    blockers = []
    events = before['events']
    expected = [(1672, ULIDS[0], 1674, 'cooking'), (1673, ULIDS[1], 1674, 'cooking-meal'), (1674, ULIDS[2], None, 'cooking-occasion')]
    if [(e['id'],e['ulid'],e['parent_id'],e['kind_hint']) for e in events] != expected:
        blockers.append('event identity/containment/kind differs from approved snapshot')
    if any(e['owning_agent'] != 'ag' or e['occurred_on'] != '2026-11-05' or e['reaction'] for e in events):
        blockers.append('ownership/date/reaction requires review')
    refs = before['foreign_references']
    if refs.get('event_record_exclusion') or refs.get('event_child_exclusion') or before['insights']:
        blockers.append('curated exclusions or insight references require review')
    if [(r['event_id'],r['record_id'],r['source_table'],r['source_id']) for r in before['record_memberships']] != [
        (1672,22708,'made_dish',3),(1672,22709,'block',35858),(1672,22710,'made_dish',4),
        (1673,22711,'artifact',31),(1673,22712,'block',35859),(1673,22713,'block',35860)]:
        blockers.append('source memberships differ from approved six-record graph')
    if [(r['event_id'],r['timeblock_id']) for r in refs.get('event_timeblock',[])] != [(1673,35859),(1673,35860)]:
        blockers.append('meal timeblock links require review')
    if [(r['event_id'],r['person_id']) for r in refs.get('experience_person',[])] != [(1674,12)]:
        blockers.append('people links require review')
    if {e['id'] for e in refs.get('event',[])} - {1672,1673}:
        blockers.append('independent child experiences require review')
    known = {'event','record_event','event_timeblock','experience_person','event_record_exclusion','event_child_exclusion','experience_capture_key','event_alias'}
    if any(rows for table,rows in refs.items() if table not in known):
        blockers.append('additional event references require review')
    before_json = json.dumps(before, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    result = {
        'operation': 'dinner-2026-11-05', 'mode': 'dry-inspect', 'applied': False,
        'schema_version': conn.execute('PRAGMA user_version').fetchone()[0],
        'blockers': blockers,
        'before': {'event_ids': [e['id'] for e in events],
                   'events': [{key: e[key] for key in ('id','title','parent_id','occurred_at')} for e in events],
                   'record_memberships': [[r['event_id'],r['record_id']] for r in before['record_memberships']],
                   'capture_keys': len(before['capture_keys']),
                   'sha256': hashlib.sha256(before_json.encode()).hexdigest()},
        'planned_after': {
            'canonical_id': 1674, 'canonical_ulid': ULIDS[2], 'retired_ids': [1672,1673],
            'aliases': [[1672,ULIDS[0],1674],[1673,ULIDS[1],1674]],
            'title': TITLE, 'primary_facet': 'meal',
            'occurred_at': '2026-11-05T17:57:59+09:00', 'time_record_id': 22712,
            'clock_basis': 'owner-approved snapshot; meal settlement fallback, not measured eating time',
            'record_ids': [r['record_id'] for r in before['record_memberships']],
            'timeblock_ids': [35859,35860], 'person_ids': [12],
            'cooking_span': ['2026-11-05T17:12:28+09:00','2026-11-05T17:40:27+09:00'],
            'redirect': ['capture keys','session experience_id','pending meal-time/satiety references',
                         'shop references if present','cached event references','saved retro identities; preserve cursor/results/revisions/receipts'],
            'preserve': ['source rows and nutrition/portions/stock','abandoned plan 35857','ready cooking stages',
                         'spends 37700-37704 remain unrelated','retro progress and callback receipts'],
        },
        'apply_status': 'unavailable in slice 1; source/config revalidation and rollback journal required in slice 5',
    }
    if before_images:
        result['before_images'] = before
    return result


def cli_inspect(kit, argv):
    if argv not in (['dinner-2026-11-05'], ['dinner-2026-11-05', '--before-images']):
        kit._err('usage: experience-consolidation-inspect dinner-2026-11-05 [--before-images] (no apply)')
    conn = sqlite3.connect(kit._inspect_uri(kit.DB_PATH), uri=True)
    try:
        conn.execute('BEGIN')
        print(json.dumps(inspect_dinner(conn, '--before-images' in argv), ensure_ascii=False, indent=2))
    finally:
        conn.close()
