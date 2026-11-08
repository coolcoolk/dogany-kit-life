"""Kit-owned live-session v2 transaction boundary (schema 78).

The installed health pack supplies strict WP0 wire contracts and pure planning /
view functions through Runtime. Runtime must never commit, send, call a model,
read mutable legacy flow files or perform I/O while apply holds the writer lock.
An authenticated service constructs Writer; an envelope's actor is NOT authority.
Legacy open-session import is deliberately unavailable without reconciliation.
"""
import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from datetime import datetime
from decimal import Decimal

SCHEMA_VERSION = 78  # Minimum schema; later additive migrations retain v2.


class CommandError(ValueError):
    def __init__(self, code, detail):
        self.code = code
        super().__init__(detail)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def audit_json(command):
    # Durable command evidence never retains a bearer handle. The original
    # envelope (including the handle) is bound by input_digest for dedupe.
    return canonical({key:value for key,value in command.items() if key!='action_token'})


def digest(value):
    return 'sha256:' + hashlib.sha256(canonical(value).encode()).hexdigest()


def grams(weight_kg):
    if weight_kg is None:
        return None
    value = Decimal(str(weight_kg)) * 1000
    if value != value.to_integral_value() or value < 0:
        raise CommandError('UNIT', 'load is not an exact nonnegative integer gram value')
    return int(value)


def row(conn, sql, args=()):
    cur = conn.execute(sql, args)
    value = cur.fetchone()
    return dict(zip([d[0] for d in cur.description], value)) if value else None


def rows(conn, sql, args=()):
    cur = conn.execute(sql, args)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, value)) for value in cur.fetchall()]


def update(conn, table, key, values):
    # Identifiers here are exclusively kit-owned dictionaries, never wire keys.
    conn.execute('UPDATE '+table+' SET '+','.join(k+'=?' for k in values)+' WHERE id=?',
                 (*values.values(), key))


def require(condition, code, detail):
    if not condition:
        raise CommandError(code, detail)


def negotiate(conn):
    require(conn.execute('PRAGMA user_version').fetchone()[0] >= SCHEMA_VERSION,
            'SCHEMA', 'live session v2 requires kit schema >= 78')
    require(not conn.in_transaction, 'TRANSACTION', 'writer requires its own transaction')
    conn.execute('PRAGMA foreign_keys=ON')
    require(conn.execute('PRAGMA foreign_keys').fetchone()[0] == 1, 'SCHEMA', 'foreign keys required')


def snapshot(conn, session_id):
    """One canonical state for receipts and the pack's pure view builder."""
    return {'workout': row(conn, 'SELECT * FROM workouts WHERE id=?', (session_id,)),
            'lifts': rows(conn, 'SELECT * FROM workout_lifts WHERE session_id=? ORDER BY order_index,id', (session_id,)),
            'sets': rows(conn, 'SELECT * FROM workout_sets WHERE session_id=? ORDER BY step_order,id', (session_id,)),
            'equipment': rows(conn, 'SELECT * FROM session_equipment_snapshots WHERE session_id=? ORDER BY id', (session_id,)),
            'evidence': rows(conn, 'SELECT * FROM session_evidence WHERE session_id=? ORDER BY id', (session_id,))}


def store_policy(conn, bundle):
    """Caller-owned seed transaction; immutable, typed and provenance-bearing."""
    from session_v2_contract.policy import PolicyBundle
    bundle = PolicyBundle(dict(bundle)).to_dict()
    values = {k: v for k, v in bundle.items() if k != 'rules'}
    old = row(conn, 'SELECT * FROM training_policy_bundles WHERE id=?', (bundle['id'],))
    if old:
        require(sorted(load_policy(conn, bundle['id'])['rules'],key=lambda r:(r['key'],r['scope'])) == sorted(bundle['rules'],key=lambda r:(r['key'],r['scope'])) and old == values, 'POLICY_CONFLICT', 'policy version already exists')
        return
    conn.execute('INSERT INTO training_policy_bundles('+','.join(values)+') VALUES('+','.join('?' for _ in values)+')', tuple(values.values()))
    for rule in bundle['rules']:
        conn.execute('INSERT INTO training_policy_rules VALUES(?,?,?,?,?,?,?,?)',
                     (rule['bundle_id'], rule['key'], rule['value_type'], canonical(rule['value']),
                      rule['scope'], rule['source_ref'], rule['source_date'], rule['approval_status']))


def load_policy(conn, bundle_id):
    bundle = row(conn, 'SELECT * FROM training_policy_bundles WHERE id=?', (bundle_id,))
    require(bundle is not None, 'POLICY', 'unknown pinned policy bundle')
    rules = rows(conn, 'SELECT * FROM training_policy_rules WHERE bundle_id=? ORDER BY key,scope', (bundle_id,))
    for rule in rules:
        rule['value'] = json.loads(rule.pop('value_json'))
    bundle['rules'] = rules
    return bundle


def effective_policy(conn, bundle_id):
    result = {}
    for scope in ('general', 'owner', 'session'):
        for rule in load_policy(conn, bundle_id)['rules']:
            if rule['scope'] == scope:
                require(rule['approval_status'] == 'approved', 'POLICY', 'unapproved rule: '+rule['key'])
                result[rule['key']] = rule['value']
    return result


class Runtime:
    """Pack integration seats. Missing fact/calculator bindings fail closed.

    authorize authenticates the *connection caller*, not the command's claimed
    actor. capability checks the real bridge receipt and legacy retirement.
    plan/resolve return kit insertion specs for unperformed rows only.
    recalculate returns {row_id: complete_target_tuple}; history is logged only.
    prepare/approve return the frozen PreparedNext/TrainerApproval contracts.
    Legacy compatibility (classification/rotation/end note) runs in this same tx.
    """
    def missing(self, name):
        raise CommandError('RUNTIME_UNAVAILABLE', 'missing installed pack binding: '+name)

    def authorize(self, command, state): self.missing('authenticated caller')
    def capability(self, conn, command): self.missing('native capability and retired legacy surfaces')
    def plan(self, conn, command, policy): self.missing('plan')
    def recalculate(self, state, command, policy): self.missing('pure prescription calculator')
    def validate_actual(self, state, actuals): self.missing('catalog units and equipment')
    def resolve(self, state, command, policy): self.missing(command['event_type']+' catalog facts')
    def approve(self, conn, command, state, policy): self.missing('reviewed trainer approval')
    def bind_approval(self, conn, command, state, policy): self.missing('resulting-revision approval binding')
    def legacy_start(self, conn, command): self.missing('rotation/classification/start mapping')
    def legacy_close(self, conn, command, state): self.missing('legacy close mapping')
    def rebind_rest(self, conn, command, before, after, approval): return None
    def import_resolution(self, conn, command, state, policy): self.missing('reviewed legacy reconciliation')
    def pin_context(self, conn, command): pass
    def delivery_context(self, command): return None
    def admission(self, conn, command, prior=None):
        # Controlled runtimes without installed authorities cannot bypass a
        # durable shared-DB fence created by another host/chat.
        if conn.execute('PRAGMA user_version').fetchone()[0] >= 83:
            require(not conn.execute('SELECT 1 FROM session_pack_fences LIMIT 1').fetchone(),
                    'FENCE_STALE', 'installed attempt authority required')


SKIP_START_FACTOR = 0.8  # owner 2026-10-15: first working set after a chat warm-up skip; tune here only
TARGET_FIELDS = frozenset(('target_reps', 'target_load_g', 'target_rir_min', 'target_rir_max',
                          'target_rest_ms', 'target_unit', 'target_count', 'target_hold_sec',
                          'equipment_snapshot_id'))


class Writer:
    def __init__(self, runtime, *, clock=None, token_key=None, local_timezone=None):
        self.runtime = runtime
        self.clock = clock or (lambda: time.time_ns() // 1_000_000)
        require(isinstance(token_key, bytes) and len(token_key) >= 32, 'CONFIG', 'durable service token key required')
        self.token_key = token_key
        require(local_timezone is not None, 'CONFIG', 'declared local timezone required for legacy date/text projections')
        self.local_timezone = local_timezone

    def action_token(self, conn, session_id):
        w = row(conn, 'SELECT * FROM workouts WHERE id=?', (session_id,))
        if not w or not w['action_token_hash']:
            return None
        meta = row(conn, 'SELECT * FROM session_input_state WHERE session_id=?', (session_id,))
        token = hmac.new(self.token_key, meta['token_nonce'].encode(), hashlib.sha256).hexdigest()[:32]
        require(hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), w['action_token_hash']), 'TOKEN', 'wrong service key')
        return token

    def apply(self, conn, command):
        from session_v2_contract.commands import CommandEnvelope, CommitReceipt
        from session_program import command as program_command
        command = program_command(dict(command))
        negotiate(conn)
        sid, cid, event, args = (command[k] for k in ('session_id','command_id','event_type','args'))
        conn.execute('BEGIN IMMEDIATE')
        now = self.clock()
        # A pack helper accidentally calling commit/rollback must not split the
        # boundary. The dedicated service connection has no other authorizer.
        conn.set_authorizer(lambda action,*unused: sqlite3.SQLITE_DENY if action in (sqlite3.SQLITE_TRANSACTION,sqlite3.SQLITE_SAVEPOINT) else sqlite3.SQLITE_OK)
        try:
            self.runtime.conn = conn
            state = snapshot(conn, sid)
            before = state
            prepared_rest = None
            if event == 'rest_end':
                saved = row(conn, "SELECT * FROM session_trainer_approvals WHERE session_id=? AND base_revision=? AND status='prepared'", (sid, command['expected_revision']))
                if saved:
                    prepared_rest = dict(saved)
                    prepared_rest['decision'] = json.loads(prepared_rest.pop('decision_json'))
                    prepared_rest['cue_ast'] = json.loads(prepared_rest['cue_ast'])
            self.runtime.authorize(command, state)
            prior = row(conn, 'SELECT * FROM session_commands WHERE session_id=? AND command_id=?', (sid,cid))
            self.runtime.admission(conn, command, prior)
            if prior:
                require(prior['input_digest'] == digest(command), 'IDEMPOTENCY_CONFLICT', 'command ID reused with different input')
                conn.set_authorizer(lambda *unused: sqlite3.SQLITE_OK)
                conn.rollback()
                return CommitReceipt.from_json(prior['receipt_json'])
            delivery_context = self.runtime.delivery_context(command)
            if delivery_context:
                from session_delivery import check_command_ready
                check_command_ready(conn, command, *delivery_context)
            if event == 'start':
                importing = state['workout'] is not None and state['workout']['session_version']==1
                require((state['workout'] is None or importing) and command['expected_revision'] == 0, 'MOVED', 'session ID already used')
                self.runtime.capability(conn, command)
                policy = effective_policy(conn, args['policy_bundle_id'])
                require(load_policy(conn,args['policy_bundle_id'])['owner_id'] == args['member_id'], 'POLICY', 'policy belongs to another member')
                source = command['source_ref']
                require(source.get('member_id') == args['member_id'] and source.get('chat_id'), 'AUTH', 'start requires authenticated member/chat')
                from kit_life import _health_agent_resolve_full
                owner=_health_agent_resolve_full(conn)[0]
                if importing:
                    from session_reconcile import preflight, materialize, link_history
                    resolution = preflight(conn, command, state, self.runtime.import_resolution(conn,command,state,policy))
                    linked = link_history(conn,resolution)
                    update(conn,'workouts',sid,dict(member_id=args['member_id'],session_version=2,revision=0,
                           phase='plan',policy_bundle_id=args['policy_bundle_id'],catalog_version=args['catalog_version']))
                else:
                    conn.execute("INSERT INTO workouts(id,date,owning_agent,type,member_id,session_version,revision,phase,policy_bundle_id,catalog_version,created_at) VALUES(?,?,?,?,?,2,0,'plan',?,?,?)",
                                 (sid, datetime.fromtimestamp(now/1000,self.local_timezone).date().isoformat(), owner, args['mode'], args['member_id'], args['policy_bundle_id'], args['catalog_version'],datetime.fromtimestamp(now/1000,self.local_timezone).strftime('%Y-%m-%d %H:%M:%S')))
                conn.execute('INSERT INTO session_input_state(session_id,member_id,chat_id,started_at,mode,split_id) VALUES(?,?,?,?,?,?)',
                             (sid,args['member_id'],source['chat_id'],resolution['started_at'] if importing else now,args['mode'],args.get('split_id')))
                self.runtime.pin_context(conn,command)
                if importing:
                    materialize(self,conn,command,resolution,linked)
                else:
                    self.runtime.legacy_start(conn,command)
                    self.insert_plan(conn, sid, self.runtime.plan(conn,command,policy), 1, cid)
                    self.advance(conn,sid)
                affected = [s['id'] for s in snapshot(conn,sid)['sets']]
            else:
                w = state['workout']
                require(w and w['session_version'] == 2, 'SESSION', 'not a canonical v2 session')
                require(w['revision'] == command['expected_revision'], 'MOVED', 'session moved')
                if w['phase'] == 'closed' and event == 'stop_session':
                    previous = row(conn,"SELECT c.receipt_json FROM session_commands c JOIN session_outbox o "
                                   "ON o.session_id=c.session_id AND o.revision=c.committed_revision "
                                   "WHERE c.session_id=? AND o.frame_kind='closeout' ORDER BY c.committed_revision LIMIT 1",(sid,))
                    require(previous, 'CLOSED', 'closed without a stop receipt')
                    original = CommitReceipt.from_json(previous['receipt_json'])
                    if delivery_context:
                        from session_delivery import bind_commit
                        bind_commit(conn, command, original, *delivery_context)
                    conn.set_authorizer(lambda *unused: sqlite3.SQLITE_OK)
                    if delivery_context:
                        conn.commit()
                    else:
                        conn.rollback()
                    return original
                require(w['phase'] != 'closed' or event in ('correct','undo','measure_kcal','reclassify'), 'CLOSED', 'session is closed')
                self.validate_source(conn, command, w)
                policy = effective_policy(conn,w['policy_bundle_id'])
                affected = self.intent(conn,command,state,policy,now)
                if w['phase']=='closed':
                    self.runtime.legacy_close(conn,command,snapshot(conn,sid))
            w = row(conn, 'SELECT * FROM workouts WHERE id=?', (sid,))
            revision = command['expected_revision']+1
            token_hash = None
            nonce = secrets.token_hex(16)
            if w['phase'] != 'closed' and w['active_set_id'] is not None:
                token = hmac.new(self.token_key,nonce.encode(),hashlib.sha256).hexdigest()[:32]
                token_hash = hashlib.sha256(token.encode()).hexdigest()
            update(conn,'workouts',sid,{'revision':revision,'action_token_hash':token_hash})
            conn.execute('UPDATE session_input_state SET token_nonce=? WHERE session_id=?',(nonce,sid))
            conn.execute("UPDATE session_trainer_approvals SET status='invalidated' WHERE session_id=? AND status IN ('prepared','bound')",(sid,))
            conn.execute("UPDATE session_outbox SET state='superseded' WHERE session_id=? AND state IN ('waiting_trainer','ready')",(sid,))
            state = snapshot(conn,sid)
            view_digest = digest(state)
            kind = 'closeout' if state['workout']['phase'] == 'closed' else 'step'
            delivery_state = 'ready' if kind == 'closeout' else 'waiting_trainer'
            approval_id = None
            if event == 'approve_step' or prepared_rest:
                from session_v2_contract.transport import TrainerApproval
                result = (self.runtime.bind_approval(conn,command,state,policy) if event=='approve_step' else
                          self.runtime.rebind_rest(conn,command,before,state,prepared_rest))
                if result is not None:
                    bound = TrainerApproval(dict(result)).to_dict()
                    expected_status = 'prepared' if state['workout']['phase']=='rest' else 'bound'
                    require(bound['session_id']==sid and bound['base_revision']==revision and bound['view_digest']==view_digest and bound['status']==expected_status and bound['policy_bundle_id']==state['workout']['policy_bundle_id'] and bound['catalog_version']==state['workout']['catalog_version'] and bound['expires_epoch']==state['workout']['session_epoch'], 'APPROVAL', 'approval must bind the resulting committed revision')
                    approval_id=bound['id']
                    conn.execute('INSERT INTO session_trainer_approvals VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                        (bound['id'],sid,revision,view_digest,canonical(bound['decision']),canonical(bound['cue_ast']),bound['dependency_digest'],bound['policy_bundle_id'],bound['catalog_version'],bound['expires_epoch'],expected_status))
                    delivery_state='ready' if expected_status=='bound' else 'waiting_trainer'
            receipt = CommitReceipt({'schema':2,'session_id':sid,'command_id':cid,'receipt_id':f'{sid}:{revision}:{cid}',
                      'revision':revision,'view_digest':view_digest,'affected_row_ids':sorted(set(affected)),
                      'outbox_id':{'session_id':sid,'revision':revision,'frame_kind':kind},
                      'delivery_state':delivery_state,'commit_state':'committed'})
            conn.execute('INSERT INTO session_commands VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                         (sid,cid,2,command['actor'],command['expected_revision'],digest(command),event,audit_json(command),
                          revision,receipt.to_json(),now,command['source_ref'].get('message_id'),
                          row(conn,'SELECT command_id FROM session_commands WHERE session_id=? AND committed_revision=?',(sid,args['recorded_revision']))['command_id'] if event in ('undo','correct') else args.get('command_id') if event == 'undo_command' else None))
            conn.execute("INSERT INTO session_outbox(session_id,revision,frame_kind,session_epoch,rest_epoch,view_digest,not_before_at,trainer_approval_id,state,stage) VALUES(?,?,?,?,?,?,?,?,?,'retire_markup')",
                         (sid,revision,kind,state['workout']['session_epoch'],state['workout']['rest_epoch'],view_digest,now,approval_id,delivery_state))
            if delivery_context:
                from session_delivery import bind_commit
                bind_commit(conn, command, receipt, *delivery_context)
            require(conn.in_transaction, 'TRANSACTION', 'pack binding committed outside the boundary')
            conn.set_authorizer(lambda *unused: sqlite3.SQLITE_OK)
            conn.commit()
            return receipt
        except BaseException:
            conn.set_authorizer(lambda *unused: sqlite3.SQLITE_OK)
            conn.rollback()
            raise

    def validate_source(self,conn,command,w):
        source = command['source_ref']
        if command['actor'] == 'member':
            meta = row(conn,'SELECT * FROM session_input_state WHERE session_id=?',(w['id'],))
            require(source.get('member_id') == meta['member_id'] and source.get('chat_id') == meta['chat_id'], 'AUTH', 'member/chat mismatch')
        if source['kind'] == 'callback':
            token = command.get('action_token','')
            require(w['action_token_hash'] and hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), w['action_token_hash']), 'MOVED', 'stale action token')
            surface = row(conn,"SELECT * FROM session_surfaces WHERE session_id=? AND surface_kind='trainer'",(w['id'],))
            require(surface and surface['message_id'] == source['message_id'] and surface['chat_id'] == source['chat_id'] and surface['markup_state']=='active' and surface['published_revision']==w['revision'], 'MOVED','callback is not on the active verified surface')

    def insert_plan(self,conn,sid,plan,revision,cid,existing_lift_ids=None):
        """Insert pack-calculated rows once; stable seq allocation includes voids."""
        w = row(conn,'SELECT * FROM workouts WHERE id=?',(sid,))
        allocated = []
        for index,lift in enumerate(plan):
            fields = dict(lift['lift'])
            allowed={'exercise_id','exercise_name','slot_num','order_index','variant_id','machine_ref','implement_resolution_id'}
            require(set(fields)<=allowed,'PLAN','unknown lift fields')
            fields.update(session_id=sid,status='open')
            if existing_lift_ids is None:
                cur=conn.execute('INSERT INTO workout_lifts('+','.join(fields)+') VALUES('+','.join('?' for _ in fields)+')',tuple(fields.values()))
                lid=cur.lastrowid
            else:
                lid=existing_lift_ids[index]
                require(row(conn,'SELECT id FROM workout_lifts WHERE id=? AND session_id=?',(lid,sid)), 'IMPORT', 'imported lift does not belong to session')
            set_ids = []
            for prescription in lift['sets']:
                fields=dict(prescription)
                require(set(fields)<=TARGET_FIELDS|{'step_order','set_kind','drill_code'},'PLAN','unknown prescription fields')
                require(TARGET_FIELDS <= set(fields),'PLAN','incomplete prescription tuple')
                seq=conn.execute('SELECT COALESCE(MAX(set_seq),0)+1 FROM workout_sets WHERE session_id=? AND exercise_name=?',(sid,lift['lift']['exercise_name'])).fetchone()[0]
                fields.update(session_id=sid,lift_id=lid,exercise_name=lift['lift']['exercise_name'],slot_num=lift['lift'].get('slot_num'),set_seq=max(1,seq),
                              machine_ref=lift['lift'].get('machine_ref'),row_status='planned',actual_source=None,
                              prescribed_revision=revision,policy_bundle_id=w['policy_bundle_id'],basis_event_id=cid,recorded_at=datetime.fromtimestamp(self.clock()/1000,self.local_timezone).strftime('%Y-%m-%d %H:%M:%S'))
                cur = conn.execute('INSERT INTO workout_sets('+','.join(fields)+') VALUES('+','.join('?' for _ in fields)+')',tuple(fields.values()))
                set_ids.append(cur.lastrowid)
            allocated.append(dict(lift_id=lid, set_ids=set_ids))
        callback = getattr(self.runtime, 'plan_inserted', None)
        if callback:
            callback(conn, sid, plan, allocated, revision, cid)

    def cancel_rest(self,conn,sid):
        conn.execute('UPDATE workouts SET rest_epoch=rest_epoch+1,rest_started_at=NULL,rest_deadline_at=NULL,rest_duration_ms=NULL WHERE id=?',(sid,))

    def advance(self,conn,sid):
        callback = getattr(self.runtime, 'advance_program', None)
        if callback:
            return callback(conn, sid)
        w=row(conn,'SELECT * FROM workouts WHERE id=?',(sid,))
        lift=row(conn,"SELECT * FROM workout_lifts WHERE session_id=? AND status='open' ORDER BY CASE WHEN id=? THEN 0 ELSE 1 END,order_index LIMIT 1",(sid,w['active_lift_id']))
        nxt=row(conn,"SELECT * FROM workout_sets WHERE session_id=? AND row_status='planned' AND (lift_id=? OR lift_id IS NULL) ORDER BY CASE WHEN lift_id IS NULL THEN 0 ELSE 1 END,step_order,id LIMIT 1",(sid,lift['id'] if lift else None))
        update(conn,'workouts',sid,{'active_lift_id':lift['id'] if lift else None,'active_set_id':nxt['id'] if nxt else None,
               'phase':('prep' if nxt['set_kind']=='prep' else 'post' if nxt['set_kind']=='post' else 'ready') if nxt else 'lift_done',
               'input_stage':'reps' if nxt else None,'action_kind':'set' if nxt else None})

    def skip_warmup(self,conn,sid,state):
        w=state['workout']; lid=w['active_lift_id']
        require(lid,'LIFT','no active lift')
        require(w['input_stage']!='rir','STAGE','finish or undo pending actual input first')
        mine=[r for r in state['sets'] if r['lift_id']==lid]
        require(not any(r['set_kind']=='working' and r['row_status']=='logged' for r in mine),'STAGE','working set already performed')
        ramps=[r for r in mine if r['set_kind']=='ramp' and r['row_status']=='planned']
        work=next((r for r in mine if r['set_kind']=='working' and r['row_status']=='planned'),None)
        require(ramps and work,'STAGE','no planned warm-up to skip')
        lift=next(l for l in state['lifts'] if l['id']==lid)
        grid=[n for n in self.runtime.grid(state,lift) if n<=int(work['target_load_g']*SKIP_START_FACTOR)]
        require(grid,'EQUIPMENT','no feasible load at the reduced first-set start')
        update(conn,'workouts',sid,{'active_set_id':None})  # the active row may be a ramp about to be omitted
        for r in ramps:
            update(conn,'workout_sets',r['id'],{'row_status':'omitted','reps':None,'weight_kg':None,'actual_source':None,'actual_unit':None})
        update(conn,'workout_sets',work['id'],{'target_load_g':max(grid)})
        self.cancel_rest(conn,sid)
        self.advance(conn,sid)
        return [r['id'] for r in ramps]+[work['id']]

    def close_lift(self,conn,sid,lid,reason,revision):
        lift=row(conn,'SELECT * FROM workout_lifts WHERE id=? AND session_id=?',(lid,sid))
        require(lift and lift['status']=='open','MOVED','lift is not open')
        count=conn.execute("SELECT count(*) FROM workout_sets WHERE lift_id=? AND row_status='logged'",(lid,)).fetchone()[0]
        # Clear pointers before closing, so every intermediate row obeys checks.
        conn.execute('UPDATE workouts SET active_set_id=NULL,active_lift_id=NULL,input_stage=NULL WHERE id=? AND active_lift_id=?',(sid,lid))
        update(conn,'workout_lifts',lid,{'status':'closed' if count else 'skipped','closed_reason':reason,'closed_revision':revision})
        affected=[r['id'] for r in rows(conn,"SELECT id FROM workout_sets WHERE lift_id=? AND row_status='planned'",(lid,))]
        conn.execute("UPDATE workout_sets SET row_status='omitted',reps=NULL,weight_kg=NULL,actual_source=NULL,actual_unit=NULL WHERE lift_id=? AND row_status='planned'",(lid,))
        self.cancel_rest(conn,sid)
        return affected

    def recalculate(self,conn,command,policy):
        state=snapshot(conn,command['session_id'])
        changes=self.runtime.recalculate(state,command,policy)
        state=snapshot(conn,command['session_id'])  # topology may allocate under the same lock
        affected=list(getattr(changes, 'topology_affected', ()))
        require(set(affected) <= {s['id'] for s in state['sets']}, 'PROGRAM', 'topology receipt contains a foreign row')
        scoped=command['event_type'] in ('change_load','change_reps','select_load')
        target_id = (self.runtime.target_scope(state,command) if scoped and hasattr(self.runtime,'target_scope')
                     else state['workout']['active_set_id'])
        scoped_row = next((s for s in state['sets'] if s['id']==target_id), None)
        for rid,rx in changes.items():
            if scoped:
                target=next((s for s in state['sets'] if s['id']==rid),None)
                require(target and scoped_row and target['lift_id']==scoped_row['lift_id'],'SCOPE','calculator changed another lift')
                if command['args'].get('scope')=='current_only':
                    require(rid==target_id,'SCOPE','current_only changed a future row')
            r=next((r for r in state['sets'] if r['id']==rid),None)
            require(r and r['row_status']=='planned','IMMUTABLE','calculator may only change planned rows')
            require(set(rx)==TARGET_FIELDS or rx=={'row_status':'omitted'},'POLICY','calculator must return the entire target tuple or omit')
            values=dict(rx)
            values.update(prescribed_revision=command['expected_revision']+1,basis_event_id=command['command_id'])
            if rx.get('row_status')=='omitted' and rid==state['workout']['active_set_id']:
                update(conn,'workouts',command['session_id'],{'active_set_id':None})
            update(conn,'workout_sets',rid,values)
            affected.append(rid)
        if scoped and state['workout']['phase']!='rest' and state['workout']['active_set_id'] in changes and changes[state['workout']['active_set_id']].get('row_status')=='omitted':
            self.advance(conn,command['session_id'])
        return affected

    def actual(self,conn,command,state,actuals,completed_at,source):
        sid=command['session_id']; w=state['workout']; rid=command['args']['row_id']
        r=next((r for r in state['sets'] if r['id']==rid),None)
        require(r and r['id']==w['active_set_id'] and r['row_status'] in ('planned','void'),'MOVED','only the active row can be recorded')
        require(w['phase'] in ('prep','ready','post'),'STAGE','set completion is not active')
        require(actuals['unit']==r['target_unit'],'UNIT','actual unit differs from prescription')
        started=row(conn,'SELECT started_at FROM session_input_state WHERE session_id=?',(sid,))['started_at']
        require(started<=completed_at<=self.clock(),'CLOCK','completion is outside the session clock')
        self.runtime.validate_actual(state,actuals)
        values={'reps':actuals['count'] if actuals['unit']!='seconds' else None,
                'weight_kg':actuals['load_g']/1000 if actuals['load_g'] is not None else None,
                'rir':actuals.get('rir'),'rir_is_lower_bound':int(actuals.get('rir_is_lower_bound',False)),
                'actual_unit':actuals['unit'],'actual_source':source,'row_status':'logged',
                'actual_hold_sec':actuals['count'] if actuals['unit']=='seconds' else actuals.get('hold_sec'),
                'hold_sec':actuals['count'] if actuals['unit']=='seconds' else r['hold_sec'],
                'recorded_revision':command['expected_revision']+1,'raw_input':audit_json(command),
                'recorded_at':datetime.fromtimestamp(completed_at/1000,self.local_timezone).strftime('%Y-%m-%d %H:%M:%S')}
        if r['set_kind']=='working':
            require(actuals.get('rir') is not None,'STAGE','working completion requires RIR')
        # Clear the active pointer before the row leaves planned state.
        update(conn,'workouts',sid,{'active_set_id':None})
        update(conn,'workout_sets',rid,values)
        conn.execute('UPDATE session_input_state SET pending_completed_at=NULL WHERE session_id=?',(sid,))
        changed = []
        if getattr(self.runtime, 'recalculate_after_actual', False):
            # This runs under the same writer lock as the actual. A hook or
            # validation refusal rolls back the actual and all target changes.
            changed = self.recalculate(conn, command, effective_policy(conn, w['policy_bundle_id']))
            r = row(conn, 'SELECT * FROM workout_sets WHERE id=?', (rid,))
            state = snapshot(conn, sid)
        if r['set_kind']=='working' and r['lift_id'] and not conn.execute("SELECT 1 FROM workout_sets WHERE lift_id=? AND row_status='planned' AND set_kind='working'",(r['lift_id'],)).fetchone():
            # C08: the final planned working set closes its lift in this transaction; only an explicit reopen restores it.
            lift=row(conn,'SELECT status FROM workout_lifts WHERE id=?',(r['lift_id'],))
            if lift['status']=='open':self.close_lift(conn,sid,r['lift_id'],'planned_complete',command['expected_revision']+1)
        self.advance(conn,sid)
        rest_duration = getattr(self.runtime, 'rest_duration', lambda state, current: current['target_rest_ms'])(state, r)
        if r['set_kind']=='working' or (getattr(self.runtime, 'rest_all_doses', False) and rest_duration > 0):
            duration=rest_duration
            update(conn,'workouts',sid,{'phase':'rest','rest_epoch':w['rest_epoch']+1,
                   'rest_started_at':completed_at,'rest_deadline_at':completed_at+duration,'rest_duration_ms':duration})
        return [rid] + changed

    def intent(self,conn,c,state,policy,now):
        handler = getattr(self.runtime, 'program_intent', None)
        if handler:
            return handler(self, conn, c, state, policy, now)
        return self.native_intent(conn, c, state, policy, now)

    def native_intent(self,conn,c,state,policy,now):
        sid=c['session_id']; event=c['event_type']; a=c['args']; w=state['workout']; revision=w['revision']+1
        if event in ('skip_block', 'complete_block'):
            handler = getattr(self.runtime, 'block_intent', None)
            require(handler, 'CAPABILITY', 'ordered block execution unavailable')
            return handler(self, conn, c, state)
        guard = getattr(self.runtime, 'guard_intent', None)
        if guard:
            guard(c, state)
        r=next((r for r in state['sets'] if r['id']==a.get('row_id')),None)
        if event in ('set_logged','select_reps','select_reps_nullable') and w['phase']=='rest':
            require(r and r['id']==w['active_set_id'],'MOVED','early completion is not the active row')
            self.evidence(conn,dict(id='rest-elapsed:'+c['command_id'],session_id=sid,event_id=c['command_id'],kind='elapsed_rest',value=max(0,now-w['rest_started_at']),unit='milliseconds',scope='set',observed_at=now,source_kind=c['source_ref']['kind'],source_ref=c['source_ref']['ref']))
            self.cancel_rest(conn,sid)
            update(conn,'workouts',sid,{'phase':'ready'})
            state=snapshot(conn,sid);w=state['workout']
        if event=='set_logged':
            require(w['input_stage']!='rir','STAGE','pending reps requires select_rir or correction')
            return self.actual(conn,c,state,a['actuals'],a['completed_at'],a['actual_source'])
        if event in ('select_reps','select_reps_nullable'):
            require(r and r['id']==w['active_set_id'] and w['input_stage']=='reps' and w['phase'] in ('ready','prep','post'),'MOVED','not the current reps input')
            require(event != 'select_reps_nullable' or (getattr(self.runtime, 'nullable_reps', False) and conn.execute('PRAGMA user_version').fetchone()[0]>=85), 'CAPABILITY', 'nullable reps require program runtime')
            require(r['target_unit']=='reps','UNIT','select_reps requires a rep dose')
            actual={'unit':'reps','count':a['actual_reps'],'load_g':a['load_g']}
            if not getattr(self.runtime, 'requires_rir', lambda state,r: r['set_kind']=='working')(state,r):
                return self.actual(conn,c,state,actual,a['completed_at'],'tap_confirmed')
            started=row(conn,'SELECT started_at FROM session_input_state WHERE session_id=?',(sid,))['started_at']
            require(started<=a['completed_at']<=now,'CLOCK','completion is outside the session clock')
            self.runtime.validate_actual(state,actual)
            update(conn,'workout_sets',r['id'],{'row_status':'planned','reps':a['actual_reps'],'weight_kg':a['load_g']/1000 if a['load_g'] is not None else None,'actual_source':'tap_confirmed','actual_unit':'reps','raw_input':audit_json(c)})
            conn.execute('UPDATE session_input_state SET pending_completed_at=? WHERE session_id=?',(a['completed_at'],sid))
            update(conn,'workouts',sid,{'input_stage':'rir','action_kind':'select_rir'})
            return [r['id']]
        if event=='select_rir':
            require(r and r['id']==w['active_set_id'] and w['input_stage']=='rir' and r['row_status']=='planned','MOVED','not the current RIR input')
            meta=row(conn,'SELECT * FROM session_input_state WHERE session_id=?',(sid,))
            require(meta['pending_completed_at'] is not None,'STAGE','missing completion tap')
            return self.actual(conn,c,state,{'unit':'reps','count':r['reps'],'load_g':grams(r['weight_kg']),
                        'rir':a['rir'],'rir_is_lower_bound':a['rir_is_lower_bound']},meta['pending_completed_at'],'tap_confirmed')
        if event in ('change_load','change_reps','select_load'):
            require(w['input_stage']!='rir','STAGE','finish or undo pending actual input first')
            if event=='select_load':require(r and r['id']==w['active_set_id'],'MOVED','load row is not active')
            affected=self.recalculate(conn,c,policy)
            require(affected,'POLICY','no unperformed prescription was changed')
            # Preserve an existing rest deadline; all other fields come from rows.
            return affected
        if event=='skip_warmup':
            # Owner 2026-10-15: warm-up is skipped only by chat, as its own command (never a next_lift reason).
            return self.skip_warmup(conn,sid,state)
        if event in ('close_lift','skip_lift','next_lift'):
            lid=a.get('lift_id',w['active_lift_id'])
            require(lid==w['active_lift_id'],'LIFT','only the active lift can be closed')
            affected=self.close_lift(conn,sid,lid,a['reason'],revision) if lid else []
            if a.get('target_lift_id'):
                target=next((l for l in snapshot(conn,sid)['lifts'] if l['id']==a['target_lift_id'] and l['status']=='open'),None)
                require(target,'LIFT','target lift is not open')
                update(conn,'workouts',sid,{'active_lift_id':target['id']})
            self.advance(conn,sid)
            return affected
        if event in ('stop_session','skip_post'):
            if event=='skip_post':
                require(w['phase'] in ('post','lift_done'),'STAGE','post work is not active')
                # Skipping an offered post activity drops only its planned rows; the member may still choose
                # another (cardio then stretch) and ends with stop_session. With nothing offered, skip_post closes.
                offered=[r['id'] for r in state['sets'] if r['set_kind']=='post' and r['row_status']=='planned']
                if offered:
                    for lift in state['lifts']:
                        if lift['status']=='open' and any(r['lift_id']==lift['id'] and r['id'] in offered for r in state['sets']):
                            affected=self.close_lift(conn,sid,lift['id'],a['reason'],revision)
                    self.advance(conn,sid)
                    return offered
            affected=[]
            for lift in state['lifts']:
                if lift['status']=='open':affected+=self.close_lift(conn,sid,lift['id'],a['reason'],revision)
            self.cancel_rest(conn,sid)
            conn.execute("UPDATE workout_sets SET row_status='omitted' WHERE session_id=? AND row_status='planned'",(sid,))
            update(conn,'workouts',sid,{'phase':'closed','active_lift_id':None,'active_set_id':None,'action_token_hash':None,
                   'action_kind':None,'input_stage':None,'session_epoch':w['session_epoch']+1,'closed_at':now,'close_reason':a['reason'],
                   'kcal_status':'unmeasured' if w['kcal_status']=='pending' else w['kcal_status']})
            conn.execute('UPDATE session_input_state SET pending_completed_at=NULL WHERE session_id=?',(sid,))
            import health_regimen
            health_regimen.session_end(conn, sid, now / 1000)
            self.runtime.legacy_close(conn,c,snapshot(conn,sid))
            return affected
        if event=='rest_end':
            require(w['phase']=='rest' and w['rest_epoch']==a['rest_epoch'] and w['rest_deadline_at']==a['deadline'] and now>=a['deadline'],'MOVED','rest is early, canceled or already released')
            # Retain the start as clock evidence for the released Duration slot.
            # No extra SQL column or independent rest clock is introduced.
            update(conn,'workouts',sid,{'rest_deadline_at':None,'rest_duration_ms':None})
            self.advance(conn,sid)
            return []
        if event in ('undo','correct','reclassify'):
            require(r and r['row_status']=='logged' and r['recorded_revision']==a['recorded_revision'],'MOVED','recorded row revision moved')
            closed=w['phase']=='closed'
            latest=max((s['recorded_revision'] or 0 for s in state['sets'] if s['row_status']=='logged'),default=0)
            reopen=a.get('reopen',False)
            require(event=='reclassify' or closed or (r['lift_id']==w['active_lift_id'] and r['recorded_revision']==latest) or reopen,'REOPEN_REQUIRED','older/closed lift correction requires explicit reopen')
            values={'recorded_revision':revision}
            if event=='undo':values['row_status']='void'
            elif event=='reclassify':values['set_kind']=a['set_kind']
            else:
                actual=a['actuals']; require(actual['unit']==r['target_unit'],'UNIT','cannot change historical prescription unit')
                self.runtime.validate_actual(dict(state,workout=dict(w,active_set_id=r['id'])),actual)
                values.update(reps=actual['count'] if actual['unit']!='seconds' else None,
                        weight_kg=actual['load_g']/1000 if actual['load_g'] is not None else None,
                        rir=actual.get('rir',r['rir']),rir_is_lower_bound=int(actual.get('rir_is_lower_bound',r['rir_is_lower_bound']) if 'rir' not in actual else actual.get('rir_is_lower_bound',False)),
                        actual_hold_sec=actual['count'] if actual['unit']=='seconds' else actual.get('hold_sec'),actual_source='typed',actual_unit=actual['unit'])
            update(conn,'workout_sets',r['id'],values)
            if not closed:
                self.cancel_rest(conn,sid)
                if event=='undo':
                    if r['lift_id'] and reopen:
                        update(conn,'workout_lifts',r['lift_id'],{'status':'open','closed_reason':None,'closed_revision':None})
                    update(conn,'workouts',sid,{'active_lift_id':r['lift_id'],'active_set_id':r['id'],'phase':'prep' if r['set_kind']=='prep' else 'ready','input_stage':'reps'})
                else:self.advance(conn,sid)
            return [r['id']]+([] if closed else self.recalculate(conn,c,policy))
        if event=='measure_kcal':
            require(a['observed_at']<=now,'CLOCK','future measurement')
            if a['status']=='measured':
                evidence=next((e for e in state['evidence'] if e['id']==a['evidence_id'] and e['scope']=='session' and e['observed_at']==a['observed_at']),None)
                require(evidence,'EVIDENCE','measurement needs session-scoped evidence')
            update(conn,'workouts',sid,{'kcal':a['kcal'],'kcal_origin':'measured' if a['kcal'] is not None else None,'kcal_status':a['status']})
            return []
        if event=='reorder':
            open_ids={l['id'] for l in state['lifts'] if l['status']=='open'}
            require(set(a['lift_ids'])==open_ids,'LIFT','reorder must name every open lift exactly once')
            base=max((l['order_index'] for l in state['lifts']),default=0)+1
            for n,lid in enumerate(a['lift_ids']):update(conn,'workout_lifts',lid,{'order_index':base+n})
            self.cancel_rest(conn,sid)
            update(conn,'workouts',sid,{'active_lift_id':a['lift_ids'][0]})
            self.advance(conn,sid)
            return []
        if event=='pain':
            require(a['observed_at']<=now,'CLOCK','future pain observation')
            conn.execute('INSERT INTO session_evidence VALUES(?,?,?,?,?,?,?,?,?,?)',
                   (a['evidence_id'],sid,c['command_id'],'pain',canonical({'lift_id':a['lift_id'],'description':a['description'],'alternative_exercise_id':a['alternative_exercise_id']}),None,a['scope'],a['observed_at'],c['source_ref']['kind'],c['source_ref']['ref']))
            affected=self.close_lift(conn,sid,a['lift_id'],'pain',revision)
            if a['alternative_exercise_id'] is not None:
                alternative=dict(c,event_type='free_lift',args={'exercise_id':a['alternative_exercise_id']})
                facts=self.runtime.resolve(snapshot(conn,sid),alternative,policy)
                self.insert_plan(conn,sid,facts['plan'],revision,c['command_id'])
            self.advance(conn,sid)
            return affected
        if event=='approve_step':
            # Pack validates the typed speech against the exact current view;
            # it may return an operation, which is committed through this class.
            approval=self.runtime.approve(conn,c,state,policy)
            from session_v2_contract.transport import TrainerApproval
            approval=TrainerApproval(dict(approval)).to_dict()
            require(approval['session_id']==sid and approval['base_revision']==w['revision'] and approval['view_digest']==digest(state),'MOVED','approval binding moved')
            decision=approval['decision']['decision']
            affected=[]
            if decision['op']!='keep':
                nested=dict(c,event_type=decision['op'],args={k:v for k,v in decision.items() if k!='op'})
                affected=self.intent(conn,nested,state,policy,now)
            conn.execute('INSERT INTO session_trainer_approvals VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                 (approval['id'],sid,approval['base_revision'],approval['view_digest'],canonical(approval['decision']),canonical(approval['cue_ast']),approval['dependency_digest'],approval['policy_bundle_id'],approval['catalog_version'],approval['expires_epoch'],'prepared'))
            return affected
        # Identity/setup/start-parity verbs depend on pinned pack catalog/rotation
        # facts; the trusted implementation participates in this transaction.
        require(event in ('choose_split','free_lift','substitute','missing_weight','choose_implement','choose_machine','photo_result','choose_post','claim_split'),'COMMAND','unknown command')
        return self.parity_intent(conn,c,state,policy,now)


    def parity_intent(self,conn,c,state,policy,now):
        """Kit owns every write; the pack resolves only immutable catalog facts."""
        from session_v2_contract.policy import EquipmentSnapshot, Evidence, CatalogEntry
        sid=c['session_id'];a=c['args'];event=c['event_type'];w=state['workout'];rev=w['revision']+1
        lid=a.get('lift_id');lift=next((l for l in state['lifts'] if l['id']==lid),None)
        if lid is not None:
            require(lift and lift['status']=='open','LIFT','setup needs an open lift in this session')
        facts=self.runtime.resolve(state,c,policy)
        require(type(facts) is dict,'FACTS','pure resolver must return catalog facts')
        affected=[]
        if event in ('choose_split','free_lift','substitute','choose_post'):
            if event=='choose_split':
                require(not any(r['row_status']=='logged' for r in state['sets']),'STAGE','split cannot change after recording')
                for l in state['lifts']:
                    if l['status']=='open':affected+=self.close_lift(conn,sid,l['id'],'choose_split',rev)
                conn.execute('UPDATE session_input_state SET split_id=? WHERE session_id=?',(a['split_id'],sid))
                self.split(conn,sid,a['split_id'])
            elif event=='substitute':
                affected+=self.close_lift(conn,sid,lid,a['reason'],rev)
                require(all(p['lift']['exercise_id']==a['exercise_id'] and p['lift']['variant_id']==a['variant_id'] for p in facts['plan']),'CATALOG','substitution must change canonical identity')
            elif event=='choose_post':
                require(w['phase'] in ('lift_done','post') and all(l['status']!='open' or not any(r['lift_id']==l['id'] and r['row_status']=='planned' for r in state['sets']) for l in state['lifts']),'STAGE','working plan is not finished')
                require(all(r['set_kind']=='post' for p in facts['plan'] for r in p['sets']),'PLAN','post plan contains working rows')
            else:
                require(all(p['lift']['exercise_id']==a['exercise_id'] and p['lift']['variant_id']==a['variant_id'] for p in facts['plan']),'CATALOG','free lift identity mismatch')
            before={r['id'] for r in state['sets']}
            self.insert_plan(conn,sid,facts['plan'],rev,c['command_id'])
            affected += [r['id'] for r in snapshot(conn,sid)['sets'] if r['id'] not in before]
            self.cancel_rest(conn,sid);self.advance(conn,sid)
            return affected
        if event=='claim_split':
            meta=row(conn,'SELECT * FROM session_input_state WHERE session_id=?',(sid,))
            require(meta['mode']=='free' and meta['claimed_split_id'] is None,'CLAIM','free split is already claimed or not eligible')
            require(facts.get('covered_split_id')==a['split_id'],'CLAIM','logged exercise coverage does not satisfy this split')
            self.split(conn,sid,a['split_id'])
            conn.execute('UPDATE session_input_state SET claimed_split_id=? WHERE session_id=?',(a['split_id'],sid))
            return []
        if event=='missing_weight':
            require(a['unavailable_load_g'] not in a['available_loads_g'] and len(a['available_loads_g'])==len(set(a['available_loads_g'])),'EQUIPMENT','available grid contradicts the missing load')
            # The learned grid is immutable evidence, never an override file.
            evidence=dict(id=a['evidence_id'],session_id=sid,event_id=c['command_id'],kind='available_loads',
                  value=dict(lift_id=lid,variant_id=lift['variant_id'],machine_ref=lift['machine_ref'],unavailable_load_g=a['unavailable_load_g'],available_loads_g=a['available_loads_g']),
                  unit='grams',scope='lift',observed_at=now,source_kind=c['source_ref']['kind'],source_ref=c['source_ref']['ref'])
            self.evidence(conn,Evidence(evidence).to_dict())
            return self.recalculate(conn,c,policy)
        if event=='photo_result':
            require(a['observed_at']<=now,'CLOCK','photo evidence is in the future')
            evidence=Evidence(facts['evidence']).to_dict()
            require(evidence['id']==a['evidence_id'] and evidence['session_id']==sid and evidence['observed_at']==a['observed_at'],'EVIDENCE','photo evidence binding mismatch')
            self.evidence(conn,evidence)
            if a['machine_ref'] is None:
                update(conn,'workouts',sid,{'phase':'entry','input_stage':'setup','action_kind':'choose_machine'})
                return []
        if event in ('choose_implement','choose_machine','photo_result'):
            catalog=CatalogEntry(facts['catalog']).to_dict()
            eq=EquipmentSnapshot(facts['equipment']).to_dict()
            require(catalog['review_status']=='approved' and catalog['catalog_version']==w['catalog_version'] and eq['session_id']==sid and eq['catalog_version']==w['catalog_version'],'CATALOG','unpinned equipment facts')
            require(eq['variant_id']==catalog['variant_id'] and all(eq[k]==catalog[k] for k in ('implement_count','load_basis')),'EQUIPMENT','catalog and equipment semantics disagree')
            if event=='choose_implement':
                require(all(a[k]==eq[k] for k in ('variant_id','implement_count','load_basis')),'EQUIPMENT','selected implement differs from resolved facts')
                require(catalog['exercise_id']==lift['exercise_id'],'CATALOG','implement must retain exercise identity')
            else:
                require(eq['machine_ref']==a['machine_ref'] and catalog['variant_id']==lift['variant_id'],'MACHINE','machine selection binding mismatch')
                require(set(catalog['compatible_machine_capabilities'])<=set(facts.get('machine_capabilities',[])),'MACHINE','machine lacks required capability')
                require(row(conn,"SELECT ulid FROM ax_machine WHERE ulid=? AND status='active'",(eq['machine_ref'],)),'MACHINE','machine is not registered and active')
            require(not any(r['lift_id']==lid and r['row_status']=='logged' for r in state['sets']) or eq['variant_id']==lift['variant_id'],'IMMUTABLE','performed variant requires a separate substitution lift')
            conn.execute('INSERT INTO session_equipment_snapshots('+','.join(eq)+') VALUES('+','.join('?' for _ in eq)+')',tuple(eq.values()))
            update(conn,'workout_lifts',lid,{'variant_id':eq['variant_id'],'machine_ref':eq['machine_ref'],'implement_resolution_id':eq['id']})
            conn.execute("UPDATE workout_sets SET machine_ref=? WHERE lift_id=? AND row_status='planned'",(eq['machine_ref'],lid))
            affected=self.recalculate(conn,c,policy)
            self.advance(conn,sid)
            return affected
        raise CommandError('COMMAND','unhandled parity intent')

    @staticmethod
    def evidence(conn,e):
        if e['kind'] in ('program_plan/1', 'prescription_basis/2'):
            from session_program import validate_evidence
            validate_evidence(conn, e)
        conn.execute('INSERT INTO session_evidence VALUES(?,?,?,?,?,?,?,?,?,?)',
                 (e['id'],e['session_id'],e['event_id'],e['kind'],canonical(e['value']),e['unit'],e['scope'],e['observed_at'],e['source_kind'],e['source_ref']))

    @staticmethod
    def split(conn,sid,split_id):
        split=row(conn,'SELECT id FROM session_type WHERE code=?',(split_id,))
        require(split,'SPLIT','split is not in kit rotation catalog')
        update(conn,'workouts',sid,{'session_type_id':split['id']})


def apply(conn, command):
    """Frozen Python entry: only a service-owned connection has a writer seat."""
    writer=getattr(conn,'session_writer',None)
    require(isinstance(writer,Writer),'AUTH','v2 writes require the authenticated kit writer service')
    return writer.apply(conn,command)


class ServiceConnection(sqlite3.Connection):
    """Connection owned by the authenticated local service, never a model tool."""
    session_writer = None
