"""DGN-2085 F1 rev8.1: kit-owned live_delivery/1 persistence.

All public operations enter through dispatch, including in-process calls. The
Authority is constructed by the installed binding, never from request JSON.
No transaction performs transport or calls a framework lock. Protected exports
are verified before dispatch; their exact values are checked again in the tx.
"""
import hashlib
import hmac
import json
import re
import secrets
import sqlite3

from session_command import canonical, digest, row, rows
from session_v2_contract.commands import CommitReceipt
from session_program import ProgramCommand as CommandEnvelope
from session_v2_contract.transport import CallbackAcknowledgement, DeliveryJournalEntry, Surface

SCHEMA = 'live_delivery/1'
# Resolution writes a durable binding, so TIMEOUT there is never retryable.
READ_ONLY = {'command_lookup', 'delivery_check', 'delivery_read'}
FIELDS = {
    'selector_put': 'S P revision handle selector command_template template_digest frame_digest slot',
    'selector_resolve': 'S P envelope_id envelope_digest handle selector message_id expected_revision',
    'command_lookup': 'S P envelope_id command_digest',
    'delivery_claim': 'S P D receipt_digest holder_id',
    'delivery_renew': 'S P D L ttl_ms',
    'delivery_fence': 'S P D expected_fence reason',
    'delivery_check': 'S P D L',
    'delivery_bind': 'S P D L frame_digest receipt_digest plan_digest stages branch_manifest budgets',
    'delivery_select': 'S P D L selection',
    'journal_write': 'S P D L B seq event_id event',
    'surface_rebind': 'S P D L B event_id expected replacement_receipt retirement_receipt selector_bindings protection cleanup',
    'delivery_read': 'S P D',
    'delivery_complete': 'S P D L plan_digest',
}
EVENT_STATES = {'planned': 'pending', 'attempt_started': 'inflight', 'delivered': 'acknowledged',
                'delivery_unknown': 'uncertain', 'attempt_failed': 'pending', 'absence_confirmed': 'pending',
                'target_absent': 'satisfied_absent', 'cleanup_retained': 'satisfied_retained',
                'branch_not_selected': 'satisfied_not_selected', 'rate_limited': 'waiting_retry'}
PROJECTION = {'planned': 'requested', 'attempt_started': 'requested', 'delivered': 'acknowledged',
              'delivery_unknown': 'uncertain', 'attempt_failed': 'failed', 'absence_confirmed': 'requested',
              'target_absent': 'requested', 'cleanup_retained': 'failed',
              'branch_not_selected': 'requested', 'rate_limited': 'failed'}


class DeliveryError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def need(value, code='INVALID_SCHEMA'):
    if not value:
        raise DeliveryError(code)


def fields(value, required, optional=''):
    need(type(value) is dict and set(required.split()) <= value.keys() and
         value.keys() <= set((required + ' ' + optional).split()))


def string(value):
    need(type(value) is str and bool(value))


def integer(value, minimum=0):
    need(type(value) is int and value >= minimum)


def hash_value(value):
    need(type(value) is str and re.fullmatch(r'sha256:[0-9a-f]{64}', value) is not None)


def scope_shape(s):
    fields(s, 'session_id chat_id surface_id epoch')
    integer(s['session_id'], 1); string(s['surface_id']); integer(s['epoch'], 1)
    need(type(s['chat_id']) is int)


def protection_shape(p):
    fields(p, 'chat_id message_id body_digest protected_guidance source_frame_digest')
    need(type(p['chat_id']) is int and type(p['protected_guidance']) is bool)
    if p['message_id'] is not None:
        integer(p['message_id'], 1)
    hash_value(p['body_digest']); hash_value(p['source_frame_digest'])


def stage_key(k):
    fields(k, 'chat_id epoch op stage_id')
    need(type(k['chat_id']) is int); integer(k['epoch'], 1)
    string(k['op']); string(k['stage_id'])
    need(re.fullmatch('[a-z][a-z0-9_]{0,31}', k['stage_id']) is not None)
    return canonical(k)


def event_key(k, seq, event):
    return canonical([k, seq, event])


def decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result)
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda value: (_ for _ in ()).throw(DeliveryError('INVALID_SCHEMA')))
    except (ValueError, TypeError) as error:
        raise DeliveryError('INVALID_SCHEMA') from error


def typed_digest(value):
    return 'sha256:' + hashlib.sha256(value.to_json().encode('utf-8')).hexdigest()


def sign_request(operation, args, publisher_id, expires_at_ms, key):
    """Trusted publisher helper; key comes from installation, never the wire."""
    value = dict(schema=SCHEMA, operation=operation, args=args,
                 publisher_id=publisher_id, expires_at_ms=expires_at_ms)
    mac = hmac.new(key, canonical(value).encode(), hashlib.sha256).hexdigest()
    return dict(schema=SCHEMA, operation=operation, args=args,
                auth=dict(publisher_id=publisher_id, expires_at_ms=expires_at_ms, mac=mac))


TERMINAL = {'acknowledged', 'satisfied_absent', 'satisfied_retained', 'satisfied_not_selected'}
EMPTY_BRANCHES = dict(schema='delivery_branches/1', groups=[])
OUTCOMES = ('ok', 'unchanged', 'absent', 'retained_cleanup')


def strict_json(value):
    """Reject values the rev8 adapter encoder cannot represent losslessly."""
    if type(value) is dict:
        for key, item in value.items():
            need(type(key) is str)
            strict_json(key); strict_json(item)
    elif type(value) is list:
        for item in value: strict_json(item)
    elif type(value) is str:
        need(not any(0xd800 <= ord(c) <= 0xdfff for c in value))
    else:
        need(value is None or type(value) in (int, bool))


def plan_digest_preimage(args):
    """F1 rev8 section 2.7.3: exact immutable object, never the request.

    Canonical UTF-8 bytes use the adapter encoder, not typed-kit to_json.
    Leases, principal, selections, authentication and transport receipts are
    outside this boundary. No defaults, projections or array sorting occur.
    """
    value = {k: args[k] for k in ('S', 'D', 'frame_digest', 'receipt_digest',
                                'stages', 'branch_manifest', 'budgets')}
    strict_json(value)
    scope_shape(value['S'])
    fields(value['D'], 'outbox_id delivery_key')
    for item in value['D'].values(): string(item)
    hash_value(value['frame_digest']); hash_value(value['receipt_digest'])
    need(type(value['stages']) is list and value['stages'])
    for stage in value['stages']:
        fields(stage, 'K payload_digest target slot mode depends_on protection allowed_outcomes notification')
        stage_key(stage['K']); hash_value(stage['payload_digest'])
        protection_shape(stage['protection']); string(stage['slot'])
        fields(stage['notification'], 'class silent')
        string(stage['notification']['class'])
        need(type(stage['notification']['silent']) is bool)
        need(stage['mode'] in ('send', 'edit', 'retire', 'delete'))
        need(stage['mode'] == 'send' or stage['notification']['silent'] is True)
    budgets = value['budgets']
    fields(budgets, 'max_sends max_notifications max_edits_by_slot')
    integer(budgets['max_sends']); integer(budgets['max_notifications'])
    edits = budgets['max_edits_by_slot']
    need(type(edits) is dict and set(edits) == {s['slot'] for s in value['stages']})
    for maximum in edits.values(): integer(maximum)
    return value


def plan_budgets(stages, manifest):
    """Maxima over executable branches, including provisional default edits.

    Rev8's manifest permits a single default edit and a replacement send plus
    optional deletes. The replacement path can follow that attempted edit;
    losing no-I/O records and retries add no logical unit.
    """
    by_id = {s['K']['stage_id']: s for s in stages}
    slots = {s['slot'] for s in stages}
    def counts(ids):
        selected = [by_id[n] for n in ids]
        return [sum(s['mode'] == 'send' for s in selected),
                sum(s['mode'] == 'send' and not s['notification']['silent'] for s in selected)] + [
                sum(s['mode'] in ('edit', 'retire') and s['slot'] == slot for s in selected) for slot in sorted(slots)]
    members = {n for g in manifest['groups'] for c in g['choices'] for n in c['stage_ids']}
    total = counts([n for n in by_id if n not in members])
    for group in manifest['groups']:
        default, replacement = group['choices']
        choices = [counts(default['stage_ids']), counts(default['stage_ids'] + replacement['stage_ids'])]
        total = [v + max(c[i] for c in choices) for i, v in enumerate(total)]
    return dict(max_sends=total[0], max_notifications=total[1],
                max_edits_by_slot=dict(zip(sorted(slots), total[2:])))


def stored_scope(conn, s):
    """Resolve old adapter labels using the persisted typed ID, preserving bytes."""
    matches = [r['scope'] for r in rows(conn, 'SELECT scope FROM session_live_scopes WHERE session_id=?', (s['session_id'],))
               if all(json.loads(r['scope'])[k] == s[k] for k in ('chat_id', 'surface_id', 'epoch'))]
    need(len(matches) <= 1, 'CONFLICT')
    return matches[0] if matches else canonical(s)


def stored_request_equal(stored, current, session_id):
    """Compare legacy snapshots through their stored typed mapping, without edits."""
    if stored is None: return False
    value = json.loads(stored)
    if type(value.get('S')) is dict and type(value['S'].get('session_id')) is str:
        value = dict(value, S=dict(value['S'], session_id=session_id))
    return canonical(value) == canonical(current)


def installed_scope(scope):
    """Normalize only trusted configuration, before signing; wire stays strict.

    A legacy adapter label uses its explicit typed workout mapping. Decimal
    text must be canonical and agree with that mapping; never coerce wire IDs.
    """
    fields(scope, 'S session_id member_id actors slots action_slot', 'transient_slots')
    transient = scope.get('transient_slots', [])
    need(type(transient) is list and transient in ([], ['answer']), 'AUTH_FAILED')
    # This typed mapping also includes closeout surfaces. The framework owns
    # live_surface/1's four current-card limit; answer belongs to neither map.
    need(type(scope['slots']) is dict and scope['slots'] and
         'answer' not in scope['slots'] and scope['action_slot'] != 'answer', 'AUTH_FAILED')
    scope = dict(scope, transient_slots=transient)
    integer(scope['session_id'], 1)
    value = scope['S']['session_id']
    if type(value) is str:
        if value.isdecimal():
            need(re.fullmatch('[1-9][0-9]*', value) is not None and int(value) == scope['session_id'], 'AUTH_FAILED')
        else:
            need(bool(value) and re.fullmatch('[A-Za-z][A-Za-z0-9_-]*', value) is not None, 'AUTH_FAILED')
        scope = dict(scope, S=dict(scope['S'], session_id=scope['session_id']))
    scope_shape(scope['S'])
    need(scope['S']['session_id'] == scope['session_id'], 'AUTH_FAILED')
    return scope


def authenticated_receipt(receipt, key):
    """Full protected export, including authentication, hashed by selections."""
    signed = dict(kind='receipt', value=receipt)
    return dict(signed, mac=hmac.new(key, canonical(signed).encode(), hashlib.sha256).hexdigest())


def selection_signature(value, key):
    unsigned = {k:v for k,v in value.items() if k not in ('selection_digest', 'auth')}
    signed = dict(unsigned, selection_digest=digest(unsigned))
    return dict(signed, auth=dict(publisher_id=value['auth']['publisher_id'],
        mac=hmac.new(key, canonical(signed).encode(), hashlib.sha256).hexdigest()))


class Authority:
    """Already verified installed publisher, identity map and protected evidence.

    scopes entries: {S, session_id: int, member_id, actors, slots: {slot: typed
    surface_kind}, action_slot, transient_slots: [] | ["answer"]}. Legacy
    declarations omit transient_slots and read as []. Exact exports bind envelope_id/digest,
    S, P and source_ref. Receipt exports contain the exact receipt object.
    The binding validates export MACs and artifact/service attestations first.
    """
    def __init__(self, publisher_id, key, scopes, token_key, *, envelopes=(), receipts=(), coordinator=False, evidence=(), selections=(), recovery_keys=None, invocations=(), exports=()):
        self.publisher_id, self.key, self.scopes = publisher_id, key, scopes
        self.token_key = token_key
        self.envelopes, self.receipts = envelopes, receipts
        self.coordinator = coordinator
        self.evidence, self.selections = evidence, selections
        self.recovery_keys, self.invocations = recovery_keys or {}, invocations
        self.exports = exports

    def scope(self, s, p):
        scope_shape(s)
        fields(p, 'publisher_id actor member_id')
        for value in p.values(): string(value)
        need(p['publisher_id'] == self.publisher_id, 'AUTH_FAILED')
        found = next((x for x in self.scopes if all(x['S'][k] == s[k] for k in ('session_id','chat_id'))), None)
        need(found and type(found['session_id']) is int and found['session_id'] == s['session_id'] and p['member_id'] == found['member_id'] and p['actor'] in found['actors'], 'AUTH_FAILED')
        return installed_scope(found)

    def envelope(self, a):
        found = next((e for e in self.envelopes if e['envelope_id'] == a['envelope_id']), None)
        need(found, 'AUTH_FAILED')
        fields(found, 'S P envelope_id envelope_digest source_ref', 'command_digest callback_data')
        hash_value(found['envelope_digest'])
        need(all(found[k] == a[k] for k in ('S', 'P', 'envelope_digest')), 'AUTH_FAILED')
        need(found.get('callback_data') == 'flow2|' + a['handle'] + '|' + a['selector'], 'AUTH_FAILED')
        return found

    def command_envelope(self, command, s, p):
        found = next((e for e in self.envelopes if e['envelope_id'] == command['source_ref']['ref']), None)
        need(found, 'AUTH_FAILED')
        fields(found, 'S P envelope_id envelope_digest source_ref', 'command_digest callback_data')
        hash_value(found['envelope_digest'])
        need(found['S'] == s and found['P'] == p and found['source_ref'] == command['source_ref'], 'AUTH_FAILED')
        if command['source_ref']['kind'] != 'callback':
            need(found.get('command_digest') == digest(command), 'AUTH_FAILED')
        return found

    def export(self, kind, value):
        """Retain full verified imports; trusted in-process values use same MAC."""
        expected = dict(kind=kind, value=value)
        expected['mac'] = hmac.new(self.key, canonical(expected).encode(), hashlib.sha256).hexdigest()
        found = next((e for e in self.exports if e.get('kind') == kind and e.get('value') == value), None)
        if found is not None:
            fields(found, 'kind value mac')
            need(found == expected, 'AUTH_FAILED')
            return found
        # Only the installed binding/trusted caller constructs Authority.
        # Request JSON cannot add values to its preverified collections.
        return expected

    def receipt(self, value):
        need(value in self.receipts, 'RECEIPT_MISMATCH')


def register_scope(conn, authority, s, p):
    config = authority.scope(s, p)
    scope = stored_scope(conn, s)
    values = (scope, config['session_id'], p['publisher_id'], p['member_id'],
              canonical(config['slots']), config['action_slot'])
    existing = conn.execute('SELECT * FROM session_live_scopes WHERE scope=?', (scope,)).fetchone()
    if existing:
        need(tuple(existing) == values, 'CONFLICT')
    else:
        conn.execute('INSERT INTO session_live_scopes VALUES(?,?,?,?,?,?)', values)
    return config


def bind_commit(conn, command, receipt, authority, s):
    """Called inside Writer.apply, after the domain/outbox inserts, before commit.

    Typed-turn/due invocations get the same durable binding as selector commands.
    Does not open a transaction or reinterpret the pinned typed object bytes.
    """
    p = dict(publisher_id=authority.publisher_id, actor=command['actor'],
             member_id=next(x['member_id'] for x in authority.scopes if x['S']['session_id'] == s['session_id'] and x['S']['chat_id'] == s['chat_id']))
    config = register_scope(conn, authority, s, p)
    need(config['session_id'] == command['session_id'], 'CONFLICT')
    scope, cd = stored_scope(conn, s), digest(command)
    old = row(conn, 'SELECT * FROM session_live_commands WHERE scope=? AND command_digest=?', (scope, cd))
    if old:
        need(old['command_json'] == CommandEnvelope(command).to_json() and old['principal'] == canonical(p), 'CONFLICT')
        bid = old['binding_id']
    else:
        bid = secrets.token_hex(16)
        envelope_id = command['source_ref']['ref']
        envelope = authority.command_envelope(command, s, p)
        conn.execute('INSERT INTO session_live_commands(binding_id,scope,envelope_id,envelope_digest,command_digest,command_json,principal,state,session_id) VALUES(?,?,?,?,?,?,?,?,?)',
                     (bid, scope, envelope_id, envelope['envelope_digest'], cd, CommandEnvelope(command).to_json(), canonical(p), 'bound', command['session_id']))
    r = CommitReceipt(dict(receipt))
    acknowledgement = CallbackAcknowledgement(dict(schema=2, session_id=command['session_id'],
        status='DELIVERY_PENDING', receipt=r.to_dict(), current_revision=r['revision'],
        diagnostic_key=None, model_fallback=False)).to_json()
    d = None
    if r['outbox_id'] is not None:
        existing = row(conn, 'SELECT * FROM session_live_deliveries WHERE typed_outbox=?', (canonical(r['outbox_id']),))
        if existing:
            # The existing writer's repeated stop returns the original receipt.
            # Retain that original delivery; never allocate a second outbox or
            # change its authoritative command binding for a no-op alias.
            need(existing['scope'] == scope and existing['receipt_digest'] == typed_digest(r), 'CONFLICT')
            d = dict(outbox_id=existing['outbox_id'], delivery_key=existing['delivery_key'])
        else:
            d = dict(outbox_id=secrets.token_hex(16), delivery_key=secrets.token_hex(16))
            conn.execute('INSERT INTO session_live_deliveries(outbox_id,delivery_key,binding_id,scope,typed_outbox,receipt_digest,revision,session_id) VALUES(?,?,?,?,?,?,?,?)',
                         (d['outbox_id'], d['delivery_key'], bid, scope, canonical(r['outbox_id']), typed_digest(r), r['revision'], command['session_id']))
    conn.execute("UPDATE session_live_commands SET state='committed',acknowledgement=?,receipt=?,delivery=? WHERE binding_id=?",
                 (acknowledgement, r.to_json(), canonical(d) if d else None, bid))


class DeliveryStore:
    def __init__(self, conn, authority, *, clock=None):
        self.conn, self.authority, self.clock = conn, authority, clock

    def dispatch(self, request):
        """Returns (wire response, CLI exit status); owns exactly one transaction."""
        op = request.get('operation') if type(request) is dict else None
        try:
            strict_json(request)
            fields(request, 'schema operation args auth')
            need(request['schema'] == SCHEMA and op in FIELDS)
            a, auth = request['args'], request['auth']
            optional = 'ttl_ms' if op == 'delivery_claim' else 'receipt' if op == 'journal_write' else ''
            fields(a, FIELDS[op], optional)
            fields(auth, 'publisher_id expires_at_ms mac')
            string(auth['publisher_id']); string(auth['mac']); integer(auth['expires_at_ms'])
            need(re.fullmatch('[0-9a-f]{64}', auth['mac']) is not None, 'AUTH_FAILED')
            scope_shape(a['S'])
            if 'L' in a:
                fields(a['L'], 'holder_id lease_fence lease_until_ms')
                string(a['L']['holder_id']); integer(a['L']['lease_fence'], 1); integer(a['L']['lease_until_ms'])
            self.authority.scope(a['S'], a['P'])
            need(auth['publisher_id'] == self.authority.publisher_id, 'AUTH_FAILED')
            signed = dict(schema=SCHEMA, operation=op, args=a, publisher_id=auth['publisher_id'], expires_at_ms=auth['expires_at_ms'])
            expected = hmac.new(self.authority.key, canonical(signed).encode(), hashlib.sha256).hexdigest()
            need(hmac.compare_digest(expected, auth['mac']), 'AUTH_FAILED')
            need(not self.conn.in_transaction, 'STORAGE_FAILED')
            self.conn.execute('PRAGMA foreign_keys=ON')
            self.conn.execute('BEGIN IMMEDIATE')
            try:
                need(self.conn.execute('PRAGMA user_version').fetchone()[0] >= 82, 'STORAGE_FAILED')
                self.now = self.clock() if self.clock else self.conn.execute("SELECT CAST((julianday('now')-2440587.5)*86400000 AS INTEGER)").fetchone()[0]
                need(auth['expires_at_ms'] > self.now, 'TIMEOUT')
                need(auth['expires_at_ms'] <= self.now + 120000, 'AUTH_FAILED')
                self.operation = op
                self.scope = stored_scope(self.conn, a['S'])
                self.config = register_scope(self.conn, self.authority, a['S'], a['P'])
                self.sid = self.config['session_id']
                result = getattr(self, op)(a)
                self.conn.commit()
            except BaseException:
                self.conn.rollback()
                raise
            return dict(ok=True, result=result), 0
        except (DeliveryError, sqlite3.Error, ValueError, KeyError, TypeError, OverflowError) as error:
            code = error.code if isinstance(error, DeliveryError) else 'CONFLICT' if isinstance(error, sqlite3.IntegrityError) and 'UNIQUE constraint' in str(error) else 'STORAGE_FAILED' if isinstance(error, sqlite3.Error) else 'INVALID_SCHEMA'
            return dict(ok=False, error=dict(code=code, retryable=code in ('CLAIM_BUSY', 'STORAGE_FAILED') or (code == 'TIMEOUT' and op in READ_ONLY))), 2 if code == 'INVALID_SCHEMA' else 40 if code == 'STORAGE_FAILED' else 4

    def current_revision(self):
        w = row(self.conn, 'SELECT revision,member_id FROM workouts WHERE id=?', (self.sid,))
        need(w and w['member_id'] == self.config['member_id'], 'AUTH_FAILED')
        return w['revision']

    def selector_put(self, a):
        integer(a['revision'], 1); hash_value(a['template_digest']); hash_value(a['frame_digest'])
        need(type(a['handle']) is str and re.fullmatch('[0-9a-f]{32}', a['handle']), 'INVALID_SELECTOR')
        need(type(a['selector']) is str and re.fullmatch('[A-Za-z0-9_-]{1,24}', a['selector']), 'INVALID_SELECTOR')
        need(len(('flow2|' + a['handle'] + '|' + a['selector']).encode()) <= 64, 'INVALID_SELECTOR')
        need(a['slot'] in self.config['slots'] and a['slot'] == self.config['action_slot'], 'INVALID_SELECTOR')
        fields(a['command_template'], 'schema session_id expected_revision event_type args')
        t = a['command_template']
        need(t['event_type'] != 'skip_warmup', 'INVALID_SELECTOR')
        need(t['session_id'] == self.sid and t['expected_revision'] == a['revision'] and digest(t) == a['template_digest'], 'INVALID_SELECTOR')
        # Validate typed action/payload before storage. No bearer token is accepted
        # from the template; the current Writer token is used at resolution.
        try:
            CommandEnvelope(dict(t, command_id='template', actor=a['P']['actor'], source_ref=dict(kind='text', ref='template')))
        except ValueError as error:
            raise DeliveryError('INVALID_SELECTOR') from error
        old = row(self.conn, 'SELECT * FROM session_live_selectors WHERE scope=? AND handle=? AND selector=?', (self.scope, a['handle'], a['selector']))
        if old:
            need(stored_request_equal(old['request'], a, self.sid), 'CONFLICT')
            return json.loads(old['result'])
        previous_handle = rows(self.conn, 'SELECT * FROM session_live_selectors WHERE handle=?', (a['handle'],))
        need(all(x['scope'] == self.scope and x['revision'] == a['revision'] and x['slot'] == a['slot'] and x['state'] == 'prepared' and json.loads(x['request'])['frame_digest'] == a['frame_digest'] for x in previous_handle), 'CONFLICT')
        need(self.current_revision() == a['revision'], 'STALE_REVISION')
        self.ensure_epoch(a, 'STALE_REVISION')
        need(not row(self.conn, 'SELECT 1 FROM session_live_deliveries WHERE session_id=? AND scope!=? AND plan IS NOT NULL AND complete=0', (self.sid,self.scope)), 'CONFLICT')
        result = dict(binding_id=secrets.token_hex(16), selector_digest=digest(a), state='prepared')
        self.conn.execute('INSERT INTO session_live_selectors(binding_id,scope,handle,selector,revision,slot,state,request,result,session_id) VALUES(?,?,?,?,?,?,?,?,?,?)',
                         (result['binding_id'], self.scope, a['handle'], a['selector'], a['revision'], a['slot'], 'prepared', canonical(a), canonical(result), self.sid))
        return result

    def selector_resolve(self, a):
        for k in ('envelope_id', 'handle', 'selector'): string(a[k])
        integer(a['message_id'], 1); integer(a['expected_revision'], 1); hash_value(a['envelope_digest'])
        need(re.fullmatch('[0-9a-f]{32}', a['handle']) is not None and re.fullmatch('[A-Za-z0-9_-]{1,24}', a['selector']) is not None)
        old = row(self.conn, 'SELECT * FROM session_live_commands WHERE scope=? AND envelope_id=?', (self.scope, a['envelope_id']))
        if old:
            need(stored_request_equal(old['resolve_request'], a, self.sid), 'CONFLICT')
            return json.loads(old['resolve_result'])
        e = self.authority.envelope(a)
        selector = row(self.conn, 'SELECT * FROM session_live_selectors WHERE scope=? AND handle=? AND selector=?', (self.scope, a['handle'], a['selector']))
        need(selector, 'SELECTOR_NOT_FOUND')
        need(selector['state'] != 'prepared', 'SURFACE_PENDING')
        need(selector['state'] == 'active', 'SURFACE_MOVED')
        need(selector['revision'] == a['expected_revision'] == self.current_revision(), 'STALE_REVISION')
        slot = row(self.conn, 'SELECT * FROM session_live_slots WHERE scope=? AND slot=?', (self.scope, selector['slot']))
        need(slot and slot['message_id'] == a['message_id'] and slot['epoch'] == a['S']['epoch'] and slot['markup_active'], 'SURFACE_MOVED')
        self.ensure_epoch(a)
        source = e['source_ref']
        need(source == dict(kind='callback', ref=a['envelope_id'], member_id=a['P']['member_id'], chat_id=str(a['S']['chat_id']), message_id=str(a['message_id'])), 'AUTH_FAILED')
        need(json.loads(selector['request'])['P'] == a['P'], 'AUTH_FAILED')
        meta = row(self.conn, 'SELECT token_nonce FROM session_input_state WHERE session_id=?', (self.sid,))
        w = row(self.conn, 'SELECT action_token_hash FROM workouts WHERE id=?', (self.sid,))
        need(meta and meta['token_nonce'] and w and w['action_token_hash'], 'SURFACE_MOVED')
        token = hmac.new(self.authority.token_key, meta['token_nonce'].encode(), hashlib.sha256).hexdigest()[:32]
        need(hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), w['action_token_hash']), 'AUTH_FAILED')
        t = json.loads(selector['request'])['command_template']
        command = CommandEnvelope(dict(t, command_id=a['envelope_id'], actor=a['P']['actor'], source_ref=source, action_token=token))
        result = dict(binding_id=secrets.token_hex(16), command=command.to_dict(), command_digest=typed_digest(command), revision=selector['revision'], selector_digest=json.loads(selector['result'])['selector_digest'])
        self.conn.execute('INSERT INTO session_live_commands(binding_id,scope,envelope_id,envelope_digest,command_digest,command_json,principal,resolve_request,resolve_result,state,session_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                         (result['binding_id'], self.scope, a['envelope_id'], a['envelope_digest'], typed_digest(command), command.to_json(), canonical(a['P']), canonical(a), canonical(result), 'bound', self.sid))
        return result

    def command_lookup(self, a):
        string(a['envelope_id']); hash_value(a['command_digest'])
        c = row(self.conn, 'SELECT * FROM session_live_commands WHERE scope=? AND envelope_id=?', (self.scope, a['envelope_id']))
        need(c, 'NOT_FOUND')
        need(c['command_digest'] == a['command_digest'], 'CONFLICT')
        need(c['principal'] == canonical(a['P']), 'AUTH_FAILED')
        return dict(state=c['state'], binding_id=c['binding_id'], invocation_started=bool(c['invocation_started']), invocation_generation=c['invocation_generation'], command=json.loads(c['command_json']), acknowledgement=json.loads(c['acknowledgement']) if c['acknowledgement'] else None,
                    receipt=json.loads(c['receipt']) if c['receipt'] else None, delivery=json.loads(c['delivery']) if c['delivery'] else None)

    def delivery(self, a):
        fields(a['D'], 'outbox_id delivery_key')
        for value in a['D'].values(): string(value)
        d = row(self.conn, 'SELECT * FROM session_live_deliveries WHERE outbox_id=?', (a['D']['outbox_id'],))
        missing = {'delivery_claim':'NOT_FOUND', 'delivery_read':'NOT_FOUND', 'delivery_renew':'FENCE_STALE',
                   'delivery_check':'FENCE_STALE', 'delivery_complete':'PLAN_MISMATCH'}.get(self.operation, 'CONFLICT')
        mismatch = {'delivery_read':'NOT_FOUND', 'delivery_renew':'FENCE_STALE', 'delivery_check':'FENCE_STALE',
                    'journal_write':'PLAN_MISMATCH', 'delivery_complete':'PLAN_MISMATCH'}.get(self.operation, 'CONFLICT')
        need(d, missing)
        need(d['scope'] == self.scope and d['delivery_key'] == a['D']['delivery_key'], mismatch)
        # P is authenticated installation identity; actors may differ between
        # domain commands and the delivery coordinator, but never members/owners.
        return d

    @staticmethod
    def lease_result(d):
        return dict(holder_id=d['holder_id'], lease_fence=d['lease_fence'], lease_until_ms=d['lease_until_ms'])

    def lease(self, a, d):
        fields(a['L'], 'holder_id lease_fence lease_until_ms')
        string(a['L']['holder_id']); integer(a['L']['lease_fence'], 1); integer(a['L']['lease_until_ms'])
        need(a['L']['holder_id'] == d['holder_id'] and a['L']['lease_fence'] == d['lease_fence'], 'FENCE_STALE')
        # A concurrent renewal does not invalidate a request from the same
        # holder/fence. Accept only a lease actually issued by this database,
        # and use the current database deadline for liveness.
        issued = json.loads(d['lease_history'])
        need(any(x['kind'] in ('grant','renew') and x['L'] == a['L'] for x in issued), 'FENCE_STALE')
        need(d['lease_until_ms'] > self.now, 'LEASE_EXPIRED')

    @staticmethod
    def ttl(a):
        value = a.get('ttl_ms', 30000)
        integer(value, 10000); need(value <= 120000)
        return value

    def lease_event(self, d, kind, reason=None):
        history = json.loads(d['lease_history'])
        history.append(dict(kind=kind, L=self.lease_result(d), at_ms=self.now, reason=reason))
        self.conn.execute('UPDATE session_live_deliveries SET lease_history=? WHERE outbox_id=?', (canonical(history), d['outbox_id']))

    def delivery_claim(self, a):
        ttl = self.ttl(a); string(a['holder_id']); hash_value(a['receipt_digest'])
        d = self.delivery(a)
        need(d['receipt_digest'] == a['receipt_digest'], 'CONFLICT')
        need(not d['complete'], 'DELIVERY_COMPLETE')
        if d['lease_until_ms'] > self.now:
            need(d['holder_id'] == a['holder_id'], 'CLAIM_BUSY')
        else:
            self.conn.execute('UPDATE session_live_deliveries SET holder_id=?,lease_fence=lease_fence+1,lease_until_ms=? WHERE outbox_id=?', (a['holder_id'], self.now+ttl, d['outbox_id']))
            d = self.delivery(a)
            self.lease_event(d, 'grant')
        return dict(D=a['D'], L=self.lease_result(d), receipt_digest=d['receipt_digest'], plan_digest=d['plan_digest'], stages=self.stage_summaries(d))

    def delivery_renew(self, a):
        ttl = self.ttl(a); d = self.delivery(a); self.lease(a, d)
        deadline = max(d['lease_until_ms'], self.now+ttl)
        self.conn.execute('UPDATE session_live_deliveries SET lease_until_ms=? WHERE outbox_id=?', (deadline, d['outbox_id']))
        renewed = dict(d, lease_until_ms=deadline)
        self.lease_event(renewed, 'renew')
        return self.lease_result(renewed)

    def delivery_fence(self, a):
        integer(a['expected_fence'], 1); string(a['reason'])
        need(self.authority.coordinator, 'AUTH_FAILED')
        d = self.delivery(a)
        need(d['lease_fence'] == a['expected_fence'], 'FENCE_STALE')
        need(not d['complete'], 'CONFLICT')
        self.conn.execute('UPDATE session_live_deliveries SET lease_fence=lease_fence+1,lease_until_ms=0 WHERE outbox_id=?', (d['outbox_id'],))
        self.lease_event(dict(d, lease_fence=d['lease_fence']+1, lease_until_ms=0), 'fence', a['reason'])
        return dict(lease_fence=d['lease_fence']+1, lease_until_ms=0)

    def delivery_check(self, a):
        d = self.delivery(a); self.lease(a, d)
        return dict(current=True, L=self.lease_result(d))

    def ensure_epoch(self, a, code='SURFACE_MOVED'):
        # A newer installed scope owns new publication. Old sealed deliveries
        # remain readable/reconcilable, but cannot bind a newer current slot.
        scopes = rows(self.conn, 'SELECT scope FROM session_live_scopes WHERE session_id=?', (self.sid,))
        need(all(json.loads(x['scope'])['epoch'] <= a['S']['epoch'] for x in scopes if json.loads(x['scope'])['chat_id'] == a['S']['chat_id']), code)

    def current_slot(self, a, slot):
        # Epoch takeover retains the previous physical slot until the new
        # epoch atomically acknowledges retirement and replacement.
        return row(self.conn, "SELECT * FROM session_live_slots WHERE session_id=? AND slot=? AND json_extract(scope,'$.chat_id')=? ORDER BY epoch DESC LIMIT 1",
                   (self.sid, slot, a['S']['chat_id']))

    def delivery_bind(self, a):
        for key in ('frame_digest', 'receipt_digest', 'plan_digest'): hash_value(a[key])
        d = self.delivery(a); self.lease(a, d)
        need(a['receipt_digest'] == d['receipt_digest'], 'CONFLICT')
        preimage = plan_digest_preimage(a)
        plan = canonical(preimage)
        if d['plan']:
            need(d['plan'] == plan and d['plan_digest'] == a['plan_digest'], 'PLAN_MISMATCH')
            return dict(plan_digest=d['plan_digest'], bindings=[s['B'] for s in self.stage_summaries(d)])
        need(self.current_revision() == d['revision'], 'CONFLICT')
        self.ensure_epoch(a, 'CONFLICT')
        need(type(a['stages']) is list and len(a['stages']) > 0)
        command_binding = row(self.conn, 'SELECT envelope_id FROM session_live_commands WHERE binding_id=?', (d['binding_id'],))
        seen, bindings, operation = {}, [], command_binding['envelope_id']
        for ordinal, s in enumerate(a['stages']):
            fields(s, 'K payload_digest target slot mode depends_on protection allowed_outcomes notification')
            key = stage_key(s['K']); hash_value(s['payload_digest']); protection_shape(s['protection'])
            need(s['K']['chat_id'] == a['S']['chat_id'] and s['K']['epoch'] == a['S']['epoch'], 'CONFLICT')
            need(s['K']['op'] == operation and s['K']['stage_id'] not in seen, 'CONFLICT')
            if s['slot'] == 'answer':
                need('answer' in self.config['transient_slots'] and operation.startswith('turn:') and
                     s['mode'] == 'send' and s['target'] is None)
            else:
                need(s['slot'] in self.config['slots'] and s['mode'] in ('send', 'edit', 'retire', 'delete'))
            need(type(s['depends_on']) is list and all(type(x) is str for x in s['depends_on']) and len(set(s['depends_on'])) == len(s['depends_on']))
            need(all(x in seen for x in s['depends_on']), 'PLAN_MISMATCH')
            need(s['depends_on'] == [x for x in seen if x in s['depends_on']], 'PLAN_MISMATCH')
            outcomes = s['allowed_outcomes']
            permitted = ('ok',) if s['mode'] == 'send' else OUTCOMES if s['mode'] == 'delete' else OUTCOMES[:3] if s['mode'] == 'retire' else OUTCOMES[:2]
            need(type(outcomes) is list and outcomes and outcomes == [x for x in OUTCOMES if x in outcomes] and all(x in permitted for x in outcomes))
            if s['target'] is not None:
                need(type(s['target']) is dict and len(s['target']) == 1)
                if 'message_id' in s['target']: integer(s['target']['message_id'], 1)
                else:
                    fields(s['target'], 'stage_id')
                    need(s['target']['stage_id'] in s['depends_on'])
                    need(seen[s['target']['stage_id']]['slot'] != 'answer', 'PLAN_MISMATCH')
            need((s['mode'] == 'send') == (s['target'] is None))
            need(s['protection']['chat_id'] == a['S']['chat_id'], 'CONFLICT')
            if s['mode'] == 'send': need(s['protection']['message_id'] is None, 'CONFLICT')
            if s['mode'] == 'delete':
                self.deletion_allowed(a, s, 'CONFLICT')
            if s['mode'] in ('retire', 'edit') and s['target'] and 'message_id' in s['target']:
                current = self.current_slot(a, s['slot'])
                need(current and current['message_id'] == s['target']['message_id'], 'CONFLICT')
                previous = json.loads(current['protection'])
                need(s['protection']['message_id'] == current['message_id'], 'CONFLICT')
                if s['mode'] == 'retire':
                    need(s['protection'] == previous, 'CONFLICT')
                if previous['protected_guidance']:
                    need(s['protection']['protected_guidance'] and s['protection']['body_digest'] == previous['body_digest'], 'CONFLICT')
            b = dict(K=s['K'], D=a['D'], plan_digest=a['plan_digest'], payload_digest=s['payload_digest'])
            self.conn.execute('INSERT INTO session_live_stages(stage_key,outbox_id,ordinal,binding,spec,state,session_id) VALUES(?,?,?,?,?,?,?)', (key, d['outbox_id'], ordinal, canonical(b), canonical(s), 'pending', self.sid))
            seen[s['K']['stage_id']] = s; bindings.append(b)
        self.validate_manifest(a, seen)
        answers = [s for s in a['stages'] if s['slot'] == 'answer']
        need(len(answers) <= 1, 'PLAN_MISMATCH')
        if answers and self.config['action_slot']:
            answer_id = answers[0]['K']['stage_id']
            members = {n for g in a['branch_manifest']['groups'] for c in g['choices'] for n in c['stage_ids']}
            ancestors = {}
            for name, spec in seen.items():
                ancestors[name] = set(spec['depends_on'])
                for dep in spec['depends_on']: ancestors[name].update(ancestors[dep])
            # Both sends are mandatory; a losing branch cannot discharge R3.
            need(answer_id not in members and any(
                s['slot'] == self.config['action_slot'] and s['mode'] == 'send' and
                answer_id in ancestors[s['K']['stage_id']] and s['K']['stage_id'] not in members
                for s in a['stages']), 'PLAN_MISMATCH')
        need(a['budgets'] == plan_budgets(a['stages'], a['branch_manifest']), 'PLAN_MISMATCH')
        self.validate_budget_limits(a, operation)
        need(digest(preimage) == a['plan_digest'], 'PLAN_MISMATCH')
        self.conn.execute('UPDATE session_live_deliveries SET plan_digest=?,plan=?,frame_digest=? WHERE outbox_id=?', (a['plan_digest'], plan, a['frame_digest'], d['outbox_id']))
        return dict(plan_digest=a['plan_digest'], bindings=bindings)

    def validate_budget_limits(self, a, operation):
        budgets = a['budgets']
        need(all(n <= 2 for n in budgets['max_edits_by_slot'].values()), 'PLAN_MISMATCH')
        # Framework F1 1.6 permits a tap close send plus an action-slot repost
        # after retirement; due permits only the repost. Close can contain
        # one close send plus three end sends. These are
        # only unconditional ceilings: bind has no frame-kind discriminator
        # for the stricter close/end and conditional repost rules (F1 1.6).
        prefix = operation.split(':', 1)[0]
        maximum = {'open': len(self.config['slots']), 'tap': 2, 'due': 1,
                   'turn': 2, 'close': 4}.get(prefix)
        need(maximum is not None and budgets['max_sends'] <= maximum, 'PLAN_MISMATCH')
        # Notification choices are resolved by framework policy, including
        # owner overrides. Their exact maximum is checked above, not replaced
        # with the default silent/loud policy here.
        if prefix == 'open':
            sends = [s for s in a['stages'] if s['mode'] == 'send']
            need(len({s['slot'] for s in sends}) == len(sends), 'PLAN_MISMATCH')
            need(all(self.current_slot(a, s['slot']) is None for s in sends), 'PLAN_MISMATCH')

    def validate_manifest(self, a, stages):
        manifest = a['branch_manifest']
        fields(manifest, 'schema groups')
        need(manifest['schema'] == 'delivery_branches/1' and type(manifest['groups']) is list)
        members, groups, firsts = {}, set(), []
        order = {name:i for i,name in enumerate(stages)}
        for g in manifest['groups']:
            fields(g, 'group_id default_choice_id replacement_choice_id choices default_when replacement_when unselected_outcome')
            for k in ('group_id','default_choice_id','replacement_choice_id'): string(g[k])
            need(g['group_id'] not in groups and g['default_choice_id'] != g['replacement_choice_id'])
            groups.add(g['group_id'])
            need(g['unselected_outcome'] == 'branch_not_selected' and type(g['choices']) is list and len(g['choices']) == 2)
            group_members = []
            for c, expected in zip(g['choices'], (g['default_choice_id'],g['replacement_choice_id'])):
                fields(c, 'choice_id stage_ids')
                need(c['choice_id'] == expected)
                ids = c['stage_ids']
                need(type(ids) is list and ids and all(type(n) is str and n in stages for n in ids))
                need(ids == sorted(set(ids), key=order.get))
                for n in ids:
                    need(n not in members)
                    members[n] = (g['group_id'], c['choice_id']); group_members.append(n)
            firsts.append(min(order[n] for n in group_members))
            default, replacement = g['default_when'], g['replacement_when']
            fields(default, 'stage_id results')
            fields(replacement, 'stage_ids result target evidence quiescence_required')
            need(default['stage_id'] in g['choices'][0]['stage_ids'] and default['results'] == ['ok','unchanged'])
            final = stages[default['stage_id']]
            need(final['mode'] == 'edit' and final['slot'] == self.config['action_slot'] and final['allowed_outcomes'] == ['ok','unchanged'])
            need(g['choices'][0]['stage_ids'] == [default['stage_id']])
            fields(replacement['target'], 'message_id'); integer(replacement['target']['message_id'], 1)
            need(final['target'] == replacement['target'] and replacement['result'] == 'absent' and
                 replacement['evidence'] == 'authoritative_target_absence' and replacement['quiescence_required'] is True)
            triggers = replacement['stage_ids']
            need(type(triggers) is list and triggers and all(type(n) is str and n in stages for n in triggers))
            need(triggers == sorted(set(triggers), key=order.get))
            for n in triggers:
                t = stages[n]
                need(t['target'] == replacement['target'] and t['slot'] == final['slot'] and
                     (n == default['stage_id'] or (t['mode'] == 'retire' and n not in members)))
                if t['mode'] == 'retire': need('absent' in t['allowed_outcomes'])
            repl = [stages[n] for n in g['choices'][1]['stage_ids']]
            sends = [t for t in repl if t['mode'] == 'send']
            need(len(sends) == 1 and sends[0]['slot'] == final['slot'] and sends[0]['allowed_outcomes'] == ['ok'])
            need(all(t is sends[0] or (t['mode'] == 'delete' and t['target'] == replacement['target'] and sends[0]['K']['stage_id'] in t['depends_on']) for t in repl))
            # This version has one final edit and one action repost with shared
            # retirement; no branch can need a losing member's message ID.
            shared_retire = [n for n in final['depends_on'] if stages[n]['mode'] == 'retire' and stages[n]['target'] == replacement['target']]
            need(shared_retire and any(n in sends[0]['depends_on'] for n in shared_retire), 'PLAN_MISMATCH')
        need(firsts == sorted(firsts))
        for name, spec in stages.items():
            owner = members.get(name)
            for dep in spec['depends_on']:
                other = members.get(dep)
                if other and other != owner:
                    need(not (spec['target'] and spec['target'].get('stage_id') == dep), 'PLAN_MISMATCH')
                    # Default/provisional execution cannot wait on a losing
                    # replacement. Shared post-branch stages may wait on both outcomes.
                    need(owner is None or (owner[0] == other[0] and
                         any(g['group_id'] == owner[0] and owner[1] == g['replacement_choice_id'] for g in manifest['groups'])), 'PLAN_MISMATCH')
        for g in manifest['groups']:
            pending = list(stages[g['default_when']['stage_id']]['depends_on'])
            visited = set()
            while pending:
                n = pending.pop()
                if n in visited: continue
                visited.add(n)
                need(n not in members, 'PLAN_MISMATCH')
                pending.extend(stages[n]['depends_on'])
            for n in g['replacement_when']['stage_ids']:
                need(n == g['default_when']['stage_id'] or n not in members)

    @staticmethod
    def manifest(d):
        return json.loads(d['plan']).get('branch_manifest', EMPTY_BRANCHES) if d['plan'] else EMPTY_BRANCHES

    def selections(self, d):
        found = {r['group_id']:json.loads(r['selection']) for r in rows(self.conn,
            'SELECT * FROM session_live_selections WHERE outbox_id=?', (d['outbox_id'],))}
        return [found[g['group_id']] for g in self.manifest(d)['groups'] if g['group_id'] in found]

    def membership(self, d, s):
        name = json.loads(s['spec'])['K']['stage_id']
        for g in self.manifest(d)['groups']:
            for c in g['choices']:
                if name in c['stage_ids']:
                    selected = next((v for v in self.selections(d) if v['group_id'] == g['group_id']), None)
                    return g, c, selected
        return None, None, None

    def save_evidence(self, d, kind, value):
        self.conn.execute('INSERT OR IGNORE INTO session_live_evidence(digest,kind,value,outbox_id,session_id) VALUES(?,?,?,?,?)',
            (digest(value), kind, canonical(value), d['outbox_id'], self.sid))

    def evidence_export(self, d):
        return [dict(digest=r['digest'], kind=r['kind'], value=json.loads(r['value'])) for r in rows(self.conn,
            'SELECT * FROM session_live_evidence WHERE outbox_id=? ORDER BY rowid', (d['outbox_id'],))]

    def imported(self, dg, kind, supplied):
        found = next((v for v in supplied if digest(v) == dg), None)
        if found is None:
            r = row(self.conn, 'SELECT value FROM session_live_evidence WHERE digest=? AND kind=?', (dg,kind))
            if r: found = json.loads(r['value'])
        need(found is not None, 'RECEIPT_MISMATCH')
        return found

    def import_receipt_evidence(self, a, s, r):
        d = self.delivery(a)
        dg = r.get('evidence_digest')
        if r['result'] == 'branch_not_selected':
            # The stored selection, checked by losing_record, authenticates the
            # trigger evidence, which belongs to a different K/seq.
            self.save_evidence(d, 'receipt', r)
            self.save_evidence(d, 'receipt_export', self.authority.export('receipt', r))
            return
        need(dg is not None or r['result'] not in ('absent','retained_cleanup','rate_limited'), 'RECEIPT_MISMATCH')
        if dg is not None:
            value = self.imported(dg, 'evidence', self.authority.evidence)
            need(type(value) is dict)
            if 'record' in value:
                fields(value, 'record evidence')
                rec = value['record']
                fields(rec, 'authority_id K seq plan_digest payload_digest target resolution message_id evidence_digest quiescence issued_at_ms', 'auth')
                authenticate_recovery(rec, self.authority)
                stage_key(rec['K']); integer(rec['seq'], 1)
                hash_value(rec['plan_digest']); hash_value(rec['payload_digest']); hash_value(rec['evidence_digest'])
                if rec['message_id'] is not None: integer(rec['message_id'], 1)
                if rec['target'] is not None:
                    fields(rec['target'], 'message_id'); integer(rec['target']['message_id'], 1)
                need(rec['resolution'] in ('ok','unchanged','absent'), 'RECEIPT_MISMATCH')
                need(rec['K'] == r['K'] and rec['seq'] == r['seq'] and rec['plan_digest'] == d['plan_digest'] and
                     rec['payload_digest'] == r['payload_digest'] and rec['target'] == r['target'] and
                     rec['resolution'] == r['result'] and rec['message_id'] == r['message_id'] and
                     rec['evidence_digest'] == digest(value['evidence']), 'RECEIPT_MISMATCH')
                need(rec['quiescence'] is True and type(value['evidence']) is dict and value['evidence'], 'SURFACE_PENDING')
                integer(rec['issued_at_ms']); need(rec['issued_at_ms'] <= self.now, 'RECEIPT_MISMATCH')
            else:
                # Definitive native 429/delete refusal from the protected export.
                fields(value, 'K seq plan_digest payload_digest target result observation')
                need(all(value[k] == r[k] for k in ('K','seq','payload_digest','target','result')) and value['plan_digest'] == d['plan_digest'], 'RECEIPT_MISMATCH')
                need(r['result'] in ('rate_limited','retained_cleanup'), 'RECEIPT_MISMATCH')
                stage_key(value['K']); integer(value['seq'], 1)
                if value['target'] is not None:
                    fields(value['target'], 'message_id'); integer(value['target']['message_id'], 1)
                observation = value['observation']
                need(type(observation) is dict and type(observation.get('raw_response')) is str)
                if 'diagnostic' in observation: string(observation['diagnostic'])
                if r['result'] == 'rate_limited':
                    fields(observation, 'response_received_at_ms retry_after raw_response', 'diagnostic')
                    integer(observation['response_received_at_ms'])
                    if observation['retry_after'] is not None: integer(observation['retry_after'])
                    need(observation['response_received_at_ms'] <= self.now and observation['retry_after'] == r['retry_after'], 'RECEIPT_MISMATCH')
                    delay = r['retry_after']
                    if delay is None:
                        string(observation.get('diagnostic'))
                        delay = 60
                    integer(delay)
                    need(type(r['next_attempt_at_ms']) is int and r['next_attempt_at_ms'] >= observation['response_received_at_ms'] + delay*1000, 'RECEIPT_MISMATCH')
                else:
                    fields(observation, 'permanent_delete_prohibition raw_response')
                    need(observation['permanent_delete_prohibition'] is True, 'RECEIPT_MISMATCH')
            self.save_evidence(d, 'evidence', value)
            self.save_evidence(d, 'evidence_export', self.authority.export('evidence', value))
        self.save_evidence(d, 'receipt', r)
        self.save_evidence(d, 'receipt_export', self.authority.export('receipt', r))

    def delivery_select(self, a):
        d = self.delivery(a); self.lease(a, d)
        v = a['selection']
        fields(v, 'schema S D plan_digest group_id choice_id trigger lease_fence selection_digest auth')
        fields(v['auth'], 'publisher_id mac')
        fields(v['trigger'], 'K seq result payload_digest target receipt_digest evidence_digest')
        need(v['schema'] == 'delivery_branch_selection/1')
        integer(v['lease_fence'], 1); integer(v['trigger']['seq'], 1)
        hash_value(v['selection_digest']); hash_value(v['trigger']['receipt_digest'])
        need(v['auth']['publisher_id'] == a['P']['publisher_id'], 'AUTH_FAILED')
        expected = selection_signature(v, self.authority.key)
        need(v['selection_digest'] == expected['selection_digest'], 'RECEIPT_MISMATCH')
        need(type(v['auth']['mac']) is str and hmac.compare_digest(v['auth']['mac'], expected['auth']['mac']), 'AUTH_FAILED')
        need(v['S'] == a['S'] and v['D'] == a['D'] and v['plan_digest'] == d['plan_digest'], 'PLAN_MISMATCH')
        old = row(self.conn, 'SELECT selection FROM session_live_selections WHERE outbox_id=? AND group_id=?', (d['outbox_id'],v['group_id']))
        if old:
            need(old['selection'] == canonical(v), 'CONFLICT')
            return dict(selection_digest=v['selection_digest'], duplicate=True)
        need(v in self.authority.selections, 'RECEIPT_MISMATCH')
        need(v['lease_fence'] == a['L']['lease_fence'], 'RECEIPT_MISMATCH')
        g = next((g for g in self.manifest(d)['groups'] if g['group_id'] == v['group_id']), None)
        need(g is not None, 'PLAN_MISMATCH')
        trigger = v['trigger']
        stage = row(self.conn, 'SELECT * FROM session_live_stages WHERE stage_key=? AND outbox_id=?', (stage_key(trigger['K']), d['outbox_id']))
        need(stage is not None, 'PLAN_MISMATCH')
        proof = self.imported(trigger['receipt_digest'], 'receipt_export',
            [self.authority.export('receipt', r) for r in self.authority.receipts])
        fields(proof, 'kind value mac')
        receipt = proof['value']
        need(proof == self.authority.export('receipt', receipt), 'AUTH_FAILED')
        need(all(trigger[k] == receipt[k] for k in ('K','seq','result','payload_digest','target','evidence_digest')), 'RECEIPT_MISMATCH')
        # Persist the original trigger, without completing a missing final edit.
        self.verify_receipt(dict(a, B=json.loads(stage['binding'])), stage, receipt, ('ok','unchanged','absent'))
        if v['choice_id'] == g['default_choice_id']:
            self.transport_eligible(a, d, stage)
            need(trigger['K']['stage_id'] == g['default_when']['stage_id'] and trigger['result'] in g['default_when']['results'] and trigger['evidence_digest'] is None, 'TRANSITION_INVALID')
        elif v['choice_id'] == g['replacement_choice_id']:
            need(trigger['K']['stage_id'] in g['replacement_when']['stage_ids'] and trigger['result'] == 'absent' and trigger['target'] == g['replacement_when']['target'] and trigger['evidence_digest'] is not None, 'TRANSITION_INVALID')
            # No successful or unresolved branch I/O may be hidden by selection.
            for c in g['choices']:
                for name in c['stage_ids']:
                    member = self.named_stage(d, name)
                    need(member['state'] not in TERMINAL, 'TRANSITION_INVALID')
                    if member['state'] in ('inflight','uncertain'):
                        need(member['stage_key'] == stage['stage_key'] and member['seq'] == receipt['seq'], 'TRANSITION_INVALID')
        else: raise DeliveryError('TRANSITION_INVALID')
        self.dependencies(stage)
        if stage['seq'] == 0:
            self.conn.execute('UPDATE session_live_stages SET seq=?,attempt_fence=? WHERE stage_key=?', (receipt['seq'],receipt['lease_fence'],stage['stage_key']))
        self.conn.execute('INSERT INTO session_live_selections VALUES(?,?,?,?,?)', (d['outbox_id'],v['group_id'],v['selection_digest'],canonical(v),self.sid))
        return dict(selection_digest=v['selection_digest'], duplicate=False)

    def named_stage(self, d, name):
        return next(s for s in rows(self.conn, 'SELECT * FROM session_live_stages WHERE outbox_id=?', (d['outbox_id'],)) if json.loads(s['spec'])['K']['stage_id'] == name)

    def transport_eligible(self, a, d, s):
        g, c, selected = self.membership(d, s)
        if not g: return
        if selected:
            need(c['choice_id'] == selected['choice_id'], 'TRANSITION_INVALID')
            if selected['choice_id'] == g['replacement_choice_id']:
                need(all(self.named_stage(d,n)['state'] == 'satisfied_not_selected' for choice in g['choices'] if choice['choice_id'] != selected['choice_id'] for n in choice['stage_ids']), 'TRANSITION_INVALID')
        else:
            need(c['choice_id'] == g['default_choice_id'], 'TRANSITION_INVALID')
            for name in g['replacement_when']['stage_ids']:
                trigger = self.named_stage(d, name)
                need(trigger['state'] != 'satisfied_absent', 'TRANSITION_INVALID')
                known = [json.loads(x['value']) for x in rows(self.conn, "SELECT value FROM session_live_evidence WHERE outbox_id=? AND kind='receipt'", (d['outbox_id'],))]
                need(not any(r['K'] == json.loads(trigger['spec'])['K'] and r['result'] == 'absent' for r in known + list(self.authority.receipts)), 'TRANSITION_INVALID')

    def terminal_allowed(self, d, s, receipt):
        spec = json.loads(s['spec'])
        need(receipt['result'] in spec.get('allowed_outcomes', ('ok','unchanged')), 'TRANSITION_INVALID')
        g, c, selected = self.membership(d, s)
        if g:
            need(selected and c['choice_id'] == selected['choice_id'], 'TRANSITION_INVALID')
            if c['choice_id'] == g['replacement_choice_id']:
                need(all(self.named_stage(d,n)['state'] == 'satisfied_not_selected' for choice in g['choices'] if choice['choice_id'] != c['choice_id'] for n in choice['stage_ids']), 'TRANSITION_INVALID')

    def losing_record(self, a, d, s, receipt, complete=False):
        g, c, selected = self.membership(d, s)
        need(g and selected and c['choice_id'] != selected['choice_id'], 'TRANSITION_INVALID')
        need(receipt['lease_fence'] >= selected['lease_fence'], 'RECEIPT_MISMATCH')
        need(receipt['selection_digest'] == selected['selection_digest'] and receipt['evidence_digest'] == selected['trigger']['evidence_digest'], 'RECEIPT_MISMATCH')
        name = json.loads(s['spec'])['K']['stage_id']
        need(all(self.named_stage(d,n)['state'] == 'satisfied_not_selected' for n in c['stage_ids'][:c['stage_ids'].index(name)]), 'TRANSITION_INVALID')
        if not complete and s['state'] in ('inflight','uncertain'):
            trigger = selected['trigger']
            need(trigger['K'] == json.loads(s['binding'])['K'] and trigger['seq'] == s['seq'] and trigger['result'] == 'absent', 'TRANSITION_INVALID')
        need(receipt['result'] == 'branch_not_selected' and receipt['message_id'] is None, 'RECEIPT_MISMATCH')
        # Receipt import authenticates prior grants, including an old holder's
        # no-I/O checkpoint after a lease takeover. No attempt is synthesized.

    def cleanup_ready(self, a, spec):
        self.deletion_allowed(a, spec, 'CONFLICT')
        message = spec['target']['message_id']
        need(row(self.conn, "SELECT 1 FROM session_live_history WHERE scope=? AND message_id=? AND retirement_receipt IS NOT NULL AND cleanup='delete_planned'", (self.scope,message)), 'TRANSITION_INVALID')
        need(not row(self.conn, 'SELECT 1 FROM session_live_slots WHERE scope=? AND message_id=?', (self.scope,message)), 'TRANSITION_INVALID')

    def deletion_allowed(self, a, s, code='PROTECTED_CONTENT'):
        need(s['target'] and 'message_id' in s['target'], code)
        message = s['target']['message_id']
        need(not row(self.conn, "SELECT 1 FROM session_live_history WHERE session_id=? AND message_id=? AND json_extract(scope,'$.chat_id')=? AND slot='answer'",
                     (self.sid, message, a['S']['chat_id'])), code)
        records = rows(self.conn, "SELECT protection FROM session_live_slots WHERE session_id=? AND message_id=? AND json_extract(scope,'$.chat_id')=? UNION ALL SELECT protection FROM session_live_history WHERE session_id=? AND message_id=? AND json_extract(scope,'$.chat_id')=?", (self.sid, message, a['S']['chat_id'], self.sid, message, a['S']['chat_id']))
        need(records and all(json.loads(x['protection']) == s['protection'] and json.loads(x['protection'])['protected_guidance'] is False for x in records), code)

    def stage(self, a, d, code='PLAN_MISMATCH'):
        fields(a['B'], 'K D plan_digest payload_digest')
        key = stage_key(a['B']['K'])
        s = row(self.conn, 'SELECT * FROM session_live_stages WHERE stage_key=?', (key,))
        need(s and s['outbox_id'] == d['outbox_id'] and s['binding'] == canonical(a['B']), code)
        return s

    def dependencies(self, s, code='TRANSITION_INVALID'):
        spec = json.loads(s['spec'])
        for name in spec['depends_on']:
            key = dict(spec['K'], stage_id=name)
            dep = row(self.conn, 'SELECT * FROM session_live_stages WHERE stage_key=?', (canonical(key),))
            ds = json.loads(dep['spec'])
            need(dep['state'] in TERMINAL and (dep['state'] == 'satisfied_not_selected' or ds['mode'] not in ('send','edit') or dep['rebound']), code)

    def verify_receipt(self, a, s, r, results):
        fields(r, 'K seq message_id target result payload_digest lease_fence', 'evidence_digest retry_after next_attempt_at_ms selection_digest')
        spec = json.loads(s['spec'])
        if 'allowed_outcomes' in spec:
            need(all(k in r for k in ('evidence_digest','retry_after','next_attempt_at_ms','selection_digest')))
        for k in ('evidence_digest', 'selection_digest'):
            if r.get(k) is not None: hash_value(r[k])
        if r.get('retry_after') is not None: integer(r['retry_after'])
        if r.get('next_attempt_at_ms') is not None: integer(r['next_attempt_at_ms'])
        if r['result'] != 'rate_limited':
            need(r.get('retry_after') is None and r.get('next_attempt_at_ms') is None, 'RECEIPT_MISMATCH')
        if r['result'] != 'branch_not_selected': need(r.get('selection_digest') is None, 'RECEIPT_MISMATCH')
        stage_key(r['K']); integer(r['seq'], 1); integer(r['lease_fence'], 1)
        need(r['result'] in results, 'RECEIPT_MISMATCH')
        if r['message_id'] is not None: integer(r['message_id'], 1)
        if r['target'] is not None:
            fields(r['target'], 'message_id'); integer(r['target']['message_id'], 1)
        self.authority.receipt(r)
        spec = json.loads(s['spec'])
        target = spec['target']
        if target and 'stage_id' in target:
            prior = row(self.conn, 'SELECT receipt FROM session_live_stages WHERE stage_key=?', (canonical(dict(spec['K'], stage_id=target['stage_id'])),))
            need(prior and prior['receipt'] and json.loads(prior['receipt'])['message_id'] is not None, 'RECEIPT_MISMATCH')
            target = dict(message_id=json.loads(prior['receipt'])['message_id'])
        need(r['K'] == a['B']['K'] and r['payload_digest'] == a['B']['payload_digest'] and r['target'] == target, 'RECEIPT_MISMATCH')
        grants = json.loads(self.delivery(a)['lease_history'])
        need(r['lease_fence'] <= a['L']['lease_fence'] and any(x['kind'] == 'grant' and x['L']['lease_fence'] == r['lease_fence'] for x in grants), 'RECEIPT_MISMATCH')
        if r['result'] == 'retained_cleanup':
            need(target is not None and (r['message_id'] is None or r['message_id'] == target['message_id']), 'RECEIPT_MISMATCH')
        if target and r['result'] in ('ok','unchanged'): need(r['message_id'] == target['message_id'], 'RECEIPT_MISMATCH')
        if r['result'] in ('ok','unchanged'): need(r['message_id'] is not None, 'RECEIPT_MISMATCH')
        if r['result'] in ('absent','branch_not_selected'): need(r['message_id'] is None, 'RECEIPT_MISMATCH')
        if s['seq'] and r['result'] != 'branch_not_selected':
            need(r['seq'] == s['seq'] and r['lease_fence'] == s['attempt_fence'], 'RECEIPT_MISMATCH')
        self.import_receipt_evidence(a, s, r)
        return r

    def replay(self, a):
        string(a['event_id'])
        old = row(self.conn, 'SELECT * FROM session_live_events WHERE event_id=?', (a['event_id'],))
        # A new current holder can acknowledge an old receipt. L is attempt
        # metadata, not the immutable event identity or successful CAS bytes.
        identity = canonical({k:v for k,v in a.items() if k != 'L'})
        if old:
            need(stored_request_equal(old['request'], json.loads(identity), self.sid), 'CONFLICT')
            return dict(json.loads(old['result']), duplicate=True)
        return None

    def record(self, a, d, s, event, seq, receipt, result):
        spec = json.loads(s['spec'])
        state = PROJECTION[event]
        if event == 'delivered' and receipt['result'] == 'unchanged': state = 'unchanged'
        # The old reconciled enum requires a delivered message id. An absent
        # result projects to a pending request, with lossless evidence retained
        # in session_live_events; never invent a Telegram message id.
        message = receipt['message_id'] if receipt else None
        ack = None
        if state in ('acknowledged','unchanged','reconciled'):
            ack = dict(receipt_id=digest(receipt), message_id=str(message),
                       body_digest=spec['protection']['body_digest'], markup_digest=digest(a.get('selector_bindings', [])))
        typed = json.loads(d['typed_outbox'])
        outbox = row(self.conn, 'SELECT session_epoch,rest_epoch FROM session_outbox WHERE session_id=? AND revision=? AND frame_kind=?',
                     (typed['session_id'], typed['revision'], typed['frame_kind']))
        need(outbox, 'CONFLICT')
        cmd = row(self.conn, 'SELECT command_json FROM session_live_commands WHERE binding_id=?', (d['binding_id'],))
        operation = 'reconcile' if event == 'absence_confirmed' else 'clear_markup' if spec['mode'] == 'retire' else spec['mode']
        entry = DeliveryJournalEntry(dict(operation_id=a['event_id'], session_id=self.sid,
            command_id=json.loads(cmd['command_json'])['command_id'], revision=d['revision'], frame_kind=typed['frame_kind'],
            sender='publisher', surface_kind=None if spec['slot'] == 'answer' else self.config['slots'][spec['slot']], message_id=str(message) if message is not None else None,
            callback_digest=None, session_epoch=outbox['session_epoch'], rest_epoch=outbox['rest_epoch'], operation=operation,
            request_digest=digest({k:v for k,v in a.items() if k != 'L'}), state=state, acknowledged_result=ack,
            latency_ms=0, error=None, artifact_digest=d['frame_digest'], delivery_key=d['delivery_key'], lease_fence=a['L']['lease_fence'])).to_dict()
        cols=list(entry)
        self.conn.execute('INSERT INTO session_delivery_journal('+','.join(cols)+') VALUES('+','.join('?' for _ in cols)+')',
                          tuple(canonical(entry[k]) if k == 'acknowledged_result' and entry[k] is not None else entry[k] for k in cols))
        attempt_fence = receipt['lease_fence'] if receipt else s['attempt_fence'] if event == 'delivery_unknown' else a['L']['lease_fence']
        self.conn.execute('INSERT INTO session_live_events(event_id,outbox_id,stage_key,request,result,entry_id,attempt_fence,acknowledgement_fence,session_id) VALUES(?,?,?,?,?,?,?,?,?)',
            (a['event_id'], d['outbox_id'], s['stage_key'], canonical({k:v for k,v in a.items() if k != 'L'}), canonical(result),
             result['entry_id'], attempt_fence, a['L']['lease_fence'], self.sid))
        self.conn.execute('UPDATE session_live_stages SET state=?,seq=?,attempt_fence=?,receipt=? WHERE stage_key=?',
            (EVENT_STATES[event], seq, attempt_fence, canonical(receipt) if receipt else s['receipt'], s['stage_key']))
        if event == 'rate_limited':
            self.conn.execute('UPDATE session_live_stages SET next_attempt_at_ms=? WHERE stage_key=?', (receipt['next_attempt_at_ms'], s['stage_key']))

    def journal_write(self, a):
        d = self.delivery(a); self.lease(a, d); s = self.stage(a, d)
        old = self.replay(a)
        if old: return old
        need(a['event'] in EVENT_STATES); integer(a['seq'])
        need(a['event_id'] == event_key(a['B']['K'], a['seq'], a['event']), 'CONFLICT')
        event, seq, spec = a['event'], a['seq'], json.loads(s['spec'])
        need(s['state'] != 'waiting_retry' or event in ('attempt_started','branch_not_selected'), 'TRANSITION_INVALID')
        need(s['state'] not in TERMINAL, 'TRANSITION_INVALID')
        receipt = a.get('receipt')
        if event == 'planned':
            need(seq == 0 and s['seq'] == 0 and s['state'] == 'pending' and receipt is None, 'TRANSITION_INVALID')
        elif event == 'attempt_started':
            need(s['state'] in ('pending','waiting_retry') and seq == s['seq']+1 and receipt is None, 'TRANSITION_INVALID')
            need(s['next_attempt_at_ms'] is None or self.now >= s['next_attempt_at_ms'], 'TRANSITION_INVALID')
            self.transport_eligible(a, d, s)
            self.dependencies(s)
        elif event == 'branch_not_selected':
            need(receipt is not None, 'RECEIPT_MISMATCH')
            self.verify_receipt(a, s, receipt, ('branch_not_selected',))
            need(seq == receipt['seq'] == s['seq']+1, 'TRANSITION_INVALID')
            self.losing_record(a, d, s, receipt)
        elif event == 'delivery_unknown' and receipt is None:
            need(s['state'] == 'inflight' and seq == s['seq'], 'TRANSITION_INVALID')
        else:
            need(receipt is not None, 'RECEIPT_MISMATCH')
            allowed = {'delivered': ('ok','unchanged'), 'delivery_unknown': ('uncertain',),
                       'attempt_failed': ('failed',), 'absence_confirmed': ('absent',),
                       'target_absent': ('absent',), 'cleanup_retained': ('retained_cleanup',), 'rate_limited': ('rate_limited',)}
            self.verify_receipt(a, s, receipt, allowed[event])
            need(seq == receipt['seq'], 'RECEIPT_MISMATCH')
            self.dependencies(s)
            if event in ('delivered','target_absent','cleanup_retained'):
                self.terminal_allowed(d, s, receipt)
            if event == 'target_absent': need(spec['mode'] in ('retire','delete'), 'TRANSITION_INVALID')
            if event == 'cleanup_retained':
                need(spec['mode'] == 'delete', 'TRANSITION_INVALID')
                self.cleanup_ready(a, spec)
            if event == 'delivered':
                need(spec['mode'] in ('retire','delete'), 'TRANSITION_INVALID')
            elif event == 'absence_confirmed': need(s['state'] in ('inflight','uncertain') or (s['state'] == 'pending' and s['seq'] == 0), 'TRANSITION_INVALID')
            elif event == 'attempt_failed': need(s['state'] == 'inflight' or (s['state'] == 'pending' and s['seq'] == 0), 'TRANSITION_INVALID')
        result = dict(entry_id=secrets.token_hex(16), stage_state=EVENT_STATES[event], duplicate=False)
        self.record(a, d, s, event, seq, receipt, result)
        if event in ('delivered','target_absent'): self.retire_or_delete(a, spec, receipt)
        if event == 'cleanup_retained':
            self.conn.execute("UPDATE session_live_history SET cleanup='retained_cleanup' WHERE scope=? AND message_id=?", (self.scope, spec['target']['message_id']))
        return result

    def retire_or_delete(self, a, spec, receipt):
        message = receipt['target']['message_id'] if receipt['result'] == 'absent' else receipt['message_id']
        if spec['mode'] == 'retire':
            current = self.current_slot(a, spec['slot'])
            if current and current['message_id'] != message: current = None
            if current:
                self.conn.execute('INSERT INTO session_live_history(history_id,scope,slot,message_id,protection,retirement_receipt,replacement_receipt,cleanup,session_id) VALUES(?,?,?,?,?,?,NULL,?,?)',
                    (secrets.token_hex(16), self.scope, spec['slot'], message, current['protection'], canonical(receipt), 'retained', self.sid))
            self.conn.execute("UPDATE session_live_slots SET markup_active=0 WHERE session_id=? AND message_id=? AND json_extract(scope,'$.chat_id')=?", (self.sid, message, a['S']['chat_id']))
            self.conn.execute("UPDATE session_surfaces SET markup_state='clear' WHERE session_id=? AND chat_id=? AND message_id=?", (self.sid, str(a['S']['chat_id']), str(message)))
            if current:
                self.conn.execute("UPDATE session_live_selectors SET state='retired' WHERE scope=? AND slot=? AND state='active'", (current['scope'], spec['slot']))
        else:
            self.deletion_allowed(a, spec, 'CONFLICT')
            need(row(self.conn, "SELECT 1 FROM session_live_history WHERE scope=? AND message_id=? AND retirement_receipt IS NOT NULL AND cleanup='delete_planned'", (self.scope, message)), 'CONFLICT')
            need(not row(self.conn, 'SELECT 1 FROM session_live_slots WHERE scope=? AND message_id=?', (self.scope, message)), 'TRANSITION_INVALID')
            if receipt['result'] == 'absent':
                self.conn.execute("UPDATE session_live_history SET cleanup='satisfied_absent' WHERE scope=? AND message_id=?", (self.scope, message))
            else:
                self.conn.execute('UPDATE session_live_history SET deleted=1 WHERE scope=? AND message_id=?', (self.scope, message))

    def surface_rebind(self, a):
        d = self.delivery(a); self.lease(a, d); s = self.stage(a, d, 'CONFLICT')
        old_result = self.replay(a)
        if old_result: return old_result
        spec = json.loads(s['spec'])
        need(spec['mode'] in ('send','edit'), 'CONFLICT')
        fields(a['expected'], 'slot message_id revision epoch')
        expected = a['expected']; integer(expected['revision'], 1); integer(expected['epoch'], 1)
        if expected['message_id'] is not None: integer(expected['message_id'], 1)
        need(expected['slot'] == spec['slot'] and expected['epoch'] <= a['S']['epoch'], 'SURFACE_MOVED')
        self.ensure_epoch(a)
        self.dependencies(s, 'RECEIPT_MISMATCH')
        r = self.verify_receipt(a, s, a['replacement_receipt'], ('ok','unchanged'))
        self.terminal_allowed(d, s, r)
        need(s['state'] != 'waiting_retry', 'TRANSITION_INVALID')
        need(a['event_id'] == event_key(a['B']['K'], r['seq'], 'delivered'), 'CONFLICT')
        need(s['state'] not in TERMINAL, 'CONFLICT')
        if spec['slot'] == 'answer':
            need(expected == dict(slot='answer', message_id=None, revision=d['revision'], epoch=a['S']['epoch']), 'SURFACE_MOVED')
            need(a['retirement_receipt'] is None and a['selector_bindings'] == [] and
                 a['cleanup'] == 'retained', 'RECEIPT_MISMATCH')
            protection = dict(spec['protection'], message_id=r['message_id'])
            need(a['protection'] == protection, 'RECEIPT_MISMATCH')
            history_id, entry_id = secrets.token_hex(16), secrets.token_hex(16)
            result = dict(slot='answer', message_id=r['message_id'], revision=d['revision'],
                          epoch=a['S']['epoch'], history_id=history_id, entry_id=entry_id, duplicate=False)
            self.record(a, d, s, 'delivered', r['seq'], r, result)
            self.conn.execute('INSERT INTO session_live_history(history_id,scope,slot,message_id,protection,retirement_receipt,replacement_receipt,cleanup,session_id) VALUES(?,?,?,?,?,NULL,?,?,?)',
                (history_id, self.scope, 'answer', r['message_id'], canonical(protection), canonical(r), 'retained', self.sid))
            self.conn.execute('UPDATE session_live_stages SET rebound=1 WHERE stage_key=?', (s['stage_key'],))
            return result
        old = self.current_slot(a, spec['slot'])
        if old is None:
            need(not row(self.conn, 'SELECT 1 FROM session_surfaces WHERE session_id=? AND surface_kind=?', (self.sid, self.config['slots'][spec['slot']])), 'SURFACE_MOVED')
        need((old is None and expected['message_id'] is None and expected['revision'] == d['revision'] and expected['epoch'] == a['S']['epoch']) or
             (old and all(old[k] == expected[k] for k in ('slot','message_id','revision','epoch'))), 'SURFACE_MOVED')
        protection_shape(a['protection'])
        protection = dict(spec['protection'], message_id=r['message_id'])
        need(a['protection'] == protection, 'RECEIPT_MISMATCH')
        need(a['cleanup'] in ('retained','delete_planned'))
        retirement = a['retirement_receipt']
        if old:
            if retirement is None:
                # A keyboard-free non-action message may remain as history
                # when a send replaces its slot. Action-slot reposts, edits,
                # active markup and deletion still need retirement evidence.
                need(spec['mode'] == 'send' and spec['slot'] != self.config['action_slot'] and
                     not old['markup_active'] and r['message_id'] != old['message_id'] and
                     a['cleanup'] == 'retained', 'RECEIPT_MISMATCH')
            else:
                retired = row(self.conn, 'SELECT * FROM session_live_stages WHERE stage_key=?', (stage_key(retirement['K']),))
                need(retired and retired['outbox_id'] == d['outbox_id'], 'RECEIPT_MISMATCH')
                rs = json.loads(retired['spec'])
                need(rs['mode'] == 'retire' and rs['K']['stage_id'] in spec['depends_on'] and retired['state'] in ('acknowledged','satisfied_absent') and json.loads(retired['receipt']) == retirement and retirement['target'] == dict(message_id=old['message_id']), 'RECEIPT_MISMATCH')
            previous = json.loads(old['protection'])
            if old['message_id'] == r['message_id'] and previous['protected_guidance']:
                need(protection['protected_guidance'] and protection['body_digest'] == previous['body_digest'], 'PROTECTED_CONTENT')
            if a['cleanup'] == 'delete_planned':
                need(not previous['protected_guidance'], 'PROTECTED_CONTENT')
                deletes = [json.loads(x['spec']) for x in rows(self.conn, 'SELECT spec FROM session_live_stages WHERE outbox_id=?', (d['outbox_id'],))]
                need(any(x['mode'] == 'delete' and x['target'] == dict(message_id=old['message_id']) and x['protection'] == previous and spec['K']['stage_id'] in x['depends_on'] for x in deletes), 'PROTECTED_CONTENT')
        else:
            need(retirement is None and a['cleanup'] == 'retained', 'RECEIPT_MISMATCH')
        need(type(a['selector_bindings']) is list and len(set(a['selector_bindings'])) == len(a['selector_bindings']))
        prepared = rows(self.conn, "SELECT binding_id,request FROM session_live_selectors WHERE scope=? AND slot=? AND revision=? AND state='prepared'", (self.scope, spec['slot'], d['revision']))
        need(set(a['selector_bindings']) == {x['binding_id'] for x in prepared if json.loads(x['request'])['frame_digest'] == d['frame_digest']}, 'CONFLICT')
        for bid in a['selector_bindings']:
            selector = row(self.conn, 'SELECT * FROM session_live_selectors WHERE binding_id=?', (bid,))
            need(selector and selector['scope'] == self.scope and selector['slot'] == spec['slot'] and selector['revision'] == d['revision'] and selector['state'] == 'prepared', 'CONFLICT')
            need(json.loads(selector['request'])['frame_digest'] == d['frame_digest'], 'CONFLICT')
        history_id, entry_id = secrets.token_hex(16), secrets.token_hex(16)
        result = dict(slot=spec['slot'], message_id=r['message_id'], revision=d['revision'], epoch=a['S']['epoch'], history_id=history_id, entry_id=entry_id, duplicate=False)
        self.record(a, d, s, 'delivered', r['seq'], r, result)
        self.conn.execute('INSERT INTO session_live_history(history_id,scope,slot,message_id,protection,retirement_receipt,replacement_receipt,cleanup,session_id) VALUES(?,?,?,?,?,?,?,?,?)',
            (history_id, self.scope, spec['slot'], old['message_id'] if old else r['message_id'], old['protection'] if old else canonical(protection), canonical(retirement) if retirement else None, canonical(r), a['cleanup'], self.sid))
        self.conn.execute("UPDATE session_live_selectors SET state='retired' WHERE scope=? AND slot=? AND state='active'", (self.scope, spec['slot']))
        for bid in a['selector_bindings']:
            self.conn.execute("UPDATE session_live_selectors SET state='active' WHERE binding_id=?", (bid,))
        active = bool(a['selector_bindings'])
        self.conn.execute('INSERT INTO session_live_slots(scope,slot,message_id,revision,epoch,protection,markup_active,session_id) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(scope,slot) DO UPDATE SET message_id=excluded.message_id,revision=excluded.revision,epoch=excluded.epoch,protection=excluded.protection,markup_active=excluded.markup_active',
            (self.scope, spec['slot'], r['message_id'], d['revision'], a['S']['epoch'], canonical(protection), int(active), self.sid))
        command = row(self.conn, 'SELECT receipt FROM session_live_commands WHERE binding_id=?', (d['binding_id'],))
        surface = Surface(dict(session_id=self.sid, surface_kind=self.config['slots'][spec['slot']], chat_id=str(a['S']['chat_id']), message_id=str(r['message_id']), published_revision=d['revision'],
            body_digest=protection['body_digest'], markup_digest=digest(a['selector_bindings']), markup_state='active' if active else 'clear', delivery_key=d['delivery_key'], receipt_id=json.loads(command['receipt'])['receipt_id'])).to_dict()
        cols=list(surface)
        self.conn.execute('INSERT INTO session_surfaces('+','.join(cols)+') VALUES('+','.join('?' for _ in cols)+') ON CONFLICT(session_id,surface_kind) DO UPDATE SET '+','.join(k+'=excluded.'+k for k in cols[2:]), tuple(surface.values()))
        self.conn.execute('UPDATE session_live_stages SET rebound=1 WHERE stage_key=?', (s['stage_key'],))
        return result

    def stage_summaries(self, d):
        result=[]
        for s in rows(self.conn, 'SELECT * FROM session_live_stages WHERE outbox_id=? ORDER BY ordinal', (d['outbox_id'],)):
            entries = rows(self.conn, 'SELECT event_id,entry_id,result,attempt_fence,acknowledgement_fence FROM session_live_events WHERE stage_key=? ORDER BY rowid', (s['stage_key'],))
            for e in entries: e['result'] = json.loads(e['result'])
            result.append(dict(B=json.loads(s['binding']), stage_state=s['state'], entries=entries, receipt=json.loads(s['receipt']) if s['receipt'] else None))
        return result

    def delivery_read(self, a):
        d = self.delivery(a)
        surfaces = rows(self.conn, 'SELECT * FROM session_live_slots WHERE scope=? ORDER BY slot', (self.scope,))
        for s in surfaces:
            del s['scope']; del s['session_id']; s['protection'] = json.loads(s['protection']); s['markup_active'] = bool(s['markup_active'])
        return dict(D=a['D'], receipt_digest=d['receipt_digest'], plan_digest=d['plan_digest'], L=self.lease_result(d) if d['holder_id'] else None, branch_manifest=self.manifest(d), branch_selections=self.selections(d), evidence=self.evidence_export(d), stages=self.stage_summaries(d), surfaces=surfaces, complete=bool(d['complete']))

    def delivery_complete(self, a):
        hash_value(a['plan_digest']); d = self.delivery(a)
        need(d['plan_digest'] == a['plan_digest'], 'PLAN_MISMATCH')
        if d['complete']: return dict(complete=True, duplicate=True)
        self.lease(a, d)
        stages = rows(self.conn, 'SELECT * FROM session_live_stages WHERE outbox_id=?', (d['outbox_id'],))
        need(stages and all(s['state'] in TERMINAL and (s['state'] == 'satisfied_not_selected' or json.loads(s['spec'])['mode'] not in ('send','edit') or s['rebound']) for s in stages), 'STAGES_PENDING')
        need(len(self.selections(d)) == len(self.manifest(d)['groups']), 'STAGES_PENDING')
        for s in stages:
            receipt = json.loads(s['receipt'])
            if s['state'] == 'satisfied_not_selected': self.losing_record(a, d, s, receipt, complete=True)
            else: self.terminal_allowed(d, s, receipt)
        result = dict(complete=True, duplicate=False)
        self.conn.execute('UPDATE session_live_deliveries SET complete=1,completion=? WHERE outbox_id=?', (canonical(result), d['outbox_id']))
        o = json.loads(d['typed_outbox'])
        self.conn.execute("UPDATE session_outbox SET state='delivered',stage='acknowledged',lease_fence=? WHERE session_id=? AND revision=? AND frame_kind=?", (d['lease_fence'], o['session_id'], o['revision'], o['frame_kind']))
        return result


def check_command_ready(conn, command, authority, s):
    """Domain transaction guard; resolver success cannot bypass current revision."""
    p = dict(publisher_id=authority.publisher_id, actor=command['actor'],
             member_id=next(x['member_id'] for x in authority.scopes if x['S']['session_id'] == s['session_id'] and x['S']['chat_id'] == s['chat_id']))
    register_scope(conn, authority, s, p)
    need(not row(conn, 'SELECT 1 FROM session_live_deliveries WHERE session_id=? AND plan IS NOT NULL AND complete=0', (command['session_id'],)), 'SURFACE_PENDING')
    source = command['source_ref']
    need(source.get('member_id', p['member_id']) == p['member_id'] and source.get('chat_id', str(s['chat_id'])) == str(s['chat_id']), 'AUTH_FAILED')
    if source['kind'] == 'callback':
        binding = row(conn, 'SELECT * FROM session_live_commands WHERE scope=? AND command_digest=?', (stored_scope(conn, s), digest(command)))
        need(binding and binding['command_json'] == CommandEnvelope(command).to_json() and binding['principal'] == canonical(p), 'AUTH_FAILED')
        envelope = authority.command_envelope(command, s, p)
        need(binding['envelope_digest'] == envelope['envelope_digest'], 'AUTH_FAILED')


def record_rejection(conn, command, acknowledgement, authority, s, attempt=None):
    """Persist a typed refusal only after the failed domain transaction rolled back.

    Never overwrite a committed acknowledgement with a publication diagnostic.
    The original bound command remains addressable for timeout recovery.
    """
    need(not conn.in_transaction, 'STORAGE_FAILED')
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        if conn.execute('PRAGMA user_version').fetchone()[0] >= 83 and row(conn, 'SELECT 1 FROM session_pack_fences LIMIT 1'):
            if attempt is None: return
            admitted = row(conn, 'SELECT a.attempt,c.invocation_generation,c.scope,c.command_digest '
                'FROM session_invocation_admissions a JOIN session_live_commands c USING(binding_id) '
                'WHERE a.binding_id=? AND a.invocation_generation=?',
                (attempt['binding_id'],attempt['invocation_generation']))
            current = row(conn, 'SELECT token,active_generation FROM session_pack_fences WHERE pack=?', (attempt['pack'],))
            revoked = row(conn, 'SELECT 1 FROM session_invocation_revocations WHERE binding_id=? AND invocation_generation=?',
                          (attempt['binding_id'],attempt['invocation_generation']))
            if not (admitted and current and not revoked and admitted['attempt'] == canonical(attempt) and
                    admitted['invocation_generation'] == attempt['invocation_generation'] and
                    admitted['scope'] == stored_scope(conn, s) and admitted['command_digest'] == digest(dict(command)) and
                    current['token'] == attempt['pack_token'] and current['active_generation'] == attempt['activation_generation']):
                return
        conn.execute("UPDATE session_live_commands SET state='rejected',acknowledgement=? WHERE scope=? AND command_digest=? AND state='bound'",
                     (CallbackAcknowledgement(acknowledgement).to_json(), stored_scope(conn, s), digest(dict(command))))


def prepare_invocation(conn, command, authority, s):
    """Persist an existing-session invocation before entering the domain writer.

    A bound invocation requires authenticated quiescence and proven rollback to retry.
    Start creates the workout and its first binding together in the domain tx;
    a missing start receipt remains an unknown outcome, never a retry permit.
    """
    command = CommandEnvelope(dict(command)).to_dict()
    need(not conn.in_transaction, 'STORAGE_FAILED')
    if not row(conn, 'SELECT 1 FROM workouts WHERE id=?', (command['session_id'],)):
        return
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        p = dict(publisher_id=authority.publisher_id, actor=command['actor'],
                 member_id=next(x['member_id'] for x in authority.scopes if x['S']['session_id'] == s['session_id'] and x['S']['chat_id'] == s['chat_id']))
        register_scope(conn, authority, s, p)
        old = row(conn, 'SELECT * FROM session_live_commands WHERE scope=? AND envelope_id=?', (stored_scope(conn, s), command['source_ref']['ref']))
        if old:
            need(old['command_digest'] == digest(command) and old['principal'] == canonical(p), 'CONFLICT')
            need(old['command_json'] == CommandEnvelope(command).to_json(), 'CONFLICT')
            if old['state'] == 'committed':
                # An exact retry reuses its original admission. Never mint a
                # new generation or upgrade an obsolete pack fence here.
                if conn.execute('PRAGMA user_version').fetchone()[0] >= 83:
                    saved = row(conn, 'SELECT attempt FROM session_invocation_admissions '
                        'WHERE binding_id=? AND invocation_generation=?',
                        (old['binding_id'],old['invocation_generation']))
                    return json.loads(saved['attempt']) if saved else None
                return
            need(old['state'] == 'bound', 'SURFACE_PENDING')
            if old['invocation_started']: recover_invocation(conn, old, command, authority, s, p)
            bid = old['binding_id']
        else:
            need(command['source_ref']['kind'] != 'callback', 'AUTH_FAILED')
            bid = secrets.token_hex(16)
            envelope = authority.command_envelope(command, s, p)
            conn.execute('INSERT INTO session_live_commands(binding_id,scope,envelope_id,envelope_digest,command_digest,command_json,principal,state,session_id) VALUES(?,?,?,?,?,?,?,?,?)',
                (bid, stored_scope(conn, s), command['source_ref']['ref'], envelope['envelope_digest'], digest(command), CommandEnvelope(command).to_json(), canonical(p), 'bound', command['session_id']))
        check_command_ready(conn, command, authority, s)
        conn.execute('UPDATE session_live_commands SET invocation_started=1,invocation_generation=invocation_generation+1 WHERE binding_id=?', (bid,))
        if conn.execute('PRAGMA user_version').fetchone()[0] >= 83 and row(conn, 'SELECT 1 FROM session_pack_fences LIMIT 1'):
            from session_authority import admit_invocation
            return admit_invocation(conn, bid)


def authenticate_recovery(record, authority):
    strict_json(record)
    string(record['authority_id'])
    need(type(record.get('auth')) is dict and 'mac' in record['auth'], 'AUTH_FAILED')
    fields(record['auth'], 'mac')
    key = authority.recovery_keys.get(record['authority_id'])
    need(key is not None and len(key) >= 32 and not hmac.compare_digest(key, authority.key), 'AUTH_FAILED')
    expected = hmac.new(key, canonical({k:v for k,v in record.items() if k != 'auth'}).encode(), hashlib.sha256).hexdigest()
    need(type(record['auth']['mac']) is str and hmac.compare_digest(expected, record['auth']['mac']), 'AUTH_FAILED')


def recover_invocation(conn, old, command, authority, s, p):
    """Authenticate one quiescent aborted generation under the writer DB lock.

    The provisioned recovery authority proves no old caller can later enter
    Writer.apply. SQLite's BEGIN IMMEDIATE and the canonical command log prove
    this exact transaction has not committed. A consumed generation cannot
    authorize a second retry; unknown/inflight callers remain pending.
    """
    proof = next((v for v in authority.invocations if v.get('binding_id') == old['binding_id'] and v.get('invocation_generation') == old['invocation_generation']), None)
    need(proof is not None, 'SURFACE_PENDING')
    fields(proof, 'authority_id S P binding_id command_digest invocation_generation outcome quiescence', 'auth')
    authenticate_recovery(proof, authority)
    scope_shape(proof['S']); fields(proof['P'], 'publisher_id actor member_id')
    for item in proof['P'].values(): string(item)
    string(proof['binding_id']); hash_value(proof['command_digest'])
    integer(proof['invocation_generation'], 1)
    need(proof['S'] == s and proof['P'] == p and proof['command_digest'] == digest(command), 'CONFLICT')
    need(proof['outcome'] == 'aborted' and proof['quiescence'] is True, 'SURFACE_PENDING')
    prior = row(conn, 'SELECT input_digest FROM session_commands WHERE session_id=? AND command_id=?', (command['session_id'],command['command_id']))
    need(prior is None, 'SURFACE_PENDING')
    if conn.execute('PRAGMA user_version').fetchone()[0] >= 83 and row(conn, 'SELECT 1 FROM session_pack_fences LIMIT 1'):
        revoked = row(conn, 'SELECT record FROM session_invocation_revocations WHERE binding_id=? AND invocation_generation=?',
                      (old['binding_id'],old['invocation_generation']))
        need(revoked and json.loads(revoked['record']) == proof, 'SURFACE_PENDING')
    for kind, value in (('invocation', proof), ('invocation_export', authority.export('invocation', proof))):
        conn.execute('INSERT INTO session_live_evidence(digest,kind,value,binding_id,session_id) VALUES(?,?,?,?,?)', (digest(value),kind,canonical(value),old['binding_id'],old['session_id']))
