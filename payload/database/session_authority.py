"""K1 trusted-host seats. No public verb, transport, filesystem I/O or leases.

Keys and the DB path are fixed by installation, never supplied by a request.
Every decision re-reads durable state under the same SQLite writer lock.
"""
import hashlib
import hmac
import json
import sqlite3
from contextlib import contextmanager

from session_command import canonical, digest, require, row
from session_delivery import fields, integer, string, hash_value, strict_json, decode


def mac(key, value):
    return hmac.new(key, canonical(value).encode(), hashlib.sha256).hexdigest()


def signed(key, value):
    return dict(value, auth=dict(mac=mac(key, value)))


def authenticated(key, value):
    strict_json(value)
    fields(value.get('auth'), 'mac')
    sig = value['auth']['mac']
    require(type(sig) is str and hmac.compare_digest(
        sig, mac(key, {k:v for k,v in value.items() if k != 'auth'})),
        'AUTH_FAILED', 'invalid authority MAC')


def target_shape(target):
    fields(target, 'chat_id message_id')
    require(type(target['chat_id']) is int, 'INVALID_SCHEMA', 'invalid chat')
    integer(target['message_id'], 1)


def maintenance_key_shape(k):
    fields(k, 'chat_id epoch op stage_id')
    require(type(k['chat_id']) is int and type(k['epoch']) is int and k['epoch'] == 0,
            'INVALID_SCHEMA', 'invalid maintenance stage key')
    string(k['op']); string(k['stage_id'])


def receipt_target_shape(target):
    fields(target, 'message_id'); integer(target['message_id'], 1)


def retirement_frame(target):
    """Frozen markup-only frame preimage for the framework TP constructor."""
    return dict(stage_id='retire', frame='card', mode='retire', slot='card',
                allowed_outcomes=['ok','unchanged'], target=dict(message_id=target['message_id']),
                silent=True, notification_class='live_card')


def checkpoint_shape(q):
    fields(q, 'chat_id target retire_prefixes id')
    require(type(q['chat_id']) is int, 'INVALID_SCHEMA', 'invalid checkpoint chat')
    require(q['target'] in ('v2','legacy'), 'INVALID_SCHEMA', 'checkpoint target engine required')
    string(q['id'])
    require(type(q['retire_prefixes']) is list and
            all(type(p) is str and p for p in q['retire_prefixes']) and
            len(q['retire_prefixes']) == len(set(q['retire_prefixes'])),
            'INVALID_SCHEMA', 'invalid retired prefixes')
    strict_json(q)


@contextmanager
def transaction(conn):
    require(not conn.in_transaction, 'STORAGE_FAILED', 'authority needs its own transaction')
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        yield


def admit_invocation(conn, binding_id):
    """Existing trusted prepare_invocation calls this before issuing context."""
    require(conn.in_transaction, 'STORAGE_FAILED', 'admission requires writer transaction')
    old=row(conn,'SELECT * FROM session_live_commands WHERE binding_id=?',(binding_id,))
    require(old and old['invocation_started'] == 1 and old['invocation_generation'] > 0,
            'SURFACE_PENDING', 'invocation has not been prepared')
    p=json.loads(old['principal'])
    f=row(conn,'SELECT * FROM session_pack_fences WHERE pack=?',(p['publisher_id'],))
    require(f and f['closure'] is not None and f['active_engine']=='v2',
            'FENCE_STALE', 'pack admission is closed')
    a=dict(pack=f['pack'],activation_generation=f['active_generation'],pack_token=f['token'],
           binding_id=binding_id,invocation_generation=old['invocation_generation'])
    saved=row(conn,'SELECT * FROM session_invocation_admissions WHERE binding_id=? AND invocation_generation=?',
              (binding_id,old['invocation_generation']))
    require(not saved or (saved['attempt']==canonical(a) and saved['command_digest']==old['command_digest'] and
                         saved['principal']==old['principal']), 'FENCE_STALE', 'old attempt cannot be upgraded')
    if not saved:
        conn.execute('INSERT INTO session_invocation_admissions VALUES(?,?,?,?,?)',
                     (binding_id,old['invocation_generation'],canonical(a),old['command_digest'],old['principal']))
    return a


class Authority:
    """Provisioned authority, with a distinct recovery and publisher identity.

    The framework calls these Python seats through its trusted host binding.
    No caller-provided configuration or boolean grants transition authority.
    """
    def __init__(self, db_path, authority_id, key, publisher_key, coordinator_key,
                 *, installed_root, config_digest, capability_receipt_id, clock):
        string(authority_id)
        require(all(type(k) is bytes and len(k) >= 32 for k in
                    (key, publisher_key, coordinator_key)) and
                len({key,publisher_key,coordinator_key}) == 3,
                'AUTH_FAILED', 'distinct provisioned authority/coordinator keys required')
        self.db_path, self.authority_id, self.key = db_path, authority_id, key
        self.publisher_key, self.coordinator_key = publisher_key, coordinator_key
        self.installed_root, self.config_digest = installed_root, config_digest
        self.capability_receipt_id, self.clock = capability_receipt_id, clock

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute('PRAGMA foreign_keys=ON')
            require(conn.execute('PRAGMA user_version').fetchone()[0] >= 83,
                    'STORAGE_FAILED', 'K1 requires schema 83')
            yield conn
        finally:
            conn.close()

    def fence_writers(self, pack, engine, generation, q):
        string(pack); string(engine); integer(generation, 1); checkpoint_shape(q)
        with self.connection() as conn, transaction(conn):
            old = row(conn, 'SELECT * FROM session_pack_fences WHERE pack=?', (pack,))
            replay = row(conn, 'SELECT fence FROM session_pack_checkpoints WHERE pack=? AND checkpoint=?', (pack,canonical(q)))
            if replay:
                f = json.loads(replay['fence'])
                authenticated(self.key, f)
                require(f['authority_id'] == self.authority_id, 'AUTH_FAILED', 'authority identity differs')
                require((f['engine'], f['generation']) == (engine, generation), 'CONFLICT', 'checkpoint reused')
                return f
            require(not old or old['closure'] is not None, 'CONFLICT', 'unfinished checkpoint')
            if old:
                require(old['active_engine'] == engine and generation > old['active_generation'],
                        'FENCE_STALE', 'retiring activation differs')
            f = signed(self.key, dict(schema='trainer_activation_fence/1', authority_id=self.authority_id,
                pack=pack, engine=engine, generation=generation, checkpoint_digest=digest(q),
                token=old['token']+1 if old else 1))
            conn.execute('INSERT INTO session_pack_fences(pack,token,engine,generation,checkpoint,fence) '
                'VALUES(?,?,?,?,?,?) ON CONFLICT(pack) DO UPDATE SET token=excluded.token,engine=excluded.engine,'
                'generation=excluded.generation,checkpoint=excluded.checkpoint,fence=excluded.fence,'
                'active_engine=NULL,active_generation=NULL,closure=NULL',
                (pack,f['token'],engine,generation,canonical(q),canonical(f)))
            conn.execute('INSERT INTO session_pack_checkpoints(pack,token,checkpoint,fence) VALUES(?,?,?,?)',
                         (pack,f['token'],canonical(q),canonical(f)))
            return f

    def current(self, conn, pack, engine, f, q):
        fields(f, 'schema authority_id pack engine generation checkpoint_digest token auth')
        authenticated(self.key, f); checkpoint_shape(q)
        integer(f['token'], 1); integer(f['generation'], 1)
        require(f['schema'] == 'trainer_activation_fence/1' and f['authority_id'] == self.authority_id and
                f['pack'] == pack and f['engine'] == engine and f['checkpoint_digest'] == digest(q),
                'FENCE_STALE', 'fence binding differs')
        stored = row(conn, 'SELECT * FROM session_pack_fences WHERE pack=?', (pack,))
        require(stored and stored['fence'] == canonical(f) and stored['checkpoint'] == canonical(q) and
                stored['token'] == f['token'] and stored['engine'] == f['engine'] and stored['generation'] == f['generation'],
                'FENCE_STALE', 'obsolete pack token')
        return stored

    def verify_writer_fence(self, pack, engine, f, q):
        with self.connection() as conn, transaction(conn):
            self.current(conn, pack, engine, f, q)
        return True

    def check_attempt(self, conn, command, principal, installed, prior=None):
        """Called only inside Writer.apply, after principal authentication."""
        require(conn.in_transaction, 'STORAGE_FAILED', 'missing domain transaction')
        a = principal.get('attempt')
        fields(a, 'pack activation_generation pack_token binding_id invocation_generation')
        integer(a['activation_generation'], 1); integer(a['pack_token'], 1)
        integer(a['invocation_generation'])
        require(a['pack'] == installed['pack'], 'AUTH_FAILED', 'wrong installed pack')
        f = row(conn, 'SELECT * FROM session_pack_fences WHERE pack=?', (a['pack'],))
        require(f and f['closure'] is not None and f['active_engine'] == installed['engine'] and
                f['active_generation'] == a['activation_generation'] == installed['generation'] and
                f['token'] == a['pack_token'], 'FENCE_STALE', 'activation/token is closed or obsolete')
        self.validate_closure(conn, json.loads(f['closure']), json.loads(f['fence']), json.loads(f['checkpoint']))
        if a['binding_id'] is None:
            require(command['event_type'] == 'start' and a['invocation_generation'] == 0 and
                    (prior is not None or not row(conn, 'SELECT 1 FROM workouts WHERE id=?', (command['session_id'],))),
                    'AUTH_FAILED', 'only pre-birth start may be unbound')
            return
        string(a['binding_id']); integer(a['invocation_generation'], 1)
        admitted = row(conn, 'SELECT * FROM session_invocation_admissions WHERE binding_id=? AND invocation_generation=?',
                       (a['binding_id'],a['invocation_generation']))
        require(admitted and admitted['attempt'] == canonical(a) and admitted['command_digest'] == digest(command),
                'FENCE_STALE', 'signed attempt differs from durable admission')
        old = row(conn, 'SELECT * FROM session_live_commands WHERE binding_id=?', (a['binding_id'],))
        require(old and admitted['principal'] == old['principal'], 'AUTH_FAILED', 'admitted principal differs')
        p = json.loads(old['principal']) if old else {}
        require(old and old['state'] in (('bound','committed') if prior else ('bound',)) and old['invocation_started'] == 1 and
                old['invocation_generation'] == a['invocation_generation'] and
                old['session_id'] == command['session_id'] and old['command_digest'] == digest(command) and
                old['envelope_id'] == command['source_ref']['ref'] and
                p.get('publisher_id') == installed['pack'] and
                p.get('actor') == principal['actor'] and p.get('member_id') == principal['member_id'] and
                not row(conn, 'SELECT 1 FROM session_invocation_revocations WHERE binding_id=? '
                        'AND invocation_generation=?', (a['binding_id'],a['invocation_generation'])),
                'FENCE_STALE', 'invocation is not the admitted unrevoked generation')
        from session_delivery import stored_scope
        scope = json.loads(old['scope'])
        require(str(scope['chat_id']) == principal['chat_id'] and
                stored_scope(conn, dict(scope, session_id=command['session_id'])) == old['scope'],
                'AUTH_FAILED', 'invocation chat/scope mismatch')

    @contextmanager
    def fence_invocation(self, record):
        # The durable decision commits before the framework can sign/export.
        yield self.resolve_invocation(record)

    def recover_committed(self, record):
        with self.connection() as conn, transaction(conn):
            old = self.invocation(conn, record, allow_committed_start=True)
            c = json.loads(old['command_json'])
            prior = row(conn, 'SELECT * FROM session_commands WHERE session_id=? AND command_id=?',
                        (c['session_id'],c['command_id']))
            require(prior and prior['input_digest'] == record['command_digest'],
                    'SURFACE_PENDING', 'no exact committed receipt')
            receipt = json.loads(prior['receipt_json'])
            return dict(outcome='committed',receipt=receipt,outbox=self.committed_outbox(conn,receipt))

    def resolve_invocation(self, record):
        """Resolve an in-flight writer by acquiring its lock; never guess abort."""
        fields(record, 'S P binding_id command_digest invocation_generation')
        integer(record['invocation_generation'], 1); hash_value(record['command_digest'])
        with self.connection() as conn, transaction(conn):
            old = self.invocation(conn, record)
            c = json.loads(old['command_json'])
            prior = row(conn, 'SELECT * FROM session_commands WHERE session_id=? AND command_id=?',
                        (c['session_id'],c['command_id']))
            if prior:
                require(prior['input_digest'] == record['command_digest'], 'CONFLICT', 'committed command differs')
                return dict(outcome='committed', receipt=json.loads(prior['receipt_json']),
                    outbox=self.committed_outbox(conn, json.loads(prior['receipt_json'])))
            prior = row(conn, 'SELECT record FROM session_invocation_revocations WHERE binding_id=? '
                        'AND invocation_generation=?', (record['binding_id'],record['invocation_generation']))
            if prior:
                return json.loads(prior['record'])
            proof = signed(self.key, dict(record, authority_id=self.authority_id, outcome='aborted', quiescence=True))
            conn.execute('INSERT INTO session_invocation_revocations VALUES(?,?,?,?,?)',
                (record['binding_id'],record['invocation_generation'],record['command_digest'],
                 canonical(record['P']),canonical(proof)))
            return proof

    @staticmethod
    def committed_outbox(conn, receipt):
        outbox = receipt['outbox_id']
        return row(conn, 'SELECT * FROM session_outbox WHERE session_id=? AND revision=? AND frame_kind=?',
                   (outbox['session_id'],outbox['revision'],outbox['frame_kind'])) if outbox else None

    def invocation(self, conn, record, allow_committed_start=False):
        fields(record, 'S P binding_id command_digest invocation_generation')
        integer(record['invocation_generation'], 0 if allow_committed_start else 1); hash_value(record['command_digest'])
        from session_delivery import scope_shape
        scope_shape(record['S']); fields(record['P'], 'publisher_id actor member_id')
        for value in record['P'].values(): string(value)
        old = row(conn, 'SELECT * FROM session_live_commands WHERE binding_id=?', (record['binding_id'],))
        require(old and (old['invocation_started'] == 1 or
                (allow_committed_start and old['state'] == 'committed' and
                 json.loads(old['command_json'])['event_type'] == 'start' and record['invocation_generation'] == 0)) and
                old['invocation_generation'] == record['invocation_generation'] and
                old['command_digest'] == record['command_digest'] and old['principal'] == canonical(record['P']),
                'SURFACE_PENDING', 'unknown invocation')
        from session_delivery import stored_scope
        require(stored_scope(conn, record['S']) == old['scope'], 'CONFLICT', 'invocation scope differs')
        return old

    def transaction_aborted(self, record, original):
        with self.connection() as conn, transaction(conn):
            old = self.invocation(conn, record)
            c = json.loads(old['command_json'])
            require((original.get('command_digest') if 'command_digest' in original else digest(dict(original))) == old['command_digest'], 'CONFLICT', 'original differs')
            revoked = row(conn, 'SELECT record FROM session_invocation_revocations WHERE binding_id=? '
                          'AND invocation_generation=?', (record['binding_id'],record['invocation_generation']))
            return bool(revoked and not row(conn, 'SELECT 1 FROM session_commands WHERE session_id=? '
                                           'AND command_id=?', (c['session_id'],c['command_id'])))

    def validate_ack(self, conn, a, f, q):
        fields(a, 'schema plan receipt export recovery'); strict_json(a)
        require(a['schema'] == 'transition_retirement_ack/1', 'INVALID_SCHEMA', 'wrong acknowledgement schema')
        tp = a['plan']
        fields(tp, 'schema pack engine generation checkpoint_digest token target K payload_digest')
        require(tp['schema'] == 'transition_retirement_plan/1' and
                all(tp[k] == f[k] for k in ('pack','engine','generation','checkpoint_digest','token')),
                'FENCE_STALE', 'transition plan differs from current fence')
        self.current(conn, tp['pack'],tp['engine'],f,q)
        target_shape(tp['target']); hash_value(tp['payload_digest'])
        integer(tp['generation'], 1); integer(tp['token'], 1)
        maintenance_key_shape(tp['K'])
        k = dict(chat_id=tp['target']['chat_id'],epoch=0,
                 op='transition:'+q['id']+':'+str(tp['target']['message_id']),stage_id='retire')
        require(tp['K'] == k, 'CONFLICT', 'transition stage differs')
        r = a['receipt']
        fields(r, 'K D seq plan_digest lease_fence payload_digest target result message_id evidence_digest '
               'selection_digest retry_after next_attempt_at_ms silent auth')
        require(type(r['auth']) is str and hmac.compare_digest(r['auth'], mac(self.coordinator_key,
            {key:value for key,value in r.items() if key != 'auth'})), 'AUTH_FAILED', 'invalid coordinator receipt')
        integer(r['seq'], 1); maintenance_key_shape(r['K']); receipt_target_shape(r['target'])
        if r['message_id'] is not None: integer(r['message_id'], 1)
        require(r['D'] is None and r['silent'] is True and r['selection_digest'] is None and
                r['retry_after'] is None and r['next_attempt_at_ms'] is None and
                tp['payload_digest'] == digest(retirement_frame(tp['target'])),
                'AUTH_FAILED', 'receipt is not markup-only transition maintenance')
        require(r['K'] == k and r['plan_digest'] == digest(tp) and
                type(r['lease_fence']) is int and r['lease_fence'] == tp['token'] and
                r['payload_digest'] == tp['payload_digest'] and
                r['target'] == dict(message_id=tp['target']['message_id']) and
                (r['message_id'] == tp['target']['message_id'] if r['result'] != 'absent' else r['message_id'] is None) and
                r['result'] in ('ok','unchanged','absent'), 'SURFACE_PENDING', 'retirement not established')
        export = a['export']
        fields(export, 'kind value mac')
        require(export['kind'] == 'receipt' and type(export['mac']) is str and
                hmac.compare_digest(export['mac'], mac(self.publisher_key,
                    dict(kind='receipt',value=export['value']))), 'AUTH_FAILED', 'invalid receipt export')
        projected = {key:r[key] for key in ('K','seq','payload_digest','target','result','message_id','lease_fence','evidence_digest','retry_after','next_attempt_at_ms','selection_digest')}
        require(export['value'] == projected, 'CONFLICT', 'export differs from full receipt')
        if r['result'] == 'absent' or a['recovery'] is not None:
            value = a['recovery']; fields(value, 'record evidence')
            rec = value['record']
            fields(rec, 'authority_id K seq plan_digest payload_digest target resolution message_id evidence_digest quiescence issued_at_ms auth')
            authenticated(self.key, rec)
            integer(rec['seq'], 1); maintenance_key_shape(rec['K']); receipt_target_shape(rec['target'])
            if rec['message_id'] is not None: integer(rec['message_id'], 1)
            require(rec['authority_id'] == self.authority_id and
                    all(rec[key] == r[key] for key in ('K','seq','plan_digest','payload_digest','target','message_id')) and
                    rec['resolution'] == r['result'] and rec['quiescence'] is True and
                    type(value['evidence']) is dict and value['evidence'] and
                    rec['evidence_digest'] == digest(value['evidence']) and r['evidence_digest'] == digest(value),
                    'AUTH_FAILED', 'exact-target recovery is not authenticated')
            integer(rec['issued_at_ms']); require(rec['issued_at_ms'] <= self.clock(), 'CONFLICT', 'future recovery')
        else:
            require(r['evidence_digest'] is None, 'CONFLICT', 'unexpected recovery evidence')
        return tp

    def import_retirement(self, pack, target, a, f, q):
        if type(a) in (str,bytes): a = decode(a)
        with self.connection() as conn, transaction(conn):
            tp = self.validate_ack(conn,a,f,q)
            require(tp['pack'] == pack and tp['target'] == target, 'CONFLICT', 'retirement target differs')
            old = row(conn, 'SELECT * FROM session_transition_acks WHERE pack=? AND token=? AND target=?',
                      (pack,f['token'],canonical(target)))
            require(not old or old['acknowledgement'] == canonical(a), 'CONFLICT', 'acknowledgement changed')
            if not old:
                conn.execute('INSERT INTO session_transition_acks VALUES(?,?,?,?,?)',
                    (pack,f['token'],canonical(target),digest(a),canonical(a)))
            return dict(ack_digest=digest(a),duplicate=bool(old))

    def verify_retirement(self, pack, target, ack, q):
        with self.connection() as conn, transaction(conn):
            f = row(conn, 'SELECT fence FROM session_pack_fences WHERE pack=?', (pack,))
            require(f, 'FENCE_STALE', 'missing fence'); f = json.loads(f['fence'])
            return self.retirement(conn, pack,target,ack,f,q)

    def retirement(self, conn, pack, target, ack, f, q):
        target_shape(target)
        fields(ack, 'ack_digest', 'duplicate'); hash_value(ack['ack_digest'])
        require('duplicate' not in ack or type(ack['duplicate']) is bool, 'INVALID_SCHEMA', 'invalid duplicate flag')
        old = row(conn, 'SELECT * FROM session_transition_acks WHERE pack=? AND token=? AND target=?',
                  (pack,f['token'],canonical(target)))
        require(old and old['ack_digest'] == ack['ack_digest'], 'SURFACE_PENDING', 'missing stored retirement')
        self.validate_ack(conn,json.loads(old['acknowledgement']),f,q)
        return True

    def validate_closure(self, conn, closure, f, q):
        """Accept installer absence and all five closure facts only as bound MACs."""
        fields(closure, 'schema authority_id pack engine generation checkpoint_digest token '
               'installed_root previous_version replacement_version previous_manifest manifest_digest '
               'remaining_paths checked_at capability_receipt_id config_digest drain deferred callbacks '
               'healthkit inventory retirements auth')
        authenticated(self.key,closure)
        integer(closure['generation'], 1); integer(closure['token'], 1)
        require(closure['schema'] == 'trainer_installation_closure/1' and
                closure['authority_id'] == self.authority_id and
                all(closure[k] == f[k] for k in ('pack','engine','generation','checkpoint_digest','token')) and
                closure['installed_root'] == self.installed_root and closure['config_digest'] == self.config_digest and
                closure['capability_receipt_id'] == self.capability_receipt_id,
                'AUTH_FAILED', 'installation closure binding differs')
        self.current(conn,f['pack'],f['engine'],f,q)
        for key in ('previous_version','replacement_version'): string(closure[key])
        manifest = closure['previous_manifest']
        require(type(manifest) is list and manifest and all(type(p) is str and p for p in manifest) and
                manifest == sorted(set(manifest)) and closure['manifest_digest'] == digest(manifest) and
                closure['remaining_paths'] == [], 'SURFACE_PENDING', 'legacy resources remain or manifest missing')
        integer(closure['checked_at']); require(closure['checked_at'] <= self.clock(), 'CONFLICT', 'future installation')
        for name in ('drain','deferred','callbacks','healthkit'):
            fact = closure[name]; fields(fact,'complete evidence_digest')
            require(fact['complete'] is True, 'SURFACE_PENDING', 'incomplete '+name)
            hash_value(fact['evidence_digest'])
        inventory = closure['inventory']
        require(type(inventory) is list, 'INVALID_SCHEMA', 'inventory required')
        for target in inventory: target_shape(target)
        require(inventory == sorted(inventory,key=lambda t:(t['chat_id'],t['message_id'])) and
                len({canonical(t) for t in inventory}) == len(inventory), 'CONFLICT', 'inventory must be immutable and unique')
        require(type(closure['retirements']) is list and len(closure['retirements']) == len(inventory),
                'SURFACE_PENDING', 'retirement inventory incomplete')
        for target, ack in zip(inventory,closure['retirements']):
            self.retirement(conn,f['pack'],target,ack,f,q)
        return True

    def activate(self, pack, engine, generation, f, q, closure):
        string(engine); integer(generation,1)
        require(engine == q['target'] == 'v2' and generation == f['generation'],
                'CONFLICT', 'activation must select the checkpoint engine/generation')
        with self.connection() as conn, transaction(conn):
            current = self.current(conn,pack,f['engine'],f,q)
            self.validate_closure(conn,closure,f,q)
            if current['closure'] is not None:
                require(current['closure'] == canonical(closure) and current['active_engine'] == engine and
                        current['active_generation'] == generation, 'CONFLICT', 'activation already fixed')
                return True
            conn.execute('UPDATE session_pack_checkpoints SET closure=? WHERE pack=? AND token=?',
                         (canonical(closure),pack,f['token']))
            conn.execute('UPDATE session_pack_fences SET active_engine=?,active_generation=?,closure=? WHERE pack=?',
                         (engine,generation,canonical(closure),pack))
        return True
