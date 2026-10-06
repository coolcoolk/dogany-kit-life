# -*- coding: utf-8 -*-
"""ingest_ledger.py -- the shared ingest ledger verbs (kit_life 057).

WHAT THIS IS
  The kit half of "들어온 기록 장부": modules (health, ledger/spend) read the
  transporter receiver themselves, interpret their own kinds and decide when
  to pull.  The kit supplies only the shared ledger and these verbs:

    ingest-consent <source> <kind> [--off] [--module M] [--json]
        The onboarding step calls this when the user approves (or turns off)
        one automation.  approved_at = NOW, always -- there is no --at: a
        backdated approval is a backfill (and the 057 trigger refuses it).
        Re-approving after --off moves approved_at to the new moment, so the
        off-period is never back-filled.
    ingest-check --source S --kind K --occurred-at TS
        ALLOW / REFUSED <reason>.  Ask BEFORE writing the domain row.
    ingest-put --source S --kind K --id X --occurred-at TS
               [--target-table T] [--target-id N] [--hash H]
        NEW / SAME / CHANGED.  Idempotent by (source, kind, id); SAME writes
        nothing.  REFUSED <reason> when there is no active consent or the
        event occurred before approved_at.
    ingest-get --source S --kind K --id X
        NONE, or target_table<TAB>target_id<TAB>payload_hash.
    ingest-get --source S --kind K [--mark-pulled] [--json]
        The pull cursor: floor (= approved_at, the oldest occurred_at that may
        ever be ingested), since (= last pull, or the floor) and the ledger's
        high-water mark.  --mark-pulled stamps this pull as the new cursor.
    ingest-status [--source S] [--kind K] [--json]
        One line per consent row with its ledger counts.
    metric-register --token T --scope S --accrual A --unit U [--owner-module M]
        A module registers the metric tokens it writes (design B-3: the kit
        seeds no module metrics).  OK NEW / OK SAME; CONFLICT (exit 4) when
        the token exists with different attributes -- never silently rewrites.

EXIT CODES  0 ok | 1 error / usage | 4 CONFLICT (metric-register) |
            6 REFUSED (no consent / consent off / occurred before approval).

TIMESTAMPS  Input: ISO-8601 with a zone ('Z' or +HH:MM).  A zone-less stamp is
refused -- a phone-local wall time is ambiguous by exactly the hours that
decide whether an event came before the approval.  Stored: UTC
'YYYY-MM-DDTHH:MM:SS.fffZ', the transporter's own shape.

This module prints tokens and key=value fields only; no user-facing sentence
is composed here.
"""
import argparse
import datetime
import json
import re
import sqlite3

TOKEN_RE = re.compile(r'^[a-z][a-z0-9_]{0,63}$')
MODULE_RE = re.compile(r'^[A-Za-z0-9_-]+$')
REFUSED_CODE = 6
CONFLICT_CODE = 4
SCOPES = ('entity', 'domain', 'block', 'place_link')
ACCRUALS = ('accrued', 'observed', 'derived')


class Ctx(object):
    """Kit helpers injected by kit_life.py (this file imports nothing from it)."""

    def __init__(self, get_conn, err):
        self.get_conn = get_conn
        self.err = err


class Refused(Exception):
    pass


# -- helpers ---------------------------------------------------------------
def _parser(verb):
    p = argparse.ArgumentParser(prog='kit_life.sh %s' % verb, add_help=False)
    p.add_argument('-h', '--help', action='help')
    return p


def _fmt(dt):
    dt = dt.astimezone(datetime.timezone.utc)
    return dt.strftime('%Y-%m-%dT%H:%M:%S.') + '%03dZ' % (dt.microsecond // 1000)


def now_utc():
    return _fmt(datetime.datetime.now(datetime.timezone.utc))


def parse_ts(raw):
    """-> canonical UTC stamp, or None when unparseable / zone-less."""
    s = (raw or '').strip()
    if not re.match(r'^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}', s):
        return None
    if s.endswith('Z') or s.endswith('z'):
        s = s[:-1] + '+00:00'
    try:
        dt = datetime.datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return _fmt(dt)


def _tok(ctx, what, v):
    if not TOKEN_RE.match(v or ''):
        ctx.err('%s %r refused: must match ^[a-z][a-z0-9_]{0,63}$' % (what, v))
    return v


def _module(ctx, v):
    if v is not None and not MODULE_RE.match(v):
        ctx.err('module %r refused: slug [A-Za-z0-9_-]+' % (v,))
    return v


def _open(ctx):
    conn = ctx.get_conn()
    have = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
        "AND name IN ('ingest_consent','ingest_ref')").fetchone()[0]
    if have != 2:
        conn.close()
        ctx.err('ingest ledger missing: this database has no ingest_consent/'
                'ingest_ref (migration 057 not applied)')
    return conn


def _consent(conn, source, kind):
    return conn.execute(
        "SELECT source, kind, module, approved_at, revoked_at, last_pulled_at "
        "FROM ingest_consent WHERE source=? AND kind=?",
        (source, kind)).fetchone()


def gate(conn, source, kind, occurred_at):
    """Raise Refused(reason) unless (source, kind) has active consent and the
    event occurred at or after approved_at.  Same rule as trg_ingest_ref_gate."""
    row = _consent(conn, source, kind)
    if row is None:
        raise Refused('no-consent')
    if row[4] is not None:
        raise Refused('consent-off')
    ok = conn.execute("SELECT julianday(?) >= julianday(?)",
                      (occurred_at, row[3])).fetchone()[0]
    if not ok:
        raise Refused('before-approval approved_at=%s' % row[3])
    return row


def _dash(v):
    return '-' if v is None else v


def _kv(pairs):
    return ' '.join('%s=%s' % (k, _dash(v)) for k, v in pairs)


def _refuse(reason):
    print('REFUSED %s' % reason)
    return REFUSED_CODE


def _occurred(ctx, raw):
    ts = parse_ts(raw)
    if ts is None:
        ctx.err('--occurred-at %r refused: ISO-8601 with a zone (Z or +HH:MM)'
                % (raw,))
    return ts


# -- ingest-consent --------------------------------------------------------
def cmd_consent(ctx, argv):
    p = _parser('ingest-consent')
    p.add_argument('source')
    p.add_argument('kind')
    p.add_argument('--off', action='store_true')
    p.add_argument('--module')
    p.add_argument('--json', action='store_true')
    a = p.parse_args(argv)
    source, kind = _tok(ctx, 'source', a.source), _tok(ctx, 'kind', a.kind)
    module = _module(ctx, a.module)
    conn = _open(ctx)
    try:
        now = now_utc()
        row = _consent(conn, source, kind)
        if a.off:
            if row is None:
                verdict = 'NONE'
            elif row[4] is not None:
                verdict = 'SAME'
            else:
                conn.execute(
                    "UPDATE ingest_consent SET revoked_at=?, updated_at=? "
                    "WHERE source=? AND kind=?", (now, now, source, kind))
                verdict = 'REVOKED'
        else:
            if row is None:
                conn.execute(
                    "INSERT INTO ingest_consent(source, kind, module, "
                    "approved_at, created_at, updated_at) VALUES(?,?,?,?,?,?)",
                    (source, kind, module, now, now, now))
                verdict = 'NEW'
            elif row[4] is not None:
                # resumed: a NEW approval moment -- the off-period is never
                # back-filled.
                conn.execute(
                    "UPDATE ingest_consent SET approved_at=?, revoked_at=NULL, "
                    "module=COALESCE(?, module), updated_at=? "
                    "WHERE source=? AND kind=?",
                    (now, module, now, source, kind))
                verdict = 'RESUMED'
            else:
                if module is not None and module != row[2]:
                    conn.execute(
                        "UPDATE ingest_consent SET module=?, updated_at=? "
                        "WHERE source=? AND kind=?",
                        (module, now, source, kind))
                verdict = 'SAME'
        conn.commit()
        row = _consent(conn, source, kind)
    finally:
        conn.close()
    state = 'none' if row is None else ('off' if row[4] else 'on')
    out = [('source', source), ('kind', kind), ('state', state),
           ('module', row[2] if row else None),
           ('approved_at', row[3] if row else None),
           ('revoked_at', row[4] if row else None), ('verdict', verdict)]
    if a.json:
        print(json.dumps(dict(out), ensure_ascii=False))
    else:
        print('CONSENT ' + _kv(out))
    return 0


# -- ingest-check ----------------------------------------------------------
def cmd_check(ctx, argv):
    p = _parser('ingest-check')
    p.add_argument('--source', required=True)
    p.add_argument('--kind', required=True)
    p.add_argument('--occurred-at', required=True)
    a = p.parse_args(argv)
    source, kind = _tok(ctx, 'source', a.source), _tok(ctx, 'kind', a.kind)
    occ = _occurred(ctx, a.occurred_at)
    conn = _open(ctx)
    try:
        gate(conn, source, kind, occ)
    except Refused as r:
        return _refuse(str(r))
    finally:
        conn.close()
    print('ALLOW')
    return 0


# -- ingest-put ------------------------------------------------------------
def cmd_put(ctx, argv):
    p = _parser('ingest-put')
    p.add_argument('--source', required=True)
    p.add_argument('--kind', required=True)
    p.add_argument('--id', required=True, dest='ext_id')
    p.add_argument('--occurred-at', required=True)
    p.add_argument('--target-table')
    p.add_argument('--target-id')
    p.add_argument('--hash')
    a = p.parse_args(argv)
    source, kind = _tok(ctx, 'source', a.source), _tok(ctx, 'kind', a.kind)
    if not a.ext_id or len(a.ext_id) > 256:
        ctx.err('--id refused: 1..256 characters')
    occ = _occurred(ctx, a.occurred_at)
    conn = _open(ctx)
    try:
        try:
            gate(conn, source, kind, occ)
        except Refused as r:
            return _refuse(str(r))
        new = (a.target_table, a.target_id, a.hash)
        row = conn.execute(
            "SELECT target_table, target_id, payload_hash, occurred_at "
            "FROM ingest_ref WHERE source=? AND kind=? AND ext_id=?",
            (source, kind, a.ext_id)).fetchone()
        now = now_utc()
        if row is None:
            conn.execute(
                "INSERT INTO ingest_ref(source, kind, ext_id, occurred_at, "
                "target_table, target_id, payload_hash, first_seen, "
                "updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (source, kind, a.ext_id, occ) + new + (now, now))
            verdict = 'NEW'
        elif tuple(row[:3]) == new and row[3] == occ:
            verdict = 'SAME'
        else:
            conn.execute(
                "UPDATE ingest_ref SET occurred_at=?, target_table=?, "
                "target_id=?, payload_hash=?, updated_at=?, "
                "revision=revision+1 "
                "WHERE source=? AND kind=? AND ext_id=?",
                (occ,) + new + (now, source, kind, a.ext_id))
            verdict = 'CHANGED'
        conn.commit()
    except sqlite3.IntegrityError as e:
        # the trigger is the backstop for a race with ingest-consent --off
        return _refuse('gate %s' % e)
    finally:
        conn.close()
    print(verdict)
    return 0


# -- ingest-get ------------------------------------------------------------
def _cursor(conn, source, kind):
    row = _consent(conn, source, kind)
    agg = conn.execute(
        "SELECT COUNT(*), MAX(occurred_at), MAX(updated_at) FROM ingest_ref "
        "WHERE source=? AND kind=?", (source, kind)).fetchone()
    state = 'none' if row is None else ('off' if row[4] else 'on')
    floor = row[3] if row else None
    since = None
    if state == 'on':
        since = floor
        if row[5] is not None and row[5] > floor:
            since = row[5]
    return state, [('source', source), ('kind', kind), ('consent', state),
                   ('floor', floor), ('since', since),
                   ('last_pulled_at', row[5] if row else None),
                   ('last_occurred_at', agg[1]), ('refs', agg[0])]


def cmd_get(ctx, argv):
    p = _parser('ingest-get')
    p.add_argument('--source', required=True)
    p.add_argument('--kind', required=True)
    p.add_argument('--id', dest='ext_id')
    p.add_argument('--mark-pulled', action='store_true')
    p.add_argument('--json', action='store_true')
    a = p.parse_args(argv)
    source, kind = _tok(ctx, 'source', a.source), _tok(ctx, 'kind', a.kind)
    conn = _open(ctx)
    try:
        if a.ext_id is not None:
            if a.mark_pulled:
                ctx.err('--mark-pulled goes with the cursor form (no --id)')
            row = conn.execute(
                "SELECT target_table, target_id, payload_hash FROM ingest_ref "
                "WHERE source=? AND kind=? AND ext_id=?",
                (source, kind, a.ext_id)).fetchone()
            if a.json:
                print(json.dumps(None if row is None else dict(zip(
                    ('target_table', 'target_id', 'payload_hash'), row))))
            else:
                print('NONE' if row is None else
                      '\t'.join('' if x is None else str(x) for x in row))
            return 0
        state, out = _cursor(conn, source, kind)
        if state == 'on' and a.mark_pulled:
            now = now_utc()
            conn.execute(
                "UPDATE ingest_consent SET last_pulled_at=?, updated_at=? "
                "WHERE source=? AND kind=?", (now, now, source, kind))
            conn.commit()
            out.append(('marked', now))
    finally:
        conn.close()
    if a.json:
        print(json.dumps(dict(out), ensure_ascii=False))
    else:
        print('CURSOR ' + _kv(out))
    return 0 if state == 'on' else REFUSED_CODE


# -- ingest-status ---------------------------------------------------------
def cmd_status(ctx, argv):
    p = _parser('ingest-status')
    p.add_argument('--source')
    p.add_argument('--kind')
    p.add_argument('--json', action='store_true')
    a = p.parse_args(argv)
    conn = _open(ctx)
    try:
        sql = ("SELECT c.source, c.kind, c.module, c.approved_at, c.revoked_at, "
               "c.last_pulled_at, COUNT(r.ext_id), "
               "COALESCE(SUM(r.revision > 1), 0), MAX(r.occurred_at), "
               "MAX(r.updated_at) "
               "FROM ingest_consent c LEFT JOIN ingest_ref r "
               "ON r.source=c.source AND r.kind=c.kind WHERE 1=1")
        params = []
        if a.source:
            sql += " AND c.source=?"
            params.append(a.source)
        if a.kind:
            sql += " AND c.kind=?"
            params.append(a.kind)
        sql += " GROUP BY c.source, c.kind ORDER BY c.source, c.kind"
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    recs = []
    for r in rows:
        recs.append([('source', r[0]), ('kind', r[1]),
                     ('state', 'off' if r[4] else 'on'), ('module', r[2]),
                     ('approved_at', r[3]), ('revoked_at', r[4]),
                     ('last_pulled_at', r[5]), ('refs', r[6]),
                     ('changed', r[7]), ('last_occurred_at', r[8]),
                     ('last_put_at', r[9])])
    if a.json:
        print(json.dumps([dict(x) for x in recs], ensure_ascii=False))
    elif not recs:
        print('INGEST none')
    else:
        for x in recs:
            print('INGEST ' + _kv(x))
    return 0


# -- metric-register -------------------------------------------------------
def cmd_metric_register(ctx, argv):
    p = _parser('metric-register')
    p.add_argument('--token', required=True)
    p.add_argument('--scope', required=True, choices=SCOPES)
    p.add_argument('--accrual', required=True, choices=ACCRUALS)
    p.add_argument('--unit', required=True)
    p.add_argument('--owner-module', required=True)
    a = p.parse_args(argv)
    tok = _tok(ctx, 'token', a.token)
    owner = _module(ctx, a.owner_module)
    conn = ctx.get_conn()
    try:
        if conn.execute("SELECT 1 FROM unit WHERE token=?",
                        (a.unit,)).fetchone() is None:
            ctx.err('unit %r is not registered (unit table)' % (a.unit,))
        want = (a.scope, a.accrual, a.unit, owner)
        row = conn.execute(
            "SELECT scope_kind, accrual, unit_token, owner_module FROM "
            "metric_def WHERE token=?", (tok,)).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO metric_def(token, scope_kind, accrual, unit_token, "
                "owner_module, origin) VALUES(?,?,?,?,?,'module')",
                (tok,) + want)
            conn.commit()
            print('OK NEW %s' % tok)
            return 0
        if tuple(row) == want:
            print('OK SAME %s' % tok)
            return 0
        print('CONFLICT %s existing %s' % (tok, _kv(zip(
            ('scope', 'accrual', 'unit', 'owner_module'), row))))
        return CONFLICT_CODE
    finally:
        conn.close()


VERBS = {
    'ingest-consent': cmd_consent,
    'ingest-check': cmd_check,
    'ingest-put': cmd_put,
    'ingest-get': cmd_get,
    'ingest-status': cmd_status,
    'metric-register': cmd_metric_register,
}
